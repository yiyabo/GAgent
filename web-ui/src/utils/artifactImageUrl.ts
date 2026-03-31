import { buildArtifactFileUrl, buildWorkspaceFileUrl } from '@api/artifacts';

const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|svg)$/i;
const WORKSPACE_ABS_RE = /^(\/|)(Users|home|private|Volumes|tmp|var|opt|workspace|workspaces|data|mnt|srv|root)\//i;

export function normalizeArtifactImagePath(src: string | null | undefined): string {
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
  if (WORKSPACE_ABS_RE.test(trimmed)) {
    return trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
  }
  return trimmed.replace(/^\/+/, '');
}

export function isWorkspaceAbsoluteImagePath(src: string | null | undefined): boolean {
  const normalized = normalizeArtifactImagePath(src);
  return normalized.startsWith('/') && WORKSPACE_ABS_RE.test(normalized);
}

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
  const normalized = normalizeArtifactImagePath(src);
  if (!normalized) {
    return '';
  }
  if (/^https?:\/\//i.test(normalized)) {
    return normalized;
  }
  const sid = typeof sessionId === 'string' ? sessionId.trim() : '';
  if (!sid) {
    return normalized;
  }
  if (normalized.includes('..') || normalized.includes('\\')) {
    return normalized;
  }
  if (!IMAGE_EXT_RE.test(normalized)) {
    return normalized;
  }
  if (isWorkspaceAbsoluteImagePath(normalized)) {
    return buildWorkspaceFileUrl(sid, normalized);
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
    const t = normalizeArtifactImagePath(p);
    if (!IMAGE_EXT_RE.test(t)) return;
    if (t.includes('..') || t.includes('\\')) return;
    out.push(t);
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
  const gallery = result.artifact_gallery;
  if (Array.isArray(gallery)) {
    gallery.forEach((item) => {
      if (item && typeof item === 'object') {
        push((item as Record<string, unknown>).path);
      }
    });
  }

  return [...new Set(out)];
}
