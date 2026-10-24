import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { pipelinesApi, type OutputPreview } from '@api/pipelines';
import PipelineOutputPreview from './PipelineOutputPreview';

vi.mock('@api/pipelines', () => ({
  pipelinesApi: { preview: vi.fn(), downloadUrl: vi.fn(() => '/download') },
}));
const api = vi.mocked(pipelinesApi);
const props = { runId: 'run', nodeId: 'first', target: { port: 'report', title: 'Report' }, onOpen: vi.fn(), onClose: vi.fn() };

beforeEach(() => {
  vi.resetAllMocks();
  api.downloadUrl.mockReturnValue('/download');
});

describe('inline output preview', () => {
  it('ignores an old response when the user switches nodes', async () => {
    let finishOld!: (data: OutputPreview) => void;
    api.preview.mockReturnValueOnce(new Promise((resolve) => { finishOld = resolve; }));
    api.preview.mockResolvedValueOnce({ kind: 'text', name: 'report.md', text: '# Second result' });
    const view = render(<PipelineOutputPreview {...props} />);
    view.rerender(<PipelineOutputPreview {...props} nodeId="second" />);
    expect(await screen.findByRole('heading', { name: 'Second result' })).toBeInTheDocument();
    await act(async () => finishOld({ kind: 'text', name: 'report.md', text: '# Stale first result' }));
    expect(screen.queryByText('Stale first result')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Second result' })).toBeInTheDocument();
  });

  it('keeps TSV values as text and warns when the visible rows are limited', async () => {
    const text = ['id\tvalue', ...Array.from({ length: 230 }, (_, i) => `${i}\t1e-05`)].join('\n');
    api.preview.mockResolvedValue({ kind: 'text', name: 'results.tsv', text });
    render(<PipelineOutputPreview {...props} />);
    expect(await screen.findByRole('table')).toBeInTheDocument();
    expect(screen.getAllByRole('row')).toHaveLength(201);
    expect(screen.getAllByRole('cell', { name: '1e-05' })).toHaveLength(200);
    expect(screen.getByText(/只显示开头部分|Only the beginning/)).toBeInTheDocument();
  });

  it('shows a failed read and retries without discarding the selected output', async () => {
    api.preview.mockRejectedValueOnce(new Error('temporary read failure'));
    api.preview.mockResolvedValueOnce({ kind: 'text', name: 'report.md', text: '# Available again' });
    render(<PipelineOutputPreview {...props} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('temporary read failure');
    fireEvent.click(screen.getByRole('button', { name: /重\s*试|Retry/ }));
    expect(await screen.findByRole('heading', { name: 'Available again' })).toBeInTheDocument();
    expect(api.preview).toHaveBeenLastCalledWith('run', 'first', 'report', undefined);
  });

  it('opens a directory member with its complete relative path', async () => {
    api.preview.mockResolvedValue({ kind: 'dir', entries: [{ path: 'nested/results.tsv' }] });
    render(<PipelineOutputPreview {...props} target={{ port: 'files', title: 'Files' }} />);
    fireEvent.click(await screen.findByRole('button', { name: /打\s*开|Open/ }));
    await waitFor(() => expect(props.onOpen).toHaveBeenCalledWith({ port: 'files', path: 'nested/results.tsv', title: 'nested/results.tsv' }));
  });
});
