import React, { useState } from 'react';
import {
  Typography,
  Space,
  Button,
  Tooltip,
  message as antMessage,
} from 'antd';
import {
  CopyOutlined,
  DatabaseOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import type { ChatMessage as ChatMessageType } from '@/types';
import { useChatStore } from '@store/chat';
import { useT } from '@/i18n/useT';
import { resolveChatSessionProcessingKey } from '@/utils/chatSessionKeys';
import { formatTime, fallbackCopyToClipboard, resolveMessageCopyText } from './utils';

const { Text } = Typography;

interface MessageActionsProps {
  message: ChatMessageType;
}

const MessageActions: React.FC<MessageActionsProps> = ({ message }) => {
  const t = useT();
  const { type, content, timestamp, metadata } = message;
  const saveMessageAsMemory = useChatStore((s) => s.saveMessageAsMemory);
  const retryActionRun = useChatStore((s) => s.retryActionRun);
  const retryLastMessage = useChatStore((s) => s.retryLastMessage);
  const isProcessing = useChatStore((state) =>
    state.processingSessionIds.has(resolveChatSessionProcessingKey(state.currentSession))
  );
  const [isSaving, setIsSaving] = useState(false);
  const copyText = resolveMessageCopyText(message);

  // Copy message content (with HTTP fallback path).
  const handleCopy = () => {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(copyText).catch(() => {
        fallbackCopyToClipboard(copyText);
      });
    } else {
      fallbackCopyToClipboard(copyText);
    }
  };

  // Save as memory.
  const handleSaveAsMemory = async () => {
    try {
      setIsSaving(true);
      await saveMessageAsMemory(message);
      antMessage.success(t('chat.savedToMemory'));
    } catch (error) {
      console.error('Failed to save memory:', error);
      antMessage.error(t('chat.saveFailed'));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="message-time">
      <Space size="small">
        <Text type="secondary" style={{ fontSize: 12 }}>
          {formatTime(timestamp)}
        </Text>

        {type !== 'system' && (
          <Space size={4}>
            <Tooltip title={t('chat.copy')}>
              <Button
                type="text"
                size="small"
                icon={<CopyOutlined />}
                onClick={handleCopy}
                style={{ fontSize: 12, padding: '0 4px' }}
              />
            </Tooltip>

            <Tooltip title={t('chat.saveAsMemory')}>
              <Button
                type="text"
                size="small"
                icon={<DatabaseOutlined />}
                onClick={handleSaveAsMemory}
                loading={isSaving}
                style={{ fontSize: 12, padding: '0 4px' }}
              />
            </Tooltip>

            {type === 'assistant' && (
              <Tooltip title={t('chat.regenerate')}>
                <Button
                  type="text"
                  size="small"
                  icon={<ReloadOutlined />}
                  onClick={() => {
                    // For Deep Think messages, resend the original prompt directly (no action replay).
                    // Keep content-based fallback for older messages without metadata.
                    // Regex check for "Thinking Summary" is case-insensitive.
                    const isDeepThink = (metadata as any)?.deep_think === true || /thinking\s*summary/i.test(content || '');
                    if (isDeepThink) {
                      void retryLastMessage();
                      return;
                    }

                    const trackingId = (metadata as any)?.tracking_id;
                    if (typeof trackingId === 'string' && trackingId) {
                      void retryActionRun(trackingId, ((metadata as any)?.raw_actions as any[]) ?? []);
                    } else {
                      void retryLastMessage();
                    }
                  }}
                  disabled={isProcessing}
                  style={{ fontSize: 12, padding: '0 4px' }}
                />
              </Tooltip>
            )}
          </Space>
        )}
      </Space>
    </div>
  );
};

export { MessageActions };
export default MessageActions;
