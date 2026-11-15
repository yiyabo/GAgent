import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@api/chat', () => ({ chatApi: {
  getSessions: vi.fn(), deleteSession: vi.fn(), updateSession: vi.fn(), autotitleSession: vi.fn(),
} }));
vi.mock('@/utils/sessionStorage', () => ({ SessionStorage: {
  getCurrentSessionId: vi.fn(() => null), setCurrentSessionId: vi.fn(),
  clearCurrentSessionId: vi.fn(), setAllSessionIds: vi.fn(),
} }));
vi.mock('@store/auth', () => ({ useAuthStore: { getState: () => ({ projectId: null }) } }));
vi.mock('@store/tasks', () => ({ useTasksStore: { getState: () => ({}) } }));
vi.mock('@utils/planSyncEvents', () => ({ dispatchPlanSyncEvent: vi.fn() }));

import { chatApi } from '@api/chat';
import { createContextSlice } from './createContextSlice';
import { createSessionSlice } from './createSessionSlice';

function session(id: string, planMode = false) {
  return {
    id: `local-${id}`, session_id: id, title: id, messages: [],
    created_at: new Date(), updated_at: new Date(),
    settings: { response_length: 'concise', plan_mode: planMode },
  } as any;
}

function fixture() {
  const a = session('server-A');
  const b = session('server-B');
  const state: any = {
    currentSession: null, sessions: [a, b], messages: [],
    currentWorkflowId: null, currentPlanId: null, currentPlanTitle: null,
    currentTaskId: null, currentTaskName: null,
    historyBeforeId: null, historyHasMore: false, historyLoading: false,
    syncUploadedFilesFromServer: vi.fn().mockResolvedValue(undefined),
    clearUploadedFiles: vi.fn(),
  };
  const get = () => state;
  const set = (patch: any) => Object.assign(state, typeof patch === 'function' ? patch(state) : patch);
  Object.assign(state, createSessionSlice(set as any, get, {} as any), createContextSlice(set as any, get, {} as any));
  state.sessions = [a, b];
  state.setCurrentSession(a);
  return { state, a, b };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(chatApi.updateSession).mockResolvedValue({} as any);
});

describe('explicit Plan mode session state', () => {
  it('persists the toggle without discarding existing session settings', async () => {
    const { state } = fixture();
    await state.setPlanMode(true);

    expect(state.planMode).toBe(true);
    expect(state.currentSession.settings).toEqual({ response_length: 'concise', plan_mode: true, plan_offer_dismissed: false });
    expect(state.sessions[0].settings.plan_mode).toBe(true);
    expect(chatApi.updateSession).toHaveBeenCalledWith('server-A', {
      settings: { response_length: 'concise', plan_mode: true, plan_offer_dismissed: false },
    });
  });

  it('restores each chat\'s own mode when switching sessions', async () => {
    const { state, a, b } = fixture();
    await state.setPlanMode(true);
    state.setCurrentSession(b);
    expect(state.planMode).toBe(false);
    state.setCurrentSession(a);
    expect(state.planMode).toBe(true);
  });

  it('persists dismissal and clears it after an explicit Plan-mode opt-in', async () => {
    const { state } = fixture();
    await state.dismissPlanModeOffer();
    expect(state.currentSession.settings.plan_offer_dismissed).toBe(true);
    expect(chatApi.updateSession).toHaveBeenLastCalledWith('server-A', {
      settings: { response_length: 'concise', plan_mode: false, plan_offer_dismissed: true },
    });

    await state.setPlanMode(true);
    expect(state.currentSession.settings.plan_offer_dismissed).toBe(false);
  });

  it('mirrors a pending clarification card for the matching session only', () => {
    const { state } = fixture();
    state.setPendingPlanClarification('server-A', 'clarify_1');
    expect(state.currentSession.settings.pending_plan_clarification_id).toBe('clarify_1');
    expect(state.currentSession.settings.waiting_user_input).toBe(true);
    expect(state.sessions[0].settings.waiting_user_input).toBe(true);
    expect(state.sessions[1].settings.waiting_user_input).toBeUndefined();
    expect(chatApi.updateSession).not.toHaveBeenCalled();

    state.setPendingPlanClarification('local-server-A', null);
    expect(state.currentSession.settings.pending_plan_clarification_id).toBeNull();
    expect(state.sessions[0].settings.waiting_user_input).toBe(false);
  });

  it('rolls back the optimistic toggle when persistence fails', async () => {
    const { state } = fixture();
    vi.mocked(chatApi.updateSession).mockRejectedValueOnce(new Error('offline'));
    await expect(state.setPlanMode(true)).rejects.toThrow('offline');
    expect(state.planMode).toBe(false);
    expect(state.currentSession.settings.plan_mode).toBe(false);
  });
});
