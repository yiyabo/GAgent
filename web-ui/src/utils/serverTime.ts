/**
 * Parsing helpers for backend timestamps.
 *
 * The backend stores and emits naive SQLite-style timestamps that are UTC but
 * carry no timezone designator (e.g. "2026-09-14T07:58:00.123456"). JavaScript
 * `Date.parse`/dayjs treat such strings as LOCAL time, which skews every
 * derived duration by the local UTC offset (on a UTC+8 browser, elapsed timers
 * start at 480m). These helpers normalize zone-less values to UTC first.
 */

import dayjs from 'dayjs';

const ZONE_SUFFIX_RE = /(?:[zZ]|[+\-]\d{2}:?\d{2})$/;
const DATETIME_HEAD_RE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}/;

/** Epoch ms for a backend timestamp; null when missing/unparsable. */
export function parseServerTimestampMs(value?: string | null): number | null {
  if (!value) return null;
  const raw = String(value).trim();
  if (!raw) return null;
  if (!ZONE_SUFFIX_RE.test(raw) && DATETIME_HEAD_RE.test(raw)) {
    const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
    const withZone = `${normalized}Z`;
    const ms = Date.parse(withZone);
    return Number.isNaN(ms) ? null : ms;
  }
  const ms = Date.parse(raw);
  return Number.isNaN(ms) ? null : ms;
}

/**
 * dayjs for a backend timestamp with the same zone-less-as-UTC rule.
 * Returns null when the value is missing or unparsable.
 */
export function parseServerTimeDayjs(value?: string | null): import('dayjs').Dayjs | null {
  const ms = parseServerTimestampMs(value);
  return ms === null ? null : dayjs(ms);
}
