import type { DecompositionJobStatus, JobLogEvent } from '@/types';

const toNumber = (value: unknown): number | null => {
  if (value === null || value === undefined) return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

const asRecord = (value: unknown): Record<string, any> =>
  value && typeof value === 'object' ? (value as Record<string, any>) : {};

const normalizeStatus = (raw: unknown): string => {
  const value = typeof raw === 'string' ? raw.trim().toLowerCase() : '';
  return value || 'queued';
};

const getLogs = (value: unknown): JobLogEvent[] =>
  Array.isArray(value) ? (value as JobLogEvent[]) : [];

export interface PlanDecomposeProgress {
  status: string;
  percent: number | null;
  totalBudget: number | null;
  consumedBudget: number | null;
  queueRemaining: number | null;
  createdCount: number | null;
  processedCount: number | null;
}

export interface PlanExecuteProgress {
  status: string;
  percent: number | null;
  totalSteps: number | null;
  executed: number;
  failed: number;
  skipped: number;
  doneSteps: number;
  currentStep: number | null;
  currentTaskId: number | null;
}

export interface ToolProgressSnapshot {
  tool: string | null;
  percent: number | null;
  status: string | null;
  phase: string | null;
  done: number | null;
  total: number | null;
}

type JobLike = Partial<DecompositionJobStatus> | Record<string, any> | null | undefined;

export const computePlanDecomposeProgress = (job: JobLike): PlanDecomposeProgress | null => {
  if (!job) return null;
  const status = normalizeStatus((job as any).status);
  const stats = asRecord((job as any).stats);
  const params = asRecord((job as any).params);
  const logs = getLogs((job as any).logs);

  const totalBudget = toNumber(params.node_budget) ?? toNumber(stats.node_budget);
  let remainingBudget: number | null = toNumber(stats.budget_remaining);
  let queueRemaining: number | null = toNumber(stats.queue_remaining);
  let createdCount: number | null = toNumber(stats.created_count ?? stats.createdCount);
  let processedCount: number | null = toNumber(stats.processed_count ?? stats.processedCount);

  for (let idx = logs.length - 1; idx >= 0; idx -= 1) {
    const metadata = asRecord(logs[idx]?.metadata);
    if (remainingBudget === null) {
      remainingBudget = toNumber(metadata.budget_remaining);
    }
    if (queueRemaining === null) {
      queueRemaining = toNumber(metadata.queue_remaining);
    }
    if (createdCount === null) {
      createdCount = toNumber(metadata.created_count ?? metadata.createdCount);
    }
    if (processedCount === null) {
      processedCount = toNumber(metadata.processed_count ?? metadata.processedCount);
    }
    if (
      (remainingBudget !== null || totalBudget === null) &&
      queueRemaining !== null &&
      createdCount !== null &&
      processedCount !== null
    ) {
      break;
    }
  }

  const consumedFromStats = toNumber(stats.consumed_budget);
  const consumedBudget =
    consumedFromStats ??
    (totalBudget !== null && remainingBudget !== null
      ? Math.max(0, totalBudget - remainingBudget)
      : createdCount !== null
        ? Math.max(0, Math.round(createdCount))
        : null);

  let percentRaw =
    totalBudget !== null && totalBudget > 0 && consumedBudget !== null
      ? Math.round((consumedBudget / totalBudget) * 100)
      : processedCount !== null && queueRemaining !== null
        ? Math.round((processedCount / Math.max(1, processedCount + queueRemaining + 1)) * 100)
        : null;
  if (percentRaw === null && (status === 'succeeded' || status === 'completed')) {
    percentRaw = 100;
  }
  const percent = percentRaw !== null ? Math.max(0, Math.min(100, percentRaw)) : null;

  return {
    status,
    percent,
    totalBudget,
    consumedBudget,
    queueRemaining,
    createdCount,
    processedCount,
  };
};

export const computePlanExecuteProgress = (job: JobLike): PlanExecuteProgress | null => {
  if (!job) return null;
  const status = normalizeStatus((job as any).status);
  const stats = asRecord((job as any).stats);
  const params = asRecord((job as any).params);
  const logs = getLogs((job as any).logs);

  let totalSteps = toNumber(stats.total_steps) ?? toNumber(params.total_steps) ?? toNumber(params.steps);
  const executed = Math.max(0, Math.round(toNumber(stats.executed) ?? 0));
  const failed = Math.max(0, Math.round(toNumber(stats.failed) ?? 0));
  const skipped = Math.max(0, Math.round(toNumber(stats.skipped) ?? 0));
  const doneSteps = executed + failed + skipped;

  let currentStep = toNumber(stats.current_step);
  let currentTaskId = toNumber(stats.current_task_id);

  for (let idx = logs.length - 1; idx >= 0; idx -= 1) {
    const metadata = asRecord(logs[idx]?.metadata);
    if (totalSteps === null) {
      totalSteps = toNumber(metadata.total_steps) ?? toNumber(metadata.steps);
    }
    if (currentStep === null) {
      currentStep = toNumber(metadata.step);
    }
    if (currentTaskId === null) {
      currentTaskId = toNumber(metadata.task_id);
    }
    if (totalSteps !== null && currentStep !== null && currentTaskId !== null) {
      break;
    }
  }

  let percentRaw =
    toNumber(stats.progress_percent) ??
    (totalSteps !== null && totalSteps > 0
      ? Math.round((Math.min(doneSteps, totalSteps) / totalSteps) * 100)
      : null);

  if (status === 'succeeded' || status === 'completed') {
    percentRaw = 100;
  } else if (status === 'running' && percentRaw !== null && totalSteps && doneSteps < totalSteps) {
    percentRaw = Math.min(percentRaw, 99);
  }
  const percent = percentRaw !== null ? Math.max(0, Math.min(100, Math.round(percentRaw))) : null;

  return {
    status,
    percent,
    totalSteps,
    executed,
    failed,
    skipped,
    doneSteps,
    currentStep,
    currentTaskId,
  };
};

export const computeToolProgressSnapshot = (job: JobLike): ToolProgressSnapshot | null => {
  if (!job) return null;
  const stats = asRecord((job as any).stats);
  const rawProgress = stats.tool_progress;
  if (!rawProgress || typeof rawProgress !== 'object') return null;
  const progress = rawProgress as Record<string, any>;
  const counts = asRecord(progress.counts);
  const done = toNumber(counts.done);
  const total = toNumber(counts.total);
  const percentRaw =
    toNumber(progress.percent) ??
    (done !== null && total !== null && total > 0 ? Math.round((done / total) * 100) : null);
  const percent = percentRaw !== null ? Math.max(0, Math.min(100, Math.round(percentRaw))) : null;

  return {
    tool: typeof progress.tool === 'string' ? progress.tool : null,
    percent,
    status: typeof progress.status === 'string' ? progress.status : null,
    phase: typeof progress.phase === 'string' ? progress.phase : null,
    done,
    total,
  };
};
