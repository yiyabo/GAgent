import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { planTreeApi } from '@api/planTree';
import { planTreeToTasks } from '@utils/planTree';
import type {
  PlanExecutionSummary,
  PlanResultItem,
  PlanResultsResponse,
  PlanSummary,
  PlanTaskNode,
  Task,
} from '@/types';

const deriveShortName = (name?: string | null) =>
  name?.replace(/^\[.*?\]\s*/, '').replace(/^ROOT[:：]\s*/, '') ?? name ?? '';

export const usePlanSummaries = () => {
  return useQuery<PlanSummary[]>({
    queryKey: ['planTree', 'summaries'],
    queryFn: () => planTreeApi.listPlans(),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
};

export const usePlanTitles = () => {
  return useQuery<string[]>({
    queryKey: ['planTree', 'titles'],
    queryFn: async () => {
      const summaries = await planTreeApi.listPlans();
      return summaries.map((plan) => plan.title);
    },
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
};

interface PlanTasksOptions {
  planId?: number | null;
}

const asPlanTaskNodes = (tasks: Task[]): PlanTaskNode[] =>
  tasks.map((task) => ({
    ...task,
    short_name: deriveShortName(task.name),
  }));

export const usePlanTasks = (options: PlanTasksOptions) => {
  const { planId } = options;

  const query = useQuery({
    queryKey: ['planTree', 'tasks', planId ?? null],
    enabled: typeof planId === 'number',
    queryFn: async () => {
      if (planId == null) {
        return [] as PlanTaskNode[];
      }
      const tree = await planTreeApi.getPlanTree(planId);
      const tasks = planTreeToTasks(tree);
      return asPlanTaskNodes(tasks);
    },
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

  const sortedData = useMemo(() => {
    if (!query.data) {
      return [];
    }
    return [...query.data].sort((a, b) => {
      const depthDiff = (a.depth ?? 0) - (b.depth ?? 0);
      if (depthDiff !== 0) {
        return depthDiff;
      }
      return a.id - b.id;
    });
  }, [query.data]);

  return {
    ...query,
    data: sortedData,
  };
};

interface PlanResultsOptions {
  planId?: number | null;
  onlyWithOutput?: boolean;
}

export const usePlanResults = (options: PlanResultsOptions) => {
  const { planId, onlyWithOutput = true } = options;

  return useQuery<PlanResultsResponse>({
    queryKey: ['planTree', 'results', planId ?? null, onlyWithOutput],
    enabled: typeof planId === 'number',
    queryFn: async () => {
      if (planId == null) {
        return { plan_id: 0, total: 0, items: [] };
      }
      return planTreeApi.getPlanResults(planId, { onlyWithOutput });
    },
    staleTime: 15_000,
    refetchOnWindowFocus: false,
  });
};

export const usePlanExecutionSummary = (planId?: number | null) => {
  return useQuery<PlanExecutionSummary>({
    queryKey: ['planTree', 'execution', planId ?? null],
    enabled: typeof planId === 'number',
    queryFn: async () => {
      if (planId == null) {
        return Promise.reject(new Error('planId is required'));
      }
      return planTreeApi.getPlanExecutionSummary(planId);
    },
    staleTime: 10_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });
};
