import { describe, expect, it, vi } from 'vitest';
import {
  collectArtifactImagePathsFromResult,
  isWorkspaceAbsoluteImagePath,
  resolveArtifactImageSrc,
  artifactImageFileName,
  artifactImageFormatUrl,
  parseSessionArtifactImageUrl,
} from './artifactImageUrl';

vi.mock('@api/artifacts', () => ({
  buildArtifactFileUrl: (sessionId: string, path: string) =>
    `http://api.test/artifacts/sessions/${sessionId}/file?path=${encodeURIComponent(path)}`,
  buildWorkspaceFileUrl: (sessionId: string, path: string) =>
    `http://api.test/artifacts/sessions/${sessionId}/workspace-file?path=${encodeURIComponent(path)}`,
}));

describe('resolveArtifactImageSrc', () => {
  it('keeps content revisions separate from the encoded file path', () => {
    const url = resolveArtifactImageSrc('results/AB.png?v=new#panel', 's1');
    const parsed = new URL(url);
    expect(parsed.searchParams.get('path')).toBe('results/AB.png');
    expect(parsed.searchParams.get('v')).toBe('new');
    expect(parsed.hash).toBe('#panel');
    const endpoint = '/api/artifacts/sessions/s1/file?path=results%2FAB.png&v=new';
    expect(resolveArtifactImageSrc(endpoint, 's1')).toBe(endpoint);
    expect(parseSessionArtifactImageUrl(endpoint)).toEqual({sessionId:'s1', path:'results/AB.png'});
    expect(artifactImageFileName(endpoint)).toBe('AB.png');
    expect(artifactImageFileName(endpoint, 'AB')).toBe('AB.png');
    const spaced = new URL(resolveArtifactImageSrc('results/chart%20name.png?v=new', 's1'));
    expect(spaced.searchParams.get('path')).toBe('results/chart name.png');
    expect(resolveArtifactImageSrc('%2e%2e/private.png?v=x', 's1')).toBe('%2e%2e/private.png?v=x');
  });

  it('keeps API base and history version for downloads without reusing another format hash', () => {
    const endpoint = '/api/artifacts/sessions/s1/deliverables/file?path=image_tabular%2FAB.png&version=history-1&v=pnghash';
    const pdf = new URL(artifactImageFormatUrl(endpoint, 'pdf'), 'http://localhost');
    expect(pdf.pathname).toBe('/api/artifacts/sessions/s1/deliverables/file');
    expect(pdf.searchParams.get('path')).toBe('image_tabular/AB.pdf');
    expect(pdf.searchParams.get('version')).toBe('history-1');
    expect(pdf.searchParams.has('v')).toBe(false);
    expect(artifactImageFormatUrl(endpoint, 'png')).toContain('v=pnghash');
  });
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

  it('rewrites workspace absolute image paths with session', () => {
    expect(resolveArtifactImageSrc('/Users/apple/LLM/agent/phagescope/results/a.png', 'sess_1')).toBe(
      'http://api.test/artifacts/sessions/sess_1/workspace-file?path=' +
        encodeURIComponent('/Users/apple/LLM/agent/phagescope/results/a.png'),
    );
    expect(resolveArtifactImageSrc('Users/apple/LLM/agent/phagescope/results/a.png', 'sess_1')).toBe(
      'http://api.test/artifacts/sessions/sess_1/workspace-file?path=' +
        encodeURIComponent('/Users/apple/LLM/agent/phagescope/results/a.png'),
    );
    expect(isWorkspaceAbsoluteImagePath('/Users/apple/LLM/agent/phagescope/results/a.png')).toBe(true);
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

  it('cuts mangled container-absolute paths at the first productive segment', () => {
    expect(
      resolveArtifactImageSrc(
        '/app/runtime/session_x/raw_files/chat_tools/scientific_figure_generator/group_bar.png',
        'sess_1',
      ),
    ).toBe(
      'http://api.test/artifacts/sessions/sess_1/file?path=' +
        encodeURIComponent('raw_files/chat_tools/scientific_figure_generator/group_bar.png'),
    );
    expect(
      resolveArtifactImageSrc('app/runtime/session_x/deliverables/latest/image_tabular/fig.png', 'sess_1'),
    ).toBe(
      'http://api.test/artifacts/sessions/sess_1/file?path=' +
        encodeURIComponent('deliverables/latest/image_tabular/fig.png'),
    );
  });

  it('keeps workspace-absolute paths on the workspace endpoint despite productive segments', () => {
    expect(resolveArtifactImageSrc('/Users/apple/proj/results/a.png', 'sess_1')).toContain(
      'workspace-file',
    );
  });

  it('does not double-prefix deliverables/latest paths', () => {
    expect(
      resolveArtifactImageSrc('deliverables/latest/image_tabular/a.png', 'sess_1', 'deliverables'),
    ).toBe(
      'http://api.test/artifacts/sessions/sess_1/file?path=' +
        encodeURIComponent('deliverables/latest/image_tabular/a.png'),
    );
    expect(
      resolveArtifactImageSrc('deliverables/metformin_egfr_cohort_report.png', 'sess_1', 'deliverables'),
    ).toBe(
      'http://api.test/artifacts/sessions/sess_1/file?path=' +
        encodeURIComponent('deliverables/metformin_egfr_cohort_report.png'),
    );
  });

  it('still prefixes bare paths for typed sources', () => {
    expect(resolveArtifactImageSrc('image_tabular/a.png', 'sess_1', 'deliverables')).toBe(
      'http://api.test/artifacts/sessions/sess_1/file?path=' +
        encodeURIComponent('deliverables/latest/image_tabular/a.png'),
    );
    expect(resolveArtifactImageSrc('plots/a.png', 'sess_1', 'raw')).toBe(
      'http://api.test/artifacts/sessions/sess_1/file?path=' +
        encodeURIComponent('raw_files/plots/a.png'),
    );
    expect(resolveArtifactImageSrc('raw_files/plots/a.png', 'sess_1', 'raw')).toBe(
      'http://api.test/artifacts/sessions/sess_1/file?path=' +
        encodeURIComponent('raw_files/plots/a.png'),
    );
  });
});

describe('collectArtifactImagePathsFromResult', () => {
  it('collects from artifact_paths and storage', () => {
    const paths = collectArtifactImagePathsFromResult({
      artifact_paths: ['/a.png', 'b.jpg', '/Users/apple/LLM/agent/phagescope/results/c.png', 'readme.txt'],
      storage: { paths: ['c.webp'] },
    });
    expect(paths.sort()).toEqual(['/Users/apple/LLM/agent/phagescope/results/c.png', 'a.png', 'b.jpg', 'c.webp']);
  });

  it('dedupes and skips unsafe', () => {
    expect(
      collectArtifactImagePathsFromResult({
        artifact_paths: ['x.png', 'x.png', '../../../etc/passwd'],
      }),
    ).toEqual(['x.png']);
  });

  it('collects from artifact_gallery paths', () => {
    expect(
      collectArtifactImagePathsFromResult({
        artifact_gallery: [
          { path: 'gallery/a.png' },
          { path: '/gallery/b.webp' },
          { path: 'notes.txt' },
        ],
      }),
    ).toEqual(['gallery/a.png', 'gallery/b.webp']);
  });
});
