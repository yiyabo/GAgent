import React, { useEffect, useRef, useState } from 'react';
import { Button, Tooltip, message as antMessage } from 'antd';
import { DislikeFilled, DislikeOutlined, LikeFilled, LikeOutlined } from '@ant-design/icons';
import { skillLearningApi, type FeedbackRating } from '@api/skillLearning';
import { useT } from '@/i18n/useT';

const TERMINAL = ['completed', 'failed', 'cancelled'];

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
  const generation = useRef(0);
  const terminal = TERMINAL.includes(status ?? '');

  useEffect(() => {
    const seq = ++generation.current;
    setRating(null);
    setUnavailable(false);
    if (!runId || !sessionId || !terminal) return;
    skillLearningApi
      .getFeedback(runId, sessionId)
      .then((data) => {
        if (seq !== generation.current) return;
        const stored = data?.feedback?.rating;
        if (stored === 'useful' || stored === 'needs_work') setRating(stored);
      })
      .catch(() => {
        if (seq === generation.current) setUnavailable(true);
      });
  }, [runId, sessionId, terminal]);

  if (!runId || !sessionId || !terminal || unavailable) return null;

  const submit = async (next: FeedbackRating) => {
    if (saving || next === rating) return;
    const previous = rating;
    setRating(next);
    setSaving(true);
    try {
      await skillLearningApi.feedback(runId, sessionId, next);
      antMessage.success(t('skills.feedbackSaved'));
    } catch (error) {
      setRating(previous);
      antMessage.error(error instanceof Error && error.message ? error.message : t('skills.actionFailed'));
    } finally {
      setSaving(false);
    }
  };

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
      <Tooltip title={t('skills.needsWork')}>
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
    </>
  );
};

export default MessageFeedback;
