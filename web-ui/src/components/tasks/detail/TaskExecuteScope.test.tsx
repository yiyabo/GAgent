import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { App as AntdApp } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { planTreeApi } from '@api/planTree';
import TaskExecuteModal from './TaskExecuteModal';

vi.mock('@api/planTree', () => ({ planTreeApi: { getTaskDependencyPlan: vi.fn(), executeTaskWithDeps: vi.fn() } }));
vi.mock('@components/chat/JobLogPanel', () => ({ default: ({ jobId }: { jobId: string }) => <span>{jobId}</span> }));
vi.mock('./TaskDetailSections', () => ({ resolveTaskName: (id: number) => `Task ${id}`, resolveTaskStatus: () => 'pending' }));
const dependencies = { plan_id: 1, target_task_id: 1, closure_dependencies: [], missing_dependencies: [], running_dependencies: [], cycle_detected: false, execution_items: [], execution_order: [], direct_dependencies: [], satisfied_statuses: [], cycle_paths: [] };
const callbacks = { onClose: vi.fn(), onExecutionStarted: vi.fn(), handleDependencyClick: vi.fn(), refetchPlanTasks: vi.fn(), refetchTaskResult: vi.fn() };
const component = (planId = 1) => <AntdApp><TaskExecuteModal {...callbacks} open currentPlanId={planId} currentSessionId={`s${planId}`} selectedTaskId={1} activeTask={null} taskMap={new Map()} isTaskDrawerOpen /></AntdApp>;
beforeEach(() => { vi.clearAllMocks(); vi.mocked(planTreeApi.getTaskDependencyPlan).mockResolvedValue(dependencies); });

describe('execution callback ownership', () => {
  it('ignores the old execution response after changing plan with the same task id', async () => {
    let finish!: (value: any) => void;
    vi.mocked(planTreeApi.executeTaskWithDeps).mockReturnValue(new Promise(resolve => { finish = resolve; }));
    const view = render(component());
    await waitFor(() => expect(screen.getByRole('button', { name: 'Execute task' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Execute task' }));
    await waitFor(() => expect(planTreeApi.executeTaskWithDeps).toHaveBeenCalled());
    view.rerender(component(2));
    await act(async () => { finish({ success: true, job: { job_id: 'old-job' } }); });
    expect(callbacks.onExecutionStarted).not.toHaveBeenCalled();
    expect(callbacks.refetchPlanTasks).not.toHaveBeenCalled();
    expect(screen.queryByText('old-job')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Execute task' })).toBeEnabled());
  });

  it('does not replace a new dependency plan with a late earlier response', async () => {
    let finish!: (value: any) => void;
    vi.mocked(planTreeApi.getTaskDependencyPlan).mockImplementation(plan => plan === 1 ? new Promise(resolve => { finish = resolve; }) : Promise.resolve({ ...dependencies, plan_id: 2 }));
    const view = render(component());
    await waitFor(() => expect(planTreeApi.getTaskDependencyPlan).toHaveBeenCalled());
    view.rerender(component(2));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Execute task' })).toBeEnabled());
    await act(async () => { finish({ ...dependencies, cycle_detected: true }); });
    expect(screen.queryByText('Dependency cycle detected')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Execute task' })).toBeEnabled();
  });
});
