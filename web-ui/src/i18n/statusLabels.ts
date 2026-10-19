import type { TFn } from './useT';

const KNOWN = ['pending', 'running', 'completed', 'failed', 'blocked', 'skipped', 'needs_review'] as const;
type KnownStatus = (typeof KNOWN)[number];

/**
 * A task status word in the current language. Backend statuses are lowercase identifiers
 * ('completed', 'needs_review', ...); `done`/`success`/`executing` are old aliases. An unknown status
 * is shown as it came, so a new backend state is visible instead of blank.
 */
export function statusLabel(t: TFn, status?: string | null): string {
  const raw = (status ?? 'pending').toString();
  const key = raw.toLowerCase();
  const alias: Record<string, KnownStatus> = { done: 'completed', success: 'completed', executing: 'running', error: 'failed' };
  const known = (KNOWN as readonly string[]).includes(key) ? (key as KnownStatus) : alias[key];
  return known ? t(`status.${known}` as 'status.pending') : raw;
}
