import { describe, expect, it } from 'vitest';
import { collectToolResultsFromMetadata } from './toolResults';

describe('collectToolResultsFromMetadata', () => {
  it('preserves artifact-related fields during normalization', () => {
    const [payload] = collectToolResultsFromMetadata([
      {
        name: 'code_executor',
        summary: 'generated figure',
        result: {
          success: true,
          artifact_paths: ['tool_outputs/run_1/figure.png'],
          storage: {
            relative: {
              preview_path: 'tool_outputs/run_1/figure.png',
            },
          },
          deliverables: {
            manifest_path: 'deliverables/manifest_latest.json',
          },
          artifact_gallery: [
            {
              path: 'tool_outputs/run_1/figure.png',
              display_name: 'figure.png',
              source_tool: 'code_executor',
            },
          ],
        },
      },
    ]);

    expect(payload.result?.artifact_paths).toEqual(['tool_outputs/run_1/figure.png']);
    expect(payload.result?.storage).toMatchObject({
      relative: { preview_path: 'tool_outputs/run_1/figure.png' },
    });
    expect(payload.result?.deliverables).toMatchObject({
      manifest_path: 'deliverables/manifest_latest.json',
    });
    expect(payload.result?.artifact_gallery?.[0]).toMatchObject({
      path: 'tool_outputs/run_1/figure.png',
      display_name: 'figure.png',
      source_tool: 'code_executor',
      mime_family: 'image',
    });
  });
});
