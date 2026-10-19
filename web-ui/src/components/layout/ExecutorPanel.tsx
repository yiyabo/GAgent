import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import { Button, Progress, Space, Tag, Tooltip, Typography, message } from 'antd';
import { ReloadOutlined, SearchOutlined, DownOutlined, UpOutlined, PauseOutlined, CaretRightOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { planTreeApi } from '@api/planTree';
import { ENV } from '@/config/env';
import JobLogPanel from '@components/chat/JobLogPanel';
import { FINAL_STATUSES } from '@components/chat/job-log/constants';
import type {
  BackgroundTaskBoardResponse,
  BackgroundTaskCategory,
  BackgroundTaskGroup,
  BackgroundTaskItem,
} from '@/types';
import { resolveChatSessionProcessingKey } from '@/utils/chatSessionKeys';
import { parseServerTimeDayjs as parseServerTimeShared } from '@/utils/serverTime';
import { translate, useT } from '@/i18n/useT';
import type { TFn } from '@/i18n/useT';
import { statusLabel } from '@/i18n/statusLabels';
import './ExecutorPanel.css';

dayjs.extend(relativeTime);
const { Text } = Typography;

const GROUP_ORDER: BackgroundTaskCategory[] = ['task_creation', 'phagescope', 'code_executor'];
// FINAL_STATUSES comes from the shared job-log constants (single source of
// truth: succeeded/completed/failed/cancelled).

const parseServerTime = (time?: string | null) => {
  // Shared helper treats zone-less backend timestamps as UTC (local-offset drift fix).
  return parseServerTimeShared(time);
};

// Product names, not translated.
const PHAGESCOPE_LABEL = 'PhageScope';
const CODE_EXECUTOR_LABEL = 'Code Executor';

const EMPTY_BOARD = (): BackgroundTaskBoardResponse => ({
  generated_at: new Date().toISOString(),
  total: 0,
  groups: {
  task_creation: {
  key: 'task_creation',
  label: translate('executorPanel.groupTaskCreation'),
  total: 0,
  running: 0,
  queued: 0,
  succeeded: 0,
  failed: 0,
  items: [],
  },
  phagescope: {
  key: 'phagescope',
  label: PHAGESCOPE_LABEL,
  total: 0,
  running: 0,
  queued: 0,
  succeeded: 0,
  failed: 0,
  items: [],
  },
  code_executor: {
  key: 'code_executor',
  label: CODE_EXECUTOR_LABEL,
  total: 0,
  running: 0,
  queued: 0,
  succeeded: 0,
  failed: 0,
  items: [],
  },
  },
});

const normalizeJobStatus = (raw: unknown): 'queued' | 'running' | 'completed' | 'failed' => {
  const key = String(raw ?? '').trim().toLowerCase();
  if (key === 'running' || key === 'in_progress') return 'running';
  if (key === 'failed' || key === 'error') return 'failed';
  if (key === 'succeeded' || key === 'completed' || key === 'success' || key === 'done') return 'completed';
  return 'queued';
};

const getStatusLabel = (status: string, t: TFn): string => {
  switch (normalizeJobStatus(status)) {
  case 'running':
  return statusLabel(t, 'running');
  case 'completed':
  return statusLabel(t, 'completed');
  case 'failed':
  return statusLabel(t, 'failed');
  default:
  return t('executorPanel.queued');
  }
};

/** The backend sends English group labels; show the category in the UI language, and any unknown category as sent. */
const groupLabel = (group: BackgroundTaskGroup, t: TFn): string => {
  switch (group.key) {
    case 'task_creation':
      return t('executorPanel.groupTaskCreation');
    case 'code_executor':
      return t('toolProgress.categoryCodeExecutor');
    default:
      return group.label;
  }
};

const getDisplayId = (item: BackgroundTaskItem): string => {
  if (item.category === 'phagescope' && item.taskid && item.taskid.trim()) {
  return item.taskid.trim();
  }
  return item.job_id.slice(0, 8);
};

const formatDuration = (startedAt?: string | null, finishedAt?: string | null): string => {
  if (!startedAt) return '';
  const start = parseServerTime(startedAt);
  if (!start || !start.isValid()) return '';
  const end = finishedAt ? parseServerTime(finishedAt) : dayjs();
  if (!end || !end.isValid()) return '';
  const diffMs = end.diff(start);
  if (diffMs < 1000) return '<1s';
  if (diffMs < 60000) return `${Math.floor(diffMs / 1000)}s`;
  if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)}m`;
  return `${Math.floor(diffMs / 3600000)}h`;
};

const formatRelativeTime = (time: string | null | undefined, t: TFn): string => {
  if (!time) return '-';
  const date = parseServerTime(time);
  if (!date || !date.isValid()) return '-';
  const diffMs = Date.now() - date.valueOf();
  if (diffMs < 60000) return t('executorPanel.justNow');
  if (diffMs < 3600000) return t('executorPanel.minutesAgo', { count: Math.floor(diffMs / 60000) });
  if (diffMs < 86400000) return t('executorPanel.hoursAgo', { count: Math.floor(diffMs / 3600000) });
  return t('executorPanel.daysAgo', { count: Math.floor(diffMs / 86400000) });
};

const clampPercent = (value: number): number => {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
};

const toProgressStatus = (status: string): 'normal' | 'active' | 'success' | 'exception' => {
  const normalized = normalizeJobStatus(status);
  if (normalized === 'running') return 'active';
  if (normalized === 'completed') return 'success';
  if (normalized === 'failed') return 'exception';
  return 'normal';
};

const getProgressText = (item: BackgroundTaskItem, t: TFn): string | null => {
  const parts: string[] = [];
  const hideStepProgress = item.job_type === 'plan_execute' && item.mode === 'full_plan';

  if (item.effective_status === 'blocked' && (item.incomplete_dependencies?.length ?? 0) > 0) {
    const deps = (item.incomplete_dependencies ?? []).map((d) => `#${d}`).join(', ');
    parts.push(t('executorPanel.blockedBy', { deps }));
  }

  if (typeof item.progress_text === 'string' && item.progress_text.trim()) {
  parts.push(item.progress_text.trim());
  } else if (
  typeof item.done_steps === 'number' &&
  Number.isFinite(item.done_steps) &&
  typeof item.total_steps === 'number' &&
  Number.isFinite(item.total_steps) &&
  item.total_steps > 0
  ) {
  parts.push(`${Math.max(0, Math.round(item.done_steps))}/${Math.max(0, Math.round(item.total_steps))}`);
  } else if (
  item.counts &&
  typeof item.counts.done === 'number' &&
  Number.isFinite(item.counts.done) &&
  typeof item.counts.total === 'number' &&
  Number.isFinite(item.counts.total) &&
  item.counts.total > 0
  ) {
  parts.push(`${Math.max(0, Math.round(item.counts.done))}/${Math.max(0, Math.round(item.counts.total))}`);
  }

  if (
  !hideStepProgress &&
  typeof item.current_step === 'number' &&
  Number.isFinite(item.current_step) &&
  item.current_step > 0
  ) {
  if (
  typeof item.total_steps === 'number' &&
  Number.isFinite(item.total_steps) &&
  item.total_steps > 0
  ) {
  parts.push(t('executorPanel.stepOf', { current: Math.round(item.current_step), total: Math.round(item.total_steps) }));
  } else {
  parts.push(t('executorPanel.step', { current: Math.round(item.current_step) }));
  }
  }

  if (
  typeof item.current_task_id === 'number' &&
  Number.isFinite(item.current_task_id) &&
  item.current_task_id > 0
  ) {
  parts.push(t('executorPanel.taskNum', { id: Math.round(item.current_task_id) }));
  }

  if (!parts.length) return null;
  return parts.join(' · ');
};

const resolveItemProgress = (
  item: BackgroundTaskItem,
  t: TFn
): { percent: number; status: 'normal' | 'active' | 'success' | 'exception'; text: string | null } => {
  const status =
    item.effective_status === 'blocked'
      ? 'normal'
      : toProgressStatus(item.progress_status || item.status || 'queued');
  const explicitPercent =
  typeof item.progress_percent === 'number' && Number.isFinite(item.progress_percent)
  ? clampPercent(item.progress_percent)
  : null;

  let fallbackPercent: number | null = null;
  if (
  typeof item.done_steps === 'number' &&
  Number.isFinite(item.done_steps) &&
  typeof item.total_steps === 'number' &&
  Number.isFinite(item.total_steps) &&
  item.total_steps > 0
  ) {
  fallbackPercent = clampPercent((item.done_steps / item.total_steps) * 100);
  } else if (
  item.counts &&
  typeof item.counts.done === 'number' &&
  Number.isFinite(item.counts.done) &&
  typeof item.counts.total === 'number' &&
  Number.isFinite(item.counts.total) &&
  item.counts.total > 0
  ) {
  fallbackPercent = clampPercent((item.counts.done / item.counts.total) * 100);
  }

  let percent = explicitPercent ?? fallbackPercent ?? 0;
  if (status === 'active' && percent >= 100) {
  percent = 99;
  }
  if (status === 'success' || status === 'exception') {
  percent = 100;
  }

  return {
  percent,
  status,
  text: getProgressText(item, t),
  };
};

interface TaskRowProps {
  item: BackgroundTaskItem;
  currentPlanId: number | null;
  isExpanded: boolean;
  canExpand: boolean;
  isProcessing: boolean;
  controlBusy: boolean;
  onToggle: (jobId: string) => void;
  onSendMessage: (msg: string, meta?: any) => void;
  onControlJob: (jobId: string, action: 'pause' | 'resume') => void;
}

const _itemFingerprint = (item: BackgroundTaskItem): string =>
  `${item.job_id}|${item.status}|${item.progress_percent ?? ''}|${item.progress_text ?? ''}|${item.done_steps ?? ''}|${item.total_steps ?? ''}|${item.current_step ?? ''}|${item.current_task_id ?? ''}|${item.error ?? ''}|${item.execution_paused ? '1' : ''}`;

const TaskRow = React.memo<TaskRowProps>(
  ({ item, currentPlanId, isExpanded, canExpand, isProcessing, controlBusy, onToggle, onSendMessage, onControlJob }) => {
    const t = useT();
    const effective = item.effective_status || null;
    const displayStatus = effective === 'blocked' ? 'queued' : item.status;
    const normalized = effective === 'blocked' ? 'queued' : normalizeJobStatus(item.status);
    const duration = formatDuration(item.started_at, item.finished_at);
    const progress = resolveItemProgress(item, t);
    const remoteMeta =
      item.category === 'phagescope'
        ? [item.remote_status, item.phase].filter((v) => typeof v === 'string' && v.trim()).join(' / ')
        : '';
    const countMeta =
      item.category === 'phagescope' && item.counts
        ? `${item.counts.done}/${item.counts.total}`
        : '';
    return (
      <React.Fragment>
        <tr
          className={`task-row${canExpand ? ' task-row-expandable' : ''}`}
          onClick={canExpand ? () => onToggle(item.job_id) : undefined}
          style={canExpand ? { cursor: 'pointer' } : undefined}
        >
          <td className="task-id" title={item.job_id}>
            <Space size={4}>
              {canExpand && (isExpanded ? <UpOutlined style={{ fontSize: 10 }} /> : <DownOutlined style={{ fontSize: 10 }} />)}
              <span>{getDisplayId(item)}</span>
            </Space>
          </td>
          <td className="task-content" title={item.label}>
            <div>{item.label}</div>
            {remoteMeta ? (
              <div className="task-content-meta">{t('executorPanel.remoteStatus', { value: remoteMeta })}</div>
            ) : null}
            {countMeta ? (
              <div className="task-content-meta">{t('executorPanel.moduleProgress', { value: countMeta })}</div>
            ) : null}
            {item.error ? (
              <div className="task-content-meta task-content-error">{item.error}</div>
            ) : null}
            <div className="task-progress-wrap">
              <Progress
                percent={progress.percent}
                status={progress.status}
                size="small"
                showInfo={false}
                strokeLinecap="round"
              />
              <div className="task-progress-meta">
                <span className="task-progress-percent">{progress.percent}%</span>
                {progress.text ? <span className="task-progress-text">{progress.text}</span> : null}
              </div>
            </div>
          </td>
          <td className="task-status">
            <span className={`task-status-dot ${normalized}`} />
            <span className="task-status-text">{effective === 'blocked' ? statusLabel(t, 'blocked') : getStatusLabel(displayStatus, t)}</span>
            {duration ? <span className="task-status-meta">{duration}</span> : null}
            <span className="task-status-meta">{formatRelativeTime(item.created_at, t)}</span>
            {item.job_type === 'plan_execute' && (normalized === 'running' || normalized === 'queued') && (
              <>
                {item.execution_paused ? (
                  <Tag color="gold" style={{ marginInlineStart: 4, fontSize: 12, lineHeight: '14px', padding: '0 4px' }}>
                    {t('executorPanel.paused')}
                  </Tag>
                ) : null}
                <Tooltip title={item.execution_paused ? t('executorPanel.resumeTip') : t('executorPanel.pauseTip')}>
                  <Button
                    type="link"
                    size="small"
                    icon={item.execution_paused ? <CaretRightOutlined /> : <PauseOutlined />}
                    loading={controlBusy}
                    onClick={(e) => {
                      e.stopPropagation();
                      onControlJob(item.job_id, item.execution_paused ? 'resume' : 'pause');
                    }}
                    style={{ padding: '0 4px', fontSize: 12, height: 'auto', lineHeight: 1 }}
                  >
                    {item.execution_paused ? t('executorPanel.resume') : t('executorPanel.pause')}
                  </Button>
                </Tooltip>
              </>
            )}
            {normalized === 'completed' && (
              <Tooltip title={t('executorPanel.analyzeTip')}>
                <Button
                  type="link"
                  size="small"
                  icon={<SearchOutlined />}
                  disabled={isProcessing}
                  onClick={(e) => {
                    e.stopPropagation();
                    const displayId = getDisplayId(item);
                    if (item.category === 'phagescope') {
                      const taskidHint = item.taskid ? `taskid=${item.taskid}` : `job_id=${item.job_id}`;
                      const prompt =
                        `/think PhageScope task (${taskidHint}) completed. ` +
                        `Please run phagescope save_all to download results, then use file_operations to read ` +
                        `summary.json, phage_info.json, quality.json, and proteins.tsv, and provide a concise analysis.`;
                      onSendMessage(prompt);
                    } else {
                      const prompt =
                        `Background task "${item.label}" (${displayId}) completed. ` +
                        `Please review execution logs, analyze task result status, and summarize key outcomes and issues.`;
                      onSendMessage(prompt, { analysis_only: true, source_job_id: item.job_id });
                    }
                  }}
                  style={{ padding: '0 4px', fontSize: 12, height: 'auto', lineHeight: 1 }}
                >
                  {t('executorPanel.analyze')}
                </Button>
              </Tooltip>
            )}
          </td>
        </tr>
        {canExpand && isExpanded && (
          <tr>
            <td colSpan={3} style={{ padding: 0 }}>
              <div style={{ padding: '4px 8px 12px', maxHeight: 480, overflowY: 'auto' }}>
                <JobLogPanel
                  jobId={item.job_id}
                  targetTaskName={item.label}
                  planId={item.plan_id ?? currentPlanId}
                  jobType={item.job_type}
                />
              </div>
            </td>
          </tr>
        )}
      </React.Fragment>
    );
  },
  (prev, next) =>
    prev.isExpanded === next.isExpanded &&
    prev.canExpand === next.canExpand &&
    prev.isProcessing === next.isProcessing &&
    prev.controlBusy === next.controlBusy &&
    prev.currentPlanId === next.currentPlanId &&
    _itemFingerprint(prev.item) === _itemFingerprint(next.item)
);

const ExecutorPanel: React.FC = () => {
  const t = useT();
  const currentSessionId = useChatStore(
  (state) => state.currentSession?.session_id ?? state.currentSession?.id ?? null
  );
  const currentPlanId = useChatStore((state) => state.currentPlanId ?? null);
  const sendMessage = useChatStore((state) => state.sendMessage);
  const isProcessing = useChatStore((state) =>
    state.processingSessionIds.has(
      resolveChatSessionProcessingKey(state.currentSession)
    )
  );
  const [board, setBoard] = useState<BackgroundTaskBoardResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedJobIds, setExpandedJobIds] = useState<Set<string>>(new Set());
  const [sseConnected, setSseConnected] = useState(false);

  const toggleJobExpand = useCallback((jobId: string) => {
  setExpandedJobIds((prev) => {
  const next = new Set(prev);
  if (next.has(jobId)) {
  next.delete(jobId);
  } else {
  next.add(jobId);
  }
  return next;
  });
  }, []);

  const boardFingerprintRef = useRef<string>('');
  const sseSourceRef = useRef<EventSource | null>(null);
  const pollerRef = useRef<number | null>(null);
  const pendingSnapshotRef = useRef<any>(null);
  const rafRef = useRef<number | null>(null);

  // ── helpers ──────────────────────────────────────────────────────────────

  const applySnapshot = useCallback((snapshot: BackgroundTaskBoardResponse) => {
  const fp = GROUP_ORDER.map((key) => {
  const group = snapshot?.groups?.[key];
  if (!group?.items?.length) return `${key}:0`;
  return `${key}:${group.items.map(_itemFingerprint).join(',')}`;
  }).join('|');
  if (fp !== boardFingerprintRef.current) {
  boardFingerprintRef.current = fp;
  setBoard(snapshot);
  }
  setError(null);
  }, []);

  const fetchBoard = useCallback(
  async (silent = false) => {
  try {
  if (!silent) setRefreshing(true);
  else setLoading(true);
  if (!currentSessionId && !currentPlanId) {
  setBoard(EMPTY_BOARD());
  setError(null);
  boardFingerprintRef.current = '';
  return;
  }
  const snapshot = await planTreeApi.getBackgroundTaskBoard({
  limit: 50,
  session_id: currentSessionId ?? undefined,
  plan_id: currentPlanId ?? undefined,
  include_finished: true,
  });
  applySnapshot(snapshot);
  } catch (err: any) {
  setError(err?.message || translate('executorPanel.loadFailed'));
  } finally {
  setLoading(false);
  setRefreshing(false);
  }
  },
  [currentPlanId, currentSessionId, applySnapshot]
  );

  const [controlBusyJobId, setControlBusyJobId] = useState<string | null>(null);

  const handleControlJob = useCallback(
  async (jobId: string, action: 'pause' | 'resume') => {
  setControlBusyJobId(jobId);
  try {
  const resp = await planTreeApi.controlJob(jobId, { action });
  if (!resp.success) {
  message.warning(resp.message || translate(action === 'pause' ? 'executorPanel.pauseFailed' : 'executorPanel.resumeFailed'));
  }
  } catch (err: any) {
  message.error(err?.message || translate(action === 'pause' ? 'executorPanel.pauseFailed' : 'executorPanel.resumeFailed'));
  } finally {
  setControlBusyJobId(null);
  void fetchBoard(true);
  }
  },
  [fetchBoard]
  );

  // ── SSE connection (with polling fallback) ────────────────────────────────

  const stopPolling = useCallback(() => {
  if (pollerRef.current !== null) {
  window.clearInterval(pollerRef.current);
  pollerRef.current = null;
  }
  }, []);

  const startPolling = useCallback(() => {
  if (pollerRef.current !== null) return;
  pollerRef.current = window.setInterval(() => {
  void fetchBoard(true);
  }, 8000);
  }, [fetchBoard]);

  const closeSse = useCallback(() => {
  if (sseSourceRef.current) {
  sseSourceRef.current.close();
  sseSourceRef.current = null;
  setSseConnected(false);
  }
  }, []);

  useEffect(() => {
  // Initial REST fetch so there's immediate data while SSE connects
  void fetchBoard(true);
  }, [fetchBoard]);

  useEffect(() => {
  // Build SSE URL with current filters
  const params = new URLSearchParams({ limit: '50', include_finished: 'true' });
  if (currentSessionId) params.set('session_id', currentSessionId);
  if (currentPlanId != null) params.set('plan_id', String(currentPlanId));
  const sseUrl = `${ENV.API_BASE_URL}/jobs/board/stream?${params.toString()}`;

  closeSse();
  stopPolling();

  try {
  const source = new EventSource(sseUrl, { withCredentials: true });
  sseSourceRef.current = source;

  source.onopen = () => {
  setSseConnected(true);
  setError(null);
  stopPolling(); // SSE is live; no need for polling
  };

   source.onmessage = (event) => {
   try {
   const data = JSON.parse(event.data);
   if (data?.type === 'snapshot' && data.board) {
     pendingSnapshotRef.current = data.board;
     if (rafRef.current === null) {
       rafRef.current = requestAnimationFrame(() => {
         rafRef.current = null;
         if (pendingSnapshotRef.current) {
           applySnapshot(pendingSnapshotRef.current);
           pendingSnapshotRef.current = null;
         }
       });
     }
   }
   } catch (_) {
   }
   };

  source.onerror = () => {
  // SSE dropped – fall back to polling
  setSseConnected(false);
  closeSse();
  startPolling();
  };
  } catch (_) {
  // EventSource not supported or URL invalid – fall back to polling
  startPolling();
  }

   return () => {
   closeSse();
   stopPolling();
   if (rafRef.current !== null) {
     cancelAnimationFrame(rafRef.current);
     rafRef.current = null;
   }
   };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSessionId, currentPlanId]);

  // ── tasksUpdated event: force an immediate REST refresh ───────────────────
  const timeoutIdsRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => {
  const onTasksUpdated = () => {
  timeoutIdsRef.current.forEach((id) => clearTimeout(id));
  timeoutIdsRef.current = [];
  // Trigger fast refresh; SSE will catch subsequent changes
  const t1 = setTimeout(() => void fetchBoard(true), 1000);
  timeoutIdsRef.current = [t1];
  };
  window.addEventListener('tasksUpdated', onTasksUpdated);
  return () => {
  window.removeEventListener('tasksUpdated', onTasksUpdated);
  timeoutIdsRef.current.forEach((id) => clearTimeout(id));
  timeoutIdsRef.current = [];
  };
  }, [fetchBoard]);

  const groups: BackgroundTaskGroup[] = useMemo(() => {
  if (!board) return [];
  return GROUP_ORDER.map((key) => board.groups[key]).filter(Boolean);
  }, [board]);

  useEffect(() => {
  if (!board) return;
  const autoExpand = new Set<string>();
  for (const key of GROUP_ORDER) {
  const group = board.groups[key];
  if (!group?.items) continue;
  for (const item of group.items) {
  const isExpandableJob =
    item.job_type === 'plan_execute' || item.job_type === 'bio_tools_run';
  const isRunning = normalizeJobStatus(item.status) === 'running';
  if (isExpandableJob && isRunning) {
  autoExpand.add(item.job_id);
  }
  }
  }
  if (autoExpand.size > 0) {
  setExpandedJobIds((prev) => {
  const merged = new Set(prev);
  let changed = false;
  autoExpand.forEach((id) => {
  if (!merged.has(id)) {
  merged.add(id);
  changed = true;
  }
  });
  return changed ? merged : prev;
  });
  }
  }, [board]);

  const totalItems = useMemo(() => groups.reduce((acc, group) => acc + (group?.items?.length || 0), 0), [groups]);

  if (loading && !board) {
  return (
  <div className="executor-empty">
  <div className="executor-empty-icon" />
  <span>{t('executorPanel.loading')}</span>
  </div>
  );
  }

  return (
  <div className="executor-container">
  <div className="executor-toolbar">
  <Space size={8}>
  <Button
  size="small"
  icon={<ReloadOutlined />}
  onClick={() => void fetchBoard(false)}
  loading={refreshing}
  >
  {t('common.refresh')}
  </Button>
  <Text type="secondary" style={{ fontSize: 12 }}>
  {t('executorPanel.taskCount', { count: totalItems })}
  </Text>
  <Tooltip title={sseConnected ? t('executorPanel.liveUpdates') : t('executorPanel.pollingMode')}>
  <span
  style={{
  display: 'inline-block',
  width: 6,
  height: 6,
  borderRadius: '50%',
  background: sseConnected ? 'var(--status-success-fg)' : 'var(--status-warning-fg)',
  flexShrink: 0,
  }}
  />
  </Tooltip>
  </Space>
  <Text type="secondary" style={{ fontSize: 12 }}>
  {t('executorPanel.updated', { time: board?.generated_at ? formatRelativeTime(board.generated_at, t) : '-' })}
  </Text>
  </div>

  {error ? (
  <div className="executor-error">
  <Text type="danger">{error}</Text>
  </div>
  ) : null}

  {totalItems === 0 ? (
  <div className="executor-empty">
  <div className="executor-empty-icon" />
  <span>{t('executorPanel.empty')}</span>
  </div>
  ) : (
  <div className="task-table-wrap">
  {groups.map((group) => (
  <div key={group.key} className="executor-group">
  <div className="executor-group-header">
  <Space size={8}>
  <Text strong>{groupLabel(group, t)}</Text>
  <Tag>{group.total}</Tag>
  {group.running > 0 ? <Tag color="blue">{t('executorPanel.tagRunning', { count: group.running })}</Tag> : null}
  {group.queued > 0 ? <Tag color="default">{t('executorPanel.tagPending', { count: group.queued })}</Tag> : null}
  {group.failed > 0 ? <Tag color="red">{t('executorPanel.tagFailed', { count: group.failed })}</Tag> : null}
  </Space>
  </div>
  <table className="task-table">
  <thead>
  <tr>
  <th>{t('executorPanel.colId')}</th>
  <th>{t('executorPanel.colContent')}</th>
  <th>{t('executorPanel.colStatus')}</th>
  </tr>
  </thead>
  <tbody>
  {group.items.map((item) => {
   const normalized = normalizeJobStatus(item.status);
   const canExpand =
     item.job_type === 'plan_execute' ||
     (item.category === 'code_executor' && normalized === 'running') ||
     item.job_type === 'bio_tools_run';
   return (
   <TaskRow
   key={item.job_id}
   item={item}
   currentPlanId={currentPlanId}
   isExpanded={expandedJobIds.has(item.job_id)}
   canExpand={canExpand}
   isProcessing={isProcessing}
   controlBusy={controlBusyJobId === item.job_id}
   onToggle={toggleJobExpand}
   onSendMessage={sendMessage}
   onControlJob={handleControlJob}
   />
   );
   })}
  </tbody>
  </table>
  </div>
  ))}
  </div>
  )}
  </div>
  );
};

export default React.memo(ExecutorPanel);
