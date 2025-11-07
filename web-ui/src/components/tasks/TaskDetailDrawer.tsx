import React, { useCallback, useEffect, useMemo } from 'react';
import {
  App as AntdApp,
  Button,
  Collapse,
  Descriptions,
  Drawer,
  Empty,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import { ReloadOutlined, CopyOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { planTreeApi } from '@api/planTree';
import { usePlanTasks } from '@hooks/usePlans';
import { useChatStore } from '@store/chat';
import { useTasksStore } from '@store/tasks';
import ToolResultCard from '@components/chat/ToolResultCard';
import type { PlanResultItem, PlanTaskNode, PlanSyncEventDetail, ToolResultPayload } from '@/types';
import { shouldHandlePlanSyncEvent } from '@utils/planSyncEvents';

const { Paragraph, Text, Title } = Typography;

const statusColorMap: Record<string, string> = {
  pending: 'gold',
  running: 'processing',
  completed: 'green',
  failed: 'red',
  skipped: 'default',
};

const statusLabelMap: Record<string, string> = {
  pending: '待执行',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  skipped: '已跳过',
};

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

  const { currentPlanId, recentToolResults } = useChatStore((state) => {
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
        const key = `${payload.name ?? ''}::${payload.summary ?? ''}::${
          payload.result?.query ?? ''
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

  const handleCopyJSON = useCallback(async (value: unknown, successMessage: string) => {
    try {
      const text = JSON.stringify(value, null, 2);
      await navigator.clipboard.writeText(text);
      message.success(successMessage);
    } catch (error) {
      console.warn('复制失败', error);
      message.error('复制失败，请手动复制内容');
    }
  }, []);

  const handleCopyTask = useCallback(() => {
    if (!activeTask) {
      return;
    }
    const payload = {
      task: activeTask,
      result: taskResult ?? cachedResult ?? null,
    };
    void handleCopyJSON(payload, '任务详情已复制');
  }, [activeTask, taskResult, cachedResult, handleCopyJSON]);

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

  const renderDependencies = () => {
    if (!activeTask?.dependencies || activeTask.dependencies.length === 0) {
      return <Text type="secondary">无依赖</Text>;
    }
    return (
      <Space wrap size={6}>
        {activeTask.dependencies.map((dep) => (
          <Button
            key={dep}
            size="small"
            type="link"
            onClick={() => handleDependencyClick(dep)}
          >
            任务 #{dep}
          </Button>
        ))}
      </Space>
    );
  };

  const renderContextSections = () => {
    const sections = activeTask?.context_sections;
    if (!Array.isArray(sections) || sections.length === 0) {
      return null;
    }
    const items = sections.map((section, index) => {
      const title =
        typeof section?.title === 'string' && section.title.trim().length > 0
          ? section.title
          : `片段 ${index + 1}`;
      const content =
        typeof section?.content === 'string'
          ? section.content
          : JSON.stringify(section, null, 2);
      return {
        key: String(index),
        label: title,
        children: <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{content}</Paragraph>,
      };
    });
    return <Collapse size="small" bordered={false} items={items} />;
  };

  const renderExecutionResult = () => {
    if (resultLoading && !taskResult && !cachedResult) {
      return (
        <div style={{ padding: '12px 0' }}>
          <Spin tip="加载执行结果..." />
        </div>
      );
    }

    const result = taskResult ?? cachedResult;
    if (!result) {
      return <Text type="secondary">暂无执行结果</Text>;
    }

    return (
      <Space direction="vertical" size="small" style={{ width: '100%' }}>
        {result.status && (
          <Tag color={statusColorMap[result.status] ?? 'default'}>
            {statusLabelMap[result.status] ?? result.status}
          </Tag>
        )}
        {result.content && (
          <Paragraph style={{ whiteSpace: 'pre-wrap' }} copyable>
            {result.content}
          </Paragraph>
        )}
        {Array.isArray(result.notes) && result.notes.length > 0 && (
          <Collapse
            size="small"
            items={[
              {
                key: 'notes',
                label: `备注 (${result.notes.length})`,
                children: (
                  <Space direction="vertical">
                    {result.notes.map((note, idx) => (
                      <Paragraph key={idx} style={{ whiteSpace: 'pre-wrap', marginBottom: 8 }}>
                        {note}
                      </Paragraph>
                    ))}
                  </Space>
                ),
              },
            ]}
          />
        )}
        {result.metadata && Object.keys(result.metadata).length > 0 && (
          <Paragraph
            code
            copyable
            style={{ maxHeight: 200, overflow: 'auto' }}
          >
            {JSON.stringify(result.metadata, null, 2)}
          </Paragraph>
        )}
      </Space>
    );
  };

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
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <section>
            <Title level={5}>基础信息</Title>
            <Descriptions column={1} bordered size="small">
              <Descriptions.Item label="任务类型">
                {activeTask.task_type ?? '未知'}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusColorMap[activeTask.status ?? 'pending'] ?? 'default'}>
                  {statusLabelMap[activeTask.status ?? 'pending'] ??
                    activeTask.status ??
                    '未知'}
                </Tag>
              </Descriptions.Item>
              {activeTask.parent_id ? (
                <Descriptions.Item label="父任务">
                  <Button
                    type="link"
                    size="small"
                    onClick={() => handleDependencyClick(activeTask.parent_id!)}
                  >
                    任务 #{activeTask.parent_id}
                  </Button>
                </Descriptions.Item>
              ) : (
                <Descriptions.Item label="父任务">无</Descriptions.Item>
              )}
              <Descriptions.Item label="层级深度">
                {activeTask.depth ?? 0}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {activeTask.created_at ? new Date(activeTask.created_at).toLocaleString() : '未知'}
              </Descriptions.Item>
              <Descriptions.Item label="更新时间">
                {activeTask.updated_at ? new Date(activeTask.updated_at).toLocaleString() : '未知'}
              </Descriptions.Item>
            </Descriptions>
          </section>

          <section>
            <Title level={5}>任务内容</Title>
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              <div>
                <Text type="secondary">指令</Text>
                <Paragraph
                  style={{ whiteSpace: 'pre-wrap' }}
                  copyable
                  ellipsis={{ rows: 6, expandable: true, symbol: '展开' }}
                >
                  {activeTask.instruction || '暂无描述'}
                </Paragraph>
              </div>
              <div>
                <Text type="secondary">依赖任务</Text>
                {renderDependencies()}
              </div>
            </Space>
          </section>

          <section>
            <Title level={5}>上下文信息</Title>
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              {activeTask.context_combined ? (
                <Paragraph
                  style={{ whiteSpace: 'pre-wrap' }}
                  copyable
                  ellipsis={{ rows: 6, expandable: true, symbol: '展开' }}
                >
                  {activeTask.context_combined}
                </Paragraph>
              ) : (
                <Text type="secondary">暂无上下文摘要</Text>
              )}
              {renderContextSections()}
              {activeTask.context_meta && Object.keys(activeTask.context_meta).length > 0 && (
                <Paragraph
                  code
                  copyable
                  style={{ maxHeight: 200, overflow: 'auto' }}
                >
                  {JSON.stringify(activeTask.context_meta, null, 2)}
                </Paragraph>
              )}
            </Space>
          </section>

          {recentToolResults.length > 0 && (
            <section>
              <Title level={5}>近期搜索摘要</Title>
              <Space direction="vertical" size="small" style={{ width: '100%' }}>
                {recentToolResults.map((result, index) => (
                  <ToolResultCard
                    key={`${result.name ?? 'tool'}_${index}`}
                    payload={result}
                    defaultOpen={index === 0}
                  />
                ))}
              </Space>
            </section>
          )}

          <section>
            <Title level={5}>元数据</Title>
            {activeTask.metadata && Object.keys(activeTask.metadata).length > 0 ? (
              <Paragraph
                code
                copyable
                style={{ maxHeight: 200, overflow: 'auto' }}
              >
                {JSON.stringify(activeTask.metadata, null, 2)}
              </Paragraph>
            ) : (
              <Text type="secondary">暂无元数据信息</Text>
            )}
          </section>

          <section>
            <Title level={5}>执行结果</Title>
            {renderExecutionResult()}
          </section>
        </Space>
      )}
    </Drawer>
  );
};

export default TaskDetailDrawer;
