import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
import { useLanguageStore } from '@/i18n/language';
import { App as AntdApp } from 'antd';

import QualityAnalytics from './QualityAnalytics';
import { qualityApi } from '@api/quality';

vi.mock('@api/quality', () => ({
  qualityApi: {
    getSummary: vi.fn(),
    getCases: vi.fn(),
  },
}));

const mockedQualityApi = vi.mocked(qualityApi);

describe('QualityAnalytics', () => {
  afterEach(() => useLanguageStore.setState({ language: 'zh-CN' }));

  beforeEach(() => {
    vi.clearAllMocks();
    useLanguageStore.setState({ language: 'en' });
    mockedQualityApi.getSummary.mockResolvedValue({
      total: 4,
      pending: 1,
      evaluated: 3,
      average_confidence: 0.72,
      by_satisfaction_level: [
        { name: 'satisfied', count: 1 },
        { name: 'acceptable', count: 1 },
        { name: 'negative', count: 1 },
      ],
      failure_modes: [{ name: 'tool_not_invoked', count: 1 }],
      responsible_stages: [{ name: 'tool_selection', count: 1 }],
      intents: [{ name: 'execute_task', count: 2 }],
      tools: [],
    });
    mockedQualityApi.getCases.mockResolvedValue([{
      id: 1,
      target_run_id: 'run-1',
      session_id: 'session-1',
      status: 'final',
      evaluation_basis: 'follow_up_message',
      satisfaction_level: 'negative',
      confidence: 0.9,
      failure_modes: ['tool_not_invoked'],
      responsible_stages: ['tool_selection'],
      evidence: [{ source: 'user_follow_up', quote: 'This did not run', explanation: 'Explicit correction' }],
      user_goal: 'Run a real analysis',
    }]);
  });

  it('renders low-risk summary and evidence-backed cases', async () => {
    render(<AntdApp><QualityAnalytics /></AntdApp>);

    expect(await screen.findByText('Conversation Quality')).toBeInTheDocument();
    await waitFor(() => expect(mockedQualityApi.getSummary).toHaveBeenCalledWith(168));
    expect(screen.getByText('Observation-only mode')).toBeInTheDocument();
    expect(screen.getByText('tool not invoked')).toBeInTheDocument();
    expect(screen.getByText('Intents')).toBeInTheDocument();
    expect(screen.getByText('execute task')).toBeInTheDocument();
    expect(screen.getByText('Run a real analysis')).toBeInTheDocument();
    expect(screen.getByText('90% confidence')).toBeInTheDocument();
  });

  it('renders the same page in Chinese, including the confidence sentence and the status words', async () => {
    useLanguageStore.setState({ language: 'zh-CN' });
    render(<AntdApp><QualityAnalytics /></AntdApp>);

    expect(await screen.findByText('对话质量')).toBeInTheDocument();
    expect(screen.getByText('仅观察模式')).toBeInTheDocument();
    expect(screen.getByText('用户目标')).toBeInTheDocument();
    expect(screen.getByText('置信度 90%')).toBeInTheDocument();
    // The raw data value is shown as data, untranslated.
    expect(screen.getByText('Run a real analysis')).toBeInTheDocument();
  });
});
