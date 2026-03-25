import React, { useState, useEffect, useMemo, useCallback } from 'react';
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
import { Button } from 'antd';
import './ThinkingProcess.css';

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

const CJK_CHAR_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;
const INTERNAL_REASONING_RE = /\b(?:the user is asking me|i notice this is just|i should\b|i need to\b|i'm ready to help|continue thinking|thinking about next step)\b/gi;

const TOOL_META: Record<string, { icon: React.ReactNode; zh: string; en: string }> = {
  web_search: { icon: <GlobalOutlined />, zh: '检索资料', en: 'Searching the web' },
  file_operations: { icon: <FileTextOutlined />, zh: '处理文件', en: 'Working with files' },
  claude_code: { icon: <CodeOutlined />, zh: '执行代码与分析', en: 'Executing code' },
  bio_tools: { icon: <ExperimentOutlined />, zh: '运行分析工具', en: 'Running analysis tools' },
  document_reader: { icon: <FileTextOutlined />, zh: '阅读文档', en: 'Reading documents' },
  vision_reader: { icon: <EyeOutlined />, zh: '分析图像内容', en: 'Analyzing visual content' },
  graph_rag: { icon: <DatabaseOutlined />, zh: '查询知识图谱', en: 'Querying knowledge graph' },
  phagescope: { icon: <ExperimentOutlined />, zh: '运行 PhageScope', en: 'Running PhageScope' },
  result_interpreter: { icon: <DatabaseOutlined />, zh: '汇总分析结果', en: 'Interpreting results' },
  plan_operation: { icon: <ProjectOutlined />, zh: '更新计划信息', en: 'Managing the plan' },
};

function localize(language: 'zh' | 'en', zh: string, en: string): string {
  return language === 'zh' ? zh : en;
}

function detectLanguage(process: ThinkingProcessType): 'zh' | 'en' {
  const samples = [
    process.summary,
    ...process.steps.flatMap((step) => [
      step.display_text,
      step.thought,
      step.action_result,
    ]),
  ];
  for (const sample of samples) {
    if (typeof sample !== 'string' || !sample.trim()) continue;
    return CJK_CHAR_RE.test(sample) ? 'zh' : 'en';
  }
  return 'en';
}

function sanitizeLegacyThought(text: string | null | undefined): string {
  const raw = String(text || '').replace(INTERNAL_REASONING_RE, '').replace(/\s+/g, ' ').trim();
  if (!raw) return '';
  if (raw.length <= 72) return raw;
  return `${raw.slice(0, 69).trim()}...`;
}

function normalizeComparableText(text: string | null | undefined): string {
  return String(text || '').replace(/\s+/g, ' ').trim();
}

function isGenericThinkingText(text: string | null | undefined, language: 'zh' | 'en'): boolean {
  const normalized = normalizeComparableText(text);
  if (!normalized) return false;
  if (language === 'zh') {
    return (
      normalized === '分析当前问题，准备下一步' ||
      normalized === '准备下一步' ||
      normalized === '准备整理回复'
    );
  }
  return (
    normalized === 'Analyzing the request and preparing the next step' ||
    normalized === 'Preparing the next step' ||
    normalized === 'Preparing the response'
  );
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
    return {
      icon: <ToolOutlined />,
      label: localize(language, '调用工具', 'Using a tool'),
      toolName: 'unknown',
    };
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
  const meta = TOOL_META[toolName] || {
    icon: <ToolOutlined />,
    zh: `调用工具：${toolName}`,
    en: `Using tool: ${toolName}`,
  };
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
      if (params.operation === 'list') {
        label = localize(language, `查看目录：${params.path || '/'}`, `Listing directory: ${params.path || '/'}`);
      } else if (params.operation === 'read') {
        label = localize(language, `读取文件：${fileName}`, `Reading: ${fileName}`);
      } else if (params.operation) {
        label = localize(language, `处理文件：${fileName}`, `${params.operation}: ${fileName}`);
      }
      break;
    }
    case 'claude_code':
      if (params.task) {
        label = localize(
          language,
          `执行代码任务：${String(params.task).slice(0, 48)}`,
          `Code task: ${String(params.task).slice(0, 60)}`,
        );
      }
      break;
    case 'document_reader':
      if (params.file_path) {
        label = localize(
          language,
          `阅读文档：${String(params.file_path).split('/').pop()}`,
          `Reading: ${String(params.file_path).split('/').pop()}`,
        );
      }
      break;
    case 'vision_reader':
      if (params.file_path) {
        label = localize(
          language,
          `分析图像：${String(params.file_path).split('/').pop()}`,
          `Analyzing: ${String(params.file_path).split('/').pop()}`,
        );
      }
      break;
  }

  return { icon: meta.icon, label, toolName };
}

function extractResultSummary(result: string | null | undefined): string | null {
  if (!result) return null;
  const trimmed = result.trim();
  if (trimmed.length <= 100) return trimmed;
  const firstLine = trimmed.split('\n')[0].trim();
  if (firstLine.length >= 12 && firstLine.length <= 200) return firstLine;
  return `${trimmed.slice(0, 100)}...`;
}

function stepHasToolError(step: ThinkingStep): boolean {
  if (step.status === 'error') return true;
  const result = step.action_result;
  return typeof result === 'string' && /^Error[ :]/.test(result);
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

function getVisibleStepText(step: ThinkingStep, language: 'zh' | 'en'): string {
  if (typeof step.display_text === 'string' && step.display_text.trim()) {
    return step.display_text.trim();
  }
  if (step.action) {
    return (
      extractSemanticLabel(step.action, language)?.label ||
      localize(language, '调用工具', 'Using a tool')
    );
  }
  return sanitizeLegacyThought(step.thought) || localize(language, '分析当前问题', 'Analyzing the request');
}

function getExpandedStepText(step: ThinkingStep, language: 'zh' | 'en'): string {
  const rawThought = String(step.thought || '').trim();
  if (rawThought) {
    return rawThought;
  }
  return getVisibleStepText(step, language);
}

function getMainSteps(steps: ThinkingStep[], language: 'zh' | 'en'): ThinkingStep[] {
  const visibleTexts = steps.map((step) => getVisibleStepText(step, language));
  const hasSpecificReasoning = visibleTexts.some((text) => text && !isGenericThinkingText(text, language));
  return steps.filter((step, index) => {
    const text = visibleTexts[index];
    if (!step.action && hasSpecificReasoning && isGenericThinkingText(text, language)) {
      return false;
    }
    return !!step.action || !!text;
  });
}

function getProcessSummary(process: ThinkingProcessType, language: 'zh' | 'en'): string {
  if (typeof process.summary === 'string' && process.summary.trim()) {
    return process.summary.trim();
  }
  const visibleTexts = getMainSteps(process.steps, language)
    .map((step) => getVisibleStepText(step, language))
    .filter(Boolean)
    .slice(0, 3);
  if (visibleTexts.length > 1) {
    return visibleTexts.join(' -> ');
  }
  if (visibleTexts.length === 1) {
    return visibleTexts[0];
  }
  return localize(language, '整理思考过程', 'Organizing the reasoning process');
}

function hasExplicitSummary(process: ThinkingProcessType): boolean {
  return typeof process.summary === 'string' && process.summary.trim().length > 0;
}

function isLegacyThoughtOnlyStep(step: ThinkingStep): boolean {
  return !step.action && !step.display_text && typeof step.thought === 'string' && step.thought.trim().length > 0;
}

const ErrorInline: React.FC<{
  step: ThinkingStep;
  nextStep?: ThinkingStep;
  language: 'zh' | 'en';
}> = ({ step, nextStep, language }) => {
  const errorDetail =
    step.action_result?.match(/^Error[: ]\s*(.+)/s)?.[1] ??
    getVisibleStepText(step, language) ??
    localize(language, '未知错误', 'Unknown error');
  const truncated = errorDetail.length > 120 ? `${errorDetail.slice(0, 120)}...` : errorDetail;

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
          <span className="tp-error-correction">
            {localize(language, '正在尝试下一步', 'Trying the next step')}
          </span>
        </>
      )}
    </div>
  );
};

const ThinkingStepItem: React.FC<{
  step: ThinkingStep;
  isLast: boolean;
  isFinished?: boolean;
  nextStep?: ThinkingStep;
  language: 'zh' | 'en';
}> = ({ step, isLast, isFinished, nextStep, language }) => {
  const [detailExpanded, setDetailExpanded] = useState(false);

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
    () => extractSemanticLabel(step.action, language),
    [step.action, language],
  );
  const displayText = useMemo(
    () => getVisibleStepText(step, language),
    [step, language],
  );
  const expandedThought = useMemo(
    () => getExpandedStepText(step, language),
    [step, language],
  );
  const showThoughtBlock = !isTool && !!expandedThought;
  const showToolThoughtBlock =
    isTool &&
    !!expandedThought &&
    normalizeComparableText(expandedThought) !== normalizeComparableText(displayText);
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
      {showThoughtBlock && (
        <div className="tp-thought-inline">{expandedThought}</div>
      )}

      {isTool && (
        <>
          {showToolThoughtBlock && (
            <div className="tp-thought-inline">{expandedThought}</div>
          )}
          <div
            className="tp-tool-pill"
            onClick={() => setDetailExpanded((value) => !value)}
          >
            <div className={`tp-tool-pill-icon${isError ? ' error' : ''}`}>
              {semantic?.icon || <ToolOutlined />}
            </div>
            <span className="tp-tool-pill-label">{displayText || semantic?.label}</span>
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

          {!detailExpanded && !isError && resultSummary && (
            <div
              style={{
                fontSize: 12,
                color: 'var(--text-tertiary)',
                paddingLeft: 26,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {resultSummary}
            </div>
          )}

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
                    <div className="tp-step-detail-label">
                      {localize(language, '参数', 'Parameters')}
                    </div>
                    <pre className="tp-step-detail-pre">
                      {typeof actionDetails.params === 'object'
                        ? JSON.stringify(actionDetails.params, null, 2)
                        : String(actionDetails.params)}
                    </pre>
                  </div>
                )}
                {step.action_result && (
                  <div className="tp-step-detail-section">
                    <div className="tp-step-detail-label">
                      {localize(language, '结果', 'Result')}
                    </div>
                    <pre className="tp-step-detail-pre">{step.action_result}</pre>
                  </div>
                )}
                {Array.isArray(step.evidence) && step.evidence.length > 0 && (
                  <div className="tp-step-detail-section">
                    <div className="tp-step-detail-label">
                      {localize(language, '证据', 'Evidence')}
                    </div>
                    <div className="tp-evidence-list">
                      {step.evidence.map((ev, evIdx) => (
                        <div className="tp-evidence-item" key={`${ev.ref || 'ev'}_${evIdx}`}>
                          <div className="tp-evidence-item-title">
                            {ev.title || ev.type || localize(language, '证据', 'Evidence')}
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

      {isError && <ErrorInline step={step} nextStep={nextStep} language={language} />}
    </motion.div>
  );
};

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
  const language = useMemo(() => detectLanguage(process), [process]);
  const mainSteps = useMemo(() => getMainSteps(process.steps, language), [process.steps, language]);
  const mainStepsCount = mainSteps.length;
  const isActive = process.status === 'active' && !isFinished;
  const summaryText = useMemo(() => getProcessSummary(process, language), [process, language]);
  const firstMainStepText = useMemo(
    () => (mainSteps.length > 0 ? getExpandedStepText(mainSteps[0], language) : ''),
    [mainSteps, language],
  );
  const explicitSummary = useMemo(() => hasExplicitSummary(process), [process]);
  const showSummaryBlock =
    !!summaryText &&
    normalizeComparableText(summaryText) !== normalizeComparableText(firstMainStepText) &&
    !(
      !explicitSummary &&
      mainSteps.length === 1 &&
      isLegacyThoughtOnlyStep(mainSteps[0])
    );

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
      requestAnimationFrame(() => {
        el.style.overflowY = 'auto';
      });
    }
  }, []);

  return (
    <div className="tp-inline-container">
      <div className="tp-summary-row" onClick={() => setIsExpanded(!isExpanded)}>
        <span className="tp-summary-icon">
          {isActive ? (
            <LoadingOutlined spin style={{ color: 'var(--primary-color)' }} />
          ) : (
            <BulbOutlined style={{ color: 'var(--primary-color)' }} />
          )}
        </span>
        <span className="tp-summary-label">
          {isActive ? localize(language, '思考中...', 'Thinking...') : localize(language, '思考过程', 'Thought process')}
        </span>
        <span className="tp-summary-preview">{summaryText}</span>
        {mainStepsCount > 0 && (
          <span className="tp-summary-meta">
            {localize(language, `${mainStepsCount} 步`, `${mainStepsCount} step${mainStepsCount > 1 ? 's' : ''}`)}
          </span>
        )}
        <span className="tp-summary-meta">{totalDuration}</span>

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
              {'Skip'}
            </Button>
          </span>
        )}

        <CaretRightOutlined className={`tp-summary-chevron${isExpanded ? ' expanded' : ''}`} />
      </div>

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
              {showSummaryBlock && (
                <div className="tp-summary-full">{summaryText}</div>
              )}
              {mainSteps.map((step, idx) => (
                <ThinkingStepItem
                  key={`${step.iteration}_${idx}`}
                  step={step}
                  isLast={idx === mainSteps.length - 1}
                  isFinished={isFinished}
                  nextStep={idx < mainSteps.length - 1 ? mainSteps[idx + 1] : undefined}
                  language={language}
                />
              ))}

              {isActive && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  style={{ padding: '4px 0' }}
                >
                  <span className="tp-thought-inline">
                    {localize(language, '准备下一步', 'Preparing the next step')}
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
