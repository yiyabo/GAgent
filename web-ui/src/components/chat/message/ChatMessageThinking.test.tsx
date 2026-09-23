import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import ChatMessage from './index';

vi.mock('../MarkdownRenderer', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div>,
}));

vi.mock('./MessageActions', () => ({
  default: () => null,
}));

vi.mock('./ToolResultDrawer', () => ({
  default: () => null,
  ToolStatusBar: () => null,
}));

vi.mock('../ArtifactGallery', () => ({
  default: () => null,
}));

vi.mock('./ToolProgressCard', () => ({
  default: () => null,
  BackgroundDispatchCard: () => null,
}));

const baseMetadata = {
  status: 'completed',
  unified_stream: true,
  thinking_visibility: 'visible',
  thinking_display_mode: 'full_thinking',
  analysis_text: 'final answer',
};

function renderMessage(thinkingProcess: any, metadata: Record<string, any> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatMessage
        message={{
          id: 'assistant-1',
          type: 'assistant',
          content: 'final answer',
          timestamp: new Date('2026-04-09T00:00:00Z'),
          metadata: { ...baseMetadata, ...metadata },
          thinking_process: thinkingProcess,
        }}
        sessionId="session-1"
      />
    </QueryClientProvider>,
  );
}

describe('ChatMessage thinking rendering', () => {
  it('renders persisted thinking after history hydration instead of hiding it in final answer mode', () => {
    const { container } = renderMessage({
      status: 'completed',
      total_iterations: 1,
      steps: [
        {
          iteration: 1,
          thought: 'inspect files',
          display_text: 'Inspect files',
          status: 'completed',
        },
      ],
    });

    // Collapsed header
    expect(screen.getByText('Thought process')).toBeInTheDocument();
    expect(screen.getByText('final answer')).toBeInTheDocument();

    // Expand the outer block → activity stream appears
    fireEvent.click(screen.getByText('Thought process'));
    const itemRow = container.querySelector('.tp-item-row');
    expect(itemRow).not.toBeNull();

    // Reasoning rows carry a uniform label; the full thought stays folded
    // until the row itself is clicked.
    expect(screen.queryByText('inspect files')).not.toBeInTheDocument();
    fireEvent.click(itemRow!);
    expect(screen.getByText('inspect files')).toBeInTheDocument();
  });

  it('labels tool rows from the action JSON, ignoring generic backend display_text', () => {
    const { container } = renderMessage({
      status: 'completed',
      total_iterations: 1,
      steps: [
        {
          iteration: 1,
          thought: '',
          display_text: 'Processing the current step',
          action: JSON.stringify({ tool: 'web_search', params: { query: 'osteoporosis RCT' } }),
          action_result: 'found 5 papers',
          status: 'completed',
        },
      ],
    });

    fireEvent.click(screen.getByText('Thought process'));
    expect(screen.getByText('Searching for: osteoporosis RCT')).toBeInTheDocument();
    expect(screen.queryByText('Processing the current step')).not.toBeInTheDocument();

    // Tool result stays folded until the row is expanded.
    expect(screen.queryByText('found 5 papers')).not.toBeInTheDocument();
    const itemRow = container.querySelector('.tp-item-row');
    fireEvent.click(itemRow!);
    expect(screen.getByText('found 5 papers')).toBeInTheDocument();
  });

  it('shows step/tool-call stats in the collapsed header preview', () => {
    renderMessage({
      status: 'completed',
      total_iterations: 2,
      steps: [
        {
          iteration: 1,
          thought: 'plan the analysis',
          status: 'completed',
        },
        {
          iteration: 2,
          thought: '',
          action: JSON.stringify({ tool: 'file_operations', params: { operation: 'read', path: '/data/cohort.csv' } }),
          action_result: '120 rows',
          status: 'completed',
        },
      ],
    });

    expect(screen.getByText('2 steps · 1 tool call')).toBeInTheDocument();
  });
});
