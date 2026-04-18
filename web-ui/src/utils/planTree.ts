import type { PlanNodeResponse, PlanTreeResponse, Task } from '@/types';

const normalizeTaskStatus = (rawStatus?: string | null): Task['status'] => {
  const normalized = typeof rawStatus === 'string' ? rawStatus.toLowerCase() : 'pending';
  switch (normalized) {
    case 'running':
      return 'running';
    case 'success':
    case 'done':
    case 'completed':
      return 'completed';
    case 'failed':
    case 'error':
      return 'failed';
    case 'skipped':
      return 'skipped';
    case 'blocked':
      return 'blocked';
    default:
      return 'pending';
  }
};

export function planTreeToTasks(tree: PlanTreeResponse): Task[] {
  const nodes = Object.values(tree.nodes || {});
  
  const childrenByParent = new Map<number, number[]>();
  nodes.forEach((node: PlanNodeResponse) => {
    if (node.parent_id != null) {
      const parentId = Number(node.parent_id);
      if (!childrenByParent.has(parentId)) {
        childrenByParent.set(parentId, []);
      }
      childrenByParent.get(parentId)!.push(Number(node.id));
    }
  });

  const tasks = nodes.map((node: PlanNodeResponse) => {
    const id = Number(node.id);
    const childIds = childrenByParent.get(id) ?? [];
    const isRoot = node.parent_id == null;
    const hasChildren = childIds.length > 0;
    const taskType: Task['task_type'] = isRoot ? 'root' : hasChildren ? 'composite' : 'atomic';

    const metadata = (node.metadata as Record<string, any>) || {};
    const status = normalizeTaskStatus(node.effective_status ?? node.status);

    return {
      id,
      name: node.name,
      status,
      effective_status: node.effective_status ?? node.status ?? null,
      status_reason: node.status_reason ?? null,
      blocked_by_dependencies: node.blocked_by_dependencies ?? false,
      incomplete_dependencies: Array.isArray(node.incomplete_dependencies)
        ? node.incomplete_dependencies.map(Number).filter((dep) => !Number.isNaN(dep))
        : [],
      is_active_execution: node.is_active_execution ?? false,
      parent_id: node.parent_id != null ? Number(node.parent_id) : undefined,
      path: node.path ?? undefined,
      depth: node.depth ?? 0,
      task_type: taskType,
      plan_id: node.plan_id ?? tree.id ?? undefined,
      instruction: node.instruction ?? null,
      dependencies: Array.isArray(node.dependencies)
        ? node.dependencies.map(Number).filter((dep) => !Number.isNaN(dep))
        : [],
      position: node.position ?? undefined,
      context_combined: node.context_combined ?? null,
      context_sections: node.context_sections ?? [],
      context_meta: node.context_meta ?? {},
      context_updated_at: node.context_updated_at ?? null,
      execution_result: node.execution_result ?? null,
      created_at: undefined,
      updated_at: undefined,
      session_id: metadata.session_id ?? null,
      workflow_id: metadata.workflow_id ?? null,
      root_id: undefined,
    } as Task;
  });

  return tasks;
}
