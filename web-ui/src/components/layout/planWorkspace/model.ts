import type { BackgroundTaskBoardResponse, BackgroundTaskItem, PlanTaskNode } from '@/types';
import { parseServerTimestampMs } from '@utils/serverTime';

export const statusLabels: Record<string, string> = {
  pending: '待执行', queued: '排队中', running: '执行中', in_progress: '执行中',
  completed: '已完成', succeeded: '已完成', failed: '失败', error: '失败',
  blocked: '等待依赖', skipped: '已跳过', cancelled: '已取消', canceled: '已取消',
  paused: '已暂停', stale: '待更新', unknown: '来源未追踪', reconciling: '同步中',
};
export const statusLabel = (status: string) => statusLabels[status] ?? status;
export const taskStatus = (task: PlanTaskNode) => task.effective_status || task.status;
export const isActiveJob = (job: BackgroundTaskItem) => ['running', 'in_progress', 'queued'].includes(job.status);

export function defaultTaskSelection(tasks: PlanTaskNode[]): PlanTaskNode | undefined {
  const leaf = (task: PlanTaskNode) => task.task_type === 'atomic';
  return tasks.find((task) => leaf(task) && taskStatus(task) === 'running')
    ?? tasks.find(leaf)
    ?? tasks.find((task) => task.task_type !== 'root' && taskStatus(task) === 'running')
    ?? tasks.find((task) => task.task_type !== 'root')
    ?? tasks[0];
}

export interface OutlineRow { task: PlanTaskNode; depth: number; hasChildren: boolean }
/** Keep parent ordering and disconnected/cyclic nodes visible without recursion loops. */
export function buildTaskOutline(tasks: PlanTaskNode[]): OutlineRow[] {
  const ids = new Set(tasks.map((task) => task.id));
  const compare = (a: PlanTaskNode, b: PlanTaskNode) => (a.position ?? a.id) - (b.position ?? b.id) || a.id - b.id;
  const children = new Map<number, PlanTaskNode[]>();
  for (const task of tasks) {
    if (task.parent_id != null && ids.has(task.parent_id) && task.parent_id !== task.id) {
      children.set(task.parent_id, [...(children.get(task.parent_id) ?? []), task].sort(compare));
    }
  }
  const rows: OutlineRow[] = [];
  const visited = new Set<number>();
  const visit = (task: PlanTaskNode, depth: number) => {
    if (visited.has(task.id)) return;
    visited.add(task.id);
    rows.push({ task, depth, hasChildren: Boolean(children.get(task.id)?.length) });
    children.get(task.id)?.forEach((child) => visit(child, depth + 1));
  };
  tasks.filter((task) => task.parent_id == null || !ids.has(task.parent_id)).sort(compare).forEach((task) => visit(task, 0));
  [...tasks].sort(compare).forEach((task) => visit(task, 0));
  return rows;
}

export function scopedPlanJobs(board: BackgroundTaskBoardResponse | undefined, planId: number): BackgroundTaskItem[] {
  const seen = new Set<string>();
  return Object.values(board?.groups ?? {}).flatMap((group) => group.items ?? [])
    .filter((job) => {
      if (job.plan_id !== planId || seen.has(job.job_id)) return false;
      seen.add(job.job_id);
      return true;
    })
    .sort((a, b) => (parseServerTimestampMs(b.created_at) ?? 0) - (parseServerTimestampMs(a.created_at) ?? 0));
}

export function selectTaskJob(jobs: BackgroundTaskItem[], taskId: number): BackgroundTaskItem | undefined {
  // A full-plan job is intentionally not presented as one task's private run.
  return jobs.find((job) => job.current_task_id === taskId && isActiveJob(job))
    ?? jobs.find((job) => job.current_task_id === taskId);
}

export function displayTime(value?: string | null): string {
  const ms = parseServerTimestampMs(value);
  return ms == null ? '时间未记录' : new Date(ms).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  });
}
