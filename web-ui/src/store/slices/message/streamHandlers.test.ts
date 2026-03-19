import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@api/chat', () => ({
  chatApi: {
    updateSession: vi.fn().mockResolvedValue({}),
    getActionStatus: vi.fn(),
  },
}));

import { chatApi } from '@api/chat';
import { handleJobUpdate, processFinalPayload } from './streamHandlers';

const buildContext = () => {
  const assistantMessage = {
    id: 'assistant-1',
    type: 'assistant',
    content: '',
    metadata: {},
  };
  const initialSession = {
    id: 'session-1',
    session_id: 'session-1',
    plan_id: null,
    plan_title: null,
    current_task_id: null,
    current_task_name: null,
    isUserNamed: true,
    messages: [assistantMessage],
  };
  const store: any = {
    messages: [assistantMessage],
    currentSession: initialSession,
    sessions: [initialSession],
    currentPlanId: null,
    currentPlanTitle: null,
    currentTaskId: null,
    currentTaskName: null,
    currentWorkflowId: null,
    updateMessage: (messageId: string, updates: Record<string, unknown>) => {
      store.messages = store.messages.map((message: any) =>
        message.id === messageId ? { ...message, ...updates } : message
      );
      if (store.currentSession) {
        store.currentSession = { ...store.currentSession, messages: store.messages };
        store.sessions = store.sessions.map((session: any) =>
          session.id === store.currentSession.id ? store.currentSession : session
        );
      }
    },
    loadChatHistory: vi.fn().mockResolvedValue(undefined),
    autotitleSession: vi.fn().mockResolvedValue(undefined),
    setCurrentWorkflowId: vi.fn((workflowId: string | null) => {
      store.currentWorkflowId = workflowId;
    }),
  };
  const get = () => store;
  const set = (updater: any) => {
    const patch = typeof updater === 'function' ? updater(store) : updater;
    Object.assign(store, patch);
  };

  return {
    store,
    ctx: {
      get,
      set,
      assistantMessageId: 'assistant-1',
      mergedMetadata: {},
      currentSession: initialSession,
      state: {
        streamedContent: '',
        lastFlushedContent: '',
        flushHandle: null,
        thinkingDeltaFlushHandle: null,
        pendingThinkingDeltas: {},
        finalPayload: null,
        jobFinalized: false,
        isBackgroundDispatch: false,
      },
      startActionStatusPolling: vi.fn(),
      flushAnalysisText: vi.fn(),
      scheduleFlush: vi.fn(),
    },
  };
};

describe('streamHandlers session sync', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps the updated session when a job completion binds a plan', async () => {
    const { store, ctx } = buildContext();

    await handleJobUpdate(ctx as any, {
      payload: {
        status: 'succeeded',
        job_id: 'job-1',
        result: {
          bound_plan_id: 55,
          plan_title: 'Plan 55',
          steps: [],
        },
      },
    });

    expect(store.currentPlanId).toBe(55);
    expect(store.currentSession.plan_id).toBe(55);
    expect(store.currentSession.plan_title).toBe('Plan 55');
    expect(chatApi.updateSession).toHaveBeenCalledWith(
      'session-1',
      expect.objectContaining({ plan_id: 55, plan_title: 'Plan 55' }),
    );
  });

  it('keeps the updated session when the final payload carries a plan binding', async () => {
    const { store, ctx } = buildContext();
    ctx.state.finalPayload = {
      response: 'Plan ready',
      actions: [],
      metadata: {
        status: 'completed',
        plan_id: 77,
        plan_title: 'Plan 77',
      },
    };

    await processFinalPayload(ctx as any);

    expect(store.currentPlanId).toBe(77);
    expect(store.currentSession.plan_id).toBe(77);
    expect(store.currentSession.plan_title).toBe('Plan 77');
    expect(chatApi.updateSession).toHaveBeenCalledWith(
      'session-1',
      expect.objectContaining({ plan_id: 77, plan_title: 'Plan 77' }),
    );
  });
});
