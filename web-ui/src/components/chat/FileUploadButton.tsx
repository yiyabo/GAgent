import React from 'react';
import { Button, message, Tooltip, Upload } from 'antd';
import { PaperClipOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';

interface FileUploadButtonProps {
  size?: 'small' | 'middle' | 'large';
}

const ALLOWED_EXTENSIONS = [
  '.pdf',
  '.doc',
  '.docx',
  '.txt',
  '.md',
  '.rtf',
  '.csv',
  '.jpg',
  '.jpeg',
  '.png',
  '.gif',
  '.webp',
  '.bmp',
  '.tif',
  '.tiff',
  '.zip',
  '.tar',
  '.tar.gz',
  '.tgz',
  '.tar.bz2',
  '.tbz',
  '.tbz2',
  '.h5',
  '.hdf5',
  '.hdf',
  '.hd5',
  '.pdb',
  '.dcm',
  '.nii',
  '.nii.gz',
  '.npz',
  '.npy',
];

const ALLOWED_MIME_TYPES = new Set([
  'application/pdf',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'text/plain',
  'text/markdown',
  'text/csv',
  'application/rtf',
  'application/zip',
  'application/x-zip-compressed',
  'application/x-tar',
]);

const FileUploadButton: React.FC<FileUploadButtonProps> = ({ size = 'middle' }) => {
  const { uploadFile, currentSession } = useChatStore();
  const [uploading, setUploading] = React.useState(false);

  const handleUpload = async (file: File) => {
    if (!currentSession) {
      message.error('请先创建或选择一个会话');
      return false;
    }

    setUploading(true);
    try {
      await uploadFile(file);
      message.success(`文件 ${file.name} 上传成功`);
      return false; // 阻止默认上传行为
    } catch (error: any) {
      message.error(`上传失败: ${error.message || '未知错误'}`);
      return false;
    } finally {
      setUploading(false);
    }
  };

  const beforeUpload = (file: File) => {
    const fileName = file.name.toLowerCase();
    const isAllowed =
      file.type.startsWith('image/') ||
      ALLOWED_MIME_TYPES.has(file.type) ||
      ALLOWED_EXTENSIONS.some((ext) => fileName.endsWith(ext));

    if (!isAllowed) {
      message.error('不支持的文件类型');
      return Upload.LIST_IGNORE;
    }

    // 执行上传
    handleUpload(file);
    return false; // 阻止默认上传
  };

  const tooltip = '上传文件';
  const acceptList = [
    'image/*',
    'application/pdf',
    ...ALLOWED_EXTENSIONS,
  ].join(',');

  return (
    <Upload
      beforeUpload={beforeUpload}
      showUploadList={false}
      accept={acceptList}
      multiple={false}
    >
      <Tooltip title={tooltip}>
        <Button
          type="text"
          size={size}
          icon={<PaperClipOutlined />}
          loading={uploading}
          disabled={!currentSession}
        />
      </Tooltip>
    </Upload>
  );
};

export default FileUploadButton;
