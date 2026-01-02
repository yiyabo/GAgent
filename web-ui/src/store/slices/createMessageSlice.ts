import { ChatSliceCreator } from './types';
import {
    ChatMessage,
    ChatResponseMetadata,
    ChatResponsePayload,
    ChatActionStatus,
    ChatActionSummary,
    Memory,
    ActionStatusResponse,
} from '@/types';
import {
    isActionStatus,
    streamChatEvents,
    mapJobStatusToChatStatus,
    buildActionsFromSteps,
    formatToolPlanPreface,
    summarizeSteps,
    summarizeActions,
    convertRawActionToSummary,
    waitForActionCompletionViaStream,
    resolveHistoryCursor,
    buildToolResultsCache,
    activeActionFollowups,
    autoTitleHistory,
} from '../chatUtils';
import { chatApi } from '@api/chat';
import { memoryApi } from '@api/memory';
import { ENV } from '@/config/env';
import {
    mergeToolResults,
    collectToolResultsFromMetadata,
    collectToolResultsFromSteps,
} from '@utils/toolResults';
import {
    derivePlanSyncEventsFromActions,
    dispatchPlanSyncEvent,
    extractPlanIdFromActions,
    extractPlanTitleFromActions,
    coercePlanId,
    coercePlanTitle,
} from '@utils/planSyncEvents';
import { SessionStorage } from '@/utils/sessionStorage';

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
                    const backendId = typeof msg.id === 'number' ? msg.id : null;
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


            const startActionStatusPolling = (trackingId: string | null | undefined, messageId: string, initialStatus?: ChatActionStatus, initialContent?: string | null) => {
                if (!trackingId) return;
                const pollOnce = async (): Promise<boolean> => {
                    try {
                        const resp = await fetch(`${ENV.API_BASE_URL}/chat/actions/${trackingId}`);
                        if (!resp.ok) return false;
                        const statusResp = (await resp.json()) as ActionStatusResponse;
                        const status = statusResp.status;
                        const done = status === 'completed' || status === 'failed';
                        const remoteToolResults = mergeToolResults(collectToolResultsFromMetadata(statusResp.result?.tool_results), collectToolResultsFromMetadata(statusResp.metadata?.tool_results));
                        const remoteAnalysis = typeof statusResp.result?.analysis_text === 'string' ? statusResp.result.analysis_text : (typeof statusResp.metadata?.analysis_text === 'string' ? statusResp.metadata.analysis_text : undefined);
                        const remoteFinalSummary = typeof statusResp.result?.final_summary === 'string' ? statusResp.result.final_summary : (typeof statusResp.metadata?.final_summary === 'string' ? statusResp.metadata.final_summary : undefined);
                        const remoteReply = typeof statusResp.result?.reply === 'string' ? statusResp.result.reply : undefined;
                        const targetMessage = get().messages.find((msg) => msg.id === messageId);
                        if (!targetMessage) return done;
                        const currentMeta: ChatResponseMetadata = { ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}) };
                        const contentCandidate = (remoteAnalysis && remoteAnalysis.trim()) || (remoteFinalSummary && remoteFinalSummary.trim()) || (remoteReply && remoteReply.trim()) || targetMessage.content || initialContent || '';
                        get().updateMessage(messageId, { content: contentCandidate, metadata: { ...currentMeta, status, analysis_text: remoteAnalysis ?? currentMeta.analysis_text, final_summary: remoteFinalSummary ?? currentMeta.final_summary, tool_results: remoteToolResults.length > 0 ? remoteToolResults : currentMeta.tool_results } });
                        if (done) {
                            const sessionKey = get().currentSession?.session_id ?? get().currentSession?.id ?? null;
                            if (sessionKey) void get().loadChatHistory(sessionKey).catch((e) => console.warn('同步历史失败:', e));
                        }
                        return done;
                    } catch (pollError) {
                        console.warn('补偿轮询动作状态失败:', pollError);
                        return false;
                    }
                };
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

            let streamedContent = '';
            let lastFlushedContent = '';
            let flushHandle: number | null = null;
            let finalPayload: ChatResponsePayload | null = null;
            let jobFinalized = false;

            const flushAnalysisText = (force: boolean = false) => {
                if (!force && streamedContent === lastFlushedContent) return;
                const targetMessage = get().messages.find((msg) => msg.id === assistantMessageId);
                if (!targetMessage) return;
                const existingMetadata: ChatResponseMetadata = { ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}) };
                get().updateMessage(assistantMessageId, { metadata: { ...existingMetadata, analysis_text: streamedContent } });
                lastFlushedContent = streamedContent;
            };

            const scheduleFlush = () => {
                if (flushHandle !== null) return;
                flushHandle = window.requestAnimationFrame(() => { flushHandle = null; flushAnalysisText(); });
            };

            for await (const event of streamChatEvents(chatRequest)) {
                if (event.type === 'delta') {
                    streamedContent += event.content ?? '';
                    scheduleFlush();
                    continue;
                }
                if (event.type === 'job_update') {
                    const payload = event.payload ?? {};
                    const targetMessage = get().messages.find((msg) => msg.id === assistantMessageId);
                    if (!targetMessage) continue;
                    const existingMetadata: ChatResponseMetadata = { ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}) };
                    const jobStatus = mapJobStatusToChatStatus(payload.status);
                    const stepList = Array.isArray(payload.result?.steps) ? (payload.result?.steps as Array<Record<string, any>>) : [];
                    const actionsFromSteps = buildActionsFromSteps(stepList);
                    const mergedToolResults = mergeToolResults(collectToolResultsFromMetadata(existingMetadata.tool_results), mergeToolResults(collectToolResultsFromMetadata(payload.result?.tool_results), collectToolResultsFromSteps(stepList)));
                    const updatedMetadata: ChatResponseMetadata = { ...existingMetadata, status: jobStatus ?? existingMetadata.status };
                    (updatedMetadata as any).unified_stream = true;
                    if (!(updatedMetadata as any).plan_message) {
                        (updatedMetadata as any).plan_message = actionsFromSteps.length > 0 ? formatToolPlanPreface(actionsFromSteps) : targetMessage.content || null;
                    }
                    const analysisFromPayload = typeof payload.result?.analysis_text === 'string' ? payload.result.analysis_text : (typeof payload.metadata?.analysis_text === 'string' ? payload.metadata.analysis_text : null);
                    if (analysisFromPayload?.trim()) updatedMetadata.analysis_text = analysisFromPayload;
                    if (payload.job_id && !updatedMetadata.tracking_id) updatedMetadata.tracking_id = payload.job_id;
                    if (actionsFromSteps.length > 0) { updatedMetadata.actions = actionsFromSteps; updatedMetadata.action_list = actionsFromSteps; }
                    if (mergedToolResults.length > 0) updatedMetadata.tool_results = mergedToolResults;
                    if (payload.error) updatedMetadata.errors = [...(updatedMetadata.errors ?? []), payload.error];
                    get().updateMessage(assistantMessageId, { metadata: updatedMetadata });

                    if (jobFinalized || (payload.status !== 'succeeded' && payload.status !== 'failed' && payload.status !== 'completed')) continue;
                    jobFinalized = true;

                    const normalizedFinalStatus = payload.status === 'completed' ? 'succeeded' : payload.status;
                    const finalActions = actionsFromSteps.length > 0 ? actionsFromSteps : (updatedMetadata.actions ?? []);
                    const finalPlanIdCandidate = coercePlanId(payload.result?.bound_plan_id) ?? extractPlanIdFromActions(finalActions) ?? updatedMetadata.plan_id ?? null;
                    let planTitleFromSteps: string | null | undefined;
                    for (const step of stepList) {
                        const candidate = coercePlanTitle(step?.details?.title) ?? coercePlanTitle(step?.details?.plan_title);
                        if (candidate !== undefined) { planTitleFromSteps = candidate ?? null; break; }
                    }
                    const finalPlanTitle = planTitleFromSteps ?? coercePlanTitle(payload.result?.plan_title) ?? updatedMetadata.plan_title ?? null;

                    // Helper logic for content consolidation
                    let contentCandidate: string | null = (updatedMetadata.analysis_text?.trim() ? updatedMetadata.analysis_text : null);
                    if (!contentCandidate) {
                        const results = payload.result || {};
                        const meta = payload.metadata || {};
                        contentCandidate =
                            (typeof results.final_summary === 'string' ? results.final_summary : null) ||
                            (typeof results.reply === 'string' ? results.reply : null) ||
                            (typeof meta.final_summary === 'string' ? meta.final_summary : null) ||
                            (typeof results.response === 'string' ? results.response : null) ||
                            (typeof results.message === 'string' ? results.message : null) ||
                            (typeof results.text === 'string' ? results.text : null) ||
                            summarizeSteps(stepList) ||
                            null;
                        if (!contentCandidate) {
                            const rawActions = Array.isArray((updatedMetadata as any).raw_actions)
                                ? (updatedMetadata as any).raw_actions.map((act: any) => convertRawActionToSummary(act))
                                : [];
                            contentCandidate = summarizeActions(finalActions.length > 0 ? finalActions : rawActions);
                        }
                    }
                    const fallbackSummary = normalizedFinalStatus === 'succeeded' && mergedToolResults.length > 0 ? '工具已完成，请查看结果。' : targetMessage.content;
                    const contentWithStatus = contentCandidate || (normalizedFinalStatus === 'failed' && updatedMetadata.errors?.length ? `${targetMessage.content}\n\n⚠️ 后台执行失败：${updatedMetadata.errors.join('; ')}` : fallbackSummary);

                    get().updateMessage(assistantMessageId, { content: contentWithStatus, metadata: { ...updatedMetadata, status: normalizedFinalStatus === 'succeeded' ? 'completed' : 'failed', plan_id: finalPlanIdCandidate ?? null, plan_title: finalPlanTitle ?? null, final_summary: (typeof payload.result?.final_summary === 'string' ? payload.result.final_summary : undefined), analysis_text: (updatedMetadata.analysis_text ?? null) } });

                    const tracking = (updatedMetadata.tracking_id ?? payload.job_id) as string | undefined;
                    if (tracking) startActionStatusPolling(tracking, assistantMessageId, normalizedFinalStatus as ChatActionStatus, contentWithStatus);

                    set((state) => {
                        const planIdValue = finalPlanIdCandidate ?? state.currentPlanId ?? null;
                        const planTitleValue = finalPlanTitle ?? state.currentPlanTitle ?? null;
                        const updatedSession = state.currentSession ? { ...state.currentSession, plan_id: planIdValue, plan_title: planTitleValue } : null;
                        return { currentPlanId: planIdValue, currentPlanTitle: planTitleValue, currentSession, sessions: updatedSession ? state.sessions.map(s => s.id === updatedSession.id ? updatedSession : s) : state.sessions };
                    });

                    const sessionAfter = get().currentSession;
                    const eventsToDispatch = derivePlanSyncEventsFromActions(finalActions, { fallbackPlanId: finalPlanIdCandidate ?? sessionAfter?.plan_id ?? null, fallbackPlanTitle: finalPlanTitle ?? sessionAfter?.plan_title ?? null });
                    if (eventsToDispatch.length > 0) {
                        for (const eventDetail of eventsToDispatch) dispatchPlanSyncEvent(eventDetail, { trackingId: updatedMetadata.tracking_id ?? null, source: 'chat.stream', status: normalizedFinalStatus as any, sessionId: sessionAfter?.session_id ?? null });
                    } else if (finalPlanIdCandidate != null) {
                        dispatchPlanSyncEvent({ type: 'task_changed', plan_id: finalPlanIdCandidate, plan_title: finalPlanTitle ?? sessionAfter?.plan_title ?? null }, { trackingId: updatedMetadata.tracking_id ?? null, source: 'chat.stream', status: normalizedFinalStatus as any, sessionId: sessionAfter?.session_id ?? null });
                    }
                    if (sessionAfter) {
                        try { await chatApi.updateSession(sessionAfter.session_id ?? sessionAfter.id, { plan_id: finalPlanIdCandidate ?? null, plan_title: finalPlanTitle ?? null, is_active: normalizedFinalStatus === 'succeeded' }); } catch (patchError) { console.warn('同步会话信息失败:', patchError); }
                        void get().loadChatHistory(sessionAfter.session_id ?? sessionAfter.id).catch((e) => console.warn('同步历史失败:', e));
                    }
                    if (flushHandle !== null) { window.cancelAnimationFrame(flushHandle); flushHandle = null; }
                    flushAnalysisText(true);
                    continue;
                }
                if (event.type === 'final') { finalPayload = event.payload; continue; }
                if (event.type === 'thinking_step') {
                    const step = event.step;
                    const targetMessage = get().messages.find((msg) => msg.id === assistantMessageId);
                    if (!targetMessage) continue;

                    const existingMetadata = { ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}) };
                    const currentProcess = targetMessage.thinking_process ?? {
                        steps: [],
                        status: 'active',
                        total_iterations: 0
                    };

                    const updatedSteps = [...currentProcess.steps];
                    // Check if we updating existing step or adding new one
                    const stepIndex = updatedSteps.findIndex(s => s.iteration === step.iteration);
                    if (stepIndex >= 0) {
                        updatedSteps[stepIndex] = step;
                    } else {
                        updatedSteps.push(step);
                    }

                    const updatedProcess = {
                        ...currentProcess,
                        steps: updatedSteps,
                        total_iterations: Math.max(currentProcess.total_iterations ?? 0, step.iteration),
                        // Update status based on last step
                        status: step.status === 'done' ? 'completed' : (step.status === 'error' ? 'error' : 'active') as 'active' | 'completed' | 'error'
                    };

                    get().updateMessage(assistantMessageId, {
                        metadata: existingMetadata,
                        thinking_process: updatedProcess
                    });

                    // Allow UI to re-render
                    scheduleFlush();
                    continue;
                }
                if (event.type === 'error') throw new Error(event.message || 'Stream error');
            }

            if (flushHandle !== null) { window.cancelAnimationFrame(flushHandle); flushHandle = null; }
            flushAnalysisText(true);

            if (!finalPayload && !jobFinalized) throw new Error('No final response received');
            if (jobFinalized) { set({ isProcessing: false }); return; }
            if (!finalPayload) throw new Error('No final response received');

            const result: ChatResponsePayload = finalPayload;
            const actions = (result.actions ?? []) as ChatActionSummary[];
            const resolvedPlanId = (result.metadata?.plan_id !== undefined ? coercePlanId(result.metadata.plan_id) : undefined) ?? extractPlanIdFromActions(actions) ?? coercePlanId(mergedMetadata.plan_id) ?? get().currentPlanId ?? null;
            const resolvedPlanTitle = (result.metadata?.plan_title !== undefined ? coercePlanTitle(result.metadata.plan_title) : undefined) ?? extractPlanTitleFromActions(actions) ?? coercePlanTitle(mergedMetadata.plan_title) ?? get().currentPlanTitle ?? null;
            const resolvedTaskId = result.metadata?.task_id ?? mergedMetadata.task_id ?? get().currentTaskId ?? null;
            const resolvedTaskName = mergedMetadata.task_name ?? get().currentTaskName ?? null;
            const resolvedWorkflowId = result.metadata?.workflow_id ?? mergedMetadata.workflow_id ?? get().currentWorkflowId ?? null;
            const initialStatus = isActionStatus(result.metadata?.status) ? (result.metadata?.status as ChatActionStatus) : (actions.length > 0 ? 'pending' : 'completed');

            const assistantMetadata: ChatResponseMetadata = {
                ...(result.metadata ?? {}),
                plan_id: resolvedPlanId ?? null,
                plan_title: resolvedPlanTitle ?? null,
                task_id: resolvedTaskId ?? null,
                workflow_id: resolvedWorkflowId ?? null,
                actions,
                action_list: actions,
                status: initialStatus,
                analysis_text: result.metadata?.analysis_text !== undefined ? (result.metadata?.analysis_text as string | null) : streamedContent || '',
                final_summary: (result.metadata?.final_summary as string | undefined) ?? (result.response ?? streamedContent ?? ''),
            };
            if (initialStatus === 'pending' || initialStatus === 'running') {
                (assistantMetadata as any).unified_stream = true;
                (assistantMetadata as any).plan_message = formatToolPlanPreface(actions);
            }
            const initialToolResults = collectToolResultsFromMetadata(result.metadata?.tool_results);
            if (initialToolResults.length > 0) assistantMetadata.tool_results = initialToolResults;

            get().updateMessage(assistantMessageId, {
                content: (assistantMetadata as any).unified_stream === true ? (assistantMetadata.analysis_text?.trim() ? assistantMetadata.analysis_text : (((assistantMetadata as any).plan_message as string) || assistantMetadata.final_summary || '')) : (result.response ?? streamedContent),
                metadata: assistantMetadata,
            });
            set({ isProcessing: false });

            const trackingIdForPoll = typeof assistantMetadata.tracking_id === 'string' ? assistantMetadata.tracking_id : null;
            if ((assistantMetadata as any).unified_stream === true && trackingIdForPoll) {
                startActionStatusPolling(trackingIdForPoll, assistantMessageId, assistantMetadata.status as ChatActionStatus, (assistantMetadata.analysis_text as string | undefined) ?? (assistantMetadata.final_summary as string | undefined) ?? null);
            }

            set((state) => {
                const planIdValue = resolvedPlanId ?? state.currentPlanId ?? null;
                const planTitleValue = resolvedPlanTitle ?? state.currentPlanTitle ?? null;
                const taskIdValue = resolvedTaskId ?? state.currentTaskId ?? null;
                const workflowValue = resolvedWorkflowId ?? state.currentWorkflowId ?? null;
                const updatedSession = state.currentSession ? { ...state.currentSession, plan_id: planIdValue, plan_title: planTitleValue, current_task_id: taskIdValue, current_task_name: resolvedTaskName ?? state.currentSession.current_task_name ?? null, workflow_id: workflowValue } : null;
                return { currentPlanId: planIdValue, currentPlanTitle: planTitleValue, currentTaskId: taskIdValue, currentTaskName: resolvedTaskName ?? state.currentTaskName ?? null, currentWorkflowId: workflowValue, currentSession, sessions: updatedSession ? state.sessions.map(s => s.id === updatedSession.id ? updatedSession : s) : state.sessions };
            });

            const sessionAfter = get().currentSession;
            if (sessionAfter) {
                const sessionKey = sessionAfter.session_id ?? sessionAfter.id;
                if (sessionKey && sessionAfter.isUserNamed !== true) {
                    const history = autoTitleHistory.get(sessionKey);
                    if (!history || history.planId !== sessionAfter.plan_id) {
                        if (sessionAfter.plan_id !== null || sessionAfter.messages.some(m => m.type === 'user')) {
                            void get().autotitleSession(sessionKey).catch(e => console.warn('自动命名失败:', e));
                        }
                    }
                }
            }

            if (resolvedWorkflowId !== get().currentWorkflowId) get().setCurrentWorkflowId(resolvedWorkflowId ?? null);
            if (assistantMetadata.agent_workflow) {
                window.dispatchEvent(new CustomEvent('tasksUpdated', { detail: { type: 'agent_workflow_created', workflow_id: assistantMetadata.workflow_id, total_tasks: assistantMetadata.total_tasks, dag_structure: assistantMetadata.dag_structure, plan_id: resolvedPlanId ?? null } }));
            }
            if (assistantMetadata.session_id) {
                const newSessionId = assistantMetadata.session_id as string;
                set((state) => {
                    const current = state.currentSession ? { ...state.currentSession, session_id: newSessionId } : null;
                    return { currentSession: current, sessions: state.sessions.map(s => s.id === current?.id ? { ...s, session_id: newSessionId } : s) };
                });
                SessionStorage.setCurrentSessionId(newSessionId);
            }

            const trackingId = typeof assistantMetadata.tracking_id === 'string' ? assistantMetadata.tracking_id : undefined;
            if (trackingId && (assistantMetadata.status === 'pending' || assistantMetadata.status === 'running') && !activeActionFollowups.has(trackingId)) {
                activeActionFollowups.add(trackingId);
                void (async () => {
                    try {
                        const timeoutMs = 10 * 60_000;
                        let lastStatus: ActionStatusResponse | null = await waitForActionCompletionViaStream(trackingId, timeoutMs);
                        if (!lastStatus) {
                            const start = Date.now();
                            while (Date.now() - start < timeoutMs) {
                                try {
                                    const status = await chatApi.getActionStatus(trackingId);
                                    lastStatus = status;
                                    const target = get().messages.find(m => m.id === assistantMessageId);
                                    if (target) {
                                        const meta = (target.metadata as ChatResponseMetadata) ?? {};
                                        if (status.status !== meta.status) get().updateMessage(assistantMessageId, { metadata: { ...meta, status: status.status as ChatActionStatus, tracking_id: trackingId, unified_stream: true } as any });
                                    }
                                    if (status.status === 'completed' || status.status === 'failed') break;
                                } catch (e) { console.warn('轮询失败:', e); break; }
                                await new Promise(r => setTimeout(r, 2500));
                            }
                        }
                        if (lastStatus) {
                            const target = get().messages.find(m => m.id === assistantMessageId);
                            if (target) {
                                const meta = (target.metadata as ChatResponseMetadata) ?? {};
                                const mergedResults = mergeToolResults(collectToolResultsFromMetadata((lastStatus.result as any)?.tool_results), collectToolResultsFromMetadata((lastStatus.metadata as any)?.tool_results));
                                const analysis = (typeof (lastStatus.result as any)?.analysis_text === 'string' ? (lastStatus.result as any).analysis_text : null)?.trim();
                                const summary = (typeof (lastStatus.result as any)?.final_summary === 'string' ? (lastStatus.result as any).final_summary : (typeof (lastStatus.metadata as any)?.final_summary === 'string' ? (lastStatus.metadata as any).final_summary : null))?.trim();
                                const completionContent = analysis ?? summary ?? (lastStatus.status === 'completed' ? '工具已完成，请查看结果。' : '执行失败，请查看错误信息。');
                                const nextMeta: any = { ...meta, status: lastStatus.status as ChatActionStatus, tracking_id: lastStatus.tracking_id ?? trackingId, plan_id: typeof lastStatus.plan_id === 'number' ? lastStatus.plan_id : (meta.plan_id ?? null), actions: Array.isArray(lastStatus.actions) ? lastStatus.actions : meta.actions, action_list: Array.isArray(lastStatus.actions) ? lastStatus.actions : meta.action_list, errors: Array.isArray(lastStatus.errors) ? lastStatus.errors : meta.errors, unified_stream: true };
                                if (analysis) nextMeta.analysis_text = analysis;
                                if (summary) nextMeta.final_summary = summary;
                                if (mergedResults.length > 0) nextMeta.tool_results = mergedResults; else delete nextMeta.tool_results;
                                get().updateMessage(assistantMessageId, { content: completionContent, metadata: nextMeta });
                                const sessionKey = get().currentSession?.session_id ?? get().currentSession?.id ?? null;
                                if (sessionKey) void get().loadChatHistory(sessionKey).catch(e => console.warn('同步失败:', e));
                            }
                        }
                    } finally { activeActionFollowups.delete(trackingId); }
                })();
            }

            if (!trackingId) {
                const planEvents = derivePlanSyncEventsFromActions(result.actions, { fallbackPlanId: resolvedPlanId ?? get().currentPlanId ?? null, fallbackPlanTitle: resolvedPlanTitle ?? get().currentPlanTitle ?? null });
                if (planEvents.length > 0) {
                    const sessionForEvent = get().currentSession;
                    for (const ev of planEvents) dispatchPlanSyncEvent(ev, { source: 'chat.sync', sessionId: sessionForEvent?.session_id ?? null });
                }
            }

            try {
                const { currentSession: cs, currentWorkflowId: cw, currentPlanId: pid } = get();
                window.dispatchEvent(new CustomEvent('tasksUpdated', { detail: { type: 'chat_message_processed', session_id: cs?.session_id ?? null, workflow_id: cw ?? null, plan_id: resolvedPlanId ?? pid ?? null } }));
            } catch (e) { console.warn('Failed to dispatch tasksUpdated:', e); }

            const sessionPatch = get().currentSession;
            if (!assistantMetadata.tracking_id && sessionPatch) {
                void (async () => {
                    try { await chatApi.updateSession(sessionPatch.session_id ?? sessionPatch.id, { plan_id: resolvedPlanId ?? null, plan_title: resolvedPlanTitle ?? null, current_task_id: resolvedTaskId ?? null, current_task_name: resolvedTaskName ?? null, is_active: true }); } catch (e) { console.warn('同步失败:', e); }
                })();
            }
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
        const { isProcessing } = get();
        if (!oldTrackingId || isProcessing) return;
        try {
            set({ isProcessing: true });
            const retryStatus = await chatApi.retryActionRun(oldTrackingId);
            const newTrackingId = retryStatus.tracking_id;
            const rawActions = Array.isArray(retryStatus.actions) ? retryStatus.actions.map((a: any, idx: number) => ({ kind: a.kind, name: a.name, parameters: a.parameters, order: a.order ?? idx + 1, blocking: a.blocking ?? true })) : rawActionsOverride;
            const pendingId = `msg_${Date.now()}_assistant_retry`;
            get().addMessage({ id: pendingId, type: 'assistant', content: '正在重新执行该动作…', timestamp: new Date(), metadata: { status: 'pending', unified_stream: true, plan_message: '正在重新执行该动作…', tracking_id: newTrackingId, plan_id: retryStatus.plan_id ?? null, raw_actions: rawActions, retry_of: oldTrackingId } });
            const lastStatus = await waitForActionCompletionViaStream(newTrackingId, 120_000) || await (async () => {
                const start = Date.now();
                while (Date.now() - start < 120_000) {
                    try {
                        const s = await chatApi.getActionStatus(newTrackingId);
                        if (s.status === 'completed' || s.status === 'failed') return s;
                    } catch (e) { break; }
                    await new Promise(r => setTimeout(r, 2500));
                }
                return null;
            })();
            if (lastStatus) {
                const summary = (typeof lastStatus.result?.final_summary === 'string' ? lastStatus.result.final_summary : (typeof lastStatus.metadata?.final_summary === 'string' ? lastStatus.metadata.final_summary : null))?.trim();
                const content = summary ?? (lastStatus.status === 'completed' ? '工具已完成，但未生成最终总结，请查看工具结果。' : '执行失败，请查看错误信息。');
                const results = mergeToolResults(collectToolResultsFromMetadata(lastStatus.result?.tool_results), collectToolResultsFromMetadata(lastStatus.metadata?.tool_results));
                get().updateMessage(pendingId, { content, metadata: { status: lastStatus.status as ChatActionStatus, unified_stream: true, actions: lastStatus.actions ?? [], tool_results: results.length > 0 ? results : undefined, errors: lastStatus.errors ?? undefined } as any });
            }
        } catch (error) {
            console.error('Retry failed:', error);
            const lastUser = [...get().messages].reverse().find(msg => msg.type === 'user');
            if (lastUser) await get().sendMessage(lastUser.content, lastUser.metadata);
        } finally { set({ isProcessing: false }); }
    },
});
