import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Drawer, Empty, Select, Space, Spin, Tabs, Tooltip } from 'antd';
import { AppstoreOutlined, ApartmentOutlined, FolderOpenOutlined, FullscreenExitOutlined, FullscreenOutlined,
  HistoryOutlined, NodeIndexOutlined, ReloadOutlined } from '@ant-design/icons';
import { usePlanSummaries, usePlanTasks, usePlanTree } from '@hooks/usePlans';
import { useChatStore } from '@store/chat';
import { useTasksStore } from '@store/tasks';
import { useLayoutStore } from '@store/layout';
import { planTreeApi } from '@api/planTree';
import { shouldHandlePlanSyncEvent } from '@utils/planSyncEvents';
import type { PlanSyncEventDetail } from '@/types';
import PlanTreeVisualization from '@components/dag/PlanTreeVisualization';
import DAG3DView from '@components/dag/DAG3DView';
import TodoListPanel from '@components/tasks/detail/TodoListPanel';
import PlanTaskArtifacts from '../planArtifacts/PlanTaskArtifacts';
import ExecutorPanel from '../ExecutorPanel';
import ArtifactsPanel from '../ArtifactsPanel';
import TaskOutline from './TaskOutline';
import SelectedTaskDetail from './SelectedTaskDetail';
import PlanRunHistory from './PlanRunHistory';
import { defaultTaskSelection, isActiveJob, scopedPlanJobs, taskStatus } from './model';
import { artifactSourceSession, usePlanSourceGuard } from './usePlanSourceGuard';
import './PlanWorkspace.css';

/** Outer scope boundary: selecting another session/plan discards transient UI and callbacks. */
const PlanWorkspace: React.FC = () => {
  const planId = useChatStore((state) => state.currentPlanId);
  const sessionId = useChatStore((state) => state.currentSession?.session_id ?? state.currentSession?.id ?? null);
  return <ScopedPlanWorkspace key={`${sessionId ?? 'none'}:${planId ?? 'none'}`} planId={planId} sessionId={sessionId} />;
};

const ScopedPlanWorkspace: React.FC<{ planId: number | null; sessionId: string | null }> = ({ planId, sessionId }) => {
  const currentPlanTitle = useChatStore((state) => state.currentPlanTitle);
  const setChatContext = useChatStore((state) => state.setChatContext);
  const fullscreen = useLayoutStore((state) => state.dagSidebarFullscreen);
  const toggleFullscreen = useLayoutStore((state) => state.toggleDagSidebarFullscreen);
  const { data: summaries = [], isLoading: plansLoading, isError: plansError } = usePlanSummaries();
  const taskQuery = usePlanTasks({ planId });
  const treeQuery = usePlanTree(planId);
  const tasks = useMemo(() => taskQuery.data.map((task) => {
    const raw = treeQuery.data?.nodes[String(task.id)];
    return { ...task, freshness: raw?.freshness ?? task.freshness,
      stale_reasons: raw?.stale_reasons ?? task.stale_reasons, metadata: raw?.metadata ?? task.metadata };
  }), [taskQuery.data, treeQuery.data]);
  const [pickedTaskId, setPickedTaskId] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState('plan');
  const [mobileDetail, setMobileDetail] = useState(false);
  const [todoOpen, setTodoOpen] = useState(false);
  const [graphOpen, setGraphOpen] = useState(false);
  const [backgroundOpen, setBackgroundOpen] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
  const board = useQuery({
    queryKey: ['workspace', 'jobs', sessionId, planId], enabled: planId != null,
    queryFn: () => planTreeApi.getBackgroundTaskBoard({ plan_id: planId!, include_finished: true, limit: 50 }),
    refetchInterval: 8000, refetchOnWindowFocus: false, retry: 1,
  });
  const jobs = useMemo(() => planId == null ? [] : scopedPlanJobs(board.data, planId), [board.data, planId]);
  const activeJobs = jobs.filter(isActiveJob);
  const hasActiveWork = activeJobs.length > 0 || tasks.some((task) => taskStatus(task) === 'running');
  const previouslyActive = useRef(false);
  const selectedTask = tasks.find((task) => task.id === pickedTaskId)
    ?? defaultTaskSelection(tasks);
  const executionTasks = tasks.filter((task) => task.task_type === 'atomic');
  const completed = executionTasks.filter((task) => taskStatus(task) === 'completed').length;
  const failures = executionTasks.filter((task) => taskStatus(task) === 'failed').length;
  const planTitle = treeQuery.data?.title || summaries.find((plan) => plan.id === planId)?.title || currentPlanTitle || '选择一个研究计划';
  const pickerOptions = summaries.map((plan) => ({ value: plan.id, label: plan.title }));
  if (planId != null && !pickerOptions.some((option) => option.value === planId)) pickerOptions.unshift({ value: planId, label: planTitle });
  const artifactSessionId = artifactSourceSession(treeQuery.data) ?? undefined;
  const foreignSource = Boolean(artifactSessionId && artifactSessionId !== sessionId);
  const sourceGuard = usePlanSourceGuard(planId, sessionId);
  const sourceNotice = sourceGuard.notice ?? (foreignSource && planId != null
    ? {planId, title: planTitle, sessionId: artifactSessionId!} : null);
  useEffect(() => {
    if (foreignSource) { setTodoOpen(false); useTasksStore.getState().closeTaskDrawer(); }
  }, [foreignSource]);

  const refresh = useCallback(() => {
    if (planId == null) return;
    void treeQuery.refetch();
    void board.refetch();
  }, [planId, treeQuery.refetch, board.refetch]);

  useEffect(() => {
    const store = useTasksStore.getState();
    store.closeTaskDrawer();
    store.clearTaskResultCache();
    store.setTasks([]);
    return () => { useTasksStore.getState().closeTaskDrawer(); };
  }, []);

  useEffect(() => { useTasksStore.getState().setTasks(tasks); }, [tasks]);

  useEffect(() => {
    const handle = (event: Event) => {
      const detail = (event as CustomEvent<PlanSyncEventDetail>).detail;
      if (!shouldHandlePlanSyncEvent(detail, planId, ['task_changed', 'plan_updated', 'plan_jobs_completed', 'plan_deleted'])) return;
      if (detail.type === 'plan_deleted') {
        useTasksStore.getState().closeTaskDrawer();
        useTasksStore.getState().setTasks([]);
        setChatContext({ planId: null, planTitle: null, taskId: null, taskName: null });
        return;
      }
      refresh();
    };
    window.addEventListener('tasksUpdated', handle);
    return () => window.removeEventListener('tasksUpdated', handle);
  }, [planId, refresh, setChatContext]);

  // Poll the authoritative tree while a job is active, including its final transition.
  useEffect(() => {
    if (board.dataUpdatedAt && planId != null && (hasActiveWork || previouslyActive.current)) void treeQuery.refetch();
    previouslyActive.current = hasActiveWork;
  }, [board.dataUpdatedAt, planId, hasActiveWork, treeQuery.refetch]);

  const selectTask = (id: number) => {
    const task = tasks.find((item) => item.id === id);
    if (!task) return;
    setPickedTaskId(id);
    setMobileDetail(true);
    useTasksStore.getState().setSelectedTask(task);
    setChatContext({ taskId: id, taskName: task.name });
    setActiveTab('plan');
  };
  const selectPlan = sourceGuard.select;

  return <div className="plan-workspace">
    <header className="pw-header">
      <div className="pw-heading-row"><span className="pw-eyebrow"><ApartmentOutlined /> 当前对话计划</span>
        <Space size={2}><Tooltip title="当前会话全部文件"><Button aria-label="全部文件" type="text" size="small" icon={<FolderOpenOutlined />} onClick={() => setFilesOpen(true)} /></Tooltip>
          <Tooltip title="执行清单"><Button aria-label="执行清单" type="text" size="small" icon={<AppstoreOutlined />} disabled={planId == null || foreignSource} onClick={() => setTodoOpen(true)} /></Tooltip>
          <Tooltip title="刷新计划"><Button aria-label="刷新计划" type="text" size="small" icon={<ReloadOutlined spin={taskQuery.isFetching} />} onClick={refresh} disabled={planId == null} /></Tooltip>
          <Tooltip title={fullscreen ? '退出专注模式' : '展开工作台'}><Button aria-label={fullscreen ? '退出专注模式' : '展开工作台'} type="text" size="small"
            icon={fullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />} onClick={toggleFullscreen} /></Tooltip></Space>
      </div>
      <Select className="pw-plan-picker" aria-label="选择当前对话计划" title="切换计划会改变后续对话的任务上下文" value={planId ?? undefined} options={pickerOptions}
        onChange={selectPlan} loading={plansLoading || sourceGuard.busy} placeholder="选择一个研究计划" showSearch optionFilterProp="label"
        variant="borderless" popupMatchSelectWidth={false} />
      <div className="pw-plan-meta"><span>{planId == null ? '在对话中创建计划，或选择已有计划' : `Plan #${planId}`}</span>
        {planId != null && <><span className="pw-separator">·</span><span>{completed} / {executionTasks.length} 个执行任务已完成</span>
          {activeJobs.length > 0 && <span className="pw-live-indicator">执行中</span>}
          {failures > 0 && <span className="pw-error-text">{failures} 个失败</span>}</>}
      </div>
    </header>
    {sourceNotice && <Alert className="pw-alert" type="info" showIcon
      message={`“${sourceNotice.title}”的产物属于另一会话`}
      description="打开来源会话后可继续执行。当前会话与计划绑定保持不变。"
      action={<Space><Button size="small" type="primary" loading={sourceGuard.busy} onClick={() => void sourceGuard.openSource(sourceNotice)}>打开来源会话</Button>
        {!foreignSource && <Button size="small" onClick={sourceGuard.dismiss}>取消</Button>}</Space>} />}
    {sourceGuard.error && <Alert className="pw-alert" type="warning" showIcon message={sourceGuard.error} closable onClose={sourceGuard.dismiss} />}
    {plansError && <Alert className="pw-alert" type="warning" showIcon message="计划列表暂时无法读取，当前计划仍可查看" />}
    <Tabs className="pw-tabs" activeKey={activeTab} onChange={setActiveTab} destroyOnHidden
      tabBarExtraContent={planId != null ? <Tooltip title="查看执行顺序及运行整个计划"><Button type="text" size="small" icon={<AppstoreOutlined />} disabled={foreignSource} onClick={() => setTodoOpen(true)}>执行清单</Button></Tooltip> : undefined}
      items={[
        { key: 'plan', label: <><ApartmentOutlined /> 计划</>, children: planId == null ? <Empty className="pw-empty" image={Empty.PRESENTED_IMAGE_SIMPLE} description="计划会把研究目标拆解成可执行的任务。选择计划后，可在这里查看过程和对应产物。" />
          : taskQuery.isError ? <Alert className="pw-alert" type="error" showIcon message="计划任务加载失败" action={<Button size="small" onClick={refresh}>重试</Button>} />
          : tasks.length ? <div className={`pw-plan-body ${mobileDetail ? 'pw-show-detail' : ''}`}>
            <TaskOutline tasks={tasks} selectedTaskId={selectedTask?.id ?? null} onSelect={selectTask} />
            {selectedTask && <SelectedTaskDetail key={`${planId}:${selectedTask.id}`} planId={planId} sessionId={sessionId}
              task={selectedTask} tasks={tasks} jobs={jobs} artifactSessionId={artifactSessionId} executionBlocked={foreignSource}
              onSelectTask={selectTask} onBack={() => setMobileDetail(false)} onShowHistory={() => setActiveTab('history')} onRefresh={refresh} />}
          </div> : <div className="pw-empty">{taskQuery.isFetching ? <><Spin /><p>正在读取计划…</p></> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该计划尚无任务；生成中的任务会自动出现在这里。" />}</div> },
        { key: 'artifacts', label: <><FolderOpenOutlined /> 产物</>, children: planId != null ? <div className="pw-artifacts"><div className="pw-section-heading"><h3>计划产物</h3><Button size="small" onClick={() => setFilesOpen(true)}>全部文件</Button></div>
          <p className="pw-muted pw-small">按任务查看交付文件、来源和历史版本。</p>
          <PlanTaskArtifacts planId={planId} sessionId={sessionId} artifactSessionId={artifactSessionId} tasks={tasks} onSelectTask={selectTask} />
        </div> : <ArtifactsPanel key={sessionId ?? 'none'} sessionId={sessionId} /> },
        { key: 'history', label: <><HistoryOutlined /> 运行记录</>, children: planId != null ? <PlanRunHistory jobs={jobs} planId={planId}
          loading={board.isLoading} error={board.isError ? '请稍后重试或点击刷新。' : null} onBackground={() => setBackgroundOpen(true)} />
          : <div className="pw-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择计划以查看运行记录" /><Button onClick={() => setBackgroundOpen(true)}>查看会话后台任务</Button></div> },
        { key: 'graph', label: <><NodeIndexOutlined /> 关系图</>, children: <div className="pw-graph"><div className="pw-graph-toolbar"><span className="pw-muted pw-small">查看任务层级与依赖关系</span>
          <Button size="small" disabled={!tasks.length} onClick={() => setGraphOpen(true)}>3D 全屏</Button></div>
          {tasks.length ? <PlanTreeVisualization tasks={tasks} loading={taskQuery.isFetching} height="100%" selectedTaskId={selectedTask?.id}
            onSelectTask={(task) => { if (task) selectTask(task.id); }} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无任务关系" />}</div> },
      ]} />
    {!foreignSource && <TodoListPanel open={todoOpen} onClose={() => setTodoOpen(false)} planId={planId} currentSessionId={sessionId}
      targetTaskId={null} fullPlan onTaskClick={(id) => { setTodoOpen(false); selectTask(id); }} />}
    {graphOpen && <DAG3DView onClose={() => setGraphOpen(false)} onNodeSelect={(task) => { if (task) { setGraphOpen(false); selectTask(task.id); } }} />}
    <Drawer title="当前会话 · 全部文件" width="min(1000px, 95vw)" open={filesOpen} onClose={() => setFilesOpen(false)} destroyOnHidden>
      <ArtifactsPanel key={sessionId ?? 'none'} sessionId={sessionId} />
    </Drawer>
    <Drawer title="后台任务" width="min(900px, 95vw)" open={backgroundOpen} onClose={() => setBackgroundOpen(false)} destroyOnHidden>
      <ExecutorPanel key={`${sessionId}:${planId}`} />
    </Drawer>
  </div>;
};
export default PlanWorkspace;
