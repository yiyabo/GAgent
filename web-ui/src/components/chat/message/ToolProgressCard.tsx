import React, { useState } from 'react';
import {
  Typography,
  Space,
  Button,
  Tag,
  Progress,
} from 'antd';
import { CloudSyncOutlined } from '@ant-design/icons';
import type { ChatMessage as ChatMessageType, CompactProgressState, DecompositionJobStatus } from '@/types';

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

const TOOL_LABELS: Record<string, string> = {
  web_search: 'web_search',
  literature_pipeline: 'literature_pipeline',
  document_reader: 'document_reader',
  file_operations: 'file_operations',
  graph_rag: 'graph_rag',
  result_interpreter: 'result_interpreter',
  claude_code: 'claude_code',
};

const PHASE_LABELS: Record<string, string> = {
  planning: 'Analyzing request',
  gathering: 'Searching sources',
  analyzing: 'Reviewing findings',
  synthesizing: 'Preparing answer',
  finalizing: 'Preparing answer',
};

const normalizeProgressText = (value: unknown) =>
  String(value ?? '')
    .replace(/\s+/g, ' ')
    .replace(/\s+([,.;:!?])/g, '$1')
    .trim();

const truncateProgressText = (value: unknown, maxChars: number) => {
  const normalized = normalizeProgressText(value);
  if (normalized.length <= maxChars) return normalized;
  return `${normalized.slice(0, Math.max(0, maxChars - 1)).trimEnd()}…`;
};

const formatToolName = (tool: unknown) => {
  const normalized = String(tool ?? '').trim();
  if (!normalized) return 'Tool';
  return TOOL_LABELS[normalized] ?? normalized;
};

const formatPhaseTitle = (phase: unknown) => {
  const normalized = String(phase ?? '').trim();
  if (!normalized) return 'Working on the request';
  return PHASE_LABELS[normalized] ?? 'Working on the request';
};

const formatCompactStatus = (status: unknown) => {
  const normalized = String(status ?? '').trim().toLowerCase();
  if (normalized === 'retrying') return 'Retrying';
  if (normalized === 'failed' || normalized === 'error') return 'Failed';
  if (normalized === 'running' || normalized === 'active') return 'Running';
  if (normalized === 'completed' || normalized === 'success' || normalized === 'done') return 'Completed';
  return null;
};

const isSameText = (left: unknown, right: unknown) =>
  normalizeProgressText(left).toLowerCase() === normalizeProgressText(right).toLowerCase();

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
  const [expandedTextKeys, setExpandedTextKeys] = useState<Record<string, boolean>>({});
  const unifiedStream = Boolean(metadata && (metadata as any).unified_stream);
  const deepThinkProgress =
    (metadata as any)?.deep_think_progress &&
    typeof (metadata as any)?.deep_think_progress === 'object'
      ? ((metadata as any).deep_think_progress as CompactProgressState)
      : null;
  const progressVisibility = (metadata as any)?.thinking_visibility;
  if (!unifiedStream && !(progressVisibility === 'progress' && deepThinkProgress)) return null;
  const status = metadata?.status;
  const actions =
    (Array.isArray(metadata?.actions) ? metadata?.actions : null) ??
    (Array.isArray(metadata?.raw_actions) ? metadata?.raw_actions : []);
  if ((!actions || actions.length === 0) && !(progressVisibility === 'progress' && deepThinkProgress)) return null;
  const visibleActions = actions;
  if (visibleActions.length === 0 && !(progressVisibility === 'progress' && deepThinkProgress)) return null;
  const toolActions = visibleActions.filter((act: any) => act?.kind === 'tool_operation');

  const toggleExpandedText = (key: string) => {
    setExpandedTextKeys((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const renderExpandableText = (
    rawValue: unknown,
    key: string,
    maxChars: number,
    opts?: { muted?: boolean; singleLine?: boolean },
  ) => {
    const value = normalizeProgressText(rawValue);
    if (!value) return null;
    const expanded = Boolean(expandedTextKeys[key]);
    const shouldClamp = value.length > maxChars;
    const displayValue = expanded || !shouldClamp ? value : truncateProgressText(value, maxChars);
    return (
      <div style={{ display: 'flex', alignItems: opts?.singleLine ? 'center' : 'flex-start', gap: 6, flexWrap: 'wrap' }}>
        <Text
          style={{
            color: opts?.muted ? 'var(--text-secondary)' : 'var(--text-primary)',
            fontSize: 12,
            lineHeight: 1.6,
            whiteSpace: opts?.singleLine ? 'nowrap' : 'normal',
          }}
        >
          {displayValue}
        </Text>
        {shouldClamp && (
          <Button
            type="link"
            size="small"
            onClick={() => toggleExpandedText(key)}
            style={{ padding: 0, height: 'auto', fontSize: 12 }}
          >
            {expanded ? 'Less' : 'More'}
          </Button>
        )}
      </div>
    );
  };

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
    progressVisibility === 'progress' && deepThinkProgress
      ? (typeof deepThinkProgress.label === 'string' && deepThinkProgress.label.trim()
          ? deepThinkProgress.label
          : 'Working on the request')
      : toolActions.length > 0
      ? `Tools: ${toolActions
        .map((act: any) => (typeof act?.name === 'string' ? act.name : null))
        .filter(Boolean)
        .slice(0, 3)
        .join(', ')}${toolActions.length > 3 ? ` and ${toolActions.length - 3} more` : ''}`
      : `Actions: ${visibleActions.length}`;
  const compactProgressLabel =
    progressVisibility === 'progress' && deepThinkProgress
      ? (typeof deepThinkProgress.label === 'string' ? deepThinkProgress.label : 'Working on the request')
      : null;
  const compactProgressPhase =
    progressVisibility === 'progress' && deepThinkProgress && typeof deepThinkProgress.phase === 'string'
      ? deepThinkProgress.phase
      : null;
  const compactProgressStatus =
    progressVisibility === 'progress' && deepThinkProgress && typeof deepThinkProgress.status === 'string'
      ? deepThinkProgress.status
      : null;
  const compactProgressIteration =
    progressVisibility === 'progress' && deepThinkProgress && typeof deepThinkProgress.iteration === 'number'
      ? deepThinkProgress.iteration
      : null;
  const compactProgressHistory =
    progressVisibility === 'progress' && deepThinkProgress && Array.isArray(deepThinkProgress.history)
      ? deepThinkProgress.history
      : [];

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

  if (progressVisibility === 'progress' && deepThinkProgress) {
    const progressFinished = String(deepThinkProgress.status ?? status ?? '')
      .trim()
      .toLowerCase() === 'completed';
    const compactStatus = progressFinished
      ? null
      : formatCompactStatus(
          deepThinkProgress.current_status ?? deepThinkProgress.status ?? status
        );
    const currentTool = normalizeProgressText(deepThinkProgress.current_tool || deepThinkProgress.tool);
    const compactToolItems = Array.isArray(deepThinkProgress.tool_items)
      ? deepThinkProgress.tool_items
      : [];
    const compactHistory = Array.isArray(deepThinkProgress.history) ? deepThinkProgress.history : [];
    const derivedToolItems =
      compactToolItems.length > 0
        ? compactToolItems
        : compactHistory.reduce<Array<{ tool: string; label?: string | null; status: string; details?: string | null }>>(
            (acc, entry) => {
              const tool = normalizeProgressText((entry as any)?.tool);
              if (!tool) return acc;
              const nextItem = {
                tool,
                label: normalizeProgressText((entry as any)?.label) || null,
                status: normalizeProgressText((entry as any)?.status) || 'running',
                details: null,
              };
              const existingIndex = acc.findIndex((item) => item.tool === tool);
              if (existingIndex >= 0) {
                acc[existingIndex] = {
                  ...acc[existingIndex],
                  ...nextItem,
                };
              } else {
                acc.push(nextItem);
              }
              return acc;
            },
            [],
          );
    const latestToolItem =
      derivedToolItems.length > 0 ? derivedToolItems[derivedToolItems.length - 1] : null;
    const currentTitle = progressFinished
      ? latestToolItem
        ? formatToolName(latestToolItem.tool)
        : 'Answer ready'
      : currentTool
        ? formatToolName(currentTool)
        : formatPhaseTitle(deepThinkProgress.phase);
    const currentSubtitle = progressFinished
      ? normalizeProgressText(latestToolItem?.details || latestToolItem?.label) || null
      : normalizeProgressText(deepThinkProgress.current_label || deepThinkProgress.label) || null;
    const currentDetails = progressFinished
      ? null
      : normalizeProgressText((deepThinkProgress as any).current_details) || null;
    const compactNotes = Array.isArray(deepThinkProgress.expanded_notes)
      ? deepThinkProgress.expanded_notes
      : [];
    const visibleNotes = compactNotes.filter(
      (note) => !isSameText(note, currentSubtitle) && !isSameText(note, currentDetails)
    );

    return (
      <div
        style={{
          marginBottom: 10,
          padding: '12px 14px',
          borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border-color)',
          background: 'var(--bg-tertiary)',
          fontSize: 12,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, minWidth: 0, flex: 1 }}>
            <div
              style={{
                width: 10,
                height: 10,
                marginTop: 5,
                borderRadius: 999,
                background:
                  compactStatus === 'Failed'
                    ? '#ff7875'
                    : compactStatus === 'Retrying'
                      ? '#faad14'
                      : 'var(--primary-color)',
                boxShadow:
                  compactStatus === 'Failed'
                    ? '0 0 0 4px rgba(255, 120, 117, 0.12)'
                    : compactStatus === 'Retrying'
                      ? '0 0 0 4px rgba(250, 173, 20, 0.12)'
                      : '0 0 0 4px color-mix(in srgb, var(--primary-color) 16%, transparent)',
                flexShrink: 0,
              }}
            />
            <div style={{ minWidth: 0, flex: 1 }}>
              <Space size={8} align="center" wrap>
                <Text strong style={{ fontSize: 14, color: 'var(--text-primary)' }}>
                  {currentTitle}
                </Text>
                {compactStatus && compactStatus !== 'Completed' && (
                  <Tag color={compactStatus === 'Failed' ? 'red' : compactStatus === 'Retrying' ? 'gold' : 'blue'} style={{ margin: 0 }}>
                    {compactStatus}
                  </Tag>
                )}
              </Space>
              {(currentSubtitle || currentDetails) && (
                <div style={{ marginTop: 6, minWidth: 0 }}>
                  {renderExpandableText(currentDetails || currentSubtitle, 'progress_current', 96, { muted: true, singleLine: true })}
                </div>
              )}
            </div>
          </div>
          <Button
            type="link"
            size="small"
            onClick={() => setToolOpen((v) => !v)}
            style={{ padding: 0, fontSize: 12, flexShrink: 0 }}
          >
            {toolOpen ? 'Collapse' : 'Expand'}
          </Button>
        </div>

        {toolOpen && (
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {derivedToolItems.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {derivedToolItems.map((item, idx) => {
                  const toolStatus = formatCompactStatus(item.status);
                  const details = normalizeProgressText(item.details || item.label);
                  return (
                    <div
                      key={`${item.tool}_${idx}`}
                      style={{
                        borderRadius: 10,
                        padding: '10px 12px',
                        border: '1px solid color-mix(in srgb, var(--border-color) 88%, transparent)',
                        background: 'var(--bg-secondary)',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                        <Text strong style={{ fontSize: 13, color: 'var(--text-primary)' }}>
                          {formatToolName(item.tool)}
                        </Text>
                        {toolStatus && toolStatus !== 'Completed' && (
                          <Tag color={toolStatus === 'Failed' ? 'red' : toolStatus === 'Retrying' ? 'gold' : 'blue'} style={{ margin: 0 }}>
                            {toolStatus}
                          </Tag>
                        )}
                      </div>
                      {details && (
                        <div style={{ marginTop: 6 }}>
                          {renderExpandableText(details, `tool_item_${idx}`, 120, { muted: true })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {visibleNotes.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <Text style={{ color: 'var(--text-secondary)', fontSize: 12 }}>Notes</Text>
                {visibleNotes.map((note, idx) => (
                  <div key={`note_${idx}`}>
                    {renderExpandableText(note, `note_${idx}`, 120, { muted: true })}
                  </div>
                ))}
              </div>
            )}

            {processSummary && !isSameText(processSummary, currentSubtitle) && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <Text style={{ color: 'var(--text-secondary)', fontSize: 12 }}>Current summary</Text>
                {renderExpandableText(processSummary, 'progress_summary', 160, { muted: true })}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

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
        {compactProgressLabel && (
          <div style={{ marginTop: 6 }}>
            <Text style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
              {compactProgressPhase ? `${compactProgressPhase}: ` : ''}
              {compactProgressLabel}
              {compactProgressIteration !== null ? ` · step ${compactProgressIteration}` : ''}
              {compactProgressStatus && compactProgressStatus !== 'active' ? ` · ${compactProgressStatus}` : ''}
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
          {compactProgressHistory.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <Text style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                Progress:
              </Text>
              <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 4 }}>
                {compactProgressHistory.map((entry: any, idx: number) => (
                  <Text key={`${entry?.phase ?? 'phase'}_${idx}`} style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                    {(typeof entry?.label === 'string' ? entry.label : 'Working on the request')}
                    {typeof entry?.tool === 'string' && entry.tool ? ` · ${entry.tool}` : ''}
                  </Text>
                ))}
              </div>
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
