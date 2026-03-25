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
import { shallow } from 'zustand/shallow';
import { resolveChatSessionProcessingKey } from '@/utils/chatSessionKeys';
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
  } = useChatStore(
    (state) => ({
      messages: state.messages,
      inputText: state.inputText,
      isProcessing: state.processingSessionIds.has(
        resolveChatSessionProcessingKey(state.currentSession)
      ),
      isTyping: state.isTyping,
      chatPanelVisible: state.chatPanelVisible,
      setInputText: state.setInputText,
      sendMessage: state.sendMessage,
      clearMessages: state.clearMessages,
      retryLastMessage: state.retryLastMessage,
      currentSession: state.currentSession,
      defaultSearchProvider: state.defaultSearchProvider,
      setDefaultSearchProvider: state.setDefaultSearchProvider,
      isUpdatingProvider: state.isUpdatingProvider,
      defaultBaseModel: state.defaultBaseModel,
      setDefaultBaseModel: state.setDefaultBaseModel,
      isUpdatingBaseModel: state.isUpdatingBaseModel,
      defaultLLMProvider: state.defaultLLMProvider,
      setDefaultLLMProvider: state.setDefaultLLMProvider,
      isUpdatingLLMProvider: state.isUpdatingLLMProvider,
    }),
    shallow
  );

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

  // Send message handler.
  const handleSendMessage = async () => {
    const draft = inputText.trim();
    if (!draft || isProcessing) return;

    const metadata = {
      task_id: selectedTask?.id,
      plan_title: currentPlan || undefined,
    };

    setInputText('');
    try {
      await sendMessage(draft, metadata);
    } catch (error) {
      setInputText(draft);
      message.error('Failed to send message. Please try again.');
    }
  };

  // Keyboard handler.
  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // Input change handler.
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputText(e.target.value);
  };

  // Quick actions.
  const handleQuickAction = (action: string) => {
    const quickMessages = {
      create_plan: 'Help me create a new plan',
      list_tasks: 'Show all current tasks',
      system_status: 'Check system status',
      help: 'I need help. Tell me what you can do',
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
      console.error('Failed to switch search provider:', error);
      message.error('Failed to switch search provider. Please try again later.');
    }
  };

  const handleBaseModelChange = async (value: string | undefined) => {
    try {
      await setDefaultBaseModel(
        (value as 'qwen3.5-plus' | 'qwen3-max-2026-01-23' | 'qwen-turbo') ?? null
      );
    } catch (error) {
      console.error('Failed to switch base model:', error);
      message.error('Failed to switch base model. Please try again later.');
    }
  };

  const handleLLMProviderChange = async (value: string | undefined) => {
    try {
      await setDefaultLLMProvider(
        (value as 'qwen') ?? null
      );
    } catch (error) {
      console.error('Failed to switch LLM provider:', error);
      message.error('Failed to switch LLM provider. Please try again later.');
    }
  };

  const providerOptions = [
    { label: 'Built-in Search', value: 'builtin' },
    { label: 'Perplexity Search', value: 'perplexity' },
    { label: 'Tavily MCP Search', value: 'tavily' },
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
      {/* Chat header */}
      <div className="chat-header">
        <Space align="center">
          <Avatar icon={<RobotOutlined />} size="small" />
          <div>
            <Title level={5} style={{ margin: 0 }}>
              AI Task Orchestration Assistant
            </Title>
            <Text type="secondary" style={{ fontSize: 12 }}>
              Online
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
            placeholder="Search provider"
            disabled={isUpdatingProvider}
          />
          <Select
            size="small"
            value={llmProviderValue}
            onChange={handleLLMProviderChange}
            options={llmProviderOptions}
            style={{ width: 120 }}
            placeholder="LLM provider"
            disabled={isUpdatingLLMProvider}
          />
          <Select
            size="small"
            value={baseModelValue}
            onChange={handleBaseModelChange}
            options={baseModelOptions}
            style={{ width: 140 }}
            placeholder="Base model"
            disabled={isUpdatingBaseModel}
          />
          <Tooltip title="Clear chat">
            <Button
              type="text"
              size="small"
              icon={<ClearOutlined />}
              onClick={clearMessages}
            />
          </Tooltip>
        </Space>
      </div>

      {/* Message list */}
      <div className="chat-messages" ref={messagesContainerRef}>
        {messages.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-tertiary)' }}>
            <MessageOutlined style={{ fontSize: 32, marginBottom: 16, color: 'var(--primary-color)' }} />
            <div>
              <Text style={{ color: 'var(--text-primary)' }}>Hello! I am your AI Task Orchestration Assistant.</Text>
            </div>
            <div style={{ marginTop: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                I can help you create plans, manage tasks, and orchestrate execution.
              </Text>
            </div>
            
            {/* Quick action buttons */}
            <div style={{ marginTop: 16 }}>
              <Space direction="vertical" size="small">
                <Button
                  size="small"
                  type="link"
                  onClick={() => handleQuickAction('create_plan')}
                >
                  📋 Create a new plan
                </Button>
                <Button
                  size="small"
                  type="link"
                  onClick={() => handleQuickAction('list_tasks')}
                >
                  📝 View task list
                </Button>
                <Button
                  size="small"
                  type="link"
                  onClick={() => handleQuickAction('system_status')}
                >
                  📊 System status
                </Button>
                <Button
                  size="small"
                  type="link"
                  onClick={() => handleQuickAction('help')}
                >
                  ❓ Help
                </Button>
              </Space>
            </div>
          </div>
        ) : (
          <>
            {messages.map((message) => (
              <ChatMessage
                key={message.id}
                message={message}
                sessionId={currentSession?.session_id ?? currentSession?.id ?? null}
              />
            ))}
            
           
          </>
        )}
      </div>

      {/* Context info */}
      {currentPlan && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <div style={{ padding: '0 16px 8px', fontSize: 12, color: 'var(--text-secondary)' }}>
            Current plan: {currentPlan}
          </div>
        </>
      )}

      {/* Input area */}
      <div className="chat-input-area">
        {/* Uploaded files list */}
        <UploadedFilesList />
        
        <div className="chat-input-main" style={{ alignItems: 'stretch' }}>
          {/* Upload button */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, justifyContent: 'center', padding: '6px 8px' }}>
            <FileUploadButton size="small" />
          </div>

          <TextArea
            ref={inputRef}
            value={inputText}
            onChange={handleInputChange}
            onKeyPress={handleKeyPress}
            placeholder="Type a message... (Shift+Enter for newline, Enter to send)"
            autoSize={{ minRows: 1, maxRows: 4 }}
            disabled={isProcessing}
            style={{ flex: 1, margin: 0 }}
          />
          
          {/* Send button */}
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={handleSendMessage}
            disabled={!inputText.trim() || isProcessing}
            loading={isProcessing}
            style={{ height: 'auto', minHeight: 36, alignSelf: 'center' }}
          >
            Send
          </Button>
        </div>
      </div>
    </div>
  );
};

export default ChatPanel;
