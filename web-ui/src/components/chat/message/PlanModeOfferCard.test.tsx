import React from 'react';
import { App as AntdApp } from 'antd';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocked = vi.hoisted(() => ({
  setPlanMode: vi.fn(),
  dismissPlanModeOffer: vi.fn(),
  sendMessage: vi.fn(),
  state: {
    planMode: false,
    dismissed: false,
  },
}));

vi.mock('@store/chat', () => ({
  useChatStore: (selector: any) => selector({
    planMode: mocked.state.planMode,
    currentSession: { settings: { plan_offer_dismissed: mocked.state.dismissed } },
    setPlanMode: mocked.setPlanMode,
    dismissPlanModeOffer: mocked.dismissPlanModeOffer,
    sendMessage: mocked.sendMessage,
  }),
}));

import PlanModeOfferCard from './PlanModeOfferCard';

const offer = {
  offer_id: 'offer-ui',
  status: 'pending' as const,
  reason: 'This needs multi-stage research and execution.',
  query: '研究裂解酶并验证候选',
};

beforeEach(() => {
  vi.clearAllMocks();
  mocked.state.planMode = false;
  mocked.state.dismissed = false;
  mocked.setPlanMode.mockResolvedValue(undefined);
  mocked.dismissPlanModeOffer.mockResolvedValue(undefined);
  mocked.sendMessage.mockResolvedValue(undefined);
});
afterEach(cleanup);

function renderCard() {
  return render(<AntdApp><PlanModeOfferCard offer={offer} /></AntdApp>);
}

describe('PlanModeOfferCard', () => {
  it('accepts with the original request and explicit creation consent', async () => {
    renderCard();
    fireEvent.click(screen.getByRole('button', { name: '进入 Plan 模式并创建计划' }));

    await waitFor(() => expect(mocked.sendMessage).toHaveBeenCalledWith(offer.query, {
      plan_mode: true,
      plan_offer_accepted: true,
      plan_offer_id: 'offer-ui',
      plan_create_required: true,
    }));
    expect(mocked.setPlanMode).toHaveBeenCalledWith(true);
    expect(mocked.dismissPlanModeOffer).not.toHaveBeenCalled();
  });

  it('declines without enabling planning or starting another run', async () => {
    renderCard();
    fireEvent.click(screen.getByRole('button', { name: '直接继续' }));

    await waitFor(() => expect(mocked.dismissPlanModeOffer).toHaveBeenCalled());
    expect(mocked.setPlanMode).not.toHaveBeenCalled();
    expect(mocked.sendMessage).not.toHaveBeenCalled();
  });

  it('restores the previous mode if the accepted run fails', async () => {
    mocked.sendMessage.mockRejectedValueOnce(new Error('offline'));
    renderCard();
    fireEvent.click(screen.getByRole('button', { name: '进入 Plan 模式并创建计划' }));

    await waitFor(() => expect(mocked.setPlanMode).toHaveBeenLastCalledWith(false));
    expect(mocked.setPlanMode.mock.calls.map(([value]) => value)).toEqual([true, false]);
  });

  it('hides after dismissal or when Plan mode is already active', () => {
    mocked.state.dismissed = true;
    const { unmount } = renderCard();
    expect(screen.queryByRole('button', { name: '进入 Plan 模式并创建计划' })).toBeNull();
    unmount();

    mocked.state.dismissed = false;
    mocked.state.planMode = true;
    renderCard();
    expect(screen.queryByRole('button', { name: '进入 Plan 模式并创建计划' })).toBeNull();
  });
});
