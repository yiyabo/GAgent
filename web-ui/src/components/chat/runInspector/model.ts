import type { TFn } from '@/i18n/useT';
import type {
  ChatRunInspectionTarget,
  PlanTaskRunInspectionTarget,
  RunInspection,
  RunInspectionStep,
  RunInspectionTarget,
} from '@/types/runInspection';

export type StepFilter = 'all' | 'tools' | 'failures';

const RUN_ID_KEYS = ['chat_run_id', 'run_id', 'deep_think_job_id'] as const;
const MESSAGE_ID_KEYS = ['backend_id', 'message_id'] as const;

function positiveInteger(value: unknown): number | null {
  const parsed = typeof value === 'string' && /^\d+$/.test(value.trim()) ? Number(value.trim()) : value;
  return typeof parsed === 'number' && Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

/**
 * Resolve which run produced an assistant message: a live/persisted run id first, then the persisted
 * message id. Any non-blank run id is accepted; the server decides whether it exists.
 */
export function resolveRunTarget(metadata: Record<string, unknown> | undefined | null): ChatRunInspectionTarget | null {
  if (!metadata) return null;
  for (const key of RUN_ID_KEYS) {
    const value = metadata[key];
    if (typeof value === 'string' && value.trim()) return { run_id: value.trim() };
  }
  for (const key of MESSAGE_ID_KEYS) {
    const id = positiveInteger(metadata[key]);
    if (id !== null) return { message_id: id };
  }
  return null;
}

export function isPlanTaskTarget(target: RunInspectionTarget | null | undefined): target is PlanTaskRunInspectionTarget {
  return Boolean(target && 'planId' in target && 'taskId' in target);
}

/** A task has something to inspect once it has left `pending` or has recorded output. */
export function taskHasRun(task: { status?: string | null; execution_result?: string | null }): boolean {
  return (typeof task.status === 'string' && task.status !== '' && task.status !== 'pending') || Boolean(task.execution_result);
}

/** "1 step · 1 tool step" / "3 steps · 2 tool steps", with the plural chosen per count. */
export function formatStepCounts(t: TFn, steps: number, toolSteps: number): string {
  const stepText = t(steps === 1 ? 'runInspector.stepCountOne' : 'runInspector.stepCountOther', { count: steps });
  const toolText = t(toolSteps === 1 ? 'runInspector.toolStepCountOne' : 'runInspector.toolStepCountOther', { count: toolSteps });
  return `${stepText} · ${toolText}`;
}

export function filterSteps(steps: RunInspectionStep[], filter: StepFilter, tool: string | null): RunInspectionStep[] {
  return steps.filter((step) => {
    if (filter === 'tools' && step.kind !== 'tool') return false;
    if (filter === 'failures' && step.success !== false) return false;
    if (tool && !step.tools.some((call) => call.tool === tool)) return false;
    return true;
  });
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest.toString().padStart(2, '0')}s`;
}

export function formatTokens(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 10_000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}

export function stepTone(step: RunInspectionStep): 'success' | 'error' | 'processing' | 'default' {
  if (step.success === false || step.status === 'error') return 'error';
  if (step.success === true || step.status === 'done') return 'success';
  if (step.status && !['done', 'analyzing'].includes(step.status) && !step.finished_at) return 'processing';
  return 'default';
}

/** Shares the exact payload the server returned, so an export matches what was inspected. */
export function exportFileName(inspection: RunInspection): string {
  return `run-${inspection.run.run_id}.json`;
}

export function exportJson(inspection: RunInspection): string {
  return JSON.stringify(inspection, null, 2);
}

export function exportBlob(inspection: RunInspection): Blob {
  return new Blob([exportJson(inspection)], { type: 'application/json' });
}
