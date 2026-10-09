import { useCallback, useEffect, useRef, useState } from 'react';
import { useChatStore } from '@store/chat';
import { useTasksStore } from '@store/tasks';
import type { PlanTreeResponse } from '@/types';

let navigationGeneration = 0;
export interface SourceNotice { planId: number; title: string; sessionId: string }
const viewingSession = () => {
  const session = useChatStore.getState().currentSession;
  return session?.session_id ?? session?.id ?? null;
};
export const artifactSourceSession = (tree?: PlanTreeResponse): string | null => {
  const value = tree?.metadata?.artifact_store_ref?.session_id;
  return typeof value === 'string' && value.trim() ? value.trim() : null;
};
const bindPlan = (id: number, title: string) => {
  useTasksStore.getState().closeTaskDrawer();
  useTasksStore.getState().clearTaskResultCache();
  useChatStore.getState().setChatContext({ planId: id, planTitle: title, taskId: null, taskName: null });
};

/** Restore only a known session. restoreSession otherwise creates a local placeholder. */
export async function restorePlanSource(notice: SourceNotice, isOriginCurrent: () => boolean, isStillRequested: () => boolean = () => true): Promise<boolean> {
  const findSource = () => useChatStore.getState().sessions.find((session) => (session.session_id ?? session.id) === notice.sessionId);
  if (!findSource()) await useChatStore.getState().loadSessions();
  if (!isOriginCurrent() || !isStillRequested()) return false;
  const source = findSource();
  if (!source) throw new Error('来源会话不在当前可访问的会话列表中，请先在对应项目中打开该会话。');
  let interrupted = false;
  let enteredSource = false;
  const initialSourcePlan = source.plan_id ?? null;
  const unsubscribe = useChatStore.subscribe((state) => {
    const current = state.currentSession?.session_id ?? state.currentSession?.id ?? null;
    if (current === notice.sessionId) {
      enteredSource = true;
      if (state.currentPlanId !== initialSourcePlan && state.currentPlanId !== notice.planId) interrupted = true;
    } else if (enteredSource || !isOriginCurrent()) interrupted = true;
  });
  try {
    const restored = await useChatStore.getState().restoreSession(source.id, source.title);
    if (interrupted || !isStillRequested() || viewingSession() !== notice.sessionId || (restored.session_id ?? restored.id) !== notice.sessionId) return false;
    bindPlan(notice.planId, notice.title);
    return true;
  } finally { unsubscribe(); }
}

/**
 * Guards the "artifacts belong to another session" notice for the session's own
 * bound plan. One session, one plan (2026-10-09, LOCAL_INFRA §113): the former
 * `select` entry that let a session adopt another plan is gone; the only
 * navigation left is opening the plan's source session, which then shows the
 * plan it produced.
 */
export function usePlanSourceGuard(planId: number | null, sessionId: string | null) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current += 1; }; }, []);
  const isCurrent = useCallback((ticket: number) => mounted.current && generation.current === ticket
    && viewingSession() === sessionId && useChatStore.getState().currentPlanId === planId, [planId, sessionId]);
  const dismiss = useCallback(() => { navigationGeneration += 1; generation.current += 1; setError(null); setBusy(false); }, []);

  const openSource = useCallback(async (target: SourceNotice) => {
    const navigation = ++navigationGeneration;
    const ticket = ++generation.current;
    setBusy(true); setError(null);
    try { await restorePlanSource(target, () => isCurrent(ticket), () => navigationGeneration === navigation); }
    catch (failure) { if (isCurrent(ticket)) setError(failure instanceof Error ? failure.message : '无法打开来源会话。'); }
    finally { if (isCurrent(ticket)) setBusy(false); }
  }, [isCurrent]);
  return {error, busy, dismiss, openSource};
}
