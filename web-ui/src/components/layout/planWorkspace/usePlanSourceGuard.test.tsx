import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { planTreeApi } from '@api/planTree';
import { useChatStore } from '@store/chat';
import type { ChatSession, PlanTreeResponse } from '@/types';
import { restorePlanSource, usePlanSourceGuard } from './usePlanSourceGuard';
vi.mock('@api/planTree', () => ({ planTreeApi: { getPlanTree: vi.fn() } }));
const api = vi.mocked(planTreeApi);
const session = (id: string, plan_id: number | null): ChatSession => ({id, session_id: id, plan_id, title: id, messages: [], created_at: new Date(), updated_at: new Date()});
const origin = session('origin', 1);
const source = session('source', 2);
const tree = (sourceId?: string): PlanTreeResponse => ({ id: 2, title: '第二个计划', nodes: {}, adjacency: {}, metadata: sourceId ? {artifact_store_ref: {session_id: sourceId}} : {} });
const notice = {planId: 2, title: '第二个计划', sessionId: 'source'};
const savedRestore = useChatStore.getState().restoreSession;
const savedLoad = useChatStore.getState().loadSessions;
beforeEach(() => {
  vi.clearAllMocks();
  useChatStore.setState({currentSession: origin, sessions: [origin, source], currentPlanId: 1, currentPlanTitle: '原计划', restoreSession: savedRestore, loadSessions: savedLoad});
});
afterEach(() => { cleanup(); useChatStore.setState({restoreSession: savedRestore, loadSessions: savedLoad}); });
describe('fixed artifact source navigation', () => {
  it('shows explicit source navigation without binding a foreign plan', async () => {
    api.getPlanTree.mockResolvedValue(tree('source'));
    const {result} = renderHook(() => usePlanSourceGuard(1, 'origin'));
    await act(async () => { await result.current.select(2); });
    expect(useChatStore.getState().currentPlanId).toBe(1);
    expect(result.current.notice).toEqual(notice);
    act(() => result.current.dismiss());
    expect(result.current.notice).toBeNull();
    expect(useChatStore.getState().currentSession?.id).toBe('origin');
  });
  it.each(['origin', undefined])('binds only an explicit same-source or legacy selection (%s)', async (sourceId) => {
    api.getPlanTree.mockResolvedValue(tree(sourceId));
    const {result} = renderHook(() => usePlanSourceGuard(1, 'origin'));
    await act(async () => { await result.current.select(2); });
    expect(useChatStore.getState().currentPlanId).toBe(2);
  });
  it('ignores a late plan check after user switches sessions', async () => {
    let finish!: (value: PlanTreeResponse) => void;
    api.getPlanTree.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const {result} = renderHook(() => usePlanSourceGuard(1, 'origin'));
    let pending!: Promise<void>;
    act(() => { pending = result.current.select(2); });
    act(() => useChatStore.setState({currentSession: session('other', 9), currentPlanId: 9}));
    await act(async () => { finish(tree()); await pending; });
    expect(useChatStore.getState().currentPlanId).toBe(9);
    expect(result.current.notice).toBeNull();
  });
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
