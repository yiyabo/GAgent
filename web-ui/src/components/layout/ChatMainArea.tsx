import React, { useRef, useEffect, useState, useMemo, useCallback } from 'react';
import {
  App as AntdApp,
  Input,
  Button,
  Space,
  Typography,
  Avatar,
  Divider,
  Empty,
  Alert,
  Tag,
  Tooltip,
  Switch,
  Select,
} from 'antd';
import {
  SendOutlined,
  PaperClipOutlined,
  RobotOutlined,
  UserOutlined,
  MessageOutlined,
  DatabaseOutlined,
  BulbOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { useTasksStore } from '@store/tasks';
import { useMessages } from '@/hooks/useMessages';
import ChatMessage from '@components/chat/ChatMessage';
import FileUploadButton from '@components/chat/FileUploadButton';
import UploadedFilesList from '@components/chat/UploadedFilesList';
import { shallow } from 'zustand/shallow';
import type { ChatMessage as ChatMessageType, Memory } from '@/types';
import VirtualList, { ListRef } from 'rc-virtual-list';

const { TextArea } = Input;
const { Title, Text } = Typography;

interface ChatMessageListProps {
  messages: ChatMessageType[];
  relevantMemories: Memory[];
  isProcessing: boolean;
  listHeight: number;
  onReachTop?: () => void;
  canLoadMore?: boolean;
  isHistoryLoading?: boolean;
  sessionId?: string | null;
}

type ChatListItem = ChatMessageType | { id: string; kind: 'memory_notice'; count: number };

const isMemoryNoticeItem = (
  item: ChatListItem
): item is { id: string; kind: 'memory_notice'; count: number } =>
  typeof (item as any)?.kind === 'string' && (item as any).kind === 'memory_notice';

const ChatMessageList: React.FC<ChatMessageListProps> = React.memo(
  ({
    messages,
    relevantMemories,
    isProcessing,
    listHeight,
    onReachTop,
    canLoadMore = false,
    isHistoryLoading = false,
    sessionId = null,
  }) => {
    const listRef = useRef<ListRef>(null);
    const pendingAdjustRef = useRef<{ scrollTop: number; scrollHeight: number } | null>(null);
    const loadMoreLockRef = useRef(false);
    const listData = useMemo<ChatListItem[]>(() => {
      if (relevantMemories.length === 0) {
        return messages;
      }
      return [
        { id: '__memory_notice__', kind: 'memory_notice', count: relevantMemories.length },
        ...messages,
      ];
    }, [messages, relevantMemories.length]);

    const renderItem = useCallback((item: ChatListItem) => {
      if (isMemoryNoticeItem(item)) {
        return (
          <div
            style={{
              marginBottom: 16,
              padding: '10px 14px',
              background: 'var(--bg-tertiary)',
              borderRadius: 'var(--radius-sm)',
              fontSize: 12,
              color: 'var(--text-secondary)',
            }}
          >
            <span style={{ color: 'var(--primary-color)' }}>🧠</span>
            {item.count} related memories
          </div>
        );
      }
      return (
        <div style={{ marginBottom: 16 }}>
          <ChatMessage message={item} sessionId={sessionId} />
        </div>
      );
    }, [sessionId]);

    useEffect(() => {
      if (!listRef.current || listData.length === 0) {
        return;
      }
      if (pendingAdjustRef.current || isHistoryLoading) {
        return;
      }
      listRef.current.scrollTo({ index: listData.length - 1, align: 'bottom' });
    }, [listData, isProcessing, isHistoryLoading]);

    useEffect(() => {
      if (isHistoryLoading) {
        return;
      }
      loadMoreLockRef.current = false;
      if (!pendingAdjustRef.current || !listRef.current) {
        return;
      }
      const container = listRef.current.nativeElement;
      const delta = container.scrollHeight - pendingAdjustRef.current.scrollHeight;
      if (delta > 0) {
        listRef.current.scrollTo(pendingAdjustRef.current.scrollTop + delta);
      }
      pendingAdjustRef.current = null;
    }, [isHistoryLoading, listData.length]);

    const resolvedHeight = listHeight > 0 ? listHeight : 360;
    const handleScroll = useCallback(
      (event: React.UIEvent<HTMLElement>) => {
        if (!onReachTop || !canLoadMore || isHistoryLoading || loadMoreLockRef.current) {
          return;
        }
        const target = event.currentTarget;
        if (target.scrollTop <= 80) {
          if (listRef.current) {
            pendingAdjustRef.current = {
              scrollTop: target.scrollTop,
              scrollHeight: target.scrollHeight,
            };
          }
          loadMoreLockRef.current = true;
          onReachTop();
        }
      },
      [onReachTop, canLoadMore, isHistoryLoading]
    );

    return (
      <VirtualList
        ref={listRef}
        data={listData}
        height={resolvedHeight}
        itemHeight={120}
        itemKey="id"
        onScroll={handleScroll}
        style={{
          padding: '0 24px',
          maxWidth: 900,
          margin: '0 auto',
          width: '100%',
        }}
      >
        {(item) => renderItem(item as ChatListItem)}
      </VirtualList>
    );
  }
);

const ChatMainArea: React.FC = () => {
  const { message } = AntdApp.useApp();
  const inputRef = useRef<any>(null);
  const messageContainerRef = useRef<HTMLDivElement>(null);
  const [messageAreaHeight, setMessageAreaHeight] = useState(0);

  const {
    messages,
    isProcessing,
    currentSession,
    currentPlanTitle,
    currentTaskName,
    memoryEnabled,
    relevantMemories,
    sendMessage,
    startNewSession,
    loadSessions,
    loadChatHistory,
    toggleMemory,
    defaultSearchProvider,
    setDefaultSearchProvider,
    isUpdatingProvider,
    defaultBaseModel,
    setDefaultBaseModel,
    isUpdatingBaseModel,
    defaultLLMProvider,
    setDefaultLLMProvider,
    isUpdatingLLMProvider,
    historyHasMore,
    historyBeforeId,
    historyLoading,
  } = useChatStore(
    (state) => ({
      messages: state.messages,
      isProcessing: state.isProcessing,
      currentSession: state.currentSession,
      currentPlanTitle: state.currentPlanTitle,
      currentTaskName: state.currentTaskName,
      memoryEnabled: state.memoryEnabled,
      relevantMemories: state.relevantMemories,
      sendMessage: state.sendMessage,
      startNewSession: state.startNewSession,
      loadSessions: state.loadSessions,
      loadChatHistory: state.loadChatHistory,
      toggleMemory: state.toggleMemory,
      defaultSearchProvider: state.defaultSearchProvider,
      setDefaultSearchProvider: state.setDefaultSearchProvider,
      isUpdatingProvider: state.isUpdatingProvider,
      defaultBaseModel: state.defaultBaseModel,
      setDefaultBaseModel: state.setDefaultBaseModel,
      isUpdatingBaseModel: state.isUpdatingBaseModel,
      defaultLLMProvider: state.defaultLLMProvider,
      setDefaultLLMProvider: state.setDefaultLLMProvider,
      isUpdatingLLMProvider: state.isUpdatingLLMProvider,
      historyHasMore: state.historyHasMore,
      historyBeforeId: state.historyBeforeId,
      historyLoading: state.historyLoading,
    }),
    shallow
  );

  const { selectedTask, currentPlan } = useTasksStore();
  const [inputText, setInputText] = useState('');
  const [deepThinkEnabled, setDeepThinkEnabled] = useState(false);

  const {
    data: historyData,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading: isHistoryLoadingData,
  } = useMessages(currentSession?.id);

  const allHistoryMessages = useMemo(() => {
    if (!historyData) return [];
    return [...historyData.pages].reverse().flatMap((page) => page.messages);
  }, [historyData]);

  const combinedMessages = useMemo(() => {
    const historyIds = new Set(allHistoryMessages.map((m) => m.id));
    const activeOnly = messages.filter((m) => !historyIds.has(m.id));
    return [...allHistoryMessages, ...activeOnly];
  }, [allHistoryMessages, messages]);

  useEffect(() => {
    if (!messageContainerRef.current) {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      if (!entries.length) {
        return;
      }
      setMessageAreaHeight(entries[0].contentRect.height);
    });
    observer.observe(messageContainerRef.current);
    return () => observer.disconnect();
  }, []);

  // Initialize session: prefer loading list from backend first.
  useEffect(() => {
    (async () => {
      if (currentSession) {
        return;
      }
      try {
        await loadSessions();
        const selected = useChatStore.getState().currentSession;
        if (selected) {
          await loadChatHistory(selected.id);
          return;
        }
        const session = startNewSession('AI Task Orchestration Assistant');
        await loadChatHistory(session.id);
      } catch (err) {
        console.warn('[ChatMainArea] Session initialization failed; creating a new session:', err);
        const session = startNewSession('AI Task Orchestration Assistant');
        await loadChatHistory(session.id);
      }
    })();
  }, [currentSession, loadSessions, loadChatHistory, startNewSession]);

  // Send message handler.
  const handleSendMessage = async () => {
    if (!inputText.trim() || isProcessing) return;

    const metadata = {
      task_id: selectedTask?.id ?? undefined,
      plan_title: currentPlan || currentPlanTitle || undefined,
      task_name: selectedTask?.name ?? currentTaskName ?? undefined,
    };

    // If Deep Think mode is enabled, auto-prefix with /think.
    let messageToSend = inputText.trim();
    if (deepThinkEnabled && !messageToSend.startsWith('/think')) {
      messageToSend = `/think ${messageToSend}`;
    }

    await sendMessage(messageToSend, metadata);
    setInputText('');
    inputRef.current?.focus();
  };

  const handleProviderChange = async (value: string | undefined) => {
    if (!currentSession) {
      return;
    }
    try {
      await setDefaultSearchProvider(
        (value as 'builtin' | 'perplexity' | 'tavily') ?? null
      );
    } catch (err) {
      console.error('[ChatMainArea] Failed to switch search provider:', err);
      message.error('Failed to switch search provider. Please try again later.');
    }
  };

  const handleBaseModelChange = async (value: string | undefined) => {
    if (!currentSession) {
      return;
    }
    try {
      await setDefaultBaseModel(
        (value as 'qwen3.5-plus' | 'qwen3-max-2026-01-23' | 'qwen-turbo') ?? null
      );
    } catch (err) {
      console.error('[ChatMainArea] Failed to switch base model:', err);
      message.error('Failed to switch base model. Please try again later.');
    }
  };

  const handleLLMProviderChange = async (value: string | undefined) => {
    if (!currentSession) {
      return;
    }
    try {
      await setDefaultLLMProvider(
        (value as 'qwen') ?? null
      );
    } catch (err) {
      console.error('[ChatMainArea] Failed to switch LLM provider:', err);
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

  // Keyboard handler.
  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const handleLoadMoreHistory = useCallback(async () => {
    if (!currentSession || !historyHasMore || historyLoading) {
      return;
    }
    const sessionId = currentSession.session_id ?? currentSession.id;
    await loadChatHistory(sessionId, { beforeId: historyBeforeId, append: true });
  }, [currentSession, historyHasMore, historyLoading, historyBeforeId, loadChatHistory]);

  // Quick actions.
  const quickActions = [
    { text: 'Create a new plan', action: () => setInputText('Help me create a new plan') },
    { text: 'View task status', action: () => setInputText('Show the status of all current tasks') },
    { text: 'System help', action: () => setInputText('I need help. Tell me what you can do') },
  ];

  // Render welcome view.
  const renderWelcome = () => (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      height: '100%',
      padding: '0 40px',
      textAlign: 'center',
    }}>
      <Avatar
        size={64}
        icon={<RobotOutlined />}
        style={{
          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
          marginBottom: 16,
        }}
      />

      <Title level={3} style={{ marginBottom: 12, color: '#1f2937' }}>
        AI Intelligent Task Orchestration Assistant
      </Title>

      <Text
        style={{
          fontSize: 14,
          color: '#6b7280',
          marginBottom: 24,
          lineHeight: 1.5,
        }}
      >
        I can help you create plans, decompose tasks, and orchestrate execution to make complex projects simpler and more efficient.
      </Text>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 280 }}>
        {quickActions.map((action, index) => (
          <Button
            key={index}
            size="middle"
            style={{
              height: 40,
              borderRadius: 8,
              border: '1px solid #e5e7eb',
              background: 'white',
              boxShadow: '0 1px 2px rgba(0, 0, 0, 0.05)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'flex-start',
              paddingLeft: 16,
            }}
            onClick={action.action}
          >
            <MessageOutlined style={{ marginRight: 10, color: '#6366f1', fontSize: 14 }} />
            <span style={{ color: '#374151', fontWeight: 500, fontSize: 14 }}>{action.text}</span>
          </Button>
        ))}
      </div>

      <Divider style={{ margin: '24px 0', width: '100%' }} />

      <Text type="secondary" style={{ fontSize: 13 }}>
        💡 You can describe your needs in natural language and I will understand and help execute.
      </Text>
    </div>
  );

  return (
    <div style={{
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      background: 'var(--bg-primary)',
    }}>
      {/* Header - minimal design */}
      <div style={{
        padding: '12px 20px',
        borderBottom: '1px solid var(--border-color)',
        background: 'var(--bg-primary)',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Avatar
            size={28}
            icon={<RobotOutlined />}
            style={{
              background: 'var(--primary-gradient)',
              borderRadius: 6,
            }}
          />
          <div>
            <Text strong style={{ fontSize: 14, color: 'var(--text-primary)' }}>
              {currentSession?.title || 'AI Task Orchestration Assistant'}
            </Text>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 }}>
              <Text type="secondary" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                Online
              </Text>
            </div>
          </div>
        </div>

        {/* Simplified controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {/* Search provider selector */}
          <Select
            size="small"
            value={providerValue}
            onChange={handleProviderChange}
            options={providerOptions}
            style={{ width: 130 }}
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

          {/* Memory toggle */}
          <Tooltip title={memoryEnabled ? "Memory enabled" : "Memory disabled"}>
            <Switch
              checked={memoryEnabled}
              onChange={toggleMemory}
              size="small"
              style={{
                background: memoryEnabled ? 'var(--primary-color)' : undefined,
              }}
            />
          </Tooltip>
        </div>
      </div>

      {/* Message area */}
      <div
        ref={messageContainerRef}
        style={{
          flex: 1,
          overflow: 'hidden',
          background: 'var(--bg-primary)',
          padding: '16px 0',
        }}
      >
        {messages.length === 0 ? (
          renderWelcome()
        ) : (
          <ChatMessageList
            messages={combinedMessages}
            relevantMemories={relevantMemories}
            isProcessing={isProcessing}
            listHeight={messageAreaHeight - 40}
            onReachTop={fetchNextPage}
            canLoadMore={!!hasNextPage}
            isHistoryLoading={isFetchingNextPage || isHistoryLoadingData}
            sessionId={currentSession?.session_id ?? currentSession?.id ?? null}
          />
        )}
      </div>

      {/* Input area - Claude style */}
      <div style={{
        padding: '16px 24px 20px',
        background: 'var(--bg-primary)',
        borderTop: '1px solid var(--border-color)',
        flexShrink: 0,
      }}>
        <div style={{ maxWidth: 920, margin: '0 auto' }}>
          {/* Uploaded files list */}
          <UploadedFilesList />

          <div
            style={{
              display: 'flex',
              gap: 12,
              alignItems: 'stretch',
            }}
          >
            {/* Left-side upload controls */}
            <div style={{
              display: 'flex',
              flexDirection: 'row',
              gap: 8,
              alignItems: 'center',
              background: 'var(--bg-tertiary)',
              padding: '6px 10px',
              borderRadius: 'var(--radius-md)',
            }}>
              <FileUploadButton size="small" />
              <Tooltip title={deepThinkEnabled ? 'Deep Think enabled' : 'Enable Deep Think mode'}>
                <Button
                  type={deepThinkEnabled ? 'primary' : 'text'}
                  icon={<BulbOutlined />}
                  size="small"
                  onClick={() => setDeepThinkEnabled(!deepThinkEnabled)}
                  style={{
                    color: deepThinkEnabled ? '#fff' : 'var(--text-secondary)',
                    background: deepThinkEnabled ? 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)' : 'transparent',
                    border: deepThinkEnabled ? 'none' : '1px dashed var(--border-color)',
                    transition: 'all 0.3s ease',
                  }}
                />
              </Tooltip>
            </div>

            {/* Input box - Claude style */}
            <div style={{
              flex: 1,
              background: '#FFFFFF',
              borderRadius: 'var(--radius-xl)',
              padding: '6px',
              display: 'flex',
              alignItems: 'flex-end',
              border: '2px solid var(--border-color)',
              boxShadow: '0 8px 32px -12px rgba(0, 0, 0, 0.03)',
              transition: 'var(--transition-normal)',
            }}>
              <TextArea
                ref={inputRef}
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="Describe what you need, and I'll help you complete it..."
                autoSize={{ minRows: 1, maxRows: 5 }}
                disabled={isProcessing}
                style={{
                  resize: 'none',
                  border: 'none',
                  background: 'transparent',
                  fontSize: 15,
                  lineHeight: 1.7,
                  letterSpacing: '0.018em',
                  outline: 'none',
                  boxShadow: 'none',
                }}
              />
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={handleSendMessage}
                disabled={!inputText.trim() || isProcessing}
                loading={isProcessing}
                style={{
                  height: 36,
                  borderRadius: 'var(--radius-md)',
                  minWidth: 80,
                  background: 'var(--primary-color)',
                  border: 'none',
                }}
              >
                Send
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ChatMainArea;
