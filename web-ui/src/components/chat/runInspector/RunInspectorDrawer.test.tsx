import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const mocked = vi.hoisted(() => ({ getRunInspection: vi.fn() }));

vi.mock('@api/chat', () => ({ chatApi: { getRunInspection: mocked.getRunInspection } }));

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

function renderDrawer(target = { run_id: 'dt_abc' }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RunInspectorDrawer open sessionId="sess_1" target={target} onClose={() => undefined} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
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

  it('shows a load error without crashing', async () => {
    mocked.getRunInspection.mockRejectedValue(new Error('run not found'));
    renderDrawer({ run_id: 'dt_missing' });
    await screen.findByText('run not found');
  });
});
