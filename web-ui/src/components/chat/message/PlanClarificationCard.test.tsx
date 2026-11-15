import { App as AntdApp } from 'antd';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocked = vi.hoisted(() => ({
  sendMessage: vi.fn(),
  setPendingPlanClarification: vi.fn(),
  state: { pendingId: 'clarify_ui' as string | null },
}));

vi.mock('@store/chat', () => ({
  useChatStore: (selector: any) => selector({
    currentSession: { id: 'local-A', settings: { pending_plan_clarification_id: mocked.state.pendingId } },
    sendMessage: mocked.sendMessage,
    setPendingPlanClarification: mocked.setPendingPlanClarification,
  }),
}));

import PlanClarificationCard from './PlanClarificationCard';

const request = {
  request_id: 'clarify_ui',
  status: 'pending' as const,
  round: 1,
  max_rounds: 2,
  context: '检索到多类宿主。',
  questions: [
    {
      id: 'host_range',
      header: '宿主范围',
      question: '计划聚焦哪类宿主菌？',
      options: [
        { label: '仅金黄色葡萄球菌', description: '最快给出候选', recommended: true },
        { label: '葡萄球菌属', description: '覆盖更多菌株' },
      ],
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  mocked.state.pendingId = 'clarify_ui';
  mocked.sendMessage.mockResolvedValue(undefined);
});
afterEach(cleanup);

const renderCard = () => render(<AntdApp><PlanClarificationCard request={request} /></AntdApp>);

describe('PlanClarificationCard', () => {
  it('submits the selected option as the next message', async () => {
    renderCard();
    const submit = screen.getByRole('button', { name: /提交回答/ });
    expect(submit).toBeDisabled();
    fireEvent.click(screen.getByRole('radio', { name: /葡萄球菌属/ }));
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);

    await waitFor(() => expect(mocked.sendMessage).toHaveBeenCalledTimes(1));
    const [text, context] = mocked.sendMessage.mock.calls[0];
    expect(text).toBe('我对规划问题的回答：\n- 宿主范围：葡萄球菌属');
    expect(context).toEqual({
      plan_clarification_response: {
        request_id: 'clarify_ui',
        mode: 'answered',
        answers: [{
          id: 'host_range', header: '宿主范围', question: '计划聚焦哪类宿主菌？',
          selected: ['葡萄球菌属'], other: null, recommended_used: false,
        }],
      },
    });
    expect(mocked.setPendingPlanClarification).toHaveBeenCalledWith('local-A', null);
  });

  it('continues with recommendations and flags them as defaults', async () => {
    renderCard();
    fireEvent.click(screen.getByRole('button', { name: /按推荐继续/ }));
    await waitFor(() => expect(mocked.sendMessage).toHaveBeenCalledTimes(1));
    const [text, context] = mocked.sendMessage.mock.calls[0];
    expect(text).toContain('仅金黄色葡萄球菌（采用推荐）');
    expect(context.plan_clarification_response.mode).toBe('recommended');
    expect(context.plan_clarification_response.answers[0].recommended_used).toBe(true);
  });

  it('requires text for Other and restores the pending state if sending fails', async () => {
    mocked.sendMessage.mockRejectedValueOnce(new Error('offline'));
    renderCard();
    fireEvent.click(screen.getByRole('radio', { name: /其他/ }));
    const submit = screen.getByRole('button', { name: /提交回答/ });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText('宿主范围 其他（自行填写）'), { target: { value: '大肠杆菌' } });
    fireEvent.click(submit);
    await waitFor(() => expect(mocked.setPendingPlanClarification).toHaveBeenLastCalledWith('local-A', 'clarify_ui'));
    expect(mocked.sendMessage.mock.calls[0][0]).toContain('其他：大肠杆菌');
  });

  it('asks for more context', async () => {
    renderCard();
    fireEvent.click(screen.getByRole('button', { name: /需要更多背景/ }));
    await waitFor(() => expect(mocked.sendMessage).toHaveBeenCalledTimes(1));
    expect(mocked.sendMessage.mock.calls[0][1].plan_clarification_response.mode).toBe('more_context');
  });

  it('is read-only once answered or superseded', () => {
    mocked.state.pendingId = null;
    renderCard();
    expect(screen.getByText('已结束')).toBeInTheDocument();
    expect(screen.getByText(/计划聚焦哪类宿主菌/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /提交回答/ })).toBeNull();
    expect(screen.queryByRole('radio')).toBeNull();
  });
});
