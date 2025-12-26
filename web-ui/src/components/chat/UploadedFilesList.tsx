import React from 'react';
import { Tag, Space, Tooltip } from 'antd';
import { CloseOutlined, FileImageOutlined, FileTextOutlined, FileZipOutlined, PaperClipOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import type { UploadedFile } from '@/types';

const UploadedFilesList: React.FC = () => {
  const { uploadedFiles, removeUploadedFile } = useChatStore();

  if (uploadedFiles.length === 0) {
    return null;
  }

  const handleRemove = async (fileId: string) => {
    try {
      await removeUploadedFile(fileId);
    } catch (error) {
      console.error('删除文件失败:', error);
    }
  };

  return (
    <div style={{ padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
      <Space wrap size={[8, 8]}>
        {uploadedFiles.map((file) => {
          const fileName = (file.original_name || file.file_name || '').toLowerCase();
          const isImage = Boolean(
            file.file_type?.startsWith('image/') ||
            /\.(png|jpe?g|gif|webp|bmp|tiff?)$/.test(fileName)
          );
          const isArchive = file.category === 'archive' || /\.(zip|tar|tgz|tar\.gz|tar\.bz2|tbz2?)$/.test(fileName);
          const isDocument = file.category === 'document' || /\.(pdf|docx?|txt|md|rtf|csv)$/.test(fileName);

          const icon = isImage
            ? <FileImageOutlined />
            : isArchive
              ? <FileZipOutlined />
              : isDocument
                ? <FileTextOutlined />
                : <PaperClipOutlined />;
          const color = isImage ? 'blue' : isArchive ? 'gold' : isDocument ? 'geekblue' : undefined;
          
          return (
            <Tag
              key={file.file_id}
              icon={icon}
              closable
              onClose={(e) => {
                e.preventDefault();
                handleRemove(file.file_id);
              }}
              color={color}
            >
              <Tooltip title={`${file.original_name} (${file.file_size})`}>
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
