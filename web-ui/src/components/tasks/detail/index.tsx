import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  App as AntdApp,
  Button,
  Drawer,
  Empty,
  Space,
  Spin,
  Typography,
} from 'antd';
import { ReloadOutlined, CopyOutlined, PlayCircleOutlined, UnorderedListOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { planTreeApi } from '@api/planTree';
import { usePlanTasks } from '@hooks/usePlans';
import { useChatStore } from '@store/chat';
import { useTasksStore } from '@store/tasks';
import type {
  PlanResultItem,
  PlanTaskNode,
  PlanSyncEventDetail,
  VerifyTaskResponse,
  ToolResultPayload,
} from '@/types';
import { dispatchPlanSyncEvent, shouldHandlePlanSyncEvent } from '@utils/planSyncEvents';
import JobLogPanel from '@components/chat/JobLogPanel';
import { TaskDrawerContent, copyJsonToClipboard } from './TaskDetailSections';
import TaskExecuteModal from './TaskExecuteModal';
import TodoListPanel from './TodoListPanel';

const { Text, Title } = Typography;

const TaskDetailDrawer: React.FC = () => {
  const { message } = AntdApp.useApp();
  const {
    isTaskDrawerOpen,
    selectedTaskId,
    selectedTask,
    taskResultCache,
    openTaskDrawer,
    openTaskDrawerById,
    closeTaskDrawer,
    setTaskResult,
  } = useTasksStore((state) => ({
    isTaskDrawerOpen: state.isTaskDrawerOpen,
    selectedTaskId: state.selectedTaskId,
    selectedTask: state.selectedTask,
    taskResultCache: state.taskResultCache,
    openTaskDrawer: state.openTaskDrawer,
    openTaskDrawerById: state.openTaskDrawerById,
    closeTaskDrawer: state.closeTaskDrawer,
    setTaskResult: state.setTaskResult,
  }));

  const { currentPlanId, currentSessionId, recentToolResults } = useChatStore((state) => {
    const results: ToolResultPayload[] = [];
    const seen = new Set<string>();
    for (let idx = state.messages.length - 1; idx >= 0 && results.length < 5; idx -= 1) {
      const toolResults = state.messages[idx]?.metadata?.tool_results;
      if (!Array.isArray(toolResults) || toolResults.length === 0) {
        continue;
      }
      for (const payload of toolResults) {
        if (!payload) {
          continue;
        }
        const key = `${payload.name ?? ''}::${payload.summary ?? ''}::${payload.result?.query ?? ''
          }`;
        if (seen.has(key)) {
          continue;
        }
        seen.add(key);
        results.push(payload);
        if (results.length >= 5) {
          break;
        }
      }
    }
    return {
      currentPlanId: state.currentPlanId,
      currentSessionId: state.currentSession?.session_id ?? state.currentSession?.id ?? null,
      recentToolResults: results,
    };
  });

  const {
    data: planTasks = [],
    isFetching: tasksLoading,
    refetch: refetchPlanTasks,
  } = usePlanTasks({ planId: currentPlanId ?? undefined });

  const taskMap = useMemo(() => {
    return new Map<number, PlanTaskNode>(planTasks.map((task) => [task.id, task]));
  }, [planTasks]);

  const activeTask = useMemo<PlanTaskNode | null>(() => {
    if (selectedTaskId == null) {
      return null;
    }
    return taskMap.get(selectedTaskId) ?? selectedTask ?? null;
  }, [selectedTaskId, selectedTask, taskMap]);

  const cachedResult =
    selectedTaskId != null ? taskResultCache[selectedTaskId] ?? undefined : undefined;

  const {
    data: taskResult,
    isFetching: resultLoading,
    refetch: refetchTaskResult,
  } = useQuery<PlanResultItem>({
    queryKey: ['planTree', 'taskResult', currentPlanId ?? null, selectedTaskId ?? null],
    enabled:
      isTaskDrawerOpen && currentPlanId != null && selectedTaskId != null && selectedTaskId > 0,
    initialData: () => cachedResult,
    refetchOnWindowFocus: false,
    queryFn: async () => {
      if (!currentPlanId || !selectedTaskId) {
        throw new Error('Missing plan or task information; cannot fetch execution result');
      }
      return planTreeApi.getTaskResult(currentPlanId, selectedTaskId);
    },
    onSuccess: (result) => {
      if (selectedTaskId != null) {
        setTaskResult(selectedTaskId, result);
      }
    },
  });

  const [executeModalOpen, setExecuteModalOpen] = useState(false);
  const [executeButtonLoading, setExecuteButtonLoading] = useState(false);
  const [verifyLoading, setVerifyLoading] = useState(false);
  const [todoListOpen, setTodoListOpen] = useState(false);
  const [latestExecution, setLatestExecution] = useState<{
    jobId: string;
    taskId: number;
    planId: number | null;
  } | null>(null);

  const activeExecutionJobId = useMemo(() => {
    if (!latestExecution) {
      return null;
    }
    if (!selectedTaskId || latestExecution.taskId !== selectedTaskId) {
      return null;
    }
    if ((latestExecution.planId ?? null) !== (currentPlanId ?? null)) {
      return null;
    }
    return latestExecution.jobId;
  }, [currentPlanId, latestExecution, selectedTaskId]);

  useEffect(() => {
    if (!isTaskDrawerOpen) {
      setLatestExecution(null);
    }
  }, [isTaskDrawerOpen]);

  useEffect(() => {
    if (selectedTaskId == null) {
      setLatestExecution(null);
    }
  }, [selectedTaskId]);

  useEffect(() => {
    if (!isTaskDrawerOpen || !currentPlanId || !selectedTaskId) {
      return;
    }
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<PlanSyncEventDetail>).detail;
      if (
        !shouldHandlePlanSyncEvent(detail, currentPlanId ?? null, [
          'task_changed',
          'plan_jobs_completed',
          'plan_updated',
          'plan_deleted',
        ])
      ) {
        return;
      }
      if (detail?.type === 'plan_deleted' && detail.plan_id === currentPlanId) {
        closeTaskDrawer();
        return;
      }
      void refetchPlanTasks();
      void refetchTaskResult();
      window.setTimeout(() => {
        void refetchPlanTasks();
        void refetchTaskResult();
      }, 800);
    };

    window.addEventListener('tasksUpdated', handler as EventListener);
    return () => {
      window.removeEventListener('tasksUpdated', handler as EventListener);
    };
  }, [
    isTaskDrawerOpen,
    currentPlanId,
    selectedTaskId,
    refetchPlanTasks,
    refetchTaskResult,
    closeTaskDrawer,
  ]);

  const handleRefresh = useCallback(() => {
    if (!currentPlanId || !selectedTaskId) {
      return;
    }
    void refetchPlanTasks();
    void refetchTaskResult();
  }, [currentPlanId, selectedTaskId, refetchPlanTasks, refetchTaskResult]);

  const handleCopyTask = useCallback(() => {
    if (!activeTask) return;
    void copyJsonToClipboard(
      { task: activeTask, result: taskResult ?? cachedResult ?? null },
      'Task details copied',
      message
    );
  }, [activeTask, taskResult, cachedResult, message]);

  const handleDependencyClick = useCallback(
    (dependencyId: number) => {
      if (dependencyId <= 0) {
        return;
      }
      const targetTask = taskMap.get(dependencyId);
      if (targetTask) {
        openTaskDrawer(targetTask);
        return;
      }
      openTaskDrawerById(dependencyId);
    },
    [openTaskDrawer, openTaskDrawerById, taskMap]
  );

  const handleOpenExecuteModal = useCallback(() => {
    if (!currentPlanId || !selectedTaskId) {
      message.error('Missing plan or task information; cannot execute task');
      return;
    }
    setExecuteModalOpen(true);
  }, [currentPlanId, message, selectedTaskId]);

  const handleReverify = useCallback(async () => {
    if (!currentPlanId || !selectedTaskId) {
      message.error('Missing plan or task information; cannot verify task');
      return;
    }
    setVerifyLoading(true);
    try {
      const response: VerifyTaskResponse = await planTreeApi.verifyTask(currentPlanId, selectedTaskId);
      setTaskResult(selectedTaskId, response.result);
      message.success(response.message || 'Task verification completed');
      dispatchPlanSyncEvent({
        type: 'task_changed',
        plan_id: currentPlanId,
        plan_title: null,
        session_id: currentSessionId ?? null,
        raw: response,
      }, {
        source: 'task.detail.verify',
        status: response.result?.status ?? null,
        sessionId: currentSessionId ?? null,
      });
      void refetchPlanTasks();
      void refetchTaskResult();
    } catch (error: any) {
      message.error(error?.message || 'Task verification failed');
    } finally {
      setVerifyLoading(false);
    }
  }, [
    currentPlanId,
    currentSessionId,
    message,
    refetchPlanTasks,
    refetchTaskResult,
    selectedTaskId,
    setTaskResult,
  ]);

  return (
    <Drawer
      width={480}
      title={
        activeTask
          ? (
            <Space direction="vertical" size={0}>
              <Title level={5} style={{ margin: 0 }}>
                {activeTask.name}
              </Title>
              <Text type="secondary">Task ID: {activeTask.id}</Text>
            </Space>
          )
          : 'Task Details'
      }
      open={isTaskDrawerOpen}
      onClose={closeTaskDrawer}
      destroyOnClose={false}
      maskClosable
      extra={
        <Space>
          <Button
            icon={<UnorderedListOutlined />}
            onClick={() => setTodoListOpen(true)}
            disabled={!currentPlanId || !selectedTaskId}
          >
            Todo List
          </Button>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            onClick={handleOpenExecuteModal}
            disabled={!currentPlanId || !selectedTaskId}
            loading={executeButtonLoading}
          >
            Execute
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={handleRefresh}
            disabled={!currentPlanId || !selectedTaskId}
            loading={tasksLoading || resultLoading}
          >
            Refresh
          </Button>
          <Button
            icon={<CopyOutlined />}
            onClick={handleCopyTask}
            disabled={!activeTask}
          >
            Copy
          </Button>
        </Space>
      }
    >
      {!currentPlanId ? (
        <Empty description="No plan is bound to the current session" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : !selectedTaskId ? (
        <Empty description="Select a task to view details" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : tasksLoading && !activeTask ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '32px 0' }}>
          <Spin tip="Loading task details..." />
        </div>
      ) : !activeTask ? (
        <Empty description="Task not found; it may have been deleted" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <TaskDrawerContent
            activeTask={activeTask}
            handleDependencyClick={handleDependencyClick}
            recentToolResults={recentToolResults}
            resultLoading={resultLoading}
            taskResult={taskResult}
            cachedResult={cachedResult}
            onReverify={handleReverify}
            verifyLoading={verifyLoading}
            canVerify={Boolean(taskResult ?? cachedResult)}
          />
          {activeExecutionJobId && (
            <section>
              <Title level={5}>Execution Chain</Title>
              <JobLogPanel
                jobId={activeExecutionJobId}
                targetTaskName={activeTask?.name ?? null}
                planId={currentPlanId}
                jobType="plan_execute"
              />
            </section>
          )}
        </Space>
      )}

      <TaskExecuteModal
        open={executeModalOpen}
        onClose={() => setExecuteModalOpen(false)}
        onExecutionStarted={(jobId) => {
          if (!selectedTaskId) {
            return;
          }
          setLatestExecution({
            jobId,
            taskId: selectedTaskId,
            planId: currentPlanId ?? null,
          });
        }}
        onLoadingChange={setExecuteButtonLoading}
        currentPlanId={currentPlanId}
        selectedTaskId={selectedTaskId}
        currentSessionId={currentSessionId}
        activeTask={activeTask}
        isTaskDrawerOpen={isTaskDrawerOpen}
        taskMap={taskMap}
        handleDependencyClick={handleDependencyClick}
        refetchPlanTasks={() => { void refetchPlanTasks(); }}
        refetchTaskResult={() => { void refetchTaskResult(); }}
      />

      <TodoListPanel
        open={todoListOpen}
        onClose={() => setTodoListOpen(false)}
        planId={currentPlanId}
        targetTaskId={selectedTaskId}
        onTaskClick={handleDependencyClick}
      />
    </Drawer>
  );
};

export default TaskDetailDrawer;
