import type { DecompositionJobStatus } from '@/types';

export const FINAL_JOB_STATUSES = new Set(['succeeded', 'failed', 'completed']);

export const normalizeJobStatus = (raw: unknown): string => {
  const value = typeof raw === 'string' ? raw.trim().toLowerCase() : '';
  return value || 'queued';
};

export const toNumber = (value: unknown): number | null => {
  if (value === null || value === undefined) return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

export const computeDecomposeProgress = (
  job: DecompositionJobStatus | null,
): {
  status: string;
  percent: number | null;
  totalBudget: number | null;
  consumedBudget: number | null;
  queueRemaining: number | null;
  createdCount: number | null;
  processedCount: number | null;
} | null => {
  if (!job) return null;
  const status = normalizeJobStatus(job.status);
  const stats = (job.stats ?? {}) as Record<string, any>;
  const params = (job.params ?? {}) as Record<string, any>;
  const logs = Array.isArray(job.logs) ? job.logs : [];
  const totalBudget = toNumber(params.node_budget) ?? toNumber(stats.node_budget);
  let remainingBudget: number | null = null;
  let queueRemaining: number | null = toNumber(stats.queue_remaining);
  let createdCount: number | null = null;
  let processedCount: number | null = null;
  for (let idx = logs.length - 1; idx >= 0; idx -= 1) {
    const metadata = (logs[idx]?.metadata ?? null) as Record<string, any> | null;
    if (!metadata) continue;
    if (remainingBudget === null) remainingBudget = toNumber(metadata.budget_remaining);
    if (queueRemaining === null) queueRemaining = toNumber(metadata.queue_remaining);
    if (createdCount === null) createdCount = toNumber(metadata.created_count ?? metadata.createdCount);
    if (processedCount === null) processedCount = toNumber(metadata.processed_count ?? metadata.processedCount);
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
  const percentRaw =
    totalBudget !== null && totalBudget > 0 && consumedBudget !== null
      ? Math.round((consumedBudget / totalBudget) * 100)
      : processedCount !== null && queueRemaining !== null
        ? Math.round((processedCount / Math.max(1, processedCount + queueRemaining + 1)) * 100)
      : null;
  const percent = percentRaw !== null ? Math.max(0, Math.min(100, percentRaw)) : null;
  return { status, percent, totalBudget, consumedBudget, queueRemaining, createdCount, processedCount };
};

// 格式化时间
export const formatTime = (date: Date) => {
  return new Date(date).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  });
};

// 降级复制方案 (用于非 HTTPS 环境)
export const fallbackCopyToClipboard = (text: string) => {
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.style.position = 'fixed';
  textArea.style.left = '-9999px';
  textArea.style.top = '-9999px';
  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();
  try {
    document.execCommand('copy');
  } catch (err) {
    console.error('Fallback copy failed:', err);
  }
  document.body.removeChild(textArea);
};
