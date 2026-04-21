import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import ArtifactsPanel from './ArtifactsPanel';
import { artifactsApi } from '@api/artifacts';
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
  };
});

const mockedArtifactsApi = vi.mocked(artifactsApi);

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
      <ArtifactsPanel sessionId="session_1776695619644_hb1elmfc0" />
    </QueryClientProvider>
  );
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
});