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
import { ReloadOutlined, CopyOutlined, PlayCircleOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { planTreeApi } from '@api/planTree';
import { usePlanTasks } from '@hooks/usePlans';
import { useChatStore } from '@store/chat';
import { useTasksStore } from '@store/tasks';
import type {
  PlanResultItem,
  PlanTaskNode,
  PlanSyncEventDetail,
  ToolResultPayload,
} from '@/types';
import { shouldHandlePlanSyncEvent } from '@utils/planSyncEvents';
import { TaskDrawerContent, copyJsonToClipboard } from './TaskDetailSections';
import TaskExecuteModal from './TaskExecuteModal';

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
        throw new Error('缺少计划或任务信息，无法获取执行结果');
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
    void copyJsonToClipboard({ task: activeTask, result: taskResult ?? cachedResult ?? null }, '任务详情已复制', message);
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
      message.error('缺少计划或任务信息，无法执行任务');
      return;
    }
    setExecuteModalOpen(true);
  }, [currentPlanId, message, selectedTaskId]);

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
              <Text type="secondary">任务 ID: {activeTask.id}</Text>
            </Space>
          )
          : '任务详情'
      }
      open={isTaskDrawerOpen}
      onClose={closeTaskDrawer}
      destroyOnClose={false}
      maskClosable
      extra={
        <Space>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            onClick={handleOpenExecuteModal}
            disabled={!currentPlanId || !selectedTaskId}
            loading={executeButtonLoading}
          >
            执行
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={handleRefresh}
            disabled={!currentPlanId || !selectedTaskId}
            loading={tasksLoading || resultLoading}
          >
            刷新
          </Button>
          <Button
            icon={<CopyOutlined />}
            onClick={handleCopyTask}
            disabled={!activeTask}
          >
            复制
          </Button>
        </Space>
      }
    >
      {!currentPlanId ? (
        <Empty description="当前会话尚未绑定计划" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : !selectedTaskId ? (
        <Empty description="请选择一个任务查看详情" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : tasksLoading && !activeTask ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '32px 0' }}>
          <Spin tip="加载任务信息..." />
        </div>
      ) : !activeTask ? (
        <Empty description="未找到该任务，可能已被删除" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <TaskDrawerContent
          activeTask={activeTask}
          handleDependencyClick={handleDependencyClick}
          recentToolResults={recentToolResults}
          resultLoading={resultLoading}
          taskResult={taskResult}
          cachedResult={cachedResult}
        />
      )}

      <TaskExecuteModal
        open={executeModalOpen}
        onClose={() => setExecuteModalOpen(false)}
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
    </Drawer>
  );
};

export default TaskDetailDrawer;
