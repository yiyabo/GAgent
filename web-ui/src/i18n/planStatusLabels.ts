import type { TranslationKey } from './keys';
import type { TFn } from './useT';

/**
 * Status words of the plan workspace and the task detail. They are not the general `status.*` words on
 * purpose: with plan acceptance off, a finished task is "executed", not "completed and approved", and a
 * blocked task is "waiting on dependencies". Keys are listed statically, so the compiler checks them.
 */
const KEYS: Record<string, TranslationKey> = {
  pending: 'planStatus.pending',
  queued: 'planStatus.queued',
  running: 'planStatus.running',
  in_progress: 'planStatus.running',
  completed: 'planStatus.completed',
  succeeded: 'planStatus.completed',
  failed: 'planStatus.failed',
  error: 'planStatus.failed',
  blocked: 'planStatus.blocked',
  skipped: 'planStatus.skipped',
  cancelled: 'planStatus.cancelled',
  canceled: 'planStatus.cancelled',
  paused: 'planStatus.paused',
  stale: 'planStatus.stale',
  unknown: 'planStatus.unknown',
  reconciling: 'planStatus.reconciling',
  needs_review: 'planStatus.needsReview',
  fresh: 'planStatus.fresh',
};

/** A status word in the current language; a status this table does not know is shown as it came. */
export function planStatusLabel(t: TFn, status?: string | null): string {
  const raw = status == null ? '' : String(status);
  const key = KEYS[raw];
  return key ? t(key) : raw;
}
