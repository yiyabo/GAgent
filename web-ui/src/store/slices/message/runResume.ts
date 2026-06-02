const ACTIVE_CHAT_RUN_STATUSES = new Set(['queued', 'running']);

export function matchesResumeSession(
  currentSession: { id?: string | null; session_id?: string | null } | null | undefined,
  sessionId: string,
): boolean {
  if (!currentSession) return false;
  return currentSession.id === sessionId || currentSession.session_id === sessionId;
}

export function selectActiveChatRun(response: unknown): Record<string, any> | null {
  const runs = Array.isArray((response as any)?.runs) ? (response as any).runs : [];
  for (const run of runs) {
    if (!run || typeof run !== 'object') continue;
    const runId = typeof (run as any).run_id === 'string' ? (run as any).run_id.trim() : '';
    const status = typeof (run as any).status === 'string' ? (run as any).status.trim().toLowerCase() : '';
    if (runId && ACTIVE_CHAT_RUN_STATUSES.has(status)) {
      return run as Record<string, any>;
    }
  }
  return null;
}
