import React, { useState } from 'react';
import { App, Alert, Button, Space } from 'antd';
import { CheckOutlined, CloseOutlined } from '@ant-design/icons';
import { useT } from '@/i18n/useT';
import { useChatStore } from '@store/chat';
import type { PlanModeOffer } from '@/types';

interface PlanModeOfferCardProps {
  offer: PlanModeOffer;
}

/** User-consent boundary for an agent-suggested Plan-mode transition. */
const PlanModeOfferCard: React.FC<PlanModeOfferCardProps> = ({ offer }) => {
  const t = useT();
  const { message } = App.useApp();
  const { planMode, dismissed, setPlanMode, dismissPlanModeOffer, sendMessage } = useChatStore((state) => ({
    planMode: state.planMode,
    dismissed: state.currentSession?.settings?.plan_offer_dismissed === true,
    setPlanMode: state.setPlanMode,
    dismissPlanModeOffer: state.dismissPlanModeOffer,
    sendMessage: state.sendMessage,
  }));
  const [busy, setBusy] = useState<'accept' | 'decline' | null>(null);

  if (planMode || dismissed || offer.status !== 'pending') return null;

  const accept = async () => {
    setBusy('accept');
    try {
      await setPlanMode(true);
      await sendMessage(offer.query, {
        plan_mode: true,
        plan_offer_accepted: true,
        plan_offer_id: offer.offer_id,
        plan_create_required: true,
      });
    } catch (error) {
      await setPlanMode(false).catch(() => undefined);
      const detail = error instanceof Error ? error.message : String(error);
      message.error(t('chatMessage.planOfferAcceptFailed', { detail }));
    } finally {
      setBusy(null);
    }
  };

  const decline = async () => {
    setBusy('decline');
    try {
      await dismissPlanModeOffer();
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      message.error(t('chatMessage.planOfferDeclineFailed', { detail }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Alert
      type="info"
      showIcon
      style={{ marginTop: 12, marginBottom: 8 }}
      message={t('chatMessage.planOfferTitle')}
      description={
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <span>{offer.reason}</span>
          <Space wrap>
            <Button
              type="primary"
              size="small"
              icon={<CheckOutlined />}
              loading={busy === 'accept'}
              disabled={busy !== null}
              onClick={accept}
              aria-label={t('chatMessage.planOfferAccept')}
            >
              {t('chatMessage.planOfferAccept')}
            </Button>
            <Button
              size="small"
              icon={<CloseOutlined />}
              loading={busy === 'decline'}
              disabled={busy !== null}
              onClick={decline}
              aria-label={t('chatMessage.planOfferDecline')}
            >
              {t('chatMessage.planOfferDecline')}
            </Button>
          </Space>
        </Space>
      }
    />
  );
};

export default PlanModeOfferCard;
