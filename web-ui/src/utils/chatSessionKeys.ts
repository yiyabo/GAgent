import type { ChatSession } from '@/types';

/** Stable key for per-session UI locks (must match API session_id when present). */
export function resolveChatSessionProcessingKey(
  session: Pick<ChatSession, 'id' | 'session_id'> | null | undefined
): string {
  if (!session) return '__none__';
  const sid = session.session_id?.trim();
  if (sid) return sid;
  const id = session.id?.trim();
  if (id) return id;
  return '__none__';
}
