import { describe, expect, it, vi } from 'vitest';
import { collectArtifactImagePathsFromResult, resolveArtifactImageSrc } from './artifactImageUrl';

vi.mock('@api/artifacts', () => ({
  buildArtifactFileUrl: (sessionId: string, path: string) =>
    `http://api.test/artifacts/sessions/${sessionId}/file?path=${encodeURIComponent(path)}`,
}));

describe('resolveArtifactImageSrc', () => {
  it('returns http(s) URLs unchanged', () => {
    expect(resolveArtifactImageSrc('https://x.com/a.png', 's1')).toBe('https://x.com/a.png');
    expect(resolveArtifactImageSrc('http://x.com/a.png', null)).toBe('http://x.com/a.png');
  });

  it('returns original when no sessionId', () => {
    expect(resolveArtifactImageSrc('plots/a.png', null)).toBe('plots/a.png');
    expect(resolveArtifactImageSrc('plots/a.png', '')).toBe('plots/a.png');
    expect(resolveArtifactImageSrc('plots/a.png', '   ')).toBe('plots/a.png');
  });

  it('rewrites safe relative image paths with session', () => {
    expect(resolveArtifactImageSrc('plots/a.png', 'sess_1')).toBe(
      'http://api.test/artifacts/sessions/sess_1/file?path=' + encodeURIComponent('plots/a.png'),
    );
    expect(resolveArtifactImageSrc('/plots/a.png', 'sess_1')).toBe(
      'http://api.test/artifacts/sessions/sess_1/file?path=' + encodeURIComponent('plots/a.png'),
    );
  });

  it('does not rewrite unsafe or non-image paths', () => {
    expect(resolveArtifactImageSrc('../x.png', 's')).toBe('../x.png');
    expect(resolveArtifactImageSrc('a\\b.png', 's')).toBe('a\\b.png');
    expect(resolveArtifactImageSrc('readme.txt', 's')).toBe('readme.txt');
    expect(resolveArtifactImageSrc('noext', 's')).toBe('noext');
  });

  it('handles empty src', () => {
    expect(resolveArtifactImageSrc('', 's')).toBe('');
    expect(resolveArtifactImageSrc(undefined, 's')).toBe('');
  });
});

describe('collectArtifactImagePathsFromResult', () => {
  it('collects from artifact_paths and storage', () => {
    const paths = collectArtifactImagePathsFromResult({
      artifact_paths: ['/a.png', 'b.jpg', 'readme.txt'],
      storage: { paths: ['c.webp'] },
    });
    expect(paths.sort()).toEqual(['a.png', 'b.jpg', 'c.webp']);
  });

  it('dedupes and skips unsafe', () => {
    expect(
      collectArtifactImagePathsFromResult({
        artifact_paths: ['x.png', 'x.png', '../../../etc/passwd'],
      }),
    ).toEqual(['x.png']);
  });
});
