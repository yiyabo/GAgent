import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
vi.mock('@api/skillLearning', () => ({ skillLearningApi: { getFeedback: vi.fn(), feedback: vi.fn() } }));
import { skillLearningApi } from '@api/skillLearning';
import MessageFeedback from './MessageFeedback';

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(skillLearningApi.getFeedback).mockResolvedValue({ run_id: 'r', feedback: null });
  vi.mocked(skillLearningApi.feedback).mockResolvedValue({} as any);
});
afterEach(cleanup);

describe('reply thumbs feedback', () => {
  it('is hidden until the run has finished', () => {
    render(<MessageFeedback runId="r" sessionId="s" status="running" />);
    expect(screen.queryByLabelText('这次结果有用')).toBeNull();
    expect(skillLearningApi.getFeedback).not.toHaveBeenCalled();
  });

  it('is hidden for replies without a chat run', () => {
    render(<MessageFeedback runId={null} sessionId="s" status="completed" />);
    expect(screen.queryByLabelText('这次结果有用')).toBeNull();
  });

  it('records thumbs up and down against the originating run', async () => {
    render(<MessageFeedback runId="r" sessionId="s" status="completed" />);
    await waitFor(() => expect(skillLearningApi.getFeedback).toHaveBeenCalledWith('r', 's'));
    fireEvent.click(screen.getByLabelText('这次结果有用'));
    await waitFor(() => expect(skillLearningApi.feedback).toHaveBeenCalledWith('r', 's', 'useful'));
    await waitFor(() => expect(screen.getByLabelText('这次结果有用').getAttribute('aria-pressed')).toBe('true'));
    fireEvent.click(screen.getByLabelText('这次需要改进'));
    await waitFor(() => expect(skillLearningApi.feedback).toHaveBeenCalledWith('r', 's', 'needs_work'));
    await waitFor(() => expect(screen.getByLabelText('这次需要改进').getAttribute('aria-pressed')).toBe('true'));
    expect(screen.getByLabelText('这次结果有用').getAttribute('aria-pressed')).toBe('false');
  });

  it('shows the stored rating and does not resend it', async () => {
    vi.mocked(skillLearningApi.getFeedback).mockResolvedValue({ run_id: 'r', feedback: { rating: 'needs_work', comment: '' } });
    render(<MessageFeedback runId="r" sessionId="s" status="completed" />);
    await waitFor(() => expect(screen.getByLabelText('这次需要改进').getAttribute('aria-pressed')).toBe('true'));
    fireEvent.click(screen.getByLabelText('这次需要改进'));
    expect(skillLearningApi.feedback).not.toHaveBeenCalled();
  });

  it('reverts the selection when saving fails', async () => {
    vi.mocked(skillLearningApi.feedback).mockRejectedValue(new Error('task has not finished'));
    render(<MessageFeedback runId="r" sessionId="s" status="completed" />);
    await waitFor(() => expect(skillLearningApi.getFeedback).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText('这次结果有用'));
    await waitFor(() => expect(skillLearningApi.feedback).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByLabelText('这次结果有用').getAttribute('aria-pressed')).toBe('false'));
  });

  it('hides itself when the run cannot be read', async () => {
    vi.mocked(skillLearningApi.getFeedback).mockRejectedValue(new Error('run not found'));
    render(<MessageFeedback runId="r" sessionId="s" status="completed" />);
    await waitFor(() => expect(screen.queryByLabelText('这次结果有用')).toBeNull());
  });
});
