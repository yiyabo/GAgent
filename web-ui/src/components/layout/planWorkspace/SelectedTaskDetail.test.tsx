import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { planTreeApi } from '@api/planTree';
import type { PlanTaskNode } from '@/types';
import SelectedTaskDetail from './SelectedTaskDetail';

vi.mock('@api/planTree', () => ({ planTreeApi: { decomposeTask: vi.fn(), getTaskResult: vi.fn() } }));
vi.mock('../planArtifacts/PlanTaskArtifacts', () => ({ default: () => null }));
vi.mock('@components/tasks/detail/TaskExecuteModal', () => ({ default: ({ open }: { open: boolean }) => open ? <div>执行对话框</div> : null }));
vi.mock('@components/tasks/detail/TaskDetailSections', () => ({ ExecutionResult: () => null }));
vi.mock('@components/chat/JobLogPanel', () => ({ default: ({ jobId, jobType }: { jobId: string; jobType: string }) => <div>日志 {jobType}:{jobId}</div> }));
const api = vi.mocked(planTreeApi);
const stage: PlanTaskNode = { id: 4, plan_id: 185, name: '分章撰写', status: 'pending', parent_id: 1, task_type: 'composite' };
let client: QueryClient;
const props = () => ({ planId: 185, sessionId: 's1', task: stage, tasks: [stage], jobs: [],
  onSelectTask: vi.fn(), onShowHistory: vi.fn(), onBack: vi.fn(), onRefresh: vi.fn() });
const mount = (p: ReturnType<typeof props>) => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } });
  const view = (current: typeof p) => <QueryClientProvider client={client}><SelectedTaskDetail {...current} /></QueryClientProvider>;
  const mounted = render(view(p));
  return { ...mounted, rerenderProps: (next: typeof p) => mounted.rerender(view(next)) };
};
beforeEach(() => {
  vi.clearAllMocks();
  api.getTaskResult.mockResolvedValue({ task_id: 4 });
  api.decomposeTask.mockResolvedValue({ success: true, message: 'Queued', result: {}, job: { job_id: 'split4', status: 'queued' } });
});
afterEach(() => { cleanup(); client?.clear(); });

describe('unfinished stage continuation', () => {
  it('submits only decomposition and displays its background log without executing the stage', async () => {
    const p = props(); mount(p);
    fireEvent.click(screen.getByRole('button', { name: /继续拆解/ }));
    expect(await screen.findByText('日志 plan_decompose:split4')).toBeInTheDocument();
    expect(api.decomposeTask).toHaveBeenCalledTimes(1);
    expect(api.decomposeTask).toHaveBeenCalledWith(4, {
      plan_id: 185, expand_depth: 1, allow_existing_children: false, async_mode: true,
    });
    expect(screen.queryByText('执行对话框')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /继续拆解/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /继续拆解/ }));
    expect(api.decomposeTask).toHaveBeenCalledTimes(1);
    expect(p.onRefresh).toHaveBeenCalledTimes(1);
    act(() => { window.dispatchEvent(new CustomEvent('tasksUpdated', { detail: {
      type: 'plan_jobs_completed', plan_id: 185, job_id: 'split4', status: 'failed',
    } })); });
    expect(screen.getByRole('button', { name: /继续拆解/ })).toBeEnabled();
  });
  it('allows execution for an atomic task or a stage that already has children', () => {
    const p = props(); p.tasks = [stage, { ...stage, id: 8, parent_id: 4, task_type: 'atomic' }];
    mount(p);
    expect(screen.queryByRole('button', { name: /继续拆解/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /执行任务/ }));
    expect(screen.getByText('执行对话框')).toBeInTheDocument();
    expect(api.decomposeTask).not.toHaveBeenCalled();
  });
  it('leaves failed submissions retryable and shows the concrete error', async () => {
    api.decomposeTask.mockRejectedValue(new Error('服务暂时不可用'));
    mount(props());
    fireEvent.click(screen.getByRole('button', { name: /继续拆解/ }));
    expect(await screen.findByText('服务暂时不可用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /继续拆解/ })).toBeEnabled();
  });
  it('blocks a stage in a foreign-source session', () => {
    mount({ ...props(), executionBlocked: true } as any);
    expect(screen.getByRole('button', { name: /继续拆解/ })).toBeDisabled();
    expect(api.decomposeTask).not.toHaveBeenCalled();
  });
  it('ignores a late submission after switching session, even with matching plan and task IDs', async () => {
    let finish!: (value: Awaited<ReturnType<typeof planTreeApi.decomposeTask>>) => void;
    api.decomposeTask.mockReturnValue(new Promise(resolve => { finish = resolve; }));
    const p = props(); const mounted = mount(p);
    fireEvent.click(screen.getByRole('button', { name: /继续拆解/ }));
    const next = { ...p, sessionId: 's2', onRefresh: vi.fn() };
    mounted.rerenderProps(next);
    await act(async () => { finish({ success: true, message: 'Queued', result: {}, job: { job_id: 'old', status: 'queued' } }); });
    expect(screen.queryByText('日志 plan_decompose:old')).not.toBeInTheDocument();
    expect(p.onRefresh).not.toHaveBeenCalled();
    expect(next.onRefresh).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /继续拆解/ })).toBeEnabled();
  });
});
