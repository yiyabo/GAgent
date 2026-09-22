import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App as AntdApp } from 'antd';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { artifactsApi } from '@api/artifacts';
import { useChatStore } from '@store/chat';
import type { PlanResultItem, PlanTaskNode } from '@/types';
import { ExecutionResult, TaskDrawerContent } from './TaskDetailSections';

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

describe('ExecutionResult', () => {
  it('renders a readable failure summary with humanized failure kind', () => {
    const onReverify = vi.fn();

    const { container } = render(
      <ExecutionResult
        resultLoading={false}
        taskResult={{
          task_id: 22,
          status: 'failed',
          content: 'candidate files downloaded',
          metadata: {
            execution_status: 'completed',
            failure_kind: 'contract_mismatch',
            artifact_verification: {
              actual_outputs: ['/tmp/generated.md'],
              expected_deliverables: ['core_technologies_evidence.md'],
            },
            verification: {
              status: 'failed',
              checks_total: 2,
              checks_passed: 1,
              blocking: true,
              generated: false,
              failures: [
                {
                  type: 'pdb_residue_present',
                  path: '/tmp/1RH5_SEC.pdb',
                  message: 'SEC residue not found',
                },
              ],
              evidence: {
                artifact_paths: ['/tmp/1RH5_SEC.pdb'],
              },
            },
          },
        }}
        cachedResult={undefined}
        canVerify
        onReverify={onReverify}
      />
    );

    expect(screen.getByText('Verification failed')).toBeInTheDocument();
    expect(screen.getByText('Execution completed, but verification failed')).toBeInTheDocument();
    expect(screen.getByText('1/2 checks passed · blocking')).toBeInTheDocument();
    expect(screen.getByText('Result summary')).toBeInTheDocument();
    expect(screen.getByText('Deliverable contract mismatch')).toBeInTheDocument();
    expect(screen.getByText('pdb_residue_present')).toBeInTheDocument();
    expect(screen.getByText('/tmp/1RH5_SEC.pdb')).toBeInTheDocument();
    expect(screen.getByText('SEC residue not found')).toBeInTheDocument();
    expect(screen.getByText('candidate files downloaded')).toBeInTheDocument();
    // No raw metadata/verification JSON in the default view.
    expect(container.textContent).not.toContain('"execution_status"');
    expect(container.textContent).not.toContain('"artifact_verification"');
    expect(container.textContent).not.toContain('"checks_total"');
    fireEvent.click(screen.getByRole('button', { name: 'Re-verify' }));
    expect(onReverify).toHaveBeenCalledTimes(1);
  });

  it('separates completed execution from published artifacts', () => {
    render(
      <ExecutionResult
        resultLoading={false}
        taskResult={{
          task_id: 19,
          status: 'completed',
          content: 'Collected recent review metadata.',
          metadata: {
            execution_status: 'completed',
            verification: {
              status: 'passed',
              checks_total: 2,
              checks_passed: 2,
              blocking: true,
              generated: false,
              failures: [],
            },
          },
        }}
        cachedResult={undefined}
      />
    );

    expect(screen.getByText('No published artifact')).toBeInTheDocument();
    expect(
      screen.getByText('Execution finished without a published artifact')
    ).toBeInTheDocument();
    expect(screen.getByText('Result summary')).toBeInTheDocument();
    expect(screen.getByText('Collected recent review metadata.')).toBeInTheDocument();
    expect(screen.getByText('2/2 checks passed · blocking')).toBeInTheDocument();
  });
});

describe('TaskDrawerContent', () => {
  const activeTask: PlanTaskNode = {
    id: 7,
    name: 'Evidence collection',
    status: 'completed',
    instruction: 'Collect review evidence for the target protein.',
    dependencies: [3],
    context_combined: 'Combined context text',
    context_meta: { source_count: 4 },
    metadata: { priority: 'high' },
    created_at: '2026-09-01T10:00:00Z',
  };

  const taskResult: PlanResultItem = {
    task_id: 7,
    status: 'completed',
    content: 'Findings summary for the collected evidence.',
    metadata: {
      execution_status: 'completed',
      published_artifacts: {
        'report.evidence_md': {
          alias: 'report.evidence_md',
          path: '/tmp/run/evidence_report.md',
          deliverable_path: 'docs/evidence_report.md',
        },
      },
      verification: {
        status: 'passed',
        checks_total: 44,
        checks_passed: 44,
        blocking: true,
        generated: false,
        failures: [],
      },
    },
  };

  const renderDrawer = (resultOverride: PlanResultItem = taskResult) => {
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
          <TaskDrawerContent
            activeTask={activeTask}
            handleDependencyClick={vi.fn()}
            recentToolResults={[]}
            resultLoading={false}
            taskResult={resultOverride}
            cachedResult={undefined}
          />
        </AntdApp>
      </QueryClientProvider>
    );
  };

  beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({
      currentSession: { session_id: 'sess-1' },
    } as any);
    mockedArtifactsApi.getSessionDeliverableText.mockResolvedValue({
      path: 'docs/evidence_report.md',
      content: '# Evidence Report\n\nCollected findings.',
      truncated: false,
    });
    mockedArtifactsApi.renderArtifact.mockResolvedValue({
      path: 'docs/evidence_report.md',
      format: 'html',
      content: '<p>rendered</p>',
      rendered_at: new Date().toISOString(),
    } as any);
  });

  afterEach(() => {
    useChatStore.setState({ currentSession: null } as any);
  });

  it('shows a clean readable default view without bare JSON', () => {
    const { container } = renderDrawer();

    expect(screen.getByText('Status')).toBeInTheDocument();
    expect(screen.getByText('Result summary')).toBeInTheDocument();
    expect(
      screen.getByText('Findings summary for the collected evidence.')
    ).toBeInTheDocument();
    expect(screen.getByText('44/44 checks passed · blocking')).toBeInTheDocument();

    // Published artifacts promoted to a first-level card section.
    expect(screen.getByText('Published Artifacts (1)')).toBeInTheDocument();
    expect(screen.getByText('evidence_report.md')).toBeInTheDocument();
    expect(screen.getByText('report.evidence_md')).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Open evidence_report.md in new tab' });
    expect(link.getAttribute('href')).toContain(
      '/artifacts/sessions/sess-1/deliverables/file?path=docs%2Fevidence_report.md'
    );
    expect(link.getAttribute('target')).toBe('_blank');

    // Instruction collapsed by default.
    expect(screen.getByText('Task instruction')).toBeInTheDocument();
    expect(
      screen.queryByText('Collect review evidence for the target protein.')
    ).not.toBeInTheDocument();

    // Bare JSON stays out of the default (collapsed) view.
    expect(container.textContent).not.toContain('context_meta');
    expect(container.textContent).not.toContain('"source_count"');
    expect(container.textContent).not.toContain('"priority"');
    expect(container.textContent).not.toContain('"execution_status"');
    expect(container.textContent).not.toContain('"checks_total"');
    expect(screen.queryByText('Token Consumption')).not.toBeInTheDocument();
    expect(screen.queryByText('Copy task JSON')).not.toBeInTheDocument();
  });

  it('expands the task instruction on demand', async () => {
    renderDrawer();

    expect(
      screen.queryByText('Collect review evidence for the target protein.')
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('Task instruction'));

    expect(
      await screen.findByText('Collect review evidence for the target protein.')
    ).toBeInTheDocument();
  });

  it('opens an in-drawer preview modal when clicking an artifact card', async () => {
    renderDrawer();

    fireEvent.click(screen.getByText('evidence_report.md'));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('evidence_report.md')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /download/i })).toBeInTheDocument();

    // The modal previews the deliverable through the shared artifacts API.
    await waitFor(() => {
      expect(mockedArtifactsApi.renderArtifact).toHaveBeenCalledWith(
        'sess-1',
        'docs/evidence_report.md',
        { sourceType: 'deliverables' }
      );
    });
    await waitFor(() => {
      expect(mockedArtifactsApi.getSessionDeliverableText).toHaveBeenCalledWith(
        'sess-1',
        'docs/evidence_report.md',
        expect.objectContaining({ maxBytes: 200000 })
      );
    });

    // Closing the modal returns to the drawer without navigation.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(screen.getByText('Published Artifacts (1)')).toBeInTheDocument();
  });

  it('falls back to the raw artifacts endpoint when deliverables text 404s', async () => {
    const csvResult: PlanResultItem = {
      task_id: 7,
      status: 'completed',
      content: 'produced cleaned_wide.csv',
      metadata: {
        execution_status: 'completed',
        published_artifacts: {
          'data.cleaned_wide_csv': {
            alias: 'data.cleaned_wide_csv',
            path: '/app/runtime/sess-1/_scratch/plan173_task7/run_20260922_115243/runtime/output/data/cleaned_wide.csv',
          },
        },
      },
    };
    mockedArtifactsApi.getSessionDeliverableText.mockRejectedValue(
      new Error('Requested resource not found')
    );
    mockedArtifactsApi.getSessionArtifactText.mockResolvedValue({
      path: '_scratch/plan173_task7/run_20260922_115243/runtime/output/data/cleaned_wide.csv',
      content: 'gene,score\nSEC,0.98',
      truncated: false,
    });

    renderDrawer(csvResult);

    fireEvent.click(screen.getByText('cleaned_wide.csv'));

    const dialog = await screen.findByRole('dialog');
    // Primary deliverables request 404s, then the raw fallback takes over.
    await waitFor(() => {
      expect(mockedArtifactsApi.getSessionDeliverableText).toHaveBeenCalledWith(
        'sess-1',
        'cleaned_wide.csv',
        expect.objectContaining({ maxBytes: 200000 })
      );
    });
    await waitFor(() => {
      expect(mockedArtifactsApi.getSessionArtifactText).toHaveBeenCalledWith(
        'sess-1',
        '_scratch/plan173_task7/run_20260922_115243/runtime/output/data/cleaned_wide.csv',
        { maxBytes: 200000 }
      );
    });
    expect(await within(dialog).findByText('1 rows x 2 columns')).toBeInTheDocument();
    expect(within(dialog).getByText('SEC')).toBeInTheDocument();
    expect(within(dialog).queryByText(/Failed to load/)).not.toBeInTheDocument();
  });

  it('keeps the error state when no raw fallback path is available', async () => {
    const csvResult: PlanResultItem = {
      task_id: 7,
      status: 'completed',
      content: 'produced table.csv',
      metadata: {
        execution_status: 'completed',
        published_artifacts: {
          'data.table_csv': {
            alias: 'data.table_csv',
            path: '/tmp/run/table.csv',
            deliverable_path: 'docs/table.csv',
          },
        },
      },
    };
    mockedArtifactsApi.getSessionDeliverableText.mockRejectedValue(
      new Error('Requested resource not found')
    );

    renderDrawer(csvResult);

    fireEvent.click(screen.getByText('table.csv'));

    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(/Failed to load/)).toBeInTheDocument();
    expect(mockedArtifactsApi.getSessionArtifactText).not.toHaveBeenCalled();
  });

  it('moves technical details into a single collapsed panel', async () => {
    const { container } = renderDrawer();

    fireEvent.click(screen.getByText('Technical Details'));

    expect(await screen.findByText('Token Consumption')).toBeInTheDocument();
    expect(screen.getByText('Dependencies')).toBeInTheDocument();
    expect(screen.getByText('Task #3')).toBeInTheDocument();
    expect(screen.getByText('Context')).toBeInTheDocument();
    expect(screen.getByText('Metadata')).toBeInTheDocument();
    expect(screen.getByText('Verification raw data')).toBeInTheDocument();
    expect(screen.getByText('Result metadata')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Copy task JSON/ })).toBeInTheDocument();
    expect(container.textContent).toContain('"source_count": 4');
    expect(container.textContent).toContain('"priority": "high"');
    expect(container.textContent).toContain('"checks_total": 44');
  });
});
