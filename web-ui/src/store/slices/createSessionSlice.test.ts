import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocked = vi.hoisted(() => ({
  getSessions: vi.fn(),
  setCurrentSessionId: vi.fn(),
  clearCurrentSessionId: vi.fn(),
  setAllSessionIds: vi.fn(),
}));

vi.mock('@api/chat', () => ({
  chatApi: {
    getSessions: mocked.getSessions,
    deleteSession: vi.fn(),
    updateSession: vi.fn(),
    autotitleSession: vi.fn(),
  },
}));

vi.mock('@/utils/sessionStorage', () => ({
  SessionStorage: {
    getCurrentSessionId: vi.fn(() => null),
    setCurrentSessionId: mocked.setCurrentSessionId,
    clearCurrentSessionId: mocked.clearCurrentSessionId,
    setAllSessionIds: mocked.setAllSessionIds,
  },
}));

vi.mock('@store/tasks', () => ({
  useTasksStore: {
    getState: () => ({
      setTasks: vi.fn(),
      clearTaskResultCache: vi.fn(),
      closeTaskDrawer: vi.fn(),
    }),
  },
}));

vi.mock('@utils/planSyncEvents', () => ({
  dispatchPlanSyncEvent: vi.fn(),
}));

import { createSessionSlice } from './createSessionSlice';

function buildStore() {
  const state: any = {
    currentSession: null,
    sessions: [],
    messages: [],
    currentWorkflowId: null,
    currentPlanId: null,
    currentPlanTitle: null,
    currentTaskId: null,
    currentTaskName: null,
    historyBeforeId: null,
    historyHasMore: false,
    historyLoading: false,
    historyPageSize: 50,
    loadChatHistory: vi.fn().mockResolvedValue(undefined),
  };

  const set = (updater: any) => {
    const patch = typeof updater === 'function' ? updater(state) : updater;
    Object.assign(state, patch);
  };
  const get = () => state;

  Object.assign(state, createSessionSlice(set as any, get as any, {} as any));
  return state;
}

describe('createSessionSlice', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('persists the canonical backend session id when selecting a promoted session', () => {
    const store = buildStore();

    store.setCurrentSession({
      id: 'local-session-1',
      session_id: 'server-session-1',
      title: 'Session',
      messages: [],
      created_at: new Date(),
      updated_at: new Date(),
      workflow_id: null,
      plan_id: null,
      plan_title: null,
      current_task_id: null,
      current_task_name: null,
      last_message_at: null,
      is_active: true,
      titleSource: 'local',
      isUserNamed: false,
    });

    expect(mocked.setCurrentSessionId).toHaveBeenCalledWith('server-session-1');
    expect(mocked.clearCurrentSessionId).not.toHaveBeenCalled();
  });

  it('renames the matching session in both sessions and currentSession', async () => {
    const store = buildStore();
    const updatedAt = new Date('2026-04-21T04:00:00.000Z');

    store.sessions = [
      {
        id: 'server-session-1',
        session_id: 'server-session-1',
        title: 'Old title',
        messages: [],
        created_at: new Date('2026-04-20T04:00:00.000Z'),
        updated_at: new Date('2026-04-20T04:00:00.000Z'),
        workflow_id: null,
        plan_id: null,
        plan_title: null,
        current_task_id: null,
        current_task_name: null,
        last_message_at: null,
        is_active: true,
        titleSource: 'local',
        isUserNamed: false,
      },
    ];
    store.currentSession = store.sessions[0];

    mocked.getSessions.mockReset();
    const { chatApi } = await import('@api/chat');
    vi.mocked(chatApi.updateSession).mockResolvedValue({
      id: 'server-session-1',
      name: 'Renamed session',
      name_source: 'user',
      is_user_named: true,
      plan_id: null,
      plan_title: null,
      current_task_id: null,
      current_task_name: null,
      last_message_at: null,
      created_at: '2026-04-20T04:00:00.000Z',
      updated_at: updatedAt.toISOString(),
      is_active: true,
    });

    await store.renameSession('server-session-1', 'Renamed session');

    expect(chatApi.updateSession).toHaveBeenCalledWith('server-session-1', {
      name: 'Renamed session',
    });
    expect(store.currentSession.title).toBe('Renamed session');
    expect(store.currentSession.titleSource).toBe('user');
    expect(store.currentSession.isUserNamed).toBe(true);
    expect(store.sessions[0].title).toBe('Renamed session');
  });
});