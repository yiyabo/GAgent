import { buildArtifactFileUrl, buildWorkspaceFileUrl } from '@api/artifacts';

const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|svg)$/i;
const WORKSPACE_ABS_RE = /^(\/|)(Users|home|private|Volumes|tmp|var|opt|workspace|workspaces|data|mnt|srv|root)\//i;
const PRODUCTIVE_SEGMENT_RE = /(?:^|\/)(raw_files|deliverables|results|figures|image_tabular)\//i;

/** Session file endpoints can be absolute or relative to the configured API. */
export function parseSessionArtifactImageUrl(src: string) {
  if (!src || !/^(https?:\/\/|\/)/i.test(src.trim())) return null;
  try {
    const url = new URL(src.trim(), 'http://artifact.local');
    const match = url.pathname.match(/\/artifacts\/sessions\/([^/]+)\/(?:deliverables\/file|workspace-file|file)$/);
    const path = url.searchParams.get('path');
    return match && path ? { sessionId: decodeURIComponent(match[1]), path } : null;
  } catch {
    return null;
  }
}

export function withArtifactImageVersion(src: string, contentHash?: string | null): string {
  if (!contentHash) return src;
  const [withoutFragment, fragment] = src.split('#', 2);
  const queryStart = withoutFragment.indexOf('?');
  const path = queryStart < 0 ? withoutFragment : withoutFragment.slice(0, queryStart);
  const query = new URLSearchParams(queryStart < 0 ? '' : withoutFragment.slice(queryStart + 1));
  query.set('v', contentHash);
  return `${path}?${query}${fragment === undefined ? '' : `#${fragment}`}`;
}

export function artifactImageFileName(src: string, path = '', alt = ''): string {
  const source = IMAGE_EXT_RE.test(path) ? path : parseSessionArtifactImageUrl(src)?.path || src.split(/[?#]/, 1)[0];
  const name = source.split('/').pop() || '';
  return IMAGE_EXT_RE.test(name) ? name : IMAGE_EXT_RE.test(alt) ? alt : 'figure.png';
}

/** Keep the API base, endpoint and immutable deliverable version for sibling exports. */
export function artifactImageFormatUrl(src: string, ext: 'png' | 'svg' | 'pdf'): string {
  const artifact = parseSessionArtifactImageUrl(src);
  if (!artifact) return src;
  const url = new URL(src, 'http://artifact.local');
  const path = artifact.path.replace(/\.[^/.]+$/, `.${ext}`);
  url.searchParams.set('path', path);
  if (path !== artifact.path) url.searchParams.delete('v');
  url.searchParams.delete('_retry');
  return /^https?:\/\//i.test(src) ? url.href : `${url.pathname}${url.search}${url.hash}`;
}

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
  sourceType?: 'raw' | 'deliverables' | null,
): string {
  if (src && parseSessionArtifactImageUrl(src)) return src.trim();
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
  const suffixStart = normalized.search(/[?#]/);
  let path = suffixStart < 0 ? normalized : normalized.slice(0, suffixStart);
  const suffix = suffixStart < 0 ? '' : normalized.slice(suffixStart);
  try { path = decodeURIComponent(path); } catch { /* Keep literal percent filenames. */ }
  if (path.includes('..') || path.includes('\\')) return normalized;
  if (!IMAGE_EXT_RE.test(path)) {
    return normalized;
  }
  if (isWorkspaceAbsoluteImagePath(path)) {
    return buildWorkspaceFileUrl(sid, path) + suffix.replace(/^\?/, '&');
  }

  // Mangled container-absolute paths: the model often pastes the tool
  // result's "/app/runtime/<sid>/raw_files/x.png" verbatim into markdown;
  // normalizeArtifactImagePath strips the leading slash, leaving
  // "app/runtime/<sid>/raw_files/x.png", which resolves under the session
  // root as a bogus "app/..." subtree. Cut at the first productive segment
  // and serve the session-relative remainder instead.
  const segMatch = PRODUCTIVE_SEGMENT_RE.exec(path);
  if (segMatch && segMatch.index > 0) {
    return buildArtifactFileUrl(sid, path.slice(segMatch.index).replace(/^\/+/, '')) + suffix.replace(/^\?/, '&');
  }

  const pathForApi = sourceType === 'deliverables'
    ? (path.startsWith('deliverables/') ? path : `deliverables/latest/${path}`)
    : sourceType === 'raw'
    ? (path.startsWith('raw_files/') ? path : `raw_files/${path}`)
    : path;
  return buildArtifactFileUrl(sid, pathForApi) + suffix.replace(/^\?/, '&');
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
