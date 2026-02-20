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
  WarningOutlined,
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

/* ------------------------------------------------------------------ */
/*  Error Recovery Flow                                                */
/* ------------------------------------------------------------------ */

const ErrorRecoveryFlow: React.FC<{
  step: ThinkingStep;
  nextStep?: ThinkingStep;
}> = ({ step, nextStep }) => {
  const errorDetail = step.action_result?.match(/^Error[: ]\s*(.+)/s)?.[1]
    ?? step.thought?.slice(0, 120)
    ?? 'Unknown error';

  interface Stage {
    label: string;
    detail?: string;
    color: string;
    icon: React.ReactNode;
  }

  const stages: Stage[] = [
    {
      label: 'Error detected',
      detail: errorDetail.length > 100 ? errorDetail.slice(0, 100) + '...' : errorDetail,
      color: '#ff4d4f',
      icon: <WarningOutlined />,
    },
    {
      label: 'Analyzing cause',
      color: '#faad14',
      icon: <SearchOutlined />,
    },
  ];

  if (step.self_correction) {
    stages.push({
      label: 'Correction strategy',
      detail: step.self_correction.slice(0, 120),
      color: 'var(--primary-color)',
      icon: <SyncOutlined />,
    });
  }

  if (nextStep) {
    stages.push({
      label: 'Retrying with corrections',
      color: '#52c41a',
      icon: <CheckCircleOutlined />,
    });
  }

  return (
    <div className="tp-recovery-flow">
      {stages.map((stage, i) => (
        <React.Fragment key={i}>
          <motion.div
            className="tp-recovery-stage"
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: i * 0.15, duration: 0.25 }}
          >
            <div className="tp-recovery-dot" style={{ background: stage.color }} />
            <div style={{ flex: 1 }}>
              <div className="tp-recovery-label" style={{ color: stage.color }}>
                {stage.icon}
                <span style={{ marginLeft: 6 }}>{stage.label}</span>
              </div>
              {stage.detail && (
                <div className="tp-recovery-detail">{stage.detail}</div>
              )}
            </div>
          </motion.div>
          {i < stages.length - 1 && (
            <div className="tp-recovery-connector">
              <div className="tp-recovery-connector-line" />
            </div>
          )}
        </React.Fragment>
      ))}
    </div>
  );
};

/* ------------------------------------------------------------------ */
/*  ThinkingStepItem – per-step progressive disclosure                 */
/* ------------------------------------------------------------------ */

const ThinkingStepItem: React.FC<{
  step: ThinkingStep;
  index: number;
  isLast: boolean;
  isFinished?: boolean;
  nextStep?: ThinkingStep;
}> = ({ step, index, isLast, isFinished, nextStep }) => {
  const [detailExpanded, setDetailExpanded] = useState(false);
  const [thoughtExpanded, setThoughtExpanded] = useState(false);

  const isTool = !!step.action;
  const isError = stepHasToolError(step);
  const hasResult = !!step.action_result;
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

  // Timeline dot
  const dotStyle: React.CSSProperties = {
    position: 'absolute',
    left: -13,
    top: 0,
    width: 24,
    height: 24,
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 12,
    fontWeight: 600,
  };

  let dotContent: React.ReactNode;
  if (isError) {
    Object.assign(dotStyle, {
      background: '#ff4d4f',
      border: 'none',
      color: '#fff',
    });
    dotContent = <CloseCircleOutlined style={{ color: '#fff', fontSize: 12 }} />;
  } else if (isStepComplete || isFinished) {
    Object.assign(dotStyle, {
      background: 'var(--success-color)',
      border: 'none',
      color: '#fff',
    });
    dotContent = <CheckCircleOutlined style={{ color: '#fff', fontSize: 14 }} />;
  } else if (step.status === 'calling_tool' || step.status === 'thinking' || step.status === 'analyzing') {
    Object.assign(dotStyle, {
      background: 'var(--bg-primary)',
      border: '2px solid var(--primary-color)',
      color: 'var(--primary-color)',
    });
    dotContent = <LoadingOutlined spin style={{ fontSize: 12 }} />;
  } else {
    Object.assign(dotStyle, {
      background: 'var(--bg-primary)',
      border: '2px solid var(--border-color)',
      color: 'var(--text-secondary)',
    });
    dotContent = index + 1;
  }

  const THOUGHT_TRUNCATE_LEN = 160;
  const thoughtIsTruncatable = step.thought && step.thought.length > THOUGHT_TRUNCATE_LEN;
  const visibleThought = thoughtIsTruncatable && !thoughtExpanded
    ? step.thought!.slice(0, THOUGHT_TRUNCATE_LEN) + '...'
    : step.thought;

  // Status badge for tool cards
  const renderToolStatus = () => {
    if (step.status === 'calling_tool') {
      return (
        <span className="tp-step-tool-status" style={{ color: 'var(--primary-color)' }}>
          <LoadingOutlined spin style={{ fontSize: 11 }} />
          <span>Running</span>
        </span>
      );
    }
    if (isError) {
      return (
        <span className="tp-step-tool-status" style={{ color: '#ff4d4f' }}>
          <CloseCircleOutlined style={{ fontSize: 11 }} />
          <span>Error</span>
        </span>
      );
    }
    if (isStepComplete || isFinished) {
      return (
        <span className="tp-step-tool-status" style={{ color: 'var(--success-color)' }}>
          <CheckCircleOutlined style={{ fontSize: 11 }} />
          <span>Done</span>
        </span>
      );
    }
    return null;
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10, height: 0 }}
      animate={{ opacity: 1, y: 0, height: 'auto' }}
      transition={{ duration: 0.3 }}
      style={{
        marginBottom: 16,
        paddingLeft: 16,
        borderLeft: '2px solid',
        borderColor: isLast && !isFinished && !isStepComplete
          ? 'var(--primary-color)'
          : 'var(--border-color)',
        position: 'relative',
      }}
    >
      {/* Timeline dot */}
      <div style={dotStyle}>{dotContent}</div>

      {/* Thought – truncated with toggle */}
      {step.thought && (
        <div className="tp-thought-text" style={{ marginBottom: isTool ? 0 : 8 }}>
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

      {/* Tool Action Card – Progressive Disclosure */}
      {isTool && semantic && (
        <div className="tp-step-tool-card">
          {/* Header: semantic label + status */}
          <div
            className="tp-step-tool-header"
            onClick={() => setDetailExpanded((v) => !v)}
          >
            <div
              className="tp-step-tool-icon"
              style={{
                background: isError
                  ? 'rgba(255, 77, 79, 0.1)'
                  : 'rgba(201, 100, 66, 0.1)',
                color: isError ? '#ff4d4f' : 'var(--primary-color)',
              }}
            >
              {semantic.icon}
            </div>
            <span className="tp-step-tool-label">{semantic.label}</span>
            {renderToolStatus()}
            <CaretRightOutlined
              className={`tp-step-tool-chevron${detailExpanded ? ' expanded' : ''}`}
            />
          </div>

          {/* Result summary line (collapsed) */}
          {!detailExpanded && !isError && resultSummary && (
            <div className="tp-step-result-summary">{resultSummary}</div>
          )}

          {/* Expanded details */}
          <AnimatePresence initial={false}>
            {detailExpanded && (
              <motion.div
                className="tp-step-detail-box"
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
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
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {/* Error Recovery Flow */}
      {isError && (
        <ErrorRecoveryFlow step={step} nextStep={nextStep} />
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
  const mainStepsCount = getMainSteps(process.steps).length;
  const isActive = process.status === 'active' && !isFinished;

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
    <div style={{ margin: '16px 0', maxWidth: '100%' }}>
      <motion.div
        style={{
          borderRadius: 12,
          overflow: 'hidden',
          border: '1px solid var(--border-color)',
          background: 'var(--bg-primary)',
          boxShadow: '0 2px 8px rgba(0, 0, 0, 0.04)',
        }}
        initial={false}
      >
        {/* Header Toggle */}
        <div
          onClick={() => setIsExpanded(!isExpanded)}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '12px 16px',
            cursor: 'pointer',
            background: 'var(--bg-primary)',
            transition: 'background 0.2s ease',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-tertiary)'; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'var(--bg-primary)'; }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {process.status === 'active' ? (
                <LoadingOutlined spin style={{ color: 'var(--primary-color)', fontSize: 14 }} />
              ) : (
                <div
                  style={{
                    width: 16,
                    height: 16,
                    borderRadius: '50%',
                    background: 'linear-gradient(135deg, var(--primary-color) 0%, #d4886e 100%)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <BulbOutlined style={{ color: '#fff', fontSize: 10 }} />
                </div>
              )}
              <span style={{ fontWeight: 500, fontSize: 14, color: 'var(--text-primary)' }}>
                {process.status === 'active' ? 'Thinking...' : 'Thought process'}
              </span>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {mainStepsCount > 0 && (
              <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                {mainStepsCount} step{mainStepsCount > 1 ? 's' : ''}
              </span>
            )}
            <div
              style={{
                transition: 'transform 0.2s ease',
                transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
              }}
            >
              <CaretRightOutlined style={{ fontSize: 12, color: 'var(--text-tertiary)' }} />
            </div>
          </div>
        </div>

        {/* Runtime control bar */}
        {canControl && (
          <div className="tp-control-bar">
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
              Skip Step
            </Button>
          </div>
        )}

        {/* Content Area */}
        <AnimatePresence initial={false}>
          {isExpanded && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.3, ease: 'easeInOut' }}
            >
              <div
                className={isActive ? undefined : 'tp-content-scroll'}
                onWheel={isActive ? undefined : handleContentWheel}
                style={{
                  padding: '12px 16px 16px',
                  background: 'var(--bg-tertiary)',
                  borderTop: '1px solid var(--border-color)',
                  ...(isActive
                    ? {}
                    : { maxHeight: '70vh', overflowY: 'auto' as const }),
                }}
              >
                <div style={{ paddingLeft: 8, paddingTop: 8 }}>
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

                  {process.status === 'active' && (
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      style={{
                        marginLeft: 16,
                        paddingLeft: 16,
                        borderLeft: '2px dashed var(--border-color)',
                        paddingTop: 4,
                        paddingBottom: 4,
                      }}
                    >
                      <span
                        style={{
                          fontSize: 12,
                          color: 'var(--text-tertiary)',
                          fontStyle: 'italic',
                        }}
                      >
                        Thinking about next step...
                      </span>
                    </motion.div>
                  )}
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
};
