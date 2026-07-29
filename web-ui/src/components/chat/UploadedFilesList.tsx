import React, { useEffect } from 'react';
import { Tag, Space, Tooltip, message, Typography } from 'antd';
import { CloseOutlined, FileImageOutlined, FileTextOutlined, FileZipOutlined, PaperClipOutlined, CloudServerOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import type { UploadedFile } from '@/types';

const UploadedFilesList: React.FC = () => {
  const uploadedFiles = useChatStore((s) => s.uploadedFiles);
  const removeUploadedFile = useChatStore((s) => s.removeUploadedFile);
  const syncUploadedFilesFromServer = useChatStore((s) => s.syncUploadedFilesFromServer);
  const currentSession = useChatStore((s) => s.currentSession);
  const sessionKey = currentSession?.session_id ?? currentSession?.id ?? '';

  useEffect(() => {
    if (!sessionKey) return;
    void syncUploadedFilesFromServer();
  }, [sessionKey, syncUploadedFilesFromServer]);

  if (uploadedFiles.length === 0) {
    return null;
  }

  const serverCount = uploadedFiles.filter(
    (f) => f.source === 'server' || (!f.source && f.category !== 'project' && f.file_type !== 'project_reference'),
  ).length;

  const handleRemove = async (file: UploadedFile) => {
    try {
      await removeUploadedFile(file.file_id);
      message.success(`已删除：${file.original_name || file.file_name}`);
    } catch (error: any) {
      console.error('Failed to remove file:', error);
      const detail = error?.response?.data?.detail || error?.message || '未知错误';
      message.error(`删除失败（文件仍保留在会话中）：${detail}`);
      void syncUploadedFilesFromServer();
    }
  };

  return (
    <div style={{ padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          <CloudServerOutlined style={{ marginRight: 4 }} />
          当前会话附件（与服务器 uploads 一致）· {serverCount} 个文件
        </Typography.Text>
      </div>
      <Space wrap size={[8, 8]}>
        {uploadedFiles.map((file, index) => {
          const fileName = (file.original_name || file.file_name || '').toLowerCase();
          const isImage = Boolean(
            file.file_type?.startsWith('image/') ||
            /\.(png|jpe?g|gif|webp|bmp|tiff?)$/.test(fileName)
          );
          const isArchive = file.category === 'archive' || /\.(zip|tar|tgz|tar\.gz|tar\.bz2|tbz2?)$/.test(fileName);
          const isDocument = file.category === 'document' || file.category === 'project' || /\.(pdf|docx?|txt|md|rtf|csv|xlsx?)$/.test(fileName);

          const icon = isImage
            ? <FileImageOutlined />
            : isArchive
              ? <FileZipOutlined />
              : isDocument
                ? <FileTextOutlined />
                : <PaperClipOutlined />;
          const color = isImage ? 'blue' : isArchive ? 'gold' : isDocument ? 'geekblue' : undefined;
          const tagKey = file.file_id || file.file_path || `${file.original_name}-${index}`;
          const tip = `${file.original_name} (${file.file_size})${file.source === 'server' ? ' · 服务器' : ''}`;

          return (
            <Tag
              key={tagKey}
              icon={icon}
              closable
              closeIcon={<CloseOutlined />}
              onClose={(e) => {
                e.preventDefault();
                e.stopPropagation();
                void handleRemove(file);
              }}
              color={color}
            >
              <Tooltip title={tip}>
                <span style={{ maxWidth: 150, display: 'inline-block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {file.original_name}
                </span>
              </Tooltip>
            </Tag>
          );
        })}
      </Space>
    </div>
  );
};

export default UploadedFilesList;
