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
import { isActiveJob, selectTaskJob, statusLabel, taskStatus } from './model';
import { TaskStatusIcon } from './TaskOutline';

interface Props {
  planId: number; sessionId: string | null; executionBlocked?: boolean; artifactSessionId?: string | null;
  task: PlanTaskNode; tasks: PlanTaskNode[]; jobs: BackgroundTaskItem[];
  onSelectTask: (id: number) => void; onShowHistory: () => void; onBack: () => void; onRefresh: () => void;
}
const SelectedTaskDetail: React.FC<Props> = ({ planId, sessionId, artifactSessionId, task, tasks, jobs, executionBlocked = false,
  onSelectTask, onShowHistory, onBack, onRefresh }) => {
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
      if (!response.success || !response.job?.job_id) throw new Error(response.message || '阶段拆解未能启动，请重试。');
      setStartedJob({ job_id: response.job.job_id, job_type: 'plan_decompose', status: response.job.status });
      onRefresh();
    } catch (error) {
      if (isCurrent()) setDecompositionError(error instanceof Error ? error.message : '阶段拆解未能启动，请重试。');
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
  return <section className="pw-detail" aria-label="选中任务详情">
    <div className="pw-detail-header">
      <Button className="pw-back-button" type="text" size="small" icon={<ArrowLeftOutlined />} onClick={onBack}>任务列表</Button>
      <div className="pw-eyebrow">任务 #{task.id} <span> / {task.task_type === 'root' ? '计划目标' : task.task_type === 'composite' ? '任务组' : '执行任务'}</span></div>
      <h2>{task.short_name || task.name}</h2>
      <div className="pw-detail-toolbar">
        <span className={`pw-status-label pw-status-${taskStatus(task)}`}><TaskStatusIcon status={taskStatus(task)} />{statusLabel(taskStatus(task))}</span>
        <Space size={6}>
          <Button size="small" icon={<ExportOutlined />} disabled={executionBlocked} title={executionBlocked ? '打开来源会话后可操作' : undefined} onClick={openDetails}>完整详情</Button>
          {needsDecomposition && <Button size="small" icon={<NodeIndexOutlined />} loading={decomposing}
            disabled={running || decomposing || executionBlocked} onClick={() => void continueDecomposition()}>继续拆解</Button>}
          <Button size="small" type="primary" icon={<PlayCircleOutlined />} disabled={running || decomposing || executionBlocked}
            onClick={() => setExecuteOpen(true)}>
            {task.status === 'failed' ? '重新执行' : '执行任务'}
          </Button>
        </Space>
      </div>
    </div>
    <div className="pw-detail-body">
      {decompositionError && <Alert type="error" showIcon message={decompositionError} />}
      {task.freshness && task.freshness !== 'fresh' && <Alert type={task.freshness === 'stale' ? 'warning' : 'info'} showIcon
        message={statusLabel(task.freshness)} description={task.stale_reasons?.join('；') || undefined} />}
      {task.status_reason && <Alert type={taskStatus(task) === 'failed' ? 'error' : 'info'} showIcon message={task.status_reason} />}
      <section className="pw-detail-section"><h3>任务目标</h3><Typography.Paragraph className="pw-instruction" ellipsis={{ rows: 4, expandable: true, symbol: '展开完整目标' }}>{task.instruction || task.name}</Typography.Paragraph></section>
      <section className="pw-detail-section"><h3><NodeIndexOutlined /> 输入与依赖</h3>
        {dependencies.length ? <div className="pw-dependencies">{dependencies.map((id) => {
          const dependency = taskMap.get(id);
          return <Button key={id} size="small" onClick={() => onSelectTask(id)} disabled={!dependency}
            title={dependency?.name ?? `任务 #${id}`}>
            {dependency && <TaskStatusIcon status={taskStatus(dependency)} />}{dependency?.short_name || dependency?.name || `任务 #${id}`}
          </Button>;
        })}</div> : <p className="pw-muted pw-small">没有前置任务依赖</p>}
      </section>
      <section className="pw-detail-section"><div className="pw-section-heading"><h3><HistoryOutlined /> 执行过程</h3>
        <Button type="link" size="small" onClick={onShowHistory}>计划运行记录</Button></div>
        {jobId ? <><p className="pw-muted pw-small">{job?.mode === 'full_plan' ? '以下为包含该任务的计划运行日志。' : '查看关联运行的实时步骤与日志。'}</p>
          <JobLogPanel key={jobId} defaultExpanded compact jobId={jobId} planId={planId} jobType={startedJob?.job_type ?? job?.job_type ?? 'plan_execute'} targetTaskName={task.name} />
        </> : <div className="pw-quiet-state">{running ? '任务正在执行，尚未收到关联运行记录。' : '暂无可关联到此任务的运行记录。'}
          <span>所有计划级执行记录保留在“运行记录”中。</span></div>}
      </section>
      <section className="pw-detail-section"><h3>任务产物</h3>
        <PlanTaskArtifacts planId={planId} sessionId={sessionId} artifactSessionId={artifactSessionId}
          taskId={task.id} tasks={tasks} onSelectTask={onSelectTask} />
      </section>
      {(result.data?.content || result.data?.metadata) && <section className="pw-detail-section"><h3>执行结果</h3>
        <ExecutionResult taskResult={result.data} cachedResult={undefined} resultLoading={result.isFetching}
          sessionId={artifactSessionId || sessionId} />
        <Button type="link" size="small" disabled={executionBlocked} onClick={openDetails}>查看执行详情</Button>
      </section>}
      {result.isError && <Alert type="warning" showIcon message="任务结果暂时无法读取" description="可以刷新计划，或在完整详情中重试。" />}
    </div>
    {!executionBlocked && <TaskExecuteModal open={executeOpen} onClose={() => setExecuteOpen(false)} currentPlanId={planId} selectedTaskId={task.id}
      currentSessionId={sessionId} activeTask={task} isTaskDrawerOpen taskMap={taskMap}
      handleDependencyClick={(id) => { setExecuteOpen(false); onSelectTask(id); }}
      refetchPlanTasks={onRefresh} refetchTaskResult={() => { void result.refetch(); }}
      onExecutionStarted={(id) => { setStartedJob({ job_id: id, job_type: 'plan_execute', status: 'queued' }); onRefresh(); }} />}
  </section>;
};
export default SelectedTaskDetail;
