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
  CloseOutlined,
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
  buildRenderedFileUrl,
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
// Files that need rendering (LaTeX -> PDF, Markdown -> HTML)
const RENDERABLE_EXTS = new Set(['tex', 'md']);

function getReleaseStatePresentation(releaseState?: string): {
  color: string;
  label: string;
  icon: React.ReactNode;
} | null {
  const normalized = String(releaseState ?? '').trim().toLowerCase();
  if (!normalized) return null;
  if (normalized === 'draft') {
    return { color: 'orange', label: 'Draft', icon: <EditOutlined /> };
  }
  if (normalized === 'final') {
    return { color: 'green', label: 'Final', icon: <CheckCircleOutlined /> };
  }
  if (normalized === 'blocked') {
    return { color: 'red', label: 'Blocked', icon: <StopOutlined /> };
  }
  return { color: 'blue', label: normalized.replace(/_/g, ' '), icon: <FileOutlined /> };
}

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
  const [previewOpen, setPreviewOpen] = React.useState(false);
  const [showSource, setShowSource] = React.useState(false); // Toggle for renderable files

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

  const handleSelectFile = (path: string) => {
    setSelectedPath(path);
    setPreviewOpen(true);
    setShowSource(false); // Reset to rendered view when selecting new file
  };

  const handleClosePreview = () => {
    setPreviewOpen(false);
    setSelectedPath(null);
  };

  const isImage = selectedItem?.extension ? IMAGE_EXTS.has(selectedItem.extension) : false;
  const isCSV = selectedItem?.extension ? CSV_EXTS.has(selectedItem.extension) : false;
  const isPDF = selectedItem?.extension ? PDF_EXTS.has(selectedItem.extension) : false;
  const isText = selectedItem?.extension ? TEXT_EXTS.has(selectedItem.extension) : false;
  const isRenderable = selectedItem?.extension ? RENDERABLE_EXTS.has(selectedItem.extension) : false;
  const isFocusedPreview = previewOpen && Boolean(selectedItem);

  // Text preview for non-renderable text files (and as fallback for renderable files)
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
    enabled: Boolean(sessionId && selectedItem?.path && isText && previewOpen),
  });

  // Rendered preview for LaTeX and Markdown
  const { 
    data: renderedPreview, 
    isLoading: renderLoading,
    error: renderError,
  } = useQuery({
    queryKey: ['artifacts', 'render', sessionId, selectedItem?.sourceType, selectedItem?.path],
    queryFn: async () => {
      if (!selectedItem?.path) {
        throw new Error('filepath');
      }
      return artifactsApi.renderArtifact(sessionId ?? '', selectedItem.path, {
        sourceType: selectedItem.sourceType,
      });
    },
    enabled: Boolean(sessionId && selectedItem?.path && isRenderable && previewOpen),
    retry: 1,
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

  const fileUrl = selectedItem
    ? selectedItem.sourceType === 'deliverables'
      ? buildDeliverableFileUrl(sessionId!, selectedItem.path)
      : buildArtifactFileUrl(sessionId!, selectedItem.path)
    : null;

  const handleRefetch = () => {
    if (mode === 'deliverables') {
      void refetchDeliverables();
      return;
    }
    void refetchRaw();
  };

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
  const releaseSummary = String(deliverableData?.release_summary ?? '').trim();
  const releaseStateMeta = getReleaseStatePresentation(deliverableData?.release_state);
  const showPaperProgress = paperCompleted > 0 && paperTotal > 0;

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', position: 'relative', minHeight: 0 }}>
      {/* Header */}
      <div
        style={{
          padding: isFocusedPreview ? '0 16px' : '12px 16px',
          borderBottom: isFocusedPreview ? '1px solid transparent' : '1px solid var(--border-color)',
          maxHeight: isFocusedPreview ? 0 : 180,
          opacity: isFocusedPreview ? 0 : 1,
          transform: isFocusedPreview ? 'translateY(-8px)' : 'translateY(0)',
          overflow: 'hidden',
          pointerEvents: isFocusedPreview ? 'none' : 'auto',
          transition: 'max-height 320ms cubic-bezier(0.22, 1, 0.36, 1), opacity 240ms ease, transform 320ms cubic-bezier(0.22, 1, 0.36, 1), padding 240ms ease, border-color 240ms ease',
        }}
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Space direction="vertical" size={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {mode === 'deliverables' ? 'Deliverables' : 'Raw Files'}
              </Text>
              {mode === 'deliverables' && (
                <>
                  <Space size={8} wrap>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {deliverableData?.count ?? 0} files
                      {showPaperProgress ? ` · Paper ${paperCompleted}/${paperTotal}` : ''}
                    </Text>
                    {releaseStateMeta && (
                      <Tag icon={releaseStateMeta.icon} color={releaseStateMeta.color} style={{ marginInlineEnd: 0 }}>
                        {releaseStateMeta.label}
                      </Tag>
                    )}
                  </Space>
                  {releaseSummary && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {releaseSummary}
                    </Text>
                  )}
                </>
              )}
            </Space>
            <Space size={6}>
              <Tooltip title={dagSidebarFullscreen ? 'Exit Fullscreen' : 'Fullscreen'}>
                <Button
                  size="small"
                  icon={dagSidebarFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
                  onClick={toggleDagSidebarFullscreen}
                />
              </Tooltip>
              <Button size="small" icon={<ReloadOutlined />} onClick={handleRefetch} loading={isFetching}>
                Refresh
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
              setPreviewOpen(false);
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

      {/* Main Content Area - Horizontal layout with file list and preview */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
        {/* File List */}
        <div style={{ 
          flex: previewOpen ? '0 0 0%' : '1 1 100%',
          minWidth: previewOpen ? 0 : undefined,
          maxWidth: isFocusedPreview ? 0 : '100%',
          opacity: isFocusedPreview ? 0 : 1,
          transform: isFocusedPreview ? 'translateX(-12px)' : 'translateX(0)',
          pointerEvents: isFocusedPreview ? 'none' : 'auto',
          overflow: 'auto', 
          padding: isFocusedPreview ? 0 : '8px 0',
          transition: 'flex 320ms cubic-bezier(0.22, 1, 0.36, 1), min-width 320ms cubic-bezier(0.22, 1, 0.36, 1), max-width 320ms cubic-bezier(0.22, 1, 0.36, 1), opacity 220ms ease, transform 320ms cubic-bezier(0.22, 1, 0.36, 1), padding 220ms ease',
        }}>
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
                  handleSelectFile(node.key);
                }
              }}
              defaultExpandAll
              style={{ padding: '0 8px 16px' }}
            />
          ) : (
            <div style={{ padding: 16 }}>
              <Empty description="No files" />
            </div>
          )}
        </div>

        {/* Preview Panel */}
        <div
          style={{
            flex: previewOpen ? '1 1 100%' : '0 0 0%',
            minWidth: previewOpen ? '100%' : 0,
            opacity: previewOpen ? 1 : 0,
            transform: previewOpen ? 'translateX(0)' : 'translateX(14px)',
            overflow: 'hidden',
            background: 'var(--bg-primary)',
            borderLeft: isFocusedPreview ? 'none' : '1px solid var(--border-color)',
            boxShadow: isFocusedPreview ? 'none' : '-4px 0 16px rgba(0, 0, 0, 0.08)',
            transition: 'flex 320ms cubic-bezier(0.22, 1, 0.36, 1), min-width 320ms cubic-bezier(0.22, 1, 0.36, 1), opacity 220ms ease, transform 320ms cubic-bezier(0.22, 1, 0.36, 1), box-shadow 220ms ease, border-left-color 220ms ease',
            display: 'flex',
            flexDirection: 'column',
            minHeight: 0,
          }}>
        {/* Preview Header */}
        <div
          style={{
            padding: '12px 16px',
            borderBottom: '1px solid var(--border-color)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: 'var(--bg-secondary)',
          }}
        >
          <Space size={8} wrap style={{ flex: 1, minWidth: 0 }}>
            <Text strong ellipsis style={{ maxWidth: 200 }}>
              {selectedItem?.name || 'Preview'}
            </Text>
            {selectedItem?.extension && <Tag>{selectedItem.extension}</Tag>}
            {selectedItem?.size !== undefined && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {formatSize(selectedItem.size)}
              </Text>
            )}
          </Space>
          <Space size={4}>
            {isRenderable && (
              <Button
                size="small"
                onClick={() => setShowSource(!showSource)}
              >
                {showSource ? 'Show Rendered' : 'Show Source'}
              </Button>
            )}
            <Tooltip title="Open in new tab">
              <Button
                size="small"
                icon={<LinkOutlined />}
                onClick={() => {
                  if (fileUrl) {
                    window.open(fileUrl, '_blank');
                  }
                }}
                disabled={!fileUrl}
              />
            </Tooltip>
            <Tooltip title="Close">
              <Button
                size="small"
                icon={<CloseOutlined />}
                onClick={handleClosePreview}
              />
            </Tooltip>
          </Space>
        </div>

        {/* Preview Content */}
        <div
          style={{
            flex: 1,
            overflow: isFocusedPreview ? 'hidden' : 'auto',
            padding: isFocusedPreview ? 0 : 16,
            minHeight: 0,
            transition: 'padding 220ms ease',
          }}
        >
          {selectedItem ? (
            <div
              style={{
                width: '100%',
                minHeight: isFocusedPreview ? 0 : '100%',
                height: isFocusedPreview ? '100%' : undefined,
                display: 'flex',
                flexDirection: 'column',
                gap: isFocusedPreview ? 0 : 12,
              }}
            >
              {/* Status Tags */}
              {!isFocusedPreview && (<Space size={8} wrap>
                {selectedItem.sourceType === 'deliverables' && selectedItem.module && (
                  <Tag color="blue">{formatModuleName(selectedItem.module)}</Tag>
                )}
                {selectedItem.sourceType === 'deliverables' && selectedItem.status === 'draft' && (
                  <Tag icon={<EditOutlined />} color="orange">Draft</Tag>
                )}
                {selectedItem.sourceType === 'deliverables' && selectedItem.status === 'final' && (
                  <Tag icon={<CheckCircleOutlined />} color="green">Final</Tag>
                )}
                {selectedItem.sourceType === 'deliverables' && selectedItem.status === 'superseded' && (
                  <Tag icon={<StopOutlined />} color="default">Superseded</Tag>
                )}
              </Space>)}

              {/* Image Preview */}
              {isImage && fileUrl && (
                <img
                  src={fileUrl}
                  alt={selectedItem.name}
                  style={{
                    width: '100%',
                    maxWidth: '100%',
                    maxHeight: isFocusedPreview ? '100%' : undefined,
                    flex: isFocusedPreview ? 1 : undefined,
                    objectFit: isFocusedPreview ? 'contain' : undefined,
                    borderRadius: isFocusedPreview ? 0 : 8,
                    border: isFocusedPreview ? 'none' : '1px solid var(--border-color)',
                  }}
                />
              )}

              {/* PDF Preview */}
              {isPDF && fileUrl && (
                <iframe
                  src={fileUrl}
                  title={selectedItem.name}
                  style={{
                    width: '100%',
                    flex: 1,
                    minHeight: isFocusedPreview ? 0 : 500,
                    border: isFocusedPreview ? 'none' : '1px solid var(--border-color)',
                    borderRadius: isFocusedPreview ? 0 : 8,
                  }}
                />
              )}

              {/* CSV Table Preview */}
              {isCSV && isText && !textLoading && textPreview?.content && (
                <CSVTablePreview content={textPreview.content} extension={selectedItem.extension ?? 'csv'} />
              )}

              {/* Text Preview (exclude renderable files) */}
              {isText && !isCSV && !isRenderable && (
                <div
                  style={{
                    flex: 1,
                    border: '1px solid var(--border-color)',
                    borderRadius: isFocusedPreview ? 0 : 8,
                    padding: isFocusedPreview ? 16 : 12,
                    background: isFocusedPreview ? 'var(--bg-primary)' : 'var(--bg-tertiary)',
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                    fontSize: 12,
                    whiteSpace: 'pre-wrap',
                    lineHeight: 1.5,
                    overflow: 'auto',
                  }}
                >
                  {textLoading ? 'Loading text preview...' : textPreview?.content ?? ''}
                  {textPreview?.truncated && (
                    <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        Content is truncated (showing first 200 KB).
                      </Text>
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

              {/* Rendered Preview (LaTeX -> PDF, Markdown -> HTML) */}
              {isRenderable && (
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: isFocusedPreview ? 0 : 12, minHeight: 0 }}>
                  {showSource && textPreview?.content ? (
                    <div
                      style={{
                        flex: 1,
                        border: isFocusedPreview ? 'none' : '1px solid var(--border-color)',
                        borderRadius: isFocusedPreview ? 0 : 8,
                        padding: isFocusedPreview ? 16 : 12,
                        background: isFocusedPreview ? 'var(--bg-primary)' : 'var(--bg-tertiary)',
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                        fontSize: 12,
                        whiteSpace: 'pre-wrap',
                        lineHeight: 1.5,
                        overflow: 'auto',
                      }}
                    >
                      {textPreview.content}
                    </div>
                  ) : renderLoading ? (
                    <Empty description="Rendering document..." image={Empty.PRESENTED_IMAGE_SIMPLE} />
                  ) : renderError ? (
                    <div>
                      <Empty
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                        description={
                          <span>
                            Failed to render: {(renderError as Error)?.message || 'Unknown error'}
                          </span>
                        }
                      />
                      {/* Show raw text when rendering fails */}
                      {textPreview?.content && (
                        <div
                          style={{
                            marginTop: 16,
                            border: '1px solid var(--border-color)',
                            borderRadius: 8,
                            padding: 12,
                            background: 'var(--bg-tertiary)',
                            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                            fontSize: 12,
                            whiteSpace: 'pre-wrap',
                            lineHeight: 1.5,
                            maxHeight: 300,
                            overflow: 'auto',
                          }}
                        >
                          <Text type="secondary" style={{ fontSize: 11, display: 'block', marginBottom: 8 }}>
                            Raw source:
                          </Text>
                          {textPreview.content}
                        </div>
                      )}
                    </div>
                  ) : renderedPreview?.format === 'pdf' && renderedPreview?.url ? (
                    <iframe
                      src={buildRenderedFileUrl(renderedPreview.url)}
                      title={selectedItem.name}
                      style={{
                        width: '100%',
                        flex: 1,
                        minHeight: isFocusedPreview ? 0 : 500,
                        border: isFocusedPreview ? 'none' : '1px solid var(--border-color)',
                        borderRadius: isFocusedPreview ? 0 : 8,
                      }}
                    />
                  ) : renderedPreview?.format === 'html' && renderedPreview?.content ? (
                    <iframe
                      title={`${selectedItem.name}-rendered-html`}
                      srcDoc={renderedPreview.content}
                      sandbox="allow-popups allow-popups-to-escape-sandbox"
                      style={{
                        width: '100%',
                        flex: 1,
                        minHeight: isFocusedPreview ? 0 : 500,
                        border: isFocusedPreview ? 'none' : '1px solid var(--border-color)',
                        borderRadius: isFocusedPreview ? 0 : 8,
                        background: '#fff',
                      }}
                    />
                  ) : (
                    <Empty
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description="Failed to render document."
                    />
                  )}
                </div>
              )}

              {/* Unsupported File Type */}
              {!isImage && !isPDF && !isText && !isRenderable && (
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
    </div>
  );
};

export default ArtifactsPanel;
