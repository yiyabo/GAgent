import type { ChatMessage, ChatSession } from '@/types';
import type { ChatState } from '../types';
import type { StoreGet, StoreSet, StreamHandlerContext } from './types';

export function matchesSession(session: ChatSession | null | undefined, sessionId?: string | null): boolean {
  return Boolean(session && sessionId && (session.id === sessionId || session.session_id === sessionId));
}

export function findSession(state: Pick<ChatState, 'currentSession' | 'sessions'>, sessionId?: string | null): ChatSession | null {
  if (!sessionId) return state.currentSession;
  if (matchesSession(state.currentSession, sessionId)) return state.currentSession;
  return state.sessions.find((session) => matchesSession(session, sessionId)) ?? null;
}

export function sessionMessages(state: ChatState, sessionId?: string | null): ChatMessage[] {
  if (!sessionId || matchesSession(state.currentSession, sessionId)) return state.messages;
  return findSession(state, sessionId)?.messages ?? [];
}

/** Apply entity changes to their owner; the active chat is only a projection of that entity. */
export function sessionPatch(state: ChatState, session: ChatSession, syncContext = false): Partial<ChatState> {
  const sessions = state.sessions.map((entry) => entry.id === session.id ? session : entry);
  if (state.currentSession?.id !== session.id) return { sessions };
  return {
    sessions,
    currentSession: session,
    messages: session.messages,
    ...(syncContext ? {
      currentPlanId: session.plan_id ?? null,
      currentPlanTitle: session.plan_title ?? null,
      currentTaskId: session.current_task_id ?? null,
      currentTaskName: session.current_task_name ?? null,
      currentWorkflowId: session.workflow_id ?? null,
    } : {}),
  };
}

/**
 * Long-running callbacks receive a view bound to the originating local session id.
 * Reading or patching currentSession/context through this view cannot target a
 * different chat after navigation. Global run/processing maps still remain shared.
 */
export function createSessionAccess(get: StoreGet, set: StoreSet, sessionId?: string | null) {
  const isCurrent = () => !sessionId || matchesSession(get().currentSession, sessionId);
  const sourceGet: StoreGet = () => {
    const state = get() as ChatState;
    const session = findSession(state, sessionId);
    return {
      ...state,
      currentSession: session,
      messages: sessionMessages(state, sessionId),
      currentPlanId: isCurrent() ? state.currentPlanId : session?.plan_id ?? null,
      currentPlanTitle: isCurrent() ? state.currentPlanTitle : session?.plan_title ?? null,
      currentTaskId: isCurrent() ? state.currentTaskId : session?.current_task_id ?? null,
      currentTaskName: isCurrent() ? state.currentTaskName : session?.current_task_name ?? null,
      currentWorkflowId: isCurrent() ? state.currentWorkflowId : session?.workflow_id ?? null,
      addMessage: (message: ChatMessage) => state.addMessage(message, sessionId ?? undefined),
      updateMessage: (messageId: string, updates: Partial<ChatMessage>) => state.updateMessage(messageId, updates, sessionId ?? undefined),
      setCurrentWorkflowId: (workflowId: string | null) => sourceSet({ currentWorkflowId: workflowId }),
      syncUploadedFilesFromServer: () => isCurrent() ? state.syncUploadedFilesFromServer() : Promise.resolve(),
    };
  };
  const sourceSet: StoreSet = (updater) => set((state: ChatState) => {
    const patch = typeof updater === 'function' ? updater(sourceGet()) : updater;
    if (!sessionId) return patch;
    const original = findSession(state, sessionId);
    // A removed chat must not be resurrected by its in-flight callbacks.
    if (!original) return {};
    const session: ChatSession = { ...original, ...(patch.currentSession ?? {}) };
    if (state.currentSession?.id === original.id) {
      // Some context setters predate persisted task fields. A plan-only patch
      // must not clear the active task/workflow selection as a side effect.
      session.current_task_id = state.currentTaskId;
      session.current_task_name = state.currentTaskName;
      session.workflow_id = state.currentWorkflowId;
    }
    if ('messages' in patch) session.messages = patch.messages;
    if ('currentPlanId' in patch) session.plan_id = patch.currentPlanId;
    if ('currentPlanTitle' in patch) session.plan_title = patch.currentPlanTitle;
    if ('currentTaskId' in patch) session.current_task_id = patch.currentTaskId;
    if ('currentTaskName' in patch) session.current_task_name = patch.currentTaskName;
    if ('currentWorkflowId' in patch) session.workflow_id = patch.currentWorkflowId;
    return sessionPatch(state, session, true);
  });
  return { get: sourceGet, set: sourceSet, isCurrent };
}

export function scopeStreamContext(ctx: StreamHandlerContext): StreamHandlerContext {
  if (ctx.sourceSessionScoped) return ctx;
  const access = createSessionAccess(ctx.get, ctx.set, ctx.currentSession?.id);
  return { ...ctx, get: access.get, set: access.set, isCurrentSession: access.isCurrent, sourceSessionScoped: true };
}
