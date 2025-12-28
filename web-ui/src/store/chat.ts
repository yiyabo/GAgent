import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import {
  ActionStatusResponse,
  ChatActionStatus,
  ChatActionSummary,
  ChatMessage,
  ChatResponseMetadata,
  ChatResponsePayload,
  ChatSession,
  ChatSessionSummary,
  ChatSessionAutoTitleResult,
  Memory,
  PlanSyncEventDetail,
  ToolResultPayload,
  UploadedFile,
  BaseModelOption,
  WebSearchProvider,
  LLMProviderOption,
} from '@/types';
import { SessionStorage } from '@/utils/sessionStorage';
import { useTasksStore } from '@store/tasks';
import { memoryApi } from '@api/memory';
import { chatApi } from '@api/chat';
import { uploadApi } from '@api/upload';
import { ENV } from '@/config/env';
import {
  collectToolResultsFromActions,
  collectToolResultsFromMetadata,
  collectToolResultsFromSteps,
  mergeToolResults,
} from '@utils/toolResults';
import {
  coercePlanId,
  coercePlanTitle,
  derivePlanSyncEventsFromActions,
  dispatchPlanSyncEvent,
  extractPlanIdFromActions,
  extractPlanTitleFromActions,
} from '@utils/planSyncEvents';

const isActionStatus = (value: any): value is ChatActionStatus => {
  return value === 'pending' || value === 'running' || value === 'completed' || value === 'failed';
};

const parseDate = (value?: string | null): Date | null => {
  if (!value) {
    return null;
  }
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return null;
  }
  return new Date(timestamp);
};

const normalizeActionStatus = (status?: string | null): ChatActionStatus | null => {
  if (!status) {
    return null;
  }
  if (status === 'succeeded') {
    return 'completed';
  }
  return isActionStatus(status) ? status : null;
};

const parseJobStreamPayload = (raw: MessageEvent<any>): Record<string, any> | null => {
  try {
    const payload = JSON.parse(raw.data);
    if (!payload || typeof payload !== 'object') {
      return null;
    }
    return payload as Record<string, any>;
  } catch (error) {
    console.warn('无法解析动作 SSE 消息:', error);
    return null;
  }
};

type ChatStreamEvent =
  | { type: 'start' }
  | { type: 'delta'; content: string }
  | { type: 'final'; payload: ChatResponsePayload }
  | { type: 'job_update'; payload: Record<string, any> }
  | { type: 'error'; message?: string; error_type?: string };

const parseChatStreamEvent = (raw: string): ChatStreamEvent | null => {
  const lines = raw.split('\n');
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (dataLines.length === 0) {
    return null;
  }
  const payload = dataLines.join('\n');
  try {
    return JSON.parse(payload) as ChatStreamEvent;
  } catch (error) {
    console.warn('无法解析 SSE payload:', error);
    return { type: 'error', message: 'SSE payload parse failed' };
  }
};

const streamChatEvents = async function* (
  request: Record<string, any>
): AsyncGenerator<ChatStreamEvent> {
  const response = await fetch(`${ENV.API_BASE_URL}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  if (!response.body) {
    throw new Error('Empty stream body');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf('\n\n');
    while (boundary !== -1) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = parseChatStreamEvent(rawEvent);
      if (event) {
        yield event;
      }
      boundary = buffer.indexOf('\n\n');
    }
  }

  if (buffer.trim().length > 0) {
    const event = parseChatStreamEvent(buffer);
    if (event) {
      yield event;
    }
  }
};

const mapJobStatusToChatStatus = (value?: string | null): ChatActionStatus | null => {
  if (!value) return null;
  if (value === 'completed') return 'completed';
  if (value === 'queued') return 'pending';
  if (value === 'running') return 'running';
  if (value === 'succeeded') return 'completed';
  if (value === 'failed') return 'failed';
  return null;
};

const buildActionsFromSteps = (steps: Array<Record<string, any>>): ChatActionSummary[] => {
  if (!Array.isArray(steps)) {
    return [];
  }
  return steps.map((step) => {
    const action = step?.action ?? {};
    const success = step?.success;
    const status =
      success === true ? 'completed' : success === false ? 'failed' : null;
    return {
      kind: action?.kind ?? null,
      name: action?.name ?? null,
      parameters: action?.parameters ?? null,
      order: action?.order ?? null,
      blocking: action?.blocking ?? null,
      status,
      success: success ?? null,
      message: step?.message ?? null,
      details: step?.details ?? null,
    };
  });
};

const formatToolPlanPreface = (actions: ChatActionSummary[]): string => {
  const toolActions = (actions ?? []).filter((a) => a?.kind === 'tool_operation');
  if (!toolActions.length) {
    return '我将先调用工具获取信息，然后给出基于结果的回答。';
  }
  const names = toolActions
    .map((a) => (typeof a?.name === 'string' ? a.name : null))
    .filter(Boolean) as string[];
  const uniqueNames = Array.from(new Set(names));
  const label = uniqueNames.slice(0, 3).join(', ');
  const suffix = uniqueNames.length > 3 ? ` 等 ${uniqueNames.length} 个工具` : '';
  return `我将先调用工具（${label}${suffix}）获取最新信息，然后给出基于结果的回答。`;
};

const summarizeSteps = (steps: Array<Record<string, any>>): string | null => {
  if (!Array.isArray(steps) || steps.length === 0) return null;
  const lines: string[] = [];
  steps.forEach((step, idx) => {
    const order = typeof step?.action?.order === 'number' ? step.action.order : idx + 1;
    const action = step?.action ?? {};
    const labelParts: string[] = [];
    if (typeof action?.kind === 'string') labelParts.push(action.kind);
    if (typeof action?.name === 'string') labelParts.push(action.name);
    const header = labelParts.length ? labelParts.join('/') : `步骤 ${order}`;

    const detail =
      (typeof step?.summary === 'string' && step.summary) ||
      (typeof (step as any)?.message === 'string' && (step as any).message) ||
      (step?.details && typeof step.details?.summary === 'string' ? step.details.summary : null);

    const subtasks =
      (step?.details && Array.isArray((step.details as any).subtasks)
        ? ((step.details as any).subtasks as any[])
        : []) ||
      (step?.details &&
        (step.details as any).result &&
        Array.isArray((step.details as any).result?.subtasks)
        ? ((step.details as any).result.subtasks as any[])
        : []);

    if (detail) {
      lines.push(`- ${header}: ${detail}`);
    } else {
      lines.push(`- ${header}`);
    }
    if (subtasks && subtasks.length > 0) {
      subtasks.forEach((st: any) => {
        const name =
          (st && typeof st.name === 'string' && st.name) ||
          (st && typeof st.title === 'string' && st.title) ||
          null;
        if (name) {
          lines.push(`  - 子任务: ${name}`);
        }
      });
    }
  });
  return lines.length ? lines.join('\n') : null;
};

const convertRawActionToSummary = (act: any): ChatActionSummary => {
  return {
    kind: typeof act?.kind === 'string' ? act.kind : null,
    name: typeof act?.name === 'string' ? act.name : null,
    parameters: act?.parameters ?? null,
    order: typeof act?.order === 'number' ? act.order : null,
    blocking: typeof act?.blocking === 'boolean' ? act.blocking : null,
    status: null,
    success: null,
    message: typeof act?.message === 'string' ? act.message : null,
    details: act?.details ?? null,
  };
};

const summarizeActions = (actions: ChatActionSummary[] | null | undefined): string | null => {
  if (!actions || actions.length === 0) return null;
  const lines: string[] = [];
  actions.forEach((act, idx) => {
    const order = typeof act.order === 'number' ? act.order : idx + 1;
    const labelParts: string[] = [];
    if (typeof act.kind === 'string') labelParts.push(act.kind);
    if (typeof act.name === 'string') labelParts.push(act.name);
    const header = labelParts.length ? labelParts.join('/') : `步骤 ${order}`;

    const params = (act.parameters ?? {}) as Record<string, any>;
    const hasMsg = typeof act.message === 'string' && act.message.trim().length > 0;
    const nameDetail = typeof params?.name === 'string' ? params.name : null;
    const instructionDetail =
      typeof params?.instruction === 'string' ? params.instruction : null;
    const detail = hasMsg
      ? act.message
      : instructionDetail
        ? nameDetail
          ? `${nameDetail}: ${instructionDetail}`
          : instructionDetail
        : nameDetail;

    if (detail) {
      lines.push(`- ${header}: ${detail}`);
    } else {
      lines.push(`- ${header}`);
    }
  });
  return lines.length ? lines.join('\n') : null;
};

const summaryToChatSession = (summary: ChatSessionSummary): ChatSession => {
  const rawName = summary.name?.trim();
  const title =
    rawName ||
    (summary.plan_title && summary.plan_title.trim()) ||
    `会话 ${summary.id.slice(0, 8)}`;
  const titleSource =
    summary.name_source ??
    (rawName ? (summary.is_user_named ? 'user' : null) : null);
  const isUserNamed =
    summary.is_user_named === undefined || summary.is_user_named === null
      ? null
      : Boolean(summary.is_user_named);
  const createdAt = parseDate(summary.created_at) ?? new Date();
  const updatedAt = parseDate(summary.updated_at) ?? createdAt;
  const lastMessageAt = parseDate(summary.last_message_at);

  return {
    id: summary.id,
    title,
    messages: [],
    created_at: createdAt,
    updated_at: updatedAt,
    workflow_id: null,
    session_id: summary.id,
    plan_id: summary.plan_id ?? null,
    plan_title: summary.plan_title ?? null,
    current_task_id: summary.current_task_id ?? null,
    current_task_name: summary.current_task_name ?? null,
    last_message_at: lastMessageAt,
    is_active: summary.is_active,
    defaultSearchProvider: summary.settings?.default_search_provider ?? null,
    defaultBaseModel: summary.settings?.default_base_model ?? null,
    defaultLLMProvider: summary.settings?.default_llm_provider ?? null,
    titleSource,
    isUserNamed,
  };
};

const derivePlanContextFromMessages = (
  messages: ChatMessage[]
): { planId: number | null | undefined; planTitle: string | null | undefined } => {
  let planId: number | null | undefined = undefined;
  let planTitle: string | null | undefined = undefined;

  for (let idx = messages.length - 1; idx >= 0; idx -= 1) {
    const metadata = messages[idx]?.metadata;
    if (!metadata) {
      continue;
    }

    if (planId === undefined && Object.prototype.hasOwnProperty.call(metadata, 'plan_id')) {
      const candidate = coercePlanId((metadata as any).plan_id);
      if (candidate !== undefined) {
        planId = candidate ?? null;
      }
    }

    if (planTitle === undefined && Object.prototype.hasOwnProperty.call(metadata, 'plan_title')) {
      const candidate = coercePlanTitle((metadata as any).plan_title);
      if (candidate !== undefined) {
        planTitle = candidate ?? null;
      }
    }

    if (planId !== undefined && planTitle !== undefined) {
      break;
    }
  }

  return { planId, planTitle };
};

const buildToolResultsCache = (
  messages: ChatMessage[]
): Map<string, ToolResultPayload[]> => {
  const cache = new Map<string, ToolResultPayload[]>();
  for (const msg of messages) {
    if (msg.type !== 'assistant') {
      continue;
    }
    const metadata = msg.metadata as Record<string, any> | undefined;
    if (!metadata) {
      continue;
    }
    const trackingId =
      typeof metadata.tracking_id === 'string' ? metadata.tracking_id : null;
    if (!trackingId) {
      continue;
    }
    const toolResults = collectToolResultsFromMetadata(metadata.tool_results);
    if (toolResults.length > 0) {
      cache.set(trackingId, toolResults);
    }
  }
  return cache;
};

const waitForActionCompletionViaStream = async (
  trackingId: string,
  timeoutMs: number = 120_000
): Promise<ActionStatusResponse | null> => {
  if (typeof EventSource === 'undefined') {
    return null;
  }

  return new Promise((resolve) => {
    let finished = false;
    // 统一使用通用 jobs SSE（GET），避免 chat/stream(POST) 中断后无法继续拿到终态
    const streamUrl = `${ENV.API_BASE_URL}/jobs/${trackingId}/stream`;
    const source = new EventSource(streamUrl);

    const finalize = async () => {
      if (finished) return;
      finished = true;
      source.close();
      try {
        const status = await chatApi.getActionStatus(trackingId);
        resolve(status);
      } catch (error) {
        console.warn('动作 SSE 获取最终状态失败:', error);
        resolve(null);
      }
    };

    const timeoutId = window.setTimeout(() => {
      if (finished) return;
      finished = true;
      source.close();
      resolve(null);
    }, timeoutMs);

    source.onmessage = (event) => {
      const payload = parseJobStreamPayload(event);
      if (!payload) return;
      const status = normalizeActionStatus(payload.status ?? payload.job?.status);
      if (status && (status === 'completed' || status === 'failed')) {
        window.clearTimeout(timeoutId);
        void finalize();
      }
    };

    source.onerror = () => {
      if (finished) return;
      window.clearTimeout(timeoutId);
      finished = true;
      source.close();
      resolve(null);
    };
  });
};

const pendingAutotitleSessions = new Set<string>();
const autoTitleHistory = new Map<string, { planId: number | null }>();
const activeActionFollowups = new Set<string>();

const resolveHistoryCursor = (messages: ChatMessage[]): number | null => {
  let minId: number | null = null;
  for (const msg of messages) {
    const backendId = msg.metadata?.backend_id;
    if (typeof backendId === 'number' && Number.isFinite(backendId)) {
      minId = minId === null ? backendId : Math.min(minId, backendId);
      continue;
    }
    const match = msg.id.match(/_(\d+)$/);
    if (match) {
      const parsed = Number(match[1]);
      if (!Number.isNaN(parsed)) {
        minId = minId === null ? parsed : Math.min(minId, parsed);
      }
    }
  }
  return minId;
};

interface ChatState {
  // 聊天数据
  currentSession: ChatSession | null;
  sessions: ChatSession[];
  messages: ChatMessage[];
  currentWorkflowId: string | null;
  historyHasMore: boolean;
  historyBeforeId: number | null;
  historyLoading: boolean;
  historyPageSize: number;

  // 当前上下文
  currentPlanId: number | null;
  currentPlanTitle: string | null;
  currentTaskId: number | null;
  currentTaskName: string | null;
  defaultSearchProvider: WebSearchProvider | null;
  defaultBaseModel: BaseModelOption | null;
  defaultLLMProvider: LLMProviderOption | null;
  
  // 输入状态
  inputText: string;
  isTyping: boolean;
  isProcessing: boolean;
  isUpdatingProvider: boolean;
  isUpdatingBaseModel: boolean;
  isUpdatingLLMProvider: boolean;
  
  // UI状态
  chatPanelVisible: boolean;
  chatPanelWidth: number;

  // Memory 相关状态
  memoryEnabled: boolean;
  relevantMemories: Memory[];

  // 文件上传相关状态
  uploadedFiles: UploadedFile[];
  uploadingFiles: Array<{ file: File; progress: number }>;

  // 操作方法
  setCurrentSession: (session: ChatSession | null) => void;
  addSession: (session: ChatSession) => void;
  removeSession: (sessionId: string) => void;
  deleteSession: (sessionId: string, options?: { archive?: boolean }) => Promise<void>;
  addMessage: (message: ChatMessage) => void;
  updateMessage: (messageId: string, updates: Partial<ChatMessage>) => void;
  removeMessage: (messageId: string) => void;
  clearMessages: () => void;
  
  // 输入操作
  setInputText: (text: string) => void;
  setIsTyping: (typing: boolean) => void;
  setIsProcessing: (processing: boolean) => void;
  
  // UI操作
  toggleChatPanel: () => void;
  setChatPanelVisible: (visible: boolean) => void;
  setChatPanelWidth: (width: number) => void;

  // 上下文操作
  setChatContext: (context: { planId?: number | null; planTitle?: string | null; taskId?: number | null; taskName?: string | null }) => void;
  clearChatContext: () => void;
  setCurrentWorkflowId: (workflowId: string | null) => void;

  // Memory 操作
  toggleMemory: () => void;
  setMemoryEnabled: (enabled: boolean) => void;
  setRelevantMemories: (memories: Memory[]) => void;
  saveMessageAsMemory: (message: ChatMessage, memoryType?: string, importance?: string) => Promise<void>;

  loadSessions: () => Promise<void>;
  autotitleSession: (
    sessionId: string,
    options?: { force?: boolean; strategy?: string | null }
  ) => Promise<ChatSessionAutoTitleResult | null>;
  
  // 快捷操作
  sendMessage: (content: string, metadata?: ChatMessage['metadata']) => Promise<void>;
  retryLastMessage: () => Promise<void>;
  retryActionRun: (trackingId: string, rawActions?: any[]) => Promise<void>;
  startNewSession: (title?: string) => ChatSession;
  restoreSession: (sessionId: string, title?: string) => Promise<ChatSession>;
  loadChatHistory: (
    sessionId: string,
    options?: { beforeId?: number | null; append?: boolean; pageSize?: number }
  ) => Promise<void>;
  setDefaultSearchProvider: (provider: WebSearchProvider | null) => Promise<void>;
  setDefaultBaseModel: (model: BaseModelOption | null) => Promise<void>;
  setDefaultLLMProvider: (provider: LLMProviderOption | null) => Promise<void>;
  
  // 文件上传操作
  uploadFile: (file: File) => Promise<UploadedFile>;
  removeUploadedFile: (fileId: string) => Promise<void>;
  clearUploadedFiles: () => void;
}

export const useChatStore = create<ChatState>()(
  subscribeWithSelector((set, get) => ({
    // 初始状态
    currentSession: null,
    sessions: [],
    messages: [],
    currentWorkflowId: null,
    historyHasMore: false,
    historyBeforeId: null,
    historyLoading: false,
    historyPageSize: 100,
    currentPlanId: null,
    currentPlanTitle: null,
    currentTaskId: null,
    currentTaskName: null,
    defaultSearchProvider: null,
    defaultBaseModel: null,
    defaultLLMProvider: null,
    inputText: '',
    isTyping: false,
    isProcessing: false,
    isUpdatingProvider: false,
    isUpdatingBaseModel: false,
    isUpdatingLLMProvider: false,
    chatPanelVisible: true,
    chatPanelWidth: 400,
    memoryEnabled: true, // 默认启用记忆功能
    relevantMemories: [],
    uploadedFiles: [],
    uploadingFiles: [],

    // 设置当前会话
    setCurrentSession: (session) => {
      const sessionPlanId = session?.plan_id ?? null;
      const sessionPlanTitle = session?.plan_title ?? null;
      const sessionTaskId = session?.current_task_id ?? null;
      const sessionTaskName = session?.current_task_name ?? null;
      const provider = session?.defaultSearchProvider ?? null;
      const baseModel = session?.defaultBaseModel ?? null;
      const llmProvider = session?.defaultLLMProvider ?? null;
      const historyCursor = session ? resolveHistoryCursor(session.messages) : null;
      const historyHasMore = historyCursor !== null;

      set({
        currentSession: session,
        currentWorkflowId: session?.workflow_id ?? null,
        messages: session ? session.messages : [],
        currentPlanId: sessionPlanId,
        currentPlanTitle: sessionPlanTitle,
        currentTaskId: sessionTaskId,
        currentTaskName: sessionTaskName,
        defaultSearchProvider: provider,
        defaultBaseModel: baseModel,
        defaultLLMProvider: llmProvider,
        historyBeforeId: historyCursor,
        historyHasMore,
        historyLoading: false,
      });
      
      if (session) {
        SessionStorage.setCurrentSessionId(session.id);
      } else {
        SessionStorage.clearCurrentSessionId();
      }
    },

    // 添加会话
    addSession: (session) => {
      const normalized: ChatSession = {
        ...session,
        defaultSearchProvider: session.defaultSearchProvider ?? null,
        defaultBaseModel: session.defaultBaseModel ?? null,
        defaultLLMProvider: session.defaultLLMProvider ?? null,
      };
      set((state) => {
        const exists = state.sessions.some((s) => s.id === normalized.id);
        const newSessions = exists
          ? state.sessions.map((s) => (s.id === normalized.id ? normalized : s))
          : [...state.sessions, normalized];
        SessionStorage.setAllSessionIds(newSessions.map((s) => s.id));
        return { sessions: newSessions };
      });
    },

    // 删除会话
    removeSession: (sessionId) => {
      autoTitleHistory.delete(sessionId);
      pendingAutotitleSessions.delete(sessionId);
      set((state) => {
        const newSessions = state.sessions.filter(s => s.id !== sessionId);
        // 更新 localStorage
        const allSessionIds = newSessions.map(s => s.id);
        SessionStorage.setAllSessionIds(allSessionIds);
        // 如果删除的是当前会话，清除current_session_id
        if (state.currentSession?.id === sessionId) {
          SessionStorage.clearCurrentSessionId();
        }
        return {
          sessions: newSessions,
          currentSession: state.currentSession?.id === sessionId ? null : state.currentSession,
          messages: state.currentSession?.id === sessionId ? [] : state.messages,
          defaultSearchProvider:
            state.currentSession?.id === sessionId ? null : state.defaultSearchProvider,
          defaultBaseModel:
            state.currentSession?.id === sessionId ? null : state.defaultBaseModel,
          defaultLLMProvider:
            state.currentSession?.id === sessionId ? null : state.defaultLLMProvider,
        };
      });
    },

    deleteSession: async (sessionId, options) => {
      const archive = options?.archive ?? false;
      
      try {
        // 先检查会话是否在后端存在
        const checkResponse = await fetch(`${ENV.API_BASE_URL}/chat/sessions/${sessionId}`, {
          method: 'HEAD',
        });
        
        // 如果会话在后端不存在，直接从本地移除，不报错
        if (!checkResponse.ok) {
          console.warn(`会话 ${sessionId} 在后端不存在，从本地移除`);
          get().removeSession(sessionId);
          return;
        }
        
        // 会话存在，调用后端删除
        await chatApi.deleteSession(
          sessionId,
          archive ? { archive: true } : undefined
        );
      } catch (error) {
        // 网络错误或其他错误，如果是因为会话不存在，直接移除本地
        if (error instanceof Error && error.message.includes('404')) {
          console.warn(`会话 ${sessionId} 不存在，从本地移除`);
          get().removeSession(sessionId);
          return;
        }
        console.error('删除会话失败:', error);
        throw error;
      }

      if (archive) {
        set((state) => {
          const updatedSessions = state.sessions.map((session) =>
            session.id === sessionId ? { ...session, is_active: false } : session
          );
          const updatedCurrent =
            state.currentSession?.id === sessionId
              ? { ...state.currentSession, is_active: false }
              : state.currentSession;
          return {
            sessions: updatedSessions,
            currentSession: updatedCurrent,
          };
        });
        dispatchPlanSyncEvent(
          {
            type: 'session_archived',
            session_id: sessionId,
            plan_id: null,
          },
          { source: 'chat.session' }
        );
        return;
      }

      const wasCurrent = get().currentSession?.id === sessionId;
      get().removeSession(sessionId);

      if (wasCurrent) {
        const tasksStore = useTasksStore.getState();
        tasksStore.setTasks([]);
        tasksStore.clearTaskResultCache();
        tasksStore.closeTaskDrawer();

        const remainingSessions = get().sessions;
        const fallbackSession =
          remainingSessions.find((session) => session.is_active) ??
          remainingSessions[0] ??
          null;

        if (fallbackSession) {
          get().setCurrentSession(fallbackSession);
          try {
            await get().loadChatHistory(fallbackSession.id);
          } catch (historyError) {
            console.warn('加载备用会话历史失败:', historyError);
          }
        } else {
          set({
            currentPlanId: null,
            currentPlanTitle: null,
            currentTaskId: null,
            currentTaskName: null,
            currentWorkflowId: null,
            messages: [],
          });
        }
      }

      dispatchPlanSyncEvent(
        {
          type: 'session_deleted',
          session_id: sessionId,
          plan_id: null,
        },
        { source: 'chat.session' }
      );
    },

    // 添加消息
    addMessage: (message) => set((state) => {
      const newMessages = [...state.messages, message];
      
      // 更新当前会话
      let updatedSession = state.currentSession;
      if (updatedSession) {
        updatedSession = {
          ...updatedSession,
          messages: newMessages,
          updated_at: new Date(),
        };
      }

      // 更新会话列表
      const updatedSessions = state.sessions.map(session =>
        session.id === updatedSession?.id ? updatedSession : session
      );

      return {
        messages: newMessages,
        currentSession: updatedSession,
        sessions: updatedSessions,
      };
    }),

    // 更新消息
    updateMessage: (messageId, updates) => set((state) => {
      const updatedMessages = state.messages.map(msg =>
        msg.id === messageId ? { ...msg, ...updates } : msg
      );

      // 更新当前会话
      let updatedSession = state.currentSession;
      if (updatedSession) {
        updatedSession = {
          ...updatedSession,
          messages: updatedMessages,
          updated_at: new Date(),
        };
      }

      return {
        messages: updatedMessages,
        currentSession: updatedSession,
      };
    }),

    // 删除消息
    removeMessage: (messageId) => set((state) => ({
      messages: state.messages.filter(msg => msg.id !== messageId),
    })),

    // 清空消息
    clearMessages: () =>
      set({
        messages: [],
        historyBeforeId: null,
        historyHasMore: false,
        historyLoading: false,
      }),

    // 设置聊天上下文
    setChatContext: ({ planId, planTitle, taskId, taskName }) => {
      set((state) => {
        const nextPlanId = planId !== undefined ? planId : state.currentPlanId;
        const nextPlanTitle = planTitle !== undefined ? planTitle : state.currentPlanTitle;
        const nextTaskId = taskId !== undefined ? taskId : state.currentTaskId;
        const nextTaskName = taskName !== undefined ? taskName : state.currentTaskName;

        if (
          state.currentPlanId === nextPlanId &&
          state.currentPlanTitle === nextPlanTitle &&
          state.currentTaskId === nextTaskId &&
          state.currentTaskName === nextTaskName
        ) {
          return state;
        }

        const planIdValue = nextPlanId ?? null;
        const planTitleValue = nextPlanTitle ?? null;

        const updatedSession = state.currentSession
          ? {
              ...state.currentSession,
              plan_id: planIdValue,
              plan_title: planTitleValue,
            }
          : null;

        const updatedSessions = updatedSession
          ? state.sessions.map((session) =>
              session.id === updatedSession.id ? updatedSession : session
            )
          : state.sessions;

        return {
          currentPlanId: planIdValue,
          currentPlanTitle: planTitleValue,
          currentTaskId: nextTaskId ?? null,
          currentTaskName: nextTaskName ?? null,
          currentSession: updatedSession,
          sessions: updatedSessions,
        };
      });
    },

    clearChatContext: () =>
      set((state) => {
        const updatedSession = state.currentSession
          ? { ...state.currentSession, plan_id: null, plan_title: null }
          : null;
        const sessions = updatedSession
          ? state.sessions.map((session) =>
              session.id === updatedSession.id ? updatedSession : session
            )
          : state.sessions;

        return {
          currentPlanId: null,
          currentPlanTitle: null,
          currentTaskId: null,
          currentTaskName: null,
          currentSession: updatedSession,
          sessions,
        };
      }),

    setCurrentWorkflowId: (workflowId) => {
      const state = get();
      if (state.currentWorkflowId === workflowId) {
        return;
      }

      const currentSession = state.currentSession
        ? { ...state.currentSession, workflow_id: workflowId ?? undefined }
        : null;
      const sessions = state.sessions.map((session) =>
        session.id === currentSession?.id
          ? { ...session, workflow_id: workflowId ?? undefined }
          : session
      );

      try {
        const { setCurrentWorkflowId } = useTasksStore.getState();
        setCurrentWorkflowId(workflowId ?? null);
      } catch (err) {
        console.warn('Unable to sync workflow id to tasks store:', err);
      }

      set({
        currentWorkflowId: workflowId ?? null,
        currentSession,
        sessions,
      });
    },

    // 设置输入文本
    setInputText: (text) => set({ inputText: text }),

    // 设置正在输入状态
    setIsTyping: (typing) => set({ isTyping: typing }),

    // 设置处理中状态
    setIsProcessing: (processing) => set({ isProcessing: processing }),

    // 切换聊天面板显示
    toggleChatPanel: () => set((state) => ({
      chatPanelVisible: !state.chatPanelVisible,
    })),

    // 设置聊天面板显示
    setChatPanelVisible: (visible) => set({ chatPanelVisible: visible }),

    // 设置聊天面板宽度
    setChatPanelWidth: (width) => set({ chatPanelWidth: width }),

    // 发送消息
    sendMessage: async (content, metadata) => {
      const {
        currentPlanTitle,
        currentPlanId,
        currentTaskId,
        currentTaskName,
        currentWorkflowId,
        currentSession,
        memoryEnabled,
        defaultSearchProvider,
        defaultBaseModel,
        defaultLLMProvider,
        uploadedFiles,
      } = get();
      // 如果有上传的文件，添加到metadata中
      const attachments = uploadedFiles.length > 0
        ? uploadedFiles.map((f) => {
            const name = f.original_name || f.file_name;
            const isImage = Boolean(
              f.file_type?.startsWith('image/') ||
              /\.(png|jpe?g|gif|webp|bmp|tiff?)$/i.test(name)
            );
            return {
              type: (isImage ? 'image' : 'file') as 'image' | 'file',
              path: f.file_path,
              name,
              ...(f.extracted_path ? { extracted_path: f.extracted_path } : {}),
            };
          })
        : undefined;

      const mergedMetadata = {
        ...metadata,
        plan_id: metadata?.plan_id ?? currentPlanId ?? undefined,
        plan_title: metadata?.plan_title ?? currentPlanTitle ?? undefined,
        task_id: metadata?.task_id ?? currentTaskId ?? undefined,
        task_name: metadata?.task_name ?? currentTaskName ?? undefined,
        workflow_id: metadata?.workflow_id ?? currentWorkflowId ?? undefined,
        attachments,
      };

      const userMessage: ChatMessage = {
        id: `msg_${Date.now()}_user`,
        type: 'user',
        content,
        timestamp: new Date(),
        metadata: mergedMetadata,
      };
      get().addMessage(userMessage);
      set({ isProcessing: true, inputText: '' });

      // 方案A：不拼接记忆到消息内容，改为通过 context 传递
      let memories: Memory[] = [];

      if (memoryEnabled) {
        try {
          const memoryResult = await memoryApi.queryMemory({
            search_text: content,
            limit: 3,
            min_similarity: 0.6,
          });
          memories = memoryResult.memories;
          set({ relevantMemories: memories });
        } catch (error) {
          console.error('Memory RAG 查询失败:', error);
        }
      }

      const assistantMessageId = `msg_${Date.now()}_assistant`;
      let assistantMessageAdded = false;

      try {
        const providerToUse =
          defaultSearchProvider ??
          currentSession?.defaultSearchProvider ??
          null;
        const baseModelToUse =
          defaultBaseModel ??
          currentSession?.defaultBaseModel ??
          null;
        const llmProviderToUse =
          defaultLLMProvider ??
          currentSession?.defaultLLMProvider ??
          null;
        const messages = get().messages;
        const recentMessages = messages.slice(-10).map((msg) => ({
          role: msg.type,
          content: msg.content,
          timestamp: msg.timestamp.toISOString(),
        }));

        // 将记忆作为 context 传递，而不是拼接到消息内容
        const memoryContext = memories.length > 0 
          ? memories.map((m) => ({
              content: m.content,
              similarity: m.similarity,
              memory_type: m.memory_type,
            }))
          : undefined;

        const chatRequest = {
          task_id: mergedMetadata.task_id,
          plan_title: mergedMetadata.plan_title,
          plan_id: mergedMetadata.plan_id,
          workflow_id: mergedMetadata.workflow_id,
          session_id: currentSession?.session_id,
          history: recentMessages,
          mode: 'assistant' as const,
          default_search_provider: providerToUse ?? undefined,
          default_base_model: baseModelToUse ?? undefined,
          default_llm_provider: llmProviderToUse ?? undefined,
          metadata: {
            ...(providerToUse ? { default_search_provider: providerToUse } : {}),
            ...(baseModelToUse ? { default_base_model: baseModelToUse } : {}),
            ...(llmProviderToUse ? { default_llm_provider: llmProviderToUse } : {}),
            ...(attachments ? { attachments } : {}),
            ...(memoryContext ? { memories: memoryContext } : {}),
            ...(metadata ?? {}),
          },
        };

        const assistantMessage: ChatMessage = {
          id: assistantMessageId,
          type: 'assistant',
          content: '',
          timestamp: new Date(),
          metadata: {
            status: 'pending',
            unified_stream: true,
            plan_message: null,
          },
        };
        get().addMessage(assistantMessage);
        assistantMessageAdded = true;

        // 启动后台动作状态轮询/补偿（SSE 丢失时确保最终内容写回）
        const startActionStatusPolling = (
          trackingId: string | null | undefined,
          messageId: string,
          initialStatus?: ChatActionStatus,
          initialContent?: string | null
        ) => {
          if (!trackingId) return;
          const pollOnce = async (): Promise<boolean> => {
            try {
              const resp = await fetch(`${ENV.API_BASE_URL}/chat/actions/${trackingId}`);
              if (!resp.ok) return false;
              const statusResp = (await resp.json()) as ActionStatusResponse;

              const status = statusResp.status;
              const done = status === 'completed' || status === 'failed';
              const remoteToolResults = mergeToolResults(
                collectToolResultsFromMetadata(statusResp.result?.tool_results),
                collectToolResultsFromMetadata(statusResp.metadata?.tool_results)
              );
              const remoteAnalysis =
                typeof statusResp.result?.analysis_text === 'string'
                  ? statusResp.result.analysis_text
                  : typeof statusResp.metadata?.analysis_text === 'string'
                    ? statusResp.metadata.analysis_text
                    : undefined;
              const remoteFinalSummary =
                typeof statusResp.result?.final_summary === 'string'
                  ? statusResp.result.final_summary
                  : typeof statusResp.metadata?.final_summary === 'string'
                    ? statusResp.metadata.final_summary
                    : undefined;
              const remoteReply =
                typeof statusResp.result?.reply === 'string'
                  ? statusResp.result.reply
                  : undefined;

              const currentMessages = get().messages;
              const targetMessage = currentMessages.find((msg) => msg.id === messageId);
              if (!targetMessage) {
                return done;
              }
              const currentMeta: ChatResponseMetadata = {
                ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}),
              };
              const contentCandidate =
                (remoteAnalysis && remoteAnalysis.trim()) ||
                (remoteFinalSummary && remoteFinalSummary.trim()) ||
                (remoteReply && remoteReply.trim()) ||
                targetMessage.content ||
                initialContent ||
                '';

              get().updateMessage(messageId, {
                content: contentCandidate,
                metadata: {
                  ...currentMeta,
                  status,
                  analysis_text: remoteAnalysis ?? currentMeta.analysis_text,
                  final_summary: remoteFinalSummary ?? currentMeta.final_summary,
                  tool_results:
                    remoteToolResults.length > 0
                      ? remoteToolResults
                      : currentMeta.tool_results,
                },
              });

              // 完成后同步一次后端历史，确保展示与落库一致（最可靠，等价于用户手动刷新但无需刷新页面）
              if (done) {
                const sessionKey = get().currentSession?.session_id ?? get().currentSession?.id ?? null;
                if (sessionKey) {
                  void get().loadChatHistory(sessionKey).catch((e) =>
                    console.warn('同步历史失败:', e)
                  );
                }
              }

              return done;
            } catch (pollError) {
              console.warn('补偿轮询动作状态失败:', pollError);
              return false;
            }
          };

          // 如果初始状态已经完成且已有内容，就不轮询
          if (initialStatus === 'completed' || initialStatus === 'failed') {
            void pollOnce();
            return;
          }

          void (async () => {
            const start = Date.now();
            const timeoutMs = 90_000;
            const intervalMs = 2_500;
            while (Date.now() - start < timeoutMs) {
              const done = await pollOnce();
              if (done) break;
              await new Promise((resolve) => setTimeout(resolve, intervalMs));
            }
          })();
        };

        const {
          default_search_provider: _ignoredProvider,
          default_base_model: _ignoredBaseModel,
          default_llm_provider: _ignoredLLMProvider,
          ...restMetadata
        } = chatRequest.metadata ?? {};
        const streamRequest = {
          message: content,
          mode: chatRequest.mode,
          history: chatRequest.history,
          session_id: chatRequest.session_id,
          context: {
            plan_id: chatRequest.plan_id,
            task_id: chatRequest.task_id,
            plan_title: chatRequest.plan_title,
            workflow_id: chatRequest.workflow_id,
            default_search_provider: chatRequest.default_search_provider,
            default_base_model: chatRequest.default_base_model,
            default_llm_provider: chatRequest.default_llm_provider,
            ...restMetadata,
          },
        };

        let streamedContent = '';
        let lastFlushedContent = '';
        let flushHandle: number | null = null;
        let finalPayload: ChatResponsePayload | null = null;
        let jobFinalized = false;

        const flushAnalysisText = (force: boolean = false) => {
          if (!force && streamedContent === lastFlushedContent) {
            return;
          }
          const currentMessages = get().messages;
          const targetMessage = currentMessages.find((msg) => msg.id === assistantMessageId);
          if (!targetMessage) {
            return;
          }
          const existingMetadata: ChatResponseMetadata = {
            ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}),
          };
          get().updateMessage(assistantMessageId, {
            metadata: {
              ...existingMetadata,
              analysis_text: streamedContent,
            },
          });
          lastFlushedContent = streamedContent;
        };

        const scheduleFlush = () => {
          if (flushHandle !== null) {
            return;
          }
          flushHandle = window.requestAnimationFrame(() => {
            flushHandle = null;
            flushAnalysisText();
          });
        };

        for await (const event of streamChatEvents(streamRequest)) {
          if (event.type === 'delta') {
            streamedContent += event.content ?? '';
            // Throttle streaming updates to avoid UI jank.
            scheduleFlush();
            continue;
          }
          if (event.type === 'job_update') {
            const payload = event.payload ?? {};
            const currentMessages = get().messages;
            const targetMessage = currentMessages.find((msg) => msg.id === assistantMessageId);
            if (!targetMessage) {
              continue;
            }

            const existingMetadata: ChatResponseMetadata = {
              ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}),
            };
            const jobStatus = mapJobStatusToChatStatus(payload.status);
            const stepList = Array.isArray(payload.result?.steps)
              ? (payload.result?.steps as Array<Record<string, any>>)
              : [];
            const actionsFromSteps = buildActionsFromSteps(stepList);

            const toolResultsFromExisting = collectToolResultsFromMetadata(
              existingMetadata.tool_results
            );
            const toolResultsFromResult = collectToolResultsFromMetadata(
              payload.result?.tool_results
            );
            const toolResultsFromSteps = collectToolResultsFromSteps(stepList);
            const mergedToolResults = mergeToolResults(
              toolResultsFromExisting,
              mergeToolResults(toolResultsFromResult, toolResultsFromSteps)
            );

            const updatedMetadata: ChatResponseMetadata = {
              ...existingMetadata,
              status: jobStatus ?? existingMetadata.status,
            };
            (updatedMetadata as any).unified_stream = true;
            const existingPlanMessage = (updatedMetadata as any).plan_message;
            if (!existingPlanMessage || existingPlanMessage === '') {
              if (actionsFromSteps.length > 0) {
                const planPreface = formatToolPlanPreface(actionsFromSteps);
                (updatedMetadata as any).plan_message = planPreface;
              } else {
                (updatedMetadata as any).plan_message = targetMessage.content || null;
              }
            }
            if (typeof existingMetadata.analysis_text === 'string' && existingMetadata.analysis_text.length > 0) {
              updatedMetadata.analysis_text = existingMetadata.analysis_text;
            }
            const analysisFromPayload =
              typeof payload.result?.analysis_text === 'string'
                ? (payload.result.analysis_text as string)
                : typeof payload.metadata?.analysis_text === 'string'
                  ? (payload.metadata.analysis_text as string)
                  : null;
            if (analysisFromPayload && analysisFromPayload.trim().length > 0) {
              updatedMetadata.analysis_text = analysisFromPayload;
            }
            if (payload.job_id && !updatedMetadata.tracking_id) {
              updatedMetadata.tracking_id = payload.job_id;
            }
            if (actionsFromSteps.length > 0) {
              updatedMetadata.actions = actionsFromSteps;
              updatedMetadata.action_list = actionsFromSteps;
            }
            if (mergedToolResults.length > 0) {
              updatedMetadata.tool_results = mergedToolResults;
            } else if ('tool_results' in updatedMetadata) {
              delete updatedMetadata.tool_results;
            }
            if (payload.error) {
              updatedMetadata.errors = [...(updatedMetadata.errors ?? []), payload.error];
            }

            get().updateMessage(assistantMessageId, {
              metadata: updatedMetadata,
            });

            if (jobFinalized) {
              continue;
            }
            if (payload.status !== 'succeeded' && payload.status !== 'failed' && payload.status !== 'completed') {
              continue;
            }
            jobFinalized = true;

            const normalizedFinalStatus =
              payload.status === 'completed' ? 'succeeded' : payload.status;
            const finalActions = actionsFromSteps.length > 0
              ? actionsFromSteps
              : (updatedMetadata.actions ?? []);

            const finalPlanIdCandidate =
              coercePlanId(payload.result?.bound_plan_id) ??
              extractPlanIdFromActions(finalActions) ??
              updatedMetadata.plan_id ??
              null;

            let planTitleFromSteps: string | null | undefined;
            for (const step of stepList) {
              const details = step?.details;
              if (!details || typeof details !== 'object') {
                continue;
              }
              const candidate =
                coercePlanTitle((details as any).title) ??
                coercePlanTitle((details as any).plan_title);
              if (candidate !== undefined) {
                planTitleFromSteps = candidate ?? null;
                break;
              }
            }
            const finalPlanTitle =
              planTitleFromSteps ??
              coercePlanTitle(payload.result?.plan_title) ??
              updatedMetadata.plan_title ??
              null;

            const actionsSourceFromSteps =
              actionsFromSteps.length > 0
                ? actionsFromSteps
                : (Array.isArray(updatedMetadata.actions)
                    ? (updatedMetadata.actions as ChatActionSummary[])
                    : []);
            const rawActions =
              Array.isArray((updatedMetadata as any).raw_actions)
                ? (updatedMetadata as any).raw_actions.map((act: any) =>
                    convertRawActionToSummary(act)
                  )
                : [];
            const actionsForSummary =
              actionsSourceFromSteps.length > 0
                ? actionsSourceFromSteps
                : rawActions;
            const summaryFromActions = summarizeActions(actionsForSummary);

            const finalSummaryCandidate =
              typeof payload.result?.final_summary === 'string'
                ? (payload.result.final_summary as string)
                : typeof payload.result?.reply === 'string'
                  ? (payload.result.reply as string)
                : typeof payload.metadata?.final_summary === 'string'
                  ? (payload.metadata.final_summary as string)
                  : typeof payload.result?.response === 'string'
                    ? (payload.result.response as string)
                    : typeof payload.result?.message === 'string'
                      ? (payload.result.message as string)
                      : typeof payload.result?.text === 'string'
                        ? (payload.result.text as string)
                        : summarizeSteps(stepList) ??
                          summaryFromActions;
            const analysisCandidate =
              typeof payload.result?.analysis_text === 'string'
                ? (payload.result.analysis_text as string)
                : typeof payload.metadata?.analysis_text === 'string'
                  ? (payload.metadata.analysis_text as string)
                  : updatedMetadata.analysis_text ??
                    (typeof existingMetadata.analysis_text === 'string'
                      ? existingMetadata.analysis_text
                      : null);

            const fallbackSummary =
              normalizedFinalStatus === 'succeeded' && mergedToolResults.length > 0
                ? '工具已完成，请查看结果。'
                : targetMessage.content;
            const contentWithStatus =
              (analysisCandidate && analysisCandidate.trim().length > 0
                ? analysisCandidate
                : null) ??
              finalSummaryCandidate ??
              (normalizedFinalStatus === 'failed' && updatedMetadata.errors?.length
                ? `${targetMessage.content}\n\n⚠️ 后台执行失败：${updatedMetadata.errors.join('; ')}`
                : fallbackSummary);

            get().updateMessage(assistantMessageId, {
              content: contentWithStatus,
              metadata: {
                ...updatedMetadata,
                status: normalizedFinalStatus === 'succeeded' ? 'completed' : 'failed',
                plan_id: finalPlanIdCandidate ?? null,
                plan_title: finalPlanTitle ?? null,
                plan_message:
                  normalizedFinalStatus === 'succeeded'
                    ? (updatedMetadata as any).plan_message
                    : (updatedMetadata as any).plan_message,
                final_summary: finalSummaryCandidate ?? undefined,
                analysis_text: analysisCandidate ?? updatedMetadata.analysis_text,
              },
            });

            // 补偿：无论是否已有正文，都再拉取一次最新状态，避免 SSE 丢包导致需手动刷新
            const tracking = (updatedMetadata.tracking_id ?? payload.job_id) as string | undefined;
            if (tracking) {
              startActionStatusPolling(tracking, assistantMessageId, normalizedFinalStatus, contentWithStatus);
            }

            set((state) => {
              const planIdValue = finalPlanIdCandidate ?? state.currentPlanId ?? null;
              const planTitleValue = finalPlanTitle ?? state.currentPlanTitle ?? null;
              const updatedSession = state.currentSession
                ? {
                    ...state.currentSession,
                    plan_id: planIdValue,
                    plan_title: planTitleValue,
                  }
                : null;
              const updatedSessions = updatedSession
                ? state.sessions.map((session) =>
                    session.id === updatedSession.id ? updatedSession : session
                  )
                : state.sessions;

              return {
                currentPlanId: planIdValue,
                currentPlanTitle: planTitleValue,
                currentSession: updatedSession,
                sessions: updatedSessions,
              };
            });

            const sessionAfter = get().currentSession;
            const asyncEvents = derivePlanSyncEventsFromActions(finalActions, {
              fallbackPlanId: finalPlanIdCandidate ?? sessionAfter?.plan_id ?? null,
              fallbackPlanTitle: finalPlanTitle ?? sessionAfter?.plan_title ?? null,
            });
            const eventsToDispatch =
              asyncEvents.length > 0
                ? asyncEvents
                : finalPlanIdCandidate != null
                ? [
                    {
                      type: 'task_changed',
                      plan_id: finalPlanIdCandidate,
                      plan_title: finalPlanTitle ?? sessionAfter?.plan_title ?? null,
                    } as PlanSyncEventDetail,
                  ]
                : [];

            if (eventsToDispatch.length > 0) {
              for (const eventDetail of eventsToDispatch) {
                dispatchPlanSyncEvent(eventDetail, {
                  trackingId: updatedMetadata.tracking_id ?? null,
                  source: 'chat.stream',
                  status: normalizedFinalStatus,
                  sessionId: sessionAfter?.session_id ?? null,
                });
              }
            }

            if (sessionAfter) {
              try {
                await chatApi.updateSession(sessionAfter.session_id ?? sessionAfter.id, {
                  plan_id: finalPlanIdCandidate ?? null,
                  plan_title: finalPlanTitle ?? null,
                  is_active: normalizedFinalStatus === 'succeeded',
                });
              } catch (patchError) {
                console.warn('同步会话信息失败:', patchError);
              }

              // 强制同步一次历史，确保 UI 立即拿到落库后的总结（避免用户手动刷新）
              void get()
                .loadChatHistory(sessionAfter.session_id ?? sessionAfter.id)
                .catch((e) => console.warn('同步历史失败:', e));
            }
            if (flushHandle !== null) {
              window.cancelAnimationFrame(flushHandle);
              flushHandle = null;
            }
            flushAnalysisText(true);
            continue;
          }
          if (event.type === 'final') {
            finalPayload = event.payload;
            continue;
          }
          if (event.type === 'error') {
            throw new Error(event.message || 'Stream error');
          }
        }

        if (flushHandle !== null) {
          window.cancelAnimationFrame(flushHandle);
          flushHandle = null;
        }
        flushAnalysisText(true);

        if (!finalPayload && !jobFinalized) {
          throw new Error('No final response received');
        }
        if (jobFinalized) {
          set({ isProcessing: false });
          return;
        }
        if (!finalPayload) {
          throw new Error('No final response received');
        }

        const result: ChatResponsePayload = finalPayload;
        const stateSnapshot = get();
        const actions = (result.actions ?? []) as ChatActionSummary[];

        const metadataHasPlanId = (
          result.metadata && Object.prototype.hasOwnProperty.call(result.metadata, 'plan_id')
        );
        const metadataPlanId = metadataHasPlanId ? coercePlanId(result.metadata?.plan_id) : undefined;
        const planIdFromActions = extractPlanIdFromActions(actions);
        const resolvedPlanId =
          metadataHasPlanId
            ? (metadataPlanId ?? null)
            : (
                planIdFromActions
                ?? coercePlanId(mergedMetadata.plan_id)
                ?? stateSnapshot.currentPlanId
                ?? null
              );

        const metadataHasPlanTitle = (
          result.metadata && Object.prototype.hasOwnProperty.call(result.metadata, 'plan_title')
        );
        const metadataPlanTitle = metadataHasPlanTitle ? coercePlanTitle(result.metadata?.plan_title) : undefined;
        const actionsPlanTitle = extractPlanTitleFromActions(actions);
        const resolvedPlanTitle =
          metadataHasPlanTitle
            ? (metadataPlanTitle ?? null)
            : (
                coercePlanTitle(mergedMetadata.plan_title)
                ?? (
                  actionsPlanTitle !== undefined
                    ? coercePlanTitle(actionsPlanTitle) ?? null
                    : undefined
                )
                ?? stateSnapshot.currentPlanTitle
                ?? null
              );

        const resolvedTaskId =
          result.metadata?.task_id
          ?? mergedMetadata.task_id
          ?? stateSnapshot.currentTaskId
          ?? null;
        const resolvedTaskName = mergedMetadata.task_name ?? stateSnapshot.currentTaskName ?? null;
        const resolvedWorkflowId =
          result.metadata?.workflow_id
          ?? mergedMetadata.workflow_id
          ?? stateSnapshot.currentWorkflowId
          ?? null;

        const initialStatus = isActionStatus(result.metadata?.status)
          ? (result.metadata?.status as ChatActionStatus)
          : (actions.length > 0 ? 'pending' : 'completed');

        const assistantMetadata: ChatResponseMetadata = {
          ...(result.metadata ?? {}),
          plan_id: resolvedPlanId ?? null,
          plan_title: resolvedPlanTitle ?? null,
          task_id: resolvedTaskId ?? null,
          workflow_id: resolvedWorkflowId ?? null,
          actions,
          action_list: actions,
          status: initialStatus,
          analysis_text:
            result.metadata?.analysis_text !== undefined
              ? (result.metadata?.analysis_text as string | null)
              : streamedContent || '',
          final_summary:
            (result.metadata?.final_summary as string | undefined) ??
            (result.response ?? streamedContent ?? ''),
        };
        if (initialStatus === 'pending' || initialStatus === 'running') {
          (assistantMetadata as any).unified_stream = true;
          const planPreface = formatToolPlanPreface(actions);
          (assistantMetadata as any).plan_message = planPreface;
        }

        const initialToolResults = collectToolResultsFromMetadata(result.metadata?.tool_results);
        if (initialToolResults.length > 0) {
          assistantMetadata.tool_results = initialToolResults;
        }

        get().updateMessage(assistantMessageId, {
          content:
            (assistantMetadata as any).unified_stream === true
              ? (assistantMetadata.analysis_text && assistantMetadata.analysis_text.trim().length > 0
                ? assistantMetadata.analysis_text
                : ((assistantMetadata as any).plan_message as string) || assistantMetadata.final_summary || '')
              : (result.response ?? streamedContent),
          metadata: assistantMetadata,
        });
        set({ isProcessing: false });

        const trackingIdForPoll =
          typeof assistantMetadata.tracking_id === 'string' ? assistantMetadata.tracking_id : null;
        if ((assistantMetadata as any).unified_stream === true && trackingIdForPoll) {
          startActionStatusPolling(
            trackingIdForPoll,
            assistantMessageId,
            assistantMetadata.status,
            (assistantMetadata.analysis_text as string | undefined) ??
              (assistantMetadata.final_summary as string | undefined) ??
              null
          );
        }

        set((state) => {
          const planIdValue = resolvedPlanId ?? state.currentPlanId ?? null;
          const planTitleValue = resolvedPlanTitle ?? state.currentPlanTitle ?? null;
          const taskIdValue = resolvedTaskId ?? state.currentTaskId ?? null;
          const workflowValue = resolvedWorkflowId ?? state.currentWorkflowId ?? null;
          const updatedSession = state.currentSession
            ? {
                ...state.currentSession,
                plan_id: planIdValue,
                plan_title: planTitleValue,
                current_task_id: taskIdValue,
                current_task_name:
                  resolvedTaskName ?? state.currentSession.current_task_name ?? null,
                workflow_id: workflowValue,
              }
            : null;
          const updatedSessions = updatedSession
            ? state.sessions.map((session) =>
                session.id === updatedSession.id ? updatedSession : session
              )
            : state.sessions;

          return {
            currentPlanId: planIdValue,
            currentPlanTitle: planTitleValue,
            currentTaskId: taskIdValue,
            currentTaskName: resolvedTaskName ?? state.currentTaskName ?? null,
            currentWorkflowId: workflowValue,
            currentSession: updatedSession,
            sessions: updatedSessions,
          };
        });

        const sessionAfter = get().currentSession ?? stateSnapshot.currentSession ?? null;
        if (sessionAfter) {
          const sessionKey = sessionAfter.session_id ?? sessionAfter.id;
          const history = sessionKey ? autoTitleHistory.get(sessionKey) : undefined;
          const planIdSnapshot = sessionAfter.plan_id ?? null;
          const shouldAttemptAutoTitle =
            !!sessionKey &&
            sessionAfter.isUserNamed !== true &&
            (!history || history.planId !== planIdSnapshot);

          if (shouldAttemptAutoTitle) {
            const userMessages = sessionAfter.messages.filter((msg) => msg.type === 'user');
            const hasContext = planIdSnapshot !== null || userMessages.length > 0;
            if (hasContext) {
              void get()
                .autotitleSession(sessionKey)
                .catch((error) => console.warn('自动命名会话失败:', error));
            }
          }
        }

        if (resolvedWorkflowId !== stateSnapshot.currentWorkflowId) {
          get().setCurrentWorkflowId(resolvedWorkflowId ?? null);
        }

        if (assistantMetadata.agent_workflow) {
          window.dispatchEvent(
            new CustomEvent('tasksUpdated', {
              detail: {
                type: 'agent_workflow_created',
                workflow_id: assistantMetadata.workflow_id,
                total_tasks: assistantMetadata.total_tasks,
                dag_structure: assistantMetadata.dag_structure,
                plan_id: resolvedPlanId ?? null,
              },
            })
          );
        }

        if (assistantMetadata.session_id) {
          const state = get();
          const newSessionId = assistantMetadata.session_id as string;
          const current = state.currentSession
            ? { ...state.currentSession, session_id: newSessionId }
            : null;
          const sessions = state.sessions.map((s) =>
            s.id === current?.id ? { ...s, session_id: newSessionId } : s
          );
          set({ currentSession: current, sessions });
          SessionStorage.setCurrentSessionId(newSessionId);
        }

        const trackingId =
          typeof assistantMetadata.tracking_id === 'string'
            ? assistantMetadata.tracking_id
            : undefined;

        // 兜底：如果 unified_stream 返回了 tracking_id（后台动作），但 chat/stream 的 job_update 没有顺利推到前端，
        // 则通过 jobs SSE / 轮询 chat/actions 自动把最终总结回填到同一条消息，避免用户必须手动刷新。
        if (
          trackingId &&
          (assistantMetadata.status === 'pending' || assistantMetadata.status === 'running') &&
          !activeActionFollowups.has(trackingId)
        ) {
          activeActionFollowups.add(trackingId);
          void (async () => {
            try {
              const timeoutMs = 10 * 60_000; // 最长等待 10 分钟
              let lastStatus: ActionStatusResponse | null =
                await waitForActionCompletionViaStream(trackingId, timeoutMs);

              if (!lastStatus) {
                const intervalMs = 2_500;
                const start = Date.now();
                while (Date.now() - start < timeoutMs) {
                  try {
                    const status = await chatApi.getActionStatus(trackingId);
                    lastStatus = status;

                    // 同步中间态（pending/running），让 UI 状态更贴近实际
                    const interimMessages = get().messages;
                    const interimTarget = interimMessages.find((msg) => msg.id === assistantMessageId);
                    if (interimTarget) {
                      const interimMeta: ChatResponseMetadata = {
                        ...((interimTarget.metadata as ChatResponseMetadata | undefined) ?? {}),
                      };
                      if (status.status !== interimMeta.status) {
                        get().updateMessage(assistantMessageId, {
                          metadata: {
                            ...interimMeta,
                            status: status.status,
                            tracking_id: trackingId,
                            unified_stream: true,
                          } as any,
                        });
                      }
                    }

                    if (status.status === 'completed' || status.status === 'failed') {
                      break;
                    }
                  } catch (pollError) {
                    console.warn('轮询动作状态失败:', pollError);
                    break;
                  }
                  await new Promise((resolve) => setTimeout(resolve, intervalMs));
                }
              }

              if (!lastStatus) {
                return;
              }

              const currentMessages = get().messages;
              const targetMessage = currentMessages.find((msg) => msg.id === assistantMessageId);
              if (!targetMessage) {
                return;
              }

              const existingMetadata: ChatResponseMetadata = {
                ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}),
              };

              const toolResultsFromResult = collectToolResultsFromMetadata(
                (lastStatus.result as any)?.tool_results
              );
              const toolResultsFromMetadata = collectToolResultsFromMetadata(
                (lastStatus.metadata as any)?.tool_results
              );
              const mergedToolResults = mergeToolResults(toolResultsFromResult, toolResultsFromMetadata);

              const analysisCandidateRaw =
                typeof (lastStatus.result as any)?.analysis_text === 'string'
                  ? ((lastStatus.result as any).analysis_text as string)
                  : null;
              const analysisCandidate =
                analysisCandidateRaw && analysisCandidateRaw.trim().length > 0
                  ? analysisCandidateRaw
                  : null;

              const finalSummaryCandidateRaw =
                typeof (lastStatus.result as any)?.final_summary === 'string'
                  ? ((lastStatus.result as any).final_summary as string)
                  : typeof (lastStatus.metadata as any)?.final_summary === 'string'
                    ? ((lastStatus.metadata as any).final_summary as string)
                    : null;
              const finalSummaryCandidate =
                finalSummaryCandidateRaw && finalSummaryCandidateRaw.trim().length > 0
                  ? finalSummaryCandidateRaw
                  : null;

              const completionContent =
                analysisCandidate ??
                finalSummaryCandidate ??
                (lastStatus.status === 'completed'
                  ? '工具已完成，请查看结果。'
                  : '执行失败，请查看错误信息。');

              const nextMetadata: ChatResponseMetadata = {
                ...existingMetadata,
                status: lastStatus.status,
                tracking_id: lastStatus.tracking_id ?? trackingId,
                plan_id:
                  typeof lastStatus.plan_id === 'number'
                    ? lastStatus.plan_id
                    : existingMetadata.plan_id ?? null,
                actions: Array.isArray(lastStatus.actions) ? lastStatus.actions : existingMetadata.actions,
                action_list: Array.isArray(lastStatus.actions)
                  ? lastStatus.actions
                  : existingMetadata.action_list,
                errors: Array.isArray(lastStatus.errors) ? lastStatus.errors : existingMetadata.errors,
              };
              (nextMetadata as any).unified_stream = true;
              if (analysisCandidate) {
                (nextMetadata as any).analysis_text = analysisCandidate;
              }
              if (finalSummaryCandidate) {
                (nextMetadata as any).final_summary = finalSummaryCandidate;
              }
              if (mergedToolResults.length > 0) {
                nextMetadata.tool_results = mergedToolResults;
              } else if ('tool_results' in nextMetadata) {
                delete (nextMetadata as any).tool_results;
              }

              get().updateMessage(assistantMessageId, {
                content: completionContent,
                metadata: nextMetadata,
              });

              const sessionKey = get().currentSession?.session_id ?? get().currentSession?.id ?? null;
              if (sessionKey) {
                void get().loadChatHistory(sessionKey).catch((e) =>
                  console.warn('同步历史失败:', e)
                );
              }
            } finally {
              activeActionFollowups.delete(trackingId);
            }
          })();
        }

        if (!trackingId) {
          const planEvents = derivePlanSyncEventsFromActions(result.actions, {
            fallbackPlanId: resolvedPlanId ?? stateSnapshot.currentPlanId ?? null,
            fallbackPlanTitle: resolvedPlanTitle ?? stateSnapshot.currentPlanTitle ?? null,
          });
          if (planEvents.length > 0) {
            const sessionForEvent = get().currentSession ?? stateSnapshot.currentSession ?? null;
            for (const eventDetail of planEvents) {
              dispatchPlanSyncEvent(eventDetail, {
                source: 'chat.sync',
                sessionId: sessionForEvent?.session_id ?? null,
              });
            }
          }
        }

        try {
          const { currentSession: cs, currentWorkflowId: cw, currentPlanId: planIdForEvent } = get();
          window.dispatchEvent(
            new CustomEvent('tasksUpdated', {
              detail: {
                type: 'chat_message_processed',
                session_id: cs?.session_id ?? null,
                workflow_id: cw ?? null,
                plan_id: resolvedPlanId ?? planIdForEvent ?? null,
              },
            })
          );
        } catch (e) {
          console.warn('Failed to dispatch tasksUpdated event:', e);
        }

        const sessionForPatch = get().currentSession;
        if (!assistantMetadata.tracking_id && sessionForPatch) {
          void (async () => {
            try {
              await chatApi.updateSession(sessionForPatch.session_id ?? sessionForPatch.id, {
                plan_id: resolvedPlanId ?? null,
                plan_title: resolvedPlanTitle ?? null,
                current_task_id: resolvedTaskId ?? null,
                current_task_name: resolvedTaskName ?? null,
                is_active: true,
              });
            } catch (patchError) {
              console.warn('同步会话信息失败:', patchError);
            }
          })();
        }

      } catch (error) {
        console.error('Failed to send message:', error);
        set({ isProcessing: false });
        const errorContent =
          '抱歉，我暂时无法处理你的请求。可能的原因：\n\n1. 后端服务未完全启动\n2. LLM API 未配置\n3. 网络连接问题\n\n请检查后端服务状态，或稍后重试。';
        if (assistantMessageAdded) {
          get().updateMessage(assistantMessageId, {
            content: errorContent,
            metadata: {
              status: 'failed',
              errors: [error instanceof Error ? error.message : String(error)],
            },
          });
        } else {
          const errorMessage: ChatMessage = {
            id: `msg_${Date.now()}_assistant`,
            type: 'assistant',
            content: errorContent,
            timestamp: new Date(),
          };
          get().addMessage(errorMessage);
        }
      }
    },
    // 重试最后一条消息 / 最近一次失败的后台动作
    retryLastMessage: async () => {
      const { messages, isProcessing } = get();
      if (isProcessing) return;

      const lastFailedActionMsg = [...messages]
        .reverse()
        .find((msg) => {
          if (msg.type !== 'assistant') return false;
          const meta = msg.metadata as any;
          return meta && typeof meta.tracking_id === 'string' && meta.status === 'failed';
        });

      if (lastFailedActionMsg) {
        const meta = lastFailedActionMsg.metadata as any;
        const oldTrackingId = meta.tracking_id as string;
        await get().retryActionRun(oldTrackingId, meta.raw_actions ?? []);
        return;
      }

      const lastUserMessage = [...messages].reverse().find((msg) => msg.type === 'user');
      if (lastUserMessage) {
        await get().sendMessage(lastUserMessage.content, lastUserMessage.metadata);
      }
    },

    // 重试指定 tracking_id 的后台动作（用于消息内的重新执行按钮）
    retryActionRun: async (oldTrackingId: string, rawActionsOverride: any[] = []) => {
      const { currentSession, messages, isProcessing } = get();
      if (!oldTrackingId || isProcessing) return;

      try {
        set({ isProcessing: true });

        const retryStatus = await chatApi.retryActionRun(oldTrackingId);
        const newTrackingId = retryStatus.tracking_id;

        const rawActions = Array.isArray(retryStatus.actions)
          ? retryStatus.actions.map((a: any, idx: number) => ({
              kind: a.kind,
              name: a.name,
              parameters: a.parameters,
              order: a.order ?? idx + 1,
              blocking: a.blocking ?? true,
            }))
          : rawActionsOverride;

        const pendingAssistantId = `msg_${Date.now()}_assistant_retry`;
        const pendingAssistant: ChatMessage = {
          id: pendingAssistantId,
          type: 'assistant',
          content: '正在重新执行该动作…',
          timestamp: new Date(),
          metadata: {
            status: 'pending',
            unified_stream: true,
            plan_message: '正在重新执行该动作…',
            tracking_id: newTrackingId,
            plan_id: retryStatus.plan_id ?? null,
            raw_actions: rawActions,
            retry_of: oldTrackingId,
          },
        };
        get().addMessage(pendingAssistant);

        const timeoutMs = 120_000;
        let lastStatus: ActionStatusResponse | null =
          await waitForActionCompletionViaStream(newTrackingId, timeoutMs);

        if (!lastStatus) {
          const intervalMs = 2_500;
          const start = Date.now();
          while (Date.now() - start < timeoutMs) {
            try {
              const status = await chatApi.getActionStatus(newTrackingId);
              lastStatus = status;
              if (status.status === 'completed' || status.status === 'failed') {
                break;
              }
            } catch (pollError) {
              console.warn('轮询重试动作状态失败:', pollError);
              break;
            }
            await new Promise((resolve) => setTimeout(resolve, intervalMs));
          }
        }

        if (lastStatus) {
          const finalSummary =
            typeof lastStatus.result?.final_summary === 'string'
              ? (lastStatus.result.final_summary as string)
              : typeof lastStatus.metadata?.final_summary === 'string'
                ? (lastStatus.metadata.final_summary as string)
                : null;
          const completionContent =
            finalSummary ??
            (lastStatus.status === 'completed'
              ? '工具已完成，但未生成最终总结，请查看工具结果。'
              : '执行失败，请查看错误信息。');
          const toolResultsFromRetry = mergeToolResults(
            collectToolResultsFromMetadata(lastStatus.result?.tool_results),
            collectToolResultsFromMetadata(lastStatus.metadata?.tool_results)
          );
          get().updateMessage(pendingAssistantId, {
            content: completionContent,
            metadata: {
              ...(pendingAssistant.metadata as any),
              status: lastStatus.status,
              unified_stream: true,
              plan_message: (pendingAssistant.metadata as any)?.plan_message ?? null,
              actions: lastStatus.actions ?? [],
              tool_results: toolResultsFromRetry.length > 0 ? toolResultsFromRetry : undefined,
              errors: lastStatus.errors ?? undefined,
            },
          });
        }
      } catch (error) {
        console.error('Retry action run failed:', error);
        const lastUserMessage = [...messages].reverse().find((msg) => msg.type === 'user');
        if (lastUserMessage) {
          await get().sendMessage(lastUserMessage.content, lastUserMessage.metadata);
        }
      } finally {
        set({ isProcessing: false });
      }
    },

    // 开始新会话（总是生成新的ID）
    startNewSession: (title) => {
      const sessionId = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      const providerPreference = get().defaultSearchProvider ?? null;
      const baseModelPreference = get().defaultBaseModel ?? null;
      const llmProviderPreference = get().defaultLLMProvider ?? null;
      autoTitleHistory.delete(sessionId);
      const session: ChatSession = {
        id: sessionId,
        title: title || `对话 ${new Date().toLocaleString()}`,
        messages: [],
        created_at: new Date(),
        updated_at: new Date(),
        workflow_id: null,
        session_id: sessionId,
        plan_id: null,
        plan_title: null,
        current_task_id: null,
        current_task_name: null,
        last_message_at: null,
        is_active: true,
        defaultSearchProvider: providerPreference,
        defaultBaseModel: baseModelPreference,
        defaultLLMProvider: llmProviderPreference,
        titleSource: 'local',
        isUserNamed: false,
      };

      console.log('🆕 创建新会话:', {
        前端会话ID: session.id,
        后端会话ID: session.session_id,
        标题: session.title
      });

      get().addSession(session);
      get().setCurrentSession(session);
      set({ currentWorkflowId: null });
      
      // 保存当前会话ID和所有会话ID列表
      SessionStorage.setCurrentSessionId(sessionId);

      return session;
    },

    // 恢复已有会话（用于刷新后保持历史）
    restoreSession: async (sessionId, title) => {
      let session = get().sessions.find((s) => s.id === sessionId) || null;

      if (!session) {
        await get().loadSessions();
        session = get().sessions.find((s) => s.id === sessionId) || null;
      }

      if (!session) {
        const providerPreference = get().defaultSearchProvider ?? null;
        const baseModelPreference = get().defaultBaseModel ?? null;
        const llmProviderPreference = get().defaultLLMProvider ?? null;
        autoTitleHistory.delete(sessionId);
        session = {
          id: sessionId,
          title: title || `对话 ${new Date().toLocaleString()}`,
          messages: [],
          created_at: new Date(),
          updated_at: new Date(),
          workflow_id: null,
          session_id: sessionId,
          plan_id: null,
          plan_title: null,
          current_task_id: null,
          current_task_name: null,
          last_message_at: null,
          is_active: true,
          defaultSearchProvider: providerPreference,
          defaultBaseModel: baseModelPreference,
          defaultLLMProvider: llmProviderPreference,
          titleSource: 'local',
          isUserNamed: false,
        };
        get().addSession(session);
      }

      get().setCurrentSession(session);
      SessionStorage.setCurrentSessionId(sessionId);

      await get().loadChatHistory(sessionId);

      const refreshedSession = get().currentSession;
      if (refreshedSession && refreshedSession.id === sessionId) {
        return refreshedSession;
      }

      return refreshedSession || session;
    },

    // 加载聊天历史
    loadChatHistory: async (sessionId: string, options) => {
      const { beforeId = null, append = false, pageSize } = options ?? {};
      if (append && (beforeId === null || beforeId === undefined)) {
        set({ historyHasMore: false });
        return;
      }
      if (append && get().historyLoading) {
        return;
      }
      const limit = pageSize ?? get().historyPageSize ?? 50;
      try {
        set({ historyLoading: true });
        console.log('📖 加载聊天历史:', sessionId);
        const query = new URLSearchParams({ limit: String(limit) });
        if (beforeId !== null && beforeId !== undefined) {
          query.set('before_id', String(beforeId));
        }
        const response = await fetch(
          `${ENV.API_BASE_URL}/chat/history/${sessionId}?${query.toString()}`
        );

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        const hasMore =
          typeof data.has_more === 'boolean'
            ? data.has_more
            : Array.isArray(data.messages) && data.messages.length >= limit;

        if (data.success && data.messages && data.messages.length > 0) {
          console.log(`✅ 加载了 ${data.messages.length} 条历史消息`);

          const existingMessages = get().messages;
          const existingToolResults = buildToolResultsCache(existingMessages);

          const newMessages: ChatMessage[] = data.messages.map((msg: any, index: number) => {
            const metadata =
              msg.metadata && typeof msg.metadata === 'object'
                ? { ...(msg.metadata as Record<string, any>) }
                : {};
            if (typeof msg.id === 'number') {
              metadata.backend_id = msg.id;
            }
            const trackingId =
              typeof metadata.tracking_id === 'string' ? metadata.tracking_id : null;
            let toolResults = collectToolResultsFromMetadata(metadata.tool_results);
            if (toolResults.length === 0 && trackingId && existingToolResults.has(trackingId)) {
              toolResults = existingToolResults.get(trackingId) ?? [];
            }
            if (toolResults.length > 0) {
              metadata.tool_results = toolResults;
            }
            const backendId =
              typeof msg.id === 'number' ? msg.id : null;
            const messageId = backendId !== null ? `${sessionId}_${backendId}` : `${sessionId}_${index}`;
            return {
              id: messageId,
              type: (msg.role || 'assistant') as 'user' | 'assistant' | 'system',
              content: msg.content,
              timestamp: msg.timestamp ? new Date(msg.timestamp) : new Date(),
              metadata,
            };
          });

          const merged = append ? [...newMessages, ...existingMessages] : newMessages;
          const seen = new Set<string>();
          const messages = merged.filter((msg) => {
            if (seen.has(msg.id)) {
              return false;
            }
            seen.add(msg.id);
            return true;
          });

          set({ messages });

          const planContext = derivePlanContextFromMessages(messages);

          set((state) => {
            const targetSession = state.sessions.find((s) => s.id === sessionId);
            if (!targetSession) {
              return {};
            }

            const planIdValue =
              planContext.planId !== undefined
                ? planContext.planId ?? null
                : targetSession.plan_id ?? null;
            const planTitleValue =
              planContext.planTitle !== undefined
                ? planContext.planTitle ?? null
                : targetSession.plan_title ?? null;

            const lastMessage = messages[messages.length - 1];
            const updatedSession: ChatSession = {
              ...targetSession,
              messages,
              updated_at: new Date(),
              plan_id: planIdValue,
              plan_title: planTitleValue,
              last_message_at: lastMessage ? lastMessage.timestamp : targetSession.last_message_at ?? null,
            };

            const sessions = state.sessions.map((s) =>
              s.id === sessionId ? updatedSession : s
            );

            const isCurrent = state.currentSession?.id === sessionId;

            return {
              sessions,
              currentSession: isCurrent ? updatedSession : state.currentSession,
              currentPlanId: isCurrent ? planIdValue ?? null : state.currentPlanId,
              currentPlanTitle: isCurrent ? planTitleValue ?? null : state.currentPlanTitle,
            };
          });

          const nextBeforeId =
            typeof data.next_before_id === 'number'
              ? data.next_before_id
              : resolveHistoryCursor(messages);
          set({
            historyBeforeId: nextBeforeId ?? null,
            historyHasMore: hasMore,
          });
        } else {
          console.log('📭 没有历史消息');
          set({
            historyHasMore: false,
            historyBeforeId: null,
          });
          if (!append) {
            set({ messages: [] });
          }
        }
      } catch (error) {
        console.error('加载聊天历史失败:', error);
        throw error;
      } finally {
        set({ historyLoading: false });
      }
    },

    // Memory 操作方法
    toggleMemory: () => set((state) => ({ memoryEnabled: !state.memoryEnabled })),

    setMemoryEnabled: (enabled) => set({ memoryEnabled: enabled }),

    setRelevantMemories: (memories) => set({ relevantMemories: memories }),

    saveMessageAsMemory: async (message, memoryType = 'conversation', importance = 'medium') => {
      try {
        console.log('💾 保存消息为记忆:', { content: message.content.substring(0, 50) });

        await memoryApi.saveMemory({
          content: message.content,
          memory_type: memoryType as any,
          importance: importance as any,
          tags: ['chat', 'manual_saved'],
          context: `对话保存于 ${new Date().toLocaleString()}`,
          related_task_id: message.metadata?.task_id
        });

        console.log('✅ 消息已保存为记忆');
      } catch (error) {
        console.error('❌ 保存记忆失败:', error);
        throw error;
      }
    },

    setDefaultSearchProvider: async (provider) => {
      const normalized: WebSearchProvider | null = provider ?? null;
      const prevProvider = get().defaultSearchProvider ?? null;
      if (normalized === prevProvider) {
        return;
      }

      const currentSession = get().currentSession;
      const sessionKey = currentSession?.session_id ?? currentSession?.id ?? null;

      set((state) => ({
        defaultSearchProvider: normalized,
        isUpdatingProvider: currentSession ? true : false,
        currentSession: currentSession
          ? { ...currentSession, defaultSearchProvider: normalized }
          : currentSession,
        sessions: currentSession
          ? state.sessions.map((session) =>
              session.id === sessionKey
                ? { ...session, defaultSearchProvider: normalized }
                : session
            )
          : state.sessions,
      }));

      if (!currentSession) {
        set({ isUpdatingProvider: false });
        return;
      }

      try {
        if (!sessionKey) {
          set({ isUpdatingProvider: false });
          return;
        }

        await chatApi.updateSession(sessionKey, {
          settings: { default_search_provider: normalized },
        });
      } catch (error) {
        console.error('更新默认搜索提供商失败:', error);
        set((state) => ({
          defaultSearchProvider: prevProvider,
          isUpdatingProvider: false,
          currentSession: state.currentSession
            ? { ...state.currentSession, defaultSearchProvider: prevProvider }
            : state.currentSession,
          sessions: state.sessions.map((session) =>
            session.id === sessionKey
              ? { ...session, defaultSearchProvider: prevProvider }
              : session
          ),
        }));
        throw error;
      }

      set((state) => ({
        isUpdatingProvider: false,
        defaultSearchProvider: normalized,
        currentSession: state.currentSession
          ? { ...state.currentSession, defaultSearchProvider: normalized }
          : state.currentSession,
        sessions: state.sessions.map((session) =>
          session.id === sessionKey
            ? { ...session, defaultSearchProvider: normalized }
            : session
        ),
      }));
    },

    setDefaultBaseModel: async (model) => {
      const normalized: BaseModelOption | null = model ?? null;
      const prevModel = get().defaultBaseModel ?? null;
      if (normalized === prevModel) {
        return;
      }

      const currentSession = get().currentSession;
      const sessionKey = currentSession?.session_id ?? currentSession?.id ?? null;

      set((state) => ({
        defaultBaseModel: normalized,
        isUpdatingBaseModel: currentSession ? true : false,
        currentSession: currentSession
          ? { ...currentSession, defaultBaseModel: normalized }
          : currentSession,
        sessions: currentSession
          ? state.sessions.map((session) =>
              session.id === sessionKey
                ? { ...session, defaultBaseModel: normalized }
                : session
            )
          : state.sessions,
      }));

      if (!currentSession) {
        set({ isUpdatingBaseModel: false });
        return;
      }

      try {
        if (!sessionKey) {
          set({ isUpdatingBaseModel: false });
          return;
        }

        await chatApi.updateSession(sessionKey, {
          settings: { default_base_model: normalized },
        });
      } catch (error) {
        console.error('更新默认基座模型失败:', error);
        set((state) => ({
          defaultBaseModel: prevModel,
          isUpdatingBaseModel: false,
          currentSession: state.currentSession
            ? { ...state.currentSession, defaultBaseModel: prevModel }
            : state.currentSession,
          sessions: state.sessions.map((session) =>
            session.id === sessionKey
              ? { ...session, defaultBaseModel: prevModel }
              : session
          ),
        }));
        throw error;
      }

      set((state) => ({
        isUpdatingBaseModel: false,
        defaultBaseModel: normalized,
        currentSession: state.currentSession
          ? { ...state.currentSession, defaultBaseModel: normalized }
          : state.currentSession,
        sessions: state.sessions.map((session) =>
          session.id === sessionKey
            ? { ...session, defaultBaseModel: normalized }
            : session
        ),
      }));
    },

    setDefaultLLMProvider: async (provider) => {
      const normalized: LLMProviderOption | null = provider ?? null;
      const prevProvider = get().defaultLLMProvider ?? null;
      if (normalized === prevProvider) {
        return;
      }

      const currentSession = get().currentSession;
      const sessionKey = currentSession?.session_id ?? currentSession?.id ?? null;

      set((state) => ({
        defaultLLMProvider: normalized,
        isUpdatingLLMProvider: currentSession ? true : false,
        currentSession: currentSession
          ? { ...currentSession, defaultLLMProvider: normalized }
          : currentSession,
        sessions: currentSession
          ? state.sessions.map((session) =>
              session.id === sessionKey
                ? { ...session, defaultLLMProvider: normalized }
                : session
            )
          : state.sessions,
      }));

      if (!currentSession) {
        set({ isUpdatingLLMProvider: false });
        return;
      }

      try {
        if (!sessionKey) {
          set({ isUpdatingLLMProvider: false });
          return;
        }

        await chatApi.updateSession(sessionKey, {
          settings: { default_llm_provider: normalized },
        });
      } catch (error) {
        console.error('更新默认LLM提供商失败:', error);
        set((state) => ({
          defaultLLMProvider: prevProvider,
          isUpdatingLLMProvider: false,
          currentSession: state.currentSession
            ? { ...state.currentSession, defaultLLMProvider: prevProvider }
            : state.currentSession,
          sessions: state.sessions.map((session) =>
            session.id === sessionKey
              ? { ...session, defaultLLMProvider: prevProvider }
              : session
          ),
        }));
        throw error;
      }

      set((state) => ({
        isUpdatingLLMProvider: false,
        defaultLLMProvider: normalized,
        currentSession: state.currentSession
          ? { ...state.currentSession, defaultLLMProvider: normalized }
          : state.currentSession,
        sessions: state.sessions.map((session) =>
          session.id === sessionKey
            ? { ...session, defaultLLMProvider: normalized }
            : session
        ),
      }));
    },

    autotitleSession: async (sessionId, options = {}) => {
      const sessionKey = sessionId?.trim();
      if (!sessionKey) {
        return null;
      }

      if (pendingAutotitleSessions.has(sessionKey)) {
        return null;
      }

      pendingAutotitleSessions.add(sessionKey);

      const payload: { force?: boolean; strategy?: string | null } = {};
      if (options.force) {
        payload.force = true;
      }
      if (options.strategy !== undefined) {
        payload.strategy = options.strategy;
      }

      try {
        const result = await chatApi.autotitleSession(sessionKey, payload);
        set((state) => {
          const updateSession = (session: ChatSession): ChatSession => {
            const matchId = session.session_id ?? session.id;
            if (matchId !== sessionKey) {
              return session;
            }

            const next: ChatSession = {
              ...session,
              title: result.title ?? session.title,
              titleSource: result.source ?? session.titleSource ?? null,
            };

            if (result.skipped_reason === 'user_named') {
              next.isUserNamed = true;
            } else if (result.source === 'user') {
              next.isUserNamed = true;
            } else if (result.updated) {
              next.isUserNamed = false;
            }

            return next;
          };

          const currentSession = state.currentSession
            ? updateSession(state.currentSession)
            : state.currentSession;

          return {
            currentSession,
            sessions: state.sessions.map(updateSession),
          };
        });

        const sessionsAfter = get().sessions;
        const target = sessionsAfter.find((session) => {
          const matchId = session.session_id ?? session.id;
          return matchId === sessionKey;
        });
        if (target) {
          autoTitleHistory.set(sessionKey, { planId: target.plan_id ?? null });
        }

        return result;
      } catch (error) {
        console.warn('自动生成会话标题失败:', error);
        throw error;
      } finally {
        pendingAutotitleSessions.delete(sessionKey);
      }
    },

    loadSessions: async () => {
      try {
        const response = await chatApi.getSessions({ limit: 100, offset: 0 });
        const summaries = response.sessions ?? [];
        const existingSessions = get().sessions;
        const existingMap = new Map(existingSessions.map((s) => [s.id, s]));

        const normalized = summaries.map((summary) => {
          const base = summaryToChatSession(summary);
          const existing = existingMap.get(summary.id);
          if (!existing) {
            return base;
          }
          return {
            ...base,
            messages: existing.messages,
            workflow_id: existing.workflow_id ?? base.workflow_id,
            created_at: existing.created_at ?? base.created_at,
            updated_at: base.updated_at,
          };
        });

        for (const session of normalized) {
          const sessionKey = session.session_id ?? session.id;
          if (!sessionKey) {
            continue;
          }
          const source = session.titleSource ?? null;
          if (source && source !== 'default' && source !== 'local') {
            autoTitleHistory.set(sessionKey, { planId: session.plan_id ?? null });
          }
        }

        set({ sessions: normalized });
        SessionStorage.setAllSessionIds(normalized.map((s) => s.id));

        const storedId = SessionStorage.getCurrentSessionId();
        const nextSession =
          (storedId && normalized.find((s) => s.id === storedId)) ||
          normalized[0] ||
          null;

        if (nextSession) {
          get().setCurrentSession(nextSession);
        } else {
          set({
            currentSession: null,
            messages: [],
            currentPlanId: null,
            currentPlanTitle: null,
            currentTaskId: null,
            currentTaskName: null,
            currentWorkflowId: null,
            defaultSearchProvider: null,
            defaultBaseModel: null,
          });
          SessionStorage.clearCurrentSessionId();
        }
      } catch (error) {
        console.error('加载会话列表失败:', error);
        throw error;
      }
    },

    // 文件上传方法
    uploadFile: async (file: File) => {
      const session = get().currentSession;
      if (!session) {
        throw new Error('请先创建或选择一个会话');
      }

      try {
        const response = await uploadApi.uploadFile(file, session.id);
        const uploadedFile: UploadedFile = {
          file_id: response.file_path.split('/').pop()?.split('_')[0] || '',
          file_path: response.file_path,
          file_name: response.file_name,
          original_name: response.original_name,
          file_size: response.file_size,
          file_type: response.file_type,
          uploaded_at: response.uploaded_at,
          category: response.category,
          is_archive: response.is_archive,
          extracted_path: response.extracted_path,
          extracted_files: response.extracted_files,
        };

        set((state) => ({
          uploadedFiles: [...state.uploadedFiles, uploadedFile],
        }));

        return uploadedFile;
      } catch (error) {
        console.error('上传文件失败:', error);
        throw error;
      }
    },

    removeUploadedFile: async (fileId: string) => {
      const session = get().currentSession;
      if (!session) {
        return;
      }

      try {
        await uploadApi.deleteFile(fileId, session.id);
        set((state) => ({
          uploadedFiles: state.uploadedFiles.filter((f) => f.file_id !== fileId),
        }));
      } catch (error) {
        console.error('删除文件失败:', error);
        throw error;
      }
    },

    clearUploadedFiles: () => {
      set({ uploadedFiles: [] });
    },
  }))
);
