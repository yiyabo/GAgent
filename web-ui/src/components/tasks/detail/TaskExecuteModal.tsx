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
  onExecutionStarted?: (jobId: string) => void;
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
  onExecutionStarted,
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
  planTreeApi.getTaskDependencyPlan(currentPlanId, selectedTaskId, {
  include_dependencies: true,
  include_subtasks: true,
  })
  .then((plan) => setDependencyPlan(plan))
  .catch((err: any) => setExecuteError(err?.message || 'Failed to load dependency plan'))
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
  message.error('Execution blocked due to dependency cycle. Resolve dependencies first.');
  return;
  }
  if (dependencyPlan?.running_dependencies?.length) {
  message.warning('Some dependencies are still running. Please wait and retry.');
  return;
  }

  setExecuteLoading(true);
  setExecuteError(null);
  try {
  const resp = await planTreeApi.executeTaskWithDeps(currentPlanId, selectedTaskId, {
  include_dependencies: true,
  include_subtasks: true,
  deep_think: true,
  async_mode: true,
  session_id: currentSessionId ?? undefined,
  });
  if (!resp.success) {
  setExecuteError(resp.message || 'Execution failed');
  setDependencyPlan(resp.dependency_plan ?? dependencyPlan);
  return;
  }
  const jobId = resp.job?.job_id;
  if (!jobId) {
  setExecuteError('Execution started, but no job_id was returned.');
  return;
  }
  setExecuteJobId(jobId);
  onExecutionStarted?.(jobId);
  message.success('Task execution started in background.');
  void refetchPlanTasks();
  void refetchTaskResult();
  } catch (err: any) {
  setExecuteError(err?.message || 'Execution failed');
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
  onExecutionStarted,
  ]);

  return (
  <Modal
  open={open}
  title={executeJobId ? 'Execution Progress' : 'Execute Task'}
  onCancel={handleClose}
  width={720}
  footer={
  executeJobId
  ? [
  <Button key="close" onClick={handleClose}>
  Close
  </Button>,
  ]
  : [
  <Button key="cancel" onClick={handleClose}>
  Cancel
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
  ? 'Execute with dependencies'
  : 'Execute task'}
  </Button>,
  ]
  }
  >
  {dependencyPlanLoading ? (
  <div style={{ padding: '16px 0' }}>
  <Spin tip="Loading execution prerequisites..." />
  </div>
  ) : executeJobId ? (
  <Space direction="vertical" size="small" style={{ width: '100%' }}>
  <Text type="secondary">Deep Think execution is active. You can monitor the thinking timeline in real time.</Text>
  <JobLogPanel
  jobId={executeJobId}
  targetTaskName={activeTask?.name ?? null}
  planId={currentPlanId}
  jobType="plan_execute"
  />
  </Space>
  ) : (
  <Space direction="vertical" size="middle" style={{ width: '100%' }}>
  {executeError && (
  <Alert
  type="error"
  message="Execution failed"
  description={executeError}
  showIcon
  />
  )}

  {dependencyPlan?.cycle_detected && (
  <Alert
  type="error"
  message="Dependency cycle detected"
  description="Resolve cyclic dependencies before executing this task."
  showIcon
  />
  )}

  {dependencyPlan && !dependencyPlan.cycle_detected && (
  <>
  <Text type="secondary">
  Dependency closure: {dependencyPlan.closure_dependencies.length}; missing prerequisites:{" "}
  {dependencyPlan.missing_dependencies.length}
  </Text>

  {dependencyPlan.running_dependencies.length > 0 && (
  <Alert
  type="warning"
  message="Dependencies are still running"
  description="Wait for running dependencies to finish before executing this task."
  showIcon
  />
  )}

  <div>
  <Text type="secondary">Missing dependencies:</Text>
  {dependencyPlan.missing_dependencies.length === 0 ? (
  <div style={{ marginTop: 6 }}>
  <Text>All dependencies are satisfied.</Text>
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
  <Text type="secondary">Execution order:</Text>
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
  {isTarget && <Text type="secondary">(target task)</Text>}
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
