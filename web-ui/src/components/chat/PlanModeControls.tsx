import React, { useState } from 'react';
import { App, Button, Space, Switch, Tooltip } from 'antd';
import { PlayCircleOutlined } from '@ant-design/icons';
import { planTreeApi } from '@api/planTree';
import { useT } from '@/i18n/useT';
import { useChatStore } from '@store/chat';

interface PlanModeControlsProps {
  showApprove?: boolean;
}

/** Explicit read-only planning mode and the user-approved execution transition. */
const PlanModeControls: React.FC<PlanModeControlsProps> = ({ showApprove = false }) => {
  const t = useT();
  const { message } = App.useApp();
  const { planMode, currentPlanId, currentSession, setPlanMode } = useChatStore((state) => ({
    planMode: state.planMode,
    currentPlanId: state.currentPlanId,
    currentSession: state.currentSession,
    setPlanMode: state.setPlanMode,
  }));
  const [executing, setExecuting] = useState(false);

  const changeMode = async (enabled: boolean) => {
    try {
      await setPlanMode(enabled);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      message.error(t('chatMain.planModeSaveFailed', { detail }));
    }
  };

  const approveAndExecute = async () => {
    if (!currentPlanId || executing) return;
    setExecuting(true);
    try {
      await setPlanMode(false);
      const result = await planTreeApi.executeFullPlan(currentPlanId, {
        async_mode: true,
        session_id: currentSession?.session_id ?? currentSession?.id ?? undefined,
        skip_completed: true,
        stop_on_failure: false,
      });
      if (!result.success) {
        throw new Error(String(result.message || result.error || 'execute-full rejected'));
      }
      message.success(t('chatMain.planExecutionStarted'));
    } catch (error) {
      // A rejected execution must not leave the UI in an unplanned half-transition.
      await setPlanMode(true).catch(() => undefined);
      const detail = error instanceof Error ? error.message : String(error);
      message.error(t('chatMain.planExecutionFailed', { detail }));
    } finally {
      setExecuting(false);
    }
  };

  return (
    <Space size={8}>
      <Tooltip title={t(planMode ? 'chatMain.planModeOn' : 'chatMain.planModeOff')}>
        <Space size={4}>
          <Switch
            aria-label={t('chatMain.planModeLabel')}
            checked={planMode}
            onChange={(checked) => void changeMode(checked)}
            size="small"
            disabled={executing}
            style={{ background: planMode ? 'var(--primary-color)' : undefined }}
          />
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t('chatMain.planModeShort')}</span>
        </Space>
      </Tooltip>
      {showApprove && planMode && currentPlanId ? (
        <Button
          type="primary"
          size="small"
          icon={<PlayCircleOutlined />}
          loading={executing}
          aria-label={t('chatMain.planApprove')}
          onClick={() => void approveAndExecute()}
        >
          {t('chatMain.planApprove')}
        </Button>
      ) : null}
    </Space>
  );
};

export default PlanModeControls;
