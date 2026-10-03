import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { planTreeApi } from '@api/planTree';
import { useChatStore } from '@store/chat';
import { useTasksStore } from '@store/tasks';
import TaskDetailDrawer from './index';

vi.mock('@api/planTree', () => ({ planTreeApi: { getTaskResult: vi.fn(), verifyTask: vi.fn(), acceptTask: vi.fn() } }));
vi.mock('@api/stats', () => ({ statsApi: { getPlanTaskTokenUsage: vi.fn(async () => ({ tasks: [] })) } }));
vi.mock('@hooks/usePlans', () => ({ usePlanTasks: ({ planId }: { planId: number }) => ({ data: [{ id: 1, plan_id: planId, name: `Task ${planId}`, status: 'failed' }], isFetching: false, refetch: vi.fn() }) }));
vi.mock('./TaskExecuteModal', () => ({ default: () => null }));
vi.mock('@components/chat/JobLogPanel', () => ({ default: () => null }));
vi.mock('./TaskDetailSections', () => ({ TaskDrawerContent: (props: any) => <div>
  <span>{props.taskResult?.content}</span>
  <button onClick={props.onReverify}>Verify now</button>
  <button onClick={props.onManualAccept}>Review now</button>
</div> }));

function setScope(planId: number) {
  useChatStore.setState({ currentPlanId: planId, currentSession: { id: `session-${planId}`, session_id: `session-${planId}` } as any, messages: [] });
  useTasksStore.setState({ selectedTaskId: 1, selectedTask: { id: 1, name: `Task ${planId}`, plan_id: planId, status: 'failed' }, isTaskDrawerOpen: true, taskResultCache: {} });
}
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } });
  return render(<QueryClientProvider client={client}><AntdApp><TaskDetailDrawer /></AntdApp></QueryClientProvider>);
}
const oldResponse = { success: true, message: 'old complete', plan_id: 1, task_id: 1, updated_fields: [], result: { task_id: 1, content: 'OLD MUTATION RESULT', status: 'completed' } };
const writeTaskResult = useTasksStore.getState().setTaskResult;
const cacheWrites = vi.fn(writeTaskResult);
beforeEach(() => {
  vi.clearAllMocks();
  useTasksStore.setState({ setTaskResult: cacheWrites });
  setScope(1);
  vi.mocked(planTreeApi.getTaskResult).mockImplementation(async planId => ({ task_id: 1, content: `Result ${planId}`, status: 'failed' }));
});

describe('task mutation callback ownership', () => {
  it('does not seed the new plan query from a previous plan task cache before mount cleanup', () => {
    useTasksStore.setState({ taskResultCache: { 1: { task_id: 1, content: 'OLD CACHED RESULT' } } });
    useChatStore.setState({ currentPlanId: 2, currentSession: { id: 'session-2', session_id: 'session-2' } as any });
    vi.mocked(planTreeApi.getTaskResult).mockReturnValue(new Promise(() => {}));
    mount();
    expect(screen.queryByText('OLD CACHED RESULT')).not.toBeInTheDocument();
  });

  it.each(['verify', 'accept'] as const)('does not let late %s completion overwrite the new plan with the same task id', async action => {
    let finish!: (value: any) => void;
    const api = action === 'verify' ? vi.mocked(planTreeApi.verifyTask) : vi.mocked(planTreeApi.acceptTask);
    api.mockReturnValue(new Promise(resolve => { finish = resolve; }));
    mount();
    await screen.findByText('Result 1');
    fireEvent.click(screen.getByText(action === 'verify' ? 'Verify now' : 'Review now'));
    if (action === 'accept') fireEvent.click(await screen.findByRole('button', { name: 'Accept task' }));
    await waitFor(() => expect(api).toHaveBeenCalled());
    await act(async () => { setScope(2); });
    await screen.findByText('Result 2');
    await act(async () => { finish(oldResponse); });
    expect(cacheWrites).not.toHaveBeenCalledWith(1, oldResponse.result);
    expect(useTasksStore.getState().taskResultCache[1]?.content).toBe('Result 2');
    expect(screen.queryByText('OLD MUTATION RESULT')).not.toBeInTheDocument();
  });

  it('does not repopulate the global cache after the drawer unmounts', async () => {
    let finish!: (value: any) => void;
    vi.mocked(planTreeApi.verifyTask).mockReturnValue(new Promise(resolve => { finish = resolve; }));
    const view = mount();
    await screen.findByText('Result 1');
    fireEvent.click(screen.getByText('Verify now'));
    await waitFor(() => expect(planTreeApi.verifyTask).toHaveBeenCalled());
    view.unmount();
    useTasksStore.getState().clearTaskResultCache();
    await act(async () => { finish(oldResponse); });
    expect(useTasksStore.getState().taskResultCache[1]).toBeUndefined();
  });

  it('still applies a verification while the original scope is selected', async () => {
    let finish!: (value: any) => void;
    vi.mocked(planTreeApi.verifyTask).mockReturnValue(new Promise(resolve => { finish = resolve; }));
    mount();
    await screen.findByText('Result 1');
    fireEvent.click(screen.getByText('Verify now'));
    await waitFor(() => expect(planTreeApi.verifyTask).toHaveBeenCalled());
    vi.mocked(planTreeApi.getTaskResult).mockResolvedValue(oldResponse.result);
    await act(async () => { finish(oldResponse); });
    await waitFor(() => expect(useTasksStore.getState().taskResultCache[1]?.content).toBe('OLD MUTATION RESULT'));
  });
});
