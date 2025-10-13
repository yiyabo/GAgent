import React from 'react';
import { Avatar, Typography, Space, Button, Tooltip } from 'antd';
import { 
  UserOutlined, 
  RobotOutlined, 
  InfoCircleOutlined,
  CopyOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { ChatMessage as ChatMessageType } from '@/types';
import ReactMarkdown from 'markdown-to-jsx';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { tomorrow } from 'react-syntax-highlighter/dist/esm/styles/prism';

const { Text } = Typography;

interface ChatMessageProps {
  message: ChatMessageType;
}

const ChatMessage: React.FC<ChatMessageProps> = ({ message }) => {
  const { type, content, timestamp, metadata } = message;

  // 复制消息内容
  const handleCopy = () => {
    navigator.clipboard.writeText(content);
  };

  // 格式化时间
  const formatTime = (date: Date) => {
    return new Date(date).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  // 渲染头像
  const renderAvatar = () => {
    const avatarProps = {
      size: 32 as const,
      style: { flexShrink: 0 },
    };

    switch (type) {
      case 'user':
        return (
          <Avatar 
            {...avatarProps}
            icon={<UserOutlined />}
            style={{ ...avatarProps.style, backgroundColor: '#1890ff' }}
          />
        );
      case 'assistant':
        return (
          <Avatar 
            {...avatarProps}
            icon={<RobotOutlined />}
            style={{ ...avatarProps.style, backgroundColor: '#52c41a' }}
          />
        );
      case 'system':
        return (
          <Avatar 
            {...avatarProps}
            icon={<InfoCircleOutlined />}
            style={{ ...avatarProps.style, backgroundColor: '#faad14' }}
          />
        );
      default:
        return null;
    }
  };

  // 自定义代码块渲染
  const CodeBlock = ({ children, className }: { children: string; className?: string }) => {
    const language = className?.replace('lang-', '') || 'text';
    
    return (
      <SyntaxHighlighter
        style={tomorrow}
        language={language}
        PreTag="div"
        customStyle={{
          margin: '8px 0',
          borderRadius: '6px',
          fontSize: '13px',
        }}
      >
        {children}
      </SyntaxHighlighter>
    );
  };

  // 渲染消息内容
  const renderContent = () => {
    if (type === 'system') {
      return (
        <Text type="secondary" style={{ fontStyle: 'italic' }}>
          {content}
        </Text>
      );
    }

    return (
      <ReactMarkdown
        options={{
          overrides: {
            code: {
              component: CodeBlock,
            },
            pre: {
              component: ({ children }: { children: React.ReactNode }) => (
                <div>{children}</div>
              ),
            },
            p: {
              props: {
                style: { margin: '0 0 8px 0', lineHeight: 1.6 },
              },
            },
            ul: {
              props: {
                style: { margin: '8px 0', paddingLeft: '20px' },
              },
            },
            ol: {
              props: {
                style: { margin: '8px 0', paddingLeft: '20px' },
              },
            },
            blockquote: {
              props: {
                style: {
                  borderLeft: '4px solid #d9d9d9',
                  paddingLeft: '12px',
                  margin: '8px 0',
                  color: '#666',
                  fontStyle: 'italic',
                },
              },
            },
          },
        }}
      >
        {content}
      </ReactMarkdown>
    );
  };

  // 渲染元数据
  const renderMetadata = () => {
    if (!metadata) return null;

    return (
      <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
        {metadata.task_id && (
          <div>关联任务: #{metadata.task_id}</div>
        )}
        {metadata.plan_title && (
          <div>关联计划: {metadata.plan_title}</div>
        )}
      </div>
    );
  };

  return (
    <div className={`message ${type}`}>
      {renderAvatar()}
      
      <div className="message-content">
        <div className="message-bubble">
          {renderContent()}
          {renderMetadata()}
        </div>
        
        <div className="message-time">
          <Space size="small">
            <Text type="secondary" style={{ fontSize: 12 }}>
              {formatTime(timestamp)}
            </Text>
            
            {type !== 'system' && (
              <Space size={4}>
                <Tooltip title="复制">
                  <Button
                    type="text"
                    size="small"
                    icon={<CopyOutlined />}
                    onClick={handleCopy}
                    style={{ fontSize: 10, padding: '0 4px' }}
                  />
                </Tooltip>
                
                {type === 'assistant' && (
                  <Tooltip title="重新生成">
                    <Button
                      type="text"
                      size="small"
                      icon={<ReloadOutlined />}
                      style={{ fontSize: 10, padding: '0 4px' }}
                    />
                  </Tooltip>
                )}
              </Space>
            )}
          </Space>
        </div>
      </div>
    </div>
  );
};

export default ChatMessage;
