import React, { useState, useEffect } from 'react';
import { Tree, Modal, Button, Spin, message, Space, Typography } from 'antd';
import { FolderOutlined, FileOutlined } from '@ant-design/icons';
import { projectApi, FileTreeNode } from '@api/project';

interface FileTreeSelectorProps {
  projectId: number;
  visible: boolean;
  onCancel: () => void;
  onSelect: (files: Array<{ path: string; name: string; data_root_path: string }>) => void;
}

const FileTreeSelector: React.FC<FileTreeSelectorProps> = ({
  projectId,
  visible,
  onCancel,
  onSelect,
}) => {
  const [treeData, setTreeData] = useState<FileTreeNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);

  useEffect(() => {
    if (visible) {
      loadTreeData();
    }
  }, [visible, projectId]);

  const loadTreeData = async () => {
    setLoading(true);
    try {
      console.log('📁 Loading project files for projectId:', projectId);
      const response = await projectApi.getProjectFiles(projectId);
      console.log('📁 Project files response:', response);
      if (response.code === 0) {
        setTreeData(response.data);
      } else {
        message.error(response.message || 'Failed to load files');
      }
    } catch (error: any) {
      console.error('❌ Error loading project files:', error);
      console.error('❌ Error details:', error.response?.data || error.message);
      message.error(`Failed to load project files: ${error.message || 'Unknown error'}`);
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
      message.warning('Please select at least one file');
      return;
    }

    try {
      const response = await projectApi.selectFiles(projectId, selectedKeys);
      if (response.code === 0) {
        onSelect(response.files);
        message.success(`Selected ${response.files.length} files`);
      } else {
        message.error(response.message || 'Failed to select files');
      }
    } catch (error) {
      message.error('Failed to select files');
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
      title="Select Files from Project"
      open={visible}
      onCancel={onCancel}
      width={600}
      footer={[
        <Button key="cancel" onClick={onCancel}>
          Cancel
        </Button>,
        <Button key="select" type="primary" onClick={handleSelect} disabled={selectedKeys.length === 0}>
          Select ({selectedKeys.length})
        </Button>,
      ]}
    >
      <Space direction="vertical" style={{ width: '100%' }}>
        <Typography.Text type="secondary">
          Select files from the project data roots to use in your chat session.
        </Typography.Text>
        
        {loading ? (
          <div style={{ textAlign: 'center', padding: '20px' }}>
            <Spin tip="Loading files..." />
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
