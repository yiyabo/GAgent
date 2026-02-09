import React, { useCallback, useEffect, useMemo, useState } from 'react';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import { Button, Space, Tag, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { planTreeApi } from '@api/planTree';
import type {
  BackgroundTaskBoardResponse,
  BackgroundTaskCategory,
  BackgroundTaskGroup,
  BackgroundTaskItem,
} from '@/types';
import './ExecutorPanel.css';

dayjs.extend(relativeTime);
const { Text } = Typography;

const GROUP_ORDER: BackgroundTaskCategory[] = ['task_creation', 'phagescope', 'claude_code'];
const FINAL_STATUSES = new Set(['succeeded', 'completed', 'failed']);

const EMPTY_BOARD = (): BackgroundTaskBoardResponse => ({
  generated_at: new Date().toISOString(),
  total: 0,
  groups: {
    task_creation: {
      key: 'task_creation',
      label: '任务创建',
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
    claude_code: {
      key: 'claude_code',
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
      return '运行中';
    case 'completed':
      return '已完成';
    case 'failed':
      return '失败';
    default:
      return '排队中';
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
  const start = dayjs(startedAt);
  const end = finishedAt ? dayjs(finishedAt) : dayjs();
  const diffMs = end.diff(start);
  if (diffMs < 1000) return '<1s';
  if (diffMs < 60000) return `${Math.floor(diffMs / 1000)}s`;
  if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)}m`;
  return `${Math.floor(diffMs / 3600000)}h`;
};

const formatRelativeTime = (time?: string | null): string => {
  if (!time) return '-';
  const date = dayjs(time);
  const diffMs = Date.now() - date.valueOf();
  if (diffMs < 60000) return '刚刚';
  if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)}分钟前`;
  if (diffMs < 86400000) return `${Math.floor(diffMs / 3600000)}小时前`;
  return `${Math.floor(diffMs / 86400000)}天前`;
};

const ExecutorPanel: React.FC = () => {
  const currentSessionId = useChatStore(
    (state) => state.currentSession?.session_id ?? state.currentSession?.id ?? null
  );
  const currentPlanId = useChatStore((state) => state.currentPlanId ?? null);
  const [board, setBoard] = useState<BackgroundTaskBoardResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchBoard = useCallback(
    async (silent = false) => {
      try {
        if (!silent) {
          setRefreshing(true);
        } else {
          setLoading(true);
        }
        if (!currentSessionId && !currentPlanId) {
          setBoard(EMPTY_BOARD());
          setError(null);
          return;
        }
        const snapshot = await planTreeApi.getBackgroundTaskBoard({
          limit: 50,
          session_id: currentSessionId ?? undefined,
          plan_id: currentPlanId ?? undefined,
          include_finished: true,
        });
        setBoard(snapshot);
        setError(null);
      } catch (err: any) {
        setError(err?.message || '后台任务加载失败');
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [currentPlanId, currentSessionId]
  );

  useEffect(() => {
    void fetchBoard(true);
  }, [fetchBoard]);

  const runningExists = useMemo(() => {
    if (!board) return false;
    return GROUP_ORDER.some((key) => {
      const group = board.groups[key];
      if (!group || !Array.isArray(group.items)) return false;
      return group.items.some((item) => !FINAL_STATUSES.has(String(item.status || '').toLowerCase()));
    });
  }, [board]);

  useEffect(() => {
    if (!runningExists) return undefined;
    const timer = window.setInterval(() => {
      void fetchBoard(true);
    }, 8000);
    return () => window.clearInterval(timer);
  }, [runningExists, fetchBoard]);

  const groups: BackgroundTaskGroup[] = useMemo(() => {
    if (!board) return [];
    return GROUP_ORDER.map((key) => board.groups[key]).filter(Boolean);
  }, [board]);

  const totalItems = useMemo(() => groups.reduce((acc, group) => acc + (group?.items?.length || 0), 0), [groups]);

  if (loading && !board) {
    return (
      <div className="executor-empty">
        <div className="executor-empty-icon" />
        <span>加载后台任务中...</span>
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
            刷新
          </Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            共 {totalItems} 条
          </Text>
        </Space>
        <Text type="secondary" style={{ fontSize: 12 }}>
          更新时间：{board?.generated_at ? formatRelativeTime(board.generated_at) : '-'}
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
          <span>暂无任务记录</span>
        </div>
      ) : (
        <div className="task-table-wrap">
          {groups.map((group) => (
            <div key={group.key} className="executor-group">
              <div className="executor-group-header">
                <Space size={8}>
                  <Text strong>{group.label}</Text>
                  <Tag>{group.total}</Tag>
                  {group.running > 0 ? <Tag color="blue">运行中 {group.running}</Tag> : null}
                  {group.queued > 0 ? <Tag color="default">排队 {group.queued}</Tag> : null}
                  {group.failed > 0 ? <Tag color="red">失败 {group.failed}</Tag> : null}
                </Space>
              </div>
              <table className="task-table">
                <thead>
                  <tr>
                    <th>Task ID</th>
                    <th>内容</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {group.items.map((item) => {
                    const normalized = normalizeJobStatus(item.status);
                    const duration = formatDuration(item.started_at, item.finished_at);
                    const remoteMeta =
                      item.category === 'phagescope'
                        ? [item.remote_status, item.phase].filter((v) => typeof v === 'string' && v.trim()).join(' / ')
                        : '';
                    const countMeta =
                      item.category === 'phagescope' && item.counts
                        ? `${item.counts.done}/${item.counts.total}`
                        : '';
                    return (
                      <tr key={item.job_id} className="task-row">
                        <td className="task-id" title={item.job_id}>
                          {getDisplayId(item)}
                        </td>
                        <td className="task-content" title={item.label}>
                          <div>{item.label}</div>
                          {remoteMeta ? (
                            <div className="task-content-meta">远端状态：{remoteMeta}</div>
                          ) : null}
                          {countMeta ? (
                            <div className="task-content-meta">模块进度：{countMeta}</div>
                          ) : null}
                          {item.error ? (
                            <div className="task-content-meta task-content-error">{item.error}</div>
                          ) : null}
                        </td>
                        <td className="task-status">
                          <span className={`task-status-dot ${normalized}`} />
                          <span className="task-status-text">{getStatusLabel(item.status)}</span>
                          {duration ? <span className="task-status-meta">{duration}</span> : null}
                          <span className="task-status-meta">{formatRelativeTime(item.created_at)}</span>
                        </td>
                      </tr>
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
