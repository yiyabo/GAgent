import type { ChatResponseMetadata, ChatActionStatus, ActionStatusResponse } from '@/types';
import { resolveChatSessionProcessingKey } from '@/utils/chatSessionKeys';
import { ENV } from '@/config/env';
import { chatApi } from '@api/chat';
import { waitForActionCompletionViaStream } from '../../chatUtils';
import {
  mergeToolResults,
  collectToolResultsFromMetadata,
} from '@utils/toolResults';
import {
  collectArtifactGallery,
  mergeArtifactGalleries,
} from '@/utils/artifactGallery';
import type { StoreGet, StoreSet, StreamMutableState } from './types';

export function startActionStatusPolling(
  get: StoreGet,
  trackingId: string | null | undefined,
  messageId: string,
  initialStatus?: ChatActionStatus,
  initialContent?: string | null,
) {
  if (!trackingId) return;
  const pollOnce = async (): Promise<boolean> => {
  try {
  const resp = await fetch(`${ENV.API_BASE_URL}/chat/actions/${trackingId}`);
  if (!resp.ok) return false;
  const statusResp = (await resp.json()) as ActionStatusResponse;
  const status = statusResp.status;
  const done = status === 'completed' || status === 'failed';
  const remoteToolResults = mergeToolResults(collectToolResultsFromMetadata(statusResp.result?.tool_results), collectToolResultsFromMetadata(statusResp.metadata?.tool_results));
  const remoteAnalysis = typeof statusResp.result?.analysis_text === 'string' ? statusResp.result.analysis_text : (typeof statusResp.metadata?.analysis_text === 'string' ? statusResp.metadata.analysis_text : undefined);
  const remoteFinalSummary = typeof statusResp.result?.final_summary === 'string' ? statusResp.result.final_summary : (typeof statusResp.metadata?.final_summary === 'string' ? statusResp.metadata.final_summary : undefined);
  const remoteReply = typeof statusResp.result?.reply === 'string' ? statusResp.result.reply : undefined;
  const targetMessage = get().messages.find((msg: any) => msg.id === messageId);
  if (!targetMessage) return done;
  const currentMeta: ChatResponseMetadata = { ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}) };
  const remoteArtifactGallery = mergeArtifactGalleries(
    collectArtifactGallery(currentMeta.artifact_gallery),
    mergeArtifactGalleries(
      collectArtifactGallery(statusResp.result?.artifact_gallery),
      collectArtifactGallery(statusResp.metadata?.artifact_gallery),
    ),
  );
  const contentCandidate = (remoteAnalysis && remoteAnalysis.trim()) || (remoteFinalSummary && remoteFinalSummary.trim()) || (remoteReply && remoteReply.trim()) || targetMessage.content || initialContent || '';
  get().updateMessage(messageId, { content: contentCandidate, metadata: { ...currentMeta, status, analysis_text: remoteAnalysis ?? currentMeta.analysis_text, final_summary: remoteFinalSummary ?? currentMeta.final_summary, tool_results: remoteToolResults.length > 0 ? remoteToolResults : currentMeta.tool_results, artifact_gallery: remoteArtifactGallery.length > 0 ? remoteArtifactGallery : currentMeta.artifact_gallery } });
  if (done) {
  const sessionKey = get().currentSession?.session_id ?? get().currentSession?.id ?? null;
  if (sessionKey) void get().loadChatHistory(sessionKey).catch((e: any) => console.warn('failed:', e));
  }
  return done;
  } catch (pollError) {
  console.warn('statusfailed:', pollError);
  return false;
  }
  };
  if (initialStatus === 'completed' || initialStatus === 'failed') {
  void pollOnce();
  return;
  }
  void (async () => {
  const start = Date.now();
  const timeoutMs = 90_000;
  const intervalMs = 2_500;
  while (Date.now() - start < timeoutMs) {
  const done = await pollOnce();
  if (done) break;
  await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  })();
}

export function flushAnalysisText(
  get: StoreGet,
  assistantMessageId: string,
  state: StreamMutableState,
  force: boolean = false,
) {
  if (!force && state.streamedContent === state.lastFlushedContent) return;
  const targetMessage = get().messages.find((msg: any) => msg.id === assistantMessageId);
  if (!targetMessage) return;
  const existingMetadata: ChatResponseMetadata = { ...((targetMessage.metadata as ChatResponseMetadata | undefined) ?? {}) };
  get().updateMessage(assistantMessageId, { metadata: { ...existingMetadata, analysis_text: state.streamedContent } });
  state.lastFlushedContent = state.streamedContent;
}

export function scheduleFlush(
  state: StreamMutableState,
  doFlush: () => void,
) {
  if (state.flushHandle !== null) return;
  state.flushHandle = window.requestAnimationFrame(() => { state.flushHandle = null; doFlush(); });
}

export async function retryActionRun(
  get: StoreGet,
  set: StoreSet,
  oldTrackingId: string,
  rawActionsOverride: any[] = [],
) {
  const retrySessionKey = resolveChatSessionProcessingKey(get().currentSession);
  if (!oldTrackingId || get().processingSessionIds.has(retrySessionKey)) return;
  try {
  get().setSessionProcessing(retrySessionKey, true);
  const retryStatus = await chatApi.retryActionRun(oldTrackingId);
  const newTrackingId = retryStatus.tracking_id;
  const rawActions = Array.isArray(retryStatus.actions) ? retryStatus.actions.map((a: any, idx: number) => ({ kind: a.kind, name: a.name, parameters: a.parameters, order: a.order ?? idx + 1, blocking: a.blocking ?? true })) : rawActionsOverride;
  const pendingId = `msg_${Date.now()}_assistant_retry`;
  get().addMessage({ id: pendingId, type: 'assistant', content: 'in progressexecute…', timestamp: new Date(), metadata: { status: 'pending', unified_stream: true, plan_message: 'in progressexecute…', tracking_id: newTrackingId, plan_id: retryStatus.plan_id ?? null, raw_actions: rawActions, retry_of: oldTrackingId } });
  const lastStatus = await waitForActionCompletionViaStream(newTrackingId, 120_000) || await (async () => {
  const start = Date.now();
  while (Date.now() - start < 120_000) {
  try {
  const s = await chatApi.getActionStatus(newTrackingId);
  if (s.status === 'completed' || s.status === 'failed') return s;
  } catch (e) { break; }
  await new Promise(r => setTimeout(r, 2500));
  }
  return null;
  })();
  if (lastStatus) {
  const summary = (typeof lastStatus.result?.final_summary === 'string' ? lastStatus.result.final_summary : (typeof lastStatus.metadata?.final_summary === 'string' ? lastStatus.metadata.final_summary : null))?.trim();
  const content =
  summary ??
  (lastStatus.status === 'completed'
  ? 'Execution completed. Please review the generated results.'
  : 'Execution failed. Please review error details.');
  const results = mergeToolResults(collectToolResultsFromMetadata(lastStatus.result?.tool_results), collectToolResultsFromMetadata(lastStatus.metadata?.tool_results));
  const artifactGallery = mergeArtifactGalleries(
  collectArtifactGallery(lastStatus.result?.artifact_gallery),
  collectArtifactGallery(lastStatus.metadata?.artifact_gallery),
  );
  get().updateMessage(pendingId, { content, metadata: { status: lastStatus.status as ChatActionStatus, unified_stream: true, actions: lastStatus.actions ?? [], tool_results: results.length > 0 ? results : undefined, artifact_gallery: artifactGallery.length > 0 ? artifactGallery : undefined, errors: lastStatus.errors ?? undefined } as any });
  }
  } catch (error) {
  console.error('Retry failed:', error);
  const lastUser = [...get().messages].reverse().find((msg: any) => msg.type === 'user');
  if (lastUser) await get().sendMessage(lastUser.content, lastUser.metadata);
  } finally {
  get().setSessionProcessing(retrySessionKey, false);
  }
}
