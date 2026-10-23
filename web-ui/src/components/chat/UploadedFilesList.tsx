import React from 'react';
import { Tag, Space, Tooltip, message, Typography } from 'antd';
import { CloseOutlined, LoadingOutlined, PaperClipOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import type { UploadedFile } from '@/types';
import { useT } from '@/i18n/useT';
import { describeUploadError } from '@/utils/uploadError';
import { attachmentVisual } from './attachmentVisual';

const EMPTY_UPLOADS: Array<{ id: string; name: string; sessionKey: string }> = [];

const chipLabelStyle: React.CSSProperties = {
  maxWidth: 180,
  display: 'inline-block',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  verticalAlign: 'bottom',
};

/** Attachments of the message being written. They are sent with it and leave the composer then. */
const UploadedFilesList: React.FC = () => {
  const t = useT();
  const uploadedFiles = useChatStore((s) => s.uploadedFiles);
  const removeUploadedFile = useChatStore((s) => s.removeUploadedFile);
  const currentSessionId = useChatStore((s) => s.currentSession?.id ?? '');
  const allUploads = useChatStore((s) => s.uploadingFiles) ?? EMPTY_UPLOADS;
  const uploading = allUploads.filter((u) => u.sessionKey === currentSessionId);

  if (uploadedFiles.length === 0 && uploading.length === 0) {
    return null;
  }

  const handleRemove = async (file: UploadedFile) => {
    const name = file.original_name || file.file_name;
    try {
      await removeUploadedFile(file.file_id);
      message.success(t('uploadedFiles.removed', { name }));
    } catch (error) {
      console.error('Failed to remove file:', error);
      message.error(t('uploadedFiles.removeFailed', { name, detail: describeUploadError(error, 'remove') }));
    }
  };

  return (
    <div style={{ padding: '8px 0', borderBottom: '1px solid var(--border-subtle)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          <PaperClipOutlined style={{ marginRight: 4 }} />
          {t('uploadedFiles.header', { count: uploadedFiles.length })}
          {uploading.length > 0 ? ` · ${t('uploadedFiles.uploadingCount', { count: uploading.length })}` : ''}
        </Typography.Text>
      </div>
      <Space wrap size={[8, 8]}>
        {uploadedFiles.map((file, index) => {
          const name = file.original_name || file.file_name || '';
          const { icon, color } = attachmentVisual(name, { fileType: file.file_type, category: file.category });
          const tagKey = file.file_id || file.file_path || `${name}-${index}`;
          return (
            <Tag
              key={tagKey}
              icon={icon}
              closable
              closeIcon={<CloseOutlined aria-label={t('uploadedFiles.removeLabel', { name })} />}
              onClose={(e) => {
                e.preventDefault();
                e.stopPropagation();
                void handleRemove(file);
              }}
              color={color}
            >
              <Tooltip title={file.file_size ? `${name} (${file.file_size})` : name}>
                <span style={chipLabelStyle}>{name}</span>
              </Tooltip>
            </Tag>
          );
        })}
        {uploading.map((u) => (
          <Tag key={u.id} icon={<LoadingOutlined />}>
            <Tooltip title={t('uploadedFiles.uploadingOne', { name: u.name })}>
              <span style={chipLabelStyle}>{u.name}</span>
            </Tooltip>
          </Tag>
        ))}
      </Space>
    </div>
  );
};

export default UploadedFilesList;
