import { describe, expect, it } from 'vitest';
import { parseServerTimestampMs } from './serverTime';

describe('server timestamps', () => {
  it('reads legacy job timestamps with an offset followed by a stray Z', () => {
    expect(parseServerTimestampMs('2026-10-13T20:47:03+08:00Z'))
      .toBe(Date.parse('2026-10-13T20:47:03+08:00'));
  });
  it('keeps ordinary UTC and offset timestamps unchanged', () => {
    expect(parseServerTimestampMs('2026-10-13T12:47:03Z'))
      .toBe(Date.parse('2026-10-13T12:47:03Z'));
    expect(parseServerTimestampMs('2026-10-13T20:47:03+08:00'))
      .toBe(Date.parse('2026-10-13T20:47:03+08:00'));
  });
});
