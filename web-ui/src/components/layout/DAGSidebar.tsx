import React, { useEffect, useMemo, useState } from 'react';
import { Typography, Button, Space, Badge, Tooltip, Empty, Tabs, Progress, Tag } from 'antd';
import {
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
import { planTreeApi } from '@api/planTree';
import ExecutorPanel from './ExecutorPanel';
import ArtifactsPanel from './ArtifactsPanel';

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
  const { setCurrentPlan, setTasks, openTaskDrawer, closeTaskDrawer, selectedTaskId } = useTasksStore((state) => ({
    setCurrentPlan: state.setCurrentPlan,
    setTasks: state.setTasks,
    openTaskDrawer: state.openTaskDrawer,
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
  const [showFullscreenDAG, setShowFullscreenDAG] = useState(false);
  const [decomposeSnapshot, setDecomposeSnapshot] = useState<Record<string, any> | null>(null);

  // 稳定化session_id以避免无限循环
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
	    const stats = (snapshot.stats ?? {}) as Record<string, any>;
	    const params = (snapshot.params ?? {}) as Record<string, any>;
	    const logs = Array.isArray(snapshot.logs) ? snapshot.logs : [];
	    const totalBudget = toNumber(params.node_budget) ?? toNumber(stats.node_budget);
	    let remainingBudget: number | null = null;
	    let queueRemaining: number | null = toNumber(stats.queue_remaining);
	    let createdCount: number | null = null;
	    let processedCount: number | null = null;
	    for (let idx = logs.length - 1; idx >= 0; idx -= 1) {
	      const metadata = logs[idx]?.metadata as Record<string, any> | undefined;
	      if (!metadata) continue;
	      if (remainingBudget === null) {
	        remainingBudget = toNumber(metadata.budget_remaining);
	      }
	      if (queueRemaining === null) {
	        queueRemaining = toNumber(metadata.queue_remaining);
	      }
	      if (createdCount === null) {
	        createdCount = toNumber(metadata.created_count ?? metadata.createdCount);
	      }
	      if (processedCount === null) {
	        processedCount = toNumber(metadata.processed_count ?? metadata.processedCount);
	      }
	      if (
	        (remainingBudget !== null || totalBudget === null) &&
	        queueRemaining !== null &&
	        createdCount !== null &&
	        processedCount !== null
	      ) {
	        break;
	      }
	    }
	    const consumedFromStats = toNumber(stats.consumed_budget);
	    const consumedBudget =
	      consumedFromStats ??
	      (totalBudget !== null && remainingBudget !== null
	        ? Math.max(0, totalBudget - remainingBudget)
	        : createdCount !== null
	          ? Math.max(0, Math.round(createdCount))
	        : null);
	    const percentRaw =
	      totalBudget !== null && totalBudget > 0 && consumedBudget !== null
	        ? Math.round((consumedBudget / totalBudget) * 100)
	        : processedCount !== null && queueRemaining !== null
	          ? Math.round((processedCount / Math.max(1, processedCount + queueRemaining + 1)) * 100)
	        : null;
	    const percent =
	      percentRaw !== null ? Math.max(0, Math.min(100, percentRaw)) : null;
	    return {
	      status: snapshot.status as string,
	      percent,
	      totalBudget,
	      consumedBudget,
	      queueRemaining,
	      createdCount,
	      processedCount,
	    };
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
        ? '拆解中'
        : statusKey === 'queued'
          ? '排队中'
          : statusKey === 'failed'
            ? '失败'
            : '进行中';

    const percent = decomposeProgress.percent != null ? decomposeProgress.percent : 0;
    const detailParts: string[] = [];
    if (decomposeProgress.consumedBudget != null) {
      detailParts.push(`已创建 ${Math.max(0, Math.round(decomposeProgress.consumedBudget))}`);
    }
    if (decomposeProgress.totalBudget != null && decomposeProgress.totalBudget > 0) {
      detailParts.push(`上限 ${Math.round(decomposeProgress.totalBudget)}`);
    }
    if (decomposeProgress.queueRemaining != null) {
      detailParts.push(`队列剩余 ${Math.max(0, Math.round(decomposeProgress.queueRemaining))}`);
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
              任务拆解
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
              估算中
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

  // 移除错误的useCallback包装

  // 监听全局任务更新事件，自动刷新侧栏DAG数据
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

  // 当 planId 变化时，延迟刷新以获取异步创建的子任务
  // 因为 decomposition 是后台异步执行的，首次加载可能只有 root task
  useEffect(() => {
    if (!currentPlanId) return;
    
    // 延迟 3 秒后刷新，等待 decomposition 完成
    const timer1 = window.setTimeout(() => {
      refetchTasks();
    }, 3000);
    
    // 再延迟 8 秒后刷新，处理较慢的 decomposition
    const timer2 = window.setTimeout(() => {
      refetchTasks();
    }, 8000);
    
    return () => {
      window.clearTimeout(timer1);
      window.clearTimeout(timer2);
    };
  }, [currentPlanId, refetchTasks]);

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

  const renderPlanPanel = () => (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '16px', borderBottom: '1px solid var(--border-color)', background: 'var(--bg-primary)' }}>
        <div
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <NodeIndexOutlined style={{ color: 'var(--primary-color)', fontSize: 18 }} />
            <Title level={5} style={{ margin: 0, color: 'var(--text-primary)' }}>
              任务图谱
            </Title>
          </div>

          <Space size={4}>
            <Tooltip title={dagVisible ? '隐藏图谱' : '显示图谱'}>
              <Button
                type="text"
                size="small"
                icon={dagVisible ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                onClick={() => setDagVisible(!dagVisible)}
              />
            </Tooltip>

            <Tooltip title="全屏视图">
              <Button
                type="text"
                size="small"
                icon={<ExpandOutlined />}
                onClick={() => setShowFullscreenDAG(true)}
                disabled={!planTasks || planTasks.length === 0}
              />
            </Tooltip>

            <Tooltip title="侧边栏全屏">
              <Button
                type="text"
                size="small"
                icon={dagSidebarFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
                onClick={toggleDagSidebarFullscreen}
              />
            </Tooltip>

            <Tooltip title="设置">
              <Button type="text" size="small" icon={<SettingOutlined />} />
            </Tooltip>
          </Space>
        </div>

        {renderDecomposeProgress()}

        <Space size={16} wrap>
          <Badge count={stats.total} size="small" offset={[8, -2]}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              总任务
            </Text>
          </Badge>
          <Badge count={stats.running} size="small" color="blue" offset={[8, -2]}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              运行中
            </Text>
          </Badge>
          <Badge count={stats.completed} size="small" color="green" offset={[8, -2]}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              已完成
            </Text>
          </Badge>
          {stats.failed > 0 && (
            <Badge count={stats.failed} size="small" color="red" offset={[8, -2]}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                失败
              </Text>
            </Badge>
          )}
        </Space>

        {renderPlanEvaluationBadge()}

        <Space direction="vertical" size={8} style={{ width: '100%', marginTop: 12 }}>
          <Text type="secondary" style={{ fontSize: 11 }}>
            当前ROOT任务：
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
            {selectedPlanTitle || '暂无ROOT任务'}
          </div>
          <Text type="secondary" style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>
            每个对话对应一个根任务
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
                  ? '加载任务中...'
                  : currentWorkflowId || currentSession?.session_id
                    ? '当前会话尚无任务'
                    : '请先开始一个对话或创建工作流'
              }
            />
          )}
        </div>
      )}

      <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border-color)', background: 'var(--bg-tertiary)' }}>
        <Space size={8} wrap style={{ width: '100%', justifyContent: 'center' }}>
          <Button size="small" icon={<ReloadOutlined />} onClick={handleRefresh} loading={planTasksLoading}>
            刷新
          </Button>
          <Button
            size="small"
            type="primary"
            icon={<ExpandOutlined />}
            onClick={() => setShowFullscreenDAG(true)}
            disabled={!planTasks || planTasks.length === 0}
          >
            全屏
          </Button>
        </Space>

        <div style={{ textAlign: 'center', marginTop: 8 }}>
          <Text type="secondary" style={{ fontSize: 11, opacity: 0.6 }}>
            点击全屏查看完整任务图
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
              label: '计划树',
              children: <div style={{ height: '100%' }}>{renderPlanPanel()}</div>,
            },
            {
              key: 'executor',
              label: '任务状态',
              children: (
                <div style={{ height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                  <ExecutorPanel />
                </div>
              ),
            },
            {
              key: 'artifacts',
              label: '生成文件',
              children: (
                <div style={{ height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                  <ArtifactsPanel sessionId={currentSession?.session_id ?? null} />
                </div>
              ),
            },
          ]}
          className="dag-sidebar-tabs"
          style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
          tabBarStyle={{ margin: 0 }}
        />
      </div>

      {/* 3D 沉浸式全屏 DAG 视图 */}
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
    </>
  );
};

export default DAGSidebar;
