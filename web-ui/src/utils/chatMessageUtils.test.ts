import { describe, expect, it } from 'vitest';

import { isLikelyPersistedDuplicateMessage, parseChatTimestamp } from './chatMessageUtils';

describe('chatMessageUtils', () => {
  it('dedupes persisted messages by client message id', () => {
    const liveTimestamp = new Date('2026-04-22T00:58:00.000Z');
    const persistedTimestamp = new Date('2026-04-22T01:01:00.000Z');

    const liveMessage: any = {
      id: 'msg_optimistic_user',
      type: 'user',
      content: 'Hello, could you help me write a review paper on non-natural amino acids?',
      timestamp: liveTimestamp,
      metadata: { client_message_id: 'client_123' },
    };
    const persistedMessage: any = {
      id: 'session_abc_42',
      type: 'user',
      content: 'Hello, could you help me write a review paper on non-natural amino acids?',
      timestamp: persistedTimestamp,
      metadata: { backend_id: 42, client_message_id: 'client_123' },
    };

    expect(isLikelyPersistedDuplicateMessage(liveMessage, persistedMessage)).toBe(true);
  });

  it('keeps legacy timestamp-based duplicate detection as fallback', () => {
    const liveTimestamp = new Date('2026-04-22T00:58:00.000Z');
    const persistedTimestamp = new Date('2026-04-22T00:58:20.000Z');

    const liveMessage: any = {
      id: 'msg_optimistic_user',
      type: 'user',
      content: 'repeatable prompt',
      timestamp: liveTimestamp,
      metadata: {},
    };
    const persistedMessage: any = {
      id: 'session_abc_7',
      type: 'user',
      content: 'repeatable prompt',
      timestamp: persistedTimestamp,
      metadata: { backend_id: 7 },
    };

    expect(isLikelyPersistedDuplicateMessage(liveMessage, persistedMessage)).toBe(true);
  });

  it('parses backend timestamps without timezone suffix as UTC', () => {
    const parsed = parseChatTimestamp('2026-04-22 01:01:00');
    expect(parsed.toISOString()).toBe('2026-04-22T01:01:00.000Z');
  });
});