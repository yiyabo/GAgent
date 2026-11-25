import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import { App as AntdApp } from 'antd';

import ArtifactsPanel from './ArtifactsPanel';
import { artifactsApi, downloadSessionBatch } from '@api/artifacts';
import { useTasksStore } from '@store/tasks';
import { useLayoutStore } from '@store/layout';
import { useLanguageStore } from '@/i18n/language';

vi.mock('@api/artifacts', async () => {
  const actual = await vi.importActual<typeof import('@api/artifacts')>('@api/artifacts');
  return {
    ...actual,
    artifactsApi: {
      listSessionArtifacts: vi.fn(),
      listSessionDeliverables: vi.fn(),
      getSessionArtifactText: vi.fn(),
      getSessionDeliverableText: vi.fn(),
      renderArtifact: vi.fn(),
    },
    downloadSessionBatch: vi.fn(),
  };
});

const mockedArtifactsApi = vi.mocked(artifactsApi);
const mockedDownloadSessionBatch = vi.mocked(downloadSessionBatch);

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <AntdApp>
        <ArtifactsPanel sessionId="session_1776695619644_hb1elmfc0" />
      </AntdApp>
    </QueryClientProvider>
  );
}

function setupDeliverableItems() {
  mockedArtifactsApi.listSessionDeliverables.mockResolvedValue({
    session_id: 'session_1776695619644_hb1elmfc0',
    scope: 'latest',
    root_path: 'deliverables/latest',
    count: 2,
    items: [
      { name: 'report.md', path: 'report.md', extension: 'md', size: 1024, module: 'writing', status: 'final' },
      { name: 'figure.png', path: 'figures/figure.png', extension: 'png', size: 2048, module: 'image_tabular', status: 'final' },
    ],
    modules: {},
    paper_status: {},
    release_state: 'draft',
    public_release_ready: false,
    hidden_artifact_prefixes: [],
    available_versions: [],
  } as any);
}

describe('ArtifactsPanel', () => {
  afterEach(() => useLanguageStore.setState({ language: 'zh-CN' }));

  beforeEach(() => {
    vi.clearAllMocks();
    useLanguageStore.setState({ language: 'en' });
    useLayoutStore.setState({
      dagSidebarFullscreen: false,
      toggleDagSidebarFullscreen: vi.fn(),
    } as any);
    useTasksStore.setState({
      tasks: [
        { id: 1, parent_id: null, name: 'Root', task_type: 'root', status: 'completed' },
        { id: 8, parent_id: 1, name: 'Parent', task_type: 'analysis', status: 'completed' },
        { id: 34, parent_id: 8, name: 'Target', task_type: 'analysis', status: 'completed' },
      ],
      selectedTaskId: 34,
      selectedTask: null,
    } as any);

    mockedArtifactsApi.listSessionArtifacts.mockResolvedValue({
      session_id: 'session_1776695619644_hb1elmfc0',
      root_path: 'raw_files/task_1/task_8/task_34',
      count: 1,
      items: [
        {
          name: 'coverage_report.json',
          path: 'raw_files/task_1/task_8/task_34/merge/coverage_report.json',
          type: 'file',
          size: 128,
          extension: 'json',
          modified_at: null,
        },
      ],
    } as any);
    mockedArtifactsApi.listSessionDeliverables.mockResolvedValue({
      session_id: 'session_1776695619644_hb1elmfc0',
      scope: 'latest',
      root_path: 'deliverables/latest',
      count: 0,
      items: [],
      modules: {},
      paper_status: {},
      release_state: 'blocked',
      public_release_ready: false,
      hidden_artifact_prefixes: [],
      available_versions: [],
    } as any);
    mockedArtifactsApi.getSessionArtifactText.mockResolvedValue({
      path: 'raw_files/task_1/task_8/task_34/merge/coverage_report.json',
      content: '{"pass": false}',
      truncated: false,
    });
    mockedArtifactsApi.renderArtifact.mockResolvedValue({
      path: 'raw_files/task_1/task_8/task_34/merge/coverage_report.json',
      format: 'text',
      content: '{"pass": false}',
      rendered_at: new Date().toISOString(),
      cached: false,
      url: null,
    });
    mockedDownloadSessionBatch.mockResolvedValue(undefined);
  });

  it('opens raw file preview using sourcePath', async () => {
    renderPanel();

    await waitFor(() => {
      expect(mockedArtifactsApi.listSessionArtifacts).toHaveBeenCalledWith(
        'session_1776695619644_hb1elmfc0',
        expect.objectContaining({ view: 'workspace' })
      );
    });

    const fileNode = await screen.findByText('coverage_report.json');
    fireEvent.click(fileNode);

    await waitFor(() => {
      expect(mockedArtifactsApi.getSessionArtifactText).toHaveBeenCalledWith(
        'session_1776695619644_hb1elmfc0',
        'raw_files/task_1/task_8/task_34/merge/coverage_report.json',
        { maxBytes: 200000 }
      );
    });

    await waitFor(() => {
      expect(screen.queryByText('Select a file to preview')).not.toBeInTheDocument();
    });
  });

  it('shows deliverables and workspace files in one list without duplicating published sources', async () => {
    useTasksStore.setState({ tasks: [], selectedTaskId: null, selectedTask: null } as any);
    mockedArtifactsApi.listSessionDeliverables.mockResolvedValue({
      session_id: 'session_1776695619644_hb1elmfc0',
      scope: 'latest',
      root_path: 'deliverables/latest',
      count: 1,
      items: [
        {
          name: 'categories.png',
          path: 'image_tabular/categories.png',
          extension: 'png',
          size: 10,
          module: 'image_tabular',
          status: 'final',
          source_path: '/srv/runtime/session_1776695619644_hb1elmfc0/results/categories.png',
        },
      ],
      modules: {},
      paper_status: {},
      release_state: 'final',
      public_release_ready: true,
      hidden_artifact_prefixes: [],
      available_versions: [],
    } as any);
    mockedArtifactsApi.listSessionArtifacts.mockResolvedValue({
      session_id: 'session_1776695619644_hb1elmfc0',
      root_path: '',
      count: 2,
      items: [
        { name: 'categories.png', path: 'results/categories.png', type: 'file', size: 10, extension: 'png' },
        { name: 'screening.tsv', path: 'results/phagescope/screening.tsv', type: 'file', size: 5, extension: 'tsv' },
      ],
    } as any);

    renderPanel();

    expect(await screen.findByText('screening.tsv')).toBeInTheDocument();
    expect(screen.getAllByText('categories.png')).toHaveLength(1);
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /download\s*all/i }));
    await waitFor(() => {
      expect(mockedDownloadSessionBatch).toHaveBeenCalledWith('session_1776695619644_hb1elmfc0', [
        { path: 'image_tabular/categories.png', scope: 'deliverables' },
        { path: 'results/phagescope/screening.tsv', scope: 'raw' },
      ]);
    });
  });

  it('opens a deliverable requested before the panel mounted, even without plan tasks', async () => {
    useTasksStore.setState({ tasks: [], selectedTaskId: null, selectedTask: null } as any);
    setupDeliverableItems();
    mockedArtifactsApi.getSessionDeliverableText.mockResolvedValue({
      path: 'report.md',
      content: '# Report',
      truncated: false,
    } as any);
    useLayoutStore.setState({
      artifactFocusRequest: { sessionId: 'session_1776695619644_hb1elmfc0', path: 'report.md', nonce: 1 },
    } as any);

    renderPanel();

    await waitFor(() => {
      expect(screen.getAllByText('report.md').length).toBeGreaterThan(1);
    });
    expect(useLayoutStore.getState().artifactFocusRequest).toBeNull();
  });

  it('defaults to every session file and can toggle back to the selected task subtree', async () => {
    renderPanel();

    expect(await screen.findByText('Session Files')).toBeInTheDocument();

    await waitFor(() => {
      expect(mockedArtifactsApi.listSessionArtifacts).toHaveBeenCalledWith(
        'session_1776695619644_hb1elmfc0',
        expect.objectContaining({ view: 'workspace' })
      );
    });

    fireEvent.click(screen.getByRole('switch', { name: 'All Tasks' }));

    expect(await screen.findByText('Session Files · Task #34')).toBeInTheDocument();

    await waitFor(() => {
      expect(mockedArtifactsApi.listSessionArtifacts).toHaveBeenLastCalledWith(
        'session_1776695619644_hb1elmfc0',
        expect.objectContaining({ pathPrefix: 'raw_files/task_1/task_8/task_34' })
      );
    });
  });

  it('shows its labels in Chinese when the language is Chinese', async () => {
    useLanguageStore.setState({ language: 'zh-CN' });
    renderPanel();

    expect(await screen.findByText('会话文件')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('搜索文件路径')).toBeInTheDocument();
    expect(screen.queryByText('Session Files')).not.toBeInTheDocument();
  });

  describe('batch download', () => {
    it('renders Tree with checkable prop (checkboxes present)', async () => {
      setupDeliverableItems();
      const { container } = renderPanel();

      await screen.findByText('report.md');

      const checkboxes = container.querySelectorAll('.ant-tree-checkbox');
      expect(checkboxes.length).toBeGreaterThan(0);
    });

    it('disables Download Selected when no files are checked', async () => {
      setupDeliverableItems();
      renderPanel();

      await screen.findByText('report.md');

      const btn = screen.getByRole('button', { name: /download\s*selected/i });
      expect(btn).toBeDisabled();
    });

    it('enables Download Selected after checking a file', async () => {
      setupDeliverableItems();
      const { container } = renderPanel();

      await screen.findByText('report.md');

      const checkboxes = container.querySelectorAll('.ant-tree-checkbox');
      fireEvent.click(checkboxes[checkboxes.length - 1]);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /download\s*selected/i })).not.toBeDisabled();
      });
    });

    it('Download All sends deliverables and workspace files with their own scope', async () => {
      setupDeliverableItems();
      renderPanel();

      await screen.findByText('report.md');

      const btn = screen.getByRole('button', { name: /download\s*all/i });
      fireEvent.click(btn);

      await waitFor(() => {
        expect(mockedDownloadSessionBatch).toHaveBeenCalledWith(
          'session_1776695619644_hb1elmfc0',
          [
            { path: 'report.md', scope: 'deliverables' },
            { path: 'figures/figure.png', scope: 'deliverables' },
            { path: 'raw_files/task_1/task_8/task_34/merge/coverage_report.json', scope: 'raw' },
          ]
        );
      });
    });

    it('Download All calls downloadSessionBatch with raw entries using item.sourcePath', async () => {
      renderPanel();

        await screen.findByText('coverage_report.json');

      const btn = screen.getByRole('button', { name: /download\s*all/i });
      fireEvent.click(btn);

      await waitFor(() => {
        expect(mockedDownloadSessionBatch).toHaveBeenCalledWith(
          'session_1776695619644_hb1elmfc0',
          [
            {
              path: 'raw_files/task_1/task_8/task_34/merge/coverage_report.json',
              scope: 'raw',
            },
          ]
        );
      });
    });

    it('Download Selected calls downloadSessionBatch with only checked items', async () => {
      setupDeliverableItems();
      const { container } = renderPanel();

      await screen.findByText('report.md');

      const checkboxes = container.querySelectorAll('.ant-tree-checkbox');
      fireEvent.click(checkboxes[checkboxes.length - 1]);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /download\s*selected/i })).not.toBeDisabled();
      });

      fireEvent.click(screen.getByRole('button', { name: /download\s*selected/i }));

      await waitFor(() => {
        expect(mockedDownloadSessionBatch).toHaveBeenCalledWith(
          'session_1776695619644_hb1elmfc0',
          [{ path: 'report.md', scope: 'deliverables' }]
        );
      });
    });

    it('toggles loading state during Download All', async () => {
      setupDeliverableItems();
      renderPanel();

      await screen.findByText('report.md');

      let resolveDownload: (v?: unknown) => void;
      mockedDownloadSessionBatch.mockImplementation(
        () => new Promise((resolve) => { resolveDownload = resolve; })
      );

      const btn = screen.getByRole('button', { name: /download\s*all/i });
      fireEvent.click(btn);

      await waitFor(() => expect(mockedDownloadSessionBatch).toHaveBeenCalled());
      expect(btn).toBeDisabled();

      resolveDownload!();

      await waitFor(() => expect(btn).not.toBeDisabled());
    });

    it('disables Download All when no files are visible', async () => {
      mockedArtifactsApi.listSessionArtifacts.mockResolvedValue({
        session_id: 'session_1776695619644_hb1elmfc0',
        root_path: '',
        count: 0,
        items: [],
      } as any);
      renderPanel();

      await waitFor(() => {
        expect(screen.getByText('No files')).toBeInTheDocument();
      });

      expect(screen.getByRole('button', { name: /download\s*all/i })).toBeDisabled();
    });
  });
});
