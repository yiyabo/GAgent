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

describe('phage_kg tool results', () => {
  it('maps graph edges and evidence onto the graph card fields', () => {
    const [payload] = collectToolResultsFromMetadata([
      {
        name: 'phage_kg',
        summary: 'phage_kg subgraph',
        result: {
          success: true,
          operation: 'subgraph',
          graph_version: 'g1',
          edges: [{ source: 'e:a', target: 'e:b', source_name: 'phage therapy', target_name: 'endolysin', description: 'uses' }],
          evidence: [{ file_path: 'paper_1', content: 'Endolysins lyse cells.' }],
        },
      },
    ]);
    expect(payload.result?.triples).toEqual([{ entity1: 'phage therapy', relation: 'uses', entity2: 'endolysin' }]);
    expect(payload.result?.prompt).toContain('[paper_1] Endolysins lyse cells.');
    expect(payload.result?.metadata).toEqual({ graph_version: 'g1' });
  });
});
