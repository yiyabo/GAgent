import React from 'react';
import { Empty, Select, Space, Typography } from 'antd';
import JobLogPanel from '@components/chat/JobLogPanel';
import { useChatStore } from '@store/chat';
import type { DecompositionJobStatus } from '@/types';

const { Text } = Typography;

interface JobEntry {
  jobId: string;
  label: string;
  timestamp: Date;
  jobType?: string | null;
  planId?: number | null;
  targetTaskName?: string | null;
  initialJob?: DecompositionJobStatus | null;
}

const formatActionLabel = (action: any): string => {
  if (!action || typeof action !== 'object') return '执行动作';
  const kind = typeof action.kind === 'string' ? action.kind : '';
  const name = typeof action.name === 'string' ? action.name : '';
  const params = action.parameters && typeof action.parameters === 'object' ? action.parameters : {};
  const query = typeof params.query === 'string' ? params.query.trim() : '';

  if (kind === 'tool_operation') {
    if (name && query) {
      return `调用 ${name} 搜索“${query}”`;
    }
    if (name) {
      return `调用 ${name}`;
    }
    return '调用工具';
  }
  if (kind === 'plan_operation') {
    const title =
      typeof params.plan_title === 'string'
        ? params.plan_title
        : typeof params.title === 'string'
          ? params.title
          : '';
    return title ? `执行计划：${title}` : `执行计划操作${name ? ` ${name}` : ''}`;
  }
  if (kind === 'task_operation') {
    const taskName =
      typeof params.task_name === 'string'
        ? params.task_name
        : typeof params.title === 'string'
          ? params.title
          : '';
    return taskName ? `执行任务：${taskName}` : `执行任务操作${name ? ` ${name}` : ''}`;
  }
  if (kind === 'context_request') {
    return `获取上下文${name ? `：${name}` : ''}`;
  }
  if (kind === 'system_operation') {
    return `系统操作${name ? `：${name}` : ''}`;
  }
  return `${kind || 'action'}${name ? `/${name}` : ''}`;
};

const formatJobLabel = (metadata: any, jobType?: string | null, jobId?: string) => {
  const actions =
    (Array.isArray(metadata?.actions) ? metadata?.actions : null) ??
    (Array.isArray(metadata?.raw_actions) ? metadata?.raw_actions : []);
  if (actions.length > 0) {
    const primary = formatActionLabel(actions[0]);
    const suffix = actions.length > 1 ? ` 等 ${actions.length} 项` : '';
    return `${primary}${suffix}${jobId ? ` · ${jobId.slice(0, 8)}` : ''}`;
  }
  if (metadata?.target_task_name) {
    return `执行任务：${metadata.target_task_name}${jobId ? ` · ${jobId.slice(0, 8)}` : ''}`;
  }
  return `${jobType || 'job'}${jobId ? ` · ${jobId.slice(0, 8)}` : ''}`;
};

const ExecutorPanel: React.FC = () => {
  const messages = useChatStore((state) => state.messages);

  const jobEntries = React.useMemo<JobEntry[]>(() => {
    const seen = new Set<string>();
    const entries: JobEntry[] = [];

    for (let idx = messages.length - 1; idx >= 0; idx -= 1) {
      const message = messages[idx];
      const metadata = message?.metadata as any;
      if (!metadata) continue;

      const jobMetadata = (metadata.job as DecompositionJobStatus | null) ?? null;
      const jobId: string | undefined = metadata.job_id ?? jobMetadata?.job_id;
      if (!jobId || seen.has(jobId)) continue;

      seen.add(jobId);

      const jobType = metadata.job_type ?? jobMetadata?.job_type ?? null;
      const targetTaskName = metadata.target_task_name ?? jobMetadata?.metadata?.target_task_name ?? null;
      const planId = metadata.plan_id ?? jobMetadata?.plan_id ?? null;

      entries.push({
        jobId,
        label: formatJobLabel(metadata, jobType, jobId),
        timestamp: message.timestamp ?? new Date(),
        jobType,
        planId,
        targetTaskName,
        initialJob: jobMetadata,
      });
    }

    return entries.sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime());
  }, [messages]);

  const [selectedJobId, setSelectedJobId] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!jobEntries.length) {
      setSelectedJobId(null);
      return;
    }
    if (!selectedJobId || !jobEntries.find((entry) => entry.jobId === selectedJobId)) {
      setSelectedJobId(jobEntries[0].jobId);
    }
  }, [jobEntries, selectedJobId]);

  const selectedEntry = jobEntries.find((entry) => entry.jobId === selectedJobId) ?? null;

  if (!jobEntries.length) {
    return (
      <div style={{ padding: 16 }}>
        <Empty description="暂无执行日志" />
      </div>
    );
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-color)' }}>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            选择执行记录
          </Text>
          <Select
            size="middle"
            value={selectedJobId ?? undefined}
            onChange={(value) => setSelectedJobId(value)}
            options={jobEntries.map((entry) => ({
              label: entry.label,
              value: entry.jobId,
            }))}
            style={{ width: '100%' }}
          />
        </Space>
      </div>

      {selectedEntry ? (
        <div style={{ flex: 1, overflow: 'auto', padding: '12px 16px' }}>
          <JobLogPanel
            jobId={selectedEntry.jobId}
            initialJob={selectedEntry.initialJob ?? undefined}
            targetTaskName={selectedEntry.targetTaskName ?? null}
            planId={selectedEntry.planId ?? null}
            jobType={selectedEntry.jobType ?? null}
          />
        </div>
      ) : (
        <div style={{ padding: 16 }}>
          <Empty description="请选择执行记录" />
        </div>
      )}
    </div>
  );
};

export default ExecutorPanel;
