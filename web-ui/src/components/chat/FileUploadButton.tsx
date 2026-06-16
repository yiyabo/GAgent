import React, { useState } from 'react';
import { Button, message, Tooltip, Upload } from 'antd';
import { PaperClipOutlined, FolderOpenOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { UPLOAD_ACCEPT_LIST, isAllowedUploadFile } from '@/constants/uploadFileTypes';
import FileTreeSelector from './FileTreeSelector';

interface FileUploadButtonProps {
  size?: 'small' | 'middle' | 'large';
  projectId?: number;
}

const FileUploadButton: React.FC<FileUploadButtonProps> = ({ size = 'middle', projectId }) => {
  const { uploadFile, currentSession } = useChatStore();
  const [uploading, setUploading] = useState(false);
  const [treeSelectorVisible, setTreeSelectorVisible] = useState(false);

  const handleUpload = async (file: File) => {
    if (!currentSession) {
      message.error('Please create or select a session first.');
      return false;
    }

    setUploading(true);
    try {
      await uploadFile(file);
      message.success(`File ${file.name} uploaded successfully.`);
      return false;
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

    handleUpload(file);
    return false;
  };

  const handleFileTreeSelect = (files: Array<{ path: string; name: string; data_root_path: string }>) => {
    setTreeSelectorVisible(false);
    
    if (files.length > 0 && currentSession) {
      const store = useChatStore.getState();
      const fileRefs = files.map(file => ({
        file_id: `project_${file.path}`,
        file_path: `${file.data_root_path}/${file.path}`,
        file_name: file.name,
        original_name: file.name,
        file_size: 'Project File',
        file_type: 'project_reference',
        uploaded_at: new Date().toISOString(),
        category: 'project',
        is_archive: false,
      }));

      store.setUploadedFiles([...store.uploadedFiles, ...fileRefs]);
      message.success(`Added ${files.length} project files to session`);
    }
  };

  return (
    <>
      <div style={{ display: 'flex', gap: '4px' }}>
        <Upload
          beforeUpload={beforeUpload}
          showUploadList={false}
          accept={UPLOAD_ACCEPT_LIST}
          multiple={false}
        >
          <Tooltip title="Upload file">
            <Button
              type="text"
              size={size}
              icon={<PaperClipOutlined />}
              loading={uploading}
              disabled={!currentSession}
            />
          </Tooltip>
        </Upload>

        {projectId && (
          <Tooltip title="Select from project">
            <Button
              type="text"
              size={size}
              icon={<FolderOpenOutlined />}
              disabled={!currentSession}
              onClick={() => setTreeSelectorVisible(true)}
            />
          </Tooltip>
        )}
      </div>

      {projectId && (
        <FileTreeSelector
          projectId={projectId}
          visible={treeSelectorVisible}
          onCancel={() => setTreeSelectorVisible(false)}
          onSelect={handleFileTreeSelect}
        />
      )}
    </>
  );
};

export default FileUploadButton;
