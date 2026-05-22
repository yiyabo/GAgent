import React from 'react';
import { Button, message, Tooltip, Upload } from 'antd';
import { PaperClipOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { UPLOAD_ACCEPT_LIST, isAllowedUploadFile } from '@/constants/uploadFileTypes';

interface FileUploadButtonProps {
  size?: 'small' | 'middle' | 'large';
}

const FileUploadButton: React.FC<FileUploadButtonProps> = ({ size = 'middle' }) => {
  const { uploadFile, currentSession } = useChatStore();
  const [uploading, setUploading] = React.useState(false);

  const handleUpload = async (file: File) => {
    if (!currentSession) {
      message.error('Please create or select a session first.');
      return false;
    }

    setUploading(true);
    try {
      await uploadFile(file);
      message.success(`File ${file.name} uploaded successfully.`);
      return false; // Prevent default upload behavior.
    } catch (error: any) {
      message.error(`Upload failed: ${error.message || 'Unknown error'}`);
      return false;
    } finally {
      setUploading(false);
    }
  };

  const beforeUpload = (file: File) => {
    if (!isAllowedUploadFile(file)) {
      message.error('Unsupported file type.');
      return Upload.LIST_IGNORE;
    }

    // Execute upload
    handleUpload(file);
    return false; // Prevent default upload
  };

  const tooltip = 'Upload file';

  return (
    <Upload
      beforeUpload={beforeUpload}
      showUploadList={false}
      accept={UPLOAD_ACCEPT_LIST}
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
