import type {
  ChatResponsePayload,
  ChatResponseMetadata,
  ChatActionStatus,
  ChatActionSummary,
  ActionStatusResponse,
} from '@/types';
import {
  isActionStatus,
  mapJobStatusToChatStatus,
  buildActionsFromSteps,
  formatToolPlanPreface,
  summarizeSteps,
  summarizeActions,
  convertRawActionToSummary,
  waitForActionCompletionViaStream,
  activeActionFollowups,
  autoTitleHistory,
  detectBackgroundCategory,
  BACKGROUND_CATEGORY_LABELS,
} from '../../chatUtils';
import { chatApi } from '@api/chat';
import {
  mergeToolResults,
  collectToolResultsFromMetadata,
  collectToolResultsFromSteps,
} from '@utils/toolResults';
import {
  derivePlanSyncEventsFromActions,
  dispatchPlanSyncEvent,
  extractPlanIdFromActions,
  extractPlanTitleFromActions,
  coercePlanId,
  coercePlanTitle,
} from '@utils/planSyncEvents';
import { SessionStorage } from '@/utils/sessionStorage';
import type { StreamHandlerContext } from './types';

export function handleDelta(ctx: StreamHandlerContext, event: any): void {
  ctx.state.streamedContent += event.content ?? '';
  ctx.scheduleFlush();
}

export async function handleJobUpdate(ctx: StreamHandlerContext, event: any): Promise<void> {
  const payload = event.payload ?? {};
  const targetMessage = ctx.get().messages.find((msg: any) => msg.id === ctx.assistantMessageId);
  if (!targetMessage) return;
  const existingMetadata: ChatResponseMetadata = { ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}) };
  const jobStatus = mapJobStatusToChatStatus(payload.status);
  const stepList = Array.isArray(payload.result?.steps) ? (payload.result?.steps as Array<Record<string, any>>) : [];
  const actionsFromSteps = buildActionsFromSteps(stepList);
  const mergedToolResults = mergeToolResults(collectToolResultsFromMetadata(existingMetadata.tool_results), mergeToolResults(collectToolResultsFromMetadata(payload.result?.tool_results), collectToolResultsFromSteps(stepList)));
  const updatedMetadata: ChatResponseMetadata = { ...existingMetadata, status: jobStatus ?? existingMetadata.status };
  (updatedMetadata as any).unified_stream = true;
  // Tool progress (e.g., PhageScope polling progress)
  const toolProgress = (payload.stats && typeof payload.stats === 'object' ? (payload.stats as any).tool_progress : null) ?? null;
  if (toolProgress && typeof toolProgress === 'object') {
    (updatedMetadata as any).tool_progress = toolProgress;
  }
  if (!(updatedMetadata as any).plan_message) {
    (updatedMetadata as any).plan_message = actionsFromSteps.length > 0 ? formatToolPlanPreface(actionsFromSteps) : targetMessage.content || null;
  }
  const analysisFromPayload = typeof payload.result?.analysis_text === 'string' ? payload.result.analysis_text : (typeof payload.metadata?.analysis_text === 'string' ? payload.metadata.analysis_text : null);
  if (analysisFromPayload?.trim()) updatedMetadata.analysis_text = analysisFromPayload;
  if (payload.job_id && !updatedMetadata.tracking_id) updatedMetadata.tracking_id = payload.job_id;
  if (actionsFromSteps.length > 0) { updatedMetadata.actions = actionsFromSteps; updatedMetadata.action_list = actionsFromSteps; }
  if (mergedToolResults.length > 0) updatedMetadata.tool_results = mergedToolResults;
  if (payload.error) updatedMetadata.errors = [...(updatedMetadata.errors ?? []), payload.error];
  ctx.get().updateMessage(ctx.assistantMessageId, { metadata: updatedMetadata });

  if (ctx.state.jobFinalized || (payload.status !== 'succeeded' && payload.status !== 'failed' && payload.status !== 'completed')) return;
  ctx.state.jobFinalized = true;

  const normalizedFinalStatus = payload.status === 'completed' ? 'succeeded' : payload.status;
  const finalActions = actionsFromSteps.length > 0 ? actionsFromSteps : (updatedMetadata.actions ?? []);
  const finalPlanIdCandidate = coercePlanId(payload.result?.bound_plan_id) ?? extractPlanIdFromActions(finalActions) ?? updatedMetadata.plan_id ?? null;
  let planTitleFromSteps: string | null | undefined;
  for (const step of stepList) {
    const candidate = coercePlanTitle(step?.details?.title) ?? coercePlanTitle(step?.details?.plan_title);
    if (candidate !== undefined) { planTitleFromSteps = candidate ?? null; break; }
  }
  const finalPlanTitle = planTitleFromSteps ?? coercePlanTitle(payload.result?.plan_title) ?? updatedMetadata.plan_title ?? null;

  // Helper logic for content consolidation
  let contentCandidate: string | null = (updatedMetadata.analysis_text?.trim() ? updatedMetadata.analysis_text : null);
  if (!contentCandidate) {
    const results = payload.result || {};
    const meta = payload.metadata || {};
    contentCandidate =
      (typeof results.final_summary === 'string' ? results.final_summary : null) ||
      (typeof results.reply === 'string' ? results.reply : null) ||
      (typeof meta.final_summary === 'string' ? meta.final_summary : null) ||
      (typeof results.response === 'string' ? results.response : null) ||
      (typeof results.message === 'string' ? results.message : null) ||
      (typeof results.text === 'string' ? results.text : null) ||
      summarizeSteps(stepList) ||
      null;
    if (!contentCandidate) {
      const rawActions = Array.isArray((updatedMetadata as any).raw_actions)
        ? (updatedMetadata as any).raw_actions.map((act: any) => convertRawActionToSummary(act))
        : [];
      contentCandidate = summarizeActions(finalActions.length > 0 ? finalActions : rawActions);
    }
  }
  const fallbackSummary = normalizedFinalStatus === 'succeeded' && mergedToolResults.length > 0
    ? 'Tool execution completed. Please review the results.'
    : targetMessage.content;
  const contentWithStatus = contentCandidate || (
    normalizedFinalStatus === 'failed' && updatedMetadata.errors?.length
      ? `${targetMessage.content}\n\n⚠️ Background execution failed: ${updatedMetadata.errors.join('; ')}`
      : fallbackSummary
  );

  ctx.get().updateMessage(ctx.assistantMessageId, { content: contentWithStatus, metadata: { ...updatedMetadata, status: normalizedFinalStatus === 'succeeded' ? 'completed' : 'failed', plan_id: finalPlanIdCandidate ?? null, plan_title: finalPlanTitle ?? null, final_summary: (typeof payload.result?.final_summary === 'string' ? payload.result.final_summary : undefined), analysis_text: (updatedMetadata.analysis_text ?? null) } });

  const tracking = (updatedMetadata.tracking_id ?? payload.job_id) as string | undefined;
  if (tracking) ctx.startActionStatusPolling(tracking, ctx.assistantMessageId, normalizedFinalStatus as ChatActionStatus, contentWithStatus);

  ctx.set((state: any) => {
    const planIdValue = finalPlanIdCandidate ?? state.currentPlanId ?? null;
    const planTitleValue = finalPlanTitle ?? state.currentPlanTitle ?? null;
    const updatedSession = state.currentSession ? { ...state.currentSession, plan_id: planIdValue, plan_title: planTitleValue } : null;
    return {
      currentPlanId: planIdValue,
      currentPlanTitle: planTitleValue,
      currentSession: updatedSession ?? state.currentSession,
      sessions: updatedSession ? state.sessions.map((s: any) => s.id === updatedSession.id ? updatedSession : s) : state.sessions,
    };
  });

  const sessionAfter = ctx.get().currentSession;
  const eventsToDispatch = derivePlanSyncEventsFromActions(finalActions, { fallbackPlanId: finalPlanIdCandidate ?? sessionAfter?.plan_id ?? null, fallbackPlanTitle: finalPlanTitle ?? sessionAfter?.plan_title ?? null });
  if (eventsToDispatch.length > 0) {
    for (const eventDetail of eventsToDispatch) dispatchPlanSyncEvent(eventDetail, { trackingId: updatedMetadata.tracking_id ?? null, source: 'chat.stream', status: normalizedFinalStatus as any, sessionId: sessionAfter?.session_id ?? null });
  } else if (finalPlanIdCandidate != null) {
    dispatchPlanSyncEvent({ type: 'task_changed', plan_id: finalPlanIdCandidate, plan_title: finalPlanTitle ?? sessionAfter?.plan_title ?? null }, { trackingId: updatedMetadata.tracking_id ?? null, source: 'chat.stream', status: normalizedFinalStatus as any, sessionId: sessionAfter?.session_id ?? null });
  }
  if (sessionAfter) {
    try { await chatApi.updateSession(sessionAfter.session_id ?? sessionAfter.id, { plan_id: finalPlanIdCandidate ?? null, plan_title: finalPlanTitle ?? null, is_active: normalizedFinalStatus === 'succeeded' }); } catch (patchError) { console.warn('Failed to sync session info:', patchError); }
    void ctx.get().loadChatHistory(sessionAfter.session_id ?? sessionAfter.id).catch((e: any) => console.warn('Failed to sync history:', e));
  }
  if (ctx.state.flushHandle !== null) { window.cancelAnimationFrame(ctx.state.flushHandle); ctx.state.flushHandle = null; }
  ctx.flushAnalysisText(true);
}

/** Returns true if the loop should break (background dispatch). */
export function handleFinal(ctx: StreamHandlerContext, event: any): boolean {
  ctx.state.finalPayload = event.payload;

  // ---- Background dispatch: detect long-running tasks ----
  const bgCategory = detectBackgroundCategory(event.payload);
  if (bgCategory) {
    // Flush any streamed analysis text accumulated so far.
    if (ctx.state.flushHandle !== null) { window.cancelAnimationFrame(ctx.state.flushHandle); ctx.state.flushHandle = null; }
    ctx.flushAnalysisText(true);

    const result = event.payload;
    const bgMeta = (result as any).metadata ?? {};
    const trackingId = typeof bgMeta.tracking_id === 'string' ? bgMeta.tracking_id : null;
    const bgActions = (result.actions ?? []) as ChatActionSummary[];
    const bgPlanId = coercePlanId(bgMeta.plan_id) ?? extractPlanIdFromActions(bgActions) ?? coercePlanId(ctx.mergedMetadata.plan_id) ?? ctx.get().currentPlanId ?? null;
    const bgPlanTitle = coercePlanTitle(bgMeta.plan_title) ?? extractPlanTitleFromActions(bgActions) ?? coercePlanTitle(ctx.mergedMetadata.plan_title) ?? ctx.get().currentPlanTitle ?? null;

    const categoryLabel = BACKGROUND_CATEGORY_LABELS[bgCategory] ?? bgCategory;
    const bgContent = ctx.state.streamedContent.trim()
      ? ctx.state.streamedContent
      : (typeof result.response === 'string' && result.response.trim()
        ? result.response
        : `${categoryLabel} task has been submitted to background execution. Check progress in the right-side "Task Status" panel.\n\nAfter completion, you can ask me to analyze the results.`);

    ctx.get().updateMessage(ctx.assistantMessageId, {
      content: bgContent,
      metadata: {
        ...bgMeta,
        status: 'running' as ChatActionStatus,
        background_category: bgCategory,
        tracking_id: trackingId,
        plan_id: bgPlanId,
        plan_title: bgPlanTitle,
        actions: bgActions,
        action_list: bgActions,
        analysis_text: ctx.state.streamedContent || null,
      },
    });

    // Sync plan context so the sidebar picks up the new plan.
    ctx.set((state: any) => {
      const planIdValue = bgPlanId ?? state.currentPlanId ?? null;
      const planTitleValue = bgPlanTitle ?? state.currentPlanTitle ?? null;
      const updatedSession = state.currentSession
        ? { ...state.currentSession, plan_id: planIdValue, plan_title: planTitleValue }
        : null;
      return {
        currentPlanId: planIdValue,
        currentPlanTitle: planTitleValue,
        currentSession: updatedSession ?? state.currentSession,
        sessions: updatedSession
          ? state.sessions.map((s: any) => (s.id === updatedSession.id ? updatedSession : s))
          : state.sessions,
      };
    });

    // Dispatch plan sync events for the sidebar DAG.
    const sessionAfterBg = ctx.get().currentSession;
    const bgSyncEvents = derivePlanSyncEventsFromActions(bgActions, {
      fallbackPlanId: bgPlanId ?? sessionAfterBg?.plan_id ?? null,
      fallbackPlanTitle: bgPlanTitle ?? sessionAfterBg?.plan_title ?? null,
    });
    if (bgSyncEvents.length > 0) {
      for (const ev of bgSyncEvents) {
        dispatchPlanSyncEvent(ev, {
          trackingId,
          source: 'chat.stream.background',
          status: 'running',
          sessionId: sessionAfterBg?.session_id ?? null,
        });
      }
    } else if (bgPlanId != null) {
      dispatchPlanSyncEvent(
        { type: 'task_changed', plan_id: bgPlanId, plan_title: bgPlanTitle ?? sessionAfterBg?.plan_title ?? null },
        { trackingId, source: 'chat.stream.background', status: 'running', sessionId: sessionAfterBg?.session_id ?? null },
      );
    }

    ctx.state.isBackgroundDispatch = true;
    return true;
  }

  return false;
}

export function handleThinkingStep(ctx: StreamHandlerContext, event: any): void {
  flushPendingThinkingDeltas(ctx);
  const rawStep = event.step ?? {};
  const step = {
    ...rawStep,
    evidence: Array.isArray(rawStep.evidence) ? rawStep.evidence : undefined,
    started_at: typeof rawStep.started_at === 'string' ? rawStep.started_at : undefined,
    finished_at: typeof rawStep.finished_at === 'string' ? rawStep.finished_at : undefined,
    timestamp: typeof rawStep.timestamp === 'string' ? rawStep.timestamp : undefined,
  };
  const targetMessage = ctx.get().messages.find((msg: any) => msg.id === ctx.assistantMessageId);
  if (!targetMessage) return;

  const existingMetadata = { ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}) };
  const currentProcess = targetMessage.thinking_process ?? {
    steps: [],
    status: 'active',
    total_iterations: 0
  };

  const updatedSteps = [...currentProcess.steps];
  // Check if we updating existing step or adding new one
  const stepIndex = updatedSteps.findIndex((s: any) => s.iteration === step.iteration);
  if (stepIndex >= 0) {
    updatedSteps[stepIndex] = step;
  } else {
    updatedSteps.push(step);
  }

  const updatedProcess = {
    ...currentProcess,
    steps: updatedSteps,
    total_iterations: Math.max(currentProcess.total_iterations ?? 0, step.iteration),
    // Update status based on last step
    status: step.status === 'done' ? 'completed' : (step.status === 'error' ? 'error' : 'active') as 'active' | 'completed' | 'error'
  };

  ctx.get().updateMessage(ctx.assistantMessageId, {
    metadata: existingMetadata,
    thinking_process: updatedProcess
  });

  // Allow UI to re-render
  ctx.scheduleFlush();
}

function _flushThinkingDeltaBuffer(ctx: StreamHandlerContext): void {
  const pendingEntries = Object.entries(ctx.state.pendingThinkingDeltas);
  if (pendingEntries.length === 0) return;
  const targetMessage = ctx.get().messages.find((msg: any) => msg.id === ctx.assistantMessageId);
  if (!targetMessage) return;

  const existingMetadata = { ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}) };
  const currentProcess = targetMessage.thinking_process ?? {
    steps: [],
    status: 'active' as const,
    total_iterations: 0,
  };
  const updatedSteps = [...currentProcess.steps];

  for (const [iterationKey, delta] of pendingEntries) {
    const iteration = Number(iterationKey);
    if (!Number.isFinite(iteration)) continue;
    const idx = updatedSteps.findIndex((s: any) => s.iteration === iteration);
    if (idx < 0) {
      updatedSteps.push({
        iteration,
        thought: delta,
        action: null,
        action_result: null,
        status: 'thinking' as const,
        timestamp: new Date().toISOString(),
        self_correction: null,
      });
      continue;
    }
    updatedSteps[idx] = {
      ...updatedSteps[idx],
      thought: (updatedSteps[idx].thought || '') + delta,
    };
  }

  ctx.state.pendingThinkingDeltas = {};
  ctx.get().updateMessage(ctx.assistantMessageId, {
    metadata: existingMetadata,
    thinking_process: {
      ...currentProcess,
      steps: updatedSteps,
      status: 'active' as const,
      total_iterations: Math.max(
        currentProcess.total_iterations ?? 0,
        ...updatedSteps.map((s: any) => Number(s?.iteration) || 0),
      ),
    },
  });
  ctx.scheduleFlush();
}

export function handleThinkingDelta(ctx: StreamHandlerContext, event: any): void {
  const { iteration, delta } = event;
  if (typeof iteration !== 'number' || !Number.isFinite(iteration)) return;
  if (typeof delta !== 'string' || delta.length === 0) return;

  ctx.state.pendingThinkingDeltas[iteration] =
    (ctx.state.pendingThinkingDeltas[iteration] || '') + delta;

  if (ctx.state.thinkingDeltaFlushHandle !== null) return;
  ctx.state.thinkingDeltaFlushHandle = window.setTimeout(() => {
    ctx.state.thinkingDeltaFlushHandle = null;
    _flushThinkingDeltaBuffer(ctx);
  }, 80);
}

/**
 * Handle reasoning_delta events from extended thinking (enable_thinking).
 * Maps reasoning tokens into the thinking_process as iteration 0 with a
 * special "reasoning" status, reusing the existing ThinkingProcess UI.
 */
export function handleReasoningDelta(ctx: StreamHandlerContext, event: any): void {
  const delta = event?.delta;
  if (typeof delta !== 'string' || delta.length === 0) return;

  const REASONING_ITERATION = 0;

  ctx.state.pendingThinkingDeltas[REASONING_ITERATION] =
    (ctx.state.pendingThinkingDeltas[REASONING_ITERATION] || '') + delta;

  if (ctx.state.thinkingDeltaFlushHandle !== null) return;
  ctx.state.thinkingDeltaFlushHandle = window.setTimeout(() => {
    ctx.state.thinkingDeltaFlushHandle = null;
    _flushThinkingDeltaBuffer(ctx);
  }, 80);
}

export function flushPendingThinkingDeltas(ctx: StreamHandlerContext): void {
  if (ctx.state.thinkingDeltaFlushHandle !== null) {
    window.clearTimeout(ctx.state.thinkingDeltaFlushHandle);
    ctx.state.thinkingDeltaFlushHandle = null;
  }
  _flushThinkingDeltaBuffer(ctx);
}

function _extractToolNameFromAction(action: unknown): string | null {
  if (typeof action !== 'string' || !action.trim()) {
    return null;
  }
  try {
    const parsed = JSON.parse(action);
    if (parsed && typeof parsed.tool === 'string' && parsed.tool.trim()) {
      return parsed.tool.trim().toLowerCase();
    }
  } catch {
    // ignore parse errors and fallback to raw text matching
  }
  return action.toLowerCase();
}

function _resolveToolOutputStepIndex(steps: Array<Record<string, any>>, event: any): number {
  const iteration =
    typeof event?.iteration === 'number' && Number.isFinite(event.iteration)
      ? event.iteration
      : null;
  if (iteration !== null) {
    const byIteration = steps.findIndex((step) => step?.iteration === iteration);
    if (byIteration >= 0) return byIteration;
  }

  const toolName =
    typeof event?.tool === 'string' && event.tool.trim()
      ? event.tool.trim().toLowerCase()
      : null;
  if (toolName) {
    for (let idx = steps.length - 1; idx >= 0; idx -= 1) {
      const stepTool = _extractToolNameFromAction(steps[idx]?.action);
      if (stepTool && stepTool.includes(toolName)) {
        return idx;
      }
    }
  }

  for (let idx = steps.length - 1; idx >= 0; idx -= 1) {
    const status = steps[idx]?.status;
    if (status === 'calling_tool' || status === 'analyzing') {
      return idx;
    }
  }
  return steps.length - 1;
}

export function handleControlAck(ctx: StreamHandlerContext, event: any): void {
  const targetMessage = ctx.get().messages.find((msg: any) => msg.id === ctx.assistantMessageId);
  if (!targetMessage) return;
  const existingMetadata: ChatResponseMetadata = {
    ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}),
  };

  const nextMetadata: ChatResponseMetadata = { ...existingMetadata };
  if (typeof event?.job_id === 'string' && event.job_id.trim()) {
    (nextMetadata as any).deep_think_job_id = event.job_id.trim();
  }
  if (typeof event?.available === 'boolean') {
    (nextMetadata as any).deep_think_control_available = event.available;
  }
  if (typeof event?.paused === 'boolean') {
    (nextMetadata as any).deep_think_paused = event.paused;
  }
  if (typeof event?.action === 'string' && event.action.trim()) {
    (nextMetadata as any).deep_think_last_control_action = event.action.trim();
  }

  ctx.get().updateMessage(ctx.assistantMessageId, {
    metadata: nextMetadata,
  });
  ctx.scheduleFlush();
}

export function handleToolOutput(ctx: StreamHandlerContext, event: any): void {
  const line = typeof event?.content === 'string' ? event.content : '';
  if (!line.trim()) return;
  const targetMessage = ctx.get().messages.find((msg: any) => msg.id === ctx.assistantMessageId);
  if (!targetMessage?.thinking_process?.steps?.length) return;

  const stream = typeof event?.stream === 'string' ? event.stream.toLowerCase() : 'stdout';
  const linePrefix = stream === 'stderr' ? '[stderr]' : '[stdout]';
  const lineText = `${linePrefix} ${line}`;

  const currentProcess = targetMessage.thinking_process;
  const updatedSteps = [...currentProcess.steps];
  const stepIndex = _resolveToolOutputStepIndex(updatedSteps as any[], event);
  if (stepIndex < 0 || stepIndex >= updatedSteps.length) return;

  const targetStep = updatedSteps[stepIndex] ?? {};
  const existingResult = typeof targetStep.action_result === 'string' ? targetStep.action_result : '';
  const appended = existingResult ? `${existingResult}\n${lineText}` : lineText;
  const MAX_OUTPUT_CHARS = 12000;
  const normalizedOutput =
    appended.length > MAX_OUTPUT_CHARS
      ? `... [tool output truncated]\n${appended.slice(-MAX_OUTPUT_CHARS)}`
      : appended;

  updatedSteps[stepIndex] = {
    ...targetStep,
    action_result: normalizedOutput,
  };

  ctx.get().updateMessage(ctx.assistantMessageId, {
    thinking_process: {
      ...currentProcess,
      steps: updatedSteps,
    },
  });
  ctx.scheduleFlush();
}

export function processBackgroundDispatch(ctx: StreamHandlerContext): void {
  ctx.set({ isProcessing: false });
  // Persist session metadata so reload picks up context.
  const sessionForBg = ctx.get().currentSession;
  if (sessionForBg) {
    void chatApi.updateSession(sessionForBg.session_id ?? sessionForBg.id, {
      plan_id: ctx.get().currentPlanId ?? null,
      plan_title: ctx.get().currentPlanTitle ?? null,
      is_active: true,
    }).catch((e: any) => console.warn('Failed to sync session:', e));
  }
  try {
    window.dispatchEvent(new CustomEvent('tasksUpdated', {
      detail: { type: 'chat_message_processed', session_id: sessionForBg?.session_id ?? null, plan_id: ctx.get().currentPlanId ?? null },
    }));
  } catch (_) { /* ignore */ }
}

export async function processFinalPayload(ctx: StreamHandlerContext): Promise<void> {
  const result: ChatResponsePayload = ctx.state.finalPayload!;
  const actions = (result.actions ?? []) as ChatActionSummary[];
  const resolvedPlanId = (result.metadata?.plan_id !== undefined ? coercePlanId(result.metadata.plan_id) : undefined) ?? extractPlanIdFromActions(actions) ?? coercePlanId(ctx.mergedMetadata.plan_id) ?? ctx.get().currentPlanId ?? null;
  const resolvedPlanTitle = (result.metadata?.plan_title !== undefined ? coercePlanTitle(result.metadata.plan_title) : undefined) ?? extractPlanTitleFromActions(actions) ?? coercePlanTitle(ctx.mergedMetadata.plan_title) ?? ctx.get().currentPlanTitle ?? null;
  const resolvedTaskId = result.metadata?.task_id ?? ctx.mergedMetadata.task_id ?? ctx.get().currentTaskId ?? null;
  const resolvedTaskName = ctx.mergedMetadata.task_name ?? ctx.get().currentTaskName ?? null;
  const resolvedWorkflowId = result.metadata?.workflow_id ?? ctx.mergedMetadata.workflow_id ?? ctx.get().currentWorkflowId ?? null;
  const initialStatus = isActionStatus(result.metadata?.status) ? (result.metadata?.status as ChatActionStatus) : (actions.length > 0 ? 'pending' : 'completed');

  const assistantMetadata: ChatResponseMetadata = {
    ...(result.metadata ?? {}),
    plan_id: resolvedPlanId ?? null,
    plan_title: resolvedPlanTitle ?? null,
    task_id: resolvedTaskId ?? null,
    workflow_id: resolvedWorkflowId ?? null,
    actions,
    action_list: actions,
    status: initialStatus,
    analysis_text: result.metadata?.analysis_text !== undefined ? (result.metadata?.analysis_text as string | null) : ctx.state.streamedContent || '',
    final_summary: (result.metadata?.final_summary as string | undefined) ?? (result.response ?? ctx.state.streamedContent ?? ''),
  };
  if (initialStatus === 'pending' || initialStatus === 'running') {
    (assistantMetadata as any).unified_stream = true;
    (assistantMetadata as any).plan_message = formatToolPlanPreface(actions);
  }
  const initialToolResults = collectToolResultsFromMetadata(result.metadata?.tool_results);
  if (initialToolResults.length > 0) assistantMetadata.tool_results = initialToolResults;

  ctx.get().updateMessage(ctx.assistantMessageId, {
    content: (assistantMetadata as any).unified_stream === true ? (assistantMetadata.analysis_text?.trim() ? assistantMetadata.analysis_text : (((assistantMetadata as any).plan_message as string) || assistantMetadata.final_summary || '')) : (result.response ?? ctx.state.streamedContent),
    metadata: assistantMetadata,
  });
  const postFinalMessage = ctx.get().messages.find((msg: any) => msg.id === ctx.assistantMessageId);
  if (postFinalMessage?.thinking_process) {
    ctx.get().updateMessage(ctx.assistantMessageId, {
      thinking_process: {
        ...postFinalMessage.thinking_process,
        status: initialStatus === 'failed' ? 'error' : 'completed',
      },
    });
  }
  ctx.set({ isProcessing: false });

  const trackingIdForPoll = typeof assistantMetadata.tracking_id === 'string' ? assistantMetadata.tracking_id : null;
  if ((assistantMetadata as any).unified_stream === true && trackingIdForPoll) {
    ctx.startActionStatusPolling(trackingIdForPoll, ctx.assistantMessageId, assistantMetadata.status as ChatActionStatus, (assistantMetadata.analysis_text as string | undefined) ?? (assistantMetadata.final_summary as string | undefined) ?? null);
  }

  ctx.set((state: any) => {
    const planIdValue = resolvedPlanId ?? state.currentPlanId ?? null;
    const planTitleValue = resolvedPlanTitle ?? state.currentPlanTitle ?? null;
    const taskIdValue = resolvedTaskId ?? state.currentTaskId ?? null;
    const workflowValue = resolvedWorkflowId ?? state.currentWorkflowId ?? null;
    const updatedSession = state.currentSession ? { ...state.currentSession, plan_id: planIdValue, plan_title: planTitleValue, current_task_id: taskIdValue, current_task_name: resolvedTaskName ?? state.currentSession.current_task_name ?? null, workflow_id: workflowValue } : null;
    return {
      currentPlanId: planIdValue,
      currentPlanTitle: planTitleValue,
      currentTaskId: taskIdValue,
      currentTaskName: resolvedTaskName ?? state.currentTaskName ?? null,
      currentWorkflowId: workflowValue,
      currentSession: updatedSession ?? state.currentSession,
      sessions: updatedSession ? state.sessions.map((s: any) => s.id === updatedSession.id ? updatedSession : s) : state.sessions,
    };
  });

  const sessionAfter = ctx.get().currentSession;
  if (sessionAfter) {
    const sessionKey = sessionAfter.session_id ?? sessionAfter.id;
    if (sessionKey && sessionAfter.isUserNamed !== true) {
      const history = autoTitleHistory.get(sessionKey);
      if (!history || history.planId !== sessionAfter.plan_id) {
        if (sessionAfter.plan_id !== null || sessionAfter.messages.some((m: any) => m.type === 'user')) {
          void ctx.get().autotitleSession(sessionKey).catch((e: any) => console.warn('Auto-title failed:', e));
        }
      }
    }
  }

  if (resolvedWorkflowId !== ctx.get().currentWorkflowId) ctx.get().setCurrentWorkflowId(resolvedWorkflowId ?? null);
  if (assistantMetadata.agent_workflow) {
    window.dispatchEvent(new CustomEvent('tasksUpdated', { detail: { type: 'agent_workflow_created', workflow_id: assistantMetadata.workflow_id, total_tasks: assistantMetadata.total_tasks, dag_structure: assistantMetadata.dag_structure, plan_id: resolvedPlanId ?? null } }));
  }
  if (assistantMetadata.session_id) {
    const newSessionId = assistantMetadata.session_id as string;
    ctx.set((state: any) => {
      const current = state.currentSession ? { ...state.currentSession, session_id: newSessionId } : null;
      return { currentSession: current, sessions: state.sessions.map((s: any) => s.id === current?.id ? { ...s, session_id: newSessionId } : s) };
    });
    SessionStorage.setCurrentSessionId(newSessionId);
  }

  const trackingId = typeof assistantMetadata.tracking_id === 'string' ? assistantMetadata.tracking_id : undefined;
  if (trackingId && (assistantMetadata.status === 'pending' || assistantMetadata.status === 'running') && !activeActionFollowups.has(trackingId)) {
    activeActionFollowups.add(trackingId);
    void (async () => {
      try {
        const timeoutMs = 10 * 60_000;
        let lastStatus: ActionStatusResponse | null = await waitForActionCompletionViaStream(trackingId, timeoutMs);
        if (!lastStatus) {
          const start = Date.now();
          while (Date.now() - start < timeoutMs) {
            try {
              const status = await chatApi.getActionStatus(trackingId);
              lastStatus = status;
              const target = ctx.get().messages.find((m: any) => m.id === ctx.assistantMessageId);
              if (target) {
                const meta = (target.metadata as ChatResponseMetadata) ?? {};
                if (status.status !== meta.status) ctx.get().updateMessage(ctx.assistantMessageId, { metadata: { ...meta, status: status.status as ChatActionStatus, tracking_id: trackingId, unified_stream: true } as any });
              }
              if (status.status === 'completed' || status.status === 'failed') break;
            } catch (e) { console.warn('Polling failed:', e); break; }
            await new Promise(r => setTimeout(r, 2500));
          }
        }
        if (lastStatus) {
          const target = ctx.get().messages.find((m: any) => m.id === ctx.assistantMessageId);
          if (target) {
            const meta = (target.metadata as ChatResponseMetadata) ?? {};
            const mergedResults = mergeToolResults(collectToolResultsFromMetadata((lastStatus.result as any)?.tool_results), collectToolResultsFromMetadata((lastStatus.metadata as any)?.tool_results));
            const analysis = (typeof (lastStatus.result as any)?.analysis_text === 'string' ? (lastStatus.result as any).analysis_text : null)?.trim();
            const summary = (typeof (lastStatus.result as any)?.final_summary === 'string' ? (lastStatus.result as any).final_summary : (typeof (lastStatus.metadata as any)?.final_summary === 'string' ? (lastStatus.metadata as any).final_summary : null))?.trim();
            const completionContent = analysis ?? summary ?? (
              lastStatus.status === 'completed'
                ? 'Tool execution completed. Please review the results.'
                : 'Execution failed. Please review the error details.'
            );
            const nextMeta: any = { ...meta, status: lastStatus.status as ChatActionStatus, tracking_id: lastStatus.tracking_id ?? trackingId, plan_id: typeof lastStatus.plan_id === 'number' ? lastStatus.plan_id : (meta.plan_id ?? null), actions: Array.isArray(lastStatus.actions) ? lastStatus.actions : meta.actions, action_list: Array.isArray(lastStatus.actions) ? lastStatus.actions : meta.action_list, errors: Array.isArray(lastStatus.errors) ? lastStatus.errors : meta.errors, unified_stream: true };
            if (analysis) nextMeta.analysis_text = analysis;
            if (summary) nextMeta.final_summary = summary;
            if (mergedResults.length > 0) nextMeta.tool_results = mergedResults; else delete nextMeta.tool_results;
            ctx.get().updateMessage(ctx.assistantMessageId, { content: completionContent, metadata: nextMeta });
            const sessionKey = ctx.get().currentSession?.session_id ?? ctx.get().currentSession?.id ?? null;
            if (sessionKey) void ctx.get().loadChatHistory(sessionKey).catch((e: any) => console.warn('Sync failed:', e));
          }
        }
      } finally { activeActionFollowups.delete(trackingId); }
    })();
  }

  if (!trackingId) {
    const planEvents = derivePlanSyncEventsFromActions(result.actions, { fallbackPlanId: resolvedPlanId ?? ctx.get().currentPlanId ?? null, fallbackPlanTitle: resolvedPlanTitle ?? ctx.get().currentPlanTitle ?? null });
    if (planEvents.length > 0) {
      const sessionForEvent = ctx.get().currentSession;
      for (const ev of planEvents) dispatchPlanSyncEvent(ev, { source: 'chat.sync', sessionId: sessionForEvent?.session_id ?? null });
    }
  }

  try {
    const { currentSession: cs, currentWorkflowId: cw, currentPlanId: pid } = ctx.get();
    window.dispatchEvent(new CustomEvent('tasksUpdated', { detail: { type: 'chat_message_processed', session_id: cs?.session_id ?? null, workflow_id: cw ?? null, plan_id: resolvedPlanId ?? pid ?? null } }));
  } catch (e) { console.warn('Failed to dispatch tasksUpdated:', e); }

  const sessionPatch = ctx.get().currentSession;
  if (!assistantMetadata.tracking_id && sessionPatch) {
    void (async () => {
      try { await chatApi.updateSession(sessionPatch.session_id ?? sessionPatch.id, { plan_id: resolvedPlanId ?? null, plan_title: resolvedPlanTitle ?? null, current_task_id: resolvedTaskId ?? null, current_task_name: resolvedTaskName ?? null, is_active: true }); } catch (e) { console.warn('Sync failed:', e); }
    })();
  }
}
