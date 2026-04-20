import React, { useMemo, useState } from 'react';
import {
  Avatar,
  Button,
  Dropdown,
  Input,
  List,
  MenuProps,
  Modal,
  Spin,
  Tag,
  Typography,
  Tooltip,
  message,
} from 'antd';
import {
  PlusOutlined,
  SearchOutlined,
  MessageOutlined,
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
import { ChatSession } from '@/types';
import { shallow } from 'zustand/shallow';

const { Text } = Typography;
const { Search } = Input;

const TITLE_SOURCE_HINT: Record<string, string> = {
  plan: '由计划标题生成',
  'plan_task': '由计划与任务上下文生成',
  heuristic: '由近期对话内容生成',
  llm: '由模型自动总结',
  default: '默认标题，建议重新生成',
  local: '临时标题，建议重新生成',
  user: '',
};

const ChatSidebar: React.FC = () => {
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

  const [searchQuery, setSearchQuery] = useState('');
  const [renameModalOpen, setRenameModalOpen] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const [renameTarget, setRenameTarget] = useState<ChatSession | null>(null);
  const [isRenaming, setIsRenaming] = useState(false);

  const isSessionsLoading = !currentSession && sessions.length === 0;

  const filteredSessions = useMemo(() => {
  const normalizedQuery = searchQuery.trim().toLowerCase();
  return sessions.filter((session) => {
  if (!normalizedQuery) {
  return true;
  }
  const title = session.title?.toLowerCase?.() ?? '';
  const planTitle = session.plan_title?.toLowerCase?.() ?? '';
  return title.includes(normalizedQuery) || planTitle.includes(normalizedQuery);
  });
  }, [searchQuery, sessions]);

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
  console.log('🔄 [ChatSidebar] loading session:', sessionId);
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
  if (!renameTarget) {
  return;
  }

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

  if (key !== 'autotitle') {
  return;
  }

  const sessionId = session.session_id ?? session.id;
  if (!sessionId) {
  return;
  }

  try {
  const result = await autotitleSession(sessionId, { force: true });
  if (!result) {
  return;
  }
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

  const formatTime = (date?: Date | null) => {
  if (!date) {
  return '';
  }
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));

  if (days === 0) {
  return date.toLocaleTimeString('zh-CN', {
  hour: '2-digit',
  minute: '2-digit',
  });
  } else if (days === 1) {
  return '昨天';
  } else if (days < 7) {
  return `${days} 天前`;
  }
  return date.toLocaleDateString('zh-CN');
  };

  return (
  <div style={{
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  padding: '16px 12px',
  background: 'var(--bg-primary)',
  }}>
  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
  <MessageOutlined style={{ color: 'var(--primary-color)' }} />
  <Text strong style={{ fontSize: 13, color: 'var(--text-primary)' }}>
  对话
  </Text>
  </div>
  <Tooltip title="收起会话列表">
  <Button
  type="text"
  size="small"
  icon={<MenuFoldOutlined />}
  onClick={toggleChatList}
  />
  </Tooltip>
  </div>

  {}
  <div style={{ marginBottom: 16 }}>
  <Button
  type="primary"
  icon={<PlusOutlined />}
  onClick={handleNewChat}
  style={{
  width: '100%',
  height: 40,
  borderRadius: 8,
  fontWeight: 500,
  }}
  >
  新建对话
  </Button>
  </div>

  {}
  <div style={{ marginBottom: 16 }}>
  <Search
  placeholder="搜索对话..."
  value={searchQuery}
  onChange={(e) => setSearchQuery(e.target.value)}
  style={{
  borderRadius: 8,
  }}
  prefix={<SearchOutlined style={{ color: '#9ca3af' }} />}
  />
  </div>

  {}
  <div style={{ flex: 1, overflow: 'hidden' }}>
  {isSessionsLoading ? (
  <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 24 }}>
  <Spin size="small" />
  </div>
  ) : (
  <List
  style={{ height: '100%', overflow: 'auto' }}
  dataSource={filteredSessions}
  locale={{ emptyText: '暂无会话' }}
  renderItem={(session) => {
  const lastTimestamp =
  session.last_message_at ?? session.updated_at ?? session.created_at;
  const titleHint = session.isUserNamed
  ? ''
  : session.titleSource && TITLE_SOURCE_HINT[session.titleSource]
  ? TITLE_SOURCE_HINT[session.titleSource]
  : undefined;

  return (
  <List.Item
  style={{
  padding: '8px 12px',
  margin: '4px 0',
  borderRadius: 8,
  background: currentSession?.id === session.id ? 'var(--bg-tertiary)' : 'transparent',
  border: currentSession?.id === session.id ? '1px solid var(--border-color)' : '1px solid transparent',
  cursor: 'pointer',
  transition: 'all var(--transition-normal)',
  }}
  onClick={() => handleSelectSession(session)}
  onMouseEnter={(e) => {
  if (currentSession?.id !== session.id) {
  e.currentTarget.style.background = 'var(--bg-tertiary)';
  }
  }}
  onMouseLeave={(e) => {
  if (currentSession?.id !== session.id) {
  e.currentTarget.style.background = 'transparent';
  }
  }}
  >
  <div
  style={{ width: '100%', display: 'flex', alignItems: 'flex-start', gap: 12 }}
  >
  <Avatar
  size={32}
  icon={<MessageOutlined />}
  style={{
  background: currentSession?.id === session.id ? 'var(--primary-color)' : 'var(--bg-tertiary)',
  color: currentSession?.id === session.id ? 'white' : 'var(--text-tertiary)',
  flexShrink: 0,
  }}
  />

  <div style={{ flex: 1, minWidth: 0 }}>
  <div
  style={{
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  marginBottom: 4,
  gap: 8,
  }}
  >
  <Tooltip title={titleHint} placement="topLeft">
  <Text
  strong={currentSession?.id === session.id}
  ellipsis
  style={{
  fontSize: 14,
  color: currentSession?.id === session.id ? 'var(--primary-color)' : 'var(--text-primary)',
  flex: 1,
  }}
  >
  {session.title || `session ${session.id.slice(-8)}`}
  </Text>
  </Tooltip>

  <Text type="secondary" style={{ fontSize: 12 }}>
  {formatTime(lastTimestamp)}
  </Text>

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
  icon={<MoreOutlined />}
  onClick={(e) => e.stopPropagation()}
  style={{
  marginLeft: 4,
  opacity: 0.6,
  flexShrink: 0,
  }}
  />
  </Dropdown>
  </div>
  <div
  style={{
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  gap: 8,
  }}
  >
  <Text
  type="secondary"
  ellipsis
  style={{ fontSize: 12, color: 'var(--text-secondary)', flex: 1 }}
  >
  {session.plan_title || '未绑定计划'}
  </Text>
  {session.is_active === false && <Tag color="gold">已归档</Tag>}
  </div>
  </div>
  </div>
  </List.Item>
  );
  }}
  />
  )}
  </div>

  {}
  {sessions.length > 0 && (
  <div style={{
  marginTop: 16,
  padding: '12px 16px',
  background: 'var(--bg-tertiary)',
  borderRadius: 8,
  textAlign: 'center'
  }}>
  <Text type="secondary" style={{ fontSize: 12 }}>
  共 {sessions.length} 个会话
  </Text>
  </div>
  )}

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

export default ChatSidebar;
