import { beforeEach, describe, expect, it, vi } from 'vitest';
const api = vi.hoisted(() => ({ get: vi.fn(), update: vi.fn() }));
const auth = vi.hoisted(() => ({ user: { user_id: 'alice' } as any, authenticated: true }));
vi.mock('@api/chat', () => ({ chatApi: { getResponsePreferences: api.get, updateSession: api.update } }));
vi.mock('@store/auth', () => ({ useAuthStore: { getState: () => auth } }));
import { createResponseStyleSlice } from './createResponseStyleSlice';
import { summaryToChatSession } from '../chatUtils';
const prefs = { response_style: 'research_advisor', response_length: 'concise' } as const;
const session = (id: string, local = false) => ({ id, session_id: id, messages: [], titleSource: local ? 'local' : 'user' });
function build() {
  const state: any = { sessions: [session('a'), session('b')], currentSession: session('a') };
  const set = (updater: any) => Object.assign(state, typeof updater === 'function' ? updater(state) : updater);
  Object.assign(state, createResponseStyleSlice(set as any, () => state, {} as any));
  return state;
}
function deferred() {
  let resolve!: (value: any) => void; let reject!: (error: Error) => void;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}
beforeEach(() => { vi.clearAllMocks(); auth.user = { user_id: 'alice' }; auth.authenticated = true; });
describe('reply preference persistence', () => {
  it('updates the original chat when an apply finishes after switching chats', async () => {
    const state = build(); const result = deferred(); api.update.mockReturnValue(result.promise);
    const pending = state.updateResponsePreferences('a', prefs, true);
    state.currentSession = state.sessions[1];
    result.resolve({ settings: prefs }); await pending;
    expect(state.sessions[0].settings).toEqual(prefs);
    expect(state.currentSession.id).toBe('b'); expect(state.currentSession.settings).toBeUndefined();
    expect(state.responseDefaults).toEqual(prefs);
    expect(api.update).toHaveBeenCalledWith('a', { settings: prefs, save_response_defaults: true });
  });
  it('does not mutate state after an account switch', async () => {
    const state = build(); const result = deferred(); api.update.mockReturnValue(result.promise);
    const pending = state.updateResponsePreferences('a', prefs, true);
    auth.user = { user_id: 'bob' }; result.resolve({ settings: prefs }); await pending;
    expect(state.responseDefaults).toBeNull(); expect(state.sessions[0].settings).toBeUndefined();
  });
  it('keeps old choices on failure and permits retry', async () => {
    const state = build(); api.update.mockRejectedValueOnce(new Error('offline'));
    await expect(state.updateResponsePreferences('a', prefs)).rejects.toThrow('offline');
    expect(state.currentSession.settings).toBeUndefined(); expect(state.responsePreferenceSaving.size).toBe(0);
    api.update.mockResolvedValue({ settings: prefs }); await state.updateResponsePreferences('a', prefs);
    expect(state.currentSession.settings).toEqual(prefs);
  });
  it('freezes defaults only into empty local sessions and ignores a late older default read', async () => {
    const state = build(); state.sessions.push(session('new', true));
    api.get.mockResolvedValue(prefs); await state.loadResponseDefaults();
    expect(state.sessions[2].settings).toEqual(prefs); expect(state.sessions[0].settings).toBeUndefined();
    const result = deferred(); state.responseDefaultsOwner = null; api.get.mockReturnValue(result.promise);
    const pending = state.loadResponseDefaults();
    const next = { ...prefs, response_style: 'product_manager' };
    api.update.mockResolvedValue({ settings: next }); await state.updateResponsePreferences('a', next, true);
    result.resolve(prefs); await pending; expect(state.responseDefaults).toEqual(next);
  });
  it('discards defaults when auth changes before the response', async () => {
    const state = build(); const result = deferred(); api.get.mockReturnValue(result.promise);
    const pending = state.loadResponseDefaults(); auth.user = { user_id: 'bob' };
    result.resolve(prefs); await pending; expect(state.responseDefaults).toBeNull();
  });
  it('retains session preferences in a refreshed session summary', () => {
    const saved = summaryToChatSession({ id:'a', is_active:true, settings:prefs });
    expect(saved.settings).toEqual(prefs);
  });
});
