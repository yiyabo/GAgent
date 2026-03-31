import type { ArtifactGalleryItem } from '@/types';
import { buildDeliverableFileUrl } from '@api/artifacts';
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
    const key = `${normalized.origin ?? 'artifact'}::${normalized.path}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    collected.push(normalized);
  }
  return collected;
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
