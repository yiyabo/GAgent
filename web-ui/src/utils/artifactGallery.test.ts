import { describe, expect, it } from 'vitest';
import {
  collectArtifactGallery,
  mergeArtifactGalleries,
  normalizeArtifactGalleryItem,
  resolveArtifactGalleryItemSrc,
} from './artifactGallery';

describe('normalizeArtifactGalleryItem', () => {
  it('normalizes safe image payloads', () => {
    expect(
      normalizeArtifactGalleryItem({
        path: '/tool_outputs/run_1/figure.png',
        display_name: 'figure.png',
        source_tool: 'code_executor',
      }),
    ).toMatchObject({
      path: 'tool_outputs/run_1/figure.png',
      display_name: 'figure.png',
      source_tool: 'code_executor',
      mime_family: 'image',
    });
  });

  it('preserves workspace absolute image paths', () => {
    expect(
      normalizeArtifactGalleryItem({
        path: '/Users/apple/LLM/agent/phagescope/results/figure.png',
        display_name: 'figure.png',
        origin: 'workspace',
      }),
    ).toMatchObject({
      path: '/Users/apple/LLM/agent/phagescope/results/figure.png',
      origin: 'workspace',
      mime_family: 'image',
    });
  });

  it('rejects unsafe or non-image payloads', () => {
    expect(normalizeArtifactGalleryItem({ path: '../etc/passwd' })).toBeNull();
    expect(normalizeArtifactGalleryItem({ path: 'notes.txt' })).toBeNull();
  });
});

describe('mergeArtifactGalleries', () => {
  it('dedupes by origin and path while preferring new items first', () => {
    const merged = mergeArtifactGalleries(
      [{ path: 'older.png', origin: 'artifact' }],
      [
        { path: 'newer.png', origin: 'artifact' },
        { path: 'older.png', origin: 'artifact' },
      ],
    );

    expect(merged).toHaveLength(2);
    expect(merged[0]).toMatchObject({ path: 'newer.png', origin: 'artifact', mime_family: 'image' });
    expect(merged[1]).toMatchObject({ path: 'older.png', origin: 'artifact', mime_family: 'image' });
  });

  it('collects lists and filters invalid entries', () => {
    const collected = collectArtifactGallery([
      { path: 'gallery/a.png', origin: 'artifact' },
      { path: 'gallery/a.png', origin: 'artifact' },
      { path: 'bad.txt', origin: 'artifact' },
    ]);

    expect(collected).toHaveLength(1);
    expect(collected[0]).toMatchObject({ path: 'gallery/a.png', origin: 'artifact', mime_family: 'image' });
  });
});

describe('resolveArtifactGalleryItemSrc', () => {
  it('routes deliverable images through the deliverables file API', () => {
    const resolved = resolveArtifactGalleryItemSrc(
      { path: 'paper/figure.png', origin: 'deliverable' },
      'session-demo',
    );

    expect(resolved).toContain(
      '/artifacts/sessions/session-demo/deliverables/file?path=paper%2Ffigure.png',
    );
  });
});
