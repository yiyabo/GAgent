import type { ChatMessage } from '@/types';

const TZ_SUFFIX_RE = /(Z|[+-]\d{2}:\d{2})$/i;

export function parseChatTimestamp(value: unknown): Date {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!text) {
    return new Date();
  }

  const direct = new Date(text);
  if (!Number.isNaN(direct.getTime()) && TZ_SUFFIX_RE.test(text)) {
    return direct;
  }

  const normalized = text.includes('T') ? `${text}Z` : `${text.replace(' ', 'T')}Z`;
  const utc = new Date(normalized);
  if (!Number.isNaN(utc.getTime())) {
    return utc;
  }

  return Number.isNaN(direct.getTime()) ? new Date() : direct;
}

export function isLikelyPersistedDuplicateMessage(
  liveMessage: ChatMessage,
  persistedMessage: ChatMessage
): boolean {
  if (!liveMessage?.id?.startsWith?.('msg_')) {
    return false;
  }
  if ((liveMessage.metadata as any)?.backend_id != null) {
    return false;
  }
  if ((persistedMessage.metadata as any)?.backend_id == null) {
    return false;
  }

  const liveClientMessageId = (liveMessage.metadata as any)?.client_message_id;
  const persistedClientMessageId = (persistedMessage.metadata as any)?.client_message_id;
  if (
    typeof liveClientMessageId === 'string' &&
    liveClientMessageId.length > 0 &&
    liveClientMessageId === persistedClientMessageId
  ) {
    return true;
  }

  if (liveMessage.type !== persistedMessage.type) {
    return false;
  }
  if (liveMessage.content.trim() !== persistedMessage.content.trim()) {
    return false;
  }

  const liveTs = liveMessage.timestamp instanceof Date ? liveMessage.timestamp.getTime() : NaN;
  const persistedTs =
    persistedMessage.timestamp instanceof Date ? persistedMessage.timestamp.getTime() : NaN;
  if (!Number.isFinite(liveTs) || !Number.isFinite(persistedTs)) {
    return false;
  }

  return Math.abs(liveTs - persistedTs) <= 30_000;
}
