import React, { useEffect, useRef, useState } from 'react';
import { Button, Input, Popover, Space, Tag, Tooltip, message as antMessage } from 'antd';
import { DislikeFilled, DislikeOutlined, LikeFilled, LikeOutlined } from '@ant-design/icons';
import { skillLearningApi, type FeedbackRating, type FeedbackReason } from '@api/skillLearning';
import { useT } from '@/i18n/useT';
import type { TranslationKey } from '@/i18n/keys';

const TERMINAL = ['completed', 'failed', 'cancelled'];

const REASON_KEYS: Record<FeedbackReason, TranslationKey> = {
  wrong: 'skills.reasonWrong',
  incomplete: 'skills.reasonIncomplete',
  failed: 'skills.reasonFailed',
  slow: 'skills.reasonSlow',
  off_topic: 'skills.reasonOffTopic',
  other: 'skills.reasonOther',
};
const REASONS = Object.keys(REASON_KEYS) as FeedbackReason[];

interface MessageFeedbackProps {
  runId?: string | null;
  sessionId?: string | null;
  status?: string;
}

const MessageFeedback: React.FC<MessageFeedbackProps> = ({ runId, sessionId, status }) => {
  const t = useT();
  const [rating, setRating] = useState<FeedbackRating | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reasonOpen, setReasonOpen] = useState(false);
  const [reasons, setReasons] = useState<FeedbackReason[]>([]);
  const [comment, setComment] = useState('');
  const generation = useRef(0);
  const terminal = TERMINAL.includes(status ?? '');

  useEffect(() => {
    const seq = ++generation.current;
    setRating(null);
    setUnavailable(false);
    setReasonOpen(false);
    setReasons([]);
    setComment('');
    if (!runId || !sessionId || !terminal) return;
    skillLearningApi
      .getFeedback(runId, sessionId)
      .then((data) => {
        if (seq !== generation.current) return;
        const stored = data?.feedback;
        if (stored?.rating === 'useful' || stored?.rating === 'needs_work') setRating(stored.rating);
        if (stored?.rating === 'needs_work') {
          setReasons((stored.reasons ?? []).filter((item) => item in REASON_KEYS));
          setComment(stored.comment ?? '');
        }
      })
      .catch(() => {
        if (seq === generation.current) setUnavailable(true);
      });
  }, [runId, sessionId, terminal]);

  if (!runId || !sessionId || !terminal || unavailable) return null;

  const submit = async (next: FeedbackRating) => {
    if (next === 'needs_work' && rating === 'needs_work') {
      setReasonOpen(true);
      return;
    }
    if (saving || next === rating) return;
    const previous = rating;
    setRating(next);
    setSaving(true);
    try {
      await skillLearningApi.feedback(runId, sessionId, next);
      if (next === 'needs_work') {
        setReasons([]);
        setComment('');
        setReasonOpen(true);
      } else {
        setReasonOpen(false);
        antMessage.success(t('skills.feedbackSaved'));
      }
    } catch (error) {
      setRating(previous);
      antMessage.error(error instanceof Error && error.message ? error.message : t('skills.actionFailed'));
    } finally {
      setSaving(false);
    }
  };

  const submitReason = async () => {
    if (saving) return;
    setSaving(true);
    try {
      await skillLearningApi.feedback(runId, sessionId, 'needs_work', comment.trim(), reasons);
      setReasonOpen(false);
      antMessage.success(t('skills.reasonSaved'));
    } catch (error) {
      antMessage.error(error instanceof Error && error.message ? error.message : t('skills.actionFailed'));
    } finally {
      setSaving(false);
    }
  };

  const toggleReason = (reason: FeedbackReason, checked: boolean) =>
    setReasons((current) => (checked ? [...current, reason] : current.filter((item) => item !== reason)));

  const reasonForm = (
    <div style={{ width: 280 }}>
      <Space size={[4, 6]} wrap style={{ marginBottom: 8 }}>
        {REASONS.map((reason) => (
          <Tag.CheckableTag
            key={reason}
            checked={reasons.includes(reason)}
            onChange={(checked) => toggleReason(reason, checked)}
          >
            {t(REASON_KEYS[reason])}
          </Tag.CheckableTag>
        ))}
      </Space>
      <Input.TextArea
        aria-label={t('skills.reasonPlaceholder')}
        placeholder={t('skills.reasonPlaceholder')}
        value={comment}
        maxLength={1000}
        autoSize={{ minRows: 2, maxRows: 5 }}
        onChange={(event) => setComment(event.target.value)}
      />
      <div style={{ marginTop: 8, textAlign: 'right' }}>
        <Button
          type="primary"
          size="small"
          loading={saving}
          disabled={!reasons.length && !comment.trim()}
          onClick={() => void submitReason()}
        >
          {t('skills.reasonSubmit')}
        </Button>
      </div>
    </div>
  );

  const style = { fontSize: 12, padding: '0 4px' };
  return (
    <>
      <Tooltip title={t('skills.useful')}>
        <Button
          type="text"
          size="small"
          aria-label={t('skills.useful')}
          aria-pressed={rating === 'useful'}
          icon={rating === 'useful' ? <LikeFilled /> : <LikeOutlined />}
          disabled={saving}
          onClick={() => void submit('useful')}
          style={style}
        />
      </Tooltip>
      <Popover
        open={reasonOpen}
        onOpenChange={(open) => {
          if (!open) setReasonOpen(false);
        }}
        trigger="click"
        title={t('skills.reasonTitle')}
        content={reasonForm}
        placement="top"
      >
        <Tooltip title={reasonOpen ? undefined : t('skills.needsWork')}>
          <Button
            type="text"
            size="small"
            aria-label={t('skills.needsWork')}
            aria-pressed={rating === 'needs_work'}
            icon={rating === 'needs_work' ? <DislikeFilled /> : <DislikeOutlined />}
            disabled={saving}
            onClick={() => void submit('needs_work')}
            style={style}
          />
        </Tooltip>
      </Popover>
    </>
  );
};

export default MessageFeedback;
