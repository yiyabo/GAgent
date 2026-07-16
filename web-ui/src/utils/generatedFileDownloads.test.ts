import { describe, expect, it } from 'vitest';
import {
  classifyDownloadScope,
  collectGeneratedDownloadFiles,
  normalizeSessionRelativePath,
  toDeliverableApiPath,
} from './generatedFileDownloads';

describe('generatedFileDownloads', () => {
  it('normalizes absolute session paths', () => {
    const path = normalizeSessionRelativePath(
      '/home/zczhao/Phage-Agent/runtime/session_abc/raw_files/tmp/AlphaMissense_中文翻译.md',
      'session_abc',
    );
    expect(path).toBe('raw_files/tmp/AlphaMissense_中文翻译.md');
  });

  it('maps bare filenames into raw_files/tmp', () => {
    expect(normalizeSessionRelativePath('report.md', 'session_abc')).toBe('raw_files/tmp/report.md');
  });

  it('collects paths from tool results and message text', () => {
    const files = collectGeneratedDownloadFiles({
      sessionId: 'session_abc',
      toolResults: [
        {
          name: 'file_operations',
          result: {
            success: true,
            path: '/home/x/runtime/session_abc/raw_files/tmp/a.md',
          },
        },
        {
          name: 'manuscript_writer',
          result: {
            success: true,
            effective_output_path: 'raw_files/tmp/b.md',
          },
        },
      ],
      content:
        'Saved to `/home/x/Phage-Agent/runtime/session_abc/raw_files/tmp/c.md` and raw_files/tmp/d.pdf',
    });
    const names = files.map((f) => f.name).sort();
    expect(names).toEqual(['a.md', 'b.md', 'c.md', 'd.pdf']);
    expect(files.every((f) => f.scope === 'raw')).toBe(true);
  });

  it('classifies deliverable paths', () => {
    expect(classifyDownloadScope('deliverables/latest/docs/report.md')).toBe('deliverables');
    expect(toDeliverableApiPath('deliverables/latest/docs/report.md')).toBe('docs/report.md');
  });
});
