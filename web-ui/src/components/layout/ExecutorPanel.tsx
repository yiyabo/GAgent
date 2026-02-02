import React, { useState, useMemo, useEffect, useCallback } from 'react';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import { useChatStore } from '@store/chat';
import { planTreeApi } from '@api/planTree';
import type { DecompositionJobStatus } from '@/types';
import './ExecutorPanel.css';

dayjs.extend(relativeTime);

interface JobEntry {
  jobId: string;
  displayId: string;
  label: string;
  timestamp: Date;
  jobType?: string | null;
  planId?: number | null;
  targetTaskName?: string | null;
  initialJob?: DecompositionJobStatus | null;
  status?: string;
  startedAt?: string | null;
  finishedAt?: string | null;
}

const normalizeJobStatus = (raw: unknown): 'queued' | 'running' | 'completed' | 'failed' => {
  const key = String(raw ?? '').trim().toLowerCase();
  if (key === 'running') return 'running';
  if (key === 'failed') return 'failed';
  if (key === 'succeeded' || key === 'completed') return 'completed';
  return 'queued';
};

const getStatusLabel = (status: string): string => {
  switch (normalizeJobStatus(status)) {
    case 'running':
      return '运行中';
    case 'completed':
      return '已完成';
    case 'failed':
      return '失败';
    default:
      return '排队中';
  }
};

const formatActionLabel = (action: any): string => {
  if (!action || typeof action !== 'object') return '执行动作';
  const kind = typeof action.kind === 'string' ? action.kind : '';
  const name = typeof action.name === 'string' ? action.name : '';
  const params = action.parameters && typeof action.parameters === 'object' ? action.parameters : {};
  const query = typeof params.query === 'string' ? params.query.trim() : '';

  if (kind === 'tool_operation') {
    if (name && query) return `调用 ${name}`;
    if (name) return `调用 ${name}`;
    return '调用工具';
  }
  if (kind === 'plan_operation') {
    const title = typeof params.plan_title === 'string' ? params.plan_title : typeof params.title === 'string' ? params.title : '';
    return title ? `执行计划：${title}` : `执行计划操作`;
  }
  if (kind === 'task_operation') {
    const taskName = typeof params.task_name === 'string' ? params.task_name : typeof params.title === 'string' ? params.title : '';
    return taskName ? `执行任务：${taskName}` : `执行任务操作`;
  }
  return kind || 'action';
};

const formatJobLabel = (metadata: any, jobType?: string | null, jobId?: string) => {
  // Prefer a human label for PhageScope tracking jobs.
  const jt = (metadata?.job_type ?? jobType ?? '').toString().toLowerCase();
  if (jt === 'phagescope_track') {
    const phageid = typeof metadata?.phageid === 'string' ? metadata.phageid : undefined;
    const hint = phageid ? `PhageScope · ${phageid}` : 'PhageScope · 注释任务';
    return hint;
  }
  const actions = (Array.isArray(metadata?.actions) ? metadata?.actions : null) ?? (Array.isArray(metadata?.raw_actions) ? metadata?.raw_actions : []);
  if (actions.length > 0) {
    const primary = formatActionLabel(actions[0]);
    const suffix = actions.length > 1 ? ` 等 ${actions.length} 项` : '';
    return `${primary}${suffix}`;
  }
  if (metadata?.target_task_name) {
    return `执行任务：${metadata.target_task_name}`;
  }
  return jobType || 'job';
};

const formatDuration = (startedAt?: string | null, finishedAt?: string | null): string => {
  if (!startedAt) return '';
  const start = dayjs(startedAt);
  const end = finishedAt ? dayjs(finishedAt) : dayjs();
  const diffMs = end.diff(start);
  
  if (diffMs < 1000) return '<1s';
  if (diffMs < 60000) return `${Math.floor(diffMs / 1000)}s`;
  if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)}m`;
  return `${Math.floor(diffMs / 3600000)}h`;
};

const formatRelativeTime = (date: Date): string => {
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 60000) return '刚刚';
  if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)}分钟前`;
  if (diffMs < 86400000) return `${Math.floor(diffMs / 3600000)}小时前`;
  return `${Math.floor(diffMs / 86400000)}天前`;
};

const FINAL_STATUSES = new Set(['succeeded', 'failed', 'completed']);

const ExecutorPanel: React.FC = () => {
  const messages = useChatStore((state) => state.messages);
  const [jobStatuses, setJobStatuses] = useState<Record<string, { status: string; startedAt?: string | null; finishedAt?: string | null }>>({});

  const jobEntries = useMemo<JobEntry[]>(() => {
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
      const phagescopeTaskId = metadata.phagescope_taskid ?? null;
      const displayId =
        typeof phagescopeTaskId === 'string' && phagescopeTaskId.trim()
          ? phagescopeTaskId.trim()
          : jobId.slice(0, 8);

      entries.push({
        jobId,
        displayId,
        label: formatJobLabel(metadata, jobType, jobId),
        timestamp: message.timestamp ?? new Date(),
        jobType,
        planId: metadata.plan_id ?? jobMetadata?.plan_id ?? null,
        targetTaskName: metadata.target_task_name ?? jobMetadata?.metadata?.target_task_name ?? null,
        initialJob: jobMetadata,
        status: metadata.status ?? jobMetadata?.status ?? 'queued',
        startedAt: metadata.started_at ?? jobMetadata?.started_at ?? null,
        finishedAt: metadata.finished_at ?? jobMetadata?.finished_at ?? null,
      });
    }

    return entries.sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime());
  }, [messages]);

  const getJobStatus = useCallback((entry: JobEntry) => {
    const polledStatus = jobStatuses[entry.jobId];
    if (polledStatus) {
      return {
        status: polledStatus.status,
        startedAt: polledStatus.startedAt ?? entry.startedAt,
        finishedAt: polledStatus.finishedAt ?? entry.finishedAt,
      };
    }
    return {
      status: entry.status ?? 'queued',
      startedAt: entry.startedAt,
      finishedAt: entry.finishedAt,
    };
  }, [jobStatuses]);

  // 轮询运行中的任务
  useEffect(() => {
    const runningJobs = jobEntries.filter((entry) => {
      const { status } = getJobStatus(entry);
      return !FINAL_STATUSES.has(status);
    });

    if (runningJobs.length === 0) return;

    const pollInterval = setInterval(async () => {
      for (const entry of runningJobs) {
        try {
          const snapshot = await planTreeApi.getJobStatus(entry.jobId);
          setJobStatuses((prev) => ({
            ...prev,
            [entry.jobId]: {
              status: snapshot.status,
              startedAt: snapshot.started_at,
              finishedAt: snapshot.finished_at,
            },
          }));
        } catch (err) {
          // 静默处理
        }
      }
    }, 5000);

    return () => clearInterval(pollInterval);
  }, [jobEntries, getJobStatus]);

  if (!jobEntries.length) {
    return (
      <div className="executor-empty">
        <div className="executor-empty-icon" />
        <span>暂无任务记录</span>
      </div>
    );
  }

  return (
    <div className="executor-container">
      <div className="task-table-wrap">
        <table className="task-table">
          <thead>
            <tr>
              <th>Task ID</th>
              <th>内容</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {jobEntries.map((entry) => {
              const { status, startedAt, finishedAt } = getJobStatus(entry);
              const normalized = normalizeJobStatus(status);
              const duration = formatDuration(startedAt, finishedAt);
              return (
                <tr key={entry.jobId} className="task-row">
                  <td className="task-id" title={entry.jobId}>
                    {entry.displayId}
                  </td>
                  <td className="task-content" title={entry.label}>
                    {entry.label}
                  </td>
                  <td className="task-status">
                    <span className={`task-status-dot ${normalized}`} />
                    <span className="task-status-text">{getStatusLabel(status)}</span>
                    {duration ? <span className="task-status-meta">{duration}</span> : null}
                    <span className="task-status-meta">{formatRelativeTime(entry.timestamp)}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default ExecutorPanel;
