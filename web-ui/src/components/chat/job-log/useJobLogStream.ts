import * as React from 'react';
import { ENV } from '@/config/env';
import { planTreeApi } from '@api/planTree';
import type { ActionLogEntry, DecompositionJobStatus, JobLogEvent, ThinkingProcess, ThinkingStep } from '@/types';
import { dispatchPlanSyncEvent } from '@utils/planSyncEvents';
import {
  inferThinkingLanguage,
  defaultThinkingDisplayText,
  extractThinkingFlushChunk,
  THINKING_DELTA_FLUSH_INTERVAL_MS,
} from '@utils/thinkingStream';
import { FINAL_STATUSES, MAX_RENDER_LOGS, parseStreamData } from './constants';

interface UseJobLogStreamOptions {
  jobId: string;
  initialJob?: DecompositionJobStatus | null;
  planId?: number | null;
  jobType?: string | null;
}

const _normalizeThinkingStatus = (value: unknown): ThinkingStep['status'] => {
  const key = String(value ?? '').trim().toLowerCase();
  if (
    key === 'pending' ||
    key === 'thinking' ||
    key === 'calling_tool' ||
    key === 'analyzing' ||
    key === 'done' ||
    key === 'completed' ||
    key === 'error'
  ) {
    return key;
  }
  return 'thinking';
};

const _extractActionTool = (action: unknown): string | null => {
  if (typeof action !== 'string' || !action.trim()) {
    return null;
  }
  try {
    const parsed = JSON.parse(action);
    if (parsed && typeof parsed === 'object' && typeof parsed.tool === 'string') {
      return parsed.tool.trim().toLowerCase();
    }
  } catch (_error) {
    // Keep best-effort fallback for non-JSON action text.
  }
  return action.trim().toLowerCase();
};

const _stringifyToolPayload = (payload: any): string => {
  if (typeof payload?.summary === 'string' && payload.summary.trim()) {
    return payload.summary;
  }
  if (typeof payload?.error === 'string' && payload.error.trim()) {
    return payload.error;
  }
  try {
    return JSON.stringify(payload ?? {}, null, 2);
  } catch (_error) {
    return String(payload ?? '');
  }
};

const _mergeToolResultIntoSteps = (
  steps: ThinkingStep[],
  tool: string,
  payload: any
): ThinkingStep[] => {
  if (!Array.isArray(steps) || steps.length === 0) {
    return steps;
  }
  const next = [...steps];
  const toolName = String(tool || '').trim().toLowerCase();
  const iteration = Number(payload?.iteration);

  let targetIndex = Number.isFinite(iteration) && iteration > 0
    ? next.findIndex((item) => item.iteration === iteration)
    : -1;

  if (targetIndex < 0 && toolName) {
    for (let idx = next.length - 1; idx >= 0; idx -= 1) {
      const itemTool = _extractActionTool(next[idx]?.action);
      if (itemTool && itemTool === toolName) {
        targetIndex = idx;
        break;
      }
    }
  }

  if (targetIndex < 0) {
    targetIndex = next.length - 1;
  }
  if (targetIndex < 0) {
    return next;
  }

  const current = next[targetIndex];
  const success = typeof payload?.success === 'boolean' ? payload.success : true;
  const nextResult = current.action_result || _stringifyToolPayload(payload);

  next[targetIndex] = {
    ...current,
    status: success ? 'done' : 'error',
    action_result: nextResult,
  };
  return next;
};

const _rebuildThinkingFromLogs = (
  logs: JobLogEvent[],
  jobStatus?: string | null,
  fallbackLanguage: 'zh' | 'en' = 'en'
): {
  process: ThinkingProcess;
  streamPaused: boolean;
  lastRuntimeControlAction: string | null;
  lastRuntimeControlAt: string | null;
} => {
  const steps: ThinkingStep[] = [];
  let streamPaused = false;
  let lastRuntimeControlAction: string | null = null;
  let lastRuntimeControlAt: string | null = null;

  const upsertStep = (step: ThinkingStep) => {
    if (!Number.isFinite(step.iteration) || step.iteration <= 0) {
      return;
    }
    const index = steps.findIndex((item) => item.iteration === step.iteration);
    if (index >= 0) {
      steps[index] = {
        ...steps[index],
        ...step,
      };
    } else {
      steps.push(step);
    }
    steps.sort((a, b) => a.iteration - b.iteration);
  };

  const appendDelta = (iteration: number, delta: string) => {
    if (!Number.isFinite(iteration) || iteration <= 0 || !delta) {
      return;
    }
    const index = steps.findIndex((item) => item.iteration === iteration);
    if (index >= 0) {
      steps[index] = {
        ...steps[index],
          thought: `${steps[index].thought || ''}${delta}`,
          display_text:
            steps[index].display_text ||
            defaultThinkingDisplayText(iteration, fallbackLanguage),
          kind: steps[index].kind || 'reasoning',
          status: steps[index].status || 'thinking',
        };
    }
  };

  for (const event of logs || []) {
    const metadata = event?.metadata ?? {};
    const subType = String(metadata?.sub_type || '').trim().toLowerCase();
    if (!subType) {
      continue;
    }

    if (subType === 'thinking_step') {
      const rawStep = metadata?.step ?? {};
      const iteration = Number(rawStep?.iteration ?? 0);
      if (!Number.isFinite(iteration) || iteration <= 0) {
        continue;
      }
      upsertStep({
        iteration,
        thought: String(rawStep?.thought ?? ''),
        display_text:
          typeof rawStep?.display_text === 'string' ? rawStep.display_text : undefined,
        kind:
          rawStep?.kind === 'reasoning' ||
          rawStep?.kind === 'tool' ||
          rawStep?.kind === 'summary'
            ? rawStep.kind
            : undefined,
        action: rawStep?.action ?? null,
        action_result: rawStep?.action_result ?? null,
        evidence: Array.isArray(rawStep?.evidence) ? rawStep.evidence : undefined,
        status: _normalizeThinkingStatus(rawStep?.status),
        timestamp: typeof rawStep?.timestamp === 'string' ? rawStep.timestamp : undefined,
        self_correction:
          rawStep?.self_correction == null ? null : String(rawStep?.self_correction),
        started_at: typeof rawStep?.started_at === 'string' ? rawStep.started_at : undefined,
        finished_at: typeof rawStep?.finished_at === 'string' ? rawStep.finished_at : undefined,
      });
      continue;
    }

    if (subType === 'thinking_delta') {
      appendDelta(
        Number(metadata?.iteration ?? 0),
        String(metadata?.delta ?? '')
      );
      continue;
    }

    if (subType === 'tool_call_result') {
      const merged = _mergeToolResultIntoSteps(
        steps,
        String(metadata?.tool ?? ''),
        metadata?.payload
      );
      steps.splice(0, steps.length, ...merged);
      continue;
    }

    if (subType === 'runtime_control') {
      const actionRaw = String(metadata?.action || '').trim().toLowerCase();
      const action = actionRaw === 'skip' ? 'skip_step' : actionRaw;
      if (action === 'pause') {
        streamPaused = true;
      } else if (action === 'resume') {
        streamPaused = false;
      }
      lastRuntimeControlAction = action || null;
      if (typeof event?.timestamp === 'string' && event.timestamp) {
        lastRuntimeControlAt = event.timestamp;
      }
      continue;
    }
  }

  const normalizedStatus = String(jobStatus || '').trim().toLowerCase();
  let processStatus: ThinkingProcess['status'] = 'active';
  if (FINAL_STATUSES.has(normalizedStatus)) {
    processStatus = normalizedStatus === 'failed' ? 'error' : 'completed';
  }

  return {
    process: {
      status: processStatus,
      steps,
    },
    streamPaused,
    lastRuntimeControlAction,
    lastRuntimeControlAt,
  };
};

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
  const [thinkingProcess, setThinkingProcess] = React.useState<ThinkingProcess>({
    status: 'active',
    steps: [],
  });
  const [streamPaused, setStreamPaused] = React.useState(false);
  const [lastRuntimeControlAction, setLastRuntimeControlAction] = React.useState<string | null>(null);
  const [lastRuntimeControlAt, setLastRuntimeControlAt] = React.useState<string | null>(null);
  const [runtimeControlBusy, setRuntimeControlBusy] = React.useState(false);
  const [runtimeControlBusyAction, setRuntimeControlBusyAction] = React.useState<'pause' | 'resume' | 'skip_step' | null>(null);

  const sourceRef = React.useRef<EventSource | null>(null);
  const pollerRef = React.useRef<number | null>(null);
  const autoCollapsedRef = React.useRef(false);
  const statusRef = React.useRef<string>(initialJob?.status ?? 'queued');
  const actionCursorRef = React.useRef<string | null>(initialJob?.action_cursor ?? null);
  const completionNotifiedRef = React.useRef(false);
  const progressSyncAtRef = React.useRef(0);
  const thinkingProcessRef = React.useRef<ThinkingProcess>({ status: 'active', steps: [] });
  const pendingThinkingDeltasRef = React.useRef<Record<number, string>>({});
  const pendingThinkingDeltaStartedAtRef = React.useRef<Record<number, number>>({});
  const thinkingDeltaFlushHandleRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    thinkingProcessRef.current = thinkingProcess;
  }, [thinkingProcess]);

  const clearPendingThinkingFlush = React.useCallback(() => {
    if (thinkingDeltaFlushHandleRef.current !== null) {
      window.clearTimeout(thinkingDeltaFlushHandleRef.current);
      thinkingDeltaFlushHandleRef.current = null;
    }
  }, []);

  const getFallbackThinkingLanguage = React.useCallback((): 'zh' | 'en' => {
    return inferThinkingLanguage(
      thinkingProcessRef.current.summary ??
        result?.content ??
        jobMetadata?.message_preview ??
        ''
    );
  }, [jobMetadata?.message_preview, result?.content]);

  const flushPendingThinkingDeltas = React.useCallback((force: boolean = false) => {
    const pendingEntries = Object.entries(pendingThinkingDeltasRef.current);
    if (pendingEntries.length === 0) {
      return;
    }

    const currentProcess = thinkingProcessRef.current;
    const nextSteps = [...(currentProcess.steps ?? [])];
    const nextPending: Record<number, string> = {};
    const nextPendingStartedAt: Record<number, number> = {};
    const fallbackLanguage = getFallbackThinkingLanguage();
    const now = Date.now();
    let didFlush = false;

    for (const [iterationKey, bufferedDelta] of pendingEntries) {
      const iteration = Number(iterationKey);
      if (!Number.isFinite(iteration) || !bufferedDelta) {
        continue;
      }
      const bufferedAt = pendingThinkingDeltaStartedAtRef.current[iteration] ?? now;
      const { flushable, remaining } = extractThinkingFlushChunk(bufferedDelta, force);
      let idx = nextSteps.findIndex((item) => item.iteration === iteration);
      if (idx < 0) {
        nextSteps.push({
          iteration,
          thought: '',
          display_text: defaultThinkingDisplayText(iteration, fallbackLanguage),
          kind: iteration <= 0 ? 'summary' : 'reasoning',
          status: 'thinking',
        });
        idx = nextSteps.length - 1;
      }

      if (flushable) {
        didFlush = true;
        nextSteps[idx] = {
          ...nextSteps[idx],
          thought: `${nextSteps[idx].thought || ''}${flushable}`,
          display_text:
            nextSteps[idx].display_text ||
            defaultThinkingDisplayText(iteration, fallbackLanguage),
          kind: nextSteps[idx].kind || (iteration <= 0 ? 'summary' : 'reasoning'),
          status: nextSteps[idx].status || 'thinking',
        };
      }

      if (remaining) {
        nextPending[iteration] = remaining;
        nextPendingStartedAt[iteration] = bufferedAt;
      }
    }

    pendingThinkingDeltasRef.current = nextPending;
    pendingThinkingDeltaStartedAtRef.current = nextPendingStartedAt;

    if (didFlush) {
      nextSteps.sort((a, b) => a.iteration - b.iteration);
      const nextProcess: ThinkingProcess = {
        ...currentProcess,
        status: 'active',
        steps: nextSteps,
      };
      thinkingProcessRef.current = nextProcess;
      setThinkingProcess(nextProcess);
    }

    if (
      Object.keys(pendingThinkingDeltasRef.current).length > 0 &&
      thinkingDeltaFlushHandleRef.current === null
    ) {
      thinkingDeltaFlushHandleRef.current = window.setTimeout(() => {
        thinkingDeltaFlushHandleRef.current = null;
        flushPendingThinkingDeltas(false);
      }, THINKING_DELTA_FLUSH_INTERVAL_MS);
    }
  }, [getFallbackThinkingLanguage]);

  const queueThinkingDelta = React.useCallback((iteration: number, delta: string) => {
    if (!Number.isFinite(iteration) || !delta) {
      return;
    }
    if (!pendingThinkingDeltasRef.current[iteration]) {
      pendingThinkingDeltaStartedAtRef.current[iteration] = Date.now();
    }
    pendingThinkingDeltasRef.current[iteration] =
      (pendingThinkingDeltasRef.current[iteration] || '') + delta;
    if (thinkingDeltaFlushHandleRef.current === null) {
      thinkingDeltaFlushHandleRef.current = window.setTimeout(() => {
        thinkingDeltaFlushHandleRef.current = null;
        flushPendingThinkingDeltas(false);
      }, THINKING_DELTA_FLUSH_INTERVAL_MS);
    }
  }, [flushPendingThinkingDeltas]);

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
      const replayLogs = snapshot.logs.slice(-MAX_RENDER_LOGS);
      setLogs(replayLogs);
      const summaryFromMetadata =
        (snapshot.result?.metadata &&
        typeof snapshot.result.metadata === 'object' &&
        typeof (snapshot.result.metadata as any)?.thinking_process?.summary === 'string')
          ? (snapshot.result.metadata as any).thinking_process.summary
          : undefined;
      const fallbackLanguage = inferThinkingLanguage(
        summaryFromMetadata ??
          snapshot.result?.content ??
          snapshot.metadata?.message_preview ??
          ''
      );
      const rebuilt = _rebuildThinkingFromLogs(replayLogs, snapshot.status, fallbackLanguage);
      clearPendingThinkingFlush();
      pendingThinkingDeltasRef.current = {};
      pendingThinkingDeltaStartedAtRef.current = {};
      thinkingProcessRef.current = rebuilt.process;
      setThinkingProcess(rebuilt.process);
      if (summaryFromMetadata) {
        setThinkingProcess((prev) => {
          const next = { ...prev, summary: summaryFromMetadata };
          thinkingProcessRef.current = next;
          return next;
        });
      }
      setStreamPaused(rebuilt.streamPaused);
      setLastRuntimeControlAction(rebuilt.lastRuntimeControlAction);
      setLastRuntimeControlAt(rebuilt.lastRuntimeControlAt);
    }
    if (Array.isArray(snapshot.action_logs)) {
      setActionLogs(snapshot.action_logs);
    }
    if (snapshot.action_cursor !== undefined) {
      actionCursorRef.current = snapshot.action_cursor ?? null;
    }
    setLastUpdatedAt(snapshot.finished_at ?? snapshot.started_at ?? snapshot.created_at ?? null);
  }, [clearPendingThinkingFlush]);

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

  const mergeThinkingStep = React.useCallback((step: ThinkingStep) => {
    flushPendingThinkingDeltas(true);
    setThinkingProcess((prev) => {
      const nextSteps = [...(prev.steps || [])];
      const idx = nextSteps.findIndex((item) => item.iteration === step.iteration);
      if (idx >= 0) {
        const existing = nextSteps[idx];
        nextSteps[idx] = {
          ...existing,
          ...step,
          thought: step.thought || existing?.thought || '',
          display_text: step.display_text || existing?.display_text,
          kind: step.kind || existing?.kind,
          action_result: step.action_result ?? existing?.action_result ?? null,
        };
      } else {
        nextSteps.push(step);
      }
      nextSteps.sort((a, b) => a.iteration - b.iteration);
      const next: ThinkingProcess = {
        ...prev,
        status: 'active',
        steps: nextSteps,
      };
      thinkingProcessRef.current = next;
      return next;
    });
  }, [flushPendingThinkingDeltas]);

  const appendThinkingDelta = React.useCallback((iteration: number, delta: string) => {
    queueThinkingDelta(iteration, delta);
  }, [queueThinkingDelta]);

  const mergeToolResult = React.useCallback((tool: string, payload: any) => {
    flushPendingThinkingDeltas(true);
    setThinkingProcess((prev) => {
      const currentSteps = Array.isArray(prev.steps) ? prev.steps : [];
      const next: ThinkingProcess = {
        ...prev,
        status: 'active',
        steps: _mergeToolResultIntoSteps(currentSteps, tool, payload),
      };
      thinkingProcessRef.current = next;
      return next;
    });
  }, [flushPendingThinkingDeltas]);

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

  const pollStartRef = React.useRef<number | null>(null);

  const stopPolling = React.useCallback(() => {
    if (pollerRef.current !== null) {
      window.clearTimeout(pollerRef.current);
      pollerRef.current = null;
    }
    pollStartRef.current = null;
  }, []);

  const startPolling = React.useCallback(() => {
    if (pollerRef.current !== null) return;
    if (pollStartRef.current === null) {
      pollStartRef.current = Date.now();
    }
    const tick = async () => {
      pollerRef.current = null;
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
          return;
        }
      } catch (err) {
        const isNotFoundError = err instanceof Error && /not found/i.test(err.message || '');
        if (isNotFoundError) {
          setMissingJob(true);
          stopPolling();
          return;
        }
        console.error('Failed to poll job status:', err);
      }
      // Backoff: first 30s poll every 5s, then every 15s.
      const elapsed = Date.now() - (pollStartRef.current ?? Date.now());
      const delay = elapsed < 30_000 ? 5_000 : 15_000;
      pollerRef.current = window.setTimeout(tick, delay);
    };
    pollerRef.current = window.setTimeout(tick, 5_000);
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

  const pauseExecution = React.useCallback(async () => {
    setRuntimeControlBusy(true);
    setRuntimeControlBusyAction('pause');
    try {
      const resp = await planTreeApi.controlJob(jobId, { action: 'pause' });
      if (resp.success) {
        setStreamPaused(true);
        setLastRuntimeControlAction('pause');
        setLastRuntimeControlAt(new Date().toISOString());
      }
      return resp;
    } catch (err) {
      return {
        success: false,
        job_id: jobId,
        action: 'pause',
        message: err instanceof Error ? err.message : 'Failed to pause execution',
      };
    } finally {
      setRuntimeControlBusy(false);
      setRuntimeControlBusyAction(null);
    }
  }, [jobId]);

  const resumeExecution = React.useCallback(async () => {
    setRuntimeControlBusy(true);
    setRuntimeControlBusyAction('resume');
    try {
      const resp = await planTreeApi.controlJob(jobId, { action: 'resume' });
      if (resp.success) {
        setStreamPaused(false);
        setLastRuntimeControlAction('resume');
        setLastRuntimeControlAt(new Date().toISOString());
      }
      return resp;
    } catch (err) {
      return {
        success: false,
        job_id: jobId,
        action: 'resume',
        message: err instanceof Error ? err.message : 'Failed to resume execution',
      };
    } finally {
      setRuntimeControlBusy(false);
      setRuntimeControlBusyAction(null);
    }
  }, [jobId]);

  const skipCurrentStep = React.useCallback(async () => {
    setRuntimeControlBusy(true);
    setRuntimeControlBusyAction('skip_step');
    try {
      const resp = await planTreeApi.controlJob(jobId, { action: 'skip_step' });
      if (resp.success) {
        setLastRuntimeControlAction('skip_step');
        setLastRuntimeControlAt(new Date().toISOString());
      }
      return resp;
    } catch (err) {
      return {
        success: false,
        job_id: jobId,
        action: 'skip_step',
        message: err instanceof Error ? err.message : 'Failed to skip current step',
      };
    } finally {
      setRuntimeControlBusy(false);
      setRuntimeControlBusyAction(null);
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
    clearPendingThinkingFlush();
    pendingThinkingDeltasRef.current = {};
    pendingThinkingDeltaStartedAtRef.current = {};
    thinkingProcessRef.current = { status: 'active', steps: [] };
    setThinkingProcess(thinkingProcessRef.current);
    setStreamPaused(false);
    setLastRuntimeControlAction(null);
    setLastRuntimeControlAt(null);
    setRuntimeControlBusy(false);
    setRuntimeControlBusyAction(null);
  }, [clearPendingThinkingFlush, jobId]);

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
        const source = new EventSource(streamUrl, { withCredentials: true });
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
            if (FINAL_STATUSES.has(parsed.status)) {
              setThinkingProcess((prev) => {
                const next: ThinkingProcess = {
                  ...prev,
                  status: parsed.status === 'failed' ? 'error' : 'completed',
                };
                thinkingProcessRef.current = next;
                return next;
              });
            }
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
          const subType = parsed.event?.metadata?.sub_type;
          if (subType === 'thinking_step') {
            const rawStep = parsed.event?.metadata?.step ?? {};
            mergeThinkingStep({
              iteration: Number(rawStep.iteration ?? 0),
              thought: String(rawStep.thought ?? ''),
              display_text:
                typeof rawStep.display_text === 'string' ? rawStep.display_text : undefined,
              kind:
                rawStep.kind === 'reasoning' ||
                rawStep.kind === 'tool' ||
                rawStep.kind === 'summary'
                  ? rawStep.kind
                  : undefined,
              action: rawStep.action ?? null,
              action_result: rawStep.action_result ?? null,
              evidence: Array.isArray(rawStep?.evidence) ? rawStep.evidence : undefined,
              status: (rawStep.status as ThinkingStep['status']) || 'thinking',
              timestamp: rawStep.timestamp ?? undefined,
              started_at: typeof rawStep?.started_at === 'string' ? rawStep.started_at : undefined,
              finished_at: typeof rawStep?.finished_at === 'string' ? rawStep.finished_at : undefined,
            });
          } else if (subType === 'thinking_delta') {
            appendThinkingDelta(
              Number(parsed.event?.metadata?.iteration ?? 0),
              String(parsed.event?.metadata?.delta ?? '')
            );
          } else if (subType === 'tool_call_result') {
            mergeToolResult(
              String(parsed.event?.metadata?.tool ?? ''),
              parsed.event?.metadata?.payload
            );
          } else if (subType === 'runtime_control') {
            const actionRaw = String(parsed.event?.metadata?.action || '').toLowerCase();
            const action = actionRaw === 'skip' ? 'skip_step' : actionRaw;
            setLastRuntimeControlAction(action || null);
            setLastRuntimeControlAt(
              typeof parsed.event?.timestamp === 'string' && parsed.event.timestamp
                ? parsed.event.timestamp
                : new Date().toISOString()
            );
            if (action === 'pause') {
              setStreamPaused(true);
            } else if (action === 'resume') {
              setStreamPaused(false);
            }
          }
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
      clearPendingThinkingFlush();
      closeStream();
      stopPolling();
    };
  }, [appendLogEvent, appendThinkingDelta, applySnapshot, clearPendingThinkingFlush, closeStream, emitPlanProgressSync, jobId, mergeThinkingStep, mergeToolResult, startPolling, stopPolling]);

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
    thinkingProcess,
    streamPaused,
    setStreamPaused,
    lastRuntimeControlAction,
    lastRuntimeControlAt,
    runtimeControlBusy,
    runtimeControlBusyAction,
    pauseExecution,
    resumeExecution,
    skipCurrentStep,
  };
}
