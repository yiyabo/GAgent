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
});
