import React, { useState } from 'react';
import { App, Button, Descriptions, Modal, Space, Spin, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, DownloadOutlined, EyeOutlined, StopOutlined } from '@ant-design/icons';
import { pipelinesApi } from '@api/pipelines';
import type { NodeCatalog, OutputPreview, PipelineRun } from '@api/pipelines';
import { MarkdownRenderer } from '@components/chat/MarkdownRenderer';
import { useLanguageStore } from '@/i18n/language';
import { useT } from '@/i18n/useT';
import { parseServerTimeDayjs } from '@utils/serverTime';
import { pick, specIndex } from './graphModel';
import { STATUS_KEYS } from './PipelineNode';

const { Text, Paragraph } = Typography;

const STATUS_COLORS: Record<string, string> = {
  succeeded: 'success',
  cached: 'success',
  failed: 'error',
  running: 'processing',
  queued: 'default',
  pending: 'default',
  skipped: 'warning',
  cancelled: 'warning',
};

/** A backend timestamp (zone-less UTC) in local time. */
export const formatServerTime = (value?: string | null): string => parseServerTimeDayjs(value)?.format('YYYY-MM-DD HH:mm:ss') ?? '';

export const StatusTag: React.FC<{ status?: string | null }> = ({ status }) => {
  const t = useT();
  if (!status) return null;
  return <Tag color={STATUS_COLORS[status] || 'default'}>{t(STATUS_KEYS[status] || 'pipelines.status.pending')}</Tag>;
};

interface PreviewState {
  title: string;
  port: string;
  nodeId: string;
  path?: string;
  loading: boolean;
  data?: OutputPreview;
}

interface Props {
  catalog: NodeCatalog;
  run: PipelineRun;
  selectedNode: string | null;
  onBack: () => void;
  onCancel: () => void;
  cancelling: boolean;
}

const RunPanel: React.FC<Props> = ({ catalog, run, selectedNode, onBack, onCancel, cancelling }) => {
  const t = useT();
  const { message } = App.useApp();
  const language = useLanguageStore((state) => state.language);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const specs = specIndex(catalog);
  const active = run.status === 'queued' || run.status === 'running';
  const node = selectedNode ? run.nodes?.[selectedNode] : undefined;
  const graphNode = selectedNode ? run.graph?.nodes[selectedNode] : undefined;
  const spec = specs.get(graphNode?.type || '');

  const openPreview = async (nodeId: string, port: string, title: string, path?: string) => {
    setPreview({ title, port, nodeId, path, loading: true });
    try {
      const data = await pipelinesApi.preview(run.id, nodeId, port, path);
      setPreview({ title, port, nodeId, path, loading: false, data });
    } catch (error) {
      setPreview(null);
      message.error(t('pipelines.run_view.previewFailed', { error: (error as Error).message }));
    }
  };

  const detail = (node?.detail || {}) as Record<string, unknown>;
  const cachedFrom = detail.cached_from as { run_id?: string } | undefined;

  return (
    <div className="pl-inspector">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Button size="small" icon={<ArrowLeftOutlined />} onClick={onBack}>
          {t('pipelines.run_view.backToEdit')}
        </Button>
        {active ? (
          <Button size="small" danger icon={<StopOutlined />} loading={cancelling} onClick={onCancel}>
            {t('pipelines.cancelRun')}
          </Button>
        ) : null}
      </Space>
      <Typography.Title level={5} style={{ margin: '10px 0 4px' }}>
        {t('pipelines.run_view.title', { id: run.id })}
      </Typography.Title>
      <Space size={6} wrap>
        <StatusTag status={run.status} />
        <Text type="secondary">{t('pipelines.run_view.versionOf', { version: run.version })}</Text>
      </Space>
      <Descriptions size="small" column={1} style={{ marginTop: 8 }}>
        <Descriptions.Item label={t('pipelines.run_view.started')}>{formatServerTime(run.started_at || run.created_at)}</Descriptions.Item>
        {run.finished_at ? <Descriptions.Item label={t('pipelines.run_view.finished')}>{formatServerTime(run.finished_at)}</Descriptions.Item> : null}
        {run.error ? <Descriptions.Item label={t('pipelines.run_view.error')}>{run.error}</Descriptions.Item> : null}
      </Descriptions>

      <div className="pl-run-node">
        {!node || !spec || !selectedNode ? (
          <Paragraph type="secondary" className="pl-hint">
            {t('pipelines.run_view.selectNode')}
          </Paragraph>
        ) : (
          <>
            <Space size={6} wrap>
              <Text strong>{pick(spec.title, language)}</Text>
              <StatusTag status={node.status} />
            </Space>
            {graphNode?.label ? (
              <Text type="secondary" className="pl-hint">
                {graphNode.label}
              </Text>
            ) : null}
            {node.error ? <div className="pl-issue-box">{node.error}</div> : null}
            {cachedFrom ? (
              <Paragraph type="secondary" className="pl-hint">
                {t('pipelines.run_view.cachedFrom')}
              </Paragraph>
            ) : null}
            <Space size={10} wrap className="pl-hint">
              {detail.slurm_job_id ? <Text type="secondary">{t('pipelines.run_view.job', { id: String(detail.slurm_job_id) })}</Text> : null}
              {typeof detail.duration_seconds === 'number' ? (
                <Text type="secondary">{t('pipelines.run_view.duration', { seconds: Math.round(detail.duration_seconds as number) })}</Text>
              ) : null}
            </Space>
            <div style={{ marginTop: 8 }}>
              <Text strong>{t('pipelines.run_view.outputs')}</Text>
            </div>
            {Object.keys(node.outputs || {}).length === 0 ? (
              <Paragraph type="secondary" className="pl-hint">
                {t('pipelines.run_view.noOutputs')}
              </Paragraph>
            ) : (
              Object.entries(node.outputs).map(([port, output]) => {
                const portSpec = spec.outputs.find((item) => item.name === port);
                const title = portSpec ? pick(portSpec.title, language) : port;
                const isFile = output.kind === 'file';
                return (
                  <div key={port} className="pl-output-row">
                    <div className="pl-output-name">
                      <span>{title}</span>
                      <Text type="secondary" className="pl-hint">
                        {output.name}
                      </Text>
                    </div>
                    <Space size={4}>
                      <Button size="small" icon={<EyeOutlined />} onClick={() => void openPreview(selectedNode, port, title)}>
                        {t('pipelines.run_view.preview')}
                      </Button>
                      {isFile ? (
                        <Button size="small" icon={<DownloadOutlined />} href={pipelinesApi.downloadUrl(run.id, selectedNode, port)}>
                          {t('pipelines.run_view.download')}
                        </Button>
                      ) : null}
                    </Space>
                  </div>
                );
              })
            )}
            {typeof detail.command === 'string' && detail.command ? (
              <details className="pl-stderr">
                <summary>{t('pipelines.run_view.command')}</summary>
                <pre>{detail.command as string}</pre>
              </details>
            ) : null}
            {typeof detail.stderr_tail === 'string' && detail.stderr_tail ? (
              <details className="pl-stderr">
                <summary>{t('pipelines.run_view.toolMessages')}</summary>
                <pre>{detail.stderr_tail as string}</pre>
              </details>
            ) : null}
          </>
        )}
      </div>

      <Modal open={Boolean(preview)} title={preview?.title} footer={null} width={860} onCancel={() => setPreview(null)} destroyOnHidden>
        {preview?.loading ? <Spin /> : null}
        {preview?.data?.kind === 'text' ? (
          <>
            {preview.data.truncated ? <Paragraph type="secondary">{t('pipelines.run_view.truncated')}</Paragraph> : null}
            {(preview.data.name || '').toLowerCase().endsWith('.md') ? (
              <div className="pl-markdown">
                <MarkdownRenderer content={preview.data.text || ''} />
              </div>
            ) : (
              <pre className="pl-preview">{preview.data.text}</pre>
            )}
          </>
        ) : null}
        {preview?.data?.kind === 'binary' ? <Paragraph type="secondary">{t('pipelines.run_view.binary')}</Paragraph> : null}
        {preview?.data && (preview.data.kind === 'dir' || preview.data.kind === 'prefix') ? (
          <>
            <Paragraph type="secondary">{t('pipelines.run_view.folderFiles', { count: preview.data.entries?.length || 0 })}</Paragraph>
            <div className="pl-preview-list">
              {(preview.data.entries || []).map((entry) => (
                <div key={entry.path} className="pl-output-row">
                  <code>{entry.path}</code>
                  {preview.data?.kind === 'dir' ? (
                    <Space size={4}>
                      <Button
                        size="small"
                        onClick={() => void openPreview(preview.nodeId, preview.port, entry.path, entry.path)}
                      >
                        {t('pipelines.run_view.open')}
                      </Button>
                      <Button
                        size="small"
                        icon={<DownloadOutlined />}
                        href={pipelinesApi.downloadUrl(run.id, preview.nodeId, preview.port, entry.path)}
                      />
                    </Space>
                  ) : null}
                </div>
              ))}
            </div>
          </>
        ) : null}
      </Modal>
    </div>
  );
};

export default RunPanel;
