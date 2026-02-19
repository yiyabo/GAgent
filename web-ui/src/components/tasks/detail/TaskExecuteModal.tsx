import React, { useCallback, useEffect, useState } from 'react';
import {
  App as AntdApp,
  Alert,
  Button,
  Modal,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import { planTreeApi } from '@api/planTree';
import JobLogPanel from '@components/chat/JobLogPanel';
import type { DependencyPlanResponse, PlanTaskNode } from '@/types';
import { statusColorMap, statusLabelMap } from './constants';
import { resolveTaskName, resolveTaskStatus } from './TaskDetailSections';

const { Text } = Typography;

interface TaskExecuteModalProps {
  open: boolean;
  onClose: () => void;
  onLoadingChange?: (loading: boolean) => void;
  currentPlanId: number | null;
  selectedTaskId: number | null;
  currentSessionId: string | null;
  activeTask: PlanTaskNode | null;
  isTaskDrawerOpen: boolean;
  taskMap: Map<number, PlanTaskNode>;
  handleDependencyClick: (depId: number) => void;
  refetchPlanTasks: () => void;
  refetchTaskResult: () => void;
}

const TaskExecuteModal: React.FC<TaskExecuteModalProps> = ({
  open,
  onClose,
  onLoadingChange,
  currentPlanId,
  selectedTaskId,
  currentSessionId,
  activeTask,
  isTaskDrawerOpen,
  taskMap,
  handleDependencyClick,
  refetchPlanTasks,
  refetchTaskResult,
}) => {
  const { message } = AntdApp.useApp();
  const [dependencyPlanLoading, setDependencyPlanLoading] = useState(false);
  const [dependencyPlan, setDependencyPlan] = useState<DependencyPlanResponse | null>(null);
  const [executeLoading, setExecuteLoading] = useState(false);
  const [executeJobId, setExecuteJobId] = useState<string | null>(null);
  const [executeError, setExecuteError] = useState<string | null>(null);

  useEffect(() => {
    onLoadingChange?.(dependencyPlanLoading || executeLoading);
  }, [dependencyPlanLoading, executeLoading, onLoadingChange]);

  useEffect(() => {
    if (open && currentPlanId && selectedTaskId) {
      setExecuteJobId(null);
      setExecuteError(null);
      setDependencyPlan(null);
      setDependencyPlanLoading(true);
      planTreeApi.getTaskDependencyPlan(currentPlanId, selectedTaskId)
        .then((plan) => setDependencyPlan(plan))
        .catch((err: any) => setExecuteError(err?.message || '获取依赖信息失败'))
        .finally(() => setDependencyPlanLoading(false));
    }
  }, [open, currentPlanId, selectedTaskId]);

  const handleClose = useCallback(() => {
    setExecuteJobId(null);
    setExecuteError(null);
    setDependencyPlan(null);
    setDependencyPlanLoading(false);
    setExecuteLoading(false);
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (!isTaskDrawerOpen && open) {
      handleClose();
    }
  }, [open, handleClose, isTaskDrawerOpen]);

  const handleExecuteWithDeps = useCallback(async () => {
    if (!currentPlanId || !selectedTaskId) {
      return;
    }
    if (dependencyPlan?.cycle_detected) {
      message.error('检测到循环依赖，无法执行。请先修正依赖关系');
      return;
    }
    if (dependencyPlan?.running_dependencies?.length) {
      message.warning('存在依赖任务正在执行中，请稍后再试');
      return;
    }

    setExecuteLoading(true);
    setExecuteError(null);
    try {
      const resp = await planTreeApi.executeTaskWithDeps(currentPlanId, selectedTaskId, {
        include_dependencies: true,
        async_mode: true,
        session_id: currentSessionId ?? undefined,
      });
      if (!resp.success) {
        setExecuteError(resp.message || '执行提交失败');
        setDependencyPlan(resp.dependency_plan ?? dependencyPlan);
        return;
      }
      const jobId = resp.job?.job_id;
      if (!jobId) {
        setExecuteError('执行已提交，但未返回 job_id');
        return;
      }
      setExecuteJobId(jobId);
      message.success('已提交后台执行');
      void refetchPlanTasks();
      void refetchTaskResult();
    } catch (err: any) {
      setExecuteError(err?.message || '执行提交失败');
    } finally {
      setExecuteLoading(false);
    }
  }, [
    currentPlanId,
    currentSessionId,
    dependencyPlan,
    message,
    refetchPlanTasks,
    refetchTaskResult,
    selectedTaskId,
  ]);

  return (
    <Modal
      open={open}
      title={executeJobId ? '执行进度' : '执行任务'}
      onCancel={handleClose}
      width={720}
      footer={
        executeJobId
          ? [
              <Button key="close" onClick={handleClose}>
                关闭
              </Button>,
            ]
          : [
              <Button key="cancel" onClick={handleClose}>
                取消
              </Button>,
              <Button
                key="run"
                type="primary"
                onClick={handleExecuteWithDeps}
                loading={executeLoading}
                disabled={
                  dependencyPlanLoading ||
                  !dependencyPlan ||
                  Boolean(dependencyPlan?.cycle_detected) ||
                  Boolean(dependencyPlan?.running_dependencies?.length)
                }
              >
                {dependencyPlan?.missing_dependencies?.length
                  ? '按顺序一键执行'
                  : '执行该任务'}
              </Button>,
            ]
      }
    >
      {dependencyPlanLoading ? (
        <div style={{ padding: '16px 0' }}>
          <Spin tip="获取依赖信息..." />
        </div>
      ) : executeJobId ? (
        <JobLogPanel
          jobId={executeJobId}
          targetTaskName={activeTask?.name ?? null}
          planId={currentPlanId}
          jobType="plan_execute"
        />
      ) : (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          {executeError && (
            <Alert
              type="error"
              message="无法执行"
              description={executeError}
              showIcon
            />
          )}

          {dependencyPlan?.cycle_detected && (
            <Alert
              type="error"
              message="检测到循环依赖"
              description="请先修正依赖关系后再执行该任务。"
              showIcon
            />
          )}

          {dependencyPlan && !dependencyPlan.cycle_detected && (
            <>
              <Text type="secondary">
                依赖闭包：{dependencyPlan.closure_dependencies.length} 个；未满足依赖：
                {dependencyPlan.missing_dependencies.length} 个
              </Text>

              {dependencyPlan.running_dependencies.length > 0 && (
                <Alert
                  type="warning"
                  message="依赖任务执行中"
                  description="存在依赖任务正在执行中。请等待其完成后再执行该任务。"
                  showIcon
                />
              )}

              <div>
                <Text type="secondary">未满足依赖</Text>
                {dependencyPlan.missing_dependencies.length === 0 ? (
                  <div style={{ marginTop: 6 }}>
                    <Text>无</Text>
                  </div>
                ) : (
                  <Space
                    direction="vertical"
                    size={6}
                    style={{ width: '100%', marginTop: 6 }}
                  >
                    {dependencyPlan.missing_dependencies.map((dep) => (
                      <Space key={dep.id} size={8} wrap>
                        <Tag color={statusColorMap[dep.status] ?? 'default'}>
                          {statusLabelMap[dep.status] ?? dep.status}
                        </Tag>
                        <Button
                          size="small"
                          type="link"
                          onClick={() => handleDependencyClick(dep.id)}
                        >
                          #{dep.id} {dep.name}
                        </Button>
                      </Space>
                    ))}
                  </Space>
                )}
              </div>

              <div>
                <Text type="secondary">推荐执行顺序</Text>
                <Space
                  direction="vertical"
                  size={6}
                  style={{ width: '100%', marginTop: 6 }}
                >
                  {(dependencyPlan.execution_order ?? []).map((tid, index) => {
                    const isTarget = tid === selectedTaskId;
                    const status = resolveTaskStatus(tid, taskMap, dependencyPlan);
                    return (
                      <Space key={`${tid}_${index}`} size={8} wrap>
                        <Text type="secondary" style={{ width: 22 }}>
                          {index + 1}.
                        </Text>
                        <Tag color={statusColorMap[status] ?? 'default'}>
                          {statusLabelMap[status] ?? status}
                        </Tag>
                        <Button
                          size="small"
                          type={isTarget ? 'primary' : 'link'}
                          onClick={() => handleDependencyClick(tid)}
                        >
                          #{tid} {resolveTaskName(tid, selectedTaskId, activeTask, taskMap, dependencyPlan)}
                        </Button>
                        {isTarget && <Text type="secondary">（目标任务）</Text>}
                      </Space>
                    );
                  })}
                </Space>
              </div>
            </>
          )}
        </Space>
      )}
    </Modal>
  );
};

export default TaskExecuteModal;
