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
          summary: '搜索完成',
          result: {
            query: 'AI 最新动态',
            success: true,
            response: '为你整理了今日 AI 行业动态。',
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

    expect(screen.getByText('搜索完成')).toBeInTheDocument();
    expect(screen.getByText('查询语句：')).toBeInTheDocument();
    expect(screen.getByText('AI Weekly')).toBeInTheDocument();
  });

  it('shows retry button when search fails', async () => {
    const sendMessage = useChatStore.getState().sendMessage as unknown as ReturnType<typeof vi.fn>;
    render(
      <ToolResultCard
        defaultOpen
        payload={{
          name: 'web_search',
          summary: '搜索失败',
          result: {
            query: 'AI 最新动态',
            success: false,
            error: '请求超时',
          },
        }}
      />
    );

    expect(screen.getByText('请求超时')).toBeInTheDocument();
    const retryButton = screen.getByRole('button', { name: '重试搜索' });
    fireEvent.click(retryButton);

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledTimes(1);
    });
  });
});
