export interface TurnIdentity {
  runId?: string | null;
  clientMessageId?: string | null;
}

const metadataOf = (message: any): Record<string, any> => {
  const value = message?.metadata;
  if (value && typeof value === 'object') return value;
  if (typeof value === 'string') {
    try { return JSON.parse(value); } catch { return {}; }
  }
  return {};
};

export function messageRunId(message: any): string | null {
  const metadata = metadataOf(message);
  return metadata.chat_run_id ?? metadata.run_id ?? metadata.deep_think_job_id ?? null;
}

/** Positive identity evidence is required; length or recency alone cannot recover a turn. */
export function findCorrelatedAssistant(messages: any[], identity: TurnIdentity): any | null {
  let inExpectedTurn = false;
  for (const message of messages) {
    const role = message?.role ?? message?.type;
    const metadata = metadataOf(message);
    if (role === 'user') {
      inExpectedTurn = Boolean(identity.clientMessageId && metadata.client_message_id === identity.clientMessageId);
      continue;
    }
    if (role !== 'assistant') continue;
    const runId = messageRunId(message);
    if (identity.runId && runId && runId !== identity.runId) continue;
    const directMatch = Boolean(
      (identity.runId && runId === identity.runId) ||
      (identity.clientMessageId && metadata.client_message_id === identity.clientMessageId),
    );
    if (!directMatch && !inExpectedTurn) continue;
    const status = String(metadata.status ?? '').toLowerCase();
    if (status && !['completed', 'succeeded', 'failed', 'cancelled', 'canceled'].includes(status)) continue;
    if (typeof message.content !== 'string' || !message.content.trim()) continue;
    return message;
  }
  return null;
}

/** Keep optimistic/source-stream messages until their own persisted turn is present. */
export function mergeUnpersistedTurns(persisted: any[], existing: any[]): any[] {
  const pending = existing.filter((message) => {
    if (message.metadata?.backend_id != null) return false;
    const clientMessageId = message.metadata?.client_message_id;
    if (message.type === 'user') {
      return Boolean(clientMessageId && !persisted.some((entry) =>
        entry.type === 'user' && entry.metadata?.client_message_id === clientMessageId));
    }
    const runId = messageRunId(message);
    return Boolean((runId || clientMessageId) && !findCorrelatedAssistant(persisted, { runId, clientMessageId }));
  });
  return [...persisted, ...pending];
}
