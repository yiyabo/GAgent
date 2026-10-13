import { describe, it, expect } from 'vitest';
import { planTreeToTasks } from './planTree';

describe('planning hierarchy', () => {
  it('preserves a failed composite instead of presenting it as an atomic leaf', () => {
    const tree = { id: 185, metadata: { plan_generation: { decomposition_status: 'partial', decomposition: { failed_nodes: [4] } } },
      nodes: {
        '1': { id: 1, name: 'Root', parent_id: null, status: 'pending' },
        '4': { id: 4, name: 'Write chapters', parent_id: 1, status: 'pending', metadata: { task_type: 'composite' } },
        '5': { id: 5, name: 'Existing leaf', parent_id: 1, status: 'pending' },
      } } as any;
    const tasks = planTreeToTasks(tree);
    expect(tasks.map(task => task.task_type)).toEqual(['root', 'composite', 'atomic']);
    expect(tasks[1].status_reason).toContain('拆解尚未完成');
  });
  it('clears the historical missing-children reason after a stage has been decomposed', () => {
    const tree = { id: 185, metadata: { plan_generation: { decomposition: { failed_nodes: [4] } } }, nodes: {
      '1': { id: 1, name: 'Root', parent_id: null, status: 'pending' },
      '4': { id: 4, name: 'Stage', parent_id: 1, status: 'pending', metadata: { task_type: 'composite', decomposition_status: 'completed' } },
      '8': { id: 8, name: 'New atomic', parent_id: 4, status: 'pending', metadata: { task_type: 'atomic' } },
    } } as any;
    expect(planTreeToTasks(tree).find(task => task.id === 4)).toMatchObject({ task_type: 'composite', status_reason: null });
  });
});
