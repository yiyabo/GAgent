import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { planTreeApi } from '../api/planTree';
import { usePlanTasks, usePlanTree } from './usePlans';
import type { PlanTreeResponse } from '../types';

vi.mock('@api/planTree', () => ({
  planTreeApi: {
    getPlanTree: vi.fn(),
  },
}));

const tree: PlanTreeResponse = {
  id: 42,
  title: 'Shared Query Plan',
  nodes: {
    '1': {
      id: 1,
      plan_id: 42,
      name: 'Root',
      status: 'completed',
      parent_id: null,
      depth: 0,
    },
    '2': {
      id: 2,
      plan_id: 42,
      name: 'Leaf',
      status: 'pending',
      parent_id: 1,
      depth: 1,
    },
  },
  adjacency: {
    '1': [2],
    '2': [],
  },
};

const renderWithClient = (children: React.ReactElement) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
};

const Probe = () => {
  const treeQuery = usePlanTree(42);
  const tasksQuery = usePlanTasks({ planId: 42 });

  return (
    <div>
      <span data-testid="title">{treeQuery.data?.title ?? 'loading'}</span>
      <span data-testid="tasks">{tasksQuery.data?.map((task) => task.short_name).join(',') ?? 'loading'}</span>
    </div>
  );
};

describe('usePlans', () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('shares one plan-tree request between tree and task hooks', async () => {
    vi.mocked(planTreeApi.getPlanTree).mockResolvedValue(tree);

    renderWithClient(<Probe />);

    await waitFor(() => {
      expect(screen.getByTestId('title').textContent).toBe('Shared Query Plan');
      expect(screen.getByTestId('tasks').textContent).toBe('Root,Leaf');
    });

    expect(planTreeApi.getPlanTree).toHaveBeenCalledTimes(1);
    expect(planTreeApi.getPlanTree).toHaveBeenCalledWith(42);
  });
});
