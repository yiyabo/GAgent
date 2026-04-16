import React, { useEffect, useMemo, useState } from 'react';
import { Typography, Button, Space, Badge, Tooltip, Empty, Tabs, Progress, Tag } from 'antd';
import {
  AppstoreOutlined,
  NodeIndexOutlined,
  FullscreenOutlined,
  FullscreenExitOutlined,
  SettingOutlined,
  ReloadOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
  ExpandOutlined,
} from '@ant-design/icons';
import { usePlanTasks, usePlanTree } from '@hooks/usePlans';
import PlanTreeVisualization from '@components/dag/PlanTreeVisualization';
import DAG3DView from '@components/dag/DAG3DView';
import type { PlanSyncEventDetail, PlanTaskNode } from '@/types';
import { useTasksStore } from '@store/tasks';
import { useChatStore } from '@store/chat';
import { useLayoutStore } from '@store/layout';
import { shouldHandlePlanSyncEvent } from '@utils/planSyncEvents';
import { computePlanDecomposeProgress } from '@utils/jobProgress';
import { planTreeApi } from '@api/planTree';
import ExecutorPanel from './ExecutorPanel';
import ArtifactsPanel from './ArtifactsPanel';
import TerminalPanel from '@components/terminal/TerminalPanel';
import TodoListPanel from '@components/tasks/detail/TodoListPanel';
import { ENV } from '@/config/env';

const { Title, Text } = Typography;

const FINAL_JOB_STATUSES = new Set(['succeeded', 'failed', 'completed']);

const toNumber = (value: unknown): number | null => {
  if (value === null || value === undefined) return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

const formatPlanOrigin = (origin: unknown): { label: string; color: string } | null => {
  const raw = typeof origin === 'string' ? origin.trim().toLowerCase() : '';
  if (!raw) return null;
  if (raw === 'deepthink') return { label: 'DeepThink', color: 'geekblue' };
  if (raw === 'standard') return { label: 'Standard', color: 'default' };
  return { label: raw, color: 'default' };
};

const RUBRIC_THRESHOLD = 80;

const scoreColor = (score: number): string => {
  if (score < RUBRIC_THRESHOLD) return 'error';
  if (score >= 90) return 'success';
  if (score >= 80) return 'processing';
  return 'warning';
};

const DAGSidebar: React.FC = () => {
  const { setCurrentPlan, setTasks, openTaskDrawer, openTaskDrawerById, closeTaskDrawer, selectedTaskId } = useTasksStore((state) => ({
  setCurrentPlan: state.setCurrentPlan,
  setTasks: state.setTasks,
  openTaskDrawer: state.openTaskDrawer,
  openTaskDrawerById: state.openTaskDrawerById,
  closeTaskDrawer: state.closeTaskDrawer,
  selectedTaskId: state.selectedTaskId,
  }));
  const { setChatContext, currentWorkflowId, currentSession, currentPlanId, currentPlanTitle, messages } =
  useChatStore((state) => ({
  setChatContext: state.setChatContext,
  currentWorkflowId: state.currentWorkflowId,
  currentSession: state.currentSession,
  currentPlanId: state.currentPlanId,
  currentPlanTitle: state.currentPlanTitle,
  messages: state.messages,
  }));
  const { dagSidebarFullscreen, toggleDagSidebarFullscreen } = useLayoutStore();
  const [dagVisible, setDagVisible] = useState(true);
  const [rootTaskId, setRootTaskId] = useState<number | null>(null);
  const [selectedPlanTitle, setSelectedPlanTitle] = useState<string | undefined>(
  currentPlanTitle ?? undefined
  );
  const [activeTab, setActiveTab] = useState<string>('plan');

  useEffect(() => {
    const onArtifact = () => { setActiveTab('artifacts'); };
    window.addEventListener('artifactProduced', onArtifact);
    return () => { window.removeEventListener('artifactProduced', onArtifact); };
  }, []);
  const [showFullscreenDAG, setShowFullscreenDAG] = useState(false);
  const [decomposeSnapshot, setDecomposeSnapshot] = useState<Record<string, any> | null>(null);
  const [planTodoListOpen, setPlanTodoListOpen] = useState(false);

  const sessionId = currentSession?.session_id;

  const {
  data: planTasks = [],
  isFetching: planTasksLoading,
  refetch: refetchTasks,
  } = usePlanTasks({ planId: currentPlanId ?? undefined });

  const { data: planTree } = usePlanTree(currentPlanId ?? null);

  const latestDecomposeJob = useMemo(() => {
  if (!currentPlanId || !messages || messages.length === 0) return null;
  for (let idx = messages.length - 1; idx >= 0; idx -= 1) {
  const metadata = messages[idx]?.metadata as Record<string, any> | null | undefined;
  if (!metadata) continue;
  const embeddedDecomposeJob = (metadata.decomposition_job as Record<string, any> | null | undefined) ?? null;
  if (embeddedDecomposeJob) {
  const jobId = (embeddedDecomposeJob.job_id ?? null) as string | null;
  const jobType = (embeddedDecomposeJob.job_type ?? 'plan_decompose') as string | null;
  const planId = (embeddedDecomposeJob.plan_id ?? null) as number | null;
  if (jobId && jobType === 'plan_decompose') {
  if (planId !== null && planId !== currentPlanId) continue;
  return {
  jobId,
  jobType,
  planId,
  initialJob: embeddedDecomposeJob,
  status: embeddedDecomposeJob.status ?? null,
  };
  }
  }
  const actions =
  (Array.isArray(metadata.actions) ? metadata.actions : null) ??
  (Array.isArray(metadata.raw_actions) ? metadata.raw_actions : []);
  for (let actIdx = actions.length - 1; actIdx >= 0; actIdx -= 1) {
  const actionJob = (actions[actIdx] as any)?.details?.decomposition_job as Record<string, any> | undefined;
  if (!actionJob) continue;
  const jobId = (actionJob.job_id ?? null) as string | null;
  const jobType = (actionJob.job_type ?? 'plan_decompose') as string | null;
  const planId = (actionJob.plan_id ?? null) as number | null;
  if (!jobId || jobType !== 'plan_decompose') continue;
  if (planId !== null && planId !== currentPlanId) break;
  return {
  jobId,
  jobType,
  planId,
  initialJob: actionJob,
  status: actionJob.status ?? null,
  };
  }
  const job = (metadata.job as Record<string, any> | null | undefined) ?? null;
  const jobType = (metadata.job_type ?? job?.job_type ?? null) as string | null;
  const jobId = (metadata.job_id ?? job?.job_id ?? null) as string | null;
  const planId = (metadata.plan_id ?? job?.plan_id ?? null) as number | null;
  if (!jobId || jobType !== 'plan_decompose') continue;
  if (planId !== null && planId !== currentPlanId) continue;
  return {
  jobId,
  jobType,
  planId,
  initialJob: job,
  status: metadata.job_status ?? job?.status ?? null,
  };
  }
  return null;
  }, [currentPlanId, messages]);

  useEffect(() => {
  if (!latestDecomposeJob?.jobId) {
  setDecomposeSnapshot(null);
  return;
  }
  setDecomposeSnapshot(latestDecomposeJob.initialJob ?? null);
  let cancelled = false;
  let timer: number | null = null;

  const stopTimer = () => {
  if (timer !== null) {
  window.clearInterval(timer);
  timer = null;
  }
  };

  const fetchStatus = async () => {
  try {
  const snapshot = await planTreeApi.getJobStatus(latestDecomposeJob.jobId);
  if (cancelled) return;
  setDecomposeSnapshot(snapshot);
  if (FINAL_JOB_STATUSES.has(snapshot.status)) {
  stopTimer();
  }
  } catch (error) {
  if (!cancelled) {
  stopTimer();
  }
  }
  };

  fetchStatus();
  timer = window.setInterval(fetchStatus, 5000);

  return () => {
  cancelled = true;
  stopTimer();
  };
  }, [latestDecomposeJob?.jobId]);

  const decomposeProgress = useMemo(() => {
  if (!latestDecomposeJob?.jobId) return null;
  const snapshot = (decomposeSnapshot as Record<string, any> | null) ?? latestDecomposeJob.initialJob ?? null;
  if (!snapshot) return null;
  return computePlanDecomposeProgress(snapshot);
  }, [decomposeSnapshot, latestDecomposeJob]);

  const planEvaluation = useMemo(() => {
  const meta = (planTree?.metadata ?? {}) as Record<string, any>;
  const origin = formatPlanOrigin(meta.plan_origin ?? meta.created_by);
  const evaluation = (meta.plan_evaluation ?? meta.planEvaluation ?? null) as Record<string, any> | null;
  const overall = toNumber(evaluation?.overall_score ?? evaluation?.overallScore ?? evaluation?.rubric_score ?? null);
  const dims = (evaluation?.dimension_scores ?? evaluation?.dimensionScores ?? null) as Record<string, any> | null;
  const dimensionScores: Record<string, number> = {};
  if (dims && typeof dims === 'object') {
  for (const [k, v] of Object.entries(dims)) {
  const num = toNumber(v);
  if (num != null) dimensionScores[k] = num;
  }
  }
  return {
  origin,
  overall,
  dimensionScores,
  };
  }, [planTree]);

  const renderPlanEvaluationBadge = () => {
  if (!currentPlanId) return null;
  const origin = planEvaluation.origin;
  const overall = planEvaluation.overall;
  const dimScores = planEvaluation.dimensionScores;
  const hasDims = Object.keys(dimScores).length > 0;
  const tooltipContent = hasDims ? (
  <div style={{ fontSize: 12, lineHeight: 1.6 }}>
  {Object.entries(dimScores)
  .sort(([a], [b]) => a.localeCompare(b))
  .map(([k, v]) => (
  <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
  <span style={{ opacity: 0.85 }}>{k}</span>
  <span style={{ fontWeight: 600 }}>{Math.round(v)}%</span>
  </div>
  ))}
  </div>
  ) : (
  <div style={{ fontSize: 12, opacity: 0.85 }}>
  No detailed breakdown available yet.
  </div>
  );

  return (
  <div style={{ marginTop: 10 }}>
  <div
  style={{
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 10,
  }}
  >
  <Space size={8} wrap>
  {origin && (
  <Tag color={origin.color} style={{ marginInlineEnd: 0 }}>
  {origin.label}
  </Tag>
  )}
  {overall != null ? (
  <Tooltip title={tooltipContent} placement="bottomLeft">
  <Tag color={scoreColor(overall)} style={{ marginInlineEnd: 0 }}>
  Rubric {Math.round(overall)}%
  {overall < RUBRIC_THRESHOLD ? ' (Below threshold)' : ''}
  </Tag>
  </Tooltip>
  ) : (
  <Tag color="default" style={{ marginInlineEnd: 0 }}>
  Rubric: Not evaluated
  </Tag>
  )}
  </Space>

  {overall != null && (
  <div style={{ minWidth: 110 }}>
  <Progress
  percent={Math.round(overall)}
  size="small"
  showInfo={false}
  strokeColor={
  overall < RUBRIC_THRESHOLD
  ? 'color-mix(in srgb, #EF4444 70%, var(--primary-color))'
  : 'color-mix(in srgb, var(--primary-color) 65%, #34D399)'
  }
  trailColor="color-mix(in srgb, var(--border-color) 45%, transparent)"
  />
  </div>
  )}
  </div>
  </div>
  );
  };

  const renderDecomposeProgress = () => {
  if (!decomposeProgress) return null;
  if (FINAL_JOB_STATUSES.has(decomposeProgress.status)) return null;

  const statusKey = String(decomposeProgress.status || '').toLowerCase();
  const badgeStatus =
  statusKey === 'failed'
  ? 'error'
  : statusKey === 'running'
  ? 'processing'
  : statusKey === 'queued'
  ? 'default'
  : 'processing';
  const statusLabel =
  statusKey === 'running'
  ? 'medium'
  : statusKey === 'queued'
  ? 'queued'
  : statusKey === 'failed'
  ? 'failed'
  : 'medium';

  const percent = decomposeProgress.percent != null ? decomposeProgress.percent : 0;
  const detailParts: string[] = [];
  if (decomposeProgress.consumedBudget != null) {
  detailParts.push(`create ${Math.max(0, Math.round(decomposeProgress.consumedBudget))}`);
  }
  if (decomposeProgress.totalBudget != null && decomposeProgress.totalBudget > 0) {
  detailParts.push(` ${Math.round(decomposeProgress.totalBudget)}`);
  }
  if (decomposeProgress.queueRemaining != null) {
  detailParts.push(`queue ${Math.max(0, Math.round(decomposeProgress.queueRemaining))}`);
  }

  return (
  <div
  style={{
  marginTop: 12,
  padding: '10px 12px',
  borderRadius: 10,
  border: '1px solid var(--border-color)',
  background: 'color-mix(in srgb, var(--bg-tertiary) 78%, transparent)',
  boxShadow: '0 6px 20px rgba(0,0,0,0.06)',
  backdropFilter: 'blur(10px)',
  }}
  >
  <div
  style={{
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 12,
  marginBottom: 8,
  }}
  >
  <Space size={8} align="center">
  <Badge status={badgeStatus as any} />
  <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
  task
  </Text>
  <Text type="secondary" style={{ fontSize: 12 }}>
  {statusLabel}
  </Text>
  </Space>
  {decomposeProgress.percent != null ? (
  <Text type="secondary" style={{ fontSize: 12 }}>
  {percent}%
  </Text>
  ) : (
  <Text type="secondary" style={{ fontSize: 12 }}>
  estimating
  </Text>
  )}
  </div>

  <Progress
  percent={percent}
  size="small"
  showInfo={false}
  strokeColor={{
  '0%': 'color-mix(in srgb, var(--primary-color) 90%, white)',
  '100%': 'color-mix(in srgb, var(--primary-color) 40%, #67C23A)',
  }}
  trailColor="color-mix(in srgb, var(--border-color) 45%, transparent)"
  />

  {detailParts.length > 0 && (
  <div style={{ marginTop: 6 }}>
  <Text type="secondary" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
  {detailParts.join(' · ')}
  </Text>
  </div>
  )}
  </div>
  );
  };


  useEffect(() => {
  const handleTasksUpdated = (event: CustomEvent<PlanSyncEventDetail>) => {
  const detail = event.detail;
  if (
  detail?.type === 'plan_deleted' &&
  detail.plan_id != null &&
  detail.plan_id === (currentPlanId ?? null)
  ) {
  setTasks([]);
  closeTaskDrawer();
  return;
  }
  if (
  !shouldHandlePlanSyncEvent(detail, currentPlanId ?? null, [
  'task_changed',
  'plan_jobs_completed',
  'plan_updated',
  ])
  ) {
  return;
  }
  refetchTasks();
  window.setTimeout(() => {
  refetchTasks();
  }, 800);
  };
  window.addEventListener('tasksUpdated', handleTasksUpdated as EventListener);
  return () => window.removeEventListener('tasksUpdated', handleTasksUpdated as EventListener);
  }, [closeTaskDrawer, currentPlanId, refetchTasks, setTasks]);

  useEffect(() => {
  if (!currentPlanId) return;
  
  const timer1 = window.setTimeout(() => {
  refetchTasks();
  }, 3000);
  
  const timer2 = window.setTimeout(() => {
  refetchTasks();
  }, 8000);
  
  return () => {
  window.clearTimeout(timer1);
  window.clearTimeout(timer2);
  };
  }, [currentPlanId, refetchTasks]);

  useEffect(() => {
  if (!currentPlanId) {
  setPlanTodoListOpen(false);
  }
  }, [currentPlanId]);

  useEffect(() => {
  setTasks(planTasks);
  }, [planTasks, setTasks]);

  useEffect(() => {
  if (planTasks.length > 0) {
  const rootTask = planTasks.find((task) => task.task_type === 'root');
  if (rootTask) {
  if (rootTaskId !== rootTask.id) {
  setRootTaskId(rootTask.id);
  setCurrentPlan(rootTask.name);
  setChatContext({
  planId: currentPlanId ?? undefined,
  planTitle: rootTask.name,
  taskId: rootTask.id,
  taskName: rootTask.name,
  });
  }
  setSelectedPlanTitle(rootTask.name);
  }
  } else if (rootTaskId !== null) {
  setRootTaskId(null);
  setSelectedPlanTitle(undefined);
  setCurrentPlan(null);
  setChatContext({
  planId: null,
  planTitle: null,
  taskId: null,
  taskName: null,
  });
  closeTaskDrawer();
  }
  }, [planTasks, rootTaskId, setCurrentPlan, setChatContext, currentPlanId, closeTaskDrawer]);

  const stats = useMemo(() => {
  if (!planTasks || planTasks.length === 0) {
  return {
  total: 0,
  pending: 0,
  running: 0,
  completed: 0,
  failed: 0,
  };
  }
  return {
  total: planTasks.length,
  pending: planTasks.filter((task) => task.status === 'pending').length,
  running: planTasks.filter((task) => task.status === 'running').length,
  completed: planTasks.filter((task) => task.status === 'completed').length,
  failed: planTasks.filter((task) => task.status === 'failed').length,
  };
  }, [planTasks]);

  const handleRefresh = () => {
  refetchTasks();
  };

  const handlePlanTodoTaskClick = (taskId: number) => {
  const targetTask = planTasks.find((task) => task.id === taskId);
  if (targetTask) {
  openTaskDrawer(targetTask);
  } else {
  openTaskDrawerById(taskId);
  }
  setPlanTodoListOpen(false);
  };

  const renderPlanPanel = () => (
  <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
  <div style={{ padding: '16px', borderBottom: '1px solid var(--border-color)', background: 'var(--bg-primary)' }}>
  <div
  style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}
  >
  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
  <NodeIndexOutlined style={{ color: 'var(--primary-color)', fontSize: 18 }} />
  <Title level={5} style={{ margin: 0, color: 'var(--text-primary)' }}>
  task
  </Title>
  </div>

  <Space size={4}>
  <Button
  size="small"
  icon={<AppstoreOutlined />}
  onClick={() => setPlanTodoListOpen(true)}
  disabled={!currentPlanId}
  >
  Todo List
  </Button>

  <Tooltip title={dagVisible ? '' : ''}>
  <Button
  type="text"
  size="small"
  icon={dagVisible ? <EyeInvisibleOutlined /> : <EyeOutlined />}
  onClick={() => setDagVisible(!dagVisible)}
  />
  </Tooltip>

  <Tooltip title="">
  <Button
  type="text"
  size="small"
  icon={<ExpandOutlined />}
  onClick={() => setShowFullscreenDAG(true)}
  disabled={!planTasks || planTasks.length === 0}
  />
  </Tooltip>

  <Tooltip title="">
  <Button
  type="text"
  size="small"
  icon={dagSidebarFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
  onClick={toggleDagSidebarFullscreen}
  />
  </Tooltip>

  <Tooltip title="">
  <Button type="text" size="small" icon={<SettingOutlined />} />
  </Tooltip>
  </Space>
  </div>

  {renderDecomposeProgress()}

  <Space size={16} wrap>
  <Badge count={stats.total} size="small" offset={[8, -2]}>
  <Text type="secondary" style={{ fontSize: 12 }}>
  task
  </Text>
  </Badge>
  <Badge count={stats.running} size="small" color="blue" offset={[8, -2]}>
  <Text type="secondary" style={{ fontSize: 12 }}>
  medium
  </Text>
  </Badge>
  <Badge count={stats.completed} size="small" color="green" offset={[8, -2]}>
  <Text type="secondary" style={{ fontSize: 12 }}>
  completed
  </Text>
  </Badge>
  {stats.failed > 0 && (
  <Badge count={stats.failed} size="small" color="red" offset={[8, -2]}>
  <Text type="secondary" style={{ fontSize: 12 }}>
  failed
  </Text>
  </Badge>
  )}
  </Space>

  {renderPlanEvaluationBadge()}

  <Space direction="vertical" size={8} style={{ width: '100%', marginTop: 12 }}>
  <Text type="secondary" style={{ fontSize: 11 }}>
  ROOT task:
  </Text>
  <div
  style={{
  padding: '6px 12px',
  background: 'var(--bg-tertiary)',
  border: '1px solid var(--border-color)',
  borderRadius: '6px',
  fontSize: '14px',
  color: selectedPlanTitle ? 'var(--text-primary)' : 'var(--text-tertiary)',
  }}
  >
  {selectedPlanTitle || 'No ROOT task'}
  </div>
  <Text type="secondary" style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>
  Current plan title
  </Text>
  </Space>
  </div>

  {dagVisible && (
  <div style={{ flex: 1, padding: '8px', overflow: 'hidden' }}>
  {planTasks && planTasks.length > 0 ? (
  <PlanTreeVisualization
  tasks={planTasks}
  loading={planTasksLoading}
  onSelectTask={(task) => {
  if (task) {
  openTaskDrawer(task);
  const rootName =
  selectedPlanTitle || planTasks.find((t) => t.task_type === 'root')?.name || null;
  setChatContext({
  planTitle: rootName,
  taskId: task.id,
  taskName: task.name,
  });
  } else {
  closeTaskDrawer();
  setChatContext({ taskId: null, taskName: null });
  }
  }}
  selectedTaskId={selectedTaskId ?? undefined}
  height="100%"
  />
  ) : (
  <Empty
  image={Empty.PRESENTED_IMAGE_SIMPLE}
  description={
  planTasksLoading
  ? 'Loading tasks...'
  : currentWorkflowId || currentSession?.session_id
  ? 'No tasks found in this session'
  : 'Create a workflow to generate tasks'
  }
  />
  )}
  </div>
  )}

  <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border-color)', background: 'var(--bg-tertiary)' }}>
  <Space size={8} wrap style={{ width: '100%', justifyContent: 'center' }}>
  <Button size="small" icon={<ReloadOutlined />} onClick={handleRefresh} loading={planTasksLoading}>
  Refresh
  </Button>
  <Button
  size="small"
  type="primary"
  icon={<ExpandOutlined />}
  onClick={() => setShowFullscreenDAG(true)}
  disabled={!planTasks || planTasks.length === 0}
  >
  Fullscreen
  </Button>
  </Space>

  <div style={{ textAlign: 'center', marginTop: 8 }}>
  <Text type="secondary" style={{ fontSize: 11, opacity: 0.6 }}>
  Select a node to inspect task details
  </Text>
  </div>
  </div>
  </div>
  );

  return (
  <>
  <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: 'var(--bg-primary)' }}>
  <Tabs
  activeKey={activeTab}
  onChange={setActiveTab}
  items={[
  {
  key: 'plan',
  label: 'Plan',
  children: <div style={{ height: '100%' }}>{renderPlanPanel()}</div>,
  },
  {
  key: 'executor',
  label: 'Execution Status',
  children: (
  <div style={{ height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
  <ExecutorPanel />
  </div>
  ),
  },
  {
  key: 'artifacts',
  label: 'Artifacts',
  children: (
  <div style={{ height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
  <ArtifactsPanel sessionId={currentSession?.session_id ?? null} />
  </div>
  ),
  },
  ...(ENV.TERMINAL_ENABLED
  ? [
    {
    key: 'terminal',
    label: 'Terminal',
    children: (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
    <TerminalPanel sessionId={currentSession?.session_id ?? null} />
    </div>
    ),
    },
    ]
  : []),
  ]}
  className="dag-sidebar-tabs"
  style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
  tabBarStyle={{ margin: 0 }}
  />
  </div>

  {}
  {showFullscreenDAG && (
  <DAG3DView
  onClose={() => setShowFullscreenDAG(false)}
  onNodeSelect={(task) => {
  if (task) {
  openTaskDrawer(task);
  }
  }}
  />
  )}

  <TodoListPanel
  open={planTodoListOpen}
  onClose={() => setPlanTodoListOpen(false)}
  planId={currentPlanId ?? null}
  targetTaskId={null}
  onTaskClick={handlePlanTodoTaskClick}
  fullPlan
  />
  </>
  );
};

export default DAGSidebar;
