import type { ChatMessage as ChatMessageType, DecompositionJobStatus } from '@/types';
import { extractLlmReplyMessage } from '@/utils/llmReplyDisplay';
import { dateLocale } from '@/i18n/format';
import { translate } from '@/i18n/useT';

const getErrorMessage = (error: unknown): string => {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  if (error === null || error === undefined) return '';
  try {
    return JSON.stringify(error);
  } catch {
    return String(error);
  }
};

export const resolveRequestFailureMessage = (error: unknown): string => {
  const message = getErrorMessage(error);
  const normalized = message.toLowerCase();

  if (normalized.includes("user query too long")) {
    const maxMatch = message.match(/max\s+([0-9,]+)\s+chars?/i);
    const maxText = maxMatch?.[1]
      ? translate('requestFailure.maxChars', { n: maxMatch[1].replace(/,/g, '') })
      : translate('requestFailure.maxDefault');
    return translate('requestFailure.tooLong', { max: maxText });
  }

  if (
    normalized.includes('insufficient_quota') ||
    normalized.includes("quota is insufficient") ||
    (normalized.includes("quota") && normalized.includes("insufficient")) ||
    normalized.includes('too many requests') ||
    normalized.includes('exceeded your current quota') ||
    normalized.includes('429')
  ) {
    return translate('requestFailure.quota');
  }

  if (normalized.includes('401') || normalized.includes('authentication required')) {
    return translate('requestFailure.auth');
  }

  if (normalized.includes('timeout') || normalized.includes('timed out')) {
    return translate('requestFailure.timeout');
  }

  if (normalized.includes('network error') || normalized.includes('failed to fetch')) {
    return translate('requestFailure.network');
  }

  if (/\b5\d\d\b/.test(normalized)) {
    return translate('requestFailure.server');
  }

  return translate('requestFailure.generic');
};

export const FINAL_JOB_STATUSES = new Set(['succeeded', 'failed', 'completed']);
export const BACKGROUND_DISPATCH_CATEGORIES = new Set([
  'phagescope',
  'code_executor',
  'task_creation',
]);

export const normalizeJobStatus = (raw: unknown): string => {
  const value = typeof raw === 'string' ? raw.trim().toLowerCase() : '';
  return value || 'queued';
};

export const isBackgroundDispatchCategory = (value: unknown): boolean => {
  const normalized = typeof value === 'string' ? value.trim() : '';
  return BACKGROUND_DISPATCH_CATEGORIES.has(normalized);
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

// Format time.
export const formatTime = (date: Date) => {
  return new Date(date).toLocaleTimeString(dateLocale(), {
    hour: '2-digit',
    minute: '2-digit',
  });
};

// Fallback copy approach (for non-HTTPS environments).
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

// Keep copy behavior aligned with what ChatMessage actually renders.
export const resolveMessageCopyText = (message: Pick<ChatMessageType, 'type' | 'content' | 'metadata'>): string => {
  const normalizedContent =
    message.type === 'assistant' ? extractLlmReplyMessage(message.content) : message.content;
  const analysisText =
    typeof (message.metadata as any)?.analysis_text === 'string'
      ? ((message.metadata as any).analysis_text as string)
      : '';
  const finalSummary =
    typeof (message.metadata as any)?.final_summary === 'string'
      ? ((message.metadata as any).final_summary as string)
      : (normalizedContent ?? '');
  const preferred =
    analysisText && analysisText.trim().length > 0 ? analysisText : finalSummary || normalizedContent || '';
  return message.type === 'assistant' ? extractLlmReplyMessage(preferred) : preferred;
};

export const shouldShowStreamingCursor = ({
  unifiedStream,
  status,
  backgroundCategory,
}: {
  unifiedStream?: boolean;
  status?: unknown;
  backgroundCategory?: unknown;
}): boolean => {
  if (!unifiedStream) return false;
  const normalizedStatus = typeof status === 'string' ? status.trim().toLowerCase() : '';
  if (normalizedStatus !== 'pending' && normalizedStatus !== 'running') return false;
  if (isBackgroundDispatchCategory(backgroundCategory)) return false;
  return true;
};
