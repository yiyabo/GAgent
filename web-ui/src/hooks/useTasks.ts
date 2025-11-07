import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { planTreeApi } from '@api/planTree';
import { planTreeToTasks } from '@utils/planTree';
import { useTasksStore } from '@store/tasks';
import type { Task } from '@/types';

interface UsePlanTreeOptions {
  planId?: number | null;
  enabled?: boolean;
  refetchIntervalMs?: number;
}

const isValidPlanId = (value: number | null | undefined): value is number =>
  typeof value === 'number' && value >= 0;

/**
 * 订阅完整的 PlanTree，并将任务同步到全局任务 store。
 */
export const usePlanTreeTasks = ({
  planId,
  enabled = true,
  refetchIntervalMs,
}: UsePlanTreeOptions) => {
  const { setTasks } = useTasksStore();

  const query = useQuery({
    queryKey: ['planTree', 'full', planId ?? null],
    enabled: enabled && isValidPlanId(planId),
    queryFn: async () => {
      if (!isValidPlanId(planId)) {
        return [] as Task[];
      }
      const tree = await planTreeApi.getPlanTree(planId);
      return planTreeToTasks(tree);
    },
    refetchOnWindowFocus: false,
    staleTime: 30_000,
    refetchInterval: refetchIntervalMs ?? false,
  });

  useEffect(() => {
    if (query.data) {
      setTasks(query.data);
    } else if (query.isFetched && !query.isFetching) {
      setTasks([]);
    }
  }, [query.data, query.isFetched, query.isFetching, setTasks]);

  return query;
};

interface UsePlanSubgraphOptions {
  planId?: number | null;
  nodeId?: number | null;
  maxDepth?: number;
  enabled?: boolean;
}

/**
 * 获取计划子图（局部 PlanTree）。
 */
export const usePlanSubgraph = ({
  planId,
  nodeId,
  maxDepth = 2,
  enabled = true,
}: UsePlanSubgraphOptions) => {
  return useQuery({
    queryKey: ['planTree', 'subgraph', planId ?? null, nodeId ?? null, maxDepth],
    enabled: enabled && isValidPlanId(planId) && typeof nodeId === 'number',
    queryFn: async () => {
      if (!isValidPlanId(planId) || typeof nodeId !== 'number') {
        return [] as Task[];
      }
      const subgraph = await planTreeApi.getPlanSubgraph(planId, nodeId, maxDepth);
      const nodes = Array.isArray(subgraph.nodes) ? subgraph.nodes : [];
      return nodes.map((node: any) => {
        const hasChildren = nodes.some((candidate) => candidate.parent_id === node.id);
        const taskType: Task['task_type'] =
          node.parent_id == null ? 'root' : hasChildren ? 'composite' : 'atomic';
        return {
          id: Number(node.id),
          name: node.name,
          status: 'pending',
          parent_id: node.parent_id ?? undefined,
          depth: node.depth ?? 0,
          path: node.path ?? undefined,
          task_type: taskType,
        } as Task;
      });
    },
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });
};
