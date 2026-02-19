import React, { useState } from 'react';
import {
  Typography,
  Space,
  Button,
  Tag,
  Progress,
} from 'antd';
import { CloudSyncOutlined } from '@ant-design/icons';
import type { ChatMessage as ChatMessageType, DecompositionJobStatus } from '@/types';

const { Text } = Typography;

export const formatToolActionLabel = (action: any) => {
  const kind = typeof action?.kind === 'string' ? action.kind : '';
  const name = typeof action?.name === 'string' ? action.name : '';
  const params = action?.parameters && typeof action.parameters === 'object' ? action.parameters : {};
  const query = typeof params.query === 'string' ? params.query.trim() : '';

  if (kind === 'tool_operation') {
    if (name && query) {
      return `Use ${name} to search "${query}"`;
    }
    if (name) {
      return `Invoke ${name}`;
    }
    return 'Invoke tool';
  }
  if (kind === 'plan_operation') {
    const title =
      typeof params.plan_title === 'string'
        ? params.plan_title
        : typeof params.title === 'string'
          ? params.title
          : '';
    return title ? `Execute plan: ${title}` : `Execute plan operation${name ? ` ${name}` : ''}`;
  }
  if (kind === 'task_operation') {
    const taskName =
      typeof params.task_name === 'string'
        ? params.task_name
        : typeof params.title === 'string'
          ? params.title
          : '';
    return taskName ? `Execute task: ${taskName}` : `Execute task operation${name ? ` ${name}` : ''}`;
  }
  if (kind === 'context_request') {
    return `Fetch context${name ? `: ${name}` : ''}`;
  }
  if (kind === 'system_operation') {
    return `System operation${name ? `: ${name}` : ''}`;
  }
  return `${kind || 'action'}${name ? `/${name}` : ''}`;
};

export const deriveToolActionStatusIcon = (action: any, messageStatus?: string) => {
  if (action?.status === 'completed' || action?.success === true) return '✅';
  if (action?.status === 'failed' || action?.success === false) return '⚠️';
  if (messageStatus === 'completed') return '✅';
  if (messageStatus === 'failed') return '⚠️';
  return '⏳';
};

interface ToolProgressCardProps {
  metadata: ChatMessageType['metadata'];
  isDecomposeActive: boolean;
  isDecomposeFailed: boolean;
  decomposeProgress: {
    status: string;
    percent: number | null;
    totalBudget: number | null;
    consumedBudget: number | null;
    queueRemaining: number | null;
    createdCount: number | null;
    processedCount: number | null;
  } | null;
  effectiveDecomposeJob: DecompositionJobStatus | null;
  processSummary: string | null;
}

const ToolProgressCard: React.FC<ToolProgressCardProps> = ({
  metadata,
  isDecomposeActive,
  isDecomposeFailed,
  decomposeProgress,
  effectiveDecomposeJob,
  processSummary,
}) => {
  const [toolOpen, setToolOpen] = useState(false);
  const unifiedStream = Boolean(metadata && (metadata as any).unified_stream);
  if (!unifiedStream) return null;
  const status = metadata?.status;
  const actions =
    (Array.isArray(metadata?.actions) ? metadata?.actions : null) ??
    (Array.isArray(metadata?.raw_actions) ? metadata?.raw_actions : []);
  if (!actions || actions.length === 0) return null;
  const visibleActions = actions;
  if (visibleActions.length === 0) return null;
  const toolActions = visibleActions.filter((act: any) => act?.kind === 'tool_operation');

  const effectiveStatus = isDecomposeFailed ? 'failed' : isDecomposeActive ? 'running' : status;
  const statusLabel =
    isDecomposeActive
      ? 'Decomposing Plan'
      : effectiveStatus === 'completed'
      ? 'Completed'
      : effectiveStatus === 'failed'
        ? 'Failed'
        : 'Running';
  const statusColor =
    isDecomposeFailed
      ? 'red'
      : isDecomposeActive
        ? 'blue'
        : effectiveStatus === 'completed'
      ? 'green'
      : effectiveStatus === 'failed'
        ? 'red'
        : 'blue';

  const summary =
    toolActions.length > 0
      ? `Tools: ${toolActions
        .map((act: any) => (typeof act?.name === 'string' ? act.name : null))
        .filter(Boolean)
        .slice(0, 3)
        .join(', ')}${toolActions.length > 3 ? ` and ${toolActions.length - 3} more` : ''}`
      : `Actions: ${visibleActions.length}`;

  const toolProgress = (metadata as any)?.tool_progress as any;
  const toolProgressPercent =
    toolProgress && typeof toolProgress?.percent === 'number' ? toolProgress.percent : null;
  const toolProgressStatus =
    toolProgress && typeof toolProgress?.status === 'string' ? toolProgress.status : null;
  const toolProgressCounts =
    toolProgress && typeof toolProgress?.counts === 'object' ? toolProgress.counts : null;
  const toolProgressModules =
    toolProgress && Array.isArray(toolProgress?.modules) ? toolProgress.modules : null;
  const moduleDone =
    toolProgressCounts && typeof toolProgressCounts.done === 'number' ? toolProgressCounts.done : null;
  const moduleTotal =
    toolProgressCounts && typeof toolProgressCounts.total === 'number' ? toolProgressCounts.total : null;
  const decomposePercent =
    isDecomposeActive && decomposeProgress?.percent !== null && decomposeProgress?.percent !== undefined
      ? Math.max(0, Math.min(99, Math.round(decomposeProgress.percent)))
      : null;
  const progressPercent =
    isDecomposeActive
      ? decomposePercent ?? 0
      : effectiveStatus === 'completed' || effectiveStatus === 'failed'
        ? 100
        : toolProgressPercent !== null
          ? Math.max(0, Math.min(99, Math.round(toolProgressPercent)))
          : 0;
  const progressStatus =
    effectiveStatus === 'failed' ? 'exception' : effectiveStatus === 'completed' ? 'success' : 'active';

  return (
    <div
      style={{
        marginBottom: 10,
        padding: '10px 12px',
        borderRadius: 'var(--radius-sm)',
        border: '1px solid var(--border-color)',
        background: 'var(--bg-tertiary)',
        fontSize: 12,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Space size={8} align="center">
          <Tag color={statusColor} style={{ margin: 0 }}>
            {statusLabel}
          </Tag>
          <Text style={{ color: 'var(--text-secondary)' }}>{summary}</Text>
        </Space>
        <Button
          type="link"
          size="small"
          onClick={() => setToolOpen((v) => !v)}
          style={{ padding: 0, fontSize: 12 }}
        >
          {toolOpen ? 'Collapse' : 'View Process'}
        </Button>
      </div>
      <div style={{ marginTop: 8 }}>
        <Progress
          percent={progressPercent}
          status={progressStatus}
          showInfo={false}
          size="small"
        />
        {isDecomposeActive && (
          <div style={{ marginTop: 6 }}>
            <Text style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
              Decomposition progress:
              {decomposeProgress?.percent !== null && decomposeProgress?.percent !== undefined
                ? `${Math.round(decomposeProgress.percent)}%`
                : 'Calculating'}
              {decomposeProgress?.consumedBudget !== null &&
                decomposeProgress?.consumedBudget !== undefined
                ? `, created ${Math.max(0, Math.round(decomposeProgress.consumedBudget))} tasks`
                : ''}
              {decomposeProgress?.queueRemaining !== null && decomposeProgress?.queueRemaining !== undefined
                ? `, queue remaining ${decomposeProgress.queueRemaining}`
                : ''}
            </Text>
          </div>
        )}
        {isDecomposeFailed && (
          <div style={{ marginTop: 6 }}>
            <Text style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
              Decomposition failed{effectiveDecomposeJob?.error ? `: ${effectiveDecomposeJob.error}` : ''}
            </Text>
          </div>
        )}
        {moduleDone !== null && moduleTotal !== null && effectiveStatus !== 'completed' && effectiveStatus !== 'failed' && (
          <div style={{ marginTop: 6 }}>
            <Text style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
              Module progress: {moduleDone}/{moduleTotal}
            </Text>
          </div>
        )}
        {toolProgressStatus && effectiveStatus !== 'completed' && effectiveStatus !== 'failed' && (
          <div style={{ marginTop: 6 }}>
            <Text style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
              Current status: {toolProgressStatus}
            </Text>
          </div>
        )}
      </div>
      {toolOpen && (
        <div style={{ marginTop: 8 }}>
          {processSummary && (
            <div style={{ marginBottom: 8 }}>
              <Text style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                Summary: {processSummary}
              </Text>
            </div>
          )}
          {toolProgressModules && toolProgressModules.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <Text style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                Module completion:
              </Text>
              <div style={{ marginTop: 6 }}>
                <Space size={[6, 6]} wrap>
                  {toolProgressModules.map((m: any, idx: number) => {
                    const name = typeof m?.name === 'string' ? m.name : `module_${idx + 1}`;
                    const done = typeof m?.done === 'boolean' ? m.done : null;
                    const rawStatus =
                      typeof m?.status === 'string' ? m.status : (done === true ? 'DONE' : 'RUNNING');
                    const upper = String(rawStatus).toUpperCase();
                    const failed = done === false || upper === 'FAILED' || upper === 'ERROR';
                    const color = failed ? 'red' : done === true ? 'green' : 'blue';
                    return (
                      <Tag key={`${name}_${idx}`} color={color} style={{ marginInlineEnd: 0 }}>
                        {name}
                      </Tag>
                    );
                  })}
                </Space>
              </div>
            </div>
          )}
          {visibleActions.map((action: any, index: number) => {
            const label = formatToolActionLabel(action);
            const order = typeof action?.order === 'number' ? action.order : index + 1;
            const statusIcon = deriveToolActionStatusIcon(action, metadata?.status);
            const createdTasks = Array.isArray(action?.details?.created)
              ? (action.details.created as Array<Record<string, any>>)
              : [];
            const singleTask = action?.details?.task && typeof action.details.task === 'object'
              ? (action.details.task as Record<string, any>)
              : null;
            return (
              <div
                key={`${order}_${action?.name ?? 'action'}`}
                style={{ color: 'var(--text-secondary)', marginBottom: 4, fontSize: 12 }}
              >
                {statusIcon} Step {order}: {label}
                {singleTask && (
                  <div style={{ marginTop: 6, paddingLeft: 18 }}>
                    {typeof singleTask.name === 'string' && (
                      <div>Subtask: {singleTask.name}</div>
                    )}
                    {typeof singleTask.instruction === 'string' && singleTask.instruction.trim().length > 0 && (
                      <div>Instruction: {singleTask.instruction}</div>
                    )}
                  </div>
                )}
                {createdTasks.length > 0 && (
                  <div style={{ marginTop: 6, paddingLeft: 18 }}>
                    {createdTasks.map((task, idx) => {
                      const name =
                        typeof task?.name === 'string'
                          ? task.name
                          : typeof task?.title === 'string'
                            ? task.title
                            : '';
                      const instruction =
                        typeof task?.instruction === 'string' ? task.instruction : '';
                      return (
                        <div key={`${order}_created_${idx}`} style={{ marginBottom: 6 }}>
                          {name ? <div>Subtask: {name}</div> : null}
                          {instruction ? <div>Instruction: {instruction}</div> : null}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

// ---- Background dispatch card ----
interface BackgroundDispatchCardProps {
  metadata: ChatMessageType['metadata'];
}

export const BackgroundDispatchCard: React.FC<BackgroundDispatchCardProps> = ({ metadata }) => {
  const bgCategory = (metadata as any)?.background_category as string | undefined;
  const isBackgroundDispatch = Boolean(bgCategory && (bgCategory === 'phagescope' || bgCategory === 'claude_code' || bgCategory === 'task_creation'));
  if (!isBackgroundDispatch) return null;
  const categoryLabels: Record<string, string> = {
    phagescope: 'PhageScope',
    claude_code: 'Claude Code',
    task_creation: 'Task Creation / Decomposition',
  };
  const categoryColors: Record<string, string> = {
    phagescope: 'purple',
    claude_code: 'geekblue',
    task_creation: 'cyan',
  };
  const label = categoryLabels[bgCategory!] ?? bgCategory;
  const color = categoryColors[bgCategory!] ?? 'blue';
  const trackingId = typeof (metadata as any)?.tracking_id === 'string'
    ? ((metadata as any).tracking_id as string)
    : null;

  return (
    <div
      style={{
        marginBottom: 10,
        padding: '12px 14px',
        borderRadius: 'var(--radius-sm)',
        border: '1px solid color-mix(in srgb, var(--primary-color) 30%, var(--border-color))',
        background: 'color-mix(in srgb, var(--primary-color) 4%, var(--bg-tertiary))',
        fontSize: 13,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <CloudSyncOutlined style={{ color: 'var(--primary-color)', fontSize: 16 }} />
        <Text strong style={{ fontSize: 13, color: 'var(--text-primary)' }}>
          Background task submitted
        </Text>
        <Tag color={color} style={{ margin: 0, fontSize: 11 }}>{label}</Tag>
      </div>
      <Text style={{ color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.6 }}>
        The task is running in the background. Track progress in the right-side "Task Status" panel, then ask me for result analysis when it completes.
      </Text>
      {trackingId && (
        <div style={{ marginTop: 6 }}>
          <Text style={{ color: 'var(--text-tertiary)', fontSize: 11 }}>
            Tracking: {trackingId.slice(0, 16)}...
          </Text>
        </div>
      )}
    </div>
  );
};

export { ToolProgressCard };
export default ToolProgressCard;
