import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import { Button, Progress, Space, Tag, Tooltip, Typography } from 'antd';
import { ReloadOutlined, SearchOutlined, DownOutlined, UpOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { planTreeApi } from '@api/planTree';
import { ENV } from '@/config/env';
import JobLogPanel from '@components/chat/JobLogPanel';
import type {
  BackgroundTaskBoardResponse,
  BackgroundTaskCategory,
  BackgroundTaskGroup,
  BackgroundTaskItem,
} from '@/types';
import { resolveChatSessionProcessingKey } from '@/utils/chatSessionKeys';
import './ExecutorPanel.css';

dayjs.extend(relativeTime);
const { Text } = Typography;

const GROUP_ORDER: BackgroundTaskCategory[] = ['task_creation', 'phagescope', 'code_executor'];
const FINAL_STATUSES = new Set(['succeeded', 'completed', 'failed']);

const parseServerTime = (time?: string | null) => {
  if (!time) return null;
  const raw = String(time).trim();
  if (!raw) return null;
  // Backend may emit SQLite-style timestamps without timezone; treat them as UTC to avoid local offset drift.
  const hasZone = /[zZ]|[+\-]\d{2}:\d{2}$/.test(raw);
  if (hasZone) return dayjs(raw);
  if (raw.includes('T')) return dayjs(`${raw}Z`);
  return dayjs(`${raw.replace(' ', 'T')}Z`);
};

const EMPTY_BOARD = (): BackgroundTaskBoardResponse => ({
  generated_at: new Date().toISOString(),
  total: 0,
  groups: {
  task_creation: {
  key: 'task_creation',
  label: 'taskcreate',
  total: 0,
  running: 0,
  queued: 0,
  succeeded: 0,
  failed: 0,
  items: [],
  },
  phagescope: {
  key: 'phagescope',
  label: 'PhageScope',
  total: 0,
  running: 0,
  queued: 0,
  succeeded: 0,
  failed: 0,
  items: [],
  },
  code_executor: {
  key: 'code_executor',
  label: 'Claude Code',
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

const getStatusLabel = (status: string): string => {
  switch (normalizeJobStatus(status)) {
  case 'running':
  return 'medium';
  case 'completed':
  return 'completed';
  case 'failed':
  return 'failed';
  default:
  return 'queued';
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

const formatRelativeTime = (time?: string | null): string => {
  if (!time) return '-';
  const date = parseServerTime(time);
  if (!date || !date.isValid()) return '-';
  const diffMs = Date.now() - date.valueOf();
  if (diffMs < 60000) return '';
  if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)}`;
  if (diffMs < 86400000) return `${Math.floor(diffMs / 3600000)}`;
  return `${Math.floor(diffMs / 86400000)}`;
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

const getProgressText = (item: BackgroundTaskItem): string | null => {
  const parts: string[] = [];

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
  typeof item.current_step === 'number' &&
  Number.isFinite(item.current_step) &&
  item.current_step > 0
  ) {
  if (
  typeof item.total_steps === 'number' &&
  Number.isFinite(item.total_steps) &&
  item.total_steps > 0
  ) {
  parts.push(`step ${Math.round(item.current_step)}/${Math.round(item.total_steps)}`);
  } else {
  parts.push(`step ${Math.round(item.current_step)}`);
  }
  }

  if (
  typeof item.current_task_id === 'number' &&
  Number.isFinite(item.current_task_id) &&
  item.current_task_id > 0
  ) {
  parts.push(`task #${Math.round(item.current_task_id)}`);
  }

  if (!parts.length) return null;
  return parts.join(' · ');
};

const resolveItemProgress = (
  item: BackgroundTaskItem
): { percent: number; status: 'normal' | 'active' | 'success' | 'exception'; text: string | null } => {
  const status = toProgressStatus(item.progress_status || item.status || 'queued');
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
  text: getProgressText(item),
  };
};

interface TaskRowProps {
  item: BackgroundTaskItem;
  currentPlanId: number | null;
  isExpanded: boolean;
  canExpand: boolean;
  isProcessing: boolean;
  onToggle: (jobId: string) => void;
  onSendMessage: (msg: string, meta?: any) => void;
}

const _itemFingerprint = (item: BackgroundTaskItem): string =>
  `${item.job_id}|${item.status}|${item.progress_percent ?? ''}|${item.progress_text ?? ''}|${item.done_steps ?? ''}|${item.total_steps ?? ''}|${item.current_step ?? ''}|${item.current_task_id ?? ''}|${item.error ?? ''}`;

const TaskRow = React.memo<TaskRowProps>(
  ({ item, currentPlanId, isExpanded, canExpand, isProcessing, onToggle, onSendMessage }) => {
    const normalized = normalizeJobStatus(item.status);
    const duration = formatDuration(item.started_at, item.finished_at);
    const progress = resolveItemProgress(item);
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
              <div className="task-content-meta">status: {remoteMeta}</div>
            ) : null}
            {countMeta ? (
              <div className="task-content-meta">progress: {countMeta}</div>
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
            <span className="task-status-text">{getStatusLabel(item.status)}</span>
            {duration ? <span className="task-status-meta">{duration}</span> : null}
            <span className="task-status-meta">{formatRelativeTime(item.created_at)}</span>
            {normalized === 'completed' && (
              <Tooltip title="Ask Agent to analyze task results">
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
                  style={{ padding: '0 4px', fontSize: 11, height: 'auto', lineHeight: 1 }}
                >
                  Analyze
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
    prev.currentPlanId === next.currentPlanId &&
    _itemFingerprint(prev.item) === _itemFingerprint(next.item)
);

const ExecutorPanel: React.FC = () => {
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
  setError(err?.message || 'Failed to load background tasks');
  } finally {
  setLoading(false);
  setRefreshing(false);
  }
  },
  [currentPlanId, currentSessionId, applySnapshot]
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
  applySnapshot(data.board as BackgroundTaskBoardResponse);
  }
  // heartbeat: do nothing
  } catch (_) {
  // ignore parse errors
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
  <span>Loading background tasks...</span>
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
  Refresh
  </Button>
  <Text type="secondary" style={{ fontSize: 12 }}>
  {totalItems} tasks
  </Text>
  <Tooltip title={sseConnected ? 'Live updates (SSE)' : 'Polling mode'}>
  <span
  style={{
  display: 'inline-block',
  width: 6,
  height: 6,
  borderRadius: '50%',
  background: sseConnected ? '#52c41a' : '#faad14',
  flexShrink: 0,
  }}
  />
  </Tooltip>
  </Space>
  <Text type="secondary" style={{ fontSize: 12 }}>
  Updated: {board?.generated_at ? formatRelativeTime(board.generated_at) : '-'}
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
  <span>No tasks</span>
  </div>
  ) : (
  <div className="task-table-wrap">
  {groups.map((group) => (
  <div key={group.key} className="executor-group">
  <div className="executor-group-header">
  <Space size={8}>
  <Text strong>{group.label}</Text>
  <Tag>{group.total}</Tag>
  {group.running > 0 ? <Tag color="blue">Running {group.running}</Tag> : null}
  {group.queued > 0 ? <Tag color="default">Pending {group.queued}</Tag> : null}
  {group.failed > 0 ? <Tag color="red">Failed {group.failed}</Tag> : null}
  </Space>
  </div>
  <table className="task-table">
  <thead>
  <tr>
  <th>Task ID</th>
  <th>Content</th>
  <th>Status</th>
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
   onToggle={toggleJobExpand}
   onSendMessage={sendMessage}
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

export default ExecutorPanel;
