import React, { useState, useEffect } from 'react';
import { Tree, Modal, Button, Spin, message, Space, Typography, Select } from 'antd';
import { FolderOutlined, FileOutlined } from '@ant-design/icons';
import { projectApi, FileTreeNode } from '@api/project';
import { useProjectStore } from '@store/project';
import { useT } from '@/i18n/useT';

interface FileTreeSelectorProps {
  projectId: number;
  sessionId?: string;
  visible: boolean;
  onCancel: () => void;
  onSelect: (files: Array<{ path: string; name: string; data_root_path: string }>) => void;
}

const FileTreeSelector: React.FC<FileTreeSelectorProps> = ({
  projectId,
  sessionId,
  visible,
  onCancel,
  onSelect,
}) => {
  const t = useT();
  const [treeData, setTreeData] = useState<FileTreeNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [dataRootIndex, setDataRootIndex] = useState(0);
  const { projectData } = useProjectStore();
  const dataRoots = projectData?.data_roots ?? [];

  useEffect(() => {
    if (visible) {
      loadTreeData();
    }
  }, [visible, projectId, dataRootIndex]);

  const loadTreeData = async () => {
    setLoading(true);
    try {
      const response = await projectApi.getProjectFiles(projectId, dataRootIndex);
      console.log('📁 Project files response:', response);
      if (response.code === 0) {
        setTreeData(response.data);
      } else {
        message.error(response.message || t('fileTree.loadFailed'));
      }
    } catch (error: any) {
      console.error('❌ Error loading project files:', error);
      console.error('❌ Error details:', error.response?.data || error.message);
      message.error(t('fileTree.loadProjectFailed', { message: error.message || t('app.unknownError') }));
    } finally {
      setLoading(false);
    }
  };

  const onExpand = (expandedKeysValue: string[]) => {
    setExpandedKeys(expandedKeysValue);
  };

  const onCheck = (checkedKeysValue: string[]) => {
    setSelectedKeys(checkedKeysValue as string[]);
  };

  const handleSelect = async () => {
    if (selectedKeys.length === 0) {
      message.warning(t('fileTree.selectAtLeastOne'));
      return;
    }

    try {
      const response = await projectApi.selectFiles(
        projectId,
        selectedKeys,
        dataRootIndex,
        sessionId,
      );
      if (response.code === 0) {
        onSelect(response.files);
        message.success(t('fileTree.selectedCount', { count: response.files.length }));
      } else {
        message.error(response.message || t('fileTree.selectFailed'));
      }
    } catch (error) {
      message.error(t('fileTree.selectFailed'));
      console.error('Error selecting files:', error);
    }
  };

  const renderTreeNodes = (nodes: FileTreeNode[]): any[] => {
    return nodes.map((node) => ({
      title: node.title,
      key: node.key,
      icon: node.is_leaf ? <FileOutlined /> : <FolderOutlined />,
      children: node.children && node.children.length > 0 ? renderTreeNodes(node.children) : undefined,
      selectable: node.is_leaf,
      checkable: node.is_leaf,
    }));
  };

  return (
    <Modal
      title={t('fileTree.title')}
      open={visible}
      onCancel={onCancel}
      width={600}
      footer={[
        <Button key="cancel" onClick={onCancel}>
          {t('common.cancel')}
        </Button>,
        <Button key="select" type="primary" onClick={handleSelect} disabled={selectedKeys.length === 0}>
          {t('fileTree.selectButton', { count: selectedKeys.length })}
        </Button>,
      ]}
    >
      <Space direction="vertical" style={{ width: '100%' }}>
        <Typography.Text type="secondary">
          {t('fileTree.hint')}
        </Typography.Text>
        {dataRoots.length > 1 && (
          <Select
            value={dataRootIndex}
            onChange={(val) => {
              setDataRootIndex(val);
              setTreeData([]);
              setSelectedKeys([]);
              setExpandedKeys([]);
            }}
            style={{ width: '100%' }}
            options={dataRoots.map((root, idx) => ({
              value: idx,
              label: root.label || root.path,
            }))}
            placeholder={t('fileTree.dataRootPlaceholder')}
          />
        )}
        {loading ? (
          <div style={{ textAlign: 'center', padding: '20px' }}>
            <Spin tip={t('fileTree.loadingFiles')} />
          </div>
        ) : (
          <Tree
            checkable
            selectable={false}
            onExpand={onExpand}
            expandedKeys={expandedKeys}
            onCheck={onCheck}
            checkedKeys={selectedKeys}
            treeData={renderTreeNodes(treeData)}
            style={{ maxHeight: '400px', overflow: 'auto' }}
          />
        )}
      </Space>
    </Modal>
  );
};

export default FileTreeSelector;
