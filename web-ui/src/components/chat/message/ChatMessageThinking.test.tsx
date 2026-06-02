import React from 'react';
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

describe('ChatMessage thinking rendering', () => {
  it('renders persisted thinking after history hydration instead of hiding it in final answer mode', () => {
    render(
      <ChatMessage
        message={{
          id: 'assistant-1',
          type: 'assistant',
          content: 'final answer',
          timestamp: new Date('2026-04-09T00:00:00Z'),
          metadata: {
            status: 'completed',
            unified_stream: true,
            thinking_visibility: 'visible',
            thinking_display_mode: 'full_thinking',
            analysis_text: 'final answer',
          },
          thinking_process: {
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
          },
        }}
        sessionId="session-1"
      />,
    );

    expect(screen.getByText('Thought process')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Thought process'));
    expect(screen.getByText('Inspect files')).toBeInTheDocument();
    expect(screen.getByText('final answer')).toBeInTheDocument();
  });
});
