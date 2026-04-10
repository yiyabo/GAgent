import type { ThinkingProcess } from '@/types';

export function promoteHydratedThinkingVisibility(
  metadata: Record<string, any>,
  thinkingProcess?: ThinkingProcess,
): void {
  const hasPersistedSteps = Boolean(
    thinkingProcess &&
      Array.isArray(thinkingProcess.steps) &&
      thinkingProcess.steps.length > 0,
  );

  if (hasPersistedSteps && metadata.thinking_visibility === 'progress') {
    metadata.thinking_visibility = 'visible';
  }
}
