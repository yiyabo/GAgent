import React, { useState } from 'react';
import {
  Avatar,
  Button,
  Dropdown,
  Input,
  List,
  MenuProps,
  Modal,
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
  ExportOutlined,
  ExclamationCircleOutlined,
  InboxOutlined,
  ReloadOutlined,
  MenuFoldOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { useLayoutStore } from '@store/layout';
import { ChatSession } from '@/types';

const { Text } = Typography;
const { Search } = Input;

const TITLE_SOURCE_HINT: Record<string, string> = {
  plan: 'plan',
  'plan_task': 'plantask',
  heuristic: 'content',
  llm: 'model',
  default: 'default, recommendation',
  local: ', recommendation',
  user: '',
};

import { useSessions, useDeleteSession, useAutoTitleSession, useUpdateSession } from '@/hooks/useSessions';
import { summaryToChatSession } from '@store/chatUtils';

const ChatSidebar: React.FC = () => {
  const { data: sessions = [], isLoading: isSessionsLoading } = useSessions();
  const { mutateAsync: deleteSessionMutation } = useDeleteSession();
  const { mutateAsync: autotitleMutation } = useAutoTitleSession();
  const { mutateAsync: updateSessionMutation } = useUpdateSession();

  const {
  currentSession,
  setCurrentSession,
  startNewSession,
  loadChatHistory,
  } = useChatStore();
  const { toggleChatList } = useLayoutStore();

  const [searchQuery, setSearchQuery] = useState('');

  const normalizedQuery = searchQuery.trim().toLowerCase();
  const filteredSessions = sessions.filter((session) => {
  if (!normalizedQuery) {
  return true;
  }
  const title = session.title?.toLowerCase?.() ?? '';
  const planTitle = session.plan_title?.toLowerCase?.() ?? '';
  return title.includes(normalizedQuery) || planTitle.includes(normalizedQuery);
  });

  const handleNewChat = () => {
  const newSession = startNewSession();
  setCurrentSession(newSession);
  };

  const handleSelectSession = async (session: ChatSession) => {
  setCurrentSession(session);

  if (session.messages.length === 0 && session.session_id) {
  console.log('🔄 [ChatSidebar] loading session:', session.session_id);
  try {
  await loadChatHistory(session.session_id);
  } catch (err) {
  console.warn('Failed to load session history:', err);
  }
  }
  };

  const handleArchiveSession = async (session: ChatSession) => {
  try {
  await deleteSessionMutation({ sessionId: session.id, options: { archive: true } });
  message.success('Session archived');
  } catch (error) {
  const errMsg = error instanceof Error ? error.message : String(error);
  message.error(`Archive failed: ${errMsg}`);
  }
  };

  const handleRenameSession = async (sessionId: string, newTitle: string) => {
  try {
  await updateSessionMutation({ sessionId, payload: { name: newTitle } });
  message.success('Session renamed');
  } catch (error) {
  const errMsg = error instanceof Error ? error.message : String(error);
  message.error(`Rename failed: ${errMsg}`);
  }
  };

  const performDeleteSession = async (session: ChatSession) => {
  try {
  await deleteSessionMutation({ sessionId: session.id });
  message.success('sessiondelete');
  } catch (error) {
  const errMsg = error instanceof Error ? error.message : String(error);
  message.error(`Delete failed: ${errMsg}`);
  throw error;
  }
  };

  const confirmDeleteSession = (session: ChatSession) => {
  Modal.confirm({
  title: 'delete',
  icon: <ExclamationCircleOutlined />,
  content: `delete「${session.title || session.id}」, ？`,
  okText: 'delete',
  okType: 'danger',
  cancelText: 'cancel',
  onOk: () => performDeleteSession(session),
  });
  };

  const handleSessionMenuAction = async (session: ChatSession, key: string) => {
  if (key !== 'autotitle') {
  return;
  }

  const sessionId = session.session_id ?? session.id;
  if (!sessionId) {
  return;
  }

  try {
  const result = await autotitleMutation({ sessionId, options: { force: true } });
  if (!result) {
  return;
  }
  if (result.updated) {
  message.success(`update「${result.title}」`);
  } else {
  message.info('');
  }
  } catch (error) {
  console.error('Session operation failed:', error);
  message.error('failed, please');
  }
  };

  const getSessionMenuItems = (session: ChatSession): MenuProps['items'] => {
  const items: MenuProps['items'] = [
  {
  key: 'rename',
  label: '',
  icon: <EditOutlined />,
  },
  {
  key: 'autotitle',
  label: '',
  icon: <ReloadOutlined />,
  },
  {
  key: 'export',
  label: '',
  icon: <ExportOutlined />,
  },
  ];

  if (session.is_active !== false) {
  items.push({
  key: 'archive',
  label: '',
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
  label: 'delete',
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
  return '';
  } else if (days < 7) {
  return `${days}`;
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
  
  </Text>
  </div>
  <Tooltip title="">
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
  
  </Button>
  </div>

  {}
  <div style={{ marginBottom: 16 }}>
  <Search
  placeholder="search..."
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
  <List
  style={{ height: '100%', overflow: 'auto' }}
  dataSource={filteredSessions}
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
  {session.plan_title || 'plan'}
  </Text>
  {session.is_active === false && <Tag color="gold"></Tag>}
  </div>
  </div>
  </div>
  </List.Item>
  );
  }}
  />
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
  {sessions.length} 
  </Text>
  </div>
  )}
  </div>
  );
};

export default ChatSidebar;
