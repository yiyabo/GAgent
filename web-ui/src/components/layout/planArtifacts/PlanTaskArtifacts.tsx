import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Drawer, Empty, Spin, Tooltip } from 'antd';
import { DownloadOutlined, FileOutlined, HistoryOutlined, ReloadOutlined } from '@ant-design/icons';
import { planTreeApi } from '@api/planTree';
import { artifactVersionsApi } from '@api/artifactVersions';
import { buildArtifactFileUrl, buildDeliverableFileUrl } from '@api/artifacts';
import type { PlanTaskNode } from '@/types';
import { ArtifactPreviewModal } from '../artifactPreview';
import { artifactFile, collectPlanArtifacts, PlanArtifactFile } from './model';
import './planArtifacts.css';

export interface PlanTaskArtifactsProps {
  planId: number;
  sessionId: string | null;
  taskId?: number | null;
  tasks?: PlanTaskNode[];
  artifactSessionId?: string | null;
  onSelectTask?: (taskId: number) => void;
}

const freshnessLabels: Record<string, string> = {
  fresh: '当前', stale: '待更新', unknown: '来源未追踪', reconciling: '同步中',
};

async function loadVersions(planId: number, alias?: string, cursor?: number) {
  const response = alias == null
    ? await artifactVersionsApi.list(planId)
    : await artifactVersionsApi.list(planId, alias, cursor ?? 0);
  if ('plan_id' in response && response.plan_id !== planId) throw new Error('版本清单返回了不同计划的数据');
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
  const href = fileUrl(file);
  return <div className="plan-artifact-row">
    <span className="plan-artifact-icon"><FileOutlined /></span>
    <div className="plan-artifact-info">
      <button className="plan-artifact-name" title={file.name} disabled={!href} onClick={() => onPreview(file)}>{file.name}</button>
      <div className="plan-artifact-meta">
        {historical ? <span>历史版本</span> : <span className={file.freshness === 'stale' ? 'plan-artifact-stale' : undefined}>{freshnessLabels[file.freshness] || '来源未追踪'}</span>}
        {file.validated != null && <span>{file.validated ? '已通过声明检查' : '尚未核验'}</span>}
        {!href && <span>文件来源待确认</span>}
      </div>
      {historical && file.recordedAt && <div className="plan-artifact-meta">{new Date(file.recordedAt * 1000).toLocaleString()}</div>}
    </div>
    <div className="plan-artifact-actions">
      {onHistory && <Tooltip title="版本历史"><Button aria-label={`${file.name} 版本历史`} size="small" type="text" icon={<HistoryOutlined />} onClick={() => onHistory(file)} /></Tooltip>}
      <Tooltip title={href ? '下载' : '无法确定来源会话'}><Button aria-label={`下载 ${file.name}`} size="small" type="text" icon={<DownloadOutlined />} href={href} download={file.name} disabled={!href} /></Tooltip>
    </div>
  </div>;
}

function ArtifactHistory({ planId, file, onClose, onPreview }: {
  planId: number;
  file: PlanArtifactFile;
  onClose: () => void;
  onPreview: (file: PlanArtifactFile) => void;
}) {
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
  return <Drawer title={`${file.name} · 版本历史`} open onClose={onClose} width={460}>
    <p className="plan-artifact-note">保留每次生产的来源与检查记录。历史文件不会替换当前结果。</p>
    {history.isLoading && <Spin aria-label="正在加载版本历史" />}
    {history.error && <Alert type="warning" showIcon message="版本历史加载失败" action={<Button size="small" onClick={() => void history.refetch()}>重试</Button>} />}
    {[...previous, ...page].map(item => <div key={item.key}>
      <ArtifactRow file={item} historical={item.artifactVersionId !== currentVersionId} onPreview={onPreview} />
      <div className="plan-artifact-note">{item.taskId == null ? '历史来源未记录' : `任务 #${item.taskId}`} · {item.artifactVersionId?.slice(0, 12)}</div>
    </div>)}
    {!history.isLoading && !history.error && !previous.length && !page.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此产物尚无不可变版本记录" />}
    {history.data?.next_cursor != null && <Button onClick={() => {
      setPrevious([...previous, ...page]);
      setCursor(history.data!.next_cursor!);
    }}>加载更多版本</Button>}
  </Drawer>;
}

function ScopedPlanArtifacts({ planId, sessionId, artifactSessionId, taskId, tasks = [], onSelectTask }: PlanTaskArtifactsProps) {
  const sourceSession = artifactSessionId || sessionId;
  const [preview, setPreview] = React.useState<PlanArtifactFile | null>(null);
  const [historyFile, setHistoryFile] = React.useState<PlanArtifactFile | null>(null);
  const results = useQuery({
    queryKey: ['plan-workspace-artifact-results', planId, sessionId, sourceSession],
    queryFn: async () => {
      const response = await planTreeApi.getPlanResults(planId);
      if (response.plan_id !== planId) throw new Error('产物返回了不同计划的数据');
      return response;
    },
    staleTime: 15000,
    retry: false,
  });
  const versions = useQuery({
    queryKey: ['plan-workspace-artifact-versions', planId, sessionId, sourceSession],
    queryFn: () => loadVersions(planId),
    // This endpoint may hash large versioned files. Refresh on task transitions
    // or explicit user action, not on a timer while the workspace is idle.
    staleTime: 30000,
    retry: false,
  });
  const taskState = tasks.map(task => `${task.id}:${task.status}:${task.updated_at || ''}:${task.freshness || ''}:${JSON.stringify(task.stale_reasons || [])}`).join('|');
  const previousTaskState = React.useRef(taskState);
  React.useEffect(() => {
    if (previousTaskState.current !== taskState) {
      previousTaskState.current = taskState;
      void results.refetch();
      void versions.refetch();
    }
  }, [taskState, results.refetch, versions.refetch]);
  const files = collectPlanArtifacts(planId, results.data?.items ?? [], versions.data, sourceSession, Boolean(artifactSessionId))
    .filter(file => taskId == null || file.taskId === taskId);
  const groups = new Map<number | null, PlanArtifactFile[]>();
  files.forEach(file => groups.set(file.taskId, [...(groups.get(file.taskId) ?? []), file]));
  const taskMap = new Map(tasks.filter(task => task.plan_id == null || task.plan_id === planId).map(task => [task.id, task]));
  const busy = results.isFetching || versions.isFetching;
  const loading = results.isLoading || versions.isLoading;
  const refresh = () => { void results.refetch(); void versions.refetch(); };
  return <section className="plan-artifacts" aria-label={taskId == null ? '计划产物' : '任务产物'}>
    <div className="plan-artifacts-heading">
      <div><strong>{taskId == null ? '计划产物' : '对应产物'}</strong><span className="plan-artifacts-count">{files.length}</span></div>
      <Button size="small" type="text" icon={<ReloadOutlined spin={busy} />} onClick={refresh} aria-label="刷新产物" />
    </div>
    {loading && !files.length && <div className="plan-artifacts-loading"><Spin size="small" /> 正在读取产物记录</div>}
    {results.error && <Alert type="warning" showIcon message="任务产物记录加载失败" description="已加载的计划清单仍可查看。" />}
    {versions.error && <Alert type="warning" showIcon message="版本与来源暂时不可用" description="已登记的产物仍可查看，版本状态可能不是最新。" />}
    {!loading && !files.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={taskId == null ? '此计划尚未登记产物' : '此任务尚未登记产物'} />}
    {[...groups].map(([producer, entries]) => <div className="plan-artifacts-group" key={producer ?? 'unknown'}>
      {taskId == null && <div className="plan-artifacts-group-heading">
        {producer != null && onSelectTask
          ? <button onClick={() => onSelectTask(producer)}>{taskMap.get(producer)?.name || `任务 #${producer}`}</button>
          : <span>{producer == null ? '任务归属未记录' : taskMap.get(producer)?.name || `任务 #${producer}`}</span>}
        <span>{entries.length} 个文件</span>
      </div>}
      {entries.map(file => <ArtifactRow key={file.key} file={file} onPreview={setPreview} onHistory={versions.data?.schema_version >= 2 && file.artifactVersionId ? setHistoryFile : undefined} />)}
    </div>)}
    {!loading && <p className="plan-artifact-note">按计划与任务的发布记录归属；未登记的会话文件可在“全部文件”查看。</p>}
    {historyFile && <ArtifactHistory key={historyFile.key} planId={planId} file={historyFile} onClose={() => setHistoryFile(null)} onPreview={setPreview} />}
    {preview?.path && <ArtifactPreviewModal key={preview.key} open onClose={() => setPreview(null)} sessionId={preview.sessionId} name={preview.name} path={preview.path} sourceType={preview.sourceType} sourcePath={preview.path} version={preview.version} />}
  </section>;
}

/** Keying the scope also closes preview/history before any new selection can render. */
export function PlanTaskArtifacts(props: PlanTaskArtifactsProps) {
  return <ScopedPlanArtifacts key={`${props.sessionId}:${props.artifactSessionId}:${props.planId}:${props.taskId ?? 'all'}`} {...props} />;
}

export default PlanTaskArtifacts;
