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
            {item.count} 条相关记忆
          </div>
        );
      }
      return (
        <div style={{ marginBottom: 16 }}>
          <ChatMessage message={item} />
        </div>
      );
    }, []);

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

  // 初始化会话：优先从后端加载列表
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
        const session = startNewSession('AI 任务编排助手');
        await loadChatHistory(session.id);
      } catch (err) {
        console.warn('[ChatMainArea] 会话初始化失败，尝试创建新会话:', err);
        const session = startNewSession('AI 任务编排助手');
        await loadChatHistory(session.id);
      }
    })();
  }, [currentSession, loadSessions, loadChatHistory, startNewSession]);

  // 处理发送消息
  const handleSendMessage = async () => {
    if (!inputText.trim() || isProcessing) return;

    const metadata = {
      task_id: selectedTask?.id ?? undefined,
      plan_title: currentPlan || currentPlanTitle || undefined,
      task_name: selectedTask?.name ?? currentTaskName ?? undefined,
    };

    await sendMessage(inputText.trim(), metadata);
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
      console.error('[ChatMainArea] 切换搜索来源失败:', err);
      message.error('切换搜索来源失败，请稍后重试。');
    }
  };

  const handleBaseModelChange = async (value: string | undefined) => {
    if (!currentSession) {
      return;
    }
    try {
      await setDefaultBaseModel(
        (value as 'qwen3-max' | 'glm-4.6' | 'kimi-k2-thinking' | 'gpt-5.2-2025-12-11') ?? null
      );
    } catch (err) {
      console.error('[ChatMainArea] 切换基座模型失败:', err);
      message.error('切换基座模型失败，请稍后重试。');
    }
  };

  const handleLLMProviderChange = async (value: string | undefined) => {
    if (!currentSession) {
      return;
    }
    try {
      await setDefaultLLMProvider(
        (value as 'glm' | 'qwen' | 'openai' | 'perplexity') ?? null
      );
    } catch (err) {
      console.error('[ChatMainArea] 切换LLM提供商失败:', err);
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
    { label: 'GLM', value: 'glm' },
    { label: 'Qwen', value: 'qwen' },
    { label: 'OpenAI', value: 'openai' },
    { label: 'Perplexity', value: 'perplexity' },
  ];

  const baseModelOptions = [
    { label: 'Qwen3-Max', value: 'qwen3-max' },
    { label: 'GLM-4.6', value: 'glm-4.6' },
    { label: 'Kimi K2 Thinking', value: 'kimi-k2-thinking' },
    { label: 'GPT-5.2', value: 'gpt-5.2-2025-12-11' },
  ];

  // 处理键盘事件
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

  // 快捷操作
  const quickActions = [
    { text: '创建新计划', action: () => setInputText('帮我创建一个新的计划') },
    { text: '查看任务状态', action: () => setInputText('显示当前所有任务的状态') },
    { text: '系统帮助', action: () => setInputText('我需要帮助，请告诉我可以做什么') },
  ];

  // 渲染欢迎界面
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
        AI 智能任务编排助手
      </Title>

      <Text
        style={{
          fontSize: 14,
          color: '#6b7280',
          marginBottom: 24,
          lineHeight: 1.5,
        }}
      >
        我可以帮你创建计划、分解任务、执行调度，让复杂的项目变得简单高效
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
        💡 你可以直接输入自然语言描述你的需求，我会智能理解并帮助执行
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
      {/* 头部信息 - 极简设计 */}
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
              {currentSession?.title || 'AI 任务编排助手'}
            </Text>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 }}>
              <Text type="secondary" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                在线
              </Text>
            </div>
          </div>
        </div>

        {/* 简化操作区 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {/* 搜索提供商选择器 */}
          <Select
            size="small"
            value={providerValue}
            onChange={handleProviderChange}
            options={providerOptions}
            style={{ width: 130 }}
            placeholder="搜索来源"
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

          {/* Memory 开关 */}
          <Tooltip title={memoryEnabled ? "记忆已启用" : "记忆已禁用"}>
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

      {/* 消息区域 */}
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
          />
        )}
      </div>

      {/* 输入区域 - Claude 风格 */}
      <div style={{
        padding: '16px 24px 20px',
        background: 'var(--bg-primary)',
        borderTop: '1px solid var(--border-color)',
        flexShrink: 0,
      }}>
        <div style={{ maxWidth: 920, margin: '0 auto' }}>
          {/* 上传文件列表 */}
          <UploadedFilesList />

          <div
            style={{
              display: 'flex',
              gap: 12,
              alignItems: 'stretch',
            }}
          >
            {/* 左侧上传按钮组 */}
            <div style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              justifyContent: 'center',
              background: 'var(--bg-tertiary)',
              padding: '6px 10px',
              borderRadius: 'var(--radius-md)',
            }}>
              <FileUploadButton size="small" />
            </div>

            {/* 输入框 - Claude 风格 */}
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
                placeholder="输入你的需求，让我来帮你完成..."
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
                发送
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ChatMainArea;
