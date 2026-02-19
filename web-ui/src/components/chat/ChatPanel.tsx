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
import FileUploadButton from './FileUploadButton';
import UploadedFilesList from './UploadedFilesList';

const { TextArea } = Input;
const { Title, Text } = Typography;

const ChatPanel: React.FC = () => {
  const { message } = AntdApp.useApp();
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const scrollRafRef = useRef<number | null>(null);
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
    defaultBaseModel,
    setDefaultBaseModel,
    isUpdatingBaseModel,
    defaultLLMProvider,
    setDefaultLLMProvider,
    isUpdatingLLMProvider,
  } = useChatStore();

  const { selectedTask, currentPlan } = useTasksStore();

  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) {
      return;
    }

    const updateAutoScroll = () => {
      const distanceToBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight;
      autoScrollRef.current = distanceToBottom < 120;
    };

    updateAutoScroll();
    container.addEventListener('scroll', updateAutoScroll, { passive: true });
    return () => {
      container.removeEventListener('scroll', updateAutoScroll);
    };
  }, []);

  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) {
      return;
    }
    if (!autoScrollRef.current) {
      return;
    }
    if (scrollRafRef.current !== null) {
      return;
    }
    scrollRafRef.current = window.requestAnimationFrame(() => {
      scrollRafRef.current = null;
      if (!autoScrollRef.current) {
        return;
      }
      container.scrollTo({
        top: container.scrollHeight,
        behavior: isProcessing ? 'auto' : 'smooth',
      });
    });
  }, [messages, isProcessing]);

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
      await setDefaultSearchProvider(
        (value as 'builtin' | 'perplexity' | 'tavily') ?? null
      );
    } catch (error) {
      console.error('切换搜索来源失败:', error);
      message.error('切换搜索来源失败，请稍后重试。');
    }
  };

  const handleBaseModelChange = async (value: string | undefined) => {
    try {
      await setDefaultBaseModel(
        (value as 'qwen3-max-2026-01-23' | 'qwen-turbo') ?? null
      );
    } catch (error) {
      console.error('切换基座模型失败:', error);
      message.error('切换基座模型失败，请稍后重试。');
    }
  };

  const handleLLMProviderChange = async (value: string | undefined) => {
    try {
      await setDefaultLLMProvider(
        (value as 'qwen') ?? null
      );
    } catch (error) {
      console.error('切换LLM提供商失败:', error);
      message.error('切换LLM提供商失败，请稍后重试。');
    }
  };

  const providerOptions = [
    { label: '模型内置搜索', value: 'builtin' },
    { label: 'Perplexity 搜索', value: 'perplexity' },
    { label: 'Tavily MCP 搜索', value: 'tavily' },
  ];

  const providerValue = defaultSearchProvider ?? undefined;
  const baseModelValue = defaultBaseModel ?? undefined;
  const llmProviderValue = defaultLLMProvider ?? undefined;

  const llmProviderOptions = [
    { label: 'Qwen', value: 'qwen' },
  ];

  const baseModelOptions = [
    { label: 'Qwen3.5-Plus', value: 'qwen3.5-plus' },
    { label: 'Qwen3-Max (2026-01-23)', value: 'qwen3-max-2026-01-23' },
    { label: 'Qwen-Turbo', value: 'qwen-turbo' },
  ];

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
              在线
            </Text>
          </div>
        </Space>

        <Space size="small">
          <Select
            size="small"
            value={providerValue}
            onChange={handleProviderChange}
            options={providerOptions}
            style={{ width: 140 }}
            placeholder="选择搜索来源"
            disabled={isUpdatingProvider}
          />
          <Select
            size="small"
            value={llmProviderValue}
            onChange={handleLLMProviderChange}
            options={llmProviderOptions}
            style={{ width: 120 }}
            placeholder="LLM 提供商"
            disabled={isUpdatingLLMProvider}
          />
          <Select
            size="small"
            value={baseModelValue}
            onChange={handleBaseModelChange}
            options={baseModelOptions}
            style={{ width: 140 }}
            placeholder="基座模型"
            disabled={isUpdatingBaseModel}
          />
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
      <div className="chat-messages" ref={messagesContainerRef}>
        {messages.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-tertiary)' }}>
            <MessageOutlined style={{ fontSize: 32, marginBottom: 16, color: 'var(--primary-color)' }} />
            <div>
              <Text style={{ color: 'var(--text-primary)' }}>你好！我是AI任务编排助手</Text>
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
            
           
          </>
        )}
      </div>

      {/* 上下文信息 */}
      {currentPlan && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <div style={{ padding: '0 16px 8px', fontSize: 12, color: 'var(--text-secondary)' }}>
            当前计划: {currentPlan}
          </div>
        </>
      )}

      {/* 输入区域 */}
      <div className="chat-input-area">
        {/* 上传文件列表 */}
        <UploadedFilesList />
        
        <div className="chat-input-main" style={{ alignItems: 'stretch' }}>
          {/* 上传按钮 */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, justifyContent: 'center', padding: '6px 8px' }}>
            <FileUploadButton size="small" />
          </div>

          <TextArea
            ref={inputRef}
            value={inputText}
            onChange={handleInputChange}
            onKeyPress={handleKeyPress}
            placeholder="输入消息... (Shift+Enter换行，Enter发送)"
            autoSize={{ minRows: 1, maxRows: 4 }}
            disabled={isProcessing}
            style={{ flex: 1, margin: 0 }}
          />
          
          {/* 发送按钮 */}
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={handleSendMessage}
            disabled={!inputText.trim() || isProcessing}
            loading={isProcessing}
            style={{ height: 'auto', minHeight: 36, alignSelf: 'center' }}
          >
            发送
          </Button>
        </div>
      </div>
    </div>
  );
};

export default ChatPanel;
