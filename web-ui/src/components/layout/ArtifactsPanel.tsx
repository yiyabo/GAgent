import React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Button,
  Empty,
  Input,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
  Tree,
  Typography,
} from 'antd';
import {
  CheckCircleOutlined,
  CloudDownloadOutlined,
  EditOutlined,
  FileImageOutlined,
  FileOutlined,
  FilePdfOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  LinkOutlined,
  ReloadOutlined,
  StopOutlined,
  TableOutlined,
} from '@ant-design/icons';
import {
  artifactsApi,
  buildArtifactFileUrl,
  buildDeliverableFileUrl,
} from '@api/artifacts';
import type { ArtifactItem, DeliverableItem } from '@/types';
import type { DataNode } from 'antd/es/tree';
import type { ColumnsType } from 'antd/es/table';
import { useLayoutStore } from '@store/layout';

const { Text } = Typography;

const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg']);
const CSV_EXTS = new Set(['csv', 'tsv']);
const PDF_EXTS = new Set(['pdf']);
const TEXT_EXTS = new Set(['md', 'txt', 'csv', 'tsv', 'json', 'log', 'py', 'r', 'html', 'tex', 'bib']);

/* ---- CSV / TSV parsing ---- */

interface ParsedTable {
  columns: string[];
  rows: string[][];
}

function parseDelimited(content: string, delimiter: string): ParsedTable {
  const lines = content.trim().split('\n').filter((l) => l.trim());
  if (!lines.length) return { columns: [], rows: [] };
  const parse = (line: string) =>
    line.split(delimiter).map((c) => c.trim().replace(/^"|"$/g, ''));
  return { columns: parse(lines[0]), rows: lines.slice(1).map(parse) };
}

const CSVTablePreview: React.FC<{ content: string; extension: string }> = ({ content, extension }) => {
  const delimiter = extension === 'tsv' ? '\t' : ',';
  const { columns, rows } = React.useMemo(() => parseDelimited(content, delimiter), [content, delimiter]);

  const antColumns: ColumnsType<Record<string, string>> = columns.map((col, i) => ({
    title: col,
    dataIndex: `col_${i}`,
    key: `col_${i}`,
    ellipsis: true,
    sorter: (a: Record<string, string>, b: Record<string, string>) =>
      (a[`col_${i}`] ?? '').localeCompare(b[`col_${i}`] ?? ''),
  }));

  const dataSource = rows.map((row, ri) => {
    const record: Record<string, string> = { key: String(ri) };
    columns.forEach((_, ci) => { record[`col_${ci}`] = row[ci] ?? ''; });
    return record;
  });

  if (!columns.length) return <Empty description="No tabular data detected" />;

  return (
    <div style={{ overflow: 'auto' }}>
      <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
        <TableOutlined style={{ color: 'var(--primary-color)' }} />
        <Text type="secondary" style={{ fontSize: 12 }}>
          {rows.length} rows x {columns.length} columns
        </Text>
      </div>
      <Table
        columns={antColumns}
        dataSource={dataSource}
        size="small"
        pagination={rows.length > 100 ? { pageSize: 100, showSizeChanger: true } : false}
        scroll={{ x: 'max-content' }}
        bordered
        style={{ fontSize: 12 }}
      />
    </div>
  );
};

const formatSize = (size = 0) => {
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${size} B`;
};

const formatModuleName = (module?: string) => {
  if (!module) return '';
  if (module === 'image_tabular') return 'image & tabular';
  return module;
};

interface ArtifactsPanelProps {
  sessionId: string | null;
}

type PanelMode = 'deliverables' | 'raw';

type ArtifactTreeNode = DataNode & {
  filePath?: string;
  isLeaf?: boolean;
};

interface DisplayFileItem {
  path: string;
  name: string;
  extension?: string | null;
  size?: number;
  module?: string;
  sourceType: PanelMode;
  status?: string;
}

const isNotFoundError = (error: unknown): boolean => {
  if (!(error instanceof Error)) {
  return false;
  }
  const status = (error as any)?.status;
  const message = error.message.toLowerCase();
  return status === 404 || message.includes('404') || message.includes('not found');
};

const ArtifactsPanel: React.FC<ArtifactsPanelProps> = ({ sessionId }) => {
  const { dagSidebarFullscreen, toggleDagSidebarFullscreen } = useLayoutStore();
  const [mode, setMode] = React.useState<PanelMode>('deliverables');
  const [keyword, setKeyword] = React.useState('');
  const [selectedPath, setSelectedPath] = React.useState<string | null>(null);

  const {
  data: rawData,
  isLoading: rawLoading,
  isFetching: rawFetching,
  error: rawError,
  refetch: refetchRaw,
  } = useQuery({
  queryKey: ['artifacts', 'raw', sessionId],
  queryFn: () =>
  artifactsApi.listSessionArtifacts(sessionId ?? '', {
  maxDepth: 4,
  includeDirs: false,
  limit: 500,
  }),
  enabled: Boolean(sessionId && mode === 'raw'),
  refetchInterval: 10000,
  });

  const {
  data: deliverableData,
  isLoading: deliverableLoading,
  isFetching: deliverableFetching,
  error: deliverableError,
  refetch: refetchDeliverables,
  } = useQuery({
  queryKey: ['artifacts', 'deliverables', sessionId],
  queryFn: () =>
  artifactsApi.listSessionDeliverables(sessionId ?? '', {
  scope: 'latest',
  includeDraft: false,
  limit: 1000,
  }),
  enabled: Boolean(sessionId && mode === 'deliverables'),
  refetchInterval: 10000,
  });

  const displayItems = React.useMemo<DisplayFileItem[]>(() => {
  if (mode === 'deliverables') {
  return (deliverableData?.items ?? []).map((item: DeliverableItem) => ({
  path: item.path,
  name: item.name,
  extension: item.extension,
  size: item.size,
  module: item.module,
  sourceType: 'deliverables',
  status: item.status,
  }));
  }
  return (rawData?.items ?? [])
  .filter((item: ArtifactItem) => item.type === 'file')
  .map((item: ArtifactItem) => ({
  path: item.path,
  name: item.name,
  extension: item.extension,
  size: item.size,
  sourceType: 'raw',
  }));
  }, [deliverableData?.items, mode, rawData?.items]);

  const filteredItems = React.useMemo(() => {
  const normalizedKeyword = keyword.trim().toLowerCase();
  if (!normalizedKeyword) {
  return displayItems;
  }
  return displayItems.filter((item) => item.path.toLowerCase().includes(normalizedKeyword));
  }, [displayItems, keyword]);

  const selectedItem = filteredItems.find((item) => item.path === selectedPath) ?? null;

  React.useEffect(() => {
  if (!filteredItems.length) {
  setSelectedPath(null);
  return;
  }
  if (!selectedPath || !filteredItems.some((item) => item.path === selectedPath)) {
  setSelectedPath(filteredItems[0].path);
  }
  }, [filteredItems, selectedPath]);

  const isImage = selectedItem?.extension ? IMAGE_EXTS.has(selectedItem.extension) : false;
  const isCSV = selectedItem?.extension ? CSV_EXTS.has(selectedItem.extension) : false;
  const isPDF = selectedItem?.extension ? PDF_EXTS.has(selectedItem.extension) : false;
  const isText = selectedItem?.extension ? TEXT_EXTS.has(selectedItem.extension) : false;

  const { data: textPreview, isLoading: textLoading } = useQuery({
  queryKey: ['artifacts', 'text', sessionId, selectedItem?.sourceType, selectedItem?.path],
  queryFn: async () => {
  if (!selectedItem?.path) {
  throw new Error('filepath');
  }
  if (selectedItem.sourceType === 'deliverables') {
  return artifactsApi.getSessionDeliverableText(sessionId ?? '', selectedItem.path, {
  maxBytes: 200000,
  });
  }
  return artifactsApi.getSessionArtifactText(sessionId ?? '', selectedItem.path, {
  maxBytes: 200000,
  });
  },
  enabled: Boolean(sessionId && selectedItem?.path && isText),
  });

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
  if (existing) {
  return existing;
  }

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

  const addFile = (item: DisplayFileItem) => {
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
  : CSV_EXTS.has(ext)
  ? <TableOutlined />
  : PDF_EXTS.has(ext)
  ? <FilePdfOutlined />
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
  return;
  }
  parent = ensureNode(parent, currentKey, part);
  });
  };

  filteredItems.forEach((item) => addFile(item));

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
  }, [filteredItems]);

  const activeError = mode === 'deliverables' ? deliverableError : rawError;
  const isLoading = mode === 'deliverables' ? deliverableLoading : rawLoading;
  const isFetching = mode === 'deliverables' ? deliverableFetching : rawFetching;

  if (!sessionId) {
  return (
  <div style={{ padding: 16 }}>
  <Empty description="No active session. Unable to load artifacts." />
  </div>
  );
  }

  if (activeError && !isNotFoundError(activeError)) {
  return (
  <div style={{ padding: 16 }}>
  <Empty description={`Load failed: ${(activeError as Error).message}`} />
  </div>
  );
  }

  const paperCompleted = Number(deliverableData?.paper_status?.completed_count ?? 0);
  const paperTotal = Number(deliverableData?.paper_status?.total_sections ?? 0);

  const fileUrl = selectedItem
  ? selectedItem.sourceType === 'deliverables'
  ? buildDeliverableFileUrl(sessionId, selectedItem.path)
  : buildArtifactFileUrl(sessionId, selectedItem.path)
  : null;

  const handleRefetch = () => {
  if (mode === 'deliverables') {
  void refetchDeliverables();
  return;
  }
  void refetchRaw();
  };

  return (
  <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
  <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-color)' }}>
  <Space direction="vertical" size={8} style={{ width: '100%' }}>
  <Space style={{ width: '100%', justifyContent: 'space-between' }}>
  <Space direction="vertical" size={2}>
  <Text type="secondary" style={{ fontSize: 12 }}>
  {mode === 'deliverables' ? 'file' : 'file'}
  </Text>
  {mode === 'deliverables' && (
  <Text type="secondary" style={{ fontSize: 12 }}>
  {deliverableData?.count ?? 0} file
  {paperTotal > 0 ? ` ·  ${paperCompleted}/${paperTotal}` : ''}
  </Text>
  )}
  </Space>
  <Space size={6}>
  <Tooltip title={dagSidebarFullscreen ? '' : ''}>
  <Button
  size="small"
  icon={dagSidebarFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
  onClick={toggleDagSidebarFullscreen}
  />
  </Tooltip>
  <Button size="small" icon={<ReloadOutlined />} onClick={handleRefetch} loading={isFetching}>
  refresh
  </Button>
  </Space>
  </Space>

  <Segmented
  value={mode}
  options={[
  { label: 'Deliverables', value: 'deliverables' },
  { label: 'Raw Files', value: 'raw' },
  ]}
  onChange={(value) => {
  setMode(value as PanelMode);
  setSelectedPath(null);
  setKeyword('');
  }}
  block
  />

  <Input.Search
  placeholder="Search file paths"
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
  <Text type="secondary">Loading...</Text>
  </div>
  ) : filteredItems.length ? (
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
  <Empty description="No files" />
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
  {selectedItem.sourceType === 'deliverables' && selectedItem.module && (
  <Tag color="blue">{formatModuleName(selectedItem.module)}</Tag>
  )}
  {selectedItem.sourceType === 'deliverables' && selectedItem.status === 'draft' && (
  <Tag icon={<EditOutlined />} color="orange">draft</Tag>
  )}
  {selectedItem.sourceType === 'deliverables' && selectedItem.status === 'final' && (
  <Tag icon={<CheckCircleOutlined />} color="green">final</Tag>
  )}
  {selectedItem.sourceType === 'deliverables' && selectedItem.status === 'superseded' && (
  <Tag icon={<StopOutlined />} color="default">superseded</Tag>
  )}
  <Tooltip title="Open file">
  <Button
  size="small"
  icon={<LinkOutlined />}
  onClick={() => {
  if (fileUrl) {
  window.open(fileUrl, '_blank');
  }
  }}
  />
  </Tooltip>
  </Space>

  {isImage && fileUrl && (
  <img
  src={fileUrl}
  alt={selectedItem.name}
  style={{ width: '100%', borderRadius: 8, border: '1px solid var(--border-color)' }}
  />
  )}

  {isPDF && fileUrl && (
  <iframe
  src={fileUrl}
  title={selectedItem.name}
  style={{
  width: '100%',
  flex: 1,
  minHeight: 500,
  border: '1px solid var(--border-color)',
  borderRadius: 8,
  }}
  />
  )}

  {isCSV && isText && !textLoading && textPreview?.content && (
  <CSVTablePreview content={textPreview.content} extension={selectedItem.extension ?? 'csv'} />
  )}

  {isText && !isCSV && (
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
  {textLoading ? 'Loading text preview...' : textPreview?.content ?? ''}
  {textPreview?.truncated && (
  <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
  <Text type="secondary" style={{ fontSize: 12 }}>Content is truncated (showing first 200 KB).</Text>
  <Tooltip title="Download full file">
  <Button
  size="small"
  type="primary"
  icon={<CloudDownloadOutlined />}
  onClick={() => {
  if (fileUrl) {
  const a = document.createElement('a');
  a.href = fileUrl;
  a.download = selectedItem?.name ?? 'download';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  }
  }}
  >
  Download
  </Button>
  </Tooltip>
  </div>
  )}
  </div>
  )}

  {!isImage && !isPDF && !isText && (
  <Empty
  image={Empty.PRESENTED_IMAGE_SIMPLE}
  description="File type is not supported for preview"
  />
  )}
  </div>
  ) : (
  <Empty description="Select a file to preview" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  )}
  </div>
  </div>
  </div>
  );
};

export default ArtifactsPanel;
