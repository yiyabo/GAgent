const TZ_SUFFIX_RE = /(Z|[+-]\d{2}:\d{2})$/i;

export function parseChatTimestamp(value: unknown): Date {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!text) {
    return new Date();
  }

  const direct = new Date(text);
  if (!Number.isNaN(direct.getTime()) && TZ_SUFFIX_RE.test(text)) {
    return direct;
  }

  const normalized = text.includes('T') ? `${text}Z` : `${text.replace(' ', 'T')}Z`;
  const utc = new Date(normalized);
  if (!Number.isNaN(utc.getTime())) {
    return utc;
  }

  return Number.isNaN(direct.getTime()) ? new Date() : direct;
}
