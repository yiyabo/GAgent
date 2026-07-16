import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import { App as AntdApp } from 'antd';

import ArtifactsPanel from './ArtifactsPanel';
import { artifactsApi, downloadSessionBatch } from '@api/artifacts';
import { useTasksStore } from '@store/tasks';
import { useLayoutStore } from '@store/layout';

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
  beforeEach(() => {
    vi.clearAllMocks();
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

  it('opens raw file preview using sourcePath instead of trimmed display path', async () => {
    renderPanel();

    fireEvent.click(screen.getByRole('radio', { name: 'Raw Files' }));

    await waitFor(() => {
      expect(mockedArtifactsApi.listSessionArtifacts).toHaveBeenCalledWith(
        'session_1776695619644_hb1elmfc0',
        expect.objectContaining({ pathPrefix: 'raw_files' })
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

  it('defaults to Raw Files when the session has no plan tasks', async () => {
    useTasksStore.setState({
      tasks: [],
      selectedTaskId: null,
      selectedTask: null,
    } as any);

    renderPanel();

    await waitFor(() => {
      expect(mockedArtifactsApi.listSessionArtifacts).toHaveBeenCalledWith(
        'session_1776695619644_hb1elmfc0',
        expect.objectContaining({ pathPrefix: 'raw_files' })
      );
    });

    expect(screen.getByText(/No plan bound/i)).toBeInTheDocument();
  });

  it('defaults raw browsing to all raw files and can toggle back to the selected task subtree', async () => {
    renderPanel();

    fireEvent.click(screen.getByRole('radio', { name: 'Raw Files' }));

    expect(await screen.findByText('Raw Files · All Tasks')).toBeInTheDocument();

    await waitFor(() => {
      expect(mockedArtifactsApi.listSessionArtifacts).toHaveBeenCalledWith(
        'session_1776695619644_hb1elmfc0',
        expect.objectContaining({ pathPrefix: 'raw_files' })
      );
    });

    fireEvent.click(screen.getByRole('switch', { name: 'All Raw Files' }));

    expect(await screen.findByText('Raw Files · Task #34')).toBeInTheDocument();

    await waitFor(() => {
      expect(mockedArtifactsApi.listSessionArtifacts).toHaveBeenLastCalledWith(
        'session_1776695619644_hb1elmfc0',
        expect.objectContaining({ pathPrefix: 'raw_files/task_1/task_8/task_34' })
      );
    });
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

      const btn = screen.getByRole('button', { name: /download selected/i });
      expect(btn).toBeDisabled();
    });

    it('enables Download Selected after checking a file', async () => {
      setupDeliverableItems();
      const { container } = renderPanel();

      await screen.findByText('report.md');

      const checkboxes = container.querySelectorAll('.ant-tree-checkbox');
      fireEvent.click(checkboxes[checkboxes.length - 1]);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /download selected/i })).not.toBeDisabled();
      });
    });

    it('Download All calls downloadSessionBatch with deliverable entries using item.path', async () => {
      setupDeliverableItems();
      renderPanel();

      await screen.findByText('report.md');

      const btn = screen.getByRole('button', { name: /download all/i });
      fireEvent.click(btn);

      await waitFor(() => {
        expect(mockedDownloadSessionBatch).toHaveBeenCalledWith(
          'session_1776695619644_hb1elmfc0',
          [
            { path: 'report.md', scope: 'deliverables' },
            { path: 'figures/figure.png', scope: 'deliverables' },
          ]
        );
      });
    });

    it('Download All calls downloadSessionBatch with raw entries using item.sourcePath', async () => {
      renderPanel();

      fireEvent.click(screen.getByRole('radio', { name: 'Raw Files' }));

      await screen.findByText('coverage_report.json');

      const btn = screen.getByRole('button', { name: /download all/i });
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
        expect(screen.getByRole('button', { name: /download selected/i })).not.toBeDisabled();
      });

      fireEvent.click(screen.getByRole('button', { name: /download selected/i }));

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

      const btn = screen.getByRole('button', { name: /download all/i });
      fireEvent.click(btn);

      await waitFor(() => expect(mockedDownloadSessionBatch).toHaveBeenCalled());
      expect(btn).toBeDisabled();

      resolveDownload!();

      await waitFor(() => expect(btn).not.toBeDisabled());
    });

    it('disables Download All when no files are visible', async () => {
      renderPanel();

      await waitFor(() => {
        expect(screen.getByText('No files')).toBeInTheDocument();
      });

      expect(screen.getByRole('button', { name: /download all/i })).toBeDisabled();
    });
  });
});
