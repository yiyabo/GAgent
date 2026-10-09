import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { App as AntdApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { planTreeApi } from '@api/planTree';
import { useChatStore } from '@store/chat';
import { useTasksStore } from '@store/tasks';
import type { PlanResultItem, PlanTreeResponse } from '@/types';
import PlanWorkspace from './PlanWorkspace';
vi.mock('@api/planTree', () => ({ planTreeApi: {
  listPlans: vi.fn(), getPlanTree: vi.fn(), getBackgroundTaskBoard: vi.fn(), getTaskResult: vi.fn(),
} }));
vi.mock('../planArtifacts/PlanTaskArtifacts', () => ({ default: (p: {planId: number; taskId?: number; sessionId: string}) => <div>产物范围 {p.planId}:{p.taskId ?? 'all'}:{p.sessionId}</div> }));
vi.mock('../ArtifactsPanel', () => ({ default: ({sessionId}: {sessionId: string}) => <div>会话全部文件 {sessionId}</div> }));
vi.mock('../ExecutorPanel', () => ({ default: () => <div>后台任务列表</div> }));
vi.mock('@components/tasks/detail/TaskExecuteModal', () => ({ default: () => null }));
vi.mock('@components/tasks/detail/TaskDetailSections', () => ({ ExecutionResult: ({taskResult}: {taskResult: PlanResultItem}) => <div>{taskResult?.content}</div> }));
vi.mock('@components/chat/JobLogPanel', () => ({ default: ({jobId}: {jobId: string}) => <div>日志 {jobId}</div> }));
vi.mock('@components/dag/PlanTreeVisualization', () => ({ default: () => <div>关系图</div> }));
vi.mock('@components/dag/DAG3DView', () => ({ default: () => null }));
vi.mock('@components/tasks/detail/TodoListPanel', () => ({ default: () => null }));
const api = vi.mocked(planTreeApi);
const tree = (id: number): PlanTreeResponse => ({ id, title: `计划 ${id}`, nodes: {
  '1': { id: 1, plan_id: id, name: `目标 ${id}`, status: 'running', depth: 0, parent_id: null },
  '2': { id: 2, plan_id: id, name: `筛选 ${id}`, instruction: `仅用于计划 ${id}`, status: 'running', depth: 1, parent_id: 1, dependencies: [3] },
  '3': { id: 3, plan_id: id, name: `检索 ${id}`, status: 'completed', depth: 1, parent_id: 1 },
}, adjacency: { '1': [2, 3] } });
let client: QueryClient;
const mount = () => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } });
  return render(<QueryClientProvider client={client}><AntdApp><PlanWorkspace /></AntdApp></QueryClientProvider>);
};
beforeEach(() => {
  vi.clearAllMocks();
  useChatStore.setState({ currentPlanId: 1, currentPlanTitle: '计划 1', currentSession: { id: 's1', session_id: 's1', plan_id: 1 } as any, messages: [] });
  useTasksStore.setState({ tasks: [], selectedTask: null, selectedTaskId: null, isTaskDrawerOpen: false, taskResultCache: {} });
  api.listPlans.mockResolvedValue([{id: 1, title: '计划 1', task_count: 3}, {id: 2, title: '计划 2', task_count: 3}]);
  api.getPlanTree.mockImplementation(async (id) => tree(id));
  api.getBackgroundTaskBoard.mockResolvedValue({ total: 0, generated_at: '', groups: {} } as any);
  api.getTaskResult.mockImplementation(async (id, taskId) => ({ task_id: taskId, status: 'running', content: `结果 ${id}:${taskId}` }));
});
afterEach(() => { cleanup(); client?.clear(); });
describe('plan workspace navigation and async isolation', () => {
  it('retains selected plan while task tree loads', async () => {
    let finish!: (value: PlanTreeResponse) => void;
    api.getPlanTree.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    mount();
    expect(useChatStore.getState().currentPlanId).toBe(1);
    await act(async () => { finish(tree(1)); });
    expect(await screen.findByRole('heading', { name: '筛选 1' })).toBeInTheDocument();
    expect(useChatStore.getState().currentPlanId).toBe(1);
    expect(screen.getByText('1 / 2 个执行任务已完成')).toBeInTheDocument();
  });
  it('shows the bound plan read-only and never offers a plan switcher', async () => {
    mount();
    expect(await screen.findByRole('heading', { name: '计划 1' })).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(api.listPlans).not.toHaveBeenCalled();
    expect(useChatStore.getState().currentPlanId).toBe(1);
  });
  // Real AntD visibility queries and two task mounts can exceed 5s on shared CI runners.
  it('selects dependency inline and preserves drawer actions', async () => {
    mount();
    await screen.findByRole('heading', { name: '筛选 1' });
    fireEvent.click(screen.getByRole('button', { name: '检索 1' }));
    expect(await screen.findByRole('heading', { name: '检索 1' })).toBeInTheDocument();
    expect(screen.getByText('产物范围 1:3:s1')).toBeInTheDocument();
    expect(useChatStore.getState().currentTaskId).toBe(3);
    expect(useTasksStore.getState().isTaskDrawerOpen).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: /完整详情/ }));
    expect(useTasksStore.getState().isTaskDrawerOpen).toBe(true);
    expect(useTasksStore.getState().selectedTaskId).toBe(3);
  }, 10_000);
  it('ignores late previous-plan result when task IDs collide', async () => {
    let finishOld!: (value: PlanResultItem) => void;
    api.getTaskResult.mockImplementation((id, taskId) => id === 1 ? new Promise((resolve) => { finishOld = resolve; })
      : Promise.resolve({task_id: taskId, content: '新计划结果'}));
    mount();
    await screen.findByRole('heading', {name: '筛选 1'});
    await act(async () => { useChatStore.setState({currentPlanId: 2, currentPlanTitle: '计划 2'}); });
    expect(await screen.findByText('新计划结果')).toBeInTheDocument();
    await act(async () => { finishOld({task_id: 2, content: '旧计划结果'}); });
    expect(screen.queryByText('旧计划结果')).not.toBeInTheDocument();
    expect(screen.getByText('产物范围 2:2:s1')).toBeInTheDocument();
  });
  it('keeps a foreign-source plan readable while blocking new execution entries', async () => {
    const foreign = tree(1);
    foreign.metadata = {artifact_store_ref: {session_id: 'source-session'}};
    foreign.nodes['2'].status = 'pending';
    foreign.nodes['1'].status = 'pending';
    api.getPlanTree.mockResolvedValue(foreign);
    mount();
    await screen.findByRole('heading', {name: '筛选 1'});
    expect(screen.getByRole('button', {name: /完整详情/})).toBeDisabled();
    expect(screen.getByRole('button', {name: /执行任务/})).toBeDisabled();
    expect(screen.getByRole('button', {name: '打开来源会话'})).toBeEnabled();
    expect(screen.getByText('产物范围 1:2:s1')).toBeInTheDocument();
    expect(useChatStore.getState().currentPlanId).toBe(1);
  });
  it('keeps standalone chat files with no bound plan', async () => {
    useChatStore.setState({currentPlanId: null, currentPlanTitle: null});
    mount();
    fireEvent.click(screen.getByRole('tab', {name: /产物/}));
    expect(await screen.findByText('会话全部文件 s1')).toBeInTheDocument();
    expect(api.getPlanTree).not.toHaveBeenCalled();
  });
  it('refreshes selected result on task terminal transition', async () => {
    mount();
    await screen.findByText('结果 1:2');
    api.getTaskResult.mockResolvedValue({task_id: 2, status: 'completed', content: '已完成的新结果'});
    const finalTree = tree(1); finalTree.nodes['2'].status = 'completed';
    await act(async () => { client.setQueryData(['planTree', 'tree', 1], finalTree); });
    expect(await screen.findByText('已完成的新结果')).toBeInTheDocument();
  });
});
