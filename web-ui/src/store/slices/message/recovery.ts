import { chatApi } from '@api/chat';
import { createSessionAccess } from './sessionState';
import { selectActiveChatRun } from './runResume';
import { findCorrelatedAssistant, type TurnIdentity } from './turnCorrelation';
import { hydratePersistedMessage } from './historyHydration';
import type { StoreGet, StoreSet } from './types';

export interface StreamRecoveryOptions extends TurnIdentity {
  apiSessionId: string;
  localSessionId: string;
  assistantMessageId: string;
  processingKey: string;
  partialContent?: string;
  get: StoreGet;
  set: StoreSet;
  resumeRun: (runId: string) => Promise<void>;
}

export async function recoverAfterStreamFailure(options: StreamRecoveryOptions): Promise<boolean> {
  const { apiSessionId, localSessionId, assistantMessageId, processingKey, runId, clientMessageId, partialContent } = options;
  const source = createSessionAccess(options.get, options.set, localSessionId);
  const keepPending = (note: string) => {
    const previous = source.get().messages.find((message: any) => message.id === assistantMessageId)?.metadata ?? {};
    source.get().updateMessage(assistantMessageId, {
      content: partialContent?.trim() || note,
      metadata: { ...previous, status: 'pending', recovering: true, errors: undefined, chat_run_id: runId ?? previous.chat_run_id },
    });
    options.get().setSessionProcessing(processingKey, true);
  };

  // Run summaries do not expose a request's client_message_id. Without a known
  // run id it is unsafe to attach this turn to an arbitrary active session run.
  if (runId) {
    try {
      const response = await chatApi.getActiveRun(apiSessionId);
      const run = selectActiveChatRun(response.data, runId);
      if (run) {
        keepPending('连接中断，正在自动重连并继续接收结果…');
        options.get().setActiveRunId(processingKey, runId);
        await options.resumeRun(runId);
        return true;
      }
    } catch (error) {
      console.warn('[chat] stream recovery resume failed:', error);
    }
  }

  keepPending('连接中断，正在从服务端同步本轮已完成的结果…');
  for (let attempt = 0; attempt < 10; attempt += 1) {
    await new Promise<void>((resolve) => window.setTimeout(resolve, attempt === 0 ? 800 : Math.min(1200 * attempt, 5000)));
    try {
      const response = await chatApi.getHistory(apiSessionId, { limit: 30 });
      const messages = Array.isArray(response.data?.messages) ? response.data.messages : [];
      const reply = findCorrelatedAssistant(messages, { runId, clientMessageId });
      if (!reply) continue;
      const restored = hydratePersistedMessage({ sessionId: apiSessionId, rawMessage: reply, index: messages.indexOf(reply) });
      const previous = source.get().messages.find((message: any) => message.id === assistantMessageId)?.metadata ?? {};
      const { backend_id: _backendId, ...metadata } = restored.metadata ?? {};
      source.get().updateMessage(assistantMessageId, {
        content: restored.content,
        timestamp: restored.timestamp,
        thinking_process: restored.thinking_process,
        metadata: { ...previous, ...metadata, status: metadata.status ?? 'completed', recovering: false,
          analysis_text: metadata.analysis_text ?? restored.content,
          final_summary: metadata.final_summary ?? restored.content },
      });
      // loadChatHistory updates the owning session entity; navigation during
      // this await must never replace a different chat's current message list.
      try {
        await options.get().loadChatHistory(apiSessionId);
      } catch (error) {
        // The correlated reply is already retained locally. A later history
        // request failing must not discard evidence that this turn completed.
        console.warn('[chat] recovered reply history sync failed:', error);
      }
      options.get().setActiveRunId(processingKey, null);
      options.get().setSessionProcessing(processingKey, false);
      return true;
    } catch (error) {
      console.warn('[chat] stream recovery history poll failed:', error);
    }
  }
  return false;
}
