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
import { resolveRequestFailureMessage } from '@/components/chat/message/utils';

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

const _consumeUnifiedStream = async (
  ctx: StreamHandlerContext,
  source: AsyncIterable<{ seq: number | null; event: ChatStreamEvent }>
): Promise<void> => {
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
  let data: any;
  try {
  const response = await chatApi.getHistory(sessionId, Object.fromEntries(query.entries()));
  data = response.data;
  } catch (err: any) {
  const status = err?.response?.status;
  if (status === 404) {
  const targetSession = get().sessions.find(
  (session) => (session.session_id ?? session.id) === sessionId || session.id === sessionId
  );
  const isUnsyncedLocalSession = Boolean(
  targetSession &&
  targetSession.titleSource === 'local' &&
  (targetSession.messages?.length ?? 0) === 0
  );
  if (isUnsyncedLocalSession) {
  set({
  messages: [],
  historyBeforeId: null,
  historyHasMore: false,
  });
  return;
  }
  }
  throw err;
  }
  const hasMore =
  typeof data.has_more === 'boolean'
  ? data.has_more
  : Array.isArray(data.messages) && data.messages.length >= limit;

  if (data.success && data.messages && data.messages.length > 0) {
  const existingMessages = get().messages;
  const existingToolResults = buildToolResultsCache(existingMessages);

  const newMessages: ChatMessage[] = data.messages.map((msg: any, index: number) =>
  hydratePersistedMessage({
  sessionId,
  rawMessage: msg,
  index,
  fallbackToolResults: existingToolResults,
  })
  );

  const merged = append ? [...newMessages, ...existingMessages] : newMessages;
  const seen = new Set<string>();
  const messages = merged.filter((msg) => {
  if (seen.has(msg.id)) return false;
  seen.add(msg.id);
  return true;
  });
  const targetSessionBeforeUpdate = get().sessions.find((s) => s.id === sessionId) ?? null;
  const recoveredPlanBinding =
  targetSessionBeforeUpdate && targetSessionBeforeUpdate.plan_id == null
  ? recoverPlanBindingFromMessages(messages)
  : null;

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
  if (targetSession.plan_id == null && recoveredPlanBinding?.planId != null) {
  updatedSession.plan_id = recoveredPlanBinding.planId;
  updatedSession.plan_title = recoveredPlanBinding.planTitle ?? targetSession.plan_title ?? null;
  }
  const sessions = state.sessions.map((s) => (s.id === sessionId ? updatedSession : s));
  const isCurrent = state.currentSession?.id === sessionId;
  return {
  sessions,
  currentSession: isCurrent ? updatedSession : state.currentSession,
  currentPlanId: isCurrent ? (updatedSession.plan_id ?? state.currentPlanId) : state.currentPlanId,
  currentPlanTitle: isCurrent ? (updatedSession.plan_title ?? state.currentPlanTitle) : state.currentPlanTitle,
  };
  });
  if (recoveredPlanBinding?.planId != null) {
  void chatApi.updateSession(sessionId, {
  plan_id: recoveredPlanBinding.planId,
  plan_title: recoveredPlanBinding.planTitle ?? null,
  }).catch((error) => console.warn('Failed to persist recovered plan binding:', error));
  }

  const nextBeforeId =
  typeof data.next_before_id === 'number'
  ? data.next_before_id
  : resolveHistoryCursor(messages);
  set({
  historyBeforeId: nextBeforeId ?? null,
  historyHasMore: hasMore,
  });
  if (!append) {
  void get().resumeActiveChatRunIfAny(sessionId);
  }
  } else {
  set({ historyHasMore: false, historyBeforeId: null });
  if (!append) {
  set({ messages: [] });
  void get().resumeActiveChatRunIfAny(sessionId);
  }
  }
  } catch (error) {
  console.error('loadfailed:', error);
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
  uploadedFiles,
  processingSessionIds,
  } = get();

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
  id: `msg_${Date.now()}_user`,
  type: 'user',
  content,
  timestamp: new Date(),
  metadata: {
  ...mergedMetadata,
  client_message_id: clientMessageId,
  },
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

  set({ inputText: '' });
  get().setSessionProcessing(processingKey, true);

  try {
  let memories: Memory[] = [];
  if (memoryEnabled) {
  try {
  const memoryResult = await memoryApi.queryMemory({ search_text: content, limit: 3, min_similarity: 0.6 });
  memories = memoryResult.memories || [];
  set({ relevantMemories: memories });
  } catch (error) {
  console.error('Memory RAG failed:', error);
  }
  }

  const recentMessages = get().messages.slice(-CHAT_REQUEST_HISTORY_LIMIT).map((msg) => ({
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

  const apiSessionId = currentSession?.session_id ?? currentSession?.id ?? undefined;
  let eventSource: AsyncIterable<{ seq: number | null; event: ChatStreamEvent }>;
  if (apiSessionId) {
  const { run_id } = await postChatRun(chatRequest);
  get().setActiveRunId(processingKey, run_id);
  const prevMeta =
  (get().messages.find((m) => m.id === assistantMessageId)?.metadata ?? {}) as Record<string, any>;
  get().updateMessage(assistantMessageId, {
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
  // Clear file attachments after successful send so they don't
  // get re-sent with every subsequent message.
  get().clearUploadedFiles();
  } catch (error) {
  console.error('Failed to send message:', error);
  get().setActiveRunId(processingKey, null);
  get().setSessionProcessing(processingKey, false);
  const errorContent =
  'Request failed. Please check:\n\n1. Backend service availability\n2. LLM API configuration\n3. Network connectivity\n\nThen retry the request.';
  if (assistantMessageAdded) {
  get().updateMessage(assistantMessageId, { content: errorContent, metadata: { status: 'failed', errors: [error instanceof Error ? error.message : String(error)] } });
  } else {
  get().addMessage({ id: `msg_${Date.now()}_assistant`, type: 'assistant', content: errorContent, timestamp: new Date() });
  }
  }
  },

  resumeActiveChatRunIfAny: async (sessionId: string) => {
  const { processingSessionIds, currentSession, messages } = get();
  if (currentSession?.id !== sessionId) {
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
  if (!res) {
  return;
  }
  const run = res?.runs?.[0];
  if (!run?.run_id) {
  // Server has no active run; client may still show "running" after backend restart.
  if (processingSessionIds.has(resumeKey)) {
  get().setActiveRunId(resumeKey, null);
  get().setSessionProcessing(resumeKey, false);
  const msgs = get().messages;
  const last = msgs.length > 0 ? msgs[msgs.length - 1] : null;
  if (last?.type === 'assistant' && (last.metadata as any)?.status === 'pending') {
  get().updateMessage(last.id, {
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
  if (processingSessionIds.has(resumeKey)) {
  const activeId = get().activeRunIds.get(resumeKey);
  if (activeId === runId) {
  return;
  }
  }
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
  get().addMessage({
  id: assistantMessageId,
  type: 'assistant',
  content: '',
  timestamp: new Date(),
  metadata: {
  status: 'pending',
  unified_stream: true,
  chat_run_id: runId,
  plan_message: null,
  },
  });
  }

  get().setActiveRunId(resumeKey, runId);
  get().setSessionProcessing(resumeKey, true);

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

  const targetMsg = get().messages.find((m) => m.id === assistantMessageId);
  const mergedMetadata = { ...((targetMsg?.metadata as Record<string, unknown> | undefined) ?? {}) };

  const boundFlush = (force: boolean = false) => flushAnalysisText(get, assistantMessageId, state, force);
  const boundScheduleFlush = () => scheduleFlush(state, boundFlush);
  const boundStartPolling = (
  trackingId: string | null | undefined,
  messageId: string,
  initialStatus?: ChatActionStatus,
  initialContent?: string | null
  ) => startActionStatusPolling(get, trackingId, messageId, initialStatus, initialContent);

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

  try {
  await _consumeUnifiedStream(ctx, streamRunEvents(apiSid, runId, -1));
  await _finalizeAfterUnifiedStream(ctx, state, boundFlush);
  } catch (error) {
  console.error('Resume chat run failed:', error);
  get().setActiveRunId(resumeKey, null);
  get().setSessionProcessing(resumeKey, false);
  get().updateMessage(assistantMessageId, {
  content:
  'Reconnect failed. Refresh the page or send a new message.\n\n' +
  (error instanceof Error ? error.message : String(error)),
  metadata: {
  status: 'failed',
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
