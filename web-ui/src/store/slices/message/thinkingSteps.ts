import type { ThinkingStep } from '@/types';

const LIVE_STATUSES = new Set(['thinking', 'calling_tool', 'analyzing']);

type StepLike = Pick<ThinkingStep, 'iteration'> &
  Partial<Pick<ThinkingStep, 'status' | 'action' | 'action_result' | 'started_at' | 'timestamp' | 'finished_at'>>;

/**
 * Close every step whose iteration has been left behind.
 *
 * The backend never gives a step its final status when the run moves on: a
 * reasoning iteration that ends without a tool call stays `thinking`, a tool
 * step stays `analyzing`. Left alone, each of them keeps a spinner and a
 * ticking clock until the whole run ends, which looks like several steps
 * working at once.
 *
 * The newest iteration is the live one. A tool step that has not produced a
 * result yet is left alone, because it may still be running.
 */
export function closeSupersededSteps<T extends StepLike>(steps: T[]): T[] {
  let current = 0;
  let currentStep: T | null = null;
  for (const step of steps) {
    if (step.iteration > current) {
      current = step.iteration;
      currentStep = step;
    }
  }
  if (current <= 0 || !currentStep) return steps;

  // Same naive server clock as the steps' own timestamps.
  const closedAt = currentStep.started_at || currentStep.timestamp || undefined;
  let changed = false;
  const next = steps.map((step) => {
    if (step.iteration <= 0 || step.iteration >= current) return step;
    if (!step.status || !LIVE_STATUSES.has(step.status)) return step;
    if (step.action && !step.action_result) return step;
    changed = true;
    return { ...step, status: 'done' as const, finished_at: step.finished_at || closedAt };
  });
  return changed ? next : steps;
}
