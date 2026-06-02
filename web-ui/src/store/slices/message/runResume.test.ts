import { describe, expect, it } from 'vitest';

import { matchesResumeSession, selectActiveChatRun } from './runResume';

describe('selectActiveChatRun', () => {
  it('selects queued runs so refresh before worker start can still resume', () => {
    expect(
      selectActiveChatRun({
        runs: [
          { run_id: 'dt-old', status: 'failed' },
          { run_id: 'dt-new', status: 'queued' },
        ],
      }),
    ).toMatchObject({ run_id: 'dt-new', status: 'queued' });
  });

  it('ignores terminal runs when no active run exists', () => {
    expect(
      selectActiveChatRun({
        runs: [
          { run_id: 'dt-done', status: 'succeeded' },
          { run_id: 'dt-failed', status: 'failed' },
        ],
      }),
    ).toBeNull();
  });
});

describe('matchesResumeSession', () => {
  it('matches either local id or backend session_id', () => {
    const session = { id: 'local-1', session_id: 'server-1' };

    expect(matchesResumeSession(session, 'local-1')).toBe(true);
    expect(matchesResumeSession(session, 'server-1')).toBe(true);
    expect(matchesResumeSession(session, 'other')).toBe(false);
  });
});
