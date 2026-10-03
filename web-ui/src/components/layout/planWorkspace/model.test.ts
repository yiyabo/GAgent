import { describe, expect, it } from 'vitest';
import type { BackgroundTaskBoardResponse, BackgroundTaskItem, PlanTaskNode } from '@/types';
import { defaultTaskSelection, buildTaskOutline, scopedPlanJobs, selectTaskJob, taskStatus } from './model';
const task = (id: number, parent_id?: number): PlanTaskNode => ({ id, parent_id, name: `Task ${id}`, status: 'pending' });
const job = (job_id: string, plan_id: number, current_task_id?: number): BackgroundTaskItem => ({
  job_id, plan_id, current_task_id, status: 'running', category: 'code_executor', job_type: 'plan_execute', label: job_id,
});
describe('plan workspace scope and outline', () => {
  it('selects the active leaf before its running root or other leaf', () => {
    const root = { ...task(1), task_type: 'root', status: 'running' } as PlanTaskNode;
    const ready = { ...task(2, 1), task_type: 'atomic' } as PlanTaskNode;
    const active = { ...task(3, 1), task_type: 'atomic', status: 'running' } as PlanTaskNode;
    expect(defaultTaskSelection([root, ready, active])?.id).toBe(3);
    expect(defaultTaskSelection([root, ready])?.id).toBe(2);
    expect(defaultTaskSelection([root])?.id).toBe(1);
  });
  it('orders parents first while preserving disconnected and cyclic tasks', () => {
    const rows = buildTaskOutline([task(3, 2), task(2, 1), task(1), task(4, 99), task(5, 6), task(6, 5)]);
    expect(rows.map((row) => row.task.id)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(rows.slice(0, 3).map((row) => row.depth)).toEqual([0, 1, 2]);
  });
  it('uses effective status instead of stale stored completion', () => {
    expect(taskStatus({ ...task(1), status: 'completed', effective_status: 'blocked' })).toBe('blocked');
  });
  it('filters foreign plans and deduplicates board groups', () => {
    const first = job('first', 1, 2);
    const board = { groups: {
      code_executor: { items: [first, job('foreign', 2, 2), job('unknown', undefined as unknown as number, 2)] },
      task_creation: { items: [first] },
    } } as unknown as BackgroundTaskBoardResponse;
    expect(scopedPlanJobs(board, 1).map((item) => item.job_id)).toEqual(['first']);
  });
  it('does not attribute an unrelated full-plan job to a task', () => {
    expect(selectTaskJob([{ ...job('full', 1, 8), mode: 'full_plan' }], 2)).toBeUndefined();
    expect(selectTaskJob([job('owned', 1, 2)], 2)?.job_id).toBe('owned');
  });
  it('prefers the active matching run over history', () => {
    expect(selectTaskJob([{ ...job('old', 1, 2), status: 'completed' }, job('current', 1, 2)], 2)?.job_id).toBe('current');
  });
});
