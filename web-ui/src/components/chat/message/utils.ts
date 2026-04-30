import type { ChatMessage as ChatMessageType, DecompositionJobStatus } from '@/types';
import { extractLlmReplyMessage } from '@/utils/llmReplyDisplay';

export const GENERIC_REQUEST_FAILURE_MESSAGE = 'Request failed. Please check:\n\n1. Backend service availability\n2. LLM API configuration\n3. Network connectivity\n\nThen retry the request.';

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

  if (normalized.includes('user query too long') || normalized.includes('max 10000 chars')) {
    return 'Your message is too long for the current backend limit (max 10,000 characters). Please shorten it or split it into smaller requests, then retry.';
  }

  if (
    normalized.includes('insufficient_quota') ||
    normalized.includes('too many requests') ||
    normalized.includes('exceeded your current quota') ||
    normalized.includes('429')
  ) {
    return 'The LLM provider rejected the request because the quota or rate limit was reached. Please check the LLM API billing/quota configuration, wait for the limit to reset, then retry.';
  }

  if (normalized.includes('401') || normalized.includes('authentication required')) {
    return 'Authentication is required or your session expired. Please sign in again, then retry the request.';
  }

  if (normalized.includes('timeout') || normalized.includes('timed out')) {
    return 'The request timed out while waiting for the backend or LLM provider. Please retry; if it repeats, check backend load and provider latency.';
  }

  if (normalized.includes('network error') || normalized.includes('failed to fetch')) {
    return 'Network connectivity failed while contacting the backend. Please check that the backend service is reachable, then retry.';
  }

  if (/\b5\d\d\b/.test(normalized)) {
    return 'The backend returned a server error. Please check backend logs for the failed request, then retry after the service recovers.';
  }

  return GENERIC_REQUEST_FAILURE_MESSAGE;
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
  return new Date(date).toLocaleTimeString('en-US', {
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
