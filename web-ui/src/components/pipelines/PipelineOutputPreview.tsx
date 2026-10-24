import React, { useEffect, useState } from 'react';
import { Button, Space, Spin, Switch, Typography } from 'antd';
import { ArrowLeftOutlined, CloseOutlined, DownloadOutlined } from '@ant-design/icons';
import { pipelinesApi, type OutputPreview } from '@api/pipelines';
import { MarkdownRenderer } from '@components/chat/MarkdownRenderer';
import { useT } from '@/i18n/useT';

export interface PreviewTarget { port: string; title: string; path?: string }
interface Props {
  runId: string;
  nodeId: string;
  target: PreviewTarget;
  onOpen: (target: PreviewTarget) => void;
  onClose: () => void;
}

const PipelineOutputPreview: React.FC<Props> = ({ runId, nodeId, target, onOpen, onClose }) => {
  const t = useT();
  const [data, setData] = useState<OutputPreview | null>(null);
  const [error, setError] = useState('');
  const [wrap, setWrap] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const { port, path } = target;
  useEffect(() => {
    let disposed = false;
    setData(null);
    setError('');
    setWrap(false);
    pipelinesApi.preview(runId, nodeId, port, path).then(
      (next) => { if (!disposed) setData(next); },
      (reason) => { if (!disposed) setError(reason instanceof Error ? reason.message : String(reason)); },
    );
    return () => { disposed = true; };
  }, [runId, nodeId, port, path, attempt]);

  const markdown = data?.kind === 'text' && /\.(md|markdown)$/i.test(data.name || '');
  const lines = data?.kind === 'text' && /\.(tsv|tab)$/i.test(data.name || '') && data.text
    ? data.text.replace(/\r?\n$/, '').split(/\r?\n/) : null;
  const rows = lines && lines[0].split('\t').length <= 40 ? lines.slice(0, 201).map((line) => line.split('\t')) : null;
  const download = pipelinesApi.downloadUrl(runId, nodeId, port, path);
  return (
    <section className={`pl-output-preview${wrap ? ' pl-wrap-code' : ''}`} aria-label={t('pipelines.panel.previewTitle')} aria-busy={!data && !error}>
      <div className="pl-preview-heading">
        <strong>{target.title}</strong>
        <Space size={4} wrap>
          {path ? <Button size="small" icon={<ArrowLeftOutlined />} onClick={() => onOpen({ port, title: port })}>{t('pipelines.panel.folderBack')}</Button> : null}
          {data?.kind === 'text' || data?.kind === 'binary' ? (
            <Button size="small" icon={<DownloadOutlined />} href={download} aria-label={t('pipelines.run_view.download')} />
          ) : null}
          <Button size="small" icon={<CloseOutlined />} onClick={onClose} aria-label={t('pipelines.panel.closePreview')} />
        </Space>
      </div>
      {!data && !error ? <Spin /> : null}
      {error ? <div role="alert" className="pl-issue-box">
        {t('pipelines.run_view.previewFailed', { error })}
        <Button size="small" onClick={() => setAttempt((n) => n + 1)}>{t('common.retry')}</Button>
      </div> : null}
      {data?.truncated || (lines && lines.length > 201) ? <Typography.Paragraph type="secondary">{t('pipelines.run_view.truncated')}</Typography.Paragraph> : null}
      {data?.kind === 'text' ? (
        <>
          <label className="pl-code-toggle">
            <Switch size="small" checked={wrap} onChange={setWrap} aria-label={t('pipelines.panel.wrap')} /> {t('pipelines.panel.wrap')}
          </label>
          {markdown ? <div className="pl-markdown"><MarkdownRenderer content={data.text || ''} /></div> : rows ? (
            <div className="pl-table-scroll" tabIndex={0} aria-label={t('pipelines.panel.tableScroll')}>
              <table>
                <thead><tr>{rows[0].map((cell, i) => <th key={i}>{cell}</th>)}</tr></thead>
                <tbody>{rows.slice(1).map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>)}</tbody>
              </table>
            </div>
          ) : <pre className="pl-preview">{data.text}</pre>}
        </>
      ) : null}
      {data?.kind === 'binary' ? <Typography.Paragraph type="secondary">{t('pipelines.run_view.binary')}</Typography.Paragraph> : null}
      {data?.kind === 'dir' || data?.kind === 'prefix' ? (
        <>
          <Typography.Paragraph type="secondary">{t('pipelines.run_view.folderFiles', { count: data.entries?.length || 0 })}</Typography.Paragraph>
          <div className="pl-preview-list">
            {(data.entries || []).map((entry) => (
              <div key={entry.path} className="pl-output-row">
                <code className="pl-output-name">{entry.path}</code>
                {data.kind === 'dir' ? <Space size={4} wrap>
                  <Button size="small" onClick={() => onOpen({ port, path: entry.path, title: entry.path })}>{t('pipelines.run_view.open')}</Button>
                  <Button size="small" icon={<DownloadOutlined />} href={pipelinesApi.downloadUrl(runId, nodeId, port, entry.path)} aria-label={t('pipelines.run_view.download')} />
                </Space> : null}
              </div>
            ))}
          </div>
        </>
      ) : null}
    </section>
  );
};

export default PipelineOutputPreview;
