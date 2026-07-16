import type { ToolResultPayload } from '@/types';
import { buildArtifactFileUrl, buildDeliverableFileUrl } from '@api/artifacts';

export type GeneratedDownloadScope = 'raw' | 'deliverables';

export interface GeneratedDownloadFile {
  path: string;
  name: string;
  scope: GeneratedDownloadScope;
  sourceTool?: string;
}

const SESSION_ABS_RE =
  /(?:^|[\s(`"'\[])((?:\/[^\s`"'<>]+?\/)?runtime\/session_[A-Za-z0-9_-]+\/[^\s`"'<>]+)/g;
const REL_ARTIFACT_RE =
  /(?:^|[\s(`"'\[])((?:raw_files|tool_outputs|deliverables|uploads)\/[^\s`"'<>]+)/g;
const FILE_EXT_RE =
  /\.(md|txt|pdf|csv|tsv|json|jsonl|xlsx?|docx?|pptx?|zip|png|jpe?g|gif|webp|svg|py|r|html|tex|bib|npz|log|npz)$/i;

const isNonEmptyString = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0;

export function normalizeSessionRelativePath(
  raw: string | null | undefined,
  sessionId?: string | null,
): string | null {
  if (!isNonEmptyString(raw)) {
    return null;
  }
  let path = raw.trim().replace(/\\/g, '/');
  path = path.replace(/^['"`]+|['"`]+$/g, '');
  path = path.replace(/[),.;:]+$/g, '');
  if (!path || path.includes('..')) {
    return null;
  }

  const sid = typeof sessionId === 'string' ? sessionId.trim() : '';
  const sessionMarkers = [
    sid ? `runtime/${sid}/` : '',
    sid ? `${sid}/` : '',
    /runtime\/session_[A-Za-z0-9_-]+\//i,
  ].filter(Boolean);

  for (const marker of sessionMarkers) {
    if (typeof marker === 'string') {
      const idx = path.indexOf(marker);
      if (idx >= 0) {
        path = path.slice(idx + marker.length);
        break;
      }
    } else {
      const match = path.match(marker);
      if (match && match.index != null) {
        path = path.slice(match.index + match[0].length);
        break;
      }
    }
  }

  path = path.replace(/^\/+/, '');
  if (!path || path.includes('..')) {
    return null;
  }
  if (!path.includes('/') && FILE_EXT_RE.test(path)) {
    path = `raw_files/tmp/${path}`;
  }
  if (!FILE_EXT_RE.test(path)) {
    return null;
  }
  return path;
}

export function classifyDownloadScope(path: string): GeneratedDownloadScope {
  const normalized = path.replace(/^\/+/, '');
  if (normalized.startsWith('deliverables/')) {
    return 'deliverables';
  }
  return 'raw';
}

export function toDeliverableApiPath(path: string): string {
  let p = path.replace(/^\/+/, '');
  if (p.startsWith('deliverables/latest/')) {
    p = p.slice('deliverables/latest/'.length);
  } else if (p.startsWith('deliverables/')) {
    p = p.slice('deliverables/'.length);
  }
  return p;
}

function pushPath(
  bucket: GeneratedDownloadFile[],
  seen: Set<string>,
  rawPath: unknown,
  sessionId?: string | null,
  sourceTool?: string,
) {
  if (!isNonEmptyString(rawPath)) {
    return;
  }
  const path = normalizeSessionRelativePath(rawPath, sessionId);
  if (!path) {
    return;
  }
  const scope = classifyDownloadScope(path);
  const key = `${scope}::${path}`;
  if (seen.has(key)) {
    return;
  }
  seen.add(key);
  const name = path.split('/').filter(Boolean).pop() || path;
  bucket.push({
    path,
    name,
    scope,
    sourceTool: sourceTool || undefined,
  });
}

function collectFromObject(
  bucket: GeneratedDownloadFile[],
  seen: Set<string>,
  obj: Record<string, any> | null | undefined,
  sessionId?: string | null,
  sourceTool?: string,
) {
  if (!obj || typeof obj !== 'object') {
    return;
  }
  const directKeys = [
    'path',
    'destination',
    'output_path',
    'effective_output_path',
    'polished_output_path',
    'pre_polish_output_path',
    'partial_output_path',
    'analysis_path',
    'file_path',
    'saved_path',
    'local_path',
  ];
  for (const key of directKeys) {
    pushPath(bucket, seen, obj[key], sessionId, sourceTool);
  }
  for (const key of ['artifact_paths', 'produced_files', 'files', 'saved_files', 'downloaded_files']) {
    const value = obj[key];
    if (Array.isArray(value)) {
      for (const item of value) {
        if (isNonEmptyString(item)) {
          pushPath(bucket, seen, item, sessionId, sourceTool);
        } else if (item && typeof item === 'object') {
          pushPath(
            bucket,
            seen,
            (item as any).path || (item as any).file_path || (item as any).local_path,
            sessionId,
            sourceTool,
          );
        }
      }
    }
  }
  if (obj.storage && typeof obj.storage === 'object') {
    collectFromObject(bucket, seen, obj.storage as Record<string, any>, sessionId, sourceTool);
  }
  if (obj.deliverables && typeof obj.deliverables === 'object') {
    collectFromObject(bucket, seen, obj.deliverables as Record<string, any>, sessionId, sourceTool);
  }
}

export function collectGeneratedDownloadFiles(options: {
  toolResults?: ToolResultPayload[] | null;
  content?: string | null;
  sessionId?: string | null;
}): GeneratedDownloadFile[] {
  const { toolResults, content, sessionId } = options;
  const bucket: GeneratedDownloadFile[] = [];
  const seen = new Set<string>();

  for (const payload of toolResults || []) {
    const toolName = isNonEmptyString(payload?.name) ? payload.name.trim() : undefined;
    const result = payload?.result && typeof payload.result === 'object' ? payload.result : null;
    const success =
      result && typeof (result as any).success === 'boolean' ? (result as any).success : true;
    if (success === false) {
      continue;
    }
    collectFromObject(bucket, seen, result as Record<string, any>, sessionId, toolName);
    collectFromObject(
      bucket,
      seen,
      payload?.parameters as Record<string, any>,
      sessionId,
      toolName,
    );
  }

  if (isNonEmptyString(content)) {
    const text = content;
    for (const re of [SESSION_ABS_RE, REL_ARTIFACT_RE]) {
      re.lastIndex = 0;
      let match: RegExpExecArray | null;
      while ((match = re.exec(text)) != null) {
        pushPath(bucket, seen, match[1], sessionId, 'message');
      }
    }
  }

  return bucket;
}

export function buildGeneratedFileDownloadUrl(
  sessionId: string,
  file: GeneratedDownloadFile,
): string {
  if (file.scope === 'deliverables') {
    return buildDeliverableFileUrl(sessionId, toDeliverableApiPath(file.path));
  }
  return buildArtifactFileUrl(sessionId, file.path);
}

export async function downloadGeneratedFile(
  sessionId: string,
  file: GeneratedDownloadFile,
): Promise<void> {
  const url = buildGeneratedFileDownloadUrl(sessionId, file);
  const response = await fetch(url, { credentials: 'include' });
  if (!response.ok) {
    throw new Error(`Download failed: ${response.status} ${response.statusText}`);
  }
  const blob = await response.blob();
  const blobUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = blobUrl;
  anchor.download = file.name;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(blobUrl);
}
