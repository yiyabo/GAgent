import React, { useState } from 'react';
import { 
  Button, 
  List, 
  Typography, 
  Input, 
  Space, 
  Avatar,
  Tooltip,
  Dropdown,
  MenuProps
} from 'antd';
import {
  PlusOutlined,
  SearchOutlined,
  MessageOutlined,
  MoreOutlined,
  EditOutlined,
  DeleteOutlined,
  ExportOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { ChatSession } from '@/types';

const { Text, Title } = Typography;
const { Search } = Input;

const ChatSidebar: React.FC = () => {
  const {
    sessions,
    currentSession,
    setCurrentSession,
    startNewSession,
    removeSession,
  } = useChatStore();

  const [searchQuery, setSearchQuery] = useState('');

  // 过滤对话列表
  const filteredSessions = sessions.filter(session =>
    session.title.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // 处理新建对话
  const handleNewChat = () => {
    const newSession = startNewSession();
    setCurrentSession(newSession);
  };

  // 处理选择对话
  const handleSelectSession = (session: ChatSession) => {
    setCurrentSession(session);
  };

  // 会话操作菜单
  const getSessionMenuItems = (session: ChatSession): MenuProps['items'] => [
    {
      key: 'rename',
      label: '重命名',
      icon: <EditOutlined />,
    },
    {
      key: 'export',
      label: '导出对话',
      icon: <ExportOutlined />,
    },
    {
      type: 'divider',
    },
    {
      key: 'delete',
      label: '删除对话',
      icon: <DeleteOutlined />,
      danger: true,
      onClick: () => removeSession(session.id),
    },
  ];

  // 格式化时间
  const formatTime = (date: Date) => {
    const now = new Date();
    const diff = now.getTime() - new Date(date).getTime();
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    
    if (days === 0) {
      return new Date(date).toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
      });
    } else if (days === 1) {
      return '昨天';
    } else if (days < 7) {
      return `${days}天前`;
    } else {
      return new Date(date).toLocaleDateString('zh-CN');
    }
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
          renderItem={(session) => (
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
              <div style={{ width: '100%', display: 'flex', alignItems: 'flex-start', gap: 12 }}>
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
                  <div style={{ 
                    display: 'flex', 
                    justifyContent: 'space-between', 
                    alignItems: 'flex-start',
                    marginBottom: 4,
                  }}>
                    <Text 
                      strong={currentSession?.id === session.id}
                      ellipsis
                      style={{ 
                        fontSize: 14,
                        color: currentSession?.id === session.id ? '#1976d2' : '#333',
                        flex: 1,
                      }}
                    >
                      {session.title}
                    </Text>
                    
                    <Dropdown 
                      menu={{ items: getSessionMenuItems(session) }}
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
                  
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Text 
                      type="secondary" 
                      style={{ fontSize: 12 }}
                      ellipsis
                    >
                      {session.messages.length > 0 
                        ? session.messages[session.messages.length - 1].content.slice(0, 30) + '...'
                        : '暂无消息'
                      }
                    </Text>
                    
                    <Text 
                      type="secondary" 
                      style={{ fontSize: 11, flexShrink: 0, marginLeft: 8 }}
                    >
                      {formatTime(session.updated_at)}
                    </Text>
                  </div>
                </div>
              </div>
            </List.Item>
          )}
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
