import React, { useState } from 'react';
import { Button, message, Tooltip, Upload } from 'antd';
import { PaperClipOutlined, FolderOpenOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { useAuthStore } from '@store/auth';
import { UPLOAD_ACCEPT_LIST, isAllowedUploadFile } from '@/constants/uploadFileTypes';
import FileTreeSelector from './FileTreeSelector';
import { useT } from '@/i18n/useT';
import { describeUploadError } from '@/utils/uploadError';

interface FileUploadButtonProps {
  size?: 'small' | 'middle' | 'large';
}

const FileUploadButton: React.FC<FileUploadButtonProps> = ({ size = 'middle' }) => {
  const t = useT();
  const uploadFile = useChatStore((s) => s.uploadFile);
  const currentSession = useChatStore((s) => s.currentSession);
  const { projectId } = useAuthStore();
  const [treeSelectorVisible, setTreeSelectorVisible] = useState(false);

  const handleUpload = async (file: File) => {
    if (!currentSession) {
      message.error(t('upload.needSession'));
      return;
    }
    try {
      await uploadFile(file);
      message.success(t('upload.uploaded', { name: file.name }));
    } catch (error) {
      message.error(t('upload.failed', { name: file.name, message: describeUploadError(error, 'upload') }));
    }
  };

  const beforeUpload = (file: File) => {
    if (!isAllowedUploadFile(file)) {
      message.error(t('upload.unsupportedType', { name: file.name }));
      return Upload.LIST_IGNORE;
    }

    void handleUpload(file);
    return false;
  };

  const handleFileTreeSelect = (files: Array<{ path: string; name: string; data_root_path: string }>) => {
    setTreeSelectorVisible(false);

    if (files.length > 0 && currentSession) {
      const store = useChatStore.getState();
      const existing = store.uploadedFiles;
      const existingKeys = new Set(
        existing.map((f) => f.file_path || f.file_id || f.original_name),
      );
      const fileRefs = files
        .map((file) => ({
          file_id: `project_${file.path}`,
          file_path: file.path,
          file_name: file.name,
          original_name: file.name,
          file_size: 'Project File',
          file_type: 'project_reference',
          uploaded_at: new Date().toISOString(),
          category: 'project',
          is_archive: false,
        }))
        .filter((f) => !existingKeys.has(f.file_path) && !existingKeys.has(f.file_id));

      if (fileRefs.length === 0) {
        message.info(t('upload.alreadyAttached'));
        return;
      }

      store.setUploadedFiles([...existing, ...fileRefs]);
      message.success(t('upload.addedProjectFiles', { count: fileRefs.length }));
    }
  };

  return (
    <>
      <div style={{ display: 'flex', gap: '4px' }}>
        <Upload
          beforeUpload={beforeUpload}
          showUploadList={false}
          accept={UPLOAD_ACCEPT_LIST}
          multiple
        >
          <Tooltip title={t('upload.tip')}>
            <Button
              type="text"
              size={size}
              icon={<PaperClipOutlined />}
              aria-label={t('upload.tip')}
              disabled={!currentSession}
            />
          </Tooltip>
        </Upload>

        {projectId && (
          <Tooltip title={t('upload.fromProject')}>
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
          sessionId={currentSession?.id}
          visible={treeSelectorVisible}
          onCancel={() => setTreeSelectorVisible(false)}
          onSelect={handleFileTreeSelect}
        />
      )}
    </>
  );
};

export default FileUploadButton;
