import { describe, expect, it } from 'vitest';
import { matchesPlanEventSession } from './planSyncEvents';

describe('plan context notification ownership', () => {
  it('rejects a background A creation while B is active', () => {
    expect(matchesPlanEventSession({ type: 'plan_created', plan_id: 77, session_id: 'server-A' }, ['local-B', 'server-B'])).toBe(false);
  });

  it('accepts either identity of the originating chat', () => {
    expect(matchesPlanEventSession({ type: 'plan_created', plan_id: 77, session_id: 'server-A' }, ['local-A', 'server-A'])).toBe(true);
    expect(matchesPlanEventSession({ type: 'plan_created', plan_id: 77, session_id: 'local-A' }, ['local-A', 'server-A'])).toBe(true);
  });

  it('preserves global plan notifications without selecting a scoped event into no chat', () => {
    expect(matchesPlanEventSession({ type: 'plan_created', plan_id: 77 }, [])).toBe(true);
    expect(matchesPlanEventSession({ type: 'plan_created', plan_id: 77, session_id: 'server-A' }, [])).toBe(false);
  });
});
