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
import { formatTime, fallbackCopyToClipboard } from './utils';

const { Text } = Typography;

interface MessageActionsProps {
  message: ChatMessageType;
}

const MessageActions: React.FC<MessageActionsProps> = ({ message }) => {
  const { type, content, timestamp, metadata } = message;
  const { saveMessageAsMemory, retryActionRun, retryLastMessage, isProcessing } = useChatStore();
  const [isSaving, setIsSaving] = useState(false);

  // Copy message content (with HTTP fallback path).
  const handleCopy = () => {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(content).catch(() => {
        fallbackCopyToClipboard(content);
      });
    } else {
      fallbackCopyToClipboard(content);
    }
  };

  // Save as memory.
  const handleSaveAsMemory = async () => {
    try {
      setIsSaving(true);
      await saveMessageAsMemory(message);
      antMessage.success('Saved to memory');
    } catch (error) {
      console.error('Failed to save memory:', error);
      antMessage.error('Save failed');
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
            <Tooltip title="Copy">
              <Button
                type="text"
                size="small"
                icon={<CopyOutlined />}
                onClick={handleCopy}
                style={{ fontSize: 10, padding: '0 4px' }}
              />
            </Tooltip>

            <Tooltip title="Save as Memory">
              <Button
                type="text"
                size="small"
                icon={<DatabaseOutlined />}
                onClick={handleSaveAsMemory}
                loading={isSaving}
                style={{ fontSize: 10, padding: '0 4px' }}
              />
            </Tooltip>

            {type === 'assistant' && (
              <Tooltip title="Regenerate">
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
                  style={{ fontSize: 10, padding: '0 4px' }}
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
