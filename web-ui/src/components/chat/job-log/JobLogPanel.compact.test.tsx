import React from 'react';
import { App as AntdApp } from 'antd';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import JobLogPanel from './index';

vi.mock('./useJobLogStream', () => ({
  useJobLogStream: () => ({
    logs: [{ level: 'info', message: 'DeepThink tool start: execute_code' }],
    actionLogs: [{ sequence: 1, message: 'Action execution started.' }],
    status: 'running', stats: { steps: 1 }, jobParams: {}, result: null, error: null,
    expanded: true, setExpanded: vi.fn(), isStreaming: true,
    lastUpdatedAt: '2026-10-13T20:47:03+08:00Z', missingJob: false,
    jobType: 'plan_execute', jobMetadata: {}, resolvedPlanId: 185,
    cliLogVisible: false, setCliLogVisible: vi.fn(), cliLogLines: [],
    cliLogLoading: false, cliLogError: null, cliLogTruncated: false,
    cliLogPath: null, fetchCliLog: vi.fn(),
    thinkingProcess: { steps: [{ iteration: 1 }] }, streamPaused: false,
    lastRuntimeControlAction: null, lastRuntimeControlAt: null,
    runtimeControlBusy: false, runtimeControlBusyAction: null,
    pauseExecution: vi.fn(), resumeExecution: vi.fn(), skipCurrentStep: vi.fn(),
  }),
}));
vi.mock('./LogSection', () => ({
  ActionLogs: () => <div>ACTION_DIAGNOSTICS</div>,
  LogList: () => <div>RAW_EVENT_DIAGNOSTICS</div>,
}));
vi.mock('./ResultSection', () => ({
  ProgressBar: () => <div>执行进度</div>,
  ResultSummary: () => null,
}));
vi.mock('@components/chat/ThinkingProcess', () => ({
  ThinkingProcess: () => <div>思考过程</div>,
}));
vi.mock('./RepairProgressBadge', () => ({ RepairProgressBadge: () => null }));

const mount = (compact: boolean) => render(
  <AntdApp><JobLogPanel jobId="job-185" compact={compact} /></AntdApp>
);

describe('plan job log presentation', () => {
  it('keeps thinking and raw-log access while hiding technical events in compact mode', () => {
    mount(true);
    expect(screen.getByText('思考过程')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /原始日志/ })).toBeInTheDocument();
    expect(screen.queryByText('RAW_EVENT_DIAGNOSTICS')).not.toBeInTheDocument();
    expect(screen.queryByText('ACTION_DIAGNOSTICS')).not.toBeInTheDocument();
  });
  it('retains diagnostic events in the full panel', () => {
    mount(false);
    expect(screen.getByText('RAW_EVENT_DIAGNOSTICS')).toBeInTheDocument();
    expect(screen.getByText('ACTION_DIAGNOSTICS')).toBeInTheDocument();
  });
});
