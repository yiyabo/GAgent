import type {
  RunInspection,
  RunInspectionStep,
  RunInspectionTarget,
} from '@/types/runInspection';

export type StepFilter = 'all' | 'tools' | 'failures';

/** Resolve which run produced an assistant message: live id first, then the persisted message id. */
export function resolveRunTarget(metadata: Record<string, unknown> | undefined | null): RunInspectionTarget | null {
  if (!metadata) return null;
  for (const key of ['chat_run_id', 'deep_think_job_id'] as const) {
    const value = metadata[key];
    if (typeof value === 'string' && value.startsWith('dt_')) return { run_id: value };
  }
  const backendId = metadata.backend_id;
  if (typeof backendId === 'number' && Number.isInteger(backendId) && backendId > 0) return { message_id: backendId };
  return null;
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
