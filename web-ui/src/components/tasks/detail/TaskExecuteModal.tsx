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
import { statusColorMap } from './constants';
import { translate, useT } from '@/i18n/useT';
import type { TFn } from '@/i18n/useT';
import { planStatusLabel } from '@/i18n/planStatusLabels';
import { resolveTaskName, resolveTaskStatus } from './TaskDetailSections';
import { useCallbackScope } from './useCallbackScope';

const { Text } = Typography;
const executionStateColorMap: Record<string, string> = {
  completed: 'success',
  running: 'processing',
  blocked: 'error',
  failed: 'error',
  ready: 'blue',
};
const executionStateLabel = (state: string, t: TFn): string => {
  switch (state) {
    case 'completed':
      return t('status.completed');
    case 'running':
      return t('status.running');
    case 'blocked':
      return t('status.blocked');
    case 'failed':
      return t('status.failed');
    case 'ready':
      return t('taskExecute.ready');
    default:
      return state;
  }
};

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
  const t = useT();
  const [dependencyPlanLoading, setDependencyPlanLoading] = useState(false);
  const [dependencyPlan, setDependencyPlan] = useState<DependencyPlanResponse | null>(null);
  const [executeLoading, setExecuteLoading] = useState(false);
  const [executeJobId, setExecuteJobId] = useState<string | null>(null);
  const [executeError, setExecuteError] = useState<string | null>(null);
  const captureScope = useCallbackScope(`${currentSessionId}:${currentPlanId}:${selectedTaskId}:${open}`);

  useEffect(() => {
  onLoadingChange?.(dependencyPlanLoading || executeLoading);
  }, [dependencyPlanLoading, executeLoading, onLoadingChange]);

  useEffect(() => {
  if (open && currentPlanId && selectedTaskId) {
  const isCurrentScope = captureScope();
  setExecuteJobId(null);
  setExecuteError(null);
  setExecuteLoading(false);
  setDependencyPlan(null);
  setDependencyPlanLoading(true);
  planTreeApi.getTaskDependencyPlan(currentPlanId, selectedTaskId, {
  include_dependencies: true,
  include_subtasks: true,
  })
  .then((plan) => { if (isCurrentScope()) setDependencyPlan(plan); })
  .catch((err: any) => { if (isCurrentScope()) setExecuteError(err?.message || translate('taskExecute.depPlanFailed')); })
  .finally(() => { if (isCurrentScope()) setDependencyPlanLoading(false); });
  }
  }, [open, currentPlanId, selectedTaskId, currentSessionId, captureScope]);

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
  message.error(translate('taskExecute.cycleBlocked'));
  return;
  }
  if (dependencyPlan?.running_dependencies?.length) {
  message.warning(translate('taskExecute.depsRunning'));
  return;
  }

  const isCurrentScope = captureScope();
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
  if (!isCurrentScope()) return;
  if (!resp.success) {
  setExecuteError(resp.message || translate('jobLog.executionFailed'));
  setDependencyPlan(resp.dependency_plan ?? dependencyPlan);
  return;
  }
  const jobId = resp.job?.job_id;
  if (!jobId) {
  setExecuteError(translate('taskExecute.noJobId'));
  return;
  }
  setExecuteJobId(jobId);
  onExecutionStarted?.(jobId);
  message.success(translate('taskExecute.started'));
  void refetchPlanTasks();
  void refetchTaskResult();
  } catch (err: any) {
  if (isCurrentScope()) setExecuteError(err?.message || translate('jobLog.executionFailed'));
  } finally {
  if (isCurrentScope()) setExecuteLoading(false);
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
  captureScope,
  ]);

  const executionItems = dependencyPlan?.execution_items ?? [];
  const executionStateCounts = executionItems.reduce<Record<string, number>>((acc, item) => {
    const key = item.execution_state || 'pending';
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});

  return (
  <Modal
  open={open}
  title={executeJobId ? t('taskExecute.titleProgress') : t('taskExecute.titleExecute')}
  onCancel={handleClose}
  width={720}
  footer={
  executeJobId
  ? [
  <Button key="close" onClick={handleClose}>
  {t('common.close')}
  </Button>,
  ]
  : [
  <Button key="cancel" onClick={handleClose}>
  {t('common.cancel')}
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
  ? t('taskExecute.runWithDeps')
  : t('taskExecute.runTask')}
  </Button>,
  ]
  }
  >
  {dependencyPlanLoading ? (
  <div style={{ padding: '16px 0' }}>
  <Spin tip={t('taskExecute.loadingPrereqs')} />
  </div>
  ) : executeJobId ? (
  <Space direction="vertical" size="small" style={{ width: '100%' }}>
  <Text type="secondary">{t('taskExecute.deepThinkActive')}</Text>
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
  message={t('taskExecute.requestFailed')}
  description={executeError}
  showIcon
  />
  )}

  {dependencyPlan?.cycle_detected && (
  <Alert
  type="error"
  message={t('taskExecute.cycleTitle')}
  description={t('taskExecute.cycleDesc')}
  showIcon
  />
  )}

  {dependencyPlan && !dependencyPlan.cycle_detected && (
  <>
  <Text type="secondary">
  {t('taskExecute.closure', { closure: dependencyPlan.closure_dependencies.length, missing: dependencyPlan.missing_dependencies.length })}
  </Text>

  {dependencyPlan.running_dependencies.length > 0 && (
  <Alert
  type="warning"
  message={t('taskExecute.stillRunningTitle')}
  description={t('taskExecute.stillRunningDesc')}
  showIcon
  />
  )}

  <div>
  <Text type="secondary">{t('taskExecute.missingDeps')}</Text>
  {dependencyPlan.missing_dependencies.length === 0 ? (
  <div style={{ marginTop: 6 }}>
  <Text>{t('taskExecute.prereqsMet')}</Text>
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
  {planStatusLabel(t, dep.status)}
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
   <Text type="secondary">{t('taskExecute.order')}</Text>
   {executionItems.length > 0 && (
   <div style={{ marginTop: 6 }}>
   <Text type="secondary">
   {t('taskExecute.steps', { steps: executionItems.length, ready: executionStateCounts.ready ?? 0, running: executionStateCounts.running ?? 0, blocked: executionStateCounts.blocked ?? 0, completed: executionStateCounts.completed ?? 0 })}
   </Text>
   </div>
   )}
   <Space
   direction="vertical"
   size={6}
   style={{ width: '100%', marginTop: 6 }}
   >
   {executionItems.length > 0
   ? executionItems.map((item) => {
   const tid = item.task_id;
   const isTarget = item.is_target;
   return (
   <Space
   key={`${tid}_${item.step_index}`}
   direction="vertical"
   size={2}
   style={{ width: '100%', padding: '8px 10px', border: '1px solid var(--border-subtle)', borderRadius: 8 }}
   >
   <Space size={8} wrap>
   <Text type="secondary" style={{ width: 22 }}>
   {item.step_index}.
   </Text>
   <Tag color={executionStateColorMap[item.execution_state] ?? 'default'}>
   {executionStateLabel(item.execution_state, t)}
   </Tag>
   <Tag color={statusColorMap[item.status] ?? 'default'}>
   {planStatusLabel(t, item.status)}
   </Tag>
   <Button
   size="small"
   type={isTarget ? 'primary' : 'link'}
   onClick={() => handleDependencyClick(tid)}
   >
   #{tid} {item.name}
   </Button>
   {isTarget && <Text type="secondary">{t('taskExecute.targetTask')}</Text>}
   </Space>
   {item.depends_on.length > 0 && (
   <Text type="secondary">
   {item.unmet_dependencies.length > 0
   ? t('taskExecute.dependsOnUnmet', { deps: item.depends_on.map((dep) => `#${dep}`).join(', '), unmet: item.unmet_dependencies.map((dep) => `#${dep}`).join(', ') })
   : t('taskExecute.dependsOn', { deps: item.depends_on.map((dep) => `#${dep}`).join(', ') })}
   </Text>
   )}
   {item.instruction && (
   <Text type="secondary">
   {item.instruction.length > 220 ? `${item.instruction.slice(0, 217)}...` : item.instruction}
   </Text>
   )}
   {item.expected_deliverables.length > 0 && (
   <Text type="secondary">
   {t('taskExecute.expectedOutputs', { list: `${item.expected_deliverables.slice(0, 4).join(', ')}${item.expected_deliverables.length > 4 ? ', ...' : ''}` })}
   </Text>
   )}
   </Space>
   );
   })
   : (dependencyPlan.execution_order ?? []).map((tid, index) => {
   const isTarget = tid === selectedTaskId;
   const status = resolveTaskStatus(tid, taskMap, dependencyPlan);
   return (
  <Space key={`${tid}_${index}`} size={8} wrap>
  <Text type="secondary" style={{ width: 22 }}>
  {index + 1}.
  </Text>
  <Tag color={statusColorMap[status] ?? 'default'}>
  {planStatusLabel(t, status)}
  </Tag>
  <Button
  size="small"
  type={isTarget ? 'primary' : 'link'}
  onClick={() => handleDependencyClick(tid)}
  >
  #{tid} {resolveTaskName(tid, selectedTaskId, activeTask, taskMap, dependencyPlan)}
  </Button>
   {isTarget && <Text type="secondary">{t('taskExecute.targetTask')}</Text>}
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
