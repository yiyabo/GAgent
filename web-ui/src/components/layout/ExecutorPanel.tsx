import React, { useState, useMemo, useEffect, useCallback } from 'react';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import JobLogPanel from '@components/chat/JobLogPanel';
import { useChatStore } from '@store/chat';
import { planTreeApi } from '@api/planTree';
import type { DecompositionJobStatus } from '@/types';
import './ExecutorPanel.css';

dayjs.extend(relativeTime);

interface JobEntry {
  jobId: string;
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
  const [expandedId, setExpandedId] = useState<string | null>(null);

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

      entries.push({
        jobId,
        label: formatJobLabel(metadata, metadata.job_type ?? jobMetadata?.job_type, jobId),
        timestamp: message.timestamp ?? new Date(),
        jobType: metadata.job_type ?? jobMetadata?.job_type ?? null,
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

  // 自动展开运行中的任务
  useEffect(() => {
    if (expandedId) return;
    const runningJob = jobEntries.find((entry) => getJobStatus(entry).status === 'running');
    if (runningJob) setExpandedId(runningJob.jobId);
    else if (jobEntries.length > 0) setExpandedId(jobEntries[0].jobId);
  }, [jobEntries, getJobStatus, expandedId]);

  // 统计
  const stats = useMemo(() => {
    let running = 0, completed = 0, failed = 0;
    for (const entry of jobEntries) {
      const { status } = getJobStatus(entry);
      if (status === 'running') running++;
      else if (status === 'succeeded' || status === 'completed') completed++;
      else if (status === 'failed') failed++;
    }
    return { running, completed, failed, total: jobEntries.length };
  }, [jobEntries, getJobStatus]);

  if (!jobEntries.length) {
    return (
      <div className="executor-empty">
        <div className="executor-empty-icon" />
        <span>暂无后台任务</span>
      </div>
    );
  }

  return (
    <div className="executor-container">
      {/* 极简统计栏 */}
      <div className="executor-stats">
        <span className="executor-stats-total">{stats.total} 个任务</span>
        <div className="executor-stats-dots">
          {stats.running > 0 && (
            <span className="executor-stat-item">
              <span className="executor-dot running" />
              <span>{stats.running}</span>
            </span>
          )}
          {stats.completed > 0 && (
            <span className="executor-stat-item">
              <span className="executor-dot completed" />
              <span>{stats.completed}</span>
            </span>
          )}
          {stats.failed > 0 && (
            <span className="executor-stat-item">
              <span className="executor-dot failed" />
              <span>{stats.failed}</span>
            </span>
          )}
        </div>
      </div>

      {/* 任务列表 */}
      <div className="executor-list">
        {jobEntries.map((entry) => {
          const { status, startedAt, finishedAt } = getJobStatus(entry);
          const isExpanded = expandedId === entry.jobId;
          const duration = formatDuration(startedAt, finishedAt);

          return (
            <div key={entry.jobId} className={`executor-item ${isExpanded ? 'expanded' : ''}`}>
              {/* 任务头部 */}
              <div 
                className="executor-item-header"
                onClick={() => setExpandedId(isExpanded ? null : entry.jobId)}
              >
                <div className="executor-item-left">
                  <span className={`executor-dot ${status}`} />
                  <span className="executor-item-label" title={entry.label}>
                    {entry.label}
                  </span>
                </div>
                <div className="executor-item-right">
                  {duration && <span className="executor-item-duration">{duration}</span>}
                  <span className="executor-item-time">{formatRelativeTime(entry.timestamp)}</span>
                  <svg 
                    className={`executor-chevron ${isExpanded ? 'expanded' : ''}`}
                    viewBox="0 0 12 12"
                  >
                    <path d="M4.5 2L8.5 6L4.5 10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </div>
              </div>

              {/* 展开内容 */}
              {isExpanded && (
                <div className="executor-item-content">
                  <JobLogPanel
                    jobId={entry.jobId}
                    initialJob={entry.initialJob ?? undefined}
                    targetTaskName={entry.targetTaskName ?? null}
                    planId={entry.planId ?? null}
                    jobType={entry.jobType ?? null}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default ExecutorPanel;
