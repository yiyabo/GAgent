import React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Button,
  Empty,
  Input,
  Space,
  Tag,
  Typography,
  Tooltip,
  Tree,
} from 'antd';
import {
  FileTextOutlined,
  FileImageOutlined,
  FileOutlined,
  FolderOpenOutlined,
  ReloadOutlined,
  LinkOutlined,
  FullscreenOutlined,
  FullscreenExitOutlined,
} from '@ant-design/icons';
import { artifactsApi, buildArtifactFileUrl } from '@api/artifacts';
import type { ArtifactItem } from '@/types';
import type { DataNode } from 'antd/es/tree';
import { useLayoutStore } from '@store/layout';

const { Text } = Typography;

const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg']);
const TEXT_EXTS = new Set([
  'md',
  'txt',
  'csv',
  'tsv',
  'json',
  'log',
  'py',
  'r',
  'html',
]);

const formatSize = (size = 0) => {
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${size} B`;
};

interface ArtifactsPanelProps {
  sessionId: string | null;
}

type ArtifactTreeNode = DataNode & {
  filePath?: string;
  isLeaf?: boolean;
};

const ArtifactsPanel: React.FC<ArtifactsPanelProps> = ({ sessionId }) => {
  const { dagSidebarFullscreen, toggleDagSidebarFullscreen } = useLayoutStore();
  const [keyword, setKeyword] = React.useState('');
  const [selectedPath, setSelectedPath] = React.useState<string | null>(null);

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ['artifacts', sessionId],
    queryFn: () =>
      artifactsApi.listSessionArtifacts(sessionId ?? '', {
        maxDepth: 4,
        includeDirs: false,
        limit: 500,
      }),
    enabled: Boolean(sessionId),
    refetchInterval: 10000,
  });

  const items = React.useMemo(() => {
    if (!data?.items) return [];
    const normalizedKeyword = keyword.trim().toLowerCase();
    if (!normalizedKeyword) return data.items;
    return data.items.filter((item) => item.path.toLowerCase().includes(normalizedKeyword));
  }, [data?.items, keyword]);

  const selectedItem = items.find((item) => item.path === selectedPath) ?? null;

  React.useEffect(() => {
    if (!items.length) {
      setSelectedPath(null);
      return;
    }
    if (!selectedPath || !items.find((item) => item.path === selectedPath)) {
      setSelectedPath(items[0].path);
    }
  }, [items, selectedPath]);

  const isImage = selectedItem?.extension ? IMAGE_EXTS.has(selectedItem.extension) : false;
  const isText = selectedItem?.extension ? TEXT_EXTS.has(selectedItem.extension) : false;

  const { data: textPreview, isLoading: textLoading } = useQuery({
    queryKey: ['artifact-text', sessionId, selectedItem?.path],
    queryFn: () =>
      artifactsApi.getSessionArtifactText(sessionId ?? '', selectedItem?.path ?? '', {
        maxBytes: 200000,
      }),
    enabled: Boolean(sessionId && selectedItem?.path && isText),
  });

  if (!sessionId) {
    return (
      <div style={{ padding: 16 }}>
        <Empty description="暂无会话，无法加载产物" />
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 16 }}>
        <Empty description={`加载失败: ${(error as Error).message}`} />
      </div>
    );
  }

  const treeData = React.useMemo<ArtifactTreeNode[]>(() => {
    const rootMap = new Map<string, ArtifactTreeNode>();

    const ensureNode = (parent: ArtifactTreeNode | null, key: string, title: string) => {
      if (!parent) {
        if (!rootMap.has(key)) {
          rootMap.set(key, {
            key,
            title,
            children: [],
            selectable: false,
            icon: <FolderOpenOutlined />,
          });
        }
        return rootMap.get(key)!;
      }

      const children = (parent.children ?? []) as ArtifactTreeNode[];
      const existing = children.find((child) => child.key === key);
      if (existing) return existing;

      const node: ArtifactTreeNode = {
        key,
        title,
        children: [],
        selectable: false,
        icon: <FolderOpenOutlined />,
      };
      parent.children = [...children, node];
      return node;
    };

    const addFile = (item: ArtifactItem) => {
      const parts = item.path.split('/').filter(Boolean);
      let parent: ArtifactTreeNode | null = null;
      let currentKey = '';
      parts.forEach((part, idx) => {
        currentKey = currentKey ? `${currentKey}/${part}` : part;
        const isLast = idx === parts.length - 1;
        if (isLast) {
          const ext = item.extension ?? '';
          const icon = IMAGE_EXTS.has(ext)
            ? <FileImageOutlined />
            : TEXT_EXTS.has(ext)
              ? <FileTextOutlined />
              : <FileOutlined />;
          const leafNode: ArtifactTreeNode = {
            key: currentKey,
            title: part,
            icon,
            isLeaf: true,
            selectable: true,
            filePath: item.path,
          };
          if (parent) {
            const children = (parent.children ?? []) as ArtifactTreeNode[];
            parent.children = [...children, leafNode];
          } else {
            rootMap.set(currentKey, leafNode);
          }
        } else {
          parent = ensureNode(parent, currentKey, part);
        }
      });
    };

    items.forEach((item) => {
      if (item.type === 'file') {
        addFile(item);
      }
    });

    const sortNodes = (nodes: ArtifactTreeNode[]) => {
      nodes.sort((a, b) => {
        const aIsLeaf = Boolean(a.isLeaf);
        const bIsLeaf = Boolean(b.isLeaf);
        if (aIsLeaf !== bIsLeaf) {
          return aIsLeaf ? 1 : -1;
        }
        return String(a.title).localeCompare(String(b.title));
      });
      nodes.forEach((node) => {
        if (node.children && node.children.length > 0) {
          sortNodes(node.children as ArtifactTreeNode[]);
        }
      });
    };

    const roots = Array.from(rootMap.values());
    sortNodes(roots);
    return roots;
  }, [items]);

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-color)' }}>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              生成文件列表
            </Text>
            <Space size={6}>
              <Tooltip title={dagSidebarFullscreen ? '退出全屏' : '侧边栏全屏'}>
                <Button
                  size="small"
                  icon={dagSidebarFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
                  onClick={toggleDagSidebarFullscreen}
                />
              </Tooltip>
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={() => refetch()}
                loading={isFetching}
              >
                刷新
              </Button>
            </Space>
          </Space>
          <Input.Search
            placeholder="搜索文件名或路径"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            allowClear
          />
        </Space>
      </div>

      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <div style={{ width: 240, borderRight: '1px solid var(--border-color)', overflow: 'auto' }}>
          {isLoading ? (
            <div style={{ padding: 16 }}>
              <Text type="secondary">加载中...</Text>
            </div>
          ) : items.length ? (
            <Tree
              showIcon
              treeData={treeData}
              selectedKeys={selectedPath ? [selectedPath] : []}
              onSelect={(keys, info) => {
                const node = info.node as ArtifactTreeNode;
                if (node.isLeaf && typeof node.key === 'string') {
                  setSelectedPath(node.key);
                }
              }}
              defaultExpandAll
              style={{ padding: '8px 8px 16px' }}
            />
          ) : (
            <div style={{ padding: 16 }}>
              <Empty description="暂无产物文件" />
            </div>
          )}
        </div>

        <div style={{ flex: 1, padding: 16, overflow: 'hidden', minHeight: 0 }}>
          {selectedItem ? (
            <div
              style={{
                width: '100%',
                height: '100%',
                overflow: 'auto',
                display: 'flex',
                flexDirection: 'column',
                gap: 12,
              }}
            >
              <Space size={8} wrap>
                <Text strong>{selectedItem.name}</Text>
                {selectedItem.extension && <Tag>{selectedItem.extension}</Tag>}
                {selectedItem.size !== undefined && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {formatSize(selectedItem.size)}
                  </Text>
                )}
                <Tooltip title="打开文件">
                  <Button
                    size="small"
                    icon={<LinkOutlined />}
                    onClick={() => {
                      const url = buildArtifactFileUrl(sessionId, selectedItem.path);
                      window.open(url, '_blank');
                    }}
                  />
                </Tooltip>
              </Space>

              {isImage && (
                <img
                  src={buildArtifactFileUrl(sessionId, selectedItem.path)}
                  alt={selectedItem.name}
                  style={{ width: '100%', borderRadius: 8, border: '1px solid var(--border-color)' }}
                />
              )}

              {isText && (
                <div
                  style={{
                    border: '1px solid var(--border-color)',
                    borderRadius: 8,
                    padding: 12,
                    background: 'var(--bg-tertiary)',
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                    fontSize: 12,
                    whiteSpace: 'pre-wrap',
                    lineHeight: 1.5,
                  }}
                >
                  {textLoading ? '加载中...' : textPreview?.content ?? '无法预览文本'}
                  {textPreview?.truncated && (
                    <div style={{ marginTop: 8 }}>
                      <Text type="secondary">内容过长，已截断</Text>
                    </div>
                  )}
                </div>
              )}

              {!isImage && !isText && (
                <Text type="secondary">该文件类型暂不支持预览，请点击右侧按钮下载。</Text>
              )}
            </div>
          ) : (
            <Empty description="请选择一个文件预览" />
          )}
        </div>
      </div>
    </div>
  );
};

export default ArtifactsPanel;
