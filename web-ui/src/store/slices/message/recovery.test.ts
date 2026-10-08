import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { recoverAfterStreamFailure, resetAutoResumeGuardsForTest } from './recovery';

vi.mock('@api/chat', () => ({
  chatApi: {
    getActiveRun: vi.fn(),
    getHistory: vi.fn(),
    getResumeInfo: vi.fn(),
  },
}));

vi.mock('./historyHydration', () => ({
  hydratePersistedMessage: vi.fn(({ rawMessage }: any) => ({
    content: rawMessage.content,
    timestamp: new Date(),
    thinking_process: undefined,
    metadata: rawMessage.metadata ?? {},
  })),
}));

import { chatApi } from '@api/chat';

const getActiveRun = chatApi.getActiveRun as unknown as ReturnType<typeof vi.fn>;
const getHistory = chatApi.getHistory as unknown as ReturnType<typeof vi.fn>;
const getResumeInfo = chatApi.getResumeInfo as unknown as ReturnType<typeof vi.fn>;

function makeStore(assistantMessage: any) {
  const state: any = {
    messages: [assistantMessage],
    sessions: [],
    currentSession: { id: 'local-1', session_id: 'api-1' },
    setSessionProcessing: vi.fn(),
    setActiveRunId: vi.fn(),
    loadChatHistory: vi.fn(async () => {}),
    addMessage: vi.fn(),
    updateMessage: vi.fn((id: string, updates: any) => {
      const i = state.messages.findIndex((m: any) => m.id === id);
      if (i >= 0) {
        state.messages[i] = {
          ...state.messages[i],
          ...updates,
          metadata: { ...state.messages[i].metadata, ...(updates.metadata ?? {}) },
        };
      }
    }),
  };
  return {
    get: () => state,
    set: (updater: any) => {
      const patch = typeof updater === 'function' ? updater(state) : updater;
      Object.assign(state, patch);
    },
  };
}

function makeOptions(overrides: Record<string, any> = {}) {
  const store = makeStore({ id: 'a1', type: 'assistant', content: '', timestamp: new Date(), metadata: { status: 'pending', chat_run_id: 'run-1' } });
  return {
    apiSessionId: 'api-1',
    localSessionId: 'local-1',
    assistantMessageId: 'a1',
    processingKey: 'api-1',
    runId: 'run-1',
    clientMessageId: 'turn-1',
    partialContent: '',
    get: store.get,
    set: store.set,
    resumeRun: vi.fn(async () => {}),
    autoResumeFromCheckpoint: vi.fn(async () => true),
    ...overrides,
  };
}

describe('recoverAfterStreamFailure stage 3 (auto resume from checkpoint)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetAutoResumeGuardsForTest();
    getActiveRun.mockResolvedValue({ data: { runs: [] } });
    getHistory.mockResolvedValue({ data: { messages: [] } });
    getResumeInfo.mockResolvedValue({ can_resume: true, message: '继续', run_id: 'run-1', session_id: 'api-1' });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it('reattaches to a live run in stage 1 without touching auto-resume', async () => {
    getActiveRun.mockResolvedValue({ data: { runs: [{ run_id: 'run-1', status: 'running' }] } });
    const opts = makeOptions();
    const ok = await recoverAfterStreamFailure(opts as any);
    expect(ok).toBe(true);
    expect(opts.resumeRun).toHaveBeenCalledWith('run-1');
    expect(opts.autoResumeFromCheckpoint).not.toHaveBeenCalled();
  });

  it('auto-resumes a dead resumable run once the history poll misses', async () => {
    const opts = makeOptions();
    const p = recoverAfterStreamFailure(opts as any);
    await vi.advanceTimersByTimeAsync(45000);
    await expect(p).resolves.toBe(true);
    expect(opts.autoResumeFromCheckpoint).toHaveBeenCalledTimes(1);
    expect(opts.autoResumeFromCheckpoint).toHaveBeenCalledWith('run-1');
  });

  it('never auto-resumes the same source run twice', async () => {
    const first = makeOptions();
    const p1 = recoverAfterStreamFailure(first as any);
    await vi.advanceTimersByTimeAsync(45000);
    await expect(p1).resolves.toBe(true);

    const second = makeOptions();
    const p2 = recoverAfterStreamFailure(second as any);
    await vi.advanceTimersByTimeAsync(45000);
    await expect(p2).resolves.toBe(false);
    expect(second.autoResumeFromCheckpoint).not.toHaveBeenCalled();
    expect(first.autoResumeFromCheckpoint).toHaveBeenCalledTimes(1);
  });

  it('stops immediately when the run is definitively not resumable', async () => {
    getResumeInfo.mockResolvedValue({ can_resume: false, reason: 'already finished' });
    const opts = makeOptions();
    const p = recoverAfterStreamFailure(opts as any);
    await vi.advanceTimersByTimeAsync(45000);
    await expect(p).resolves.toBe(false);
    expect(opts.autoResumeFromCheckpoint).not.toHaveBeenCalled();
    expect(getResumeInfo).toHaveBeenCalledTimes(1);
  });

  it('treats a 4xx from resume-info as definitive, not as backend-restarting', async () => {
    getResumeInfo.mockRejectedValue({ response: { status: 404 } });
    const opts = makeOptions();
    const p = recoverAfterStreamFailure(opts as any);
    await vi.advanceTimersByTimeAsync(45000);
    await expect(p).resolves.toBe(false);
    expect(opts.autoResumeFromCheckpoint).not.toHaveBeenCalled();
  });

  it('restores a completed reply from history instead of resuming', async () => {
    getHistory.mockResolvedValue({
      data: {
        messages: [
          { role: 'assistant', content: '完成了', metadata: { chat_run_id: 'run-1', status: 'completed' } },
        ],
      },
    });
    const opts = makeOptions();
    const p = recoverAfterStreamFailure(opts as any);
    await vi.advanceTimersByTimeAsync(5000);
    await expect(p).resolves.toBe(true);
    expect(opts.autoResumeFromCheckpoint).not.toHaveBeenCalled();
  });
});
