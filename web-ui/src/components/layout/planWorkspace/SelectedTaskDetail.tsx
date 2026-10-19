import React, { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Space, Typography } from 'antd';
import { ArrowLeftOutlined, ExportOutlined, PlayCircleOutlined, HistoryOutlined, NodeIndexOutlined } from '@ant-design/icons';
import { planTreeApi } from '@api/planTree';
import { useTasksStore } from '@store/tasks';
import TaskExecuteModal from '@components/tasks/detail/TaskExecuteModal';
import { ExecutionResult } from '@components/tasks/detail/TaskDetailSections';
import { useCallbackScope } from '@components/tasks/detail/useCallbackScope';
import JobLogPanel from '@components/chat/JobLogPanel';
import PlanTaskArtifacts from '../planArtifacts/PlanTaskArtifacts';
import type { BackgroundTaskItem, PlanSyncEventDetail, PlanTaskNode } from '@/types';
import { isActiveJob, selectTaskJob, taskStatus } from './model';
import { useT } from '@/i18n/useT';
import { planStatusLabel } from '@/i18n/planStatusLabels';
import { TaskStatusIcon } from './TaskOutline';

interface Props {
  planId: number; sessionId: string | null; executionBlocked?: boolean; artifactSessionId?: string | null;
  task: PlanTaskNode; tasks: PlanTaskNode[]; jobs: BackgroundTaskItem[];
  onSelectTask: (id: number) => void; onShowHistory: () => void; onBack: () => void; onRefresh: () => void;
}
const SelectedTaskDetail: React.FC<Props> = ({ planId, sessionId, artifactSessionId, task, tasks, jobs, executionBlocked = false,
  onSelectTask, onShowHistory, onBack, onRefresh }) => {
  const t = useT();
  const [executeOpen, setExecuteOpen] = useState(false);
  const [startedJob, setStartedJob] = useState<{ job_id: string; job_type: string; status: string } | null>(null);
  const [decomposing, setDecomposing] = useState(false);
  const [decompositionError, setDecompositionError] = useState<string | null>(null);
  const scope = `${sessionId}:${planId}:${task.id}:${executionBlocked}`;
  const beginRequest = useCallbackScope(scope);
  useEffect(() => { setStartedJob(null); setDecomposing(false); setDecompositionError(null); setExecuteOpen(false); }, [scope]);
  const taskMap = useMemo(() => new Map(tasks.map((item) => [item.id, item])), [tasks]);
  const job = selectTaskJob(jobs, task.id);
  const running = taskStatus(task) === 'running' || Boolean(job && isActiveJob(job)) ||
    Boolean(startedJob && ['running', 'in_progress', 'queued'].includes(startedJob.status));
  const needsDecomposition = task.task_type === 'composite' && taskStatus(task) !== 'completed' &&
    !tasks.some((item) => item.parent_id === task.id);
  useEffect(() => {
    const current = jobs.find((item) => item.job_id === startedJob?.job_id);
    if (current) setStartedJob((previous) => previous ? { ...previous, status: current.status } : previous);
  }, [jobs, startedJob?.job_id]);
  useEffect(() => {
    const completed = (event: Event) => {
      const detail = (event as CustomEvent<PlanSyncEventDetail>).detail;
      if (detail?.type === 'plan_jobs_completed' && detail.plan_id === planId && detail.job_id === startedJob?.job_id) {
        setStartedJob((previous) => previous ? { ...previous, status: detail.status || 'completed' } : previous);
      }
    };
    window.addEventListener('tasksUpdated', completed);
    return () => window.removeEventListener('tasksUpdated', completed);
  }, [planId, startedJob?.job_id]);
  const continueDecomposition = async () => {
    if (running || decomposing || executionBlocked || !needsDecomposition) return;
    const isCurrent = beginRequest();
    setDecomposing(true); setDecompositionError(null);
    try {
      const response = await planTreeApi.decomposeTask(task.id, {
        plan_id: planId, expand_depth: 1, allow_existing_children: false, async_mode: true,
      });
      if (!isCurrent()) return;
      if (!response.success || !response.job?.job_id) throw new Error(response.message || t('planWorkspace.decomposeFailed'));
      setStartedJob({ job_id: response.job.job_id, job_type: 'plan_decompose', status: response.job.status });
      onRefresh();
    } catch (error) {
      if (isCurrent()) setDecompositionError(error instanceof Error ? error.message : t('planWorkspace.decomposeFailed'));
    } finally { if (isCurrent()) setDecomposing(false); }
  };
  const result = useQuery({
    queryKey: ['planTree', 'taskResult', planId, task.id],
    queryFn: () => planTreeApi.getTaskResult(planId, task.id),
    refetchInterval: running ? 8000 : false, refetchOnWindowFocus: false, retry: 1,
  });
  useEffect(() => { void result.refetch(); }, [task.status, task.effective_status, task.freshness, task.updated_at, result.refetch]);
  const openDetails = () => { if (!executionBlocked) useTasksStore.getState().openTaskDrawer(task); };
  const jobId = startedJob?.job_id ?? job?.job_id;
  const dependencies = task.dependencies ?? [];
  return <section className="pw-detail" aria-label={t('planWorkspace.detailAria')}>
    <div className="pw-detail-header">
      <Button className="pw-back-button" type="text" size="small" icon={<ArrowLeftOutlined />} onClick={onBack}>{t('planWorkspace.backToList')}</Button>
      <div className="pw-eyebrow">{t('taskDetail.taskNum', { id: task.id })} <span> / {task.task_type === 'root' ? t('planWorkspace.typeRoot') : task.task_type === 'composite' ? t('planWorkspace.typeComposite') : t('planWorkspace.typeAtomic')}</span></div>
      <h2>{task.short_name || task.name}</h2>
      <div className="pw-detail-toolbar">
        <span className={`pw-status-label pw-status-${taskStatus(task)}`}><TaskStatusIcon status={taskStatus(task)} />{planStatusLabel(t, taskStatus(task))}</span>
        <Space size={6}>
          <Button size="small" icon={<ExportOutlined />} disabled={executionBlocked} title={executionBlocked ? t('planWorkspace.openSourceFirst') : undefined} onClick={openDetails}>{t('planWorkspace.fullDetails')}</Button>
          {needsDecomposition && <Button size="small" icon={<NodeIndexOutlined />} loading={decomposing}
            disabled={running || decomposing || executionBlocked} onClick={() => void continueDecomposition()}>{t('planWorkspace.continueDecompose')}</Button>}
          <Button size="small" type="primary" icon={<PlayCircleOutlined />} disabled={running || decomposing || executionBlocked}
            onClick={() => setExecuteOpen(true)}>
            {task.status === 'failed' ? t('planWorkspace.runAgain') : t('planWorkspace.runTask')}
          </Button>
        </Space>
      </div>
    </div>
    <div className="pw-detail-body">
      {decompositionError && <Alert type="error" showIcon message={decompositionError} />}
      {task.freshness && task.freshness !== 'fresh' && <Alert type={task.freshness === 'stale' ? 'warning' : 'info'} showIcon
        message={planStatusLabel(t, task.freshness)} description={task.stale_reasons?.join(t('common.listSeparator')) || undefined} />}
      {task.status_reason && <Alert type={taskStatus(task) === 'failed' ? 'error' : 'info'} showIcon message={task.status_reason} />}
      <section className="pw-detail-section"><h3>{t('planWorkspace.taskGoal')}</h3><Typography.Paragraph className="pw-instruction" ellipsis={{ rows: 4, expandable: true, symbol: t('planWorkspace.showFullGoal') }}>{task.instruction || task.name}</Typography.Paragraph></section>
      <section className="pw-detail-section"><h3><NodeIndexOutlined /> {t('planWorkspace.inputsDeps')}</h3>
        {dependencies.length ? <div className="pw-dependencies">{dependencies.map((id) => {
          const dependency = taskMap.get(id);
          return <Button key={id} size="small" onClick={() => onSelectTask(id)} disabled={!dependency}
            title={dependency?.name ?? t('taskDetail.taskNum', { id })}>
            {dependency && <TaskStatusIcon status={taskStatus(dependency)} />}{dependency?.short_name || dependency?.name || t('taskDetail.taskNum', { id })}
          </Button>;
        })}</div> : <p className="pw-muted pw-small">{t('planWorkspace.noPrereqs')}</p>}
      </section>
      <section className="pw-detail-section"><div className="pw-section-heading"><h3><HistoryOutlined /> {t('planWorkspace.execution')}</h3>
        <Button type="link" size="small" onClick={onShowHistory}>{t('planWorkspace.planRunHistory')}</Button></div>
        {jobId ? <><p className="pw-muted pw-small">{job?.mode === 'full_plan' ? t('planWorkspace.fullPlanLog') : t('planWorkspace.liveLog')}</p>
          <JobLogPanel key={jobId} defaultExpanded compact jobId={jobId} planId={planId} jobType={startedJob?.job_type ?? job?.job_type ?? 'plan_execute'} targetTaskName={task.name} />
        </> : <div className="pw-quiet-state">{running ? t('planWorkspace.runningNoRecord') : t('planWorkspace.noLinkedRun')}
          <span>{t('planWorkspace.allRunsKept')}</span></div>}
      </section>
      <section className="pw-detail-section"><h3>{t('planWorkspace.taskArtifacts')}</h3>
        <PlanTaskArtifacts planId={planId} sessionId={sessionId} artifactSessionId={artifactSessionId}
          taskId={task.id} tasks={tasks} onSelectTask={onSelectTask} />
      </section>
      {(result.data?.content || result.data?.metadata) && <section className="pw-detail-section"><h3>{t('planWorkspace.executionResult')}</h3>
        <ExecutionResult taskResult={result.data} cachedResult={undefined} resultLoading={result.isFetching}
          sessionId={artifactSessionId || sessionId} />
        <Button type="link" size="small" disabled={executionBlocked} onClick={openDetails}>{t('planWorkspace.viewExecutionDetails')}</Button>
      </section>}
      {result.isError && <Alert type="warning" showIcon message={t('planWorkspace.resultUnavailable')} description={t('planWorkspace.resultUnavailableDesc')} />}
    </div>
    {!executionBlocked && <TaskExecuteModal open={executeOpen} onClose={() => setExecuteOpen(false)} currentPlanId={planId} selectedTaskId={task.id}
      currentSessionId={sessionId} activeTask={task} isTaskDrawerOpen taskMap={taskMap}
      handleDependencyClick={(id) => { setExecuteOpen(false); onSelectTask(id); }}
      refetchPlanTasks={onRefresh} refetchTaskResult={() => { void result.refetch(); }}
      onExecutionStarted={(id) => { setStartedJob({ job_id: id, job_type: 'plan_execute', status: 'queued' }); onRefresh(); }} />}
  </section>;
};
export default SelectedTaskDetail;
