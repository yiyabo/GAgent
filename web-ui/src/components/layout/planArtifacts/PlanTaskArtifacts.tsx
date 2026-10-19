import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Drawer, Empty, Spin, Tooltip } from 'antd';
import { DownloadOutlined, FileOutlined, HistoryOutlined, ReloadOutlined } from '@ant-design/icons';
import { planArtifactsApi, PlanArtifactEntry } from '@api/planArtifacts';
import { artifactVersionsApi } from '@api/artifactVersions';
import { buildArtifactFileUrl, buildDeliverableFileUrl } from '@api/artifacts';
import type { PlanTaskNode } from '@/types';
import { ArtifactPreviewModal } from '../artifactPreview';
import { artifactFile, PlanArtifactFile } from './model';
import { translate, useT } from '@/i18n/useT';
import { planStatusLabel } from '@/i18n/planStatusLabels';
import { dateLocale } from '@/i18n/format';
import './planArtifacts.css';

export interface PlanTaskArtifactsProps {
  planId: number;
  sessionId: string | null;
  taskId?: number | null;
  tasks?: PlanTaskNode[];
  artifactSessionId?: string | null;
  onSelectTask?: (taskId: number) => void;
}

const FRESHNESS = ['fresh', 'stale', 'unknown', 'reconciling'];

async function loadVersions(planId: number, alias?: string, cursor?: number) {
  const response = alias == null
    ? await artifactVersionsApi.list(planId)
    : await artifactVersionsApi.list(planId, alias, cursor ?? 0);
  if ('plan_id' in response && response.plan_id !== planId) throw new Error(translate('planArtifacts.versionListMismatch'));
  return response;
}

function fileUrl(file: PlanArtifactFile): string | undefined {
  if (!file.sessionId || !file.path) return undefined;
  return file.sourceType === 'raw'
    ? buildArtifactFileUrl(file.sessionId, file.path)
    : buildDeliverableFileUrl(file.sessionId, file.path, { version: file.version });
}

function ArtifactRow({ file, onPreview, onHistory, historical = false }: {
  file: PlanArtifactFile;
  onPreview: (file: PlanArtifactFile) => void;
  onHistory?: (file: PlanArtifactFile) => void;
  historical?: boolean;
}) {
  const t = useT();
  const href = fileUrl(file);
  return <div className="plan-artifact-row">
    <span className="plan-artifact-icon"><FileOutlined /></span>
    <div className="plan-artifact-info">
      <button className="plan-artifact-name" title={file.name} disabled={!href} onClick={() => onPreview(file)}>{file.name}</button>
      <div className="plan-artifact-meta">
        {file.outputStatus ? <span>{file.outputStatus === 'accepted' ? t('planArtifacts.acceptedAtExecution') : t('planArtifacts.unfinished', { progress: file.outputProgress || t('planArtifacts.partialOutput') })}</span>
          : historical ? <span>{t('planArtifacts.earlierVersion')}</span> : <span className={file.freshness === 'stale' ? 'plan-artifact-stale' : undefined}>{file.recovered ? t('planArtifacts.restored') : planStatusLabel(t, FRESHNESS.includes(file.freshness) ? file.freshness : 'unknown')}</span>}
        {!file.outputStatus && (file.contentStatus === 'unchecked' ? <span>{t('planArtifacts.checkedAwaitingReview')}</span> : file.validated != null && <span>{file.validated ? t('planArtifacts.passedDeclared') : t('planArtifacts.notVerified')}</span>)}
        {!href && <span>{t('planArtifacts.sourceUnconfirmed')}</span>}
      </div>
      {historical && file.recordedAt && <div className="plan-artifact-meta">{new Date(file.recordedAt * 1000).toLocaleString(dateLocale())}</div>}
    </div>
    <div className="plan-artifact-actions">
      {onHistory && <Tooltip title={t('planArtifacts.versionHistory')}><Button aria-label={t('planArtifacts.versionHistoryOf', { name: file.name })} size="small" type="text" icon={<HistoryOutlined />} onClick={() => onHistory(file)} /></Tooltip>}
      <Tooltip title={href ? t('preview.download') : t('planArtifacts.sourceUnknown')}><Button aria-label={t('planArtifacts.downloadFile', { name: file.name })} size="small" type="text" icon={<DownloadOutlined />} href={href} download={file.name} disabled={!href} /></Tooltip>
    </div>
  </div>;
}

function ArtifactHistory({ planId, file, onClose, onPreview }: {
  planId: number;
  file: PlanArtifactFile;
  onClose: () => void;
  onPreview: (file: PlanArtifactFile) => void;
}) {
  const t = useT();
  const [cursor, setCursor] = React.useState(0);
  const [previous, setPrevious] = React.useState<PlanArtifactFile[]>([]);
  const history = useQuery({
    queryKey: ['plan-artifact-history', planId, file.sessionId, file.alias, cursor],
    queryFn: () => loadVersions(planId, file.alias, cursor),
    retry: false,
  });
  const page = (history.data?.versions ?? [])
    .filter(item => item.alias === file.alias)
    .map(item => {
      const current = history.data?.current_artifacts[item.alias];
      const record = current?.artifact_version_id === item.artifact_version_id ? { ...item, freshness: current.freshness } : item;
      return artifactFile(item.alias, record as unknown as Record<string, unknown>, item.producer_task_id ?? null, file.sessionId);
    });
  const currentVersionId = history.data?.current_artifacts[file.alias]?.artifact_version_id;
  return <Drawer title={t('planArtifacts.historyTitle', { name: file.name })} open onClose={onClose} width={460}>
    <p className="plan-artifact-note">{t('planArtifacts.historyNote')}</p>
    {history.isLoading && <Spin aria-label={t('planArtifacts.loadingHistory')} />}
    {history.error && <Alert type="warning" showIcon message={t('planArtifacts.historyFailed')} action={<Button size="small" onClick={() => void history.refetch()}>{t('common.retry')}</Button>} />}
    {[...previous, ...page].map(item => <div key={item.key}>
      <ArtifactRow file={item} historical={item.artifactVersionId !== currentVersionId} onPreview={onPreview} />
      <div className="plan-artifact-note">{item.taskId == null ? t('planArtifacts.historySourceMissing') : t('taskDetail.taskNum', { id: item.taskId })} · {item.artifactVersionId?.slice(0, 12)}</div>
    </div>)}
    {!history.isLoading && !history.error && !previous.length && !page.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('planArtifacts.noImmutable')} />}
    {history.data?.next_cursor != null && <Button onClick={() => {
      setPrevious([...previous, ...page]);
      setCursor(history.data!.next_cursor!);
    }}>{t('planArtifacts.loadMoreVersions')}</Button>}
  </Drawer>;
}

function ScopedPlanArtifacts({ planId, sessionId, artifactSessionId, taskId, tasks = [], onSelectTask }: PlanTaskArtifactsProps) {
  const t = useT();
  const sourceSession = artifactSessionId || sessionId;
  const [preview, setPreview] = React.useState<PlanArtifactFile | null>(null);
  const [historyFile, setHistoryFile] = React.useState<PlanArtifactFile | null>(null);
  const [cursor, setCursor] = React.useState<string | null>(null);
  const [previous, setPrevious] = React.useState<PlanArtifactEntry[]>([]);
  const results = useQuery({
    queryKey: ['plan-artifact-results', planId, sessionId, sourceSession, taskId, cursor],
    queryFn: async () => {
      const response = await planArtifactsApi.list(planId, taskId, cursor);
      if (response.plan_id !== planId) throw new Error(translate('planArtifacts.resultsMismatch'));
      return response;
    },
    staleTime: 15000,
    refetchInterval: tasks.some(task => (taskId == null || task.id === taskId) && task.status === 'running') ? 12000 : false,
    retry: false,
  });
  const taskState = tasks.map(task => `${task.id}:${task.status}:${task.updated_at || ''}:${task.freshness || ''}:${JSON.stringify(task.stale_reasons || [])}`).join('|');
  const previousTaskState = React.useRef(taskState);
  React.useEffect(() => {
    if (previousTaskState.current !== taskState) {
      previousTaskState.current = taskState;
      setPrevious([]);
      if (cursor != null) setCursor(null); else void results.refetch();
    }
  }, [taskState, results.refetch, cursor]);
  const files = [...previous, ...(results.data?.items ?? [])].map(entry =>
    artifactFile(entry.alias, entry, entry.producer_task_id, results.data?.session_id || sourceSession,
      Boolean(results.data?.session_id || artifactSessionId))).filter(file => taskId == null || file.taskId === taskId);
  const declaredFiles = (results.data?.declared_outputs ?? []).map(entry =>
    artifactFile(entry.alias, entry, entry.producer_task_id, entry.session_id || sourceSession,
      true)).filter(file => taskId == null || file.taskId === taskId);
  const groups = new Map<number | null, PlanArtifactFile[]>();
  files.forEach(file => groups.set(file.taskId, [...(groups.get(file.taskId) ?? []), file]));
  const declaredGroups = new Map<number | null, PlanArtifactFile[]>();
  declaredFiles.forEach(file => declaredGroups.set(file.taskId, [...(declaredGroups.get(file.taskId) ?? []), file]));
  const taskMap = new Map(tasks.filter(task => task.plan_id == null || task.plan_id === planId).map(task => [task.id, task]));
  const busy = results.isFetching;
  const loading = results.isLoading;
  const refresh = () => { setPrevious([]); if (cursor != null) setCursor(null); else void results.refetch(); };
  const countLabel = results.error ? (files.length ? t('planArtifacts.loaded', { count: files.length }) : t('planArtifacts.countUnknown'))
    : results.data ? t('planArtifacts.matching', { count: results.data.total + declaredFiles.length, plus: results.data.declared_outputs_truncated ? '+' : '' }) : t('planArtifacts.reading');
  return <section className="plan-artifacts" aria-label={taskId == null ? t('planArtifacts.planAria') : t('planArtifacts.taskAria')}>
    <div className="plan-artifacts-heading">
      <div><strong>{taskId == null ? t('planArtifacts.planAria') : t('planArtifacts.matchingTitle')}</strong><span className="plan-artifacts-count">{countLabel}</span></div>
      <Button size="small" type="text" icon={<ReloadOutlined spin={busy} />} onClick={refresh} aria-label={t('planArtifacts.refresh')} />
    </div>
    {loading && !files.length && <div className="plan-artifacts-loading"><Spin size="small" /> {t('planArtifacts.readingRecords')}</div>}
    {results.error && <Alert type="warning" showIcon message={t('planArtifacts.recordsFailed')} description={files.length ? t('planArtifacts.partialDesc') : t('planArtifacts.unknownDesc')} action={<Button size="small" onClick={refresh}>{t('common.retry')}</Button>} />}
    {!loading && !results.error && !files.length && !declaredFiles.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('planArtifacts.empty')} />}
    {[...groups].map(([producer, entries]) => <div className="plan-artifacts-group" key={producer ?? 'unknown'}>
      {taskId == null && <div className="plan-artifacts-group-heading">
        {producer != null && onSelectTask
          ? <button onClick={() => onSelectTask(producer)}>{taskMap.get(producer)?.name || t('taskDetail.taskNum', { id: producer })}</button>
          : <span>{producer == null ? t('planArtifacts.producerMissing') : taskMap.get(producer)?.name || t('taskDetail.taskNum', { id: producer })}</span>}
        <span>{t('planArtifacts.fileCount', { count: entries.length })}</span>
      </div>}
      {entries.map(file => <ArtifactRow key={file.key} file={file} onPreview={setPreview} onHistory={file.artifactVersionId ? setHistoryFile : undefined} />)}
    </div>)}
    {declaredFiles.length > 0 && <div className="plan-artifacts-group">
      <div className="plan-artifacts-group-heading"><strong>{t('planArtifacts.declared')}</strong><span>{t('planArtifacts.declaredFiles', { count: declaredFiles.length, plus: results.data?.declared_outputs_truncated ? '+' : '' })}</span></div>
      <p className="plan-artifact-note">{t('planArtifacts.declaredNote')}</p>
      {[...declaredGroups].map(([producer, entries]) => <div key={producer ?? 'unknown'}>
        {taskId == null && <div className="plan-artifacts-group-heading">
          {producer != null && onSelectTask
            ? <button onClick={() => onSelectTask(producer)}>{taskMap.get(producer)?.name || t('taskDetail.taskNum', { id: producer })}</button>
            : <span>{producer == null ? t('planArtifacts.producerMissing') : taskMap.get(producer)?.name || t('taskDetail.taskNum', { id: producer })}</span>}
        </div>}
        {entries.map(file => <ArtifactRow key={file.key} file={file} onPreview={setPreview} />)}
      </div>)}
    </div>}
    {results.data?.next_cursor && <Button loading={busy} onClick={() => {
      setPrevious([...previous, ...results.data!.items]); setCursor(results.data!.next_cursor);
    }}>{t('planArtifacts.loadMore')}</Button>}
    {historyFile && <ArtifactHistory key={historyFile.key} planId={planId} file={historyFile} onClose={() => setHistoryFile(null)} onPreview={setPreview} />}
    {preview?.path && <ArtifactPreviewModal key={preview.key} open onClose={() => setPreview(null)} sessionId={preview.sessionId} name={preview.name} path={preview.path} sourceType={preview.sourceType} sourcePath={preview.path} version={preview.version} />}
  </section>;
}

/** Keying the scope also closes preview/history before any new selection can render. */
export function PlanTaskArtifacts(props: PlanTaskArtifactsProps) {
  return <ScopedPlanArtifacts key={`${props.sessionId}:${props.artifactSessionId}:${props.planId}:${props.taskId ?? 'all'}`} {...props} />;
}

export default PlanTaskArtifacts;
