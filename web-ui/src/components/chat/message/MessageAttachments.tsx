import React from 'react';
import { Tag, Tooltip } from 'antd';
import type { MessageAttachment } from '@/utils/messageAttachments';
import { useT } from '@/i18n/useT';
import { attachmentVisual } from '../attachmentVisual';

/** The files that were sent with a user message, shown under its text. */
const MessageAttachments: React.FC<{ attachments?: MessageAttachment[] | null }> = ({ attachments }) => {
  const t = useT();
  if (!Array.isArray(attachments) || attachments.length === 0) return null;
  return (
    <div
      className="message-attachments"
      role="list"
      aria-label={t('messageAttachments.label', { count: attachments.length })}
      style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}
    >
      {attachments.map((att, index) => {
        const { icon, color } = attachmentVisual(att.name, { image: att.type === 'image' });
        return (
          <Tag key={att.path || `${att.name}-${index}`} role="listitem" icon={icon} color={color} style={{ marginInlineEnd: 0 }}>
            <Tooltip title={att.name}>
              <span style={{ maxWidth: 220, display: 'inline-block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>
                {att.name}
              </span>
            </Tooltip>
          </Tag>
        );
      })}
    </div>
  );
};

export default MessageAttachments;
