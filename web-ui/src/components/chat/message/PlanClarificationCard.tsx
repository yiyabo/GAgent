import React, { useMemo, useState } from 'react';
import { App, Button, Card, Checkbox, Input, Radio, Space, Tag, Typography } from 'antd';
import { BulbOutlined, CheckOutlined, QuestionCircleOutlined } from '@ant-design/icons';
import { useT } from '@/i18n/useT';
import { useChatStore } from '@store/chat';
import type { PlanClarificationQuestion, PlanClarificationRequest, PlanClarificationResponse } from '@/types';
import {
  OTHER_OPTION,
  buildClarificationReply,
  isQuestionAnswered,
  type ClarificationOtherText,
  type ClarificationSelections,
} from './planClarification';

interface PlanClarificationCardProps {
  request: PlanClarificationRequest;
}

const { Text } = Typography;

/** Non-blocking Plan-mode questions: answering sends the next chat message. */
const PlanClarificationCard: React.FC<PlanClarificationCardProps> = ({ request }) => {
  const t = useT();
  const { message } = App.useApp();
  const { pendingId, sessionId, sendMessage, setPendingPlanClarification } = useChatStore((state) => ({
    pendingId: state.currentSession?.settings?.pending_plan_clarification_id ?? null,
    sessionId: state.currentSession?.id ?? null,
    sendMessage: state.sendMessage,
    setPendingPlanClarification: state.setPendingPlanClarification,
  }));
  const [selections, setSelections] = useState<ClarificationSelections>({});
  const [otherText, setOtherText] = useState<ClarificationOtherText>({});
  const [busy, setBusy] = useState<PlanClarificationResponse['mode'] | null>(null);
  const active = pendingId === request.request_id;
  const allAnswered = useMemo(
    () => request.questions.every((question) => isQuestionAnswered(question, selections, otherText)),
    [request.questions, selections, otherText],
  );

  const submit = async (mode: PlanClarificationResponse['mode']) => {
    const { text, response } = buildClarificationReply(request, mode, selections, otherText, {
      answerIntro: t('planClarify.answerIntro'),
      defaultsIntro: t('planClarify.defaultsIntro'),
      moreContext: t('planClarify.moreContextMessage'),
      recommendedSuffix: t('planClarify.recommendedSuffix'),
      otherPrefix: t('planClarify.otherPrefix'),
    });
    setBusy(mode);
    if (sessionId) setPendingPlanClarification(sessionId, null);
    try {
      await sendMessage(text, { plan_clarification_response: response });
    } catch (error) {
      if (sessionId) setPendingPlanClarification(sessionId, request.request_id);
      const detail = error instanceof Error ? error.message : String(error);
      message.error(t('planClarify.sendFailed', { detail }));
    } finally {
      setBusy(null);
    }
  };

  const renderOptions = (question: PlanClarificationQuestion) => {
    const chosen = selections[question.id] ?? [];
    const items = [
      ...question.options.map((option) => ({
        value: option.label,
        label: (
          <span className="plan-clarify-option">
            <Text strong>{option.label}</Text>
            {option.recommended ? <Tag color="blue" style={{ marginInlineStart: 8 }}>{t('planClarify.recommended')}</Tag> : null}
            {option.description ? <Text type="secondary" className="plan-clarify-option-desc">{option.description}</Text> : null}
          </span>
        ),
      })),
      { value: OTHER_OPTION, label: <Text>{t('planClarify.other')}</Text> },
    ];
    const update = (values: string[]) => setSelections((prev) => ({ ...prev, [question.id]: values }));
    return (
      <Space direction="vertical" size={6} style={{ width: '100%' }}>
        {question.multi_select ? (
          <Checkbox.Group value={chosen} onChange={(values) => update(values.map(String))} disabled={!active || busy !== null}>
            <Space direction="vertical" size={6}>
              {items.map((item) => <Checkbox key={item.value} value={item.value}>{item.label}</Checkbox>)}
            </Space>
          </Checkbox.Group>
        ) : (
          <Radio.Group value={chosen[0]} onChange={(event) => update([String(event.target.value)])} disabled={!active || busy !== null}>
            <Space direction="vertical" size={6}>
              {items.map((item) => <Radio key={item.value} value={item.value}>{item.label}</Radio>)}
            </Space>
          </Radio.Group>
        )}
        {chosen.includes(OTHER_OPTION) ? (
          <Input.TextArea
            autoSize={{ minRows: 1, maxRows: 4 }}
            maxLength={500}
            value={otherText[question.id] ?? ''}
            placeholder={t('planClarify.otherPlaceholder')}
            onChange={(event) => setOtherText((prev) => ({ ...prev, [question.id]: event.target.value }))}
            disabled={!active || busy !== null}
            aria-label={`${question.header} ${t('planClarify.other')}`}
          />
        ) : null}
      </Space>
    );
  };

  return (
    <Card
      size="small"
      className="plan-clarify-card"
      style={{ marginTop: 12, marginBottom: 8 }}
      title={(
        <Space size={8} wrap>
          <QuestionCircleOutlined />
          <span>{t('planClarify.title')}</span>
          <Tag>{t('planClarify.round', { round: request.round, max: request.max_rounds })}</Tag>
          {!active ? <Tag color="default">{t('planClarify.closed')}</Tag> : null}
        </Space>
      )}
    >
      <Space direction="vertical" size={14} style={{ width: '100%' }}>
        {request.context ? <Text type="secondary">{request.context}</Text> : null}
        {request.questions.map((question, index) => (
          <div key={question.id} className="plan-clarify-question" data-question-id={question.id}>
            <Space size={8} align="start" style={{ marginBottom: 6 }}>
              <Tag color="geekblue">{question.header}</Tag>
              <Text strong>{`${index + 1}. ${question.question}`}</Text>
            </Space>
            {active ? renderOptions(question) : null}
          </div>
        ))}
        {active ? (
          <>
            <Space wrap>
              <Button
                type="primary"
                size="small"
                icon={<CheckOutlined />}
                disabled={!allAnswered || busy !== null}
                loading={busy === 'answered'}
                onClick={() => void submit('answered')}
              >
                {t('planClarify.submit')}
              </Button>
              <Button
                size="small"
                icon={<BulbOutlined />}
                disabled={busy !== null}
                loading={busy === 'recommended'}
                onClick={() => void submit('recommended')}
              >
                {t('planClarify.useRecommended')}
              </Button>
              <Button
                size="small"
                type="link"
                disabled={busy !== null}
                loading={busy === 'more_context'}
                onClick={() => void submit('more_context')}
              >
                {t('planClarify.moreContext')}
              </Button>
            </Space>
            <Text type="secondary" style={{ fontSize: 12 }}>{t('planClarify.hint')}</Text>
          </>
        ) : null}
      </Space>
    </Card>
  );
};

export default PlanClarificationCard;
