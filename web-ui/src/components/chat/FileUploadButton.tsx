import React, { useRef } from 'react';
import { Button, message, Tooltip, Upload } from 'antd';
import { PaperClipOutlined, FileImageOutlined, FilePdfOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import type { UploadFile } from 'antd/es/upload/interface';

interface FileUploadButtonProps {
  type?: 'file' | 'image';
  size?: 'small' | 'middle' | 'large';
}

const FileUploadButton: React.FC<FileUploadButtonProps> = ({ type = 'file', size = 'middle' }) => {
  const { uploadFile, uploadImage, currentSession } = useChatStore();
  const [uploading, setUploading] = React.useState(false);

  const handleUpload = async (file: File) => {
    if (!currentSession) {
      message.error('请先创建或选择一个会话');
      return false;
    }

    setUploading(true);
    try {
      if (type === 'image') {
        await uploadImage(file);
        message.success(`图片 ${file.name} 上传成功`);
      } else {
        await uploadFile(file);
        message.success(`文件 ${file.name} 上传成功`);
      }
      return false; // 阻止默认上传行为
    } catch (error: any) {
      message.error(`上传失败: ${error.message || '未知错误'}`);
      return false;
    } finally {
      setUploading(false);
    }
  };

  const beforeUpload = (file: File) => {
    // 验证文件类型
    if (type === 'image') {
      const isImage = file.type.startsWith('image/');
      if (!isImage) {
        message.error('只能上传图片文件！');
        return Upload.LIST_IGNORE;
      }
      const isLt20M = file.size / 1024 / 1024 < 20;
      if (!isLt20M) {
        message.error('图片大小不能超过 20MB！');
        return Upload.LIST_IGNORE;
      }
    } else {
      const isPdf = file.type === 'application/pdf';
      if (!isPdf) {
        message.error('只能上传 PDF 文件！');
        return Upload.LIST_IGNORE;
      }
      const isLt50M = file.size / 1024 / 1024 < 50;
      if (!isLt50M) {
        message.error('PDF 大小不能超过 50MB！');
        return Upload.LIST_IGNORE;
      }
    }

    // 执行上传
    handleUpload(file);
    return false; // 阻止默认上传
  };

  const icon = type === 'image' ? <FileImageOutlined /> : <FilePdfOutlined />;
  const tooltip = type === 'image' ? '上传图片' : '上传PDF';

  return (
    <Upload
      beforeUpload={beforeUpload}
      showUploadList={false}
      accept={type === 'image' ? 'image/*' : 'application/pdf'}
      multiple={false}
    >
      <Tooltip title={tooltip}>
        <Button
          type="text"
          size={size}
          icon={icon}
          loading={uploading}
          disabled={!currentSession}
        />
      </Tooltip>
    </Upload>
  );
};

export default FileUploadButton;
