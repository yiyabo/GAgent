import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Drawer, Empty, Space, Spin, Tabs, Tooltip } from 'antd';
import { AppstoreOutlined, ApartmentOutlined, FolderOpenOutlined, FullscreenExitOutlined, FullscreenOutlined,
  HistoryOutlined, NodeIndexOutlined, ReloadOutlined } from '@ant-design/icons';
import { usePlanTasks, usePlanTree } from '@hooks/usePlans';
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
import { useT } from '@/i18n/useT';
import './PlanWorkspace.css';

/** Outer scope boundary: selecting another session/plan discards transient UI and callbacks. */
const PlanWorkspace: React.FC = () => {
  const planId = useChatStore((state) => state.currentPlanId);
  const sessionId = useChatStore((state) => state.currentSession?.session_id ?? state.currentSession?.id ?? null);
  return <ScopedPlanWorkspace key={`${sessionId ?? 'none'}:${planId ?? 'none'}`} planId={planId} sessionId={sessionId} />;
};

const ScopedPlanWorkspace: React.FC<{ planId: number | null; sessionId: string | null }> = ({ planId, sessionId }) => {
  const t = useT();
  const currentPlanTitle = useChatStore((state) => state.currentPlanTitle);
  const setChatContext = useChatStore((state) => state.setChatContext);
  const fullscreen = useLayoutStore((state) => state.dagSidebarFullscreen);
  const toggleFullscreen = useLayoutStore((state) => state.toggleDagSidebarFullscreen);
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
  const executionTasks = tasks.filter((task) => task.task_type !== 'root' &&
    !tasks.some((child) => child.parent_id === task.id));
  const completed = executionTasks.filter((task) => taskStatus(task) === 'completed').length;
  const failures = executionTasks.filter((task) => taskStatus(task) === 'failed').length;
  const needsReview = executionTasks.filter((task) => taskStatus(task) === 'needs_review').length;
  const planTitle = treeQuery.data?.title || currentPlanTitle || (planId != null ? t('planWorkspace.planNum', { id: planId }) : t('planWorkspace.noPlanYet'));
  const artifactSessionId = artifactSourceSession(treeQuery.data) ?? undefined;
  const foreignSource = Boolean(artifactSessionId && artifactSessionId !== sessionId);
  const sourceGuard = usePlanSourceGuard(planId, sessionId);
  const sourceNotice = foreignSource && planId != null
    ? {planId, title: planTitle, sessionId: artifactSessionId!} : null;
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

  return <div className="plan-workspace">
    <header className="pw-header">
      <div className="pw-heading-row"><span className="pw-eyebrow"><ApartmentOutlined /> {t('planWorkspace.eyebrow')}</span>
        <Space size={2}><Tooltip title={t('planWorkspace.allFilesTip')}><Button aria-label={t('planWorkspace.allFiles')} type="text" size="small" icon={<FolderOpenOutlined />} onClick={() => setFilesOpen(true)} /></Tooltip>
          <Tooltip title={t('planWorkspace.checklist')}><Button aria-label={t('planWorkspace.checklist')} type="text" size="small" icon={<AppstoreOutlined />} disabled={planId == null || foreignSource} onClick={() => setTodoOpen(true)} /></Tooltip>
          <Tooltip title={t('planWorkspace.refreshPlan')}><Button aria-label={t('planWorkspace.refreshPlan')} type="text" size="small" icon={<ReloadOutlined spin={taskQuery.isFetching} />} onClick={refresh} disabled={planId == null} /></Tooltip>
          <Tooltip title={fullscreen ? t('planWorkspace.exitFocus') : t('planWorkspace.expandWorkspace')}><Button aria-label={fullscreen ? t('planWorkspace.exitFocus') : t('planWorkspace.expandWorkspace')} type="text" size="small"
            icon={fullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />} onClick={toggleFullscreen} /></Tooltip></Space>
      </div>
      {/* One session, one plan: the bound plan is shown read-only. Plans are created,
          reviewed or replaced inside the conversation itself and are never picked from
          another session's list (2026-10-09, LOCAL_INFRA §113). */}
      <h2 className="pw-plan-title" title={planTitle}>{planTitle}</h2>
      <div className="pw-plan-meta"><span>{planId == null ? t('planWorkspace.createInChat') : t('planWorkspace.planNum', { id: planId })}</span>
        {planId != null && <><span className="pw-separator">·</span><span>{t('planWorkspace.tasksExecuted', { done: completed, total: executionTasks.length })}</span>
          {activeJobs.length > 0 && <span className="pw-live-indicator">{t('planStatus.running')}</span>}
          {failures > 0 && <span className="pw-error-text">{t('planWorkspace.failedCount', { count: failures })}</span>}</>}
        {needsReview > 0 && <span className="pw-review-text">{t('planWorkspace.reviewCount', { count: needsReview })}</span>}
      </div>
    </header>
    {tasks.some((task) => task.task_type === 'composite' && taskStatus(task) !== 'completed' &&
      !tasks.some((child) => child.parent_id === task.id)) &&
      <Alert className="pw-alert" type="warning" showIcon message={t('planWorkspace.decomposeIncomplete')}
        description={t('planWorkspace.decomposeIncompleteDesc')} />}
    {sourceNotice && <Alert className="pw-alert" type="info" showIcon
      message={t('planWorkspace.foreignArtifacts', { title: sourceNotice.title })}
      description={t('planWorkspace.foreignArtifactsDesc')}
      action={<Button size="small" type="primary" loading={sourceGuard.busy} onClick={() => void sourceGuard.openSource(sourceNotice)}>{t('planWorkspace.openSource')}</Button>} />}
    {sourceGuard.error && <Alert className="pw-alert" type="warning" showIcon message={sourceGuard.error} closable onClose={sourceGuard.dismiss} />}
    <Tabs className="pw-tabs" activeKey={activeTab} onChange={setActiveTab} destroyOnHidden
      tabBarExtraContent={planId != null ? <Tooltip title={t('planWorkspace.checklistTip')}><Button type="text" size="small" icon={<AppstoreOutlined />} disabled={foreignSource} onClick={() => setTodoOpen(true)}>{t('planWorkspace.checklist')}</Button></Tooltip> : undefined}
      items={[
        { key: 'plan', label: <><ApartmentOutlined /> {t('planWorkspace.tabPlan')}</>, children: planId == null ? <Empty className="pw-empty" image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('planWorkspace.emptyPlan')} />
          : taskQuery.isError ? <Alert className="pw-alert" type="error" showIcon message={t('planWorkspace.tasksLoadFailed')} action={<Button size="small" onClick={refresh}>{t('common.retry')}</Button>} />
          : tasks.length ? <div className={`pw-plan-body ${mobileDetail ? 'pw-show-detail' : ''}`}>
            <TaskOutline tasks={tasks} selectedTaskId={selectedTask?.id ?? null} onSelect={selectTask} />
            {selectedTask && <SelectedTaskDetail key={`${planId}:${selectedTask.id}`} planId={planId} sessionId={sessionId}
              task={selectedTask} tasks={tasks} jobs={jobs} artifactSessionId={artifactSessionId} executionBlocked={foreignSource}
              onSelectTask={selectTask} onBack={() => setMobileDetail(false)} onShowHistory={() => setActiveTab('history')} onRefresh={refresh} />}
          </div> : <div className="pw-empty">{taskQuery.isFetching ? <><Spin /><p>{t('planWorkspace.readingPlan')}</p></> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('planWorkspace.noTasksYet')} />}</div> },
        { key: 'artifacts', label: <><FolderOpenOutlined /> {t('planWorkspace.tabArtifacts')}</>, children: planId != null ? <div className="pw-artifacts"><div className="pw-section-heading"><h3>{t('planWorkspace.planArtifacts')}</h3><Button size="small" onClick={() => setFilesOpen(true)}>{t('planWorkspace.allFiles')}</Button></div>
          <p className="pw-muted pw-small">{t('planWorkspace.artifactsHint')}</p>
          <PlanTaskArtifacts planId={planId} sessionId={sessionId} artifactSessionId={artifactSessionId} tasks={tasks} onSelectTask={selectTask} />
        </div> : <ArtifactsPanel key={sessionId ?? 'none'} sessionId={sessionId} /> },
        { key: 'history', label: <><HistoryOutlined /> {t('planWorkspace.tabHistory')}</>, children: planId != null ? <PlanRunHistory jobs={jobs} planId={planId}
          loading={board.isLoading} error={board.isError ? t('planWorkspace.historyRetry') : null} onBackground={() => setBackgroundOpen(true)} />
          : <div className="pw-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('planWorkspace.pickPlanForHistory')} /><Button onClick={() => setBackgroundOpen(true)}>{t('planWorkspace.viewBackground')}</Button></div> },
        { key: 'graph', label: <><NodeIndexOutlined /> {t('planWorkspace.tabGraph')}</>, children: <div className="pw-graph"><div className="pw-graph-toolbar"><span className="pw-muted pw-small">{t('planWorkspace.graphHint')}</span>
          <Button size="small" disabled={!tasks.length} onClick={() => setGraphOpen(true)}>{t('planWorkspace.graph3d')}</Button></div>
          {tasks.length ? <PlanTreeVisualization tasks={tasks} loading={taskQuery.isFetching} height="100%" selectedTaskId={selectedTask?.id}
            onSelectTask={(task) => { if (task) selectTask(task.id); }} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('planWorkspace.noRelations')} />}</div> },
      ]} />
    {!foreignSource && <TodoListPanel open={todoOpen} onClose={() => setTodoOpen(false)} planId={planId} currentSessionId={sessionId}
      targetTaskId={null} fullPlan onTaskClick={(id) => { setTodoOpen(false); selectTask(id); }} />}
    {graphOpen && <DAG3DView onClose={() => setGraphOpen(false)} onNodeSelect={(task) => { if (task) { setGraphOpen(false); selectTask(task.id); } }} />}
    <Drawer title={t('planWorkspace.filesDrawerTitle')} width="min(1000px, 95vw)" open={filesOpen} onClose={() => setFilesOpen(false)} destroyOnHidden>
      <ArtifactsPanel key={sessionId ?? 'none'} sessionId={sessionId} />
    </Drawer>
    <Drawer title={t('planWorkspace.backgroundDrawerTitle')} width="min(900px, 95vw)" open={backgroundOpen} onClose={() => setBackgroundOpen(false)} destroyOnHidden>
      <ExecutorPanel key={`${sessionId}:${planId}`} />
    </Drawer>
  </div>;
};
export default PlanWorkspace;
