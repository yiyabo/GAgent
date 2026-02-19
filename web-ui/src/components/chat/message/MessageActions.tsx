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

  // 复制消息内容 (带降级方案，支持 HTTP 环境)
  const handleCopy = () => {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(content).catch(() => {
        fallbackCopyToClipboard(content);
      });
    } else {
      fallbackCopyToClipboard(content);
    }
  };

  // 保存为记忆
  const handleSaveAsMemory = async () => {
    try {
      setIsSaving(true);
      await saveMessageAsMemory(message);
      antMessage.success('✅ 已保存为记忆');
    } catch (error) {
      console.error('保存记忆失败:', error);
      antMessage.error('❌ 保存失败');
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
            <Tooltip title="复制">
              <Button
                type="text"
                size="small"
                icon={<CopyOutlined />}
                onClick={handleCopy}
                style={{ fontSize: 10, padding: '0 4px' }}
              />
            </Tooltip>

            <Tooltip title="保存为记忆">
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
              <Tooltip title="重新生成">
                <Button
                  type="text"
                  size="small"
                  icon={<ReloadOutlined />}
                  onClick={() => {
                    // Deep Think 消息直接重新发送原始消息，不 replay actions
                    // 增加内容检测作为兜底，防止旧消息没有 metadata 导致误触发
                    // 使用正则检测 'Thinking Summary'，忽略大小写
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
