import { buildArtifactFileUrl } from '@api/artifacts';

const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|svg)$/i;

/**
 * Rewrite markdown image `src` to session artifact file URL when safe.
 * - Absolute http(s) URLs are returned unchanged.
 * - Without sessionId, returns src unchanged (may not load for relative paths).
 * - Relative paths must not contain `..` or backslashes; must match image extension whitelist.
 */
export function resolveArtifactImageSrc(
  src: string | null | undefined,
  sessionId: string | null | undefined,
): string {
  if (src == null || typeof src !== 'string') {
    return '';
  }
  const trimmed = src.trim();
  if (!trimmed) {
    return '';
  }
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed;
  }
  const sid = typeof sessionId === 'string' ? sessionId.trim() : '';
  if (!sid) {
    return trimmed;
  }

  const normalized = trimmed.replace(/^\/+/, '');
  if (!normalized) {
    return trimmed;
  }
  if (normalized.includes('..') || normalized.includes('\\')) {
    return trimmed;
  }
  if (!IMAGE_EXT_RE.test(normalized)) {
    return trimmed;
  }

  return buildArtifactFileUrl(sid, normalized);
}

/**
 * Collect image-capable artifact relative paths from a tool result payload.
 */
export function collectArtifactImagePathsFromResult(result: Record<string, any> | null | undefined): string[] {
  if (!result || typeof result !== 'object') {
    return [];
  }
  const out: string[] = [];
  const push = (p: unknown) => {
    if (typeof p !== 'string' || !p.trim()) return;
    const t = p.trim();
    if (!IMAGE_EXT_RE.test(t)) return;
    if (t.includes('..') || t.includes('\\')) return;
    out.push(t.replace(/^\/+/, ''));
  };

  const raw = result.artifact_paths;
  if (Array.isArray(raw)) {
    raw.forEach(push);
  }
  const storage = result.storage;
  if (storage && typeof storage === 'object') {
    const sp = (storage as Record<string, unknown>).paths;
    const sap = (storage as Record<string, unknown>).artifact_paths;
    if (Array.isArray(sp)) sp.forEach(push);
    if (Array.isArray(sap)) sap.forEach(push);
  }

  return [...new Set(out)];
}
