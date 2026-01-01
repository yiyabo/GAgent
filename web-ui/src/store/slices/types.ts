import { StateCreator } from 'zustand';
import {
    ChatSession,
    ChatMessage,
    Memory,
    UploadedFile,
    WebSearchProvider,
    BaseModelOption,
    LLMProviderOption,
    ChatSessionAutoTitleResult,
    ChatActionSummary,
} from '@/types';

export interface ChatState {
    // Session Slice State
    currentSession: ChatSession | null;
    sessions: ChatSession[];

    // Message Slice State
    messages: ChatMessage[];
    historyHasMore: boolean;
    historyBeforeId: number | null;
    historyLoading: boolean;
    historyPageSize: number;

    // Context Slice State
    currentWorkflowId: string | null;
    currentPlanId: number | null;
    currentPlanTitle: string | null;
    currentTaskId: number | null;
    currentTaskName: string | null;
    defaultSearchProvider: WebSearchProvider | null;
    defaultBaseModel: BaseModelOption | null;
    defaultLLMProvider: LLMProviderOption | null;

    // UI Slice State
    inputText: string;
    isTyping: boolean;
    isProcessing: boolean;
    isUpdatingProvider: boolean;
    isUpdatingBaseModel: boolean;
    isUpdatingLLMProvider: boolean;
    chatPanelVisible: boolean;
    chatPanelWidth: number;

    // Memory & Files Slice State
    memoryEnabled: boolean;
    relevantMemories: Memory[];
    uploadedFiles: UploadedFile[];
    uploadingFiles: Array<{ file: File; progress: number }>;

    // =========================
    // ACTIONS
    // =========================

    // Session Actions
    setCurrentSession: (session: ChatSession | null) => void;
    addSession: (session: ChatSession) => void;
    removeSession: (sessionId: string) => void;
    deleteSession: (sessionId: string, options?: { archive?: boolean }) => Promise<void>;
    startNewSession: (title?: string) => ChatSession;
    restoreSession: (sessionId: string, title?: string) => Promise<ChatSession>;
    loadSessions: () => Promise<void>;
    autotitleSession: (
        sessionId: string,
        options?: { force?: boolean; strategy?: string | null }
    ) => Promise<ChatSessionAutoTitleResult | null>;

    // Message Actions
    addMessage: (message: ChatMessage) => void;
    updateMessage: (messageId: string, updates: Partial<ChatMessage>) => void;
    removeMessage: (messageId: string) => void;
    clearMessages: () => void;
    loadChatHistory: (
        sessionId: string,
        options?: { beforeId?: number | null; append?: boolean; pageSize?: number }
    ) => Promise<void>;
    sendMessage: (content: string, metadata?: ChatMessage['metadata']) => Promise<void>;
    retryLastMessage: () => Promise<void>;
    retryActionRun: (trackingId: string, rawActions?: any[]) => Promise<void>;

    // Context Actions
    setChatContext: (context: { planId?: number | null; planTitle?: string | null; taskId?: number | null; taskName?: string | null }) => void;
    clearChatContext: () => void;
    setCurrentWorkflowId: (workflowId: string | null) => void;
    setDefaultSearchProvider: (provider: WebSearchProvider | null) => Promise<void>;
    setDefaultBaseModel: (model: BaseModelOption | null) => Promise<void>;
    setDefaultLLMProvider: (provider: LLMProviderOption | null) => Promise<void>;

    // UI Actions
    setInputText: (text: string) => void;
    setIsTyping: (typing: boolean) => void;
    setIsProcessing: (processing: boolean) => void;
    toggleChatPanel: () => void;
    setChatPanelVisible: (visible: boolean) => void;
    setChatPanelWidth: (width: number) => void;

    // Memory & Files Actions
    toggleMemory: () => void;
    setMemoryEnabled: (enabled: boolean) => void;
    setRelevantMemories: (memories: Memory[]) => void;
    saveMessageAsMemory: (message: ChatMessage, memoryType?: string, importance?: string) => Promise<void>;
    uploadFile: (file: File) => Promise<UploadedFile>;
    removeUploadedFile: (fileId: string) => Promise<void>;
    clearUploadedFiles: () => void;
}

export type ChatSliceCreator<T = Partial<ChatState>> = StateCreator<ChatState, [['zustand/subscribeWithSelector', never]], [], T>;
