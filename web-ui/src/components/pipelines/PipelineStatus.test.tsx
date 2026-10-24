import React from 'react';
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { NodeRun } from '@api/pipelines';
import { StatusTag } from './PipelineStatus';
import { specIndex, toFlow } from './graphModel';
import { chainGraph, TEST_CATALOG } from './testCatalog';

describe('pipeline state semantics', () => {
  it.each([
    ['cached', 'success'], ['succeeded', 'success'], ['failed', 'error'], ['running', 'running'],
    ['warning', 'warning'], ['cancelled', 'neutral'], ['skipped', 'neutral'], ['pending', 'pending'],
  ])('%s has its own icon and label with the %s palette', (status, tone) => {
    const { container } = render(<StatusTag status={status} />);
    const badge = container.querySelector('.pl-status')!;
    expect(badge).toHaveClass(`pl-status-${tone}`);
    expect(badge.textContent?.length).toBeGreaterThan(0);
    expect(badge.querySelector('svg')).not.toBeNull();
  });

  it('marks data available only when that port has an output, including cached outputs', () => {
    const run: NodeRun = { status: 'cached', error: null, started_at: null, finished_at: null, detail: {}, outputs: { proteins: { name: 'proteins.faa' } } };
    const graph = chainGraph();
    const ready = toFlow(graph, specIndex(TEST_CATALOG), { genes: run });
    expect(ready.nodes.find((node) => node.id === 'hits')?.data.readyInputs).toEqual(['query']);
    expect(ready.edges.find((edge) => edge.source === 'genes' && edge.target === 'hits')?.className).toContain('pl-edge-ready');
    const missing = toFlow(graph, specIndex(TEST_CATALOG), { genes: { ...run, outputs: {} } });
    expect(missing.nodes.find((node) => node.id === 'hits')?.data.readyInputs).toEqual([]);
    expect(missing.edges.find((edge) => edge.source === 'genes' && edge.target === 'hits')?.className).toContain('pl-edge-pending');
  });
});
