import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatStore } from '@store/chat';
import type { ChatSession } from '@/types';
import { restorePlanSource } from './usePlanSourceGuard';
const session = (id: string, plan_id: number | null): ChatSession => ({id, session_id: id, plan_id, title: id, messages: [], created_at: new Date(), updated_at: new Date()});
const origin = session('origin', 1);
const source = session('source', 2);
const notice = {planId: 2, title: '第二个计划', sessionId: 'source'};
const savedRestore = useChatStore.getState().restoreSession;
const savedLoad = useChatStore.getState().loadSessions;
beforeEach(() => {
  vi.clearAllMocks();
  useChatStore.setState({currentSession: origin, sessions: [origin, source], currentPlanId: 1, currentPlanTitle: '原计划', restoreSession: savedRestore, loadSessions: savedLoad});
});
afterEach(() => { cleanup(); useChatStore.setState({restoreSession: savedRestore, loadSessions: savedLoad}); });
describe('fixed artifact source navigation', () => {
  it('restores the known source then binds the chosen plan', async () => {
    const restore = vi.fn(async () => {
      useChatStore.setState({currentSession: source, currentPlanId: 2});
      return source;
    });
    useChatStore.setState({restoreSession: restore});
    expect(await restorePlanSource(notice, () => useChatStore.getState().currentSession?.id === 'origin')).toBe(true);
    expect(restore).toHaveBeenCalledWith('source', 'source');
    expect(useChatStore.getState().currentPlanId).toBe(2);
    expect(useChatStore.getState().currentPlanTitle).toBe('第二个计划');
  });
  it('does not bind after user leaves the source while history is loading', async () => {
    let finish!: (value: ChatSession) => void;
    useChatStore.setState({restoreSession: vi.fn(() => {
      useChatStore.setState({currentSession: source, currentPlanId: 2});
      return new Promise((resolve) => { finish = resolve; });
    })});
    const pending = restorePlanSource(notice, () => useChatStore.getState().currentSession?.id === 'origin');
    useChatStore.setState({currentSession: session('other', 9), currentPlanId: 9});
    finish(source);
    expect(await pending).toBe(false);
    expect(useChatStore.getState().currentPlanId).toBe(9);
  });
  it('does not fabricate or restore a source that is absent from accessible sessions', async () => {
    const restore = vi.fn();
    useChatStore.setState({sessions: [origin], loadSessions: vi.fn(async () => {}), restoreSession: restore});
    await expect(restorePlanSource(notice, () => true)).rejects.toThrow('来源会话不在当前可访问');
    expect(restore).not.toHaveBeenCalled();
  });
});
