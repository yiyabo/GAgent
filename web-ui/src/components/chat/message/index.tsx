import React, { useMemo, useState } from 'react';
import {
  Typography,
  Space,
  Button,
  Divider,
  message as antdMessage,
} from 'antd';
import { ChatMessage as ChatMessageType, ToolResultPayload } from '@/types';
import { planTreeApi } from '@api/planTree';
import { chatApi } from '@api/chat';
import JobLogPanel from '../JobLogPanel';
import { ThinkingProcess } from '../ThinkingProcess';
import { MarkdownRenderer } from '../MarkdownRenderer';
import { TypingIndicator } from '../TypingIndicator';
import ArtifactGallery from '../ArtifactGallery';
import type { DecompositionJobStatus } from '@/types';
import { FINAL_JOB_STATUSES, normalizeJobStatus, computeDecomposeProgress } from './utils';
import MessageAvatar from './MessageAvatar';
import ToolProgressCard, { BackgroundDispatchCard } from './ToolProgressCard';
import MessageActions from './MessageActions';
import ToolResultDrawer, { ToolStatusBar } from './ToolResultDrawer';
import { extractLlmReplyMessage } from '@/utils/llmReplyDisplay';
import { collectArtifactGallery } from '@/utils/artifactGallery';

const { Text } = Typography;

interface ChatMessageProps {
  message: ChatMessageType;
  sessionId?: string | null;
}

const ChatMessageInner: React.FC<ChatMessageProps> = ({ message, sessionId: sessionIdProp }) => {
  const { type, content, timestamp, metadata } = message;
  const effectiveSessionId =
    sessionIdProp ??
    (typeof (metadata as any)?.session_id === 'string' ? (metadata as any).session_id : null);
  const [toolDrawerOpen, setToolDrawerOpen] = useState(false);
  const [pendingDetailOpen, setPendingDetailOpen] = useState(false);
  const unifiedStream = Boolean(metadata && (metadata as any).unified_stream);
  const decompositionJob = useMemo(() => {
    const direct = (metadata as any)?.decomposition_job;
    if (direct && typeof direct === 'object') {
      const jobId = (direct as any)?.job_id;
      if (typeof jobId === 'string' && jobId.trim().length > 0) {
        return direct as DecompositionJobStatus;
      }
    }
    const actions =
      (Array.isArray((metadata as any)?.actions) ? (metadata as any).actions : null) ??
      (Array.isArray((metadata as any)?.raw_actions) ? (metadata as any).raw_actions : []);
    for (let idx = actions.length - 1; idx >= 0; idx -= 1) {
      const embedded = actions[idx]?.details?.decomposition_job;
      if (embedded && typeof embedded === 'object') {
        const jobId = (embedded as any)?.job_id;
        if (typeof jobId === 'string' && jobId.trim().length > 0) {
          return embedded as DecompositionJobStatus;
        }
      }
    }
    return null;
  }, [metadata]);
  const decompositionJobId = typeof decompositionJob?.job_id === 'string' ? decompositionJob.job_id : null;
  const [decomposeSnapshot, setDecomposeSnapshot] = useState<DecompositionJobStatus | null>(null);
  const effectiveDecomposeJob = decomposeSnapshot ?? decompositionJob;
  const decomposeProgress = useMemo(
    () => computeDecomposeProgress(effectiveDecomposeJob),
    [effectiveDecomposeJob],
  );
  const decomposeStatus = decomposeProgress?.status ?? (effectiveDecomposeJob ? normalizeJobStatus(effectiveDecomposeJob.status) : null);
  const isDecomposeActive = Boolean(decompositionJobId) && Boolean(decomposeStatus) && !FINAL_JOB_STATUSES.has(decomposeStatus as string);
  const isDecomposeFailed = decomposeStatus === 'failed';
  const planMessage =
    unifiedStream && typeof (metadata as any)?.plan_message === 'string'
      ? ((metadata as any).plan_message as string)
      : null;
  React.useEffect(() => {
    if (!decompositionJobId) {
      setDecomposeSnapshot(null);
      return;
    }
    let cancelled = false;
    let timer: number | null = null;

    const stopTimer = () => {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };

    const fetchStatus = async () => {
      try {
        const snapshot = await planTreeApi.getJobStatus(decompositionJobId);
        if (cancelled) return;
        setDecomposeSnapshot(snapshot);
        const normalized = normalizeJobStatus(snapshot.status);
        if (FINAL_JOB_STATUSES.has(normalized)) {
          stopTimer();
        }
      } catch (error) {
        if (!cancelled) {
          stopTimer();
        }
      }
    };

    fetchStatus();
    timer = window.setInterval(fetchStatus, 5000);

    return () => {
      cancelled = true;
      stopTimer();
    };
  }, [decompositionJobId]);

  const toolResults: ToolResultPayload[] = useMemo(
    () =>
      Array.isArray(metadata?.tool_results)
        ? (metadata?.tool_results as ToolResultPayload[])
        : [],
    [metadata?.tool_results],
  );
  const artifactGallery = useMemo(
    () => collectArtifactGallery((metadata as any)?.artifact_gallery),
    [metadata],
  );
  const normalizedAssistantContent = useMemo(
    () => (type === 'assistant' ? extractLlmReplyMessage(content) : content),
    [type, content],
  );
  const analysisText =
    typeof (metadata as any)?.analysis_text === 'string'
      ? ((metadata as any)?.analysis_text as string)
      : '';
  const finalSummary =
    typeof (metadata as any)?.final_summary === 'string'
      ? ((metadata as any)?.final_summary as string)
      : (normalizedAssistantContent && normalizedAssistantContent.trim().length > 0
          ? normalizedAssistantContent
          : null);
  const displayText =
    analysisText && analysisText.trim().length > 0 ? analysisText : finalSummary;
  /** Coerce legacy simple-chat JSON blobs during stream or in metadata. */
  const displayTextForUi =
    type === 'assistant' && typeof displayText === 'string'
      ? extractLlmReplyMessage(displayText)
      : displayText;
  const processSummary =
    finalSummary &&
      displayText &&
      finalSummary.trim().length > 0 &&
      finalSummary.trim() !== displayText.trim()
      ? finalSummary
      : null;
  const status = metadata?.status;
  const isStreaming =
    unifiedStream && (status === 'pending' || status === 'running');
  /** Same id as chat run (dt_...). Prefer chat_run_id — it is set as soon as the run is created; deep_think_job_id may arrive later via control_ack. */
  const chatRunIdFromMeta =
    typeof (metadata as any)?.chat_run_id === 'string'
      ? String((metadata as any).chat_run_id).trim()
      : null;
  const deepThinkJobId =
    typeof (metadata as any)?.deep_think_job_id === 'string'
      ? String((metadata as any).deep_think_job_id).trim()
      : null;
  const runIdForDeepThink = chatRunIdFromMeta || deepThinkJobId;
  const hasCompactProgress =
    (metadata as any)?.thinking_visibility === 'progress' &&
    Boolean((metadata as any)?.deep_think_progress);
  const deepThinkPausedFromMetadata = Boolean((metadata as any)?.deep_think_paused);
  const showThinkingProcess =
    Boolean(message.thinking_process) &&
    (metadata as any)?.thinking_visibility === 'visible';
  const [deepThinkPaused, setDeepThinkPaused] = useState<boolean>(deepThinkPausedFromMetadata);
  const [deepThinkControlBusyAction, setDeepThinkControlBusyAction] = useState<
    'pause' | 'resume' | 'skip_step' | null
  >(null);
  const [deepThinkCancelRunBusy, setDeepThinkCancelRunBusy] = useState(false);
  const thinkingIsFinished =
    message.metadata?.status === 'completed' ||
    message.metadata?.status === 'failed' ||
    message.thinking_process?.status === 'completed' ||
    message.thinking_process?.status === 'error';
  const deepThinkCanControl = Boolean(
    runIdForDeepThink &&
      message.thinking_process?.status === 'active' &&
      !thinkingIsFinished,
  );
  const deepThinkControlDisabled =
    !runIdForDeepThink || deepThinkControlBusyAction !== null || deepThinkCancelRunBusy;

  const issueCancelRun = React.useCallback(async () => {
    if (!runIdForDeepThink || deepThinkCancelRunBusy) return;
    setDeepThinkCancelRunBusy(true);
    try {
      await chatApi.cancelRun(runIdForDeepThink);
      antdMessage.success('已请求停止本次运行');
    } catch (error) {
      console.warn('Failed to cancel chat run:', error);
      antdMessage.error(
        error instanceof Error ? error.message : '停止失败，请稍后重试或刷新页面',
      );
    } finally {
      setDeepThinkCancelRunBusy(false);
    }
  }, [runIdForDeepThink, deepThinkCancelRunBusy]);

  React.useEffect(() => {
    setDeepThinkPaused(deepThinkPausedFromMetadata);
  }, [deepThinkPausedFromMetadata, runIdForDeepThink]);

  const issueDeepThinkControl = React.useCallback(
    async (action: 'pause' | 'resume' | 'skip_step') => {
      if (!runIdForDeepThink || deepThinkControlBusyAction !== null) return;
      setDeepThinkControlBusyAction(action);
      try {
        const response = await planTreeApi.controlJob(runIdForDeepThink, { action });
        if (response.success) {
          if (action === 'pause') setDeepThinkPaused(true);
          if (action === 'resume') setDeepThinkPaused(false);
        }
      } catch (error) {
        console.warn('Failed to control deep think runtime:', error);
      } finally {
        setDeepThinkControlBusyAction(null);
      }
    },
    [runIdForDeepThink, deepThinkControlBusyAction],
  );

  // In unified stream mode, show typing indicator only at initial pending stage
  // when there is no preface/actions and no thinking steps yet.
  const hasThinkingSteps = message.thinking_process && message.thinking_process.steps && message.thinking_process.steps.length > 0;
  if (
    unifiedStream &&
    metadata?.status === 'pending' &&
    !planMessage &&
    !(metadata as any)?.raw_actions?.length &&
    !(metadata as any)?.actions?.length &&
    (content?.trim?.() ?? '') === '' &&
    !analysisText &&
    !hasCompactProgress &&
    !hasThinkingSteps  // If thinking steps exist, render ThinkingProcess instead.
  ) {
    return <TypingIndicator message="Thinking..." showAvatar={true} />;
  }
  const hasFooterDivider =
    !unifiedStream &&
    (toolResults.length > 0 ||
      (!!metadata &&
        (metadata.type === 'job_log' ||
          metadata.plan_id !== undefined ||
          metadata.plan_title)));
  const isPendingAction =
    type === 'assistant' &&
    metadata?.status === 'pending' &&
    Array.isArray(metadata?.raw_actions) &&
    metadata.raw_actions.length > 0;

  const renderPendingActions = () => {
    if (!isPendingAction) return null;
    if (unifiedStream) return null;
    const actions = Array.isArray(metadata?.raw_actions) ? metadata?.raw_actions : [];
    const visible = pendingDetailOpen ? actions : actions.slice(0, 3);

    return (
      <div
        style={{
          marginTop: 10,
          padding: '10px 12px',
          borderRadius: 'var(--radius-sm)',
          border: '1px dashed var(--border-color)',
          background: 'var(--bg-tertiary)',
          fontSize: 12,
        }}
      >
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          <Space align="center" size={8}>
            <div
              style={{
                width: 6,
                height: 6,
                borderRadius: 6,
                background: 'var(--primary-color)',
              }}
            />
            <Text type="secondary" style={{ opacity: 0.8, fontSize: 12 }}>
              Planning {actions.length} tool actions...
            </Text>
          </Space>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
            {visible.map((act: any, idx: number) => {
              const kind = typeof act?.kind === 'string' ? act.kind : 'action';
              const name = typeof act?.name === 'string' ? act.name : '';
              const order = typeof act?.order === 'number' ? act.order : idx + 1;
              return (
                <div key={`${order}_${kind}_${name}`} style={{ marginBottom: 4 }}>
                  • Step {order}: {kind}
                  {name ? ` / ${name}` : ''}
                </div>
              );
            })}
            {actions.length > 3 && !pendingDetailOpen && <Text type="secondary">…</Text>}
          </div>
          {actions.length > 3 && (
            <Button
              type="link"
              size="small"
              onClick={() => setPendingDetailOpen((v) => !v)}
              style={{ padding: 0 }}
            >
              {pendingDetailOpen ? 'Collapse Plan' : 'Expand Full Plan'}
            </Button>
          )}
        </Space>
      </div>
    );
  };

  // ---- Background dispatch card ----
  const bgCategory = (metadata as any)?.background_category as string | undefined;
  const isBackgroundDispatch = Boolean(bgCategory && (bgCategory === 'phagescope' || bgCategory === 'code_executor' || bgCategory === 'task_creation'));

  const renderSummary = () => {
    const responsePlaceholderVisible =
      unifiedStream &&
      isStreaming &&
      !hasCompactProgress &&
      !displayTextForUi;
    if (responsePlaceholderVisible) {
      // When ThinkingProcess is active and visible, don't show redundant placeholder —
      // the ThinkingProcess component itself already shows live status.
      if (showThinkingProcess && message.thinking_process?.status === 'active') {
        return null;
      }
      const placeholderText = showThinkingProcess ? '正在生成回答…' : '正在准备回复…';
      return (
        <div
          style={{
            marginTop: 8,
            color: 'var(--text-secondary)',
            fontSize: 14,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {placeholderText}
          <span className="stream-cursor">▍</span>
        </div>
      );
    }
    if (!displayTextForUi) return null;
    // Use Markdown during streaming too; plain text showed raw ### / table pipes to users.
    return (
      <div style={{ marginTop: 4 }} className="assistant-markdown-stream">
        <MarkdownRenderer content={displayTextForUi} sessionId={effectiveSessionId} />
        {isStreaming ? <span className="stream-cursor">▍</span> : null}
      </div>
    );
  };

  const renderUnifiedStatusLine = () => {
    return null;
  };

  // Render message content.
  const renderContent = () => {
    // For unified stream, primary content uses renderSummary; otherwise Markdown.
    if (type === 'system') {
      return (
        <Text type="secondary" style={{ fontStyle: 'italic' }}>
          {content}
        </Text>
      );
    }
    if (unifiedStream) {
      return null;
    }
    const body = type === 'assistant' ? normalizedAssistantContent : content;
    return <MarkdownRenderer content={body} sessionId={effectiveSessionId} />;
  };

  // Render metadata.
  const renderMetadata = () => {
    if (!metadata) return null;
    const planTitle = metadata.plan_title;
    const planId = metadata.plan_id;
    if (!planTitle && (planId === undefined || planId === null)) {
      return null;
    }
    return (
      <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
        <div>
          Linked Plan:
          {planTitle ? ` ${planTitle}` : ''}
          {planId !== undefined && planId !== null ? ` (#${planId})` : ''}
        </div>
      </div>
    );
  };

  const renderJobLogPanel = () => {
    if (!metadata || metadata.type !== 'job_log') return null;
    if (unifiedStream) return null;
    const jobMetadata = (metadata.job as DecompositionJobStatus | null) ?? null;
    const jobId: string | undefined = metadata.job_id ?? jobMetadata?.job_id;
    if (!jobId) return null;
    return (
      <JobLogPanel
        jobId={jobId}
        initialJob={jobMetadata}
        targetTaskName={metadata.target_task_name ?? null}
        planId={metadata.plan_id ?? null}
        jobType={metadata.job_type ?? jobMetadata?.job_type ?? null}
      />
    );
  };

  return (
    <div className={`message ${type}`}>
      <div className="message-content">
        {type === 'assistant' && <MessageAvatar type={type} />}

        <div className="message-bubble">
          {(() => {
            // Prefer summary block even outside unified stream mode.
            const summaryBlock = renderSummary();
            if (unifiedStream) {
              return (
                <>
                  {isBackgroundDispatch ? (
                    <BackgroundDispatchCard metadata={metadata} />
                  ) : (
                    <ToolProgressCard
                      metadata={metadata}
                      isDecomposeActive={isDecomposeActive}
                      isDecomposeFailed={isDecomposeFailed}
                      decomposeProgress={decomposeProgress}
                      effectiveDecomposeJob={effectiveDecomposeJob}
                      processSummary={processSummary}
                    />
                  )}
                  {/* Thinking Process */}
                  {showThinkingProcess && message.thinking_process && (
                    <ThinkingProcess
                      process={message.thinking_process}
                      isFinished={thinkingIsFinished}
                      canControl={deepThinkCanControl}
                      onPause={() => {
                        void issueDeepThinkControl('pause');
                      }}
                      onResume={() => {
                        void issueDeepThinkControl('resume');
                      }}
                      onSkipStep={() => {
                        void issueDeepThinkControl('skip_step');
                      }}
                      onCancelRun={issueCancelRun}
                      paused={deepThinkPaused}
                      controlDisabled={deepThinkControlDisabled}
                      controlBusy={deepThinkControlBusyAction !== null}
                      controlBusyAction={deepThinkControlBusyAction}
                      cancelRunBusy={deepThinkCancelRunBusy}
                    />
                  )}
                  {summaryBlock}
                  {artifactGallery.length > 0 && (
                    <ArtifactGallery items={artifactGallery} sessionId={effectiveSessionId} />
                  )}
                </>
              );
            }
            return (
              <>
                {hasCompactProgress && (
                  <ToolProgressCard
                    metadata={metadata}
                    isDecomposeActive={isDecomposeActive}
                    isDecomposeFailed={isDecomposeFailed}
                    decomposeProgress={decomposeProgress}
                    effectiveDecomposeJob={effectiveDecomposeJob}
                    processSummary={processSummary}
                  />
                )}
                {/* Thinking Process - always render if present */}
                {showThinkingProcess && message.thinking_process && (
                  <ThinkingProcess
                    process={message.thinking_process}
                    isFinished={thinkingIsFinished}
                    canControl={deepThinkCanControl}
                    onPause={() => {
                      void issueDeepThinkControl('pause');
                    }}
                    onResume={() => {
                      void issueDeepThinkControl('resume');
                    }}
                    onSkipStep={() => {
                      void issueDeepThinkControl('skip_step');
                    }}
                    onCancelRun={issueCancelRun}
                    paused={deepThinkPaused}
                    controlDisabled={deepThinkControlDisabled}
                    controlBusy={deepThinkControlBusyAction !== null}
                    controlBusyAction={deepThinkControlBusyAction}
                    cancelRunBusy={deepThinkCancelRunBusy}
                  />
                )}
                {summaryBlock ?? renderContent()}
                {artifactGallery.length > 0 && (
                  <ArtifactGallery items={artifactGallery} sessionId={effectiveSessionId} />
                )}
                {renderPendingActions()}
                {!isPendingAction && renderUnifiedStatusLine()}
                {!isPendingAction && (
                  <ToolStatusBar
                    toolResults={toolResults}
                    isPendingAction={isPendingAction}
                    unifiedStream={unifiedStream}
                    onOpenDrawer={() => setToolDrawerOpen(true)}
                  />
                )}
                {hasFooterDivider && !isPendingAction && <Divider style={{ margin: '12px 0' }} dashed />}
                {!isPendingAction && renderJobLogPanel()}
                {renderMetadata()}
              </>
            );
          })()}
        </div>
      </div>

      <MessageActions message={message} />
      {!isPendingAction && (
        <ToolResultDrawer
          toolResults={toolResults}
          unifiedStream={unifiedStream}
          open={toolDrawerOpen}
          onClose={() => setToolDrawerOpen(false)}
          actionsSummary={metadata?.actions_summary as Array<Record<string, any>>}
          sessionId={effectiveSessionId}
        />
      )}
    </div>
  );
};

const ChatMessage = React.memo(ChatMessageInner);
export { ChatMessage };
export default ChatMessage;
