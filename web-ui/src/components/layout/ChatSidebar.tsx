import React, { useMemo, useState } from 'react';
import {
  Button,
  Dropdown,
  Input,
  MenuProps,
  Modal,
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
} from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { useLayoutStore } from '@store/layout';
import { useAuthStore } from '@store/auth';
import { ChatSession } from '@/types';
import { shallow } from 'zustand/shallow';

const { Text } = Typography;

function formatTimeAgo(dateInput?: Date | string | null): string {
  if (!dateInput) return '';
  const date = typeof dateInput === 'string' ? new Date(dateInput) : dateInput;
  if (isNaN(date.getTime())) return '';
  
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMinutes = Math.floor(diffMs / (1000 * 60));
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  const diffWeeks = Math.floor(diffDays / 7);

  if (diffMinutes < 1) return '刚刚';
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays === 1) return '昨天';
  if (diffDays < 7) return `${diffDays}d ago`;
  if (diffWeeks < 4) return `${diffWeeks}w ago`;
  return `${Math.floor(diffDays / 30)}mo ago`;
}

export const ChatSidebar: React.FC = () => {
  const {
    sessions,
    currentSession,
    setCurrentSession,
    startNewSession,
    deleteSession,
    loadChatHistory,
    autotitleSession,
    renameSession,
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
  };

  const handleSelectSession = async (session: ChatSession) => {
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
      message.success('对话已归档');
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      message.error(`归档失败: ${errMsg}`);
    }
  };

  const performDeleteSession = async (session: ChatSession) => {
    try {
      await deleteSession(session.id);
      message.success('对话已删除');
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      message.error(`删除失败: ${errMsg}`);
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
      message.error('标题不能为空');
      return;
    }

    const sessionId = renameTarget.session_id ?? renameTarget.id;
    setIsRenaming(true);
    try {
      await renameSession(sessionId, nextTitle);
      message.success('会话标题已更新');
      closeRenameModal();
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      message.error(`重命名失败: ${errMsg}`);
      setIsRenaming(false);
    }
  };

  const confirmDeleteSession = (session: ChatSession) => {
    Modal.confirm({
      title: '删除会话',
      icon: <ExclamationCircleOutlined />,
      content: `确认删除「${session.title || session.id}」吗？`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => performDeleteSession(session),
    });
  };

  const handleSessionMenuAction = async (session: ChatSession, key: string) => {
    if (key === 'rename') {
      openRenameModal(session);
      return;
    }

    if (key !== 'autotitle') return;

    const sessionId = session.session_id ?? session.id;
    if (!sessionId) return;

    try {
      const result = await autotitleSession(sessionId, { force: true });
      if (!result) return;
      if (result.updated) {
        message.success(`已更新标题为「${result.title}」`);
      } else {
        message.info('标题无需更新');
      }
    } catch (error) {
      console.error('Session operation failed:', error);
      message.error('自动命名失败，请稍后重试');
    }
  };

  const getSessionMenuItems = (session: ChatSession): MenuProps['items'] => {
    const items: MenuProps['items'] = [
      {
        key: 'rename',
        label: '重命名',
        icon: <EditOutlined />,
      },
      {
        key: 'autotitle',
        label: '重新生成标题',
        icon: <ReloadOutlined />,
      },
    ];

    if (session.is_active !== false) {
      items.push({
        key: 'archive',
        label: '归档会话',
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
      label: '删除会话',
      icon: <DeleteOutlined />,
      danger: true,
      onClick: (_info: any) => {
        _info?.domEvent?.stopPropagation?.();
        confirmDeleteSession(session);
      },
    });

    return items;
  };

  const projectName = projectLabel || '快速任务';

  const renderSessionItem = (session: ChatSession) => {
    const isSelected = currentSession?.id === session.id;
    const lastTimestamp = session.last_message_at ?? session.updated_at ?? session.created_at;
    const timeAgo = formatTimeAgo(lastTimestamp);

    return (
      <div
        key={session.id}
        className={`biomni-session-item ${isSelected ? 'selected' : ''}`}
        onClick={() => handleSelectSession(session)}
      >
        <div className="biomni-session-bullet">
          <span className="biomni-bullet-dot" />
        </div>
        <div className="biomni-session-title-wrap">
          <Text ellipsis className="biomni-session-title">
            {session.title || `会话 ${session.id.slice(-6)}`}
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
          <span className="biomni-project-tag">PROJECT</span>
          <div className="biomni-project-title-row">
            <span className="biomni-project-name">{projectName}</span>
          </div>
        </div>
        <div className="biomni-project-actions">
          <Tooltip title="收起侧边栏">
            <Button
              type="text"
              size="small"
              icon={<MenuFoldOutlined />}
              className="biomni-icon-btn"
              onClick={toggleChatList}
            />
          </Tooltip>
        </div>
      </div>

      {/* 2. Tasks / Sessions Section Header */}
      <div className="biomni-section-header">
        <div className="biomni-section-title">
          <span>任务</span>
        </div>
        <div className="biomni-section-actions">
          <Tooltip title="搜索对话">
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
            新建任务
          </Button>
        </div>
      </div>

      {/* Search Bar Input (toggleable) */}
      {searchVisible && (
        <div className="biomni-search-bar">
          <Input
            placeholder="搜索任务或会话..."
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
              暂无任务会话，点击右上角「+ 新建任务」
            </Text>
          </div>
        ) : (
          <>
            {/* Pending Input Group (if any) */}
            {pendingSessions.length > 0 && (
              <div className="biomni-group-container">
                <div className="biomni-group-header">
                  <div className="biomni-group-title">
                    <span>等待输入</span>
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
        title="重命名会话"
        open={renameModalOpen}
        onOk={() => void handleRenameConfirm()}
        confirmLoading={isRenaming}
        onCancel={closeRenameModal}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Input
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          placeholder="输入新的会话标题"
          maxLength={120}
          onPressEnter={() => void handleRenameConfirm()}
          autoFocus
        />
      </Modal>
    </div>
  );
};

export default React.memo(ChatSidebar);
