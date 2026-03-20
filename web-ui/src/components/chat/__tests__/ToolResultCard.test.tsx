import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { vi } from 'vitest';
import ToolResultCard from '../ToolResultCard';
import { useChatStore } from '@store/chat';

describe('ToolResultCard', () => {
  beforeEach(() => {
    useChatStore.setState({
      sendMessage: vi.fn().mockResolvedValue(undefined),
    } as any);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders search results when successful', () => {
    render(
      <ToolResultCard
        defaultOpen
        payload={{
          name: 'web_search',
          summary: 'Web search completed',
          result: {
            query: 'AI research',
            success: true,
            response: 'AI is advancing rapidly.',
            results: [
              {
                title: 'AI Weekly',
                url: 'https://example.com',
                source: 'Example News',
                snippet: 'Summary snippet',
              },
            ],
          },
        }}
      />
    );

    expect(screen.getByText('Web search completed')).toBeInTheDocument();
    expect(screen.getByText('AI Weekly')).toBeInTheDocument();
  });

  it('shows warning when web search succeeded but no verifiable result URLs', () => {
    render(
      <ToolResultCard
        defaultOpen
        payload={{
          name: 'web_search',
          summary: 'Web search completed',
          result: {
            query: 'breaking news',
            success: true,
            response: 'Some summary without structured citations.',
            results: [],
          },
        }}
      />
    );

    expect(screen.getByText('No parseable source links returned')).toBeInTheDocument();
  });

  it('shows retry button when search fails', async () => {
    const sendMessage = useChatStore.getState().sendMessage as unknown as ReturnType<typeof vi.fn>;
    render(
      <ToolResultCard
        defaultOpen
        payload={{
          name: 'web_search',
          summary: 'Web search failed. Please try again later.',
          result: {
            query: 'AI research',
            success: false,
            error: 'Network timeout',
          },
        }}
      />
    );

    expect(screen.getByText('Web search failed. Please try again later.')).toBeInTheDocument();
    const retryButton = screen.getByRole('button', { name: /Retry search/i });
    fireEvent.click(retryButton);

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledTimes(1);
    });
  });
});
