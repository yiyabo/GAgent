import React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Button,
  Empty,
  Modal,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  CloudDownloadOutlined,
  DownloadOutlined,
  LinkOutlined,
  TableOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  artifactsApi,
  buildArtifactFileUrl,
  buildDeliverableFileUrl,
  buildRenderedFileUrl,
} from '@api/artifacts';
import { MarkdownRenderer } from '@components/chat/MarkdownRenderer';

const { Text } = Typography;

export const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg']);
export const CSV_EXTS = new Set(['csv', 'tsv']);
export const PDF_EXTS = new Set(['pdf']);
export const TEXT_EXTS = new Set(['md', 'txt', 'csv', 'tsv', 'json', 'log', 'py', 'r', 'html', 'tex', 'bib']);
// Files that need rendering (LaTeX -> PDF, Markdown -> HTML)
export const RENDERABLE_EXTS = new Set(['tex', 'md', 'docx']);

/* ---- CSV / TSV parsing ---- */

export interface ParsedTable {
  columns: string[];
  rows: string[][];
}

export function parseDelimited(content: string, delimiter: string): ParsedTable {
  const lines = content.trim().split('\n').filter((l) => l.trim());
  if (!lines.length) return { columns: [], rows: [] };
  const parse = (line: string) =>
    line.split(delimiter).map((c) => c.trim().replace(/^"|"$/g, ''));
  return { columns: parse(lines[0]), rows: lines.slice(1).map(parse) };
}

export const CSVTablePreview: React.FC<{ content: string; extension: string }> = ({ content, extension }) => {
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

export const formatSize = (size = 0) => {
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${size} B`;
};

/* ---- Standalone preview modal ---- */

export interface ArtifactPreviewModalProps {
  open: boolean;
  onClose: () => void;
  sessionId: string | null;
  name: string;
  path: string;
  sourcePath?: string;
  sourceType: 'deliverables' | 'raw';
  extension?: string | null;
  version?: string;
}

const monoBlockStyle: React.CSSProperties = {
  flex: 1,
  border: '1px solid var(--border-color)',
  borderRadius: 8,
  padding: 12,
  background: 'var(--bg-tertiary)',
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
  fontSize: 12,
  whiteSpace: 'pre-wrap',
  lineHeight: 1.5,
  overflow: 'auto',
};

export const ArtifactPreviewModal: React.FC<ArtifactPreviewModalProps> = ({
  open,
  onClose,
  sessionId,
  name,
  path,
  sourcePath,
  sourceType,
  extension,
  version,
}) => {
  const [showSource, setShowSource] = React.useState(false);
  React.useEffect(() => {
    if (open) {
      setShowSource(false);
    }
  }, [open, path, sourcePath]);

  const ext = String(extension ?? name.split('.').pop() ?? '').trim().toLowerCase();
  const isImage = IMAGE_EXTS.has(ext);
  const isCSV = CSV_EXTS.has(ext);
  const isPDF = PDF_EXTS.has(ext);
  const isText = TEXT_EXTS.has(ext);
  const isRenderable = RENDERABLE_EXTS.has(ext);

  const sid = typeof sessionId === 'string' && sessionId.trim().length > 0 ? sessionId.trim() : null;
  const rawPath = sourcePath ?? path;
  const fileUrl = sid
    ? sourceType === 'deliverables'
      ? buildDeliverableFileUrl(sid, path, version ? { version } : undefined)
      : buildArtifactFileUrl(sid, rawPath)
    : null;

  // Text preview for non-renderable text files (and as fallback / source view)
  const {
    data: textPreview,
    isLoading: textLoading,
    error: textError,
  } = useQuery({
    queryKey: ['artifacts', 'preview-text', sid, sourceType, rawPath, version ?? null],
    queryFn: () => {
      if (sourceType === 'deliverables') {
        return artifactsApi.getSessionDeliverableText(sid ?? '', path, {
          maxBytes: 200000,
          version,
        });
      }
      return artifactsApi.getSessionArtifactText(sid ?? '', rawPath, { maxBytes: 200000 });
    },
    enabled: Boolean(open && sid && path && isText),
  });

  // Rendered preview for LaTeX and Markdown
  const {
    data: renderedPreview,
    isLoading: renderLoading,
    error: renderError,
  } = useQuery({
    queryKey: ['artifacts', 'preview-render', sid, sourceType, rawPath, version ?? null],
    queryFn: () =>
      artifactsApi.renderArtifact(sid ?? '', sourceType === 'raw' ? rawPath : path, {
        sourceType,
      }),
    enabled: Boolean(open && sid && path && isRenderable),
    retry: 1,
  });

  const handleDownload = () => {
    if (!fileUrl) {
      return;
    }
    const a = document.createElement('a');
    a.href = fileUrl;
    a.download = name || 'download';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  return (
    <Modal
      open={open}
      onCancel={onClose}
      width="80%"
      centered
      title={
        <Space size={8}>
          <Text strong ellipsis style={{ maxWidth: 320 }}>
            {name || 'Preview'}
          </Text>
          {ext && <Tag>{ext}</Tag>}
        </Space>
      }
      footer={
        <Space size={8}>
          {isRenderable && (
            <Button onClick={() => setShowSource(!showSource)}>
              {showSource ? 'Show Rendered' : 'Show Source'}
            </Button>
          )}
          <Tooltip title="Open in new tab">
            <Button
              icon={<LinkOutlined />}
              onClick={() => {
                if (fileUrl) {
                  window.open(fileUrl, '_blank');
                }
              }}
              disabled={!fileUrl}
            />
          </Tooltip>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            onClick={handleDownload}
            disabled={!fileUrl}
          >
            Download
          </Button>
        </Space>
      }
    >
      <div style={{ minHeight: 200, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {!fileUrl && (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="Preview unavailable: no active session"
          />
        )}

        {/* Image Preview */}
        {fileUrl && isImage && (
          <img
            src={fileUrl}
            alt={name}
            style={{
              width: '100%',
              maxWidth: '100%',
              maxHeight: '70vh',
              objectFit: 'contain',
              borderRadius: 8,
              border: '1px solid var(--border-color)',
            }}
          />
        )}

        {/* PDF Preview */}
        {fileUrl && isPDF && (
          <iframe
            src={fileUrl}
            title={name}
            style={{
              width: '100%',
              flex: 1,
              minHeight: 500,
              border: '1px solid var(--border-color)',
              borderRadius: 8,
            }}
          />
        )}

        {/* CSV Table Preview */}
        {isCSV && isText && textLoading && (
          <Empty description="Loading text preview..." image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
        {isCSV && isText && !textLoading && textError && (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={`Failed to load: ${(textError as Error)?.message || 'Unknown error'}`}
          />
        )}
        {isCSV && isText && !textLoading && textPreview?.content && (
          <CSVTablePreview content={textPreview.content} extension={ext || 'csv'} />
        )}

        {/* Text Preview (exclude renderable files) */}
        {isText && !isCSV && !isRenderable && (
          <div style={monoBlockStyle}>
            {textLoading
              ? 'Loading text preview...'
              : textError
              ? `Failed to load: ${(textError as Error)?.message || 'Unknown error'}`
              : textPreview?.content ?? ''}
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
                    onClick={handleDownload}
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
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12, minHeight: 0 }}>
            {showSource && textPreview?.content ? (
              <div style={monoBlockStyle}>{textPreview.content}</div>
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
                  <div style={{ ...monoBlockStyle, marginTop: 16, maxHeight: 300 }}>
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
                title={name}
                style={{
                  width: '100%',
                  flex: 1,
                  minHeight: 500,
                  border: '1px solid var(--border-color)',
                  borderRadius: 8,
                }}
              />
            ) : name.endsWith('.md') && textPreview?.content ? (
              <div style={{ flex: 1, overflow: 'auto', padding: 16, background: '#fff' }}>
                <MarkdownRenderer
                  content={textPreview.content}
                  sessionId={sid}
                  sourceType={sourceType}
                />
              </div>
            ) : renderedPreview?.format === 'html' && renderedPreview?.content ? (
              <iframe
                title={`${name}-rendered-html`}
                srcDoc={renderedPreview.content}
                sandbox=""
                style={{
                  width: '100%',
                  flex: 1,
                  minHeight: 500,
                  border: '1px solid var(--border-color)',
                  borderRadius: 8,
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
        {fileUrl && !isImage && !isPDF && !isText && !isRenderable && (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="File type is not supported for preview"
          >
            <Button
              type="primary"
              icon={<DownloadOutlined />}
              onClick={handleDownload}
            >
              Download
            </Button>
          </Empty>
        )}
      </div>
    </Modal>
  );
};

export default ArtifactPreviewModal;
