import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { ThinkingProcess as ThinkingProcessType, ThinkingStep } from '@/types';
import {
  CaretRightOutlined,
  LoadingOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  BulbOutlined,
  ToolOutlined,
  SearchOutlined,
  CodeOutlined,
  FileTextOutlined,
  ExperimentOutlined,
  GlobalOutlined,
  DatabaseOutlined,
  EyeOutlined,
  ProjectOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { motion, AnimatePresence } from 'framer-motion';
import { Button } from 'antd';
import './ThinkingProcess.css';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface ThinkingProcessProps {
  process: ThinkingProcessType;
  isFinished?: boolean;
  canControl?: boolean;
  onPause?: () => void;
  onResume?: () => void;
  onSkipStep?: () => void;
  paused?: boolean;
  controlDisabled?: boolean;
  controlBusy?: boolean;
  controlBusyAction?: 'pause' | 'resume' | 'skip_step' | null;
}

interface ToolSemantic {
  icon: React.ReactNode;
  label: string;
  toolName: string;
}

/* ------------------------------------------------------------------ */
/*  Semantic label extraction                                          */
/* ------------------------------------------------------------------ */

const TOOL_META: Record<string, { icon: React.ReactNode; defaultLabel: string }> = {
  web_search:          { icon: <GlobalOutlined />,      defaultLabel: 'Searching the web' },
  file_operations:     { icon: <FileTextOutlined />,    defaultLabel: 'Accessing files' },
  claude_code:         { icon: <CodeOutlined />,        defaultLabel: 'Executing code' },
  bio_tools:           { icon: <ExperimentOutlined />,  defaultLabel: 'Running bioinformatics analysis' },
  document_reader:     { icon: <FileTextOutlined />,    defaultLabel: 'Reading document' },
  vision_reader:       { icon: <EyeOutlined />,         defaultLabel: 'Analyzing visual content' },
  graph_rag:           { icon: <DatabaseOutlined />,    defaultLabel: 'Querying knowledge graph' },
  phagescope:          { icon: <ExperimentOutlined />,  defaultLabel: 'Running PhageScope analysis' },
  result_interpreter:  { icon: <DatabaseOutlined />,    defaultLabel: 'Interpreting results' },
  plan_operation:      { icon: <ProjectOutlined />,     defaultLabel: 'Managing plan' },
};

function extractSemanticLabel(actionStr: string | null | undefined): ToolSemantic | null {
  if (!actionStr) return null;
  let parsed: any;
  try {
    parsed = JSON.parse(actionStr);
  } catch {
    return { icon: <ToolOutlined />, label: 'Running tool', toolName: 'unknown' };
  }
  if (Array.isArray(parsed?.tools) && parsed.tools.length > 0) {
    const toolNames = parsed.tools
      .map((item: any) => (typeof item?.tool === 'string' ? item.tool : null))
      .filter((name: string | null): name is string => !!name);
    const preview = toolNames.slice(0, 2).join(', ');
    const suffix = toolNames.length > 2 ? ` +${toolNames.length - 2}` : '';
    return {
      icon: <ToolOutlined />,
      label: `Running ${toolNames.length} tools${preview ? ` (${preview}${suffix})` : ''}`,
      toolName: toolNames[0] || 'multi_tool',
    };
  }
  const toolName: string = parsed?.tool || 'unknown';
  const params: Record<string, any> = parsed?.params || {};
  const meta = TOOL_META[toolName] || { icon: <ToolOutlined />, defaultLabel: `Running ${toolName}` };
  let label = meta.defaultLabel;

  switch (toolName) {
    case 'web_search':
      if (params.query) label = `Searching: "${String(params.query).slice(0, 60)}"`;
      break;
    case 'file_operations': {
      const fileName = (params.path || '').split('/').pop() || params.path || '';
      if (params.operation === 'list') label = `Listing directory: ${params.path || '/'}`;
      else if (params.operation === 'read') label = `Reading: ${fileName}`;
      else if (params.operation) label = `${params.operation}: ${fileName}`;
      break;
    }
    case 'claude_code':
      if (params.task) label = `Code: ${String(params.task).slice(0, 80)}`;
      break;
    case 'bio_tools':
      if (params.tool_name && params.operation) label = `${params.tool_name} ${params.operation}`;
      else if (params.tool_name) label = `Running ${params.tool_name}`;
      break;
    case 'document_reader':
      if (params.file_path) label = `Reading: ${(params.file_path || '').split('/').pop()}`;
      break;
    case 'vision_reader':
      if (params.file_path) label = `Analyzing: ${(params.file_path || '').split('/').pop()}`;
      break;
    case 'phagescope':
      if (params.action) label = `PhageScope: ${params.action}`;
      break;
    case 'plan_operation':
      if (params.operation) label = `Plan: ${params.operation}`;
      break;
    case 'result_interpreter':
      if (params.operation) label = `Interpreting: ${params.operation}`;
      break;
  }

  return { icon: meta.icon, label, toolName };
}

function extractResultSummary(result: string | null | undefined): string | null {
  if (!result) return null;
  const trimmed = result.trim();
  if (trimmed.length <= 100) return trimmed;
  const firstLine = trimmed.split('\n')[0].trim();
  if (firstLine.length >= 15 && firstLine.length <= 200) return firstLine;
  return trimmed.slice(0, 100) + '...';
}

function stepHasToolError(step: ThinkingStep): boolean {
  if (step.status === 'error') return true;
  const r = step.action_result;
  return typeof r === 'string' && /^Error[ :]/.test(r);
}

function _toMs(value?: string): number | null {
  if (!value) return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

function formatDurationMs(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return '--';
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

function stepDurationMs(step: ThinkingStep): number | null {
  const start = _toMs(step.started_at) ?? _toMs(step.timestamp);
  const end = _toMs(step.finished_at) ?? _toMs(step.timestamp);
  if (start === null || end === null) return null;
  return Math.max(0, end - start);
}

/* ------------------------------------------------------------------ */
/*  ErrorInline – compact error + correction hint                      */
/* ------------------------------------------------------------------ */

const ErrorInline: React.FC<{
  step: ThinkingStep;
  nextStep?: ThinkingStep;
}> = ({ step, nextStep }) => {
  const errorDetail = step.action_result?.match(/^Error[: ]\s*(.+)/s)?.[1]
    ?? step.thought?.slice(0, 120)
    ?? 'Unknown error';
  const truncated = errorDetail.length > 120 ? errorDetail.slice(0, 120) + '...' : errorDetail;

  return (
    <div className="tp-error-inline">
      <span>{truncated}</span>
      {step.self_correction && (
        <>
          {' '}
          <SyncOutlined style={{ fontSize: 10 }} />{' '}
          <span className="tp-error-correction">{step.self_correction.slice(0, 100)}</span>
        </>
      )}
      {nextStep && !step.self_correction && (
        <>
          {' '}
          <SyncOutlined style={{ fontSize: 10 }} />{' '}
          <span className="tp-error-correction">Retrying...</span>
        </>
      )}
    </div>
  );
};

/* ------------------------------------------------------------------ */
/*  ThinkingStepItem – inline pill / thought aside                     */
/* ------------------------------------------------------------------ */

const ThinkingStepItem: React.FC<{
  step: ThinkingStep;
  index: number;
  isLast: boolean;
  isFinished?: boolean;
  nextStep?: ThinkingStep;
}> = ({ step, isLast, isFinished, nextStep }) => {
  const [detailExpanded, setDetailExpanded] = useState(false);
  const [thoughtExpanded, setThoughtExpanded] = useState(false);

  const isTool = !!step.action;
  const isError = stepHasToolError(step);
  const hasResult = !!step.action_result;
  const duration = stepDurationMs(step);
  const isStepComplete =
    step.status === 'done' ||
    step.status === 'completed' ||
    hasResult ||
    (!isLast && step.status !== 'thinking' && step.status !== 'calling_tool');

  const semantic = useMemo(
    () => extractSemanticLabel(step.action),
    [step.action],
  );

  const resultSummary = useMemo(
    () => extractResultSummary(step.action_result),
    [step.action_result],
  );

  const actionDetails = useMemo(() => {
    if (!step.action) return null;
    try {
      return JSON.parse(step.action);
    } catch {
      return { tool: 'unknown', params: step.action };
    }
  }, [step.action]);

  const THOUGHT_TRUNCATE_LEN = 160;
  const thoughtIsTruncatable = step.thought && step.thought.length > THOUGHT_TRUNCATE_LEN;
  const visibleThought = thoughtIsTruncatable && !thoughtExpanded
    ? step.thought!.slice(0, THOUGHT_TRUNCATE_LEN) + '...'
    : step.thought;

  const renderStatus = () => {
    if (step.status === 'calling_tool') {
      return (
        <span className="tp-tool-pill-status running">
          <LoadingOutlined spin style={{ fontSize: 11 }} />
        </span>
      );
    }
    if (isError) {
      return (
        <span className="tp-tool-pill-status error">
          <CloseCircleOutlined style={{ fontSize: 11 }} />
        </span>
      );
    }
    if (isStepComplete || isFinished) {
      return (
        <span className="tp-tool-pill-status success">
          <CheckCircleOutlined style={{ fontSize: 11 }} />
        </span>
      );
    }
    return null;
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2 }}
    >
      {/* Thought text as aside */}
      {step.thought && !isTool && (
        <div className="tp-thought-inline">
          {visibleThought}
          {thoughtIsTruncatable && (
            <button
              className="tp-thought-toggle"
              onClick={() => setThoughtExpanded((v) => !v)}
            >
              {thoughtExpanded ? 'Collapse' : 'Show more'}
            </button>
          )}
        </div>
      )}

      {/* Thought before tool (short preview) */}
      {step.thought && isTool && (
        <div className="tp-thought-inline" style={{ marginBottom: 1 }}>
          {step.thought.length > 80 ? step.thought.slice(0, 80) + '...' : step.thought}
        </div>
      )}

      {/* Tool Pill row */}
      {isTool && semantic && (
        <>
          <div
            className="tp-tool-pill"
            onClick={() => setDetailExpanded((v) => !v)}
          >
            <div className={`tp-tool-pill-icon${isError ? ' error' : ''}`}>
              {semantic.icon}
            </div>
            <span className="tp-tool-pill-label">{semantic.label}</span>
            {renderStatus()}
            <span className="tp-tool-pill-duration">{formatDurationMs(duration)}</span>
            <CaretRightOutlined
              style={{
                fontSize: 9,
                color: 'var(--text-tertiary)',
                transition: 'transform 0.15s ease',
                transform: detailExpanded ? 'rotate(90deg)' : 'none',
                flexShrink: 0,
              }}
            />
          </div>

          {/* Collapsed result summary */}
          {!detailExpanded && !isError && resultSummary && (
            <div style={{
              fontSize: 12,
              color: 'var(--text-tertiary)',
              paddingLeft: 26,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}>
              {resultSummary}
            </div>
          )}

          {/* Expanded details */}
          <AnimatePresence initial={false}>
            {detailExpanded && (
              <motion.div
                className="tp-step-detail-box"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
              >
                {actionDetails?.params && Object.keys(actionDetails.params).length > 0 && (
                  <div className="tp-step-detail-section">
                    <div className="tp-step-detail-label">Parameters</div>
                    <pre className="tp-step-detail-pre">
                      {typeof actionDetails.params === 'object'
                        ? JSON.stringify(actionDetails.params, null, 2)
                        : String(actionDetails.params)}
                    </pre>
                  </div>
                )}
                {step.action_result && (
                  <div className="tp-step-detail-section">
                    <div className="tp-step-detail-label">Result</div>
                    <pre className="tp-step-detail-pre">{step.action_result}</pre>
                  </div>
                )}
                {Array.isArray(step.evidence) && step.evidence.length > 0 && (
                  <div className="tp-step-detail-section">
                    <div className="tp-step-detail-label">Evidence</div>
                    <div className="tp-evidence-list">
                      {step.evidence.map((ev, evIdx) => (
                        <div className="tp-evidence-item" key={`${ev.ref || 'ev'}_${evIdx}`}>
                          <div className="tp-evidence-item-title">
                            {ev.title || ev.type || 'Evidence'}
                          </div>
                          {ev.ref && <div className="tp-evidence-item-ref">{ev.ref}</div>}
                          {ev.snippet && <div className="tp-evidence-item-snippet">{ev.snippet}</div>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </>
      )}

      {/* Error inline */}
      {isError && (
        <ErrorInline step={step} nextStep={nextStep} />
      )}
    </motion.div>
  );
};

/* ------------------------------------------------------------------ */
/*  Main steps filter                                                  */
/* ------------------------------------------------------------------ */

const getMainSteps = (steps: ThinkingStep[]): ThinkingStep[] =>
  steps.filter((step) => {
    if (step.action) return true;
    if (step.thought && step.thought.length > 50) return true;
    return false;
  });

/* ------------------------------------------------------------------ */
/*  ThinkingProcess (exported)                                         */
/* ------------------------------------------------------------------ */

export const ThinkingProcess: React.FC<ThinkingProcessProps> = ({
  process,
  isFinished,
  canControl = false,
  onPause,
  onResume,
  onSkipStep,
  paused = false,
  controlDisabled = false,
  controlBusy = false,
  controlBusyAction = null,
}) => {
  const [isExpanded, setIsExpanded] = useState(!isFinished && process.status === 'active');
  const mainSteps = getMainSteps(process.steps);
  const mainStepsCount = mainSteps.length;
  const isActive = process.status === 'active' && !isFinished;

  const totalDuration = useMemo(() => {
    let total = 0;
    for (const step of process.steps) {
      total += stepDurationMs(step) || 0;
    }
    return formatDurationMs(total || null);
  }, [process.steps]);

  useEffect(() => {
    if (!isFinished && process.status === 'active') {
      setIsExpanded(true);
    }
    if (isFinished && process.status !== 'active') {
      setIsExpanded(false);
    }
  }, [process.steps.length, isFinished, process.status]);

  const handleContentWheel = useCallback((e: React.WheelEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const atTop = el.scrollTop <= 0 && e.deltaY < 0;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 1 && e.deltaY > 0;
    if (atTop || atBottom) {
      el.style.overflowY = 'hidden';
      requestAnimationFrame(() => { el.style.overflowY = 'auto'; });
    }
  }, []);

  return (
    <div className="tp-inline-container">
      {/* Summary row */}
      <div
        className="tp-summary-row"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <span className="tp-summary-icon">
          {isActive ? (
            <LoadingOutlined spin style={{ color: 'var(--primary-color)' }} />
          ) : (
            <BulbOutlined style={{ color: 'var(--primary-color)' }} />
          )}
        </span>
        <span className="tp-summary-label">
          {isActive ? 'Thinking...' : 'Thought process'}
        </span>
        {mainStepsCount > 0 && (
          <span className="tp-summary-meta">
            {mainStepsCount} step{mainStepsCount > 1 ? 's' : ''}
          </span>
        )}
        <span className="tp-summary-meta">{totalDuration}</span>

        {/* Inline control buttons */}
        {canControl && isActive && (
          <span className="tp-control-bar" onClick={(e) => e.stopPropagation()}>
            <Button
              size="small"
              onClick={paused ? onResume : onPause}
              disabled={controlDisabled}
              loading={
                controlBusy &&
                ((paused && controlBusyAction === 'resume') ||
                  (!paused && controlBusyAction === 'pause'))
              }
            >
              {paused ? 'Resume' : 'Pause'}
            </Button>
            <Button
              size="small"
              onClick={onSkipStep}
              disabled={controlDisabled}
              loading={controlBusy && controlBusyAction === 'skip_step'}
            >
              Skip
            </Button>
          </span>
        )}

        <CaretRightOutlined
          className={`tp-summary-chevron${isExpanded ? ' expanded' : ''}`}
        />
      </div>

      {/* Expanded content */}
      <AnimatePresence initial={false}>
        {isExpanded && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <div
              className={`tp-expanded-content${isActive ? '' : ' tp-content-scroll'}`}
              onWheel={isActive ? undefined : handleContentWheel}
            >
              {process.steps.map((step, idx) => (
                <ThinkingStepItem
                  key={idx}
                  step={step}
                  index={idx}
                  isLast={idx === process.steps.length - 1}
                  isFinished={isFinished}
                  nextStep={idx < process.steps.length - 1 ? process.steps[idx + 1] : undefined}
                />
              ))}

              {isActive && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  style={{ padding: '4px 0' }}
                >
                  <span className="tp-thought-inline">
                    Thinking about next step...
                  </span>
                </motion.div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
