import type { ArtifactGalleryItem } from '@/types';
import {
  buildArtifactFileUrl,
  buildDeliverableFileUrl,
  buildWorkspaceFileUrl,
} from '@api/artifacts';
import { resolveArtifactImageSrc } from './artifactImageUrl';
import { normalizeArtifactImagePath } from './artifactImageUrl';

const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|svg)$/i;

const isNonEmptyString = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0;

export const normalizeArtifactGalleryItem = (raw: any): ArtifactGalleryItem | null => {
  if (!raw || typeof raw !== 'object') {
    return null;
  }
  const path = isNonEmptyString(raw.path) ? normalizeArtifactImagePath(raw.path) : '';
  if (!path || path.includes('..') || path.includes('\\') || !IMAGE_EXT_RE.test(path)) {
    return null;
  }
  return {
    path,
    display_name: isNonEmptyString(raw.display_name) ? raw.display_name.trim() : undefined,
    source_tool: isNonEmptyString(raw.source_tool) ? raw.source_tool.trim() : undefined,
    mime_family: isNonEmptyString(raw.mime_family) ? raw.mime_family.trim() : 'image',
    origin: isNonEmptyString(raw.origin) ? raw.origin.trim() : undefined,
    created_at: isNonEmptyString(raw.created_at) ? raw.created_at.trim() : undefined,
    tracking_id: isNonEmptyString(raw.tracking_id) ? raw.tracking_id.trim() : undefined,
  };
};

export const collectArtifactGallery = (value: any): ArtifactGalleryItem[] => {
  if (!value) {
    return [];
  }
  const items = Array.isArray(value) ? value : [value];
  const collected: ArtifactGalleryItem[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    const normalized = normalizeArtifactGalleryItem(item);
    if (!normalized) {
      continue;
    }
    // Normalize path to prevent duplicate raw_files vs root duplicates
    const cleanPath = normalized.path.replace(/^raw_files\//, '');
    const key = `${normalized.origin ?? 'artifact'}::${cleanPath}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    collected.push(normalized);
  }
  return collected;
};

const WORKSPACE_ABS_FILE_RE = /^\/(home|Users|tmp|data|mnt|var|opt)\//i;

export const resolveArtifactFileItemSrc = (
  item: ArtifactFileItem | null | undefined,
  sessionId: string | null | undefined,
): string => {
  if (!item) {
    return '';
  }
  const sid = typeof sessionId === 'string' ? sessionId.trim() : '';
  if (!sid) {
    return item.path;
  }
  if ((item.origin ?? '').trim().toLowerCase() === 'deliverable') {
    return buildDeliverableFileUrl(sid, item.path);
  }
  if (WORKSPACE_ABS_FILE_RE.test(item.path)) {
    return buildWorkspaceFileUrl(sid, item.path);
  }
  return buildArtifactFileUrl(sid, item.path);
};

export const mergeArtifactGalleries = (
  existing: ArtifactGalleryItem[] | null | undefined,
  additions: ArtifactGalleryItem[] | null | undefined,
): ArtifactGalleryItem[] => {
  return collectArtifactGallery([...(additions ?? []), ...(existing ?? [])]);
};

export const resolveArtifactGalleryItemSrc = (
  item: ArtifactGalleryItem | null | undefined,
  sessionId: string | null | undefined,
): string => {
  if (!item) {
    return '';
  }
  const sid = typeof sessionId === 'string' ? sessionId.trim() : '';
  if (sid && (item.origin ?? '').trim().toLowerCase() === 'deliverable') {
    return buildDeliverableFileUrl(sid, item.path);
  }
  return resolveArtifactImageSrc(item.path, sessionId);
};

// ---------------------------------------------------------------------------
// Produced non-image files (documents, archives, data files) shown as
// download entries under the message that generated them.
// ---------------------------------------------------------------------------

export interface ArtifactFileItem {
  path: string;
  display_name?: string;
  source_tool?: string;
  origin?: string;
  mime_family: 'file';
}

const FILE_EXT_RE = /\.(md|markdown|txt|docx?|xlsx?|pptx?|pdf|csv|tsv|jsonl?|fa|fasta|fna|faa|gbk|gbff|gff|tsv\.gz|tar\.gz|tgz|zip|gz|xlsx|html?|tex|bib|rds|maf|vcf|h5ad|npy|npz|pkl)$/i;

export const normalizeArtifactFileItem = (raw: any): ArtifactFileItem | null => {
  if (!raw || typeof raw !== 'object') {
    return null;
  }
  const rawPath = isNonEmptyString(raw.path) ? raw.path.trim() : '';
  if (!rawPath || rawPath.endsWith('/') || rawPath.includes('..') || rawPath.includes('\\')) {
    return null;
  }
  if (IMAGE_EXT_RE.test(rawPath)) {
    return null; // images belong to the gallery, not the file list
  }
  if (!FILE_EXT_RE.test(rawPath)) {
    return null;
  }
  // Workspace-absolute paths keep their leading slash for the workspace endpoint.
  const path = /^\/(home|Users|tmp|data|mnt|var|opt)\//i.test(rawPath)
    ? rawPath
    : rawPath.replace(/^\/+/, '');
  return {
    path,
    display_name: isNonEmptyString(raw.display_name) ? raw.display_name.trim() : undefined,
    source_tool: isNonEmptyString(raw.source_tool) ? raw.source_tool.trim() : undefined,
    origin: isNonEmptyString(raw.origin) ? raw.origin.trim() : undefined,
    mime_family: 'file',
  };
};

export const collectArtifactFiles = (value: any): ArtifactFileItem[] => {
  if (!value) {
    return [];
  }
  const items = Array.isArray(value) ? value : [value];
  const collected: ArtifactFileItem[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    const normalized = normalizeArtifactFileItem(item);
    if (!normalized) {
      continue;
    }
    const key = `${normalized.origin ?? 'artifact'}::${normalized.path}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    collected.push(normalized);
  }
  return collected;
};
