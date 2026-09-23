import { describe, expect, it } from 'vitest';

import { parseChatTimestamp } from './chatMessageUtils';

describe('chatMessageUtils', () => {
  it('parses backend timestamps without timezone suffix as UTC', () => {
    const parsed = parseChatTimestamp('2026-04-22 01:01:00');
    expect(parsed.toISOString()).toBe('2026-04-22T01:01:00.000Z');
  });

  it('keeps timestamps with explicit timezone suffix as-is', () => {
    const parsed = parseChatTimestamp('2026-04-22T01:01:00+08:00');
    expect(parsed.toISOString()).toBe('2026-04-21T17:01:00.000Z');
  });

  it('falls back to now for empty input', () => {
    const before = Date.now();
    const parsed = parseChatTimestamp('');
    expect(Math.abs(parsed.getTime() - before)).toBeLessThan(5000);
  });
});
