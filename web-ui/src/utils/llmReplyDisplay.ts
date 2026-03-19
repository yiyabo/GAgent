/**
 * If assistant content was persisted as structured-agent JSON (legacy simple-chat bug),
 * extract llm_reply.message for display.
 */
export function extractLlmReplyMessage(raw: string | null | undefined): string {
  if (raw == null) return '';
  const trimmed = String(raw).trim();
  if (!trimmed.startsWith('{')) return String(raw);

  let cleaned = trimmed;
  if (cleaned.startsWith('```')) {
    const lines = cleaned.split('\n');
    if (lines[0]?.startsWith('```')) lines.shift();
    if (lines.length && lines[lines.length - 1]?.trim() === '```') lines.pop();
    cleaned = lines.join('\n').trim();
  }

  try {
    const obj = JSON.parse(cleaned) as Record<string, unknown>;
    const lr = obj?.llm_reply;
    if (lr && typeof lr === 'object' && lr !== null) {
      const msg = (lr as { message?: unknown }).message;
      if (typeof msg === 'string' && msg.trim()) return msg;
    }
  } catch {
    /* not JSON */
  }
  return String(raw);
}
