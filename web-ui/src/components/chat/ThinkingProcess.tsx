import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { ThinkingProcess as ThinkingProcessType, ThinkingStep } from '@/types';
import {
  CaretRightOutlined,
  LoadingOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  BulbOutlined,
  ToolOutlined,
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
import { Button, Tooltip } from 'antd';
import './ThinkingProcess.css';

interface ThinkingProcessProps {
  process: ThinkingProcessType;
  isFinished?: boolean;
  canControl?: boolean;
  onPause?: () => void;
  onResume?: () => void;
  onSkipStep?: () => void;
  /** Stops the whole chat run (POST /chat/runs/:id/cancel), not just the current reasoning step. */
  onCancelRun?: () => void;
  paused?: boolean;
  controlDisabled?: boolean;
  controlBusy?: boolean;
  controlBusyAction?: 'pause' | 'resume' | 'skip_step' | null;
  cancelRunBusy?: boolean;
}

interface ToolSemantic {
  icon: React.ReactNode;
  label: string;
  toolName: string;
}

const CJK_CHAR_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

const TOOL_META: Record<string, { icon: React.ReactNode; zh: string; en: string }> = {
  web_search: { icon: <GlobalOutlined />, zh: '检索资料', en: 'Searching the web' },
  file_operations: { icon: <FileTextOutlined />, zh: '处理文件', en: 'Working with files' },
  code_executor: { icon: <CodeOutlined />, zh: '执行代码与分析', en: 'Executing code' },
  bio_tools: { icon: <ExperimentOutlined />, zh: '运行分析工具', en: 'Running analysis tools' },
  document_reader: { icon: <FileTextOutlined />, zh: '阅读文档', en: 'Reading documents' },
  vision_reader: { icon: <EyeOutlined />, zh: '分析图像内容', en: 'Analyzing visual content' },
  graph_rag: { icon: <DatabaseOutlined />, zh: '查询知识图谱', en: 'Querying knowledge graph' },
  phagescope: { icon: <ExperimentOutlined />, zh: '运行 PhageScope', en: 'Running PhageScope' },
  result_interpreter: { icon: <DatabaseOutlined />, zh: '汇总分析结果', en: 'Interpreting results' },
  plan_operation: { icon: <ProjectOutlined />, zh: '更新计划信息', en: 'Managing the plan' },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function localize(language: 'zh' | 'en', zh: string, en: string): string {
  return language === 'zh' ? zh : en;
}

function detectLanguage(process: ThinkingProcessType): 'zh' | 'en' {
  const samples = [
    process.summary,
    ...process.steps.flatMap((step) => [step.display_text, step.thought, step.action_result]),
  ];
  for (const sample of samples) {
    if (typeof sample !== 'string' || !sample.trim()) continue;
    return CJK_CHAR_RE.test(sample) ? 'zh' : 'en';
  }
  return 'en';
}

/** One-line display label for a step header row. */
function getDisplayLabel(step: ThinkingStep, language: 'zh' | 'en'): string {
  if (typeof step.display_text === 'string' && step.display_text.trim()) {
    return step.display_text.trim();
  }
  if (step.action) {
    return extractSemanticLabel(step.action, language)?.label || localize(language, '调用工具', 'Using a tool');
  }
  // Fallback: first sentence of thought, sanitized
  const raw = String(step.thought || '')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (!raw) return localize(language, '分析中', 'Analyzing');
  const first = raw.split(/(?<=[。！？!?;；.])\s+/)[0]?.trim() || raw;
  return first.length <= 80 ? first : `${first.slice(0, 77).trim()}...`;
}

function extractSemanticLabel(
  actionStr: string | null | undefined,
  language: 'zh' | 'en'
): ToolSemantic | null {
  if (!actionStr) return null;
  let parsed: any;
  try {
    parsed = JSON.parse(actionStr);
  } catch {
    return { icon: <ToolOutlined />, label: localize(language, '调用工具', 'Using a tool'), toolName: 'unknown' };
  }
  if (Array.isArray(parsed?.tools) && parsed.tools.length > 0) {
    const toolNames = parsed.tools
      .map((item: any) => (typeof item?.tool === 'string' ? item.tool : null))
      .filter((name: string | null): name is string => !!name);
    const preview = toolNames.slice(0, 2).join(', ');
    const suffix = toolNames.length > 2 ? ` +${toolNames.length - 2}` : '';
    return {
      icon: <ToolOutlined />,
      label: localize(
        language,
        `并行调用 ${toolNames.length} 个工具${preview ? `（${preview}${suffix}）` : ''}`,
        `Running ${toolNames.length} tools${preview ? ` (${preview}${suffix})` : ''}`,
      ),
      toolName: toolNames[0] || 'multi_tool',
    };
  }

  const toolName: string = parsed?.tool || 'unknown';
  const params: Record<string, any> = parsed?.params || {};
  const meta = TOOL_META[toolName] || { icon: <ToolOutlined />, zh: `调用工具：${toolName}`, en: `Using tool: ${toolName}` };
  let label = localize(language, meta.zh, meta.en);

  switch (toolName) {
    case 'web_search':
      if (params.query) {
        const clipped = String(params.query).slice(0, 60);
        label = localize(language, `检索资料：${clipped}`, `Searching for: ${clipped}`);
      }
      break;
    case 'file_operations': {
      const fileName = (params.path || '').split('/').pop() || params.path || '';
      if (params.operation === 'list') label = localize(language, `查看目录：${params.path || '/'}`, `Listing directory: ${params.path || '/'}`);
      else if (params.operation === 'read') label = localize(language, `读取文件：${fileName}`, `Reading: ${fileName}`);
      else if (params.operation) label = localize(language, `处理文件：${fileName}`, `${params.operation}: ${fileName}`);
      break;
    }
    case 'code_executor':
      if (params.task) label = localize(language, `执行代码任务：${String(params.task).slice(0, 48)}`, `Code task: ${String(params.task).slice(0, 60)}`);
      break;
    case 'document_reader':
      if (params.file_path) label = localize(language, `阅读文档：${String(params.file_path).split('/').pop()}`, `Reading: ${String(params.file_path).split('/').pop()}`);
      break;
    case 'vision_reader':
      if (params.file_path) label = localize(language, `分析图像：${String(params.file_path).split('/').pop()}`, `Analyzing: ${String(params.file_path).split('/').pop()}`);
      break;
  }
  return { icon: meta.icon, label, toolName };
}

function extractResultSummary(result: string | null | undefined, maxLen = 120): string | null {
  if (!result) return null;
  const trimmed = result.trim();
  if (trimmed.length <= maxLen) return trimmed;
  const firstLine = trimmed.split('\n')[0].trim();
  if (firstLine.length >= 12 && firstLine.length <= 200) return firstLine;
  return `${trimmed.slice(0, maxLen)}...`;
}

function stepHasToolError(step: ThinkingStep): boolean {
  if (step.status === 'error') return true;
  return typeof step.action_result === 'string' && /^Error[ :]/.test(step.action_result);
}

function _toMs(value?: string): number | null {
  if (!value) return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

/** Sub-second: ms; otherwise seconds (one decimal) — avoids ambiguous `m` (minutes vs meters). */
function formatDurationMs(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return '--';
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function stepDurationMs(step: ThinkingStep): number | null {
  const start = _toMs(step.started_at) ?? _toMs(step.timestamp);
  const end = _toMs(step.finished_at) ?? _toMs(step.timestamp);
  if (start === null || end === null) return null;
  return Math.max(0, end - start);
}

function isGenericText(text: string | null | undefined, language: 'zh' | 'en'): boolean {
  const n = String(text || '').replace(/\s+/g, ' ').trim();
  if (!n) return false;
  const generics = language === 'zh'
    ? ['分析当前问题，准备下一步', '准备下一步', '准备整理回复', '分析中', '分析当前步骤', '处理当前步骤']
    : ['Analyzing the request and preparing the next step', 'Preparing the next step', 'Preparing the response', 'Analyzing', 'Working through the current step'];
  return generics.includes(n);
}

function getMainSteps(steps: ThinkingStep[], language: 'zh' | 'en'): ThinkingStep[] {
  const labels = steps.map((s) => getDisplayLabel(s, language));
  const hasSpecific = labels.some((t) => t && !isGenericText(t, language));
  return steps.filter((step, idx) => {
    const text = labels[idx];
    // During active state, keep generic steps too (so user sees something is happening)
    if (step.status === 'thinking' || step.status === 'calling_tool') return true;
    if (!step.action && hasSpecific && isGenericText(text, language)) return false;
    return !!step.action || !!text;
  });
}

function getProcessSummary(process: ThinkingProcessType, language: 'zh' | 'en'): string {
  if (typeof process.summary === 'string' && process.summary.trim()) return process.summary.trim();
  const texts = getMainSteps(process.steps, language)
    .map((s) => getDisplayLabel(s, language))
    .filter(Boolean)
    .slice(0, 3);
  if (texts.length > 1) return texts.join(' → ');
  if (texts.length === 1) return texts[0];
  return localize(language, '整理思考过程', 'Organizing the reasoning process');
}

/** Truncate thought for live display — show last N lines for long content */
function truncateForLiveDisplay(text: string, maxLines = 12): { text: string; truncated: boolean } {
  const lines = text.split('\n');
  if (lines.length <= maxLines) return { text, truncated: false };
  return {
    text: lines.slice(-maxLines).join('\n'),
    truncated: true,
  };
}

// ---------------------------------------------------------------------------
// Step item — shows live thinking content
// ---------------------------------------------------------------------------

const ThinkingStepItem: React.FC<{
  step: ThinkingStep;
  isLast: boolean;
  isFinished?: boolean;
  isProcessActive?: boolean;
  nextStep?: ThinkingStep;
  language: 'zh' | 'en';
}> = ({ step, isLast, isFinished, isProcessActive, nextStep, language }) => {
  const [detailExpanded, setDetailExpanded] = useState(false);
  const streamRef = useRef<HTMLDivElement>(null);

  const isTool = !!step.action;
  const isError = stepHasToolError(step);
  const hasResult = !!step.action_result;
  const duration = stepDurationMs(step);
  const isStepActive = step.status === 'thinking' || step.status === 'calling_tool';
  const isStepComplete =
    step.status === 'done' ||
    step.status === 'completed' ||
    hasResult ||
    (!isLast && !isStepActive);

  const semantic = useMemo(() => extractSemanticLabel(step.action, language), [step.action, language]);
  const label = useMemo(() => getDisplayLabel(step, language), [step, language]);
  const resultSummary = useMemo(() => extractResultSummary(step.action_result), [step.action_result]);

  const actionDetails = useMemo(() => {
    if (!step.action) return null;
    try { return JSON.parse(step.action); }
    catch { return { tool: 'unknown', params: step.action }; }
  }, [step.action]);

  // Raw thought content
  const rawThought = useMemo(() => {
    const t = String(step.thought || '').trim();
    if (!t) return null;
    return t;
  }, [step.thought]);

  // For live display: show streaming thought with truncation
  const liveThought = useMemo(() => {
    if (!rawThought) return null;
    return truncateForLiveDisplay(rawThought);
  }, [rawThought]);

  // Icon for the row
  const icon = useMemo(() => {
    if (isTool) return semantic?.icon || <ToolOutlined />;
    return <BulbOutlined />;
  }, [isTool, semantic]);

  // Auto-scroll the streaming area to bottom
  useEffect(() => {
    if (isStepActive && streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [rawThought, isStepActive]);

  // Whether the step is a reasoning step with thought content worth showing in detail
  const hasFullThought = !!rawThought && rawThought.length > 100;
  // Whether to show live stream: active reasoning steps with thought content
  const showLiveStream = !isTool && rawThought && (isStepActive || (isLast && isProcessActive));
  // Whether to show the thought content inline (completed reasoning steps)
  const showCompletedThought = !isTool && rawThought && !showLiveStream && isStepComplete;

  const renderStatus = () => {
    if (isStepActive) {
      return (
        <span className="tp-step-status running">
          <LoadingOutlined spin style={{ fontSize: 11 }} />
        </span>
      );
    }
    if (isError) {
      return (
        <span className="tp-step-status error">
          <CloseCircleOutlined style={{ fontSize: 11 }} />
        </span>
      );
    }
    if (isStepComplete || isFinished) {
      return (
        <span className="tp-step-status success">
          <CheckCircleOutlined style={{ fontSize: 11 }} />
        </span>
      );
    }
    return null;
  };

  return (
    <motion.div
      className="tp-step-item"
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: 'easeOut' }}
    >
      {/* Header row */}
      <div
        className={`tp-step-row${isStepActive ? ' active' : ''}${isError ? ' has-error' : ''}`}
      >
        <div className={`tp-step-icon${isTool ? '' : ' reasoning'}${isError ? ' error' : ''}`}>
          {icon}
        </div>
        <span className="tp-step-label">{label}</span>
        {renderStatus()}
        {(isStepComplete || isFinished) && (
          <span className="tp-step-duration">{formatDurationMs(duration)}</span>
        )}
      </div>

      {/* ===== LIVE STREAMING: Active reasoning step — show thought as it streams ===== */}
      {showLiveStream && liveThought && (
        <div className="tp-step-stream" ref={streamRef}>
          {liveThought.truncated && (
            <div className="tp-stream-truncated">···</div>
          )}
          <span className="tp-stream-text">{liveThought.text}</span>
          <span className="tp-stream-cursor" />
        </div>
      )}

      {/* ===== COMPLETED REASONING: Show thought preview, expandable for full ===== */}
      {showCompletedThought && (
        <div className="tp-step-thought-block">
          <div
            className={`tp-step-thought-preview${hasFullThought ? ' clickable' : ''}`}
            onClick={hasFullThought ? () => setDetailExpanded((v) => !v) : undefined}
          >
            <span className="tp-thought-text">
              {detailExpanded
                ? rawThought
                : (rawThought!.length > 200 ? `${rawThought!.slice(0, 200).trim()}...` : rawThought)}
            </span>
            {hasFullThought && (
              <CaretRightOutlined
                style={{
                  fontSize: 9,
                  color: 'var(--text-quaternary)',
                  transition: 'transform 0.15s ease',
                  transform: detailExpanded ? 'rotate(90deg)' : 'none',
                  flexShrink: 0,
                  marginLeft: 4,
                }}
              />
            )}
          </div>
        </div>
      )}

      {/* ===== TOOL: Show params inline when calling ===== */}
      {isTool && isStepActive && actionDetails?.params && Object.keys(actionDetails.params).length > 0 && (
        <div className="tp-tool-params">
          <pre className="tp-tool-params-pre">
            {typeof actionDetails.params === 'object'
              ? JSON.stringify(actionDetails.params, null, 2)
              : String(actionDetails.params)}
          </pre>
        </div>
      )}

      {/* ===== TOOL RESULT: Show result when complete ===== */}
      {isTool && hasResult && !isError && (
        <div
          className={`tp-tool-result${step.action_result && step.action_result.length > 150 ? ' clickable' : ''}`}
          onClick={step.action_result && step.action_result.length > 150 ? () => setDetailExpanded((v) => !v) : undefined}
        >
          <span className="tp-tool-result-text">
            {detailExpanded ? step.action_result : resultSummary}
          </span>
          {step.action_result && step.action_result.length > 150 && (
            <CaretRightOutlined
              style={{
                fontSize: 9,
                color: 'var(--text-quaternary)',
                transition: 'transform 0.15s ease',
                transform: detailExpanded ? 'rotate(90deg)' : 'none',
                flexShrink: 0,
                marginLeft: 4,
              }}
            />
          )}
        </div>
      )}

      {/* ===== ERROR INLINE ===== */}
      {isError && (
        <div className="tp-error-inline">
          <span>
            {(step.action_result?.match(/^Error[: ]\s*(.+)/s)?.[1] || label || localize(language, '未知错误', 'Unknown error')).slice(0, 200)}
          </span>
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
              <span className="tp-error-correction">
                {localize(language, '正在尝试下一步', 'Trying the next step')}
              </span>
            </>
          )}
        </div>
      )}

      {/* ===== EVIDENCE ===== */}
      {Array.isArray(step.evidence) && step.evidence.length > 0 && (
        <div className="tp-evidence-block">
          {step.evidence.map((ev, evIdx) => (
            <div className="tp-evidence-item" key={`${ev.ref || 'ev'}_${evIdx}`}>
              <span className="tp-evidence-item-title">
                {ev.title || ev.type || localize(language, '证据', 'Evidence')}
              </span>
              {ev.ref && <span className="tp-evidence-item-ref"> {ev.ref}</span>}
              {ev.snippet && <div className="tp-evidence-item-snippet">{ev.snippet}</div>}
            </div>
          ))}
        </div>
      )}
    </motion.div>
  );
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export const ThinkingProcess: React.FC<ThinkingProcessProps> = ({
  process,
  isFinished,
  canControl = false,
  onPause,
  onResume,
  onSkipStep,
  onCancelRun,
  paused = false,
  controlDisabled = false,
  controlBusy = false,
  controlBusyAction = null,
  cancelRunBusy = false,
}) => {
  const [isExpanded, setIsExpanded] = useState(!isFinished && process.status === 'active');
  const language = useMemo(() => detectLanguage(process), [process]);
  const mainSteps = useMemo(() => getMainSteps(process.steps, language), [process.steps, language]);
  const mainStepsCount = mainSteps.length;
  const isActive = process.status === 'active' && !isFinished;
  const summaryText = useMemo(() => getProcessSummary(process, language), [process, language]);
  const stepsEndRef = useRef<HTMLDivElement>(null);

  const totalDuration = useMemo(() => {
    let total = 0;
    for (const step of process.steps) total += stepDurationMs(step) || 0;
    return formatDurationMs(total || null);
  }, [process.steps]);

  // Auto-expand when active, auto-collapse when done
  useEffect(() => {
    if (!isFinished && process.status === 'active') setIsExpanded(true);
    if (isFinished && process.status !== 'active') {
      // Small delay before collapsing so user can see the final state
      const timer = setTimeout(() => setIsExpanded(false), 800);
      return () => clearTimeout(timer);
    }
  }, [process.steps.length, isFinished, process.status]);

  // Auto-scroll to latest step during active thinking
  useEffect(() => {
    if (isActive && stepsEndRef.current) {
      stepsEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [mainSteps.length, isActive]);

  // Scroll containment is handled by CSS `overscroll-behavior: contain`.

  return (
    <div className={`tp-container${isActive ? ' tp-active' : ''}`}>
      {/* Header row */}
      <div className="tp-header" onClick={() => setIsExpanded(!isExpanded)}>
        <span className="tp-header-icon">
          {isActive ? (
            <LoadingOutlined spin style={{ color: 'var(--primary-color)' }} />
          ) : (
            <BulbOutlined style={{ color: 'var(--primary-color)' }} />
          )}
        </span>
        <span className="tp-header-label">
          {isActive
            ? localize(language, '思考中...', 'Thinking...')
            : localize(language, '思考过程', 'Thought process')}
        </span>
        {!isExpanded && (
          <span className="tp-header-preview">{summaryText}</span>
        )}
        {mainStepsCount > 0 && (
          <span className="tp-header-meta">
            {localize(language, `${mainStepsCount} 步`, `${mainStepsCount} step${mainStepsCount > 1 ? 's' : ''}`)}
          </span>
        )}
        <span className="tp-header-meta">{totalDuration}</span>

        {canControl && isActive && (
          <span className="tp-control-bar" onClick={(e) => e.stopPropagation()}>
            <Button
              size="small"
              onClick={paused ? onResume : onPause}
              disabled={controlDisabled}
              loading={controlBusy && ((paused && controlBusyAction === 'resume') || (!paused && controlBusyAction === 'pause'))}
            >
              {paused ? 'Resume' : 'Pause'}
            </Button>
            <Tooltip
              title={localize(
                language,
                '仅在本步结束后生效；卡在模型或工具内部时可能无效。',
                'Takes effect after the current step finishes; may not interrupt an in-flight LLM or tool call.',
              )}
            >
              <Button
                size="small"
                onClick={onSkipStep}
                disabled={controlDisabled || cancelRunBusy}
                loading={controlBusy && controlBusyAction === 'skip_step'}
              >
                Skip
              </Button>
            </Tooltip>
            {onCancelRun && (
              <Tooltip
                title={localize(
                  language,
                  '请求终止本次对话运行（与 Skip 不同）。',
                  'Request cancel for this chat run (unlike Skip).',
                )}
              >
                <Button
                  size="small"
                  danger
                  onClick={onCancelRun}
                  disabled={controlDisabled || cancelRunBusy}
                  loading={cancelRunBusy}
                >
                  {localize(language, '停止', 'Stop')}
                </Button>
              </Tooltip>
            )}
          </span>
        )}

        <CaretRightOutlined className={`tp-header-chevron${isExpanded ? ' expanded' : ''}`} />
      </div>

      {/* Expanded step list */}
      <AnimatePresence initial={false}>
        {isExpanded && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <div
              className={`tp-steps${isActive ? '' : ' tp-steps-scroll'}`}
            >
              {mainSteps.map((step, idx) => (
                <ThinkingStepItem
                  key={`${step.iteration}_${idx}`}
                  step={step}
                  isLast={idx === mainSteps.length - 1}
                  isFinished={isFinished}
                  isProcessActive={isActive}
                  nextStep={idx < mainSteps.length - 1 ? mainSteps[idx + 1] : undefined}
                  language={language}
                />
              ))}

              {/* "Preparing next step" indicator — only when active and last step is complete */}
              {isActive && mainSteps.length > 0 && !['thinking', 'calling_tool'].includes(mainSteps[mainSteps.length - 1]?.status || '') && (
                <motion.div
                  className="tp-step-item"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: [0.4, 0.8, 0.4] }}
                  transition={{ duration: 1.5, repeat: Infinity, ease: 'easeInOut' }}
                >
                  <div className="tp-step-row">
                    <div className="tp-step-icon reasoning">
                      <LoadingOutlined spin style={{ fontSize: 10 }} />
                    </div>
                    <span className="tp-step-label" style={{ color: 'var(--text-quaternary)' }}>
                      {localize(language, '准备下一步...', 'Preparing next step...')}
                    </span>
                  </div>
                </motion.div>
              )}

              {/* Empty state when no steps yet */}
              {isActive && mainSteps.length === 0 && (
                <motion.div
                  className="tp-step-item"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: [0.4, 0.8, 0.4] }}
                  transition={{ duration: 1.5, repeat: Infinity, ease: 'easeInOut' }}
                >
                  <div className="tp-step-row">
                    <div className="tp-step-icon reasoning">
                      <LoadingOutlined spin style={{ fontSize: 10 }} />
                    </div>
                    <span className="tp-step-label" style={{ color: 'var(--text-quaternary)' }}>
                      {localize(language, '正在分析问题...', 'Analyzing the question...')}
                    </span>
                  </div>
                </motion.div>
              )}

              <div ref={stepsEndRef} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
