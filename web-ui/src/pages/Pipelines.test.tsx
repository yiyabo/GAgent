import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App as AntdApp } from 'antd';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { NodeRun, PipelineGraph, PipelineRun } from '@api/pipelines';
import { pipelinesApi } from '@api/pipelines';
import { TEST_CATALOG, chainGraph, pipelineDetail } from '@components/pipelines/testCatalog';

// A full antd page with modals and a debounced save: generous on a loaded CI runner.
vi.setConfig({ testTimeout: 30_000 });

import Pipelines from './Pipelines';

vi.mock('@api/pipelines', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@api/pipelines')>();
  return {
    ...actual,
    pipelinesApi: {
      status: vi.fn(),
      catalog: vi.fn(),
      list: vi.fn(),
      create: vi.fn(),
      detail: vi.fn(),
      save: vi.fn(),
      remove: vi.fn(),
      freeze: vi.fn(),
      validate: vi.fn(),
      files: vi.fn(),
      upload: vi.fn(),
      runs: vi.fn(),
      startRun: vi.fn(),
      run: vi.fn(),
      cancelRun: vi.fn(),
      preview: vi.fn(),
      downloadUrl: vi.fn(() => '#'),
      agentMessages: vi.fn(),
      sendToAgent: vi.fn(),
      clearAgent: vi.fn(),
      undoAgent: vi.fn(),
      answerRunRequest: vi.fn(),
    },
  };
});

const status = vi.hoisted(() => ({ enabled: true as boolean | null }));
vi.mock('@hooks/usePipelines', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@hooks/usePipelines')>();
  return { ...actual, usePipelinesStatus: () => status.enabled };
});

// React Flow needs a real layout engine; the canvas is exercised by the visual smoke instead.
vi.mock('@components/pipelines/PipelineCanvas', () => ({
  default: (props: {
    graph: PipelineGraph;
    readOnly?: boolean;
    runNodes?: Record<string, NodeRun>;
    onChange?: (update: (graph: PipelineGraph) => PipelineGraph) => void;
    onSelectNode: (id: string | null) => void;
  }) => (
    <div data-testid="canvas" data-readonly={String(Boolean(props.readOnly))}>
      <span data-testid="canvas-nodes">{Object.keys(props.graph.nodes).join(',')}</span>
      <span data-testid="canvas-status">
        {Object.entries(props.runNodes || {})
          .map(([id, node]) => `${id}:${node.status}`)
          .join(',')}
      </span>
      <button
        type="button"
        onClick={() =>
          props.onChange?.((graph) => ({
            ...graph,
            nodes: { ...graph.nodes, extra: { type: 'prodigal.predict', inputs: { genome: { node: 'dl', port: 'genome' } } } },
          }))
        }
      >
        edit-graph
      </button>
      <button type="button" onClick={() => props.onSelectNode('dl')}>
        select-dl
      </button>
    </div>
  ),
}));

const api = vi.mocked(pipelinesApi);

const Where = () => {
  const location = useLocation();
  return <div data-testid="where">{`${location.pathname}${location.search}`}</div>;
};

function renderPage(path = '/pipelines') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AntdApp>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route
              path="/pipelines"
              element={
                <>
                  <Pipelines />
                  <Where />
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </AntdApp>
    </QueryClientProvider>,
  );
}

const nodeRun = (state: NodeRun['status']): NodeRun => ({
  status: state,
  error: null,
  started_at: '2026-10-24 08:00:00',
  finished_at: '2026-10-24 08:01:00',
  outputs: {},
  detail: {},
});

const pipelineRun = (overrides: Partial<PipelineRun> = {}): PipelineRun => ({
  id: 'r1',
  pipeline_id: 'pl1',
  version: 1,
  status: 'queued',
  inputs: {},
  error: null,
  cancel_requested: false,
  created_at: '2026-10-24 08:00:00',
  started_at: null,
  finished_at: null,
  graph: chainGraph(),
  nodes: {},
  ...overrides,
});

const where = () => screen.getByTestId('where').textContent;

beforeEach(() => {
  vi.clearAllMocks();
  status.enabled = true;
  api.catalog.mockResolvedValue(TEST_CATALOG);
  api.list.mockResolvedValue({ pipelines: [] });
  api.files.mockResolvedValue({ files: [] });
  api.runs.mockResolvedValue({ runs: [] });
  api.detail.mockResolvedValue(pipelineDetail());
  api.agentMessages.mockResolvedValue({ messages: [], busy: false });
});

describe('the pipelines page', () => {
  it('says so when the server has pipelines switched off, and asks nothing else', async () => {
    status.enabled = false;
    renderPage();
    expect(await screen.findByText('这台服务器没有开启节点流水线。')).toBeInTheDocument();
    expect(api.catalog).not.toHaveBeenCalled();
  });

  it('lists the pipelines and starts a new one from a template', async () => {
    api.list.mockResolvedValue({
      pipelines: [
        {
          id: 'old',
          name: 'First try',
          description: 'lambda only',
          latest_version: 2,
          created_at: '2026-10-20 08:00:00',
          updated_at: '2026-10-21 08:00:00',
          last_run_status: 'failed',
        },
      ],
    });
    api.create.mockResolvedValue(pipelineDetail({ id: 'pl2', name: '噬菌体基础分析：lambda 对比 T7' }));
    renderPage();

    expect(await screen.findByText('First try')).toBeInTheDocument();
    expect(screen.getByText('版本 2')).toBeInTheDocument();
    expect(screen.getByText('失败')).toBeInTheDocument();
    expect(screen.getByText('噬菌体基础分析：lambda 对比 T7')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '使用这个模板' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('名称')).toHaveValue('噬菌体基础分析：lambda 对比 T7');
    fireEvent.click(within(dialog).getByRole('button', { name: /创\s*建/ }));

    await waitFor(() => expect(api.create).toHaveBeenCalledWith('噬菌体基础分析：lambda 对比 T7', { template: 'phage_lambda_t7' }));
    expect(await screen.findByTestId('canvas-nodes')).toHaveTextContent('dl,genes,db,hits,report');
    expect(where()).toBe('/pipelines?id=pl2');
  });

  it('opens an existing pipeline from the list', async () => {
    api.list.mockResolvedValue({
      pipelines: [
        { id: 'pl1', name: 'Lambda basics', description: null, latest_version: 0, created_at: '', updated_at: '', last_run_status: null },
      ],
    });
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Lambda basics/ }));
    expect(await screen.findByTestId('canvas')).toBeInTheDocument();
    expect(where()).toBe('/pipelines?id=pl1');
    expect(screen.getByRole('textbox', { name: '名称' })).toHaveValue('Lambda basics');
  });

  it('saves every edit on its own and shows the problems in the UI language', async () => {
    api.detail.mockResolvedValue(
      pipelineDetail({
        validation: {
          ok: false,
          errors: [{ code: 'missing_param', message: 'accessions is required', node: 'dl', param: 'accessions' }],
          warnings: [],
          order: [],
        },
      }),
    );
    api.save.mockImplementation(async (_id, changes) =>
      pipelineDetail({ draft: changes.graph, validation: { ok: true, errors: [], warnings: [], order: [] } }),
    );
    renderPage('/pipelines?id=pl1');

    expect(await screen.findByText('必须填写“登录号”')).toBeInTheDocument();
    expect(screen.getByText('1 个问题')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /运行$/ })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'edit-graph' }));
    expect(screen.getByText('有未保存的修改')).toBeInTheDocument();
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1), { timeout: 4000 });
    const [id, changes] = api.save.mock.calls[0];
    expect(id).toBe('pl1');
    expect(changes.graph?.nodes.extra).toEqual({ type: 'prodigal.predict', inputs: { genome: { node: 'dl', port: 'genome' } } });

    expect(await screen.findByText('已保存')).toBeInTheDocument();
    expect(screen.queryByText('必须填写“登录号”')).toBeNull();
    expect(screen.getByText('可以运行')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /运行$/ })).toBeEnabled();
  });

  it('shows the selected node in the inspector', async () => {
    renderPage('/pipelines?id=pl1');
    fireEvent.click(await screen.findByRole('button', { name: 'select-dl' }));
    expect(await screen.findByRole('heading', { name: 'NCBI 基因组下载' })).toBeInTheDocument();
    expect(screen.getByText('登录号')).toBeInTheDocument();
    expect(screen.getByDisplayValue('NC_001416.1')).toBeInTheDocument();
    expect(screen.getByDisplayValue('lambda')).toBeInTheDocument();
  });

  it('runs the pipeline, then shows the run with every node status', async () => {
    api.startRun.mockResolvedValue(pipelineRun());
    api.run.mockResolvedValue(
      pipelineRun({ status: 'succeeded', nodes: { dl: nodeRun('succeeded'), genes: nodeRun('cached') }, finished_at: '2026-10-24 08:05:00' }),
    );
    renderPage('/pipelines?id=pl1');

    fireEvent.click(await screen.findByRole('button', { name: /运行$/ }));
    await waitFor(() => expect(api.startRun).toHaveBeenCalledWith('pl1'));
    expect(api.save).not.toHaveBeenCalled();
    expect(await screen.findByText('运行 r1')).toBeInTheDocument();
    expect(where()).toBe('/pipelines?id=pl1&run=r1');
    await waitFor(() => expect(screen.getByTestId('canvas-status')).toHaveTextContent('dl:succeeded,genes:cached'));
    expect(screen.getByTestId('canvas')).toHaveAttribute('data-readonly', 'true');

    fireEvent.click(screen.getByRole('button', { name: /回到编辑/ }));
    expect(where()).toBe('/pipelines?id=pl1');
    expect(await screen.findByTestId('canvas')).toHaveAttribute('data-readonly', 'false');
  });

  it('previews a Markdown report as formatted text', async () => {
    api.run.mockResolvedValue(
      pipelineRun({
        status: 'succeeded',
        nodes: {
          dl: { ...nodeRun('succeeded'), outputs: { genome: { name: 'report.md', type: 'report', kind: 'file', size: 40 } } },
        },
      }),
    );
    api.preview.mockResolvedValue({ kind: 'text', name: 'report.md', size: 40, truncated: false, text: '# Lambda vs T7\n\n| GC | 49.86% |\n|---|---|\n| T7 | 48.40% |\n' });
    renderPage('/pipelines?id=pl1&run=r1');
    fireEvent.click(await screen.findByRole('button', { name: 'select-dl' }));
    fireEvent.click(await screen.findByRole('button', { name: /预览/ }));
    expect(await screen.findByRole('heading', { name: 'Lambda vs T7' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '48.40%' })).toBeInTheDocument();
    expect(api.preview).toHaveBeenCalledWith('r1', 'dl', 'genome', undefined);
  });

  it('explains why a run could not start', async () => {
    const { PipelinesApiError } = await import('@api/pipelines');
    api.startRun.mockRejectedValue(
      new PipelinesApiError('the pipeline cannot run yet', 422, {
        ok: false,
        errors: [{ code: 'missing_input', message: 'input query is not connected', node: 'hits', port: 'query' }],
        warnings: [],
        order: [],
      }),
    );
    renderPage('/pipelines?id=pl1');
    fireEvent.click(await screen.findByRole('button', { name: /运行$/ }));
    expect(await screen.findByText('输入“查询蛋白”还没有连接')).toBeInTheDocument();
    expect(await screen.findByText('无法开始运行：the pipeline cannot run yet')).toBeInTheDocument();
    expect(where()).toBe('/pipelines?id=pl1');
  });
});
