import React, { useState } from 'react';
import { Button, Descriptions, Space, Typography } from 'antd';
import { ArrowLeftOutlined, DownloadOutlined, EyeOutlined, StopOutlined } from '@ant-design/icons';
import { pipelinesApi } from '@api/pipelines';
import type { NodeCatalog, PipelineRun } from '@api/pipelines';
import { useLanguageStore } from '@/i18n/language';
import { useT } from '@/i18n/useT';
import { parseServerTimeDayjs } from '@utils/serverTime';
import { pick, specIndex } from './graphModel';
import { StatusTag } from './PipelineStatus';
import PipelineOutputPreview, { type PreviewTarget } from './PipelineOutputPreview';
export { StatusTag } from './PipelineStatus';

const { Text, Paragraph } = Typography;

/** A backend timestamp (zone-less UTC) in local time. */
export const formatServerTime = (value?: string | null): string => parseServerTimeDayjs(value)?.format('YYYY-MM-DD HH:mm:ss') ?? '';

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
  const language = useLanguageStore((state) => state.language);
  const [preview, setPreview] = useState<PreviewTarget | null | undefined>(undefined);
  const specs = specIndex(catalog);
  const active = run.status === 'queued' || run.status === 'running';
  const node = selectedNode ? run.nodes?.[selectedNode] : undefined;
  const graphNode = selectedNode ? run.graph?.nodes[selectedNode] : undefined;
  const spec = specs.get(graphNode?.type || '');

  const reportPort = spec?.kind === 'agent' ? Object.entries(node?.outputs || {}).find(([, output]) => /\.md$/i.test(output.name))?.[0] : undefined;
  const currentPreview = preview === undefined && reportPort ? { port: reportPort, title: node!.outputs[reportPort].name } : preview;

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
                      <Button size="small" icon={<EyeOutlined />} onClick={() => setPreview({ port, title })}>
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
            {currentPreview ? <PipelineOutputPreview
              key={`${run.id}:${selectedNode}:${currentPreview.port}:${currentPreview.path || ''}`}
              runId={run.id}
              nodeId={selectedNode}
              target={currentPreview}
              onOpen={setPreview}
              onClose={() => setPreview(null)}
            /> : null}
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

    </div>
  );
};

export default RunPanel;
