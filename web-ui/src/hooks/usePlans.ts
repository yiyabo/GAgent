import { useQuery } from '@tanstack/react-query';
import { plansApi } from '@api/plans';
import { tasksApi } from '@api/tasks';
import type { PlanTaskNode } from '@/types';
import type { ScopeOverrides } from '@api/scope';

interface WorkflowFilter {
  sessionId?: string;
  workflowId?: string;
  planTitle?: string;
}

export const usePlanTitles = (filters?: WorkflowFilter) => {
  return useQuery<string[]>({
    queryKey: ['workflows', 'titles', filters?.sessionId, filters?.workflowId],
    queryFn: async () => {
      const overrides: ScopeOverrides = {
        session_id: filters?.sessionId ?? undefined,
        workflow_id: filters?.workflowId ?? undefined,
      };

      const [planTitles, scopedTasks] = await Promise.all([
        plansApi
          .listPlanTitles(overrides)
          .catch(() => []),
        tasksApi.getAllTasks(overrides),
      ]);

      const titles = new Set<string>();

      planTitles.forEach((title) => {
        if (title) titles.add(title);
      });

      scopedTasks
        .filter((task) => task.task_type === 'root' && task.name)
        .forEach((task) => titles.add(task.name as string));

      return Array.from(titles);
    },
    enabled: true,
    staleTime: 60_000,
    refetchOnWindowFocus: false, // 窗口聚焦时不重新获取
    refetchOnMount: false, // 组件挂载时不重新获取（如果有缓存）
  });
};

export const usePlanTasks = (filters?: WorkflowFilter) => {
  return useQuery<PlanTaskNode[]>({
    queryKey: ['workflows', 'tasks', filters?.sessionId, filters?.workflowId, filters?.planTitle],
    queryFn: async () => {
      const overrides: ScopeOverrides = {
        session_id: filters?.sessionId ?? undefined,
        workflow_id: filters?.workflowId ?? undefined,
      };
      const planTitle = filters?.planTitle;

      const tasks = await tasksApi.getAllTasks(overrides);

      const normalizeName = (name?: string | null) =>
        name?.replace(/^\[.*?\]\s*/, '').replace(/^ROOT[:：]\s*/, '') ?? name ?? '';

      // 🔍 关键修改：如果没有指定planTitle，只返回当前对话的ROOT任务树
      if (!planTitle) {
        // 字符串等值比较，避免类型不一致导致匹配失败
        const eq = (a?: string | number | null, b?: string | number | null) => String(a ?? '') === String(b ?? '');
        const typedRoots = tasks.filter((t) => t.task_type === 'root');
        const roots = typedRoots.length > 0 ? typedRoots : tasks.filter((t) => t.parent_id == null);

        // 1) 优先按 session_id 匹配
        let pickedRoot = roots.find((r) => eq(r.session_id, overrides.session_id));

        // 2) 其次按 workflow_id 匹配
        if (!pickedRoot) {
          pickedRoot = roots.find((r) => eq(r.workflow_id, overrides.workflow_id));
        }

        // 3) 若只有一个 ROOT，直接采用
        if (!pickedRoot && roots.length === 1) {
          pickedRoot = roots[0];
        }

        // 4) 兜底：选择最新的 ROOT（按id最大）
        if (!pickedRoot && roots.length > 1) {
          pickedRoot = roots.reduce((acc, cur) => (cur.id > acc.id ? cur : acc));
        }

        if (!pickedRoot) {
          return [];
        }

        const visited = new Set<number>();
        const collectSubtree = (parentId: number): PlanTaskNode[] => {
          if (visited.has(parentId)) return [];
          const parent = tasks.find((task) => task.id === parentId);
          if (!parent) return [];
          visited.add(parentId);

          const parentNode: PlanTaskNode = {
            ...parent,
            short_name: normalizeName(parent.name),
          };

          const children = tasks
            .filter((task) => task.parent_id === parentId)
            .flatMap((child) => collectSubtree(child.id));

          return [parentNode, ...children];
        };

        return collectSubtree(pickedRoot.id);
      }

      const normalizedTarget = normalizeName(planTitle);
      const rootTask = tasks.find((task) =>
        task.task_type === 'root' &&
        normalizeName(task.name) === normalizedTarget
      );

      if (!rootTask) {
        return [];
      }

      const visited = new Set<number>();
      const collectSubtree = (parentId: number): PlanTaskNode[] => {
        if (visited.has(parentId)) return [];
        const parent = tasks.find((task) => task.id === parentId);
        if (!parent) return [];
        visited.add(parentId);

        const parentNode: PlanTaskNode = {
          ...parent,
          short_name: normalizeName(parent.name),
        };

        const children = tasks
          .filter((task) => task.parent_id === parentId)
          .flatMap((child) => collectSubtree(child.id));

        return [parentNode, ...children];
      };

      return collectSubtree(rootTask.id);
    },
    enabled: true,
    staleTime: 30_000, // 30秒内不重新获取
    refetchOnWindowFocus: false, // 窗口聚焦时不重新获取
    refetchOnMount: false, // 组件挂载时不重新获取（如果有缓存）
  });
};
