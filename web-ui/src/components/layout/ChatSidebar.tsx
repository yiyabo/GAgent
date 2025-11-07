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
} from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { ChatSession } from '@/types';

const { Text } = Typography;
const { Search } = Input;

const TITLE_SOURCE_HINT: Record<string, string> = {
  plan: '基于计划标题自动生成',
  'plan_task': '基于计划和任务自动生成',
  heuristic: '基于最近对话内容自动生成',
  llm: '由模型自动总结',
  default: '默认标题，建议重新生成',
  local: '临时标题，建议重新生成',
  user: '用户自定义标题',
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
  } = useChatStore();

  const [searchQuery, setSearchQuery] = useState('');

  // 过滤对话列表
  const normalizedQuery = searchQuery.trim().toLowerCase();
  const filteredSessions = sessions.filter((session) => {
    if (!normalizedQuery) {
      return true;
    }
    const title = session.title?.toLowerCase?.() ?? '';
    const planTitle = session.plan_title?.toLowerCase?.() ?? '';
    return title.includes(normalizedQuery) || planTitle.includes(normalizedQuery);
  });

  // 处理新建对话
  const handleNewChat = () => {
    const newSession = startNewSession();
    setCurrentSession(newSession);
  };

  // 处理选择对话
  const handleSelectSession = async (session: ChatSession) => {
    // 先切换会话
    setCurrentSession(session);
    
    // 如果会话没有消息，尝试从后端加载历史
    if (session.messages.length === 0 && session.session_id) {
      console.log('🔄 [ChatSidebar] 加载会话历史:', session.session_id);
      try {
        await loadChatHistory(session.session_id);
      } catch (err) {
        console.warn('加载会话历史失败:', err);
      }
    }
  };

  const handleArchiveSession = async (session: ChatSession) => {
    try {
      await deleteSession(session.id, { archive: true });
      message.success('会话已归档');
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      message.error(`归档失败：${errMsg}`);
    }
  };

  const performDeleteSession = async (session: ChatSession) => {
    try {
      await deleteSession(session.id);
      message.success('会话已删除');
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      message.error(`删除失败：${errMsg}`);
      throw error;
    }
  };

  const confirmDeleteSession = (session: ChatSession) => {
    Modal.confirm({
      title: '删除对话',
      icon: <ExclamationCircleOutlined />, 
      content: `删除后将无法恢复该对话「${session.title || session.id}」，确定继续吗？`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
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
      const result = await autotitleSession(sessionId, { force: true });
      if (!result) {
        return;
      }
      if (result.updated) {
        message.success(`标题已更新为「${result.title}」`);
      } else {
        message.info('标题已保持不变');
      }
    } catch (error) {
      console.error('重新生成标题失败:', error);
      message.error('重新生成标题失败，请稍后重试');
    }
  };

  // 会话操作菜单
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
      {
        key: 'export',
        label: '导出对话',
        icon: <ExportOutlined />,
      },
    ];

    if (session.is_active !== false) {
      items.push({
        key: 'archive',
        label: '归档对话',
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
      label: '删除对话',
      icon: <DeleteOutlined />,
      danger: true,
      onClick: (_info: any) => {
        _info?.domEvent?.stopPropagation?.();
        confirmDeleteSession(session);
      },
    });

    return items;
  };

  // 格式化时间
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
      return `${days}天前`;
    }
    return date.toLocaleDateString('zh-CN');
  };

  return (
    <div style={{ 
      height: '100%', 
      display: 'flex', 
      flexDirection: 'column',
      padding: '16px 12px'
    }}>
      {/* 头部 - 新建对话 */}
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

      {/* 搜索框 */}
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

      {/* 对话列表 */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <List
          style={{ height: '100%', overflow: 'auto' }}
          dataSource={filteredSessions}
          renderItem={(session) => {
            const lastTimestamp =
              session.last_message_at ?? session.updated_at ?? session.created_at;
            const titleHint = session.isUserNamed
              ? '用户自定义标题'
              : session.titleSource && TITLE_SOURCE_HINT[session.titleSource]
              ? TITLE_SOURCE_HINT[session.titleSource]
              : undefined;

            return (
              <List.Item
                style={{
                  padding: '8px 12px',
                  margin: '4px 0',
                  borderRadius: 8,
                  background: currentSession?.id === session.id ? '#e3f2fd' : 'transparent',
                border: currentSession?.id === session.id ? '1px solid #2196f3' : '1px solid transparent',
                cursor: 'pointer',
                transition: 'all 0.2s ease',
              }}
              onClick={() => handleSelectSession(session)}
              onMouseEnter={(e) => {
                if (currentSession?.id !== session.id) {
                  e.currentTarget.style.background = '#f5f5f5';
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
                    background: currentSession?.id === session.id ? '#2196f3' : '#f0f0f0',
                    color: currentSession?.id === session.id ? 'white' : '#999',
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
                          color: currentSession?.id === session.id ? '#1976d2' : '#333',
                          flex: 1,
                        }}
                      >
                        {session.title || `会话 ${session.id.slice(-8)}`}
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
                      style={{ fontSize: 12, color: '#6b7280', flex: 1 }}
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
      </div>

      {/* 底部统计信息 */}
      {sessions.length > 0 && (
        <div style={{ 
          marginTop: 16, 
          padding: '12px 16px',
          background: '#f8f9fa',
          borderRadius: 8,
          textAlign: 'center'
        }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            共 {sessions.length} 个对话
          </Text>
        </div>
      )}
    </div>
  );
};

export default ChatSidebar;
