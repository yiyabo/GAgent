import type { ArtifactVersions } from '@/api/artifactVersions';
import type { PlanResultItem } from '@/types';

export interface PlanArtifactFile {
  key: string;
  alias: string;
  name: string;
  taskId: number | null;
  path: string | null;
  sessionId: string | null;
  sourceType: 'raw' | 'deliverables';
  version?: string;
  artifactVersionId?: string;
  freshness: string;
  validated?: boolean;
  recordedAt?: number;
}

const text = (value: unknown): string => typeof value === 'string' ? value.trim() : '';
const object = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};

function relativePath(value: string): string | null {
  if (!value || value.startsWith('/') || value.includes('\\') || /^[a-z]+:/i.test(value)) return null;
  const parts = value.split('/');
  return parts.some(part => part === '..' || part === '.') ? null : value;
}

/** A plan receipt proves task ownership; a session path proves where it can be opened. */
export function artifactFile(
  alias: string,
  entry: Record<string, unknown>,
  taskId: number | null,
  sessionId: string | null,
  sessionIsAuthoritative = false,
): PlanArtifactFile {
  const binding = object(entry.binding);
  const boundSession = text(binding.session_id) || text(entry.session_id);
  const sourceSession = boundSession || sessionId;
  const confirmedSession = Boolean(boundSession || (sessionId && sessionIsAuthoritative));
  const originalPath = text(entry.path);
  const marker = sourceSession ? `/${sourceSession}/` : '';
  const markerIndex = marker ? originalPath.indexOf(marker) : -1;
  const rawPath = originalPath.startsWith('/')
    ? markerIndex >= 0 ? relativePath(originalPath.slice(markerIndex + marker.length)) : null
    : confirmedSession ? relativePath(originalPath) : null;
  // A bare basename is not sufficient to invent a deliverable path. In particular,
  // never open the current session's similarly named file for an older receipt.
  const hasConflictingAbsolutePath = originalPath.startsWith('/') && markerIndex < 0;
  const publishedPath = hasConflictingAbsolutePath || !(confirmedSession || rawPath)
    ? null : relativePath(text(entry.deliverable_path));
  const path = rawPath || publishedPath;
  const name = (text(entry.deliverable_path) || originalPath || alias).split('/').filter(Boolean).pop() || alias;
  const artifactVersionId = text(entry.artifact_version_id) || undefined;
  return {
    key: artifactVersionId || `${taskId ?? 'unknown'}:${alias}:${originalPath}`,
    alias, name, taskId, path,
    sessionId: sourceSession || null,
    sourceType: rawPath ? 'raw' : 'deliverables',
    version: text(entry.version) || undefined,
    artifactVersionId,
    freshness: text(entry.freshness) || 'unknown',
    validated: typeof entry.validated === 'boolean' ? entry.validated : undefined,
    recordedAt: typeof entry.created_at === 'number' ? entry.created_at :
      typeof entry.updated_at === 'number' ? entry.updated_at : undefined,
  };
}

export function collectPlanArtifacts(
  planId: number,
  results: PlanResultItem[],
  versions: ArtifactVersions | undefined,
  sessionId: string | null,
  sessionIsAuthoritative = false,
): PlanArtifactFile[] {
  const entries = new Map<string, PlanArtifactFile>();
  for (const result of results) {
    for (const [alias, value] of Object.entries(object(result.metadata?.published_artifacts))) {
      const record = object(value);
      if (!Object.keys(record).length) continue;
      if (record.producer_task_id != null && record.producer_task_id !== result.task_id) continue;
      if (record.plan_id != null && record.plan_id !== planId) continue;
      if (record.producer_plan_id != null && record.producer_plan_id !== planId) continue;
      const manifestPath = text(result.metadata?.artifact_manifest_path);
      const manifestConfirmsSession = Boolean(sessionId && manifestPath.endsWith(`/${sessionId}/artifacts/plan_${planId}/artifacts_manifest.json`));
      entries.set(`${result.task_id}:${alias}`, artifactFile(alias, record, result.task_id, sessionId, sessionIsAuthoritative || manifestConfirmsSession));
    }
  }
  // The plan manifest is authoritative for current aliases, including v1 plans.
  for (const [alias, value] of Object.entries(versions?.current_artifacts ?? {})) {
    const record = object(value);
    if (record.plan_id != null && record.plan_id !== planId) continue;
    const taskId = typeof record.producer_task_id === 'number' ? record.producer_task_id : null;
    for (const [key, entry] of entries) {
      if (entry.alias === alias) entries.delete(key);
    }
    entries.set(`${taskId}:${alias}`, artifactFile(alias, record, taskId, sessionId, sessionIsAuthoritative));
  }
  // Two declared aliases may refer to the same physical output; show it once per task.
  const paths = new Set<string>();
  return [...entries.values()].filter(entry => {
    const key = `${entry.taskId}:${entry.sessionId}:${entry.path || entry.key}`;
    if (paths.has(key)) return false;
    paths.add(key);
    return true;
  });
}
