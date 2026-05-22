import { render, screen } from '@testing-library/react';
import React from 'react';
import { describe, expect, it } from 'vitest';

import PlanTreeVisualization from './PlanTreeVisualization';
import type { PlanTaskNode } from '../../types';

const makeTask = (id: number, name: string, status: PlanTaskNode['status']): PlanTaskNode => ({
  id,
  name,
  short_name: name,
  status,
  parent_id: id === 1 ? undefined : 1,
  task_type: id === 1 ? 'root' : 'atomic',
  depth: id === 1 ? 0 : 1,
});

describe('PlanTreeVisualization', () => {
  it('keeps pending, blocked, and failed statuses visually distinct', () => {
    render(
      <PlanTreeVisualization
        tasks={[
          makeTask(1, 'Root', 'completed'),
          makeTask(2, 'Pending task', 'pending'),
          makeTask(3, 'Blocked task', 'blocked'),
          makeTask(4, 'Failed task', 'failed'),
        ]}
      />
    );

    const pendingDot = screen.getByTitle('pending');
    const blockedDot = screen.getByTitle('blocked');
    const failedDot = screen.getByTitle('failed');

    expect(pendingDot.classList.contains('status-pending')).toBe(true);
    expect(blockedDot.classList.contains('status-blocked')).toBe(true);
    expect(failedDot.classList.contains('status-failed')).toBe(true);
    expect(blockedDot.classList.contains('status-failed')).toBe(false);
    expect(pendingDot.classList.contains('status-failed')).toBe(false);
  });
});
