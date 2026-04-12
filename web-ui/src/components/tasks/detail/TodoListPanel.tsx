import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Modal,
  Progress,
  Space,
  Spin,
  Steps,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  MinusCircleOutlined,
  ReloadOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import { planTreeApi } from '@api/planTree';
import type { TodoItemResponse, TodoListResponse, TodoPhaseResponse } from '@/types';

const { Text, Title, Paragraph } = Typography;

// ---- Status rendering helpers ----

const phaseStatusConfig: Record<string, { color: string; label: string }> = {
  completed: { color: 'success', label: 'Completed' },
  in_progress: { color: 'processing', label: 'In Progress' },
  pending: { color: 'warning', label: 'Pending' },
  partial_failure: { color: 'error', label: 'Partial Failure' },
  empty: { color: 'default', label: 'Empty' },
};

const taskStatusIcon: Record<string, React.ReactNode> = {
  completed: <CheckCircleOutlined style={{ color: '#52c41a' }} />,
  pending: <ClockCircleOutlined style={{ color: '#faad14' }} />,
  failed: <CloseCircleOutlined style={{ color: '#ff4d4f' }} />,
  skipped: <MinusCircleOutlined style={{ color: '#d9d9d9' }} />,
  running: <ExclamationCircleOutlined style={{ color: '#1890ff' }} />,
};

const taskStatusColor: Record<string, string> = {
  completed: 'green',
  pending: 'gold',
  failed: 'red',
  skipped: 'default',
  running: 'processing',
};

// ---- Phase step status mapping for Steps component ----
function phaseToStepStatus(status: string): 'finish' | 'process' | 'wait' | 'error' {
  switch (status) {
    case 'completed':
      return 'finish';
    case 'in_progress':
      return 'process';
    case 'partial_failure':
      return 'error';
    default:
      return 'wait';
  }
}

// ---- Sub-components ----

const TodoTaskItem: React.FC<{
  item: TodoItemResponse;
  onTaskClick?: (taskId: number) => void;
}> = ({ item, onTaskClick }) => {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 8,
        padding: '6px 8px',
        borderRadius: 6,
        border: '1px solid #f0f0f0',
        background: item.status === 'completed' ? '#f6ffed' : undefined,
      }}
    >
      <span style={{ marginTop: 2, flexShrink: 0 }}>
        {taskStatusIcon[item.status] ?? taskStatusIcon.pending}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Space size={6} wrap>
          <Tag color={taskStatusColor[item.status] ?? 'default'} style={{ marginRight: 0 }}>
            {item.status}
          </Tag>
          <Button
            type="link"
            size="small"
            style={{ padding: 0, height: 'auto' }}
            onClick={() => onTaskClick?.(item.task_id)}
          >
            #{item.task_id} {item.name}
          </Button>
        </Space>
        {item.instruction && (
          <Paragraph
            type="secondary"
            style={{ margin: '2px 0 0', fontSize: 12, whiteSpace: 'pre-wrap' }}
            ellipsis={{ rows: 2, expandable: true, symbol: 'more' }}
          >
            {item.instruction}
          </Paragraph>
        )}
        {item.dependencies.length > 0 && (
          <Text type="secondary" style={{ fontSize: 11 }}>
            deps: {item.dependencies.map((d) => `#${d}`).join(', ')}
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
  const pct = phase.total > 0 ? Math.round((phase.completed / phase.total) * 100) : 0;
  const cfg = phaseStatusConfig[phase.status] ?? phaseStatusConfig.pending;

  return (
    <div
      style={{
        border: '1px solid #e8e8e8',
        borderRadius: 8,
        padding: '12px 16px',
        background: '#fafafa',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <Space size={8}>
          <Badge status={cfg.color as any} />
          <Title level={5} style={{ margin: 0 }}>
            Phase {phase.phase_id}: {phase.label}
          </Title>
        </Space>
        <Space size={8}>
          <Tag color={cfg.color}>{cfg.label}</Tag>
          <Text type="secondary">
            {phase.completed}/{phase.total}
          </Text>
        </Space>
      </div>

      <Progress
        percent={pct}
        size="small"
        status={phase.status === 'partial_failure' ? 'exception' : undefined}
        strokeColor={phase.status === 'completed' ? '#52c41a' : undefined}
      />

      <Space direction="vertical" size={4} style={{ width: '100%', marginTop: 8 }}>
        {phase.items.map((item) => (
          <TodoTaskItem key={item.task_id} item={item} onTaskClick={onTaskClick} />
        ))}
      </Space>
    </div>
  );
};

// ---- Main panel ----

interface TodoListPanelProps {
  open: boolean;
  onClose: () => void;
  planId: number | null;
  targetTaskId: number | null;
  onTaskClick?: (taskId: number) => void;
}

const TodoListPanel: React.FC<TodoListPanelProps> = ({
  open,
  onClose,
  planId,
  targetTaskId,
  onTaskClick,
}) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [todoList, setTodoList] = useState<TodoListResponse | null>(null);

  const fetchTodoList = useCallback(async () => {
    if (!planId || !targetTaskId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await planTreeApi.getPlanTodoList(planId, targetTaskId);
      setTodoList(data);
    } catch (err: any) {
      setError(err?.message || 'Failed to load todo list');
    } finally {
      setLoading(false);
    }
  }, [planId, targetTaskId]);

  useEffect(() => {
    if (open && planId && targetTaskId) {
      void fetchTodoList();
    }
    if (!open) {
      setTodoList(null);
      setError(null);
    }
  }, [open, planId, targetTaskId, fetchTodoList]);

  const overallPct =
    todoList && todoList.total_tasks > 0
      ? Math.round((todoList.completed_tasks / todoList.total_tasks) * 100)
      : 0;

  return (
    <Modal
      open={open}
      title={
        <Space>
          <UnorderedListOutlined />
          <span>Todo List — Task #{targetTaskId}</span>
        </Space>
      }
      onCancel={onClose}
      width={720}
      footer={[
        <Button key="refresh" icon={<ReloadOutlined />} onClick={fetchTodoList} loading={loading}>
          Refresh
        </Button>,
        <Button key="close" onClick={onClose}>
          Close
        </Button>,
      ]}
    >
      {loading && !todoList ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '32px 0' }}>
          <Spin tip="Loading todo list..." />
        </div>
      ) : error ? (
        <Alert type="error" message="Failed to load" description={error} showIcon />
      ) : !todoList ? (
        <Text type="secondary">No data</Text>
      ) : (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          {/* Overall progress */}
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <Text strong>
                Overall Progress: {todoList.completed_tasks}/{todoList.total_tasks} tasks
              </Text>
              <Text type="secondary">{todoList.phases.length} phases</Text>
            </div>
            <Progress percent={overallPct} />
          </div>

          {/* Phase timeline */}
          {todoList.phases.length > 0 && (
            <Steps
              size="small"
              current={todoList.phases.findIndex((p) => p.status !== 'completed')}
              items={todoList.phases.map((phase) => ({
                title: (
                  <Tooltip title={`${phase.completed}/${phase.total} completed`}>
                    <span>{phase.label}</span>
                  </Tooltip>
                ),
                status: phaseToStepStatus(phase.status),
                description: `${phase.completed}/${phase.total}`,
              }))}
            />
          )}

          {/* Phase cards */}
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {todoList.phases.map((phase) => (
              <TodoPhaseCard key={phase.phase_id} phase={phase} onTaskClick={onTaskClick} />
            ))}
          </Space>

          {/* Summary */}
          {todoList.summary && (
            <Paragraph
              type="secondary"
              style={{ whiteSpace: 'pre-wrap', fontSize: 12, marginTop: 8 }}
              copyable
              ellipsis={{ rows: 4, expandable: true, symbol: 'Show full summary' }}
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
