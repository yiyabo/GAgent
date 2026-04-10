import type { ChatMessage, ThinkingProcess, ToolResultPayload } from '@/types';
import { parseChatTimestamp } from '@/utils/chatMessageUtils';
import { collectArtifactGallery } from '@/utils/artifactGallery';
import { collectToolResultsFromMetadata } from '@/utils/toolResults';
import { normalizeHydratedThinkingPresentation } from './thinkingPresentation';

const normalizeThinkingProcessStatus = (
  status: any,
): 'active' | 'completed' | 'error' => {
  if (status === 'completed' || status === 'done') return 'completed';
  if (status === 'error' || status === 'failed') return 'error';
  return 'active';
};

const normalizeThinkingStepStatus = (
  status: any,
): 'pending' | 'thinking' | 'calling_tool' | 'analyzing' | 'done' | 'completed' | 'error' => {
  const value = typeof status === 'string' ? status.trim().toLowerCase() : '';
  if (value === 'pending') return 'pending';
  if (value === 'thinking') return 'thinking';
  if (value === 'calling_tool') return 'calling_tool';
  if (value === 'analyzing') return 'analyzing';
  if (value === 'done') return 'done';
  if (value === 'completed') return 'completed';
  if (value === 'error' || value === 'failed') return 'error';
  return 'thinking';
};

export const hydrateThinkingProcess = (raw: any): ThinkingProcess | undefined => {
  let payload = raw;
  if (typeof payload === 'string') {
    try {
      payload = JSON.parse(payload);
    } catch {
      return undefined;
    }
  }
  if (!payload || typeof payload !== 'object') {
    return undefined;
  }
  const rawSteps = Array.isArray((payload as any).steps) ? (payload as any).steps : [];
  const steps = rawSteps
    .filter((step: any) => step && typeof step === 'object')
    .map((step: any, index: number) => {
      const rawIteration = Number(step.iteration);
      const iteration =
        Number.isFinite(rawIteration) && rawIteration > 0 ? rawIteration : index + 1;
      return {
        iteration,
        thought: typeof step.thought === 'string' ? step.thought : '',
        display_text:
          typeof step.display_text === 'string' ? step.display_text : undefined,
        kind:
          step.kind === 'reasoning' || step.kind === 'tool' || step.kind === 'summary'
            ? step.kind
            : undefined,
        action: step.action ?? null,
        action_result: step.action_result ?? null,
        evidence: Array.isArray(step.evidence)
          ? step.evidence
              .filter((ev: any) => ev && typeof ev === 'object')
              .map((ev: any) => ({
                type: typeof ev.type === 'string' ? ev.type : 'output',
                title: typeof ev.title === 'string' ? ev.title : undefined,
                ref: typeof ev.ref === 'string' ? ev.ref : undefined,
                snippet: typeof ev.snippet === 'string' ? ev.snippet : undefined,
              }))
          : undefined,
        status: normalizeThinkingStepStatus(step.status),
        timestamp: typeof step.timestamp === 'string' ? step.timestamp : undefined,
        self_correction: step.self_correction ?? null,
        started_at: typeof step.started_at === 'string' ? step.started_at : undefined,
        finished_at:
          typeof step.finished_at === 'string' ? step.finished_at : undefined,
      };
    });
  const totalIterationsRaw = Number((payload as any).total_iterations);
  const inferredIterations = steps.reduce(
    (max, step) => Math.max(max, step.iteration || 0),
    0,
  );
  const totalIterations =
    Number.isFinite(totalIterationsRaw) && totalIterationsRaw > 0
      ? Math.max(totalIterationsRaw, inferredIterations)
      : inferredIterations;
  return {
    steps,
    status: normalizeThinkingProcessStatus((payload as any).status),
    total_iterations: totalIterations,
    summary:
      typeof (payload as any).summary === 'string'
        ? (payload as any).summary
        : undefined,
    error:
      typeof (payload as any).error === 'string'
        ? (payload as any).error
        : undefined,
  };
};

export const hydratePersistedMessage = ({
  sessionId,
  rawMessage,
  index,
  fallbackToolResults,
}: {
  sessionId: string;
  rawMessage: any;
  index: number;
  fallbackToolResults?: Map<string, ToolResultPayload[]>;
}): ChatMessage => {
  const metadata =
    rawMessage?.metadata && typeof rawMessage.metadata === 'object'
      ? { ...(rawMessage.metadata as Record<string, any>) }
      : {};
  if (typeof rawMessage?.id === 'number') {
    metadata.backend_id = rawMessage.id;
  }

  const trackingId =
    typeof metadata.tracking_id === 'string' ? metadata.tracking_id : null;
  let toolResults = collectToolResultsFromMetadata(metadata.tool_results);
  if (toolResults.length === 0 && trackingId && fallbackToolResults?.has(trackingId)) {
    toolResults = fallbackToolResults.get(trackingId) ?? [];
  }
  if (toolResults.length > 0) {
    metadata.tool_results = toolResults;
  } else if ('tool_results' in metadata) {
    delete (metadata as any).tool_results;
  }

  const artifactGallery = collectArtifactGallery(metadata.artifact_gallery);
  if (artifactGallery.length > 0) {
    metadata.artifact_gallery = artifactGallery;
  } else if ('artifact_gallery' in metadata) {
    delete (metadata as any).artifact_gallery;
  }

  const thinkingProcess = hydrateThinkingProcess(metadata.thinking_process);
  if (thinkingProcess) {
    metadata.thinking_process = thinkingProcess;
    normalizeHydratedThinkingPresentation(metadata, thinkingProcess);
  } else if ('thinking_process' in metadata) {
    delete (metadata as any).thinking_process;
  }

  const backendId = typeof rawMessage?.id === 'number' ? rawMessage.id : null;
  const messageId =
    backendId !== null ? `${sessionId}_${backendId}` : `${sessionId}_${index}`;

  return {
    id: messageId,
    type: (rawMessage?.role || 'assistant') as 'user' | 'assistant' | 'system',
    content: rawMessage?.content ?? '',
    timestamp: parseChatTimestamp(rawMessage?.timestamp),
    metadata,
    thinking_process: thinkingProcess,
  };
};
