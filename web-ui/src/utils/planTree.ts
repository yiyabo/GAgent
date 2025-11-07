import type { PlanNodeResponse, PlanTreeResponse, Task } from '@/types';

export function planTreeToTasks(tree: PlanTreeResponse): Task[] {
  const adjacency = tree.adjacency || {};
  const childrenLookup = new Map<number, number[]>();

  Object.entries(adjacency).forEach(([key, value]) => {
    if (!Array.isArray(value)) {
      return;
    }
    const parentId = key === 'null' ? null : Number(key);
    if (parentId == null) {
      return;
    }
    childrenLookup.set(parentId, value.map((child) => Number(child)));
  });

  return Object.values(tree.nodes || {}).map((node: PlanNodeResponse) => {
    const id = Number(node.id);
    const childIds = childrenLookup.get(id) ?? [];
    const isRoot = node.parent_id == null;
    const hasChildren = childIds.length > 0;
    const taskType: Task['task_type'] = isRoot ? 'root' : hasChildren ? 'composite' : 'atomic';

    const metadata = (node.metadata as Record<string, any>) || {};
    const rawStatus = typeof node.status === 'string' ? node.status.toLowerCase() : 'pending';
    let status: Task['status'];
    switch (rawStatus) {
      case 'running':
        status = 'running';
        break;
      case 'success':
      case 'completed':
        status = 'completed';
        break;
      case 'failed':
        status = 'failed';
        break;
      case 'skipped':
        status = 'skipped';
        break;
      default:
        status = 'pending';
    }

    return {
      id,
      name: node.name,
      status,
      parent_id: node.parent_id ?? undefined,
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
}
