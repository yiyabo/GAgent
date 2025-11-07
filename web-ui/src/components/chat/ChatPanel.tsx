import React, { useRef, useEffect } from 'react';
import { App as AntdApp, Card, Input, Button, Space, Typography, Avatar, Divider, Tooltip, Select } from 'antd';
import {
  SendOutlined,
  PaperClipOutlined,
  ReloadOutlined,
  ClearOutlined,
  RobotOutlined,
  UserOutlined,
  MessageOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { useTasksStore } from '@store/tasks';
import ChatMessage from './ChatMessage';

const { TextArea } = Input;
const { Title, Text } = Typography;

const ChatPanel: React.FC = () => {
  const { message } = AntdApp.useApp();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<any>(null);

  const {
    messages,
    inputText,
    isProcessing,
    isTyping,
    chatPanelVisible,
    setInputText,
    sendMessage,
    clearMessages,
    retryLastMessage,
    currentSession,
    defaultSearchProvider,
    setDefaultSearchProvider,
    isUpdatingProvider,
  } = useChatStore();

  const { selectedTask, currentPlan } = useTasksStore();

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // 处理发送消息
  const handleSendMessage = async () => {
    if (!inputText.trim() || isProcessing) return;

    const metadata = {
      task_id: selectedTask?.id,
      plan_title: currentPlan || undefined,
    };

    await sendMessage(inputText.trim(), metadata);
  };

  // 处理键盘事件
  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // 处理输入变化
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputText(e.target.value);
  };

  // 快捷操作
  const handleQuickAction = (action: string) => {
    const quickMessages = {
      create_plan: '帮我创建一个新的计划',
      list_tasks: '显示当前所有任务',
      system_status: '查看系统状态',
      help: '我需要帮助，请告诉我可以做什么',
    };

    const message = quickMessages[action as keyof typeof quickMessages];
    if (message) {
      setInputText(message);
      inputRef.current?.focus();
    }
  };

  const handleProviderChange = async (value: string | undefined) => {
    try {
      await setDefaultSearchProvider((value as 'builtin' | 'perplexity') ?? null);
    } catch (error) {
      console.error('切换搜索来源失败:', error);
      message.error('切换搜索来源失败，请稍后重试。');
    }
  };

  const providerOptions = [
    { label: '模型内置搜索', value: 'builtin' },
    { label: 'Perplexity 搜索', value: 'perplexity' },
  ];

  const providerValue = defaultSearchProvider ?? undefined;

  if (!chatPanelVisible) {
    return null;
  }

  return (
    <div className="chat-panel">
      {/* 聊天头部 */}
      <div className="chat-header">
        <Space align="center">
          <Avatar icon={<RobotOutlined />} size="small" />
          <div>
            <Title level={5} style={{ margin: 0 }}>
              AI 任务编排助手
            </Title>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {isProcessing ? '正在思考...' : isTyping ? '正在输入...' : '在线'}
            </Text>
          </div>
        </Space>

        <Space>
          <Tooltip title="清空对话">
            <Button
              type="text"
              size="small"
              icon={<ClearOutlined />}
              onClick={clearMessages}
            />
          </Tooltip>
        </Space>
      </div>

      {/* 消息列表 */}
      <div className="chat-messages">
        {messages.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px 20px', color: '#999' }}>
            <MessageOutlined style={{ fontSize: 32, marginBottom: 16 }} />
            <div>
              <Text>你好！我是AI任务编排助手</Text>
            </div>
            <div style={{ marginTop: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                我可以帮你创建计划、管理任务、执行调度等
              </Text>
            </div>
            
            {/* 快捷操作按钮 */}
            <div style={{ marginTop: 16 }}>
              <Space direction="vertical" size="small">
                <Button
                  size="small"
                  type="link"
                  onClick={() => handleQuickAction('create_plan')}
                >
                  📋 创建新计划
                </Button>
                <Button
                  size="small"
                  type="link"
                  onClick={() => handleQuickAction('list_tasks')}
                >
                  📝 查看任务列表
                </Button>
                <Button
                  size="small"
                  type="link"
                  onClick={() => handleQuickAction('system_status')}
                >
                  📊 系统状态
                </Button>
                <Button
                  size="small"
                  type="link"
                  onClick={() => handleQuickAction('help')}
                >
                  ❓ 帮助
                </Button>
              </Space>
            </div>
          </div>
        ) : (
          <>
            {messages.map((message) => (
              <ChatMessage key={message.id} message={message} />
            ))}
            
            {/* 正在处理指示器 */}
            {isProcessing && (
              <div className="message assistant">
                <div className="message-avatar assistant">
                  <RobotOutlined />
                </div>
                <div className="message-content">
                  <div className="message-bubble">
                    <Text>正在思考中...</Text>
                  </div>
                </div>
              </div>
            )}
          </>
        )}
        
        <div ref={messagesEndRef} />
      </div>

      {/* 上下文信息 */}
      {currentPlan && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <div style={{ padding: '0 16px 8px', fontSize: 12, color: '#666' }}>
            当前计划: {currentPlan}
          </div>
        </>
      )}

      {/* 输入区域 */}
      <div className="chat-input-area">
        <div className="chat-input-main">
          <TextArea
            ref={inputRef}
            value={inputText}
            onChange={handleInputChange}
            onKeyPress={handleKeyPress}
            placeholder="输入消息... (Shift+Enter换行，Enter发送)"
            autoSize={{ minRows: 1, maxRows: 4 }}
            disabled={isProcessing}
            style={{ flex: 1 }}
          />
          <div className="chat-input-side">
            <Select
              size="small"
              value={providerValue}
              placeholder="选择网络搜索来源"
              options={providerOptions}
              allowClear
              onChange={handleProviderChange}
              disabled={!currentSession || isProcessing}
              loading={isUpdatingProvider}
              style={{ width: '100%' }}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleSendMessage}
              disabled={!inputText.trim() || isProcessing}
              loading={isProcessing}
              style={{ width: '100%' }}
            />
          </div>
        </div>

        <div style={{ marginTop: 8, display: 'flex', justifyContent: 'space-between' }}>
          <Space size="small">
            <Tooltip title="附件">
              <Button 
                type="text" 
                size="small" 
                icon={<PaperClipOutlined />}
                disabled
              />
            </Tooltip>
          </Space>

          <Space size="small">
            <Tooltip title="重试">
              <Button
                type="text"
                size="small"
                icon={<ReloadOutlined />}
                onClick={retryLastMessage}
                disabled={isProcessing || messages.length === 0}
              />
            </Tooltip>
          </Space>
        </div>
      </div>
    </div>
  );
};

export default ChatPanel;
