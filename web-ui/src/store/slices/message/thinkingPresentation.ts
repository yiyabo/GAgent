import type {
  ChatResponseMetadata,
  ThinkingDisplayMode,
  ThinkingProcess,
} from '@/types';

const isThinkingDisplayMode = (value: unknown): value is ThinkingDisplayMode =>
  value === 'compact_progress' ||
  value === 'full_thinking' ||
  value === 'final_answer' ||
  value === 'hidden';

export function hasThinkingSteps(thinkingProcess?: ThinkingProcess | null): boolean {
  return Boolean(
    thinkingProcess &&
      Array.isArray(thinkingProcess.steps) &&
      thinkingProcess.steps.length > 0,
  );
}

export function hasCompactThinkingProgress(
  metadata?: ChatResponseMetadata | Record<string, any> | null,
): boolean {
  return Boolean(
    metadata &&
      metadata.thinking_visibility === 'progress' &&
      metadata.deep_think_progress &&
      typeof metadata.deep_think_progress === 'object',
  );
}

export function resolveThinkingDisplayMode({
  metadata,
  thinkingProcess,
  isStreaming = false,
}: {
  metadata?: ChatResponseMetadata | Record<string, any> | null;
  thinkingProcess?: ThinkingProcess | null;
  isStreaming?: boolean;
}): ThinkingDisplayMode {
  const nextMetadata = metadata ?? {};
  const explicitMode = isThinkingDisplayMode(nextMetadata.thinking_display_mode)
    ? nextMetadata.thinking_display_mode
    : null;
  const visibility = nextMetadata.thinking_visibility;
  const hasPersistedSteps = hasThinkingSteps(thinkingProcess);
  const hasCompactProgress = hasCompactThinkingProgress(nextMetadata);
  const isFinished =
    nextMetadata.status === 'completed' ||
    nextMetadata.status === 'failed' ||
    thinkingProcess?.status === 'completed' ||
    thinkingProcess?.status === 'error' ||
    (!isStreaming && hasPersistedSteps);

  if (explicitMode === 'hidden' || visibility === 'hidden') {
    return 'hidden';
  }

  if (hasPersistedSteps) {
    return isFinished ? 'final_answer' : 'full_thinking';
  }

  if (explicitMode === 'compact_progress') {
    return !isFinished && hasCompactProgress ? 'compact_progress' : 'final_answer';
  }

  if (explicitMode === 'full_thinking') {
    return isFinished ? 'final_answer' : 'full_thinking';
  }

  if (explicitMode === 'final_answer') {
    return 'final_answer';
  }

  if (!isFinished && hasCompactProgress) {
    return 'compact_progress';
  }

  if (!isFinished && visibility === 'visible') {
    return 'full_thinking';
  }

  return 'final_answer';
}

export function normalizeHydratedThinkingPresentation(
  metadata: ChatResponseMetadata | Record<string, any>,
  thinkingProcess?: ThinkingProcess | null,
): void {
  const hasPersistedSteps = hasThinkingSteps(thinkingProcess);

  if (hasPersistedSteps && metadata.thinking_visibility === 'progress') {
    metadata.thinking_visibility = 'visible';
  }

  const shouldAnnotate =
    hasPersistedSteps ||
    hasCompactThinkingProgress(metadata) ||
    metadata.thinking_visibility === 'visible' ||
    metadata.thinking_visibility === 'progress' ||
    isThinkingDisplayMode(metadata.thinking_display_mode);

  if (!shouldAnnotate) {
    return;
  }

  metadata.thinking_display_mode = resolveThinkingDisplayMode({
    metadata,
    thinkingProcess,
    isStreaming: false,
  });
}
