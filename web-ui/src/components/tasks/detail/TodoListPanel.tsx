import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Modal,
  Progress,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  MinusCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import { planTreeApi } from '@api/planTree';
import JobLogPanel from '@/components/chat/JobLogPanel';
import type { TodoItemResponse, TodoListResponse, TodoPhaseResponse } from '@/types';
import { translate, useT } from '@/i18n/useT';
import type { TFn } from '@/i18n/useT';
import { statusLabel } from '@/i18n/statusLabels';

const { Text, Title, Paragraph } = Typography;

const phaseStatusColor: Record<string, string> = {
  completed: 'success',
  in_progress: 'processing',
  pending: 'warning',
  partial_failure: 'error',
  empty: 'default',
};

const phaseStatusLabel = (key: string, t: TFn): string => {
  switch (key) {
    case 'completed':
      return statusLabel(t, 'completed');
    case 'in_progress':
      return t('todoList.inProgress');
    case 'partial_failure':
      return t('toolDrawer.partialFailure');
    case 'empty':
      return t('todoList.empty');
    default:
      return statusLabel(t, 'pending');
  }
};

const taskStatusIcon: Record<string, React.ReactNode> = {
  completed: <CheckCircleOutlined style={{ color: 'var(--status-success-fg)' }} />,
  pending: <ClockCircleOutlined style={{ color: 'var(--status-warning-fg)' }} />,
  failed: <CloseCircleOutlined style={{ color: 'var(--status-error-fg)' }} />,
  skipped: <MinusCircleOutlined style={{ color: 'var(--border-default)' }} />,
  running: <ExclamationCircleOutlined style={{ color: 'var(--color-action)' }} />,
  blocked: <MinusCircleOutlined style={{ color: 'var(--status-warning-fg)' }} />,
};

const taskStatusColor: Record<string, string> = {
  completed: 'green',
  pending: 'gold',
  failed: 'red',
  skipped: 'default',
  running: 'processing',
  blocked: 'orange',
};

const TodoTaskItem: React.FC<{
  item: TodoItemResponse;
  onTaskClick?: (taskId: number) => void;
  showPhaseTag?: boolean;
}> = ({ item, onTaskClick, showPhaseTag = false }) => {
  const t = useT();
  const displayStatus = item.effective_status || item.status;
  const incomplete = item.incomplete_dependencies ?? [];
  const blocked =
    (item.blocked_by_dependencies ?? false) || displayStatus === 'blocked';
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 8,
        padding: '6px 8px',
        borderRadius: 6,
        border: '1px solid var(--border-subtle)',
        background: displayStatus === 'completed' ? 'var(--status-success-bg)' : undefined,
      }}
    >
      <span style={{ marginTop: 2, flexShrink: 0 }}>
        {taskStatusIcon[displayStatus] ?? taskStatusIcon.pending}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Space size={6} wrap>
          <Tag color={taskStatusColor[displayStatus] ?? 'default'} style={{ marginRight: 0 }}>
            {statusLabel(t, displayStatus)}
          </Tag>
          {showPhaseTag && (
            <Tag color="blue" style={{ marginRight: 0 }}>
              {t('todoList.phase', { n: item.phase + 1 })}
            </Tag>
          )}
          <Button
            type="link"
            size="small"
            style={{ padding: 0, height: 'auto' }}
            onClick={() => onTaskClick?.(item.task_id)}
          >
            #{item.task_id} {item.name}
          </Button>
        </Space>
        {blocked && incomplete.length > 0 && (
          <div style={{ marginTop: 2 }}>
            <Tooltip title={item.status_reason || undefined}>
              <Text type="warning" style={{ fontSize: 12 }}>
                {t('executorPanel.blockedBy', { deps: incomplete.map((d) => `#${d}`).join(', ') })}
              </Text>
            </Tooltip>
          </div>
        )}
        {item.instruction && (
          <Paragraph
            type="secondary"
            style={{ margin: '2px 0 0', fontSize: 12, whiteSpace: 'pre-wrap' }}
            ellipsis={{ rows: 2, expandable: true, symbol: t('common.more') }}
          >
            {item.instruction}
          </Paragraph>
        )}
        {item.dependencies.length > 0 && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('todoList.deps', { deps: item.dependencies.map((d) => `#${d}`).join(', ') })}
          </Text>
        )}
      </div>
    </div>
  );
};

const TodoPhaseCard: React.FC<{
  phase: TodoPhaseResponse;
  onTaskClick?: (taskId: number) => void;
}> = ({ phase, onTaskClick }) => {
  const t = useT();
  const pct = phase.total > 0 ? Math.round((phase.completed / phase.total) * 100) : 0;
  const phaseColor = phaseStatusColor[phase.status] ?? phaseStatusColor.pending;
  const allCompleted = phase.status === 'completed';
  const [collapsed, setCollapsed] = useState(allCompleted);

  return (
    <div
      style={{
        border: '1px solid var(--border-subtle)',
        borderRadius: 8,
        padding: '12px 16px',
        background: 'var(--surface-sunken)',
      }}
    >
      <div
        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: collapsed ? 0 : 8, cursor: 'pointer' }}
        onClick={() => setCollapsed(!collapsed)}
      >
        <Space size={8}>
          <Badge status={phaseColor as any} />
          <Title level={5} style={{ margin: 0 }}>
            {phase.label.startsWith('Phase ') ? phase.label : t('todoList.phaseTitle', { n: phase.phase_id + 1, label: phase.label })}
          </Title>
        </Space>
        <Space size={8}>
          <Tag color={phaseColor}>{phaseStatusLabel(phase.status, t)}</Tag>
          <Text type="secondary">
            {phase.completed}/{phase.total}
          </Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {collapsed ? '▸' : '▾'}
          </Text>
        </Space>
      </div>

      {!collapsed && (
        <>
          <Progress
            percent={pct}
            size="small"
            status={phase.status === 'partial_failure' ? 'exception' : undefined}
            strokeColor={phase.status === 'completed' ? 'var(--status-success-fg)' : undefined}
          />

          <Space direction="vertical" size={4} style={{ width: '100%', marginTop: 8 }}>
            {phase.items.map((item) => (
              <TodoTaskItem key={item.task_id} item={item} onTaskClick={onTaskClick} />
            ))}
          </Space>
        </>
      )}
    </div>
  );
};

interface TodoListPanelProps {
  open: boolean;
  onClose: () => void;
  planId: number | null;
  currentSessionId?: string | null;
  targetTaskId: number | null;
  onTaskClick?: (taskId: number) => void;
  fullPlan?: boolean;
}

const TodoListPanel: React.FC<TodoListPanelProps> = ({
  open,
  onClose,
  planId,
  currentSessionId,
  targetTaskId,
  onTaskClick,
  fullPlan = false,
}) => {
  const t = useT();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [todoList, setTodoList] = useState<TodoListResponse | null>(null);
  const [executing, setExecuting] = useState(false);
  const [executionJobId, setExecutionJobId] = useState<string | null>(null);

  const fetchTodoList = useCallback(async () => {
    if (!planId) return;
    if (!fullPlan && !targetTaskId) return;
    setLoading(true);
    setError(null);
    try {
      const data = fullPlan
        ? await planTreeApi.getFullPlanTodoList(planId)
        : await planTreeApi.getPlanTodoList(planId!, targetTaskId!);
      setTodoList(data);
    } catch (err: any) {
      setError(err?.message || translate('todoList.loadListFailed'));
    } finally {
      setLoading(false);
    }
  }, [planId, targetTaskId, fullPlan]);

  useEffect(() => {
    if (open && planId && (fullPlan || targetTaskId)) {
      void fetchTodoList();
      // Check for active execution job (may have been started via DeepThink or another path)
      planTreeApi.getActiveJob(planId).then((res) => {
        if (res.job_id) {
          setExecutionJobId(prev => prev ?? res.job_id);
        }
      }).catch(() => {});
    }
    if (!open) {
      setTodoList(null);
      setError(null);
      setExecutionJobId(null);
    }
  }, [open, planId, targetTaskId, fullPlan, fetchTodoList]);

  const handleExecuteFullPlan = useCallback(async () => {
    if (!planId) return;
    setExecuting(true);
    try {
      const result = await planTreeApi.executeFullPlan(planId, {
        async_mode: true,
        session_id: currentSessionId ?? undefined,
        skip_completed: true,
        stop_on_failure: false,
      });
      if (result.success) {
        const jobId = result.result?.job_id || result.job?.job_id || null;
        setExecutionJobId(jobId);
        message.success(result.message || translate('todoList.executionStarted'));
        setTimeout(() => void fetchTodoList(), 1500);
      } else {
        message.error(result.message || translate('todoList.executionStartFailed'));
      }
    } catch (err: any) {
      message.error(err?.message || translate('todoList.executeFailed'));
    } finally {
      setExecuting(false);
    }
  }, [currentSessionId, planId, fetchTodoList]);

  const overallPct =
    todoList && todoList.total_tasks > 0
      ? Math.round((todoList.completed_tasks / todoList.total_tasks) * 100)
      : 0;

  const isFullPlanMode = fullPlan || targetTaskId === null;
  const hasPending = todoList ? todoList.pending_order.length > 0 : false;
  const titleText = isFullPlanMode
    ? t('todoList.titleFullPlan')
    : t('todoList.titleTask', { id: targetTaskId });

  return (
    <Modal
      open={open}
      title={
        <Space>
          <UnorderedListOutlined />
          <span>{titleText}</span>
        </Space>
      }
      onCancel={onClose}
      width={720}
      footer={[
        isFullPlanMode && hasPending ? (
          <Button
            key="execute"
            type="primary"
            icon={<PlayCircleOutlined />}
            onClick={handleExecuteFullPlan}
            loading={executing}
          >
            {t('todoList.executeFullPlan')}
          </Button>
        ) : null,
        <Button key="refresh" icon={<ReloadOutlined />} onClick={fetchTodoList} loading={loading}>
          {t('common.refresh')}
        </Button>,
        <Button key="close" onClick={onClose}>
          {t('common.close')}
        </Button>,
      ].filter(Boolean)}
    >
      {executionJobId && (
        <JobLogPanel
          jobId={executionJobId}
          jobType="plan_execute"
          planId={planId ?? undefined}
        />
      )}
      {loading && !todoList ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '32px 0' }}>
          <Spin tip={t('todoList.loading')} />
        </div>
      ) : error ? (
        <Alert type="error" message={t('todoList.loadFailed')} description={error} showIcon />
      ) : !todoList ? (
        <Text type="secondary">{t('todoList.noData')}</Text>
      ) : (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <Text strong>
                {t('todoList.overall', { done: todoList.completed_tasks, total: todoList.total_tasks })}
              </Text>
              <Text type="secondary">
                {t('todoList.phaseCount', { count: todoList.phases.length })}
              </Text>
            </div>
            <Progress percent={overallPct} />
          </div>

          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {isFullPlanMode && (
              <Alert
                type="info"
                showIcon
                message={t('todoList.orderedTitle')}
                description={t('todoList.orderedDesc')}
              />
            )}
            {todoList.phases.map((phase) => (
              <TodoPhaseCard key={phase.phase_id} phase={phase} onTaskClick={onTaskClick} />
            ))}
          </Space>

          {todoList.summary && (
            <Paragraph
              type="secondary"
              style={{ whiteSpace: 'pre-wrap', fontSize: 12, marginTop: 8 }}
              copyable
              ellipsis={{ rows: 4, expandable: true, symbol: t('todoList.showFullSummary') }}
            >
              {todoList.summary}
            </Paragraph>
          )}
        </Space>
      )}
    </Modal>
  );
};

export default TodoListPanel;
