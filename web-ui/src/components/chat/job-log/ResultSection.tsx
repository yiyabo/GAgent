import * as React from 'react';
import { Space, Typography, Divider, Progress } from 'antd';
import { FileTextOutlined } from '@ant-design/icons';
import type { JobLogEvent } from '@/types';
import { FINAL_STATUSES, toNumber } from './constants';

const { Text } = Typography;

interface ResultSummaryProps {
  result: Record<string, any> | null;
  jobType: string;
}

export const ResultSummary: React.FC<ResultSummaryProps> = ({ result, jobType }) => {
  if (!result) return null;
  if (jobType === 'plan_decompose') {
    const createdTasks = Array.isArray(result.created_tasks) ? result.created_tasks : [];
    const stoppedReason = result.stopped_reason;
    return (
      <div style={{ marginTop: 12 }}>
        <Divider plain style={{ margin: '12px 0' }}>
          Execution Results
        </Divider>
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          <Space size="small">
            <FileTextOutlined />
            <Text>New subtasks: {createdTasks.length}</Text>
          </Space>
          {stoppedReason && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              Stop reason: {stoppedReason}
            </Text>
          )}
        </Space>
      </div>
    );
  }

  if (jobType === 'chat_action') {
    const steps = Array.isArray(result.steps) ? (result.steps as any[]) : [];
    const failedSteps = steps.filter((step) => step?.success === false);
    const succeededSteps = steps.filter((step) => step?.success === true);
    const firstFailure = failedSteps[0];
    return (
      <div style={{ marginTop: 12 }}>
        <Divider plain style={{ margin: '12px 0' }}>
          Action Summary
        </Divider>
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          <Space size="small">
            <FileTextOutlined />
            <Text>Total actions: {steps.length}</Text>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>
            Success: {succeededSteps.length}, Failed: {failedSteps.length}
          </Text>
          {firstFailure && (
            <Text type="danger" style={{ fontSize: 12 }}>
              First failed action: {firstFailure?.action?.name ?? '-'} - {firstFailure?.message ?? 'Execution failed'}
            </Text>
          )}
        </Space>
      </div>
    );
  }

  if (jobType === 'plan_execute') {
    const executed = Array.isArray((result as any).executed_task_ids)
      ? ((result as any).executed_task_ids as any[])
      : [];
    const failed = Array.isArray((result as any).failed_task_ids)
      ? ((result as any).failed_task_ids as any[])
      : [];
    const skipped = Array.isArray((result as any).skipped_task_ids)
      ? ((result as any).skipped_task_ids as any[])
      : [];
    const order = Array.isArray((result as any).execution_order)
      ? ((result as any).execution_order as any[])
      : [];
    const total = order.length || executed.length + failed.length + skipped.length;
    const targetTaskId = (result as any).target_task_id;
    const firstFailed = failed.length ? failed[0] : null;
    const firstSkipped = skipped.length ? skipped[0] : null;

    return (
      <div style={{ marginTop: 12 }}>
        <Divider plain style={{ margin: '12px 0' }}>
          Execution Summary
        </Divider>
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          <Space size="small">
            <FileTextOutlined />
            <Text>Step count: {total}</Text>
          </Space>
          {typeof targetTaskId === 'number' && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              Target task: #{targetTaskId}
            </Text>
          )}
          <Text type="secondary" style={{ fontSize: 12 }}>
            Success: {executed.length}, Failed: {failed.length}, Skipped: {skipped.length}
          </Text>
          {firstFailed != null && (
            <Text type="danger" style={{ fontSize: 12 }}>
              First failed task: #{String(firstFailed)}
            </Text>
          )}
          {firstFailed == null && firstSkipped != null && (
            <Text type="warning" style={{ fontSize: 12 }}>
              First skipped task: #{String(firstSkipped)}
            </Text>
          )}
        </Space>
      </div>
    );
  }

  return null;
};

interface ProgressBarProps {
  jobType: string;
  status: string;
  logs: JobLogEvent[];
  stats: Record<string, any>;
  jobParams: Record<string, any>;
}

export const ProgressBar: React.FC<ProgressBarProps> = ({ jobType, status, logs, stats, jobParams }) => {
  const progressContext = React.useMemo(() => {
    if (jobType !== 'plan_decompose') return null;
    const totalBudget =
      toNumber(jobParams?.node_budget) ??
      toNumber(stats?.node_budget);
    let remainingBudget: number | null = null;
    let queueRemaining: number | null = null;
    for (let idx = logs.length - 1; idx >= 0; idx -= 1) {
      const metadata = logs[idx]?.metadata;
      if (!metadata || typeof metadata !== 'object') continue;
      if (remainingBudget === null) {
        remainingBudget = toNumber((metadata as Record<string, any>).budget_remaining);
      }
      if (queueRemaining === null) {
        queueRemaining = toNumber((metadata as Record<string, any>).queue_remaining);
      }
      if (remainingBudget !== null || queueRemaining !== null) {
        break;
      }
    }
    const consumedFromStats = toNumber(stats?.consumed_budget);
    const consumedBudget =
      consumedFromStats ??
      (totalBudget !== null && remainingBudget !== null
        ? Math.max(0, totalBudget - remainingBudget)
        : null);
    const percentRaw =
      totalBudget !== null && totalBudget > 0 && consumedBudget !== null
        ? Math.round((consumedBudget / totalBudget) * 100)
        : null;
    const percent =
      percentRaw !== null ? Math.max(0, Math.min(100, percentRaw)) : null;
    return {
      totalBudget,
      consumedBudget,
      remainingBudget,
      queueRemaining,
      percent,
    };
  }, [jobParams, jobType, logs, stats]);

  if (jobType !== 'plan_decompose') return null;
  const isFinal = FINAL_STATUSES.has(status);
  const percent =
    progressContext?.percent !== null && progressContext?.percent !== undefined
      ? progressContext.percent
      : isFinal
        ? 100
        : 0;
  const progressStatus =
    status === 'failed' ? 'exception' : isFinal ? 'success' : 'active';
  const showBudget =
    progressContext?.totalBudget !== null &&
    progressContext?.totalBudget !== undefined &&
    progressContext?.consumedBudget !== null &&
    progressContext?.consumedBudget !== undefined;
  const detailParts: string[] = [];
  if (showBudget) {
    detailParts.push(
      `Processed ${Math.max(0, Math.round(progressContext!.consumedBudget!))}/${Math.round(
        progressContext!.totalBudget!
      )}`
    );
  }
  if (
    progressContext?.queueRemaining !== null &&
    progressContext?.queueRemaining !== undefined
  ) {
    detailParts.push(`Queue remaining ${Math.max(0, Math.round(progressContext.queueRemaining))}`);
  }
  const detailText = detailParts.length ? detailParts.join(' · ') : null;
  return (
    <div style={{ width: '100%' }}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        Decomposition Progress
      </Text>
      <Progress
        percent={percent}
        status={progressStatus as any}
        size="small"
        showInfo
        format={() => {
          if (progressContext?.percent !== null && progressContext?.percent !== undefined) {
            return `${percent}%`;
          }
          if (status === 'failed') return 'Failed';
          if (isFinal) return 'Completed';
          return 'Estimating';
        }}
      />
      {detailText && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {detailText}
        </Text>
      )}
    </div>
  );
};
