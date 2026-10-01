import { ChatSliceCreator } from '../types';
import {
  ChatMessage,
  ChatActionStatus,
  Memory,
} from '@/types';
import {
  streamChatEvents,
  streamRunEvents,
  postChatRun,
  buildToolResultsCache,
  resolveHistoryCursor,
} from '../../chatUtils';
import { memoryApi } from '@api/memory';
import { chatApi } from '@api/chat';
import {
  collectArtifactGallery,
  mergeArtifactGalleries,
} from '@/utils/artifactGallery';
import { resolveChatSessionProcessingKey } from '@/utils/chatSessionKeys';
import { recoverPlanBindingFromMessages } from './planRecovery';
import { hydratePersistedMessage } from './historyHydration';
import { matchesResumeSession, selectActiveChatRun } from './runResume';
import { createSessionAccess, findSession, matchesSession, sessionMessages, sessionPatch } from './sessionState';
import { resolveRequestFailureMessage } from '@/components/chat/message/utils';
import { recoverAfterStreamFailure } from './recovery';
import { mergeUnpersistedTurns } from './turnCorrelation';

/** Recent turns attached to each API request. Align with backend `CHAT_HISTORY_MAX_MESSAGES` (default 80, cap 200). */
const CHAT_REQUEST_HISTORY_LIMIT = 80;
import { startActionStatusPolling, flushAnalysisText, scheduleFlush, retryActionRun as retryActionRunHelper } from './helpers';
import {
  handleDelta,
  handleJobUpdate,
  handleFinal,
  handleThinkingStep,
  handleThinkingDelta,
  handleReasoningDelta,
  handleProgressStatus,
  flushPendingThinkingDeltas,
  handleControlAck,
  handleToolOutput,
  processBackgroundDispatch,
  processFinalPayload,
} from './streamHandlers';
import type { StreamMutableState, StreamHandlerContext } from './types';
import type { ChatStreamEvent } from '../../chatUtils';


const _looksLikeSubstantialAssistant = (content: unknown): boolean => {
  const text = typeof content === 'string' ? content.trim() : '';
  if (text.length < 40) return false;
  const lower = text.toLowerCase();
  if (lower.includes('backend returned a server error')) return false;
  if (lower.includes('request failed. please check')) return false;
  if (lower.includes('reconnect failed')) return false;
  if (lower.includes('对话已中断')) return false;
  if (lower.includes('连接中断')) return false;
  return true;
};

/**
 * After SSE/stream drop: backend may still be running or already saved the reply.
 * Prefer resume active run, then poll history — only fail if both miss.
 */
const _recoverAfterStreamFailure = async (
  get: any,
  opts: {
    apiSessionId: string;
    localSessionId: string;
    assistantMessageId: string;
    processingKey: string;
    runId?: string | null;
    clientMessageId?: string | null;
    partialContent?: string;
    set: any;
    currentSession: any;
  },
): Promise<boolean> => {
  const source = createSessionAccess(get, opts.set, opts.localSessionId);
  return recoverAfterStreamFailure({
    ...opts,
    get,
    resumeRun: async (runId) => {
      const target = source.get().messages.find((message: ChatMessage) => message.id === opts.assistantMessageId);
      const metadata = { ...(target?.metadata ?? {}), status: 'pending', chat_run_id: runId, recovering: true };
      // Replay starts at -1, so its accumulated text/thinking must start empty.
      source.get().updateMessage(opts.assistantMessageId, {
        thinking_process: undefined,
        metadata: { ...metadata, analysis_text: '', deep_think_progress: undefined, thinking_process: undefined, thinking_display_mode: undefined },
      });
      const state: StreamMutableState = {
        streamedContent: '', lastFlushedContent: '', flushHandle: null,
        thinkingDeltaFlushHandle: null, pendingThinkingDeltas: {},
        pendingThinkingDeltaStartedAt: {}, finalPayload: null,
        jobFinalized: false, isBackgroundDispatch: false,
      };
      const boundFlush = (force = false) => flushAnalysisText(source.get, opts.assistantMessageId, state, force);
      const ctx: StreamHandlerContext = {
        get: source.get, set: source.set, assistantMessageId: opts.assistantMessageId,
        mergedMetadata: metadata, currentSession: opts.currentSession, state,
        sourceSessionScoped: true, isCurrentSession: source.isCurrent,
        startActionStatusPolling: (trackingId, messageId, status, content) =>
          startActionStatusPolling(source.get, trackingId, messageId, status, content),
        flushAnalysisText: boundFlush,
        scheduleFlush: () => scheduleFlush(state, boundFlush),
      };
      await _consumeUnifiedStream(ctx, streamRunEvents(opts.apiSessionId, runId, -1));
      await _finalizeAfterUnifiedStream(ctx, state, boundFlush);
    },
  });
};

const _consumeUnifiedStream = async (
  ctx: StreamHandlerContext,
  source: AsyncIterable<{ seq: number | null; event: ChatStreamEvent }>
): Promise<void> => {
  try {
  for await (const { event } of source) {
    if (event.type === 'start') {
      continue;
    }
    if (event.type === 'delta') {
      handleDelta(ctx, event);
      continue;
    }
    if (event.type === 'job_update') {
      await handleJobUpdate(ctx, event);
      continue;
    }
    if (event.type === 'final') {
      flushPendingThinkingDeltas(ctx);
      if (handleFinal(ctx, event)) {
        break;
      }
      continue;
    }
    if (event.type === 'thinking_step') {
      handleThinkingStep(ctx, event);
      continue;
    }
    if (event.type === 'thinking_delta') {
      handleThinkingDelta(ctx, event);
      continue;
    }
    if (event.type === 'reasoning_delta') {
      handleReasoningDelta(ctx, event);
      continue;
    }
    if (event.type === 'progress_status') {
      handleProgressStatus(ctx, event);
      continue;
    }
    if (event.type === 'control_ack') {
      handleControlAck(ctx, event);
      continue;
    }
    if (event.type === 'tool_output') {
      handleToolOutput(ctx, event);
      continue;
    }
    if (event.type === 'artifact') {
      const targetMessage = ctx.get().messages.find((msg: any) => msg.id === ctx.assistantMessageId);
      if (targetMessage) {
        const existingMetadata = { ...((targetMessage.metadata as Record<string, any> | undefined) ?? {}) };
        const mergedGallery = mergeArtifactGalleries(
          collectArtifactGallery(existingMetadata.artifact_gallery),
          collectArtifactGallery([event]),
        );
        if (mergedGallery.length > 0) {
          existingMetadata.artifact_gallery = mergedGallery;
          ctx.get().updateMessage(ctx.assistantMessageId, { metadata: existingMetadata });
        }
      }
      continue;
    }
    if (event.type === 'steer_ack') {
      continue;
    }
    if (event.type === 'error') {
      throw new Error((event as { message?: string }).message || 'Stream error');
    }
  }
  } catch (error) {
    // A failed consumer leaves no frame/timer that can write old deltas into
    // the same message after an authoritative replay has restarted it.
    flushPendingThinkingDeltas(ctx);
    ctx.flushAnalysisText(true);
    if (ctx.state.flushHandle !== null) {
      window.cancelAnimationFrame(ctx.state.flushHandle);
      ctx.state.flushHandle = null;
    }
    throw error;
  }
};

const _finalizeAfterUnifiedStream = async (
  ctx: StreamHandlerContext,
  state: StreamMutableState,
  boundFlush: (force?: boolean) => void
): Promise<void> => {
  if (state.flushHandle !== null) {
    window.cancelAnimationFrame(state.flushHandle);
    state.flushHandle = null;
  }
  if (state.thinkingDeltaFlushHandle !== null) {
    window.clearTimeout(state.thinkingDeltaFlushHandle);
    state.thinkingDeltaFlushHandle = null;
  }
  flushPendingThinkingDeltas(ctx);
  boundFlush(true);

  if (state.isBackgroundDispatch) {
    processBackgroundDispatch(ctx);
    return;
  }

  if (!state.finalPayload && !state.jobFinalized) {
    throw new Error('No final response received');
  }
  if (state.jobFinalized) {
    ctx.get().setActiveRunId(resolveChatSessionProcessingKey(ctx.currentSession), null);
    ctx.get().setSessionProcessing(
      resolveChatSessionProcessingKey(ctx.currentSession),
      false
    );
    return;
  }
  if (!state.finalPayload) {
    throw new Error('No final response received');
  }

  await processFinalPayload(ctx);
};

export const createMessageSlice: ChatSliceCreator = (set, get) => {
  const historyRequests = new Map<string, symbol>();
  return ({
  messages: [],
  historyHasMore: false,
  historyBeforeId: null,
  historyLoading: false,
  historyPageSize: 100,

  addMessage: (message, sessionId) => set((state) => {
    const session = findSession(state, sessionId);
    if (sessionId && !session) return {};
    const messages = [...sessionMessages(state, sessionId), message];
    return session
      ? sessionPatch(state, { ...session, messages, updated_at: new Date() })
      : { messages };
  }),

  updateMessage: (messageId, updates, sessionId) => set((state) => {
    const session = findSession(state, sessionId);
    if (sessionId && !session) return {};
    const previous = sessionMessages(state, sessionId);
    if (!previous.some((message) => message.id === messageId)) return {};
    const messages = previous.map((message) => message.id === messageId ? { ...message, ...updates } : message);
    return session
      ? sessionPatch(state, { ...session, messages, updated_at: new Date() })
      : { messages };
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
    const sourceId = findSession(get(), sessionId)?.id ?? sessionId;
    const isCurrent = () => matchesSession(get().currentSession, sourceId);
    if (append && beforeId == null) {
      if (isCurrent()) set({ historyHasMore: false });
      return;
    }
    if (append && isCurrent() && get().historyLoading) return;
    const request = Symbol(sourceId);
    historyRequests.set(sourceId, request);
    const isLatest = () => historyRequests.get(sourceId) === request;
    const limit = pageSize ?? get().historyPageSize ?? 50;
    if (isCurrent()) set({ historyLoading: true });
    try {
      let data: any;
      try {
        const response = await chatApi.getHistory(sessionId, {
          limit, ...(beforeId == null ? {} : { before_id: beforeId }),
        });
        data = response.data;
      } catch (error: any) {
        const target = findSession(get(), sourceId);
        if (error?.response?.status === 404 && target?.titleSource === 'local' && target.messages.length === 0) {
          if (isLatest() && isCurrent()) set({ messages: [], historyBeforeId: null, historyHasMore: false });
          return;
        }
        throw error;
      }
      if (!isLatest()) return;
      const existing = sessionMessages(get(), sourceId);
      const toolResults = buildToolResultsCache(existing);
      const persisted: ChatMessage[] = data.success && Array.isArray(data.messages)
        ? data.messages.map((rawMessage: any, index: number) => hydratePersistedMessage({
          sessionId, rawMessage, index, fallbackToolResults: toolResults,
        })) : [];
      const merged = append ? [...persisted, ...existing] : mergeUnpersistedTurns(persisted, existing);
      const seen = new Set<string>();
      const messages: ChatMessage[] = merged.filter((message) => {
        if (seen.has(message.id)) return false;
        seen.add(message.id);
        return true;
      });
      const target = findSession(get(), sourceId);
      if (!target) return;
      const recoveredPlanBinding = target.plan_id == null ? recoverPlanBindingFromMessages(messages) : null;
      set((state) => {
        const currentTarget = findSession(state, sourceId);
        if (!currentTarget || !isLatest()) return {};
        const updated = {
          ...currentTarget, messages, updated_at: new Date(),
          last_message_at: messages[messages.length - 1]?.timestamp ?? currentTarget.last_message_at ?? null,
          ...(currentTarget.plan_id == null && recoveredPlanBinding?.planId != null ? {
            plan_id: recoveredPlanBinding.planId,
            plan_title: recoveredPlanBinding.planTitle ?? currentTarget.plan_title ?? null,
          } : {}),
        };
        const active = matchesSession(state.currentSession, sourceId);
        return {
          ...sessionPatch(state, updated),
          ...(active ? {
            currentPlanId: updated.plan_id ?? state.currentPlanId,
            currentPlanTitle: updated.plan_title ?? state.currentPlanTitle,
            historyBeforeId: typeof data.next_before_id === 'number' ? data.next_before_id : resolveHistoryCursor(persisted),
            historyHasMore: typeof data.has_more === 'boolean' ? data.has_more : persisted.length >= limit,
          } : {}),
        };
      });
      if (recoveredPlanBinding?.planId != null) {
        void chatApi.updateSession(target.session_id ?? target.id, {
          plan_id: recoveredPlanBinding.planId, plan_title: recoveredPlanBinding.planTitle ?? null,
        }).catch((error) => console.warn('Failed to persist recovered plan binding:', error));
      }
      if (!append && isCurrent()) void get().resumeActiveChatRunIfAny(sourceId);
    } catch (error) {
      console.error('load history failed:', error);
      throw error;
    } finally {
      if (isLatest()) {
        historyRequests.delete(sourceId);
        if (isCurrent()) set({ historyLoading: false });
      }
    }
  },

  // Single-source pagination: fetch the next older page into the same store
  // list (replaces the removed React Query useMessages side channel).
  loadMoreHistory: async () => {
  const { currentSession, historyBeforeId, historyHasMore, historyLoading } = get();
  if (!historyHasMore || historyLoading || historyBeforeId === null || historyBeforeId === undefined) {
  return;
  }
  const sessionId = currentSession?.session_id ?? currentSession?.id;
  if (!sessionId) {
  return;
  }
  await get().loadChatHistory(sessionId, { beforeId: historyBeforeId, append: true });
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
  uploadedFiles,
  processingSessionIds,
  } = get();

  const source = createSessionAccess(get, set, currentSession?.id);
  const processingKey = resolveChatSessionProcessingKey(currentSession);
  if (processingSessionIds.has(processingKey)) {
  return;
  }

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
  const clientMessageId = `client_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;

  const userMessage: ChatMessage = {
  id: `msg_${clientMessageId}_user`,
  type: 'user',
  content,
  timestamp: new Date(),
  metadata: {
  ...mergedMetadata,
  client_message_id: clientMessageId,
  },
  };
  source.get().addMessage(userMessage);

  // Optimistic Assistant Message
  const assistantMessageId = `msg_${clientMessageId}_assistant`;
  const assistantMessage: ChatMessage = {
  id: assistantMessageId,
  type: 'assistant',
  content: '',
  timestamp: new Date(),
  metadata: { status: 'pending', unified_stream: true, plan_message: null, client_message_id: clientMessageId },
  };
  source.get().addMessage(assistantMessage);
  let assistantMessageAdded = true;

  set({ inputText: '' });
  source.get().setSessionProcessing(processingKey, true);

  try {
  let memories: Memory[] = [];
  if (memoryEnabled) {
  try {
  const memoryResult = await memoryApi.queryMemory({ search_text: content, limit: 3, min_similarity: 0.6 });
  memories = memoryResult.memories || [];
  if (source.isCurrent()) set({ relevantMemories: memories });
  } catch (error) {
  console.error('Memory RAG failed:', error);
  }
  }

  const recentMessages = source.get().messages.slice(-CHAT_REQUEST_HISTORY_LIMIT).map((msg) => ({
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
  client_message_id: clientMessageId,
  context: {
  plan_id: mergedMetadata.plan_id,
  task_id: mergedMetadata.task_id,
  plan_title: mergedMetadata.plan_title,
  workflow_id: mergedMetadata.workflow_id,
  attachments,
  memories: memoryContext,
  ...(metadata ?? {}),
  },
  };

  const state: StreamMutableState = {
  streamedContent: '',
  lastFlushedContent: '',
  flushHandle: null,
  thinkingDeltaFlushHandle: null,
  pendingThinkingDeltas: {},
  pendingThinkingDeltaStartedAt: {},
  finalPayload: null,
  jobFinalized: false,
  isBackgroundDispatch: false,
  };

  const boundFlush = (force: boolean = false) => flushAnalysisText(source.get, assistantMessageId, state, force);
  const boundScheduleFlush = () => scheduleFlush(state, boundFlush);
  const boundStartPolling = (trackingId: string | null | undefined, messageId: string, initialStatus?: ChatActionStatus, initialContent?: string | null) =>
  startActionStatusPolling(source.get, trackingId, messageId, initialStatus, initialContent);

  const ctx: StreamHandlerContext = {
  get: source.get,
  set: source.set,
  sourceSessionScoped: true,
  isCurrentSession: source.isCurrent,
  assistantMessageId,
  mergedMetadata,
  currentSession,
  state,
  startActionStatusPolling: boundStartPolling,
  flushAnalysisText: boundFlush,
  scheduleFlush: boundScheduleFlush,
  };

  const apiSessionId = currentSession?.session_id ?? currentSession?.id ?? undefined;
  let eventSource: AsyncIterable<{ seq: number | null; event: ChatStreamEvent }>;
  if (apiSessionId) {
  const { run_id } = await postChatRun(chatRequest);
  source.get().setActiveRunId(processingKey, run_id);
  const prevMeta =
  (source.get().messages.find((m) => m.id === assistantMessageId)?.metadata ?? {}) as Record<string, any>;
  source.get().updateMessage(assistantMessageId, {
  metadata: {
  ...prevMeta,
  chat_run_id: run_id,
  unified_stream: true,
  status: 'pending',
  },
  });
  eventSource = streamRunEvents(apiSessionId, run_id, -1);
  } else {
  eventSource = (async function* () {
  for await (const event of streamChatEvents(chatRequest)) {
  yield { seq: null, event };
  }
  })();
  }

  await _consumeUnifiedStream(ctx, eventSource);
  await _finalizeAfterUnifiedStream(ctx, state, boundFlush);
  // Keep composer chips in sync with server uploads (disk truth).
  // User can ✕ to delete; otherwise same files remain attached for follow-ups.
  try {
    await source.get().syncUploadedFilesFromServer();
  } catch {
    /* keep current chips */
  }
  } catch (error) {
  console.error('Failed to send message:', error);
  const apiSid = currentSession?.session_id ?? currentSession?.id ?? undefined;
  const localSid = currentSession?.id ?? apiSid;
  const runIdForRecovery =
    (source.get().activeRunIds.get(processingKey) as string | undefined) ||
    ((source.get().messages.find((m) => m.id === assistantMessageId)?.metadata as any)?.chat_run_id as string | undefined) ||
    null;
  const partialContent =
    (source.get().messages.find((m) => m.id === assistantMessageId)?.content as string | undefined) || '';
  let recovered = false;
  if (assistantMessageAdded && apiSid && localSid) {
    try {
      recovered = await _recoverAfterStreamFailure(get, {
        apiSessionId: String(apiSid),
        localSessionId: String(localSid),
        assistantMessageId,
        processingKey,
        runId: runIdForRecovery,
        clientMessageId,
        partialContent,
        set,
        currentSession,
      });
    } catch (recoverErr) {
      console.warn('[chat] recoverAfterStreamFailure threw:', recoverErr);
      recovered = false;
    }
  }
  if (recovered) {
    return;
  }
  source.get().setActiveRunId(processingKey, null);
  source.get().setSessionProcessing(processingKey, false);
  const errorContent =
    resolveRequestFailureMessage(error) +
    '\n\n若任务较长，服务端可能仍在继续或已完成：请刷新页面查看最新结果，或稍后重试。';
  if (assistantMessageAdded) {
    const prev =
      (source.get().messages.find((m) => m.id === assistantMessageId)?.metadata ?? {}) as Record<string, any>;
    const keepPartial = _looksLikeSubstantialAssistant(partialContent);
    source.get().updateMessage(assistantMessageId, {
      content: keepPartial ? `${partialContent}\n\n---\n${errorContent}` : errorContent,
      metadata: {
        ...prev,
        status: 'failed',
        recovering: false,
        errors: [error instanceof Error ? error.message : String(error)],
      },
    });
  } else {
    source.get().addMessage({
      id: `msg_${Date.now()}_assistant`,
      type: 'assistant',
      content: errorContent,
      timestamp: new Date(),
      metadata: { status: 'failed' },
    });
  }
  }
  },

  resumeActiveChatRunIfAny: async (sessionId: string) => {
  const { currentSession } = get();
  const source = createSessionAccess(get, set, currentSession?.id);
  if (!matchesResumeSession(currentSession, sessionId)) {
  return;
  }
  const resumeKey = resolveChatSessionProcessingKey(currentSession);
  const apiSid = currentSession?.session_id ?? currentSession?.id;
  if (!apiSid) {
  return;
  }
  let res: any;
  try {
  const response = await chatApi.getActiveRun(apiSid);
  res = response.data;
  } catch {
  return;
  }
  if (!matchesResumeSession(get().currentSession, sessionId)) return;
  if (!res) {
  return;
  }
  const expectedRunId = source.get().activeRunIds.get(resumeKey) ?? null;
  const run = selectActiveChatRun(res, expectedRunId);
  if (!run?.run_id) {
  // Server has no active run; client may still show "running" after backend restart.
  if (source.get().processingSessionIds.has(resumeKey)) {
  source.get().setActiveRunId(resumeKey, null);
  source.get().setSessionProcessing(resumeKey, false);
  const msgs = source.get().messages;
  const last = msgs.length > 0 ? msgs[msgs.length - 1] : null;
  if (last?.type === 'assistant' && (last.metadata as any)?.status === 'pending') {
  source.get().updateMessage(last.id, {
  content:
  (last.content && String(last.content).trim())
  ? last.content
  : '对话已中断（服务端已重启或无进行中的任务）。请重新发送消息。',
  metadata: {
  ...(last.metadata as any),
  status: 'failed',
  errors: ['No active run on server (e.g. server restarted).'],
  },
  });
  }
  }
  return;
  }
  const runId = run.run_id as string;
  if (source.get().processingSessionIds.has(resumeKey)) {
  const activeId = source.get().activeRunIds.get(resumeKey);
  if (activeId === runId) {
  return;
  }
  }
  const messages = source.get().messages as ChatMessage[];
  const lastUser = [...messages].reverse().find((message) => message.type === 'user');
  const last = messages[messages.length - 1];
  let assistantMessageId: string;
  if (
  last?.type === 'assistant' &&
  (last.metadata as any)?.chat_run_id === runId &&
  (last.metadata as any)?.status === 'pending'
  ) {
  assistantMessageId = last.id;
  } else {
  assistantMessageId = `msg_${Date.now()}_assistant_resume`;
  source.get().addMessage({
  id: assistantMessageId,
  type: 'assistant',
  content: '',
  timestamp: new Date(),
  metadata: {
  status: 'pending',
  unified_stream: true,
  chat_run_id: runId,
  plan_message: null,
  client_message_id: lastUser?.metadata?.client_message_id,
  },
  });
  }

  source.get().setActiveRunId(resumeKey, runId);
  source.get().setSessionProcessing(resumeKey, true);

  const state: StreamMutableState = {
  streamedContent: '',
  lastFlushedContent: '',
  flushHandle: null,
  thinkingDeltaFlushHandle: null,
  pendingThinkingDeltas: {},
  pendingThinkingDeltaStartedAt: {},
  finalPayload: null,
  jobFinalized: false,
  isBackgroundDispatch: false,
  };

  const targetMsg = source.get().messages.find((m) => m.id === assistantMessageId);
  const mergedMetadata = { ...((targetMsg?.metadata as Record<string, unknown> | undefined) ?? {}) };
  source.get().updateMessage(assistantMessageId, { thinking_process: undefined, metadata: { ...mergedMetadata, analysis_text: '', deep_think_progress: undefined, thinking_process: undefined, thinking_display_mode: undefined } });

  const boundFlush = (force: boolean = false) => flushAnalysisText(source.get, assistantMessageId, state, force);
  const boundScheduleFlush = () => scheduleFlush(state, boundFlush);
  const boundStartPolling = (
  trackingId: string | null | undefined,
  messageId: string,
  initialStatus?: ChatActionStatus,
  initialContent?: string | null
  ) => startActionStatusPolling(source.get, trackingId, messageId, initialStatus, initialContent);

  const ctx: StreamHandlerContext = {
  get: source.get,
  set: source.set,
  sourceSessionScoped: true,
  isCurrentSession: source.isCurrent,
  assistantMessageId,
  mergedMetadata,
  currentSession,
  state,
  startActionStatusPolling: boundStartPolling,
  flushAnalysisText: boundFlush,
  scheduleFlush: boundScheduleFlush,
  };

  try {
  await _consumeUnifiedStream(ctx, streamRunEvents(apiSid, runId, -1));
  await _finalizeAfterUnifiedStream(ctx, state, boundFlush);
  } catch (error) {
  console.error('Resume chat run failed:', error);
  const partialContent =
    (source.get().messages.find((m) => m.id === assistantMessageId)?.content as string | undefined) || '';
  let recovered = false;
  try {
    recovered = await _recoverAfterStreamFailure(get, {
      apiSessionId: String(apiSid),
      localSessionId: String(currentSession?.id ?? apiSid),
      assistantMessageId,
      processingKey: resumeKey,
      runId,
      clientMessageId: lastUser?.metadata?.client_message_id,
      partialContent,
      set,
      currentSession,
    });
  } catch (recoverErr) {
    console.warn('[chat] resume recover threw:', recoverErr);
  }
  if (recovered) {
    return;
  }
  source.get().setActiveRunId(resumeKey, null);
  source.get().setSessionProcessing(resumeKey, false);
  source.get().updateMessage(assistantMessageId, {
  content:
  '连接中断且自动恢复失败。请刷新页面查看是否已有结果，或重新发送消息。\n\n' +
  (error instanceof Error ? error.message : String(error)),
  metadata: {
  status: 'failed',
  recovering: false,
  errors: [error instanceof Error ? error.message : String(error)],
  },
  });
  }
  },

  retryLastMessage: async () => {
  const { messages, currentSession, processingSessionIds } = get();
  const retryKey = resolveChatSessionProcessingKey(currentSession);
  if (processingSessionIds.has(retryKey)) return;
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
};
