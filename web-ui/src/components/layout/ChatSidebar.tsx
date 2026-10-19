import React, { useMemo, useState } from 'react';
import {
  Button,
  Dropdown,
  Input,
  MenuProps,
  Modal,
  Select,
  Spin,
  Typography,
  Tooltip,
  message,
} from 'antd';
import {
  PlusOutlined,
  SearchOutlined,
  MoreOutlined,
  EditOutlined,
  DeleteOutlined,
  ExclamationCircleOutlined,
  InboxOutlined,
  ReloadOutlined,
  MenuFoldOutlined,
  SwapOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { useLayoutStore } from '@store/layout';
import { useAuthStore } from '@store/auth';
import { chatApi } from '@api/chat';
import { ChatProjectItem, ChatSession } from '@/types';
import { shallow } from 'zustand/shallow';
import { useT, type TFn } from '@/i18n/useT';

const { Text } = Typography;

function formatTimeAgo(t: TFn, dateInput?: Date | string | null): string {
  if (!dateInput) return '';
  const date = typeof dateInput === 'string' ? new Date(dateInput) : dateInput;
  if (isNaN(date.getTime())) return '';
  
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMinutes = Math.floor(diffMs / (1000 * 60));
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  const diffWeeks = Math.floor(diffDays / 7);

  if (diffMinutes < 1) return t('sidebar.justNow');
  if (diffMinutes < 60) return t('sidebar.minutesAgo', { n: diffMinutes });
  if (diffHours < 24) return t('sidebar.hoursAgo', { n: diffHours });
  if (diffDays === 1) return t('sidebar.yesterday');
  if (diffDays < 7) return t('sidebar.daysAgo', { n: diffDays });
  if (diffWeeks < 4) return t('sidebar.weeksAgo', { n: diffWeeks });
  return t('sidebar.monthsAgo', { n: Math.floor(diffDays / 30) });
}

export const ChatSidebar: React.FC<{ onSessionSelected?: () => void; onCollapse?: () => void }> = ({ onSessionSelected, onCollapse }) => {
  const t = useT();
  const {
    sessions,
    currentSession,
    setCurrentSession,
    startNewSession,
    deleteSession,
    loadChatHistory,
    autotitleSession,
    renameSession,
    moveSessionToProject,
  } = useChatStore(
    (state) => ({
      sessions: state.sessions,
      currentSession: state.currentSession,
      setCurrentSession: state.setCurrentSession,
      startNewSession: state.startNewSession,
      deleteSession: state.deleteSession,
      loadChatHistory: state.loadChatHistory,
      autotitleSession: state.autotitleSession,
      renameSession: state.renameSession,
      moveSessionToProject: state.moveSessionToProject,
    }),
    shallow
  );
  const { toggleChatList } = useLayoutStore();
  const { projectLabel } = useAuthStore();

  const [searchVisible, setSearchVisible] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [renameModalOpen, setRenameModalOpen] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const [renameTarget, setRenameTarget] = useState<ChatSession | null>(null);
  const [isRenaming, setIsRenaming] = useState(false);
  const [moveModalOpen, setMoveModalOpen] = useState(false);
  const [moveTarget, setMoveTarget] = useState<ChatSession | null>(null);
  const [moveProjects, setMoveProjects] = useState<ChatProjectItem[]>([]);
  const [moveValue, setMoveValue] = useState<number | null>(null);
  const [moveLoading, setMoveLoading] = useState(false);
  const [moveSubmitting, setMoveSubmitting] = useState(false);

  const isSessionsLoading = !currentSession && sessions.length === 0;

  const filteredSessions = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();
    return sessions
      .filter((session) => {
        if (!normalizedQuery) return true;
        const title = session.title?.toLowerCase?.() ?? '';
        const planTitle = session.plan_title?.toLowerCase?.() ?? '';
        return title.includes(normalizedQuery) || planTitle.includes(normalizedQuery);
      })
      .slice()
      .sort((a, b) => {
        const ta = new Date(a.created_at ?? a.updated_at ?? 0).getTime();
        const tb = new Date(b.created_at ?? b.updated_at ?? 0).getTime();
        return tb - ta;
      });
  }, [searchQuery, sessions]);

  // Split sessions into "Pending Input / In Progress" and "Recent History"
  const { pendingSessions, recentSessions } = useMemo(() => {
    const pending: ChatSession[] = [];
    const recent: ChatSession[] = [];

    filteredSessions.forEach((s) => {
      const raw = s as any;
      const isPending = raw?.metadata?.waiting_user_input || raw?.metadata?.status === 'waiting_input';
      if (isPending) {
        pending.push(s);
      } else {
        recent.push(s);
      }
    });

    return { pendingSessions: pending, recentSessions: recent };
  }, [filteredSessions]);

  const handleNewChat = () => {
    startNewSession();
    onSessionSelected?.();
  };

  const handleSelectSession = async (session: ChatSession) => {
    onSessionSelected?.();
    if (currentSession?.id === session.id && session.messages.length > 0) {
      return;
    }
    setCurrentSession(session);

    const sessionId = session.session_id ?? session.id;
    if (sessionId) {
      try {
        await loadChatHistory(sessionId);
      } catch (err) {
        console.warn('Failed to load session history:', err);
      }
    }
  };

  const handleArchiveSession = async (session: ChatSession) => {
    try {
      await deleteSession(session.id, { archive: true });
      message.success(t('sidebar.archived'));
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      message.error(t('sidebar.archiveFailed', { error: errMsg }));
    }
  };

  const performDeleteSession = async (session: ChatSession) => {
    try {
      await deleteSession(session.id);
      message.success(t('sidebar.deleted'));
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      message.error(t('sidebar.deleteFailed', { error: errMsg }));
      throw error;
    }
  };

  const openRenameModal = (session: ChatSession) => {
    setRenameTarget(session);
    setRenameValue(session.title || '');
    setRenameModalOpen(true);
  };

  const closeRenameModal = () => {
    setRenameModalOpen(false);
    setRenameTarget(null);
    setRenameValue('');
    setIsRenaming(false);
  };

  const handleRenameConfirm = async () => {
    if (!renameTarget) return;

    const nextTitle = renameValue.trim();
    if (!nextTitle) {
      message.error(t('sidebar.titleEmpty'));
      return;
    }

    const sessionId = renameTarget.session_id ?? renameTarget.id;
    setIsRenaming(true);
    try {
      await renameSession(sessionId, nextTitle);
      message.success(t('sidebar.titleUpdated'));
      closeRenameModal();
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      message.error(t('sidebar.renameFailed', { error: errMsg }));
      setIsRenaming(false);
    }
  };

  const confirmDeleteSession = (session: ChatSession) => {
    Modal.confirm({
      title: t('sidebar.deleteTitle'),
      icon: <ExclamationCircleOutlined />,
      content: t('sidebar.deleteConfirm', { title: session.title || session.id }),
      okText: t('sidebar.delete'),
      okType: 'danger',
      cancelText: t('common.cancel'),
      onOk: () => performDeleteSession(session),
    });
  };

  const openMoveModal = async (session: ChatSession) => {
    setMoveTarget(session);
    setMoveValue(session.project_id ?? null);
    setMoveModalOpen(true);
    setMoveLoading(true);
    try {
      const response = await chatApi.getProjects();
      setMoveProjects(response.projects ?? []);
    } catch (error) {
      console.warn('Failed to load projects:', error);
      setMoveProjects([]);
      message.error(t('sidebar.projectsLoadFailed'));
    } finally {
      setMoveLoading(false);
    }
  };

  const closeMoveModal = () => {
    setMoveModalOpen(false);
    setMoveTarget(null);
    setMoveProjects([]);
    setMoveValue(null);
    setMoveLoading(false);
    setMoveSubmitting(false);
  };

  const handleMoveConfirm = async () => {
    if (!moveTarget) return;
    const sessionId = moveTarget.session_id ?? moveTarget.id;
    setMoveSubmitting(true);
    try {
      await moveSessionToProject(sessionId, moveValue);
      const targetLabel = moveValue === null
        ? t('sidebar.unassigned')
        : moveProjects.find((p) => p.id === moveValue)?.label ?? t('sidebar.projectN', { id: moveValue });
      message.success(t('sidebar.moved', { label: targetLabel }));
      closeMoveModal();
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      message.error(t('sidebar.moveFailed', { error: errMsg }));
      setMoveSubmitting(false);
    }
  };

  const handleSessionMenuAction = async (session: ChatSession, key: string) => {
    if (key === 'rename') {
      openRenameModal(session);
      return;
    }

    if (key === 'move') {
      void openMoveModal(session);
      return;
    }

    if (key !== 'autotitle') return;

    const sessionId = session.session_id ?? session.id;
    if (!sessionId) return;

    try {
      const result = await autotitleSession(sessionId, { force: true });
      if (!result) return;
      if (result.updated) {
        message.success(t('sidebar.autotitled', { title: result.title }));
      } else {
        message.info(t('sidebar.titleUnchanged'));
      }
    } catch (error) {
      console.error('Session operation failed:', error);
      message.error(t('sidebar.autotitleFailed'));
    }
  };

  const getSessionMenuItems = (session: ChatSession): MenuProps['items'] => {
    const items: MenuProps['items'] = [
      {
        key: 'rename',
        label: t('sidebar.menuRename'),
        icon: <EditOutlined />,
      },
      {
        key: 'autotitle',
        label: t('sidebar.menuAutotitle'),
        icon: <ReloadOutlined />,
      },
      {
        key: 'move',
        label: t('sidebar.menuMove'),
        icon: <SwapOutlined />,
      },
    ];

    if (session.is_active !== false) {
      items.push({
        key: 'archive',
        label: t('sidebar.menuArchive'),
        icon: <InboxOutlined />,
        onClick: async (_info: any) => {
          _info?.domEvent?.stopPropagation?.();
          await handleArchiveSession(session);
        },
      });
    }

    items.push({ type: 'divider' });
    items.push({
      key: 'delete',
      label: t('sidebar.menuDelete'),
      icon: <DeleteOutlined />,
      danger: true,
      onClick: (_info: any) => {
        _info?.domEvent?.stopPropagation?.();
        confirmDeleteSession(session);
      },
    });

    return items;
  };

  const projectName = projectLabel || t('sidebar.defaultProject');

  const renderSessionItem = (session: ChatSession) => {
    const isSelected = currentSession?.id === session.id;
    const lastTimestamp = session.last_message_at ?? session.updated_at ?? session.created_at;
    const timeAgo = formatTimeAgo(t, lastTimestamp);

    return (
      <div
        key={session.id}
        className={`biomni-session-item ${isSelected ? 'selected' : ''}`}
        role="button"
        tabIndex={0}
        aria-current={isSelected ? 'page' : undefined}
        onKeyDown={(event) => {
          if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault();
            void handleSelectSession(session);
          }
        }}
        onClick={() => handleSelectSession(session)}
      >
        <div className="biomni-session-bullet">
          <span className="biomni-bullet-dot" />
        </div>
        <div className="biomni-session-title-wrap">
          <Text ellipsis className="biomni-session-title">
            {session.title || t('sidebar.untitled', { id: session.id.slice(-6) })}
          </Text>
        </div>
        <div className="biomni-session-meta">
          <span className="biomni-session-time">{timeAgo}</span>
          <Dropdown
            menu={{
              items: getSessionMenuItems(session),
              onClick: ({ key, domEvent }) => {
                domEvent?.stopPropagation();
                void handleSessionMenuAction(session, String(key));
              },
            }}
            trigger={['click']}
            placement="bottomRight"
          >
            <Button
              type="text"
              size="small"
              className="biomni-session-more-btn"
              icon={<MoreOutlined />}
              onClick={(e) => e.stopPropagation()}
            />
          </Dropdown>
        </div>
      </div>
    );
  };

  return (
    <div className="biomni-sidebar-container">
      {/* 1. Top Project Header */}
      <div className="biomni-sidebar-project-header">
        <div className="biomni-project-meta">
          <span className="biomni-project-tag">{t('sidebar.currentProject')}</span>
          <div className="biomni-project-title-row">
            <span className="biomni-project-name">{projectName}</span>
          </div>
        </div>
        <div className="biomni-project-actions">
          <Tooltip title={t('sidebar.collapse')}>
            <Button
              type="text"
              size="small"
              icon={<MenuFoldOutlined />}
              className="biomni-icon-btn"
              onClick={onCollapse ?? toggleChatList}
            />
          </Tooltip>
        </div>
      </div>

      {/* 2. Tasks / Sessions Section Header */}
      <div className="biomni-section-header">
        <div className="biomni-section-title">
          <span>{t('sidebar.researchChats')}</span>
        </div>
        <div className="biomni-section-actions">
          <Tooltip title={t('sidebar.search')}>
            <Button
              type="text"
              size="small"
              icon={<SearchOutlined />}
              className="biomni-action-icon-btn"
              onClick={() => setSearchVisible((prev) => !prev)}
            />
          </Tooltip>
          <Button
            type="text"
            size="small"
            icon={<PlusOutlined />}
            className="biomni-new-task-btn"
            onClick={handleNewChat}
          >
            {t('sidebar.newTask')}
          </Button>
        </div>
      </div>

      {/* Search Bar Input (toggleable) */}
      {searchVisible && (
        <div className="biomni-search-bar">
          <Input
            placeholder={t('sidebar.searchPlaceholder')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            prefix={<SearchOutlined style={{ color: 'var(--text-tertiary)' }} />}
            allowClear
            size="small"
            autoFocus
          />
        </div>
      )}

      {/* 3. Session List Area */}
      <div className="biomni-session-list-scroll">
        {isSessionsLoading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: '24px 0' }}>
            <Spin size="small" />
          </div>
        ) : filteredSessions.length === 0 ? (
          <div className="biomni-empty-sessions">
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('sidebar.empty')}
            </Text>
          </div>
        ) : (
          <>
            {/* Pending Input Group (if any) */}
            {pendingSessions.length > 0 && (
              <div className="biomni-group-container">
                <div className="biomni-group-header">
                  <div className="biomni-group-title">
                    <span>{t('sidebar.waitingInput')}</span>
                  </div>
                  <span className="biomni-group-count">{pendingSessions.length}</span>
                </div>
                <div className="biomni-group-items">
                  {pendingSessions.map(renderSessionItem)}
                </div>
                <div className="biomni-group-divider" />
              </div>
            )}

            {/* Recent Sessions List */}
            <div className="biomni-group-items">
              {recentSessions.map(renderSessionItem)}
            </div>
          </>
        )}
      </div>

      <Modal
        title={t('sidebar.renameTitle')}
        open={renameModalOpen}
        onOk={() => void handleRenameConfirm()}
        confirmLoading={isRenaming}
        onCancel={closeRenameModal}
        okText={t('common.save')}
        cancelText={t('common.cancel')}
        destroyOnClose
      >
        <Input
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          placeholder={t('sidebar.renamePlaceholder')}
          maxLength={120}
          onPressEnter={() => void handleRenameConfirm()}
          autoFocus
        />
      </Modal>

      <Modal
        title={t('sidebar.moveTitle')}
        open={moveModalOpen}
        onOk={() => void handleMoveConfirm()}
        confirmLoading={moveSubmitting}
        onCancel={closeMoveModal}
        okText={t('sidebar.move')}
        cancelText={t('common.cancel')}
        destroyOnClose
      >
        <Spin spinning={moveLoading}>
          <div style={{ marginBottom: 8 }}>
            <Text type="secondary">
              {t('sidebar.moveInto', { title: moveTarget?.title || moveTarget?.id })}
            </Text>
          </div>
          <Select
            style={{ width: '100%' }}
            value={moveValue}
            onChange={(value) => setMoveValue(value ?? null)}
            placeholder={t('sidebar.pickProject')}
            optionFilterProp="label"
            showSearch
            allowClear
            options={moveProjects.map((p) => ({
              value: p.id,
              label: p.current ? t('sidebar.currentTag', { label: p.label }) : p.label,
            }))}
          />
          <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('sidebar.moveHint')}
            </Text>
          </div>
        </Spin>
      </Modal>
    </div>
  );
};

export default React.memo(ChatSidebar);
