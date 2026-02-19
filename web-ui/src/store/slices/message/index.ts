import { ChatSliceCreator } from '../types';
import {
    ChatMessage,
    ChatActionStatus,
    Memory,
} from '@/types';
import {
    streamChatEvents,
    buildToolResultsCache,
    resolveHistoryCursor,
} from '../../chatUtils';
import { memoryApi } from '@api/memory';
import { ENV } from '@/config/env';
import {
    collectToolResultsFromMetadata,
} from '@utils/toolResults';
import { startActionStatusPolling, flushAnalysisText, scheduleFlush, retryActionRun as retryActionRunHelper } from './helpers';
import {
    handleDelta,
    handleJobUpdate,
    handleFinal,
    handleThinkingStep,
    handleThinkingDelta,
    processBackgroundDispatch,
    processFinalPayload,
} from './streamHandlers';
import type { StreamMutableState, StreamHandlerContext } from './types';

export const createMessageSlice: ChatSliceCreator = (set, get) => ({
    messages: [],
    historyHasMore: false,
    historyBeforeId: null,
    historyLoading: false,
    historyPageSize: 100,

    addMessage: (message) => set((state) => {
        const newMessages = [...state.messages, message];
        let updatedSession = state.currentSession;
        if (updatedSession) {
            updatedSession = {
                ...updatedSession,
                messages: newMessages,
                updated_at: new Date(),
            };
        }
        const updatedSessions = state.sessions.map(session =>
            session.id === updatedSession?.id ? updatedSession : session
        );
        return {
            messages: newMessages,
            currentSession: updatedSession,
            sessions: updatedSessions,
        };
    }),

    updateMessage: (messageId, updates) => set((state) => {
        const updatedMessages = state.messages.map(msg =>
            msg.id === messageId ? { ...msg, ...updates } : msg
        );
        let updatedSession = state.currentSession;
        if (updatedSession) {
            updatedSession = {
                ...updatedSession,
                messages: updatedMessages,
                updated_at: new Date(),
            };
        }
        const updatedSessions = state.sessions.map(session =>
            session.id === updatedSession?.id ? updatedSession : session
        );
        return {
            messages: updatedMessages,
            currentSession: updatedSession,
            sessions: updatedSessions,
        };
    }),

    removeMessage: (messageId) => set((state) => ({
        messages: state.messages.filter(msg => msg.id !== messageId),
    })),

    clearMessages: () =>
        set({
            messages: [],
            historyBeforeId: null,
            historyHasMore: false,
            historyLoading: false,
        }),

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

                    // Hydrate thinking_process from metadata
                    const thinkingProcess = metadata.thinking_process;

                    const backendId = typeof msg.id === 'number' ? msg.id : null;
                    const messageId = backendId !== null ? `${sessionId}_${backendId}` : `${sessionId}_${index}`;
                    return {
                        id: messageId,
                        type: (msg.role || 'assistant') as 'user' | 'assistant' | 'system',
                        content: msg.content,
                        timestamp: msg.timestamp ? new Date(msg.timestamp) : new Date(),
                        metadata,
                        thinking_process: thinkingProcess,
                    };
                });

                const merged = append ? [...newMessages, ...existingMessages] : newMessages;
                const seen = new Set<string>();
                const messages = merged.filter((msg) => {
                    if (seen.has(msg.id)) return false;
                    seen.add(msg.id);
                    return true;
                });

                set({ messages });

                set((state) => {
                    const targetSession = state.sessions.find((s) => s.id === sessionId);
                    if (!targetSession) return {};
                    const lastMessage = messages[messages.length - 1];
                    const updatedSession: any = {
                        ...targetSession,
                        messages,
                        updated_at: new Date(),
                        last_message_at: lastMessage ? lastMessage.timestamp : targetSession.last_message_at ?? null,
                    };
                    const sessions = state.sessions.map((s) => (s.id === sessionId ? updatedSession : s));
                    const isCurrent = state.currentSession?.id === sessionId;
                    return {
                        sessions,
                        currentSession: isCurrent ? updatedSession : state.currentSession,
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
                set({ historyHasMore: false, historyBeforeId: null });
                if (!append) set({ messages: [] });
            }
        } catch (error) {
            console.error('加载聊天历史失败:', error);
            throw error;
        } finally {
            set({ historyLoading: false });
        }
    },

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

        const attachments = uploadedFiles.length > 0
            ? uploadedFiles.map((f) => ({
                type: (Boolean(f.file_type?.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp|tiff?)$/i.test(f.original_name || f.file_name)) ? 'image' : 'file') as 'image' | 'file',
                path: f.file_path,
                name: f.original_name || f.file_name,
                ...(f.extracted_path ? { extracted_path: f.extracted_path } : {}),
            }))
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

        // Optimistic Assistant Message
        const assistantMessageId = `msg_${Date.now()}_assistant`;
        const assistantMessage: ChatMessage = {
            id: assistantMessageId,
            type: 'assistant',
            content: '',
            timestamp: new Date(),
            metadata: { status: 'pending', unified_stream: true, plan_message: null },
        };
        get().addMessage(assistantMessage);
        let assistantMessageAdded = true;

        set({ isProcessing: true, inputText: '' });

        try {
            let memories: Memory[] = [];
            if (memoryEnabled) {
                try {
                    const memoryResult = await memoryApi.queryMemory({ search_text: content, limit: 3, min_similarity: 0.6 });
                    memories = memoryResult.memories || [];
                    set({ relevantMemories: memories });
                } catch (error) {
                    console.error('Memory RAG 查询失败:', error);
                }
            }

            const providerToUse = defaultSearchProvider ?? currentSession?.defaultSearchProvider ?? undefined;
            const baseModelToUse = defaultBaseModel ?? currentSession?.defaultBaseModel ?? undefined;
            const llmProviderToUse = defaultLLMProvider ?? currentSession?.defaultLLMProvider ?? undefined;
            const recentMessages = get().messages.slice(-10).map((msg) => ({
                role: msg.type,
                content: msg.content,
                timestamp: msg.timestamp.toISOString(),
            }));

            const memoryContext = memories.length > 0 ? memories.map((m) => ({ content: m.content, similarity: m.similarity, memory_type: m.memory_type })) : undefined;

            const chatRequest: any = {
                message: content,
                mode: 'assistant' as const,
                history: recentMessages,
                session_id: currentSession?.session_id,
                context: {
                    plan_id: mergedMetadata.plan_id,
                    task_id: mergedMetadata.task_id,
                    plan_title: mergedMetadata.plan_title,
                    workflow_id: mergedMetadata.workflow_id,
                    default_search_provider: providerToUse,
                    default_base_model: baseModelToUse,
                    default_llm_provider: llmProviderToUse,
                    attachments,
                    memories: memoryContext,
                    ...(metadata ?? {}),
                },
            };

            const state: StreamMutableState = {
                streamedContent: '',
                lastFlushedContent: '',
                flushHandle: null,
                finalPayload: null,
                jobFinalized: false,
                isBackgroundDispatch: false,
            };

            const boundFlush = (force: boolean = false) => flushAnalysisText(get, assistantMessageId, state, force);
            const boundScheduleFlush = () => scheduleFlush(state, boundFlush);
            const boundStartPolling = (trackingId: string | null | undefined, messageId: string, initialStatus?: ChatActionStatus, initialContent?: string | null) =>
                startActionStatusPolling(get, trackingId, messageId, initialStatus, initialContent);

            const ctx: StreamHandlerContext = {
                get,
                set,
                assistantMessageId,
                mergedMetadata,
                currentSession,
                state,
                startActionStatusPolling: boundStartPolling,
                flushAnalysisText: boundFlush,
                scheduleFlush: boundScheduleFlush,
            };

            for await (const event of streamChatEvents(chatRequest)) {
                if (event.type === 'delta') { handleDelta(ctx, event); continue; }
                if (event.type === 'job_update') { await handleJobUpdate(ctx, event); continue; }
                if (event.type === 'final') { if (handleFinal(ctx, event)) break; continue; }
                if (event.type === 'thinking_step') { handleThinkingStep(ctx, event); continue; }
                if (event.type === 'thinking_delta') { handleThinkingDelta(ctx, event); continue; }
                if (event.type === 'error') throw new Error(event.message || 'Stream error');
            }

            if (state.flushHandle !== null) { window.cancelAnimationFrame(state.flushHandle); state.flushHandle = null; }
            boundFlush(true);

            if (state.isBackgroundDispatch) {
                processBackgroundDispatch(ctx);
                return;
            }

            if (!state.finalPayload && !state.jobFinalized) throw new Error('No final response received');
            if (state.jobFinalized) { set({ isProcessing: false }); return; }
            if (!state.finalPayload) throw new Error('No final response received');

            await processFinalPayload(ctx);
        } catch (error) {
            console.error('Failed to send message:', error);
            set({ isProcessing: false });
            const errorContent = '抱歉，我暂时无法处理你的请求。可能的原因：\n\n1. 后端服务未完全启动\n2. LLM API 未配置\n3. 网络连接问题\n\n请检查后端服务状态，或稍后重试。';
            if (assistantMessageAdded) {
                get().updateMessage(assistantMessageId, { content: errorContent, metadata: { status: 'failed', errors: [error instanceof Error ? error.message : String(error)] } });
            } else {
                get().addMessage({ id: `msg_${Date.now()}_assistant`, type: 'assistant', content: errorContent, timestamp: new Date() });
            }
        }
    },

    retryLastMessage: async () => {
        const { messages, isProcessing } = get();
        if (isProcessing) return;
        const lastFailed = [...messages].reverse().find(msg => msg.type === 'assistant' && (msg.metadata as any)?.status === 'failed' && typeof (msg.metadata as any)?.tracking_id === 'string');
        if (lastFailed) {
            const meta = lastFailed.metadata as any;
            await get().retryActionRun(meta.tracking_id, meta.raw_actions ?? []);
            return;
        }
        const lastUser = [...messages].reverse().find(msg => msg.type === 'user');
        if (lastUser) await get().sendMessage(lastUser.content, lastUser.metadata);
    },

    retryActionRun: async (oldTrackingId, rawActionsOverride = []) => {
        await retryActionRunHelper(get, set, oldTrackingId, rawActionsOverride);
    },
});
