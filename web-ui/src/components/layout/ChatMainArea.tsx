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
  InboxOutlined,
  FileImageOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { chatApi } from '@api/chat';
import { useTasksStore } from '@store/tasks';
import { resolveChatSessionProcessingKey } from '@/utils/chatSessionKeys';
import { useMessages } from '@/hooks/useMessages';
import ChatMessage from '@components/chat/ChatMessage';
import FileUploadButton from '@components/chat/FileUploadButton';
import UploadedFilesList from '@components/chat/UploadedFilesList';
import { shallow } from 'zustand/shallow';
import type { ChatMessage as ChatMessageType, Memory } from '@/types';
import VirtualList, { ListRef } from 'rc-virtual-list';
import { isLikelyPersistedDuplicateMessage } from '@/utils/chatMessageUtils';

/** FastAPI/axios errors often put the reason in `response.data.detail`; BaseApi may not surface it on `Error.message`. */
function httpErrorDetail(err: unknown): { status?: number; detail: string } {
  const ax = err as { response?: { status?: number; data?: { detail?: unknown; message?: string } }; message?: string };
  const status = ax?.response?.status;
  const raw = ax?.response?.data?.detail;
  if (typeof raw === 'string' && raw.trim()) {
    return { status, detail: raw.trim() };
  }
  if (Array.isArray(raw)) {
    const joined = raw
      .map((x: { msg?: string }) => (typeof x?.msg === 'string' ? x.msg : ''))
      .filter(Boolean)
      .join('; ');
    if (joined) return { status, detail: joined };
  }
  if (err instanceof Error && err.message) {
    return { status, detail: err.message };
  }
  return { status, detail: '请重试' };
}

const { TextArea } = Input;
const { Title, Text } = Typography;

// ---------------------------------------------------------------------------
// Helpers for paste / drag-drop
// ---------------------------------------------------------------------------

const DROP_ALLOWED_EXTENSIONS = [
  '.pdf', '.doc', '.docx', '.txt', '.md', '.rtf', '.csv',
  '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif', '.tiff',
  '.zip', '.tar', '.tar.gz', '.tgz', '.gz',
  '.h5', '.hdf5', '.hdf', '.hd5', '.pdb', '.dcm', '.nii', '.npz', '.npy',
  '.fasta', '.fa', '.fna', '.faa', '.ffn', '.frn',
  '.fastq', '.fq', '.gff', '.gff3', '.gtf',
  '.vcf', '.sam', '.bam', '.bed',
  '.genbank', '.gb', '.gbk', '.embl',
  '.phy', '.phylip', '.nwk', '.newick', '.aln', '.clustal',
];

const DROP_ALLOWED_MIMES = new Set([
  'application/pdf',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'text/plain', 'text/markdown', 'text/csv',
  'application/rtf',
  'application/zip', 'application/x-zip-compressed',
  'application/x-tar', 'application/gzip', 'application/x-gzip',
  'application/octet-stream',
]);

function isFileAllowed(file: File): boolean {
  if (file.type.startsWith('image/')) return true;
  if (DROP_ALLOWED_MIMES.has(file.type)) return true;
  const name = file.name.toLowerCase();
  return DROP_ALLOWED_EXTENSIONS.some((ext) => name.endsWith(ext));
}

function extractPasteImages(clipboardData: DataTransfer): File[] {
  const files: File[] = [];
  for (let i = 0; i < clipboardData.items.length; i++) {
    const item = clipboardData.items[i];
    if (item.kind === 'file' && item.type.startsWith('image/')) {
      const file = item.getAsFile();
      if (file) {
        const ext = file.type.split('/')[1]?.replace('jpeg', 'jpg') || 'png';
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        const named = new File([file], `pasted-image-${timestamp}.${ext}`, { type: file.type });
        files.push(named);
      }
    }
  }
  return files;
}

// ---------------------------------------------------------------------------
// ChatMessageList (unchanged)
// ---------------------------------------------------------------------------

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
    const stickyBottomRef = useRef(true); // Track whether we should stay pinned to bottom
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
            <span style={{ color: 'var(--primary-color)' }}>&#x1F9E0;</span>
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

    // Aggressively scroll to bottom: index-based + native DOM, repeated with delays
    // to handle rc-virtual-list's unreliable initial positioning and async image loading.
    const scrollTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);

    const forceScrollToBottom = useCallback(() => {
      // Clear any pending scroll timers to avoid stacking.
      scrollTimersRef.current.forEach(clearTimeout);
      scrollTimersRef.current = [];

      const doScroll = () => {
        if (!listRef.current || !stickyBottomRef.current) return;
        // Index-based: tells VirtualList to render items near the end.
        listRef.current.scrollTo({ index: listData.length - 1, align: 'bottom' });
        // Native DOM: scrolls to true bottom regardless of estimation.
        const el = listRef.current.nativeElement;
        if (el) el.scrollTop = el.scrollHeight;
      };

      doScroll();
      // Retry at increasing delays to catch VirtualList measurement + image loading.
      for (const delay of [50, 150, 400, 1000]) {
        scrollTimersRef.current.push(setTimeout(doScroll, delay));
      }
    }, [listData.length]);

    // Clean up timers on unmount.
    useEffect(() => () => scrollTimersRef.current.forEach(clearTimeout), []);

    // Trigger scroll-to-bottom when messages change or history finishes loading.
    useEffect(() => {
      if (!listRef.current || listData.length === 0) return;
      if (pendingAdjustRef.current || isHistoryLoading) return;
      stickyBottomRef.current = true;
      forceScrollToBottom();
    }, [listData, isProcessing, isHistoryLoading, forceScrollToBottom]);

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
        const target = event.currentTarget;
        // Track whether user is near the bottom (within 150px).
        const distanceToBottom = target.scrollHeight - target.scrollTop - target.clientHeight;
        stickyBottomRef.current = distanceToBottom < 150;

        if (!onReachTop || !canLoadMore || isHistoryLoading || loadMoreLockRef.current) {
          return;
        }
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
        itemHeight={400}
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

// ---------------------------------------------------------------------------
// ChatMainArea
// ---------------------------------------------------------------------------

const ChatMainArea: React.FC = () => {
  const { message } = AntdApp.useApp();
  const inputRef = useRef<any>(null);
  const messageContainerRef = useRef<HTMLDivElement>(null);
  const dragCounterRef = useRef(0);
  const [messageAreaHeight, setMessageAreaHeight] = useState(0);
  const [isDragOver, setIsDragOver] = useState(false);
  const [pasteUploading, setPasteUploading] = useState(false);

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
    uploadFile,
  } = useChatStore(
    (state) => ({
      messages: state.messages,
      isProcessing: state.processingSessionIds.has(
        resolveChatSessionProcessingKey(state.currentSession)
      ),
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
      uploadFile: state.uploadFile,
    }),
    shallow
  );

  const { selectedTask, currentPlan } = useTasksStore();
  const [inputText, setInputText] = useState('');
  const [deepThinkEnabled, setDeepThinkEnabled] = useState(false);
  const [steerSending, setSteerSending] = useState(false);

  const activeRunId = useChatStore((s) => {
    const key = resolveChatSessionProcessingKey(currentSession);
    return s.activeRunIds.get(key) ?? null;
  });

  const canQuerySessionHistory = Boolean(
    currentSession &&
    !(currentSession.titleSource === 'local' && currentSession.messages.length === 0)
  );

  const {
    data: historyData,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading: isHistoryLoadingData,
  } = useMessages(
    canQuerySessionHistory
      ? (currentSession?.session_id ?? currentSession?.id)
      : null
  );

  const allHistoryMessages = useMemo(() => {
    if (!historyData) return [];
    return [...historyData.pages].reverse().flatMap((page) => page.messages);
  }, [historyData]);

  const combinedMessages = useMemo(() => {
    const historyIds = new Set(allHistoryMessages.map((m) => m.id));
    const activeOnly = messages.filter((message) => {
      if (historyIds.has(message.id)) {
        return false;
      }
      return !allHistoryMessages.some((historyMessage) =>
        isLikelyPersistedDuplicateMessage(message, historyMessage)
      );
    });
    return [...allHistoryMessages, ...activeOnly];
  }, [allHistoryMessages, messages]);

  // ---- Prevent browser from opening dropped files globally ----
  useEffect(() => {
    const prevent = (e: DragEvent) => {
      e.preventDefault();
    };
    window.addEventListener('dragover', prevent);
    window.addEventListener('drop', prevent);
    return () => {
      window.removeEventListener('dragover', prevent);
      window.removeEventListener('drop', prevent);
    };
  }, []);

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

  useEffect(() => {
    setInputText('');
  }, [currentSession?.id]);

  // Initialize session
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
        startNewSession('AI Task Orchestration Assistant');
      } catch (err) {
        console.warn('[ChatMainArea] Session initialization failed; creating a new session:', err);
        startNewSession('AI Task Orchestration Assistant');
      }
    })();
  }, [currentSession, loadSessions, loadChatHistory, startNewSession]);

  // ---- Upload helper ----
  const doUploadFile = useCallback(async (file: File) => {
    if (!currentSession) {
      message.error('请先创建或选择一个会话');
      return;
    }
    try {
      await uploadFile(file);
      message.success(`${file.name} 上传成功`);
    } catch (error: any) {
      message.error(`上传失败: ${error.message || '未知错误'}`);
    }
  }, [currentSession, uploadFile, message]);

  // ---- Paste handler (images) ----
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    if (!e.clipboardData) return;
    const images = extractPasteImages(e.clipboardData);
    if (images.length === 0) return;

    e.preventDefault();
    setPasteUploading(true);
    Promise.all(images.map((img) => doUploadFile(img)))
      .finally(() => setPasteUploading(false));
  }, [doUploadFile]);

  // ---- Drag & Drop handlers ----
  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current += 1;
    if (e.dataTransfer?.types?.includes('Files')) {
      setIsDragOver(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current -= 1;
    if (dragCounterRef.current <= 0) {
      dragCounterRef.current = 0;
      setIsDragOver(false);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setIsDragOver(false);

    const files = Array.from(e.dataTransfer?.files || []);
    if (files.length === 0) return;

    const allowed: File[] = [];
    const rejected: string[] = [];

    for (const file of files) {
      if (isFileAllowed(file)) {
        allowed.push(file);
      } else {
        rejected.push(file.name);
      }
    }

    if (rejected.length > 0) {
      message.warning(`不支持的文件类型: ${rejected.join(', ')}`);
    }

    if (allowed.length > 0) {
      Promise.all(allowed.map((f) => doUploadFile(f)));
    }
  }, [doUploadFile, message]);

  // ---- Message handlers ----

  const handleSendMessage = async () => {
    const draft = inputText.trim();
    if (!draft) return;

    if (isProcessing && activeRunId) {
      setInputText('');
      setSteerSending(true);
      try {
        await chatApi.steerRun(activeRunId, draft, currentSession?.session_id ?? currentSession?.id);
        message.success('引导已发送，将在下一步采纳');
      } catch (err) {
        console.error('[ChatMainArea] Failed to send steer:', err);
        setInputText(draft);
        const { status, detail } = httpErrorDetail(err);
        if (status === 409) {
          const k = resolveChatSessionProcessingKey(currentSession);
          useChatStore.getState().setActiveRunId(k, null);
          useChatStore.getState().setSessionProcessing(k, false);
          message.error(
            `发送引导失败：本轮 Run 已结束或不再接受中途引导（${detail}）。界面已退出「进行中」；若需继续请重新发送消息。`
          );
        } else {
          message.error(`发送引导失败：${detail}`);
        }
      } finally {
        setSteerSending(false);
      }
      inputRef.current?.focus();
      return;
    }

    if (isProcessing) return;

    const metadata = {
      task_id: selectedTask?.id ?? undefined,
      plan_title: currentPlan || currentPlanTitle || undefined,
      task_name: selectedTask?.name ?? currentTaskName ?? undefined,
    };

    let messageToSend = draft;
    if (deepThinkEnabled && !messageToSend.startsWith('/think')) {
      messageToSend = `/think ${messageToSend}`;
    }

    setInputText('');
    inputRef.current?.focus();
    try {
      await sendMessage(messageToSend, metadata);
    } catch (err) {
      console.error('[ChatMainArea] Failed to send message:', err);
      setInputText(draft);
      message.error('发送失败，请重试');
    }
  };

  const handleProviderChange = async (value: string | undefined) => {
    if (!currentSession) return;
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
    if (!currentSession) return;
    try {
      await setDefaultBaseModel(
        (value as 'qwen3.6-plus' | 'qwen3.5-plus' | 'qwen3-max-2026-01-23' | 'qwen-turbo') ?? null
      );
    } catch (err) {
      console.error('[ChatMainArea] Failed to switch base model:', err);
      message.error('Failed to switch base model. Please try again later.');
    }
  };

  const handleLLMProviderChange = async (value: string | undefined) => {
    if (!currentSession) return;
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
    { label: 'Qwen3.6-Plus', value: 'qwen3.6-plus' },
    { label: 'Qwen3.5-Plus', value: 'qwen3.5-plus' },
    { label: 'Qwen3-Max (2026-01-23)', value: 'qwen3-max-2026-01-23' },
    { label: 'Qwen-Turbo', value: 'qwen-turbo' },
  ];

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
        You can describe your needs in natural language and I will understand and help execute.
      </Text>
    </div>
  );

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--bg-primary)',
        position: 'relative',
      }}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* Drag overlay */}
      {isDragOver && (
        <div className="chat-drop-overlay">
          <div className="chat-drop-overlay-content">
            <InboxOutlined style={{ fontSize: 48, color: 'var(--primary-color)' }} />
            <div className="chat-drop-overlay-title">
              松开以上传文件
            </div>
            <div className="chat-drop-overlay-hint">
              支持图片、PDF、文档等文件
            </div>
          </div>
        </div>
      )}

      {/* Header */}
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

        {/* Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
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

      {/* Input area */}
      <div style={{
        padding: '16px 24px 20px',
        background: 'var(--bg-primary)',
        borderTop: '1px solid var(--border-color)',
        flexShrink: 0,
      }}>
        <div style={{ maxWidth: 920, margin: '0 auto' }}>
          {/* Uploaded files list */}
          <UploadedFilesList />

          {/* Paste uploading indicator */}
          {pasteUploading && (
            <div style={{ padding: '4px 0', fontSize: 12, color: 'var(--primary-color)' }}>
              <FileImageOutlined style={{ marginRight: 4 }} />
              正在上传粘贴的图片...
            </div>
          )}

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

            {/* Input box */}
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
                onPaste={handlePaste}
                placeholder={isProcessing && activeRunId
                  ? "输入引导内容，Agent 将在下一步参考..."
                  : "输入消息... (可粘贴图片 / 拖放文件上传)"}
                autoSize={{ minRows: 1, maxRows: 5 }}
                disabled={false}
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
                disabled={!inputText.trim() || steerSending || (isProcessing && !activeRunId)}
                loading={isProcessing && !activeRunId ? true : steerSending}
                style={{
                  height: 36,
                  borderRadius: 'var(--radius-md)',
                  minWidth: 80,
                  background: isProcessing && activeRunId
                    ? 'var(--accent-color, #e8854a)'
                    : 'var(--primary-color)',
                  border: 'none',
                }}
              >
                {isProcessing && activeRunId ? '发送引导' : 'Send'}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ChatMainArea;
