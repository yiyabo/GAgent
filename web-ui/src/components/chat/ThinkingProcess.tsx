import React, { useState, useEffect, useMemo, useRef } from 'react';
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
import { parseServerTimestampMs } from '@utils/serverTime';
import {
  buildStreamRows,
  summarizeToolGroup,
  type ToolStepGroup,
} from '@utils/toolGrouping';
import { MarkdownRenderer } from './MarkdownRenderer';
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
  /** Optional backend-reported progress (deep_think_progress). Surfaces the
   *  current tool/phase when the active step label is generic (e.g. "处理当前步骤"). */
  progressHint?: {
    label?: string | null;
    tool?: string | null;
    phase?: string | null;
    details?: string | null;
    updated_at?: string | null;
  } | null;
  /** Forwarded to MarkdownRenderer so relative artifact images in thoughts resolve. */
  sessionId?: string | null;
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
  lightrag_query: { icon: <DatabaseOutlined />, zh: '查询 LightRAG 知识库', en: 'Querying LightRAG knowledge base' },
  graph_rag: { icon: <DatabaseOutlined />, zh: '查询本地小图谱', en: 'Querying local triples graph' },
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
  if (Array.isArray(parsed?.tools) && parsed.tools.length > 1) {
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
  // Single-entry `tools` array: unwrap and render as a normal single-tool call.
  if (Array.isArray(parsed?.tools) && parsed.tools.length === 1 && parsed.tools[0] && typeof parsed.tools[0] === 'object') {
    parsed = parsed.tools[0];
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

function stepHasToolError(step: ThinkingStep): boolean {
  if (step.status === 'error') return true;
  return typeof step.action_result === 'string' && /^Error[ :]/.test(step.action_result);
}

function _toMs(value?: string): number | null {
  // Backend timestamps are naive UTC; Date.parse alone would read them as
  // local time and skew elapsed timers by the local offset (UTC+8 → 480m).
  return parseServerTimestampMs(value);
}

/** Sub-second: ms; otherwise seconds (one decimal) — avoids ambiguous `m` (minutes vs meters). */
function formatDurationMs(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return '--';
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m${seconds.toString().padStart(2, '0')}s`;
}

function stepDurationMs(step: ThinkingStep): number | null {
  const start = _toMs(step.started_at) ?? _toMs(step.timestamp);
  const end = _toMs(step.finished_at) ?? _toMs(step.timestamp);
  if (start === null || end === null) return null;
  return Math.max(0, end - start);
}

/** Returns the best-effort start timestamp (ms) for an in-progress step. */
function stepStartMs(step: ThinkingStep): number | null {
  return _toMs(step.started_at) ?? _toMs(step.timestamp);
}

/** Earliest known start across a step list (ms). */
function earliestStepStartMs(steps: ThinkingStep[]): number | null {
  let earliest: number | null = null;
  for (const step of steps) {
    const start = stepStartMs(step);
    if (start === null) continue;
    if (earliest === null || start < earliest) earliest = start;
  }
  return earliest;
}

/** Ticks `Date.now()` every few seconds while `active`, so elapsed counters stay live. */
function useLiveNow(active: boolean): number {
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const handle = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(handle);
  }, [active]);
  return now;
}

function isGenericText(text: string | null | undefined, language: 'zh' | 'en'): boolean {
  const n = String(text || '').replace(/\s+/g, ' ').trim();
  if (!n) return false;
  const generics = language === 'zh'
    ? ['分析当前问题，准备下一步', '准备下一步', '准备整理回复', '分析中', '分析当前步骤', '处理当前步骤', '思考过程']
    : ['Analyzing the request and preparing the next step', 'Preparing the next step', 'Preparing the response', 'Analyzing', 'Working through the current step', 'Processing the current step', 'Thought process'];
  return generics.includes(n);
}

/** Base row-worthiness: active steps, tool steps, and reasoning steps that
 *  already carry thought text. */
function _passesStepFilter(step: ThinkingStep): boolean {
  if (step.status === 'thinking' || step.status === 'calling_tool' || step.status === 'analyzing') return true;
  if (step.action) return true;
  return typeof step.thought === 'string' && step.thought.trim().length > 0;
}

/**
 * Steps worth a row in the activity stream, in chronological (iteration)
 * order — the store appends steps in arrival order, which interleaves
 * parallel tool calls ahead of the reasoning step that prompted them.
 *
 * While the run is active, visibility is sticky: a step that once qualified
 * stays visible even if a transient update (e.g. a done-event arriving
 * before its streamed thought) would filter it out. Once finished, only the
 * base filter applies, so cleared-thought steps drop out cleanly at the end.
 * If filtering would empty the list (legacy runs), everything is shown.
 */
function getVisibleSteps(
  steps: ThinkingStep[],
  isFinished: boolean,
  stickyIterations: ReadonlySet<number>,
): ThinkingStep[] {
  const sorted = [...steps].sort(
    (a, b) => (Number(a.iteration) || 0) - (Number(b.iteration) || 0),
  );
  const visible = sorted.filter((step) => {
    if (_passesStepFilter(step)) return true;
    if (!isFinished && stickyIterations.has(step.iteration)) return true;
    return false;
  });
  return visible.length > 0 ? visible : sorted;
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
// Activity item — one collapsible row per step
// ---------------------------------------------------------------------------

const ThinkingActivityItem: React.FC<{
  step: ThinkingStep;
  isFinished?: boolean;
  isProcessActive?: boolean;
  nextStep?: ThinkingStep;
  language: 'zh' | 'en';
  liveNow?: number;
  hintText?: string | null;
  sessionId?: string | null;
}> = ({ step, isFinished, isProcessActive, nextStep, language, liveNow, hintText, sessionId }) => {
  const [expanded, setExpanded] = useState(false);
  const streamRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const [showScrollBtn, setShowScrollBtn] = useState(false);

  const isTool = !!step.action;
  const isError = stepHasToolError(step);
  const hasResult = !!step.action_result;
  const duration = stepDurationMs(step);
  // A finished run has no live steps: backend leaves tool steps at
  // status 'analyzing' forever, so gate the spinner/elapsed on isFinished.
  const isStepActive =
    !isFinished &&
    (step.status === 'thinking' || step.status === 'calling_tool' || step.status === 'analyzing');
  const isStepComplete =
    step.status === 'done' ||
    step.status === 'completed' ||
    hasResult ||
    (!isStepActive && isFinished);

  const semantic = useMemo(() => extractSemanticLabel(step.action, language), [step.action, language]);
  // Tool rows always use the action-derived semantic label (specific); reasoning
  // rows use a uniform label — the backend's generic display_text
  // ("处理当前步骤") is never surfaced as a row title.
  const label = isTool
    ? semantic?.label || localize(language, '调用工具', 'Using a tool')
    : localize(language, '思考过程', 'Thought process');

  const actionDetails = useMemo(() => {
    if (!step.action) return null;
    try { return JSON.parse(step.action); }
    catch { return { tool: 'unknown', params: step.action }; }
  }, [step.action]);

  const paramsDetail = useMemo(() => {
    if (!actionDetails) return null;
    const effective =
      Array.isArray(actionDetails.tools) && actionDetails.tools.length === 1 && actionDetails.tools[0] && typeof actionDetails.tools[0] === 'object'
        ? actionDetails.tools[0]
        : actionDetails;
    if (Array.isArray(effective.tools) && effective.tools.length > 0) {
      return JSON.stringify(effective.tools, null, 2);
    }
    if (effective.params && Object.keys(effective.params).length > 0) {
      return typeof effective.params === 'object'
        ? JSON.stringify(effective.params, null, 2)
        : String(effective.params);
    }
    return null;
  }, [actionDetails]);

  const thoughtText = useMemo(() => String(step.thought || '').trim(), [step.thought]);

  const expandable = isTool
    ? Boolean(isStepActive || paramsDetail || hasResult || isError)
    : thoughtText.length > 0;

  const liveElapsedMs = useMemo(() => {
    if (!isStepActive) return null;
    const start = stepStartMs(step);
    if (start === null || typeof liveNow !== 'number') return null;
    return Math.max(0, liveNow - start);
  }, [isStepActive, step, liveNow]);

  const showHint = isStepActive && !isTool && typeof hintText === 'string' && hintText.trim().length > 0;

  const liveThought = useMemo(() => {
    if (!thoughtText) return null;
    return truncateForLiveDisplay(thoughtText);
  }, [thoughtText]);

  const icon = useMemo(() => {
    if (isTool) return semantic?.icon || <ToolOutlined />;
    return <BulbOutlined />;
  }, [isTool, semantic]);

  // Auto-scroll the streaming area to bottom
  useEffect(() => {
    if (expanded && isStepActive && streamRef.current && autoScrollRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [thoughtText, expanded, isStepActive]);

  const renderStatus = () => {
    if (isStepActive) {
      return (
        <span className="tp-item-status running">
          <LoadingOutlined spin style={{ fontSize: 11 }} />
        </span>
      );
    }
    if (isError) {
      return (
        <span className="tp-item-status error">
          <CloseCircleOutlined style={{ fontSize: 11 }} />
        </span>
      );
    }
    if (isStepComplete) {
      return (
        <span className="tp-item-status success">
          <CheckCircleOutlined style={{ fontSize: 11 }} />
        </span>
      );
    }
    return null;
  };

  const renderDetail = () => {
    if (!expanded || !expandable) return null;

    if (!isTool) {
      // Reasoning detail: live tail while streaming, full markdown once done.
      if (isStepActive && liveThought) {
        return (
          <div className="tp-item-stream" ref={streamRef} onScroll={() => {
            const el = streamRef.current;
            if (!el) return;
            const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
            autoScrollRef.current = atBottom;
            setShowScrollBtn(!atBottom);
          }} style={{ position: 'relative' }}>
            {liveThought.truncated && (
              <div className="tp-stream-truncated">···</div>
            )}
            <span className="tp-stream-text">{liveThought.text}</span>
            {showScrollBtn && (
              <div
                onClick={(e) => {
                  e.stopPropagation();
                  if (streamRef.current) {
                    streamRef.current.scrollTop = streamRef.current.scrollHeight;
                    autoScrollRef.current = true;
                    setShowScrollBtn(false);
                  }
                }}
                className="tp-stream-scrollbtn"
              >↓</div>
            )}
            <span className="tp-stream-cursor" />
          </div>
        );
      }
      if (!thoughtText) return null;
      return (
        <div className="tp-item-thought">
          <MarkdownRenderer content={thoughtText} className="tp-md" sessionId={sessionId} />
        </div>
      );
    }

    // Tool detail: params + full result / error.
    return (
      <div className="tp-item-tooldetail">
        {paramsDetail && (
          <div className="tp-tool-params">
            <div className="tp-tool-detail-caption">{localize(language, '参数', 'Parameters')}</div>
            <pre className="tp-tool-params-pre">{paramsDetail}</pre>
          </div>
        )}
        {isStepActive && !hasResult && (
          <div className="tp-tool-waiting">{localize(language, '执行中…', 'Running…')}</div>
        )}
        {hasResult && !isError && (
          <div className="tp-tool-result-full">
            <div className="tp-tool-detail-caption">{localize(language, '结果', 'Result')}</div>
            <div className="tp-tool-result-fulltext">{step.action_result}</div>
          </div>
        )}
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
      </div>
    );
  };

  return (
    <motion.div
      className="tp-item"
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: 'easeOut' }}
    >
      <div
        className={`tp-item-row${isStepActive ? ' active' : ''}${isError ? ' has-error' : ''}${expandable ? ' expandable' : ''}`}
        onClick={expandable ? () => setExpanded((v) => !v) : undefined}
      >
        <div className={`tp-item-icon${isTool ? '' : ' reasoning'}${isError ? ' error' : ''}`}>
          {icon}
        </div>
        <span className="tp-item-label">{label}</span>
        {renderStatus()}
        {isStepActive && liveElapsedMs !== null && (
          <span className="tp-item-duration" aria-label="elapsed">
            {formatDurationMs(liveElapsedMs)}
          </span>
        )}
        {!isStepActive && isStepComplete && (
          <span className="tp-item-duration">{formatDurationMs(duration)}</span>
        )}
        {expandable && (
          <CaretRightOutlined className={`tp-item-chevron${expanded ? ' expanded' : ''}`} />
        )}
      </div>

      {showHint && (
        <div className="tp-item-hint">{hintText}</div>
      )}

      {renderDetail()}
    </motion.div>
  );
};

// ---------------------------------------------------------------------------
// Tool-call group — one collapsible summary row for consecutive tool steps
// ---------------------------------------------------------------------------

const ToolCallGroupRow: React.FC<{
  group: ToolStepGroup<ThinkingStep>;
  isFinished?: boolean;
  isProcessActive?: boolean;
  language: 'zh' | 'en';
  liveNow?: number;
  hintText?: string | null;
  sessionId?: string | null;
}> = ({ group, isFinished, isProcessActive, language, liveNow, hintText, sessionId }) => {
  const [expanded, setExpanded] = useState(false);
  const summary = useMemo(
    () => summarizeToolGroup(group.steps, language),
    [group.steps, language],
  );
  const hasError = group.steps.some((s) => stepHasToolError(s));
  const duration = useMemo(() => {
    let total = 0;
    let known = false;
    for (const s of group.steps) {
      const d = stepDurationMs(s);
      if (d !== null) {
        total += d;
        known = true;
      }
    }
    return formatDurationMs(known ? total : null);
  }, [group.steps]);

  return (
    <motion.div
      className="tp-item tp-group"
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: 'easeOut' }}
    >
      <div
        className={`tp-item-row expandable${hasError ? ' has-error' : ''}`}
        onClick={() => setExpanded((v) => !v)}
      >
        <div className={`tp-item-icon${hasError ? ' error' : ''}`}>
          <ToolOutlined />
        </div>
        <span className="tp-item-label">{summary}</span>
        {hasError && (
          <span className="tp-item-status error">
            <CloseCircleOutlined style={{ fontSize: 11 }} />
          </span>
        )}
        <span className="tp-item-duration">{duration}</span>
        <CaretRightOutlined className={`tp-item-chevron${expanded ? ' expanded' : ''}`} />
      </div>
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
          >
            <div className="tp-group-children">
              {group.steps.map((step, idx) => (
                <ThinkingActivityItem
                  key={`git-${step.iteration}`}
                  step={step}
                  isFinished={isFinished}
                  isProcessActive={isProcessActive}
                  nextStep={idx < group.steps.length - 1 ? group.steps[idx + 1] : undefined}
                  language={language}
                  liveNow={liveNow}
                  hintText={hintText}
                  sessionId={sessionId}
                />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
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
  progressHint = null,
  sessionId = null,
}) => {
  const [isExpanded, setIsExpanded] = useState(!isFinished && process.status === 'active');
  const language = useMemo(() => detectLanguage(process), [process]);
  const isActive = process.status === 'active' && !isFinished;

  // Sticky visibility: once a step qualifies for a row during a live run,
  // keep its row even through transient updates that would filter it (e.g. a
  // done-event arriving before its streamed thought). Prevents rows from
  // flickering out and back in.
  const [stickyIterations, setStickyIterations] = useState<ReadonlySet<number>>(new Set());
  useEffect(() => {
    if (isFinished) return;
    setStickyIterations((prev) => {
      let next: Set<number> | null = null;
      for (const step of process.steps) {
        if (_passesStepFilter(step) && !prev.has(step.iteration)) {
          if (!next) next = new Set(prev);
          next.add(step.iteration);
        }
      }
      return next ?? prev;
    });
  }, [process.steps, isFinished]);

  const visibleSteps = useMemo(
    () => getVisibleSteps(process.steps, Boolean(isFinished), stickyIterations),
    [process.steps, isFinished, stickyIterations],
  );
  const nextStepByIteration = useMemo(() => {
    const map = new Map<number, ThinkingStep>();
    for (let i = 0; i < visibleSteps.length - 1; i += 1) {
      map.set(visibleSteps[i].iteration, visibleSteps[i + 1]);
    }
    return map;
  }, [visibleSteps]);
  // Fold runs of consecutive tool calls into one collapsible summary row;
  // during live runs the newest step stays solo so the in-flight call shows.
  const streamRows = useMemo(
    () => buildStreamRows(visibleSteps, { keepLastSolo: isActive }),
    [visibleSteps, isActive],
  );
  const stepCount = visibleSteps.length;
  const toolCallCount = useMemo(() => visibleSteps.filter((s) => !!s.action).length, [visibleSteps]);
  const stepsEndRef = useRef<HTMLDivElement>(null);
  const liveNow = useLiveNow(isActive);

  const totalDuration = useMemo(() => {
    let total = 0;
    let hasKnown = false;
    for (const step of process.steps) {
      const d = stepDurationMs(step);
      if (d !== null) {
        total += d;
        hasKnown = true;
      }
    }
    if (isActive) {
      // While running: extend the last (in-progress) step with live elapsed,
      // so the header clock keeps ticking even before any step has `finished_at`.
      const earliest = earliestStepStartMs(process.steps);
      if (earliest !== null) {
        const liveElapsed = Math.max(0, liveNow - earliest);
        return formatDurationMs(liveElapsed);
      }
    }
    return formatDurationMs(hasKnown ? total : null);
  }, [process.steps, isActive, liveNow]);

  // Normalized hint text — surfaced for the active reasoning step.
  const hintText = useMemo(() => {
    if (!progressHint) return null;
    const label = typeof progressHint.label === 'string' ? progressHint.label.trim() : '';
    const tool = typeof progressHint.tool === 'string' ? progressHint.tool.trim() : '';
    const details = typeof progressHint.details === 'string' ? progressHint.details.trim() : '';
    const pieces: string[] = [];
    if (tool) pieces.push(tool);
    if (label && label.toLowerCase() !== tool.toLowerCase()) pieces.push(label);
    if (details && !pieces.some((p) => p.toLowerCase() === details.toLowerCase())) {
      pieces.push(details.length > 80 ? `${details.slice(0, 77)}…` : details);
    }
    const joined = pieces.join(' · ').trim();
    if (!joined) return null;
    if (isGenericText(joined, language)) return null;
    return joined;
  }, [progressHint, language]);

  const collapsedStats = useMemo(() => {
    const parts: string[] = [];
    if (stepCount > 0) {
      parts.push(localize(language, `${stepCount} 步`, `${stepCount} step${stepCount > 1 ? 's' : ''}`));
    }
    if (toolCallCount > 0) {
      parts.push(localize(language, `${toolCallCount} 次工具调用`, `${toolCallCount} tool call${toolCallCount > 1 ? 's' : ''}`));
    }
    return parts.join(' · ');
  }, [stepCount, toolCallCount, language]);

  const backendSummary = useMemo(() => {
    const s = typeof process.summary === 'string' ? process.summary.trim() : '';
    if (!s) return null;
    // Historical summaries are joins of per-step display labels ("a → b → c").
    // When every segment is a generic label ("处理当前步骤"…), the summary
    // carries no information — fall back to the stats line instead.
    const segments = s.split('→').map((x) => x.trim()).filter(Boolean);
    const meaningful = segments.some((seg) => seg.length > 1 && !isGenericText(seg, language));
    return meaningful ? s : null;
  }, [process.summary, language]);

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
    if (isActive && isExpanded && stepsEndRef.current) {
      stepsEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [visibleSteps.length, isActive, isExpanded]);

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
          <span className="tp-header-preview">{backendSummary || collapsedStats}</span>
        )}
        {isExpanded && collapsedStats && (
          <span className="tp-header-meta">{collapsedStats}</span>
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

      {/* Expanded activity stream */}
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
              {streamRows.map((row) =>
                row.type === 'step' ? (
                  <ThinkingActivityItem
                    key={`it-${row.step.iteration}`}
                    step={row.step}
                    isFinished={isFinished}
                    isProcessActive={isActive}
                    nextStep={nextStepByIteration.get(row.step.iteration)}
                    language={language}
                    liveNow={liveNow}
                    hintText={hintText}
                    sessionId={sessionId}
                  />
                ) : (
                  <ToolCallGroupRow
                    key={row.group.id}
                    group={row.group}
                    isFinished={isFinished}
                    isProcessActive={isActive}
                    language={language}
                    liveNow={liveNow}
                    hintText={hintText}
                    sessionId={sessionId}
                  />
                ),
              )}

              {/* "Preparing next step" indicator — only when active and last step is complete */}
              {isActive && visibleSteps.length > 0 && !['thinking', 'calling_tool', 'analyzing'].includes(visibleSteps[visibleSteps.length - 1]?.status || '') && (
                <motion.div
                  className="tp-item"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: [0.4, 0.8, 0.4] }}
                  transition={{ duration: 1.5, repeat: Infinity, ease: 'easeInOut' }}
                >
                  <div className="tp-item-row">
                    <div className="tp-item-icon reasoning">
                      <LoadingOutlined spin style={{ fontSize: 10 }} />
                    </div>
                    <span className="tp-item-label" style={{ color: 'var(--text-quaternary)' }}>
                      {hintText || localize(language, '准备下一步...', 'Preparing next step...')}
                    </span>
                  </div>
                </motion.div>
              )}

              {/* Empty state when no steps yet */}
              {isActive && visibleSteps.length === 0 && (
                <motion.div
                  className="tp-item"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: [0.4, 0.8, 0.4] }}
                  transition={{ duration: 1.5, repeat: Infinity, ease: 'easeInOut' }}
                >
                  <div className="tp-item-row">
                    <div className="tp-item-icon reasoning">
                      <LoadingOutlined spin style={{ fontSize: 10 }} />
                    </div>
                    <span className="tp-item-label" style={{ color: 'var(--text-quaternary)' }}>
                      {hintText || localize(language, '正在分析问题...', 'Analyzing the question...')}
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
