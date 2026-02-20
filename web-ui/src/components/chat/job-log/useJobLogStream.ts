import * as React from 'react';
import { ENV } from '@/config/env';
import { planTreeApi } from '@api/planTree';
import type { ActionLogEntry, DecompositionJobStatus, JobLogEvent } from '@/types';
import { dispatchPlanSyncEvent } from '@utils/planSyncEvents';
import { FINAL_STATUSES, MAX_RENDER_LOGS, parseStreamData } from './constants';

interface UseJobLogStreamOptions {
  jobId: string;
  initialJob?: DecompositionJobStatus | null;
  planId?: number | null;
  jobType?: string | null;
}

export function useJobLogStream({ jobId, initialJob, planId, jobType: initialJobType }: UseJobLogStreamOptions) {
  const PROGRESS_SYNC_THROTTLE_MS = 10000;
  const [logs, setLogs] = React.useState<JobLogEvent[]>(initialJob?.logs ?? []);
  const [actionLogs, setActionLogs] = React.useState<ActionLogEntry[]>(initialJob?.action_logs ?? []);
  const [status, setStatus] = React.useState<string>(initialJob?.status ?? 'queued');
  const [stats, setStats] = React.useState<Record<string, any>>(initialJob?.stats ?? {});
  const [jobParams, setJobParams] = React.useState<Record<string, any>>(initialJob?.params ?? {});
  const [result, setResult] = React.useState<Record<string, any> | null>(initialJob?.result ?? null);
  const [error, setError] = React.useState<string | null>(initialJob?.error ?? null);
  const [expanded, setExpanded] = React.useState(false);
  const [isStreaming, setIsStreaming] = React.useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = React.useState<string | null>(initialJob?.finished_at ?? initialJob?.started_at ?? initialJob?.created_at ?? null);
  const [missingJob, setMissingJob] = React.useState(false);
  const [jobType, setJobType] = React.useState<string>(initialJob?.job_type ?? initialJobType ?? 'plan_decompose');
  const [jobMetadata, setJobMetadata] = React.useState<Record<string, any>>(initialJob?.metadata ?? {});
  const [resolvedPlanId, setResolvedPlanId] = React.useState<number | null>(planId ?? initialJob?.plan_id ?? null);
  const [cliLogVisible, setCliLogVisible] = React.useState(false);
  const [cliLogLines, setCliLogLines] = React.useState<string[]>([]);
  const [cliLogLoading, setCliLogLoading] = React.useState(false);
  const [cliLogError, setCliLogError] = React.useState<string | null>(null);
  const [cliLogTruncated, setCliLogTruncated] = React.useState(false);
  const [cliLogPath, setCliLogPath] = React.useState<string | null>(null);

  const sourceRef = React.useRef<EventSource | null>(null);
  const pollerRef = React.useRef<number | null>(null);
  const autoCollapsedRef = React.useRef(false);
  const statusRef = React.useRef<string>(initialJob?.status ?? 'queued');
  const actionCursorRef = React.useRef<string | null>(initialJob?.action_cursor ?? null);
  const completionNotifiedRef = React.useRef(false);
  const progressSyncAtRef = React.useRef(0);

  const applySnapshot = React.useCallback((snapshot: DecompositionJobStatus | null) => {
    if (!snapshot) return;
    setStatus(snapshot.status);
    statusRef.current = snapshot.status;
    setStats(snapshot.stats ?? {});
    setResult(snapshot.result ?? null);
    setError(snapshot.error ?? null);
    if (snapshot.job_type) {
      setJobType(snapshot.job_type || 'plan_decompose');
    }
    if (snapshot.params && typeof snapshot.params === 'object') {
      setJobParams(snapshot.params as Record<string, any>);
    }
    if (snapshot.metadata && typeof snapshot.metadata === 'object') {
      setJobMetadata(snapshot.metadata);
    }
    if (snapshot.plan_id !== undefined && snapshot.plan_id !== null) {
      setResolvedPlanId(snapshot.plan_id);
    }
    if (Array.isArray(snapshot.logs)) {
      setLogs(snapshot.logs.slice(-MAX_RENDER_LOGS));
    }
    if (Array.isArray(snapshot.action_logs)) {
      setActionLogs(snapshot.action_logs);
    }
    if (snapshot.action_cursor !== undefined) {
      actionCursorRef.current = snapshot.action_cursor ?? null;
    }
    setLastUpdatedAt(snapshot.finished_at ?? snapshot.started_at ?? snapshot.created_at ?? null);
  }, []);

  const appendLogEvent = React.useCallback((event: JobLogEvent | undefined) => {
    if (!event) return;
    setLogs((prev) => {
      const next = [...prev, event];
      if (next.length > MAX_RENDER_LOGS) {
        return next.slice(-MAX_RENDER_LOGS);
      }
      return next;
    });
  }, []);

  const emitPlanProgressSync = React.useCallback(
    (payload?: {
      status?: string | null;
      jobType?: string | null;
      planId?: number | null;
      metadata?: Record<string, any> | null;
    }) => {
      const now = Date.now();
      if (now - progressSyncAtRef.current < PROGRESS_SYNC_THROTTLE_MS) {
        return;
      }
      progressSyncAtRef.current = now;

      const metadata = payload?.metadata ?? jobMetadata;
      const planIdForEvent =
        payload?.planId ??
        resolvedPlanId ??
        (typeof metadata?.plan_id === 'number' ? metadata.plan_id : null) ??
        null;
      const planTitle =
        typeof metadata?.plan_title === 'string' ? metadata.plan_title : null;
      const statusForEvent = payload?.status ?? statusRef.current ?? null;
      const jobTypeForEvent = payload?.jobType ?? jobType ?? null;

      dispatchPlanSyncEvent(
        {
          type: 'task_changed',
          plan_id: planIdForEvent,
          plan_title: planTitle,
          job_id: jobId,
          job_type: jobTypeForEvent,
          status: statusForEvent,
        },
        {
          jobId,
          jobType: jobTypeForEvent,
          status: statusForEvent,
          source: 'job.log.progress',
        }
      );
    },
    [jobId, jobMetadata, jobType, resolvedPlanId]
  );

  const closeStream = React.useCallback(() => {
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  const stopPolling = React.useCallback(() => {
    if (pollerRef.current !== null) {
      window.clearInterval(pollerRef.current);
      pollerRef.current = null;
    }
  }, []);

  const startPolling = React.useCallback(() => {
    if (pollerRef.current !== null) return;
    pollerRef.current = window.setInterval(async () => {
      try {
        const snapshot = await planTreeApi.getJobStatus(jobId);
        applySnapshot(snapshot);
        if (!FINAL_STATUSES.has(snapshot.status)) {
          emitPlanProgressSync({
            status: snapshot.status,
            jobType: snapshot.job_type ?? null,
            planId: snapshot.plan_id ?? null,
            metadata:
              snapshot.metadata && typeof snapshot.metadata === 'object'
                ? (snapshot.metadata as Record<string, any>)
                : null,
          });
        }
        if (FINAL_STATUSES.has(snapshot.status)) {
          stopPolling();
        }
      } catch (err) {
        const isNotFoundError = err instanceof Error && /not found/i.test(err.message || '');
        if (isNotFoundError) {
          setMissingJob(true);
          stopPolling();
        } else {
          console.error('Failed to poll job status:', err);
        }
      }
    }, 5000);
  }, [applySnapshot, emitPlanProgressSync, jobId, stopPolling]);

  const fetchCliLog = React.useCallback(async () => {
    setCliLogLoading(true);
    setCliLogError(null);
    try {
      const response = await planTreeApi.getJobLogTail(jobId, 200);
      setCliLogLines(response.lines);
      setCliLogTruncated(response.truncated);
      setCliLogPath(response.log_path);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Failed to load CLI logs';
      setCliLogError(message);
      setCliLogLines([]);
      setCliLogTruncated(false);
      setCliLogPath(null);
    } finally {
      setCliLogLoading(false);
    }
  }, [jobId]);

  React.useEffect(() => {
    applySnapshot(initialJob ?? null);
  }, [applySnapshot, initialJob, jobId]);

  React.useEffect(() => {
    if (FINAL_STATUSES.has(status) && !autoCollapsedRef.current) {
      autoCollapsedRef.current = true;
      const timer = window.setTimeout(() => {
        setExpanded(false);
      }, 1800);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [status]);

  React.useEffect(() => {
    statusRef.current = status;
  }, [status]);

  React.useEffect(() => {
    completionNotifiedRef.current = false;
    progressSyncAtRef.current = 0;
  }, [jobId]);

  React.useEffect(() => {
    if (cliLogVisible) {
      fetchCliLog();
    }
  }, [cliLogVisible, fetchCliLog]);

  React.useEffect(() => {
    if (!FINAL_STATUSES.has(status) || completionNotifiedRef.current) {
      return;
    }
    const planIdForEvent =
      resolvedPlanId ??
      (typeof jobMetadata?.plan_id === 'number' ? jobMetadata.plan_id : null) ??
      null;
    const planTitle =
      typeof jobMetadata?.plan_title === 'string' ? jobMetadata.plan_title : null;
    dispatchPlanSyncEvent(
      {
        type: 'plan_jobs_completed',
        plan_id: planIdForEvent,
        plan_title: planTitle,
        job_id: jobId,
        job_type: jobType ?? null,
        status,
      },
      {
        jobId,
        jobType: jobType ?? null,
        status,
        source: 'job.log',
      }
    );
    completionNotifiedRef.current = true;
  }, [jobId, jobMetadata, jobType, resolvedPlanId, status]);

  React.useEffect(() => {
    if (!jobId) {
      return undefined;
    }

    let cancelled = false;

    const isNotFoundError = (err: unknown) =>
      err instanceof Error && /not found/i.test(err.message || '');

    const init = async () => {
      try {
        const snapshot = await planTreeApi.getJobStatus(jobId);
        if (cancelled) {
          return;
        }
        setMissingJob(false);
        applySnapshot(snapshot);
        if (!FINAL_STATUSES.has(snapshot.status)) {
          emitPlanProgressSync({
            status: snapshot.status,
            jobType: snapshot.job_type ?? null,
            planId: snapshot.plan_id ?? null,
            metadata:
              snapshot.metadata && typeof snapshot.metadata === 'object'
                ? (snapshot.metadata as Record<string, any>)
                : null,
          });
        }
      } catch (err) {
        if (cancelled) return;
        if (isNotFoundError(err)) {
          setMissingJob(true);
          closeStream();
          stopPolling();
          return;
        }
        console.error('Failed to load task-decomposition status:', err);
        // Fallback to polling.
      }

      const streamUrl = `${ENV.API_BASE_URL}/jobs/${jobId}/stream`;
      try {
        const source = new EventSource(streamUrl);
        sourceRef.current = source;
        setIsStreaming(true);

        source.onmessage = (event) => {
          const parsed = parseStreamData(event);
          if (!parsed) return;

          if (parsed.type === 'snapshot') {
            applySnapshot(parsed.job);
            if (!FINAL_STATUSES.has(parsed.job.status)) {
              emitPlanProgressSync({
                status: parsed.job.status,
                jobType: parsed.job.job_type ?? null,
                planId: parsed.job.plan_id ?? null,
                metadata:
                  parsed.job.metadata && typeof parsed.job.metadata === 'object'
                    ? (parsed.job.metadata as Record<string, any>)
                    : null,
              });
            }
            return;
          }

          if (parsed.type === 'heartbeat') {
            setStatus(parsed.job.status);
            statusRef.current = parsed.job.status;
            setStats(parsed.job.stats ?? {});
            if (parsed.job.job_type) {
              setJobType(parsed.job.job_type || 'plan_decompose');
            }
            if (parsed.job.metadata) {
              setJobMetadata(parsed.job.metadata as Record<string, any>);
            }
            if (parsed.job.plan_id !== undefined && parsed.job.plan_id !== null) {
              setResolvedPlanId(parsed.job.plan_id);
            }
            if (!FINAL_STATUSES.has(parsed.job.status)) {
              emitPlanProgressSync({
                status: parsed.job.status,
                jobType: parsed.job.job_type ?? null,
                planId: parsed.job.plan_id ?? null,
                metadata:
                  parsed.job.metadata && typeof parsed.job.metadata === 'object'
                    ? (parsed.job.metadata as Record<string, any>)
                    : null,
              });
            }
            return;
          }

          if (parsed.status) {
            setStatus(parsed.status);
            statusRef.current = parsed.status;
          }
          if (parsed.stats) {
            setStats(parsed.stats);
          }
          if (parsed.result) {
            setResult(parsed.result);
          }
          if (parsed.error) {
            setError(parsed.error);
          }
          if (parsed.job_type) {
            setJobType(parsed.job_type || 'plan_decompose');
          }
          if (parsed.metadata) {
            setJobMetadata(parsed.metadata);
          }
          appendLogEvent(parsed.event);
          setLastUpdatedAt(new Date().toISOString());

          if (parsed.status && FINAL_STATUSES.has(parsed.status)) {
            closeStream();
          }
        };

        source.onerror = () => {
          if (FINAL_STATUSES.has(statusRef.current)) {
            closeStream();
            return;
          }
          console.warn('SSE connection interrupted; switching to polling mode.');
          closeStream();
          startPolling();
        };
      } catch (err) {
        console.warn('SSE initialization failed; falling back to polling:', err);
        startPolling();
      }
    };

    init();

    return () => {
      cancelled = true;
      closeStream();
      stopPolling();
    };
  }, [appendLogEvent, applySnapshot, closeStream, emitPlanProgressSync, jobId, startPolling, stopPolling]);

  return {
    logs,
    actionLogs,
    status,
    stats,
    jobParams,
    result,
    error,
    expanded,
    setExpanded,
    isStreaming,
    lastUpdatedAt,
    missingJob,
    jobType,
    jobMetadata,
    resolvedPlanId,
    cliLogVisible,
    setCliLogVisible,
    cliLogLines,
    cliLogLoading,
    cliLogError,
    cliLogTruncated,
    cliLogPath,
    fetchCliLog,
  };
}
