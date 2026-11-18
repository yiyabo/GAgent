import { App as AntdApp } from 'antd';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocked = vi.hoisted(() => ({
  setPlanMode: vi.fn(),
  executeFullPlan: vi.fn(),
  state: {
    planMode: true,
    currentPlanId: 34,
    currentSession: { id: 'local-A', session_id: 'server-A' },
  },
}));

vi.mock('@store/chat', () => ({
  useChatStore: (selector: any) => selector({ ...mocked.state, setPlanMode: mocked.setPlanMode }),
}));
vi.mock('@api/planTree', () => ({ planTreeApi: { executeFullPlan: mocked.executeFullPlan } }));

import PlanModeControls from './PlanModeControls';

beforeEach(() => {
  vi.clearAllMocks();
  mocked.state.planMode = true;
  mocked.state.currentPlanId = 34;
  mocked.setPlanMode.mockResolvedValue(undefined);
  mocked.executeFullPlan.mockResolvedValue({ success: true, result: { job_id: 'job-1' } });
});
afterEach(cleanup);

function renderControls() {
  return render(<AntdApp><PlanModeControls showApprove /></AntdApp>);
}

describe('PlanModeControls', () => {
  it('persists the explicit mode toggle', async () => {
    renderControls();
    fireEvent.click(screen.getByRole('switch', { name: '启用 Plan 模式' }));
    await waitFor(() => expect(mocked.setPlanMode).toHaveBeenCalledWith(false));
    expect(mocked.executeFullPlan).not.toHaveBeenCalled();
  });

  it('leaves Plan mode and submits the approved Plan through the existing background API', async () => {
    renderControls();
    fireEvent.click(screen.getByRole('button', { name: '批准并执行' }));

    await waitFor(() => expect(mocked.executeFullPlan).toHaveBeenCalledWith(34, {
      async_mode: true,
      session_id: 'server-A',
      skip_completed: true,
      stop_on_failure: false,
    }));
    expect(mocked.setPlanMode).toHaveBeenCalledWith(false);
    expect(mocked.setPlanMode).not.toHaveBeenCalledWith(true);
  });

  it('restores Plan mode when execution submission fails', async () => {
    mocked.executeFullPlan.mockRejectedValueOnce(new Error('offline'));
    renderControls();
    fireEvent.click(screen.getByRole('button', { name: '批准并执行' }));

    await waitFor(() => expect(mocked.setPlanMode).toHaveBeenCalledWith(true));
    expect(mocked.setPlanMode.mock.calls.map(([value]) => value)).toEqual([false, true]);
  });
});
