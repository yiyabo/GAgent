import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const mocked = vi.hoisted(() => ({ getRunInspection: vi.fn(), getPlanTaskRunInspection: vi.fn() }));

vi.mock('@api/chat', () => ({
  chatApi: { getRunInspection: mocked.getRunInspection, getPlanTaskRunInspection: mocked.getPlanTaskRunInspection },
}));

import { useLanguageStore } from '@/i18n/language';
import type { RunInspectionTarget } from '@/types/runInspection';
import InspectTaskRunButton from './InspectTaskRunButton';
import RunInspectorDrawer from './RunInspectorDrawer';

const inspection = {
  version: 1,
  run: {
    run_id: 'dt_abc',
    session_id: 'sess_1',
    status: 'succeeded',
    duration_ms: 12000,
    message: '调研裂解酶',
    context_flags: {},
  },
  summary: {
    steps: 3,
    tool_steps: 2,
    tool_failures: 1,
    tools: ['execute_code', 'web_search'],
    artifacts: 1,
    errors: 0,
    event_counts: {},
    streaming_events: { thinking_delta: 10, reasoning_delta: 5, delta: 2 },
  },
  steps: [
    { first_seq: 1, last_seq: 1, iteration: 1, kind: 'reasoning', status: 'done', label: 'think', tools: [] },
    {
      first_seq: 2, last_seq: 3, iteration: 1, kind: 'tool', status: 'analyzing', tools: [{ tool: 'web_search', params: '{"query":"x"}' }],
      success: true, duration_ms: 4000, result: '[web_search] {"success": true}',
    },
    {
      first_seq: 4, last_seq: 5, iteration: 2, kind: 'tool', status: 'error', tools: [{ tool: 'execute_code' }],
      success: false, duration_ms: 1000, result: 'Traceback boom',
    },
  ],
  events: [{ seq: 6, at: '', type: 'artifact', display_name: 'a.png', source_tool: 'execute_code' }],
  final: { status: 'completed', response_preview: 'done' },
  usage: {
    calls: 2, failed_calls: 0, prompt_tokens: 100, completion_tokens: 20, total_tokens: 120,
    cache_read_tokens: 50, duration_ms: 900, estimated_cost: 0, cost_currency: 'CNY', models: ['m1'],
    by_purpose: [{ phase: 'deep_think', purpose: 'deep_think_iteration', calls: 2, total_tokens: 120, duration_ms: 900, failed: 0 }],
  },
  truncated: { steps: false, artifacts: false },
};

function withClient(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function renderDrawer(target: RunInspectionTarget = { run_id: 'dt_abc' }, sessionId: string | null = 'sess_1') {
  return withClient(<RunInspectorDrawer open sessionId={sessionId} target={target} onClose={() => undefined} />);
}

const planTaskInspection = {
  ...inspection,
  run: { ...inspection.run, run_id: 'dt_task', plan_id: 3, task_id: 7, task_name: 'Assemble genomes' },
  summary: { ...inspection.summary, steps: 1, tool_steps: 1 },
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  act(() => useLanguageStore.getState().setLanguage('zh-CN'));
});

describe('RunInspectorDrawer', () => {
  it('loads the inspection for the target and shows the summary', async () => {
    mocked.getRunInspection.mockResolvedValue(inspection);
    renderDrawer();

    await screen.findByText('dt_abc');
    expect(mocked.getRunInspection).toHaveBeenCalledWith('sess_1', { run_id: 'dt_abc' });
    expect(screen.getByText('succeeded')).toBeTruthy();
    expect(screen.getByText('12 s')).toBeTruthy();
    expect(screen.getByText('a.png')).toBeTruthy();
  });

  it('narrows the step list to failures', async () => {
    mocked.getRunInspection.mockResolvedValue(inspection);
    renderDrawer();
    await screen.findByText('dt_abc');

    const segmented = document.querySelector('.ant-segmented');
    expect(segmented).not.toBeNull();
    fireEvent.click(within(segmented as HTMLElement).getByText(/^(Failures|失败)$/));
    await waitFor(() => expect(screen.queryByText('web_search')).toBeNull());
    expect(screen.getAllByText('execute_code').length).toBeGreaterThan(0);
  });

  it('loads a plan task run without a chat session and names the task in the header', async () => {
    mocked.getPlanTaskRunInspection.mockResolvedValue(planTaskInspection);
    renderDrawer({ planId: 3, taskId: 7 }, null);

    await screen.findByText('dt_task');
    expect(mocked.getPlanTaskRunInspection).toHaveBeenCalledWith(3, 7, undefined);
    expect(mocked.getRunInspection).not.toHaveBeenCalled();
    expect(screen.getByText('任务 #7 · Assemble genomes')).toBeTruthy();
  });

  it('passes a pinned job id and falls back to the target task name and id', async () => {
    mocked.getPlanTaskRunInspection.mockResolvedValue({ ...inspection, run: { ...inspection.run, task_id: null, task_name: null } });
    renderDrawer({ planId: 3, taskId: 9, jobId: 'job_1', taskName: 'Annotate' }, '');
    await screen.findByText('dt_abc');
    expect(mocked.getPlanTaskRunInspection).toHaveBeenCalledWith(3, 9, 'job_1');
    expect(screen.getByText('任务 #9 · Annotate')).toBeTruthy();

    cleanup();
    mocked.getPlanTaskRunInspection.mockResolvedValue(inspection);
    renderDrawer({ planId: 3, taskId: 11 }, null);
    await screen.findByText('dt_abc');
    expect(screen.getByText('任务 #11')).toBeTruthy();
  });

  it('does not fetch a chat run without a session', () => {
    renderDrawer({ run_id: 'dt_abc' }, null);
    expect(mocked.getRunInspection).not.toHaveBeenCalled();
    expect(mocked.getPlanTaskRunInspection).not.toHaveBeenCalled();
  });

  it('pluralises the English step counts', async () => {
    act(() => useLanguageStore.getState().setLanguage('en'));
    mocked.getPlanTaskRunInspection.mockResolvedValue(planTaskInspection);
    renderDrawer({ planId: 3, taskId: 7 }, null);
    await screen.findByText('1 step · 1 tool step');
    expect(screen.getByText('Task #7 · Assemble genomes')).toBeTruthy();

    cleanup();
    mocked.getRunInspection.mockResolvedValue(inspection);
    renderDrawer();
    await screen.findByText('3 steps · 2 tool steps');
  });

  it('offers the task inspect button only once the task has run, and opens the drawer', async () => {
    mocked.getPlanTaskRunInspection.mockResolvedValue(planTaskInspection);
    withClient(<InspectTaskRunButton planId={3} taskId={7} taskName="Assemble genomes" status="pending" />);
    expect(screen.queryByRole('button', { name: /查看运行详情/ })).toBeNull();
    cleanup();

    withClient(<InspectTaskRunButton planId={null} taskId={7} status="completed" />);
    expect(screen.queryByRole('button', { name: /查看运行详情/ })).toBeNull();
    cleanup();

    withClient(<InspectTaskRunButton planId={3} taskId={7} taskName="Assemble genomes" status="completed" />);
    fireEvent.click(screen.getByRole('button', { name: /查看运行详情/ }));
    await screen.findByText('dt_task');
    expect(mocked.getPlanTaskRunInspection).toHaveBeenCalledWith(3, 7, undefined);
  });

  it('shows a load error without crashing', async () => {
    mocked.getRunInspection.mockRejectedValue(new Error('run not found'));
    renderDrawer({ run_id: 'dt_missing' });
    await screen.findByText('run not found');
  });
});
