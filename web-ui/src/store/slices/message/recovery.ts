import { chatApi } from '@api/chat';
import { createSessionAccess } from './sessionState';
import { selectActiveChatRun } from './runResume';
import { findCorrelatedAssistant, type TurnIdentity } from './turnCorrelation';
import { hydratePersistedMessage } from './historyHydration';
import type { StoreGet, StoreSet } from './types';
import { translate } from '@/i18n/useT';

export interface StreamRecoveryOptions extends TurnIdentity {
  apiSessionId: string;
  localSessionId: string;
  assistantMessageId: string;
  processingKey: string;
  partialContent?: string;
  get: StoreGet;
  set: StoreSet;
  resumeRun: (runId: string) => Promise<void>;
  /** Dead-but-resumable run → checkpoint resume (the "继续此任务" click,
   * done automatically). Optional: when absent, stage 3 is skipped and the
   * manual button remains the only path. */
  autoResumeFromCheckpoint?: (runId: string) => Promise<boolean>;
}

// Auto-resume loop guards (per browser session): a source run is auto-resumed
// at most once, and the session caps total auto-resumes to bound token burn
// when the backend keeps dying mid-run (2026-10-08 GVHD case: the container
// restarted mid-run, recovery gave up in ~30s, the user had to click resume
// manually — the checkpoint resume itself worked perfectly).
const AUTO_RESUME_WINDOW_MS = 4 * 60 * 1000;
const MAX_AUTO_RESUMES_PER_SESSION = 5;
const autoResumedRunIds = new Set<string>();
let autoResumeCount = 0;

export function resetAutoResumeGuardsForTest(): void {
  autoResumedRunIds.clear();
  autoResumeCount = 0;
}

/**
 * The interim text this module writes while a turn recovers. The note is stored in the message's
 * metadata next to its content, so "is this a status note or a real partial reply" is an equality
 * check that holds in any UI language and stops matching as soon as real text replaces the note.
 */
export const isRecoveryNote = (content: unknown, metadata?: Record<string, any> | null): boolean => {
  const text = typeof content === 'string' ? content.trim() : '';
  const note = metadata?.recovery_note_text;
  return Boolean(text) && typeof note === 'string' && note.trim() === text;
};

export async function recoverAfterStreamFailure(options: StreamRecoveryOptions): Promise<boolean> {
  const { apiSessionId, localSessionId, assistantMessageId, processingKey, runId, clientMessageId, partialContent } = options;
  const source = createSessionAccess(options.get, options.set, localSessionId);
  const keepPending = (note: string) => {
    const previous = source.get().messages.find((message: any) => message.id === assistantMessageId)?.metadata ?? {};
    const partial = isRecoveryNote(partialContent, previous) ? '' : partialContent?.trim() ?? '';
    source.get().updateMessage(assistantMessageId, {
      content: partial || note,
      metadata: {
        ...previous, status: 'pending', recovering: true, errors: undefined,
        chat_run_id: runId ?? previous.chat_run_id,
        recovery_note_text: partial ? undefined : note,
      },
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
        keepPending(translate('chat.recovery.reconnecting'));
        options.get().setActiveRunId(processingKey, runId);
        await options.resumeRun(runId);
        return true;
      }
    } catch (error) {
      console.warn('[chat] stream recovery resume failed:', error);
    }
  }

  keepPending(translate('chat.recovery.syncing'));
  const restoreFromHistory = async (): Promise<boolean> => {
    const response = await chatApi.getHistory(apiSessionId, { limit: 30 });
    const messages = Array.isArray(response.data?.messages) ? response.data.messages : [];
    const reply = findCorrelatedAssistant(messages, { runId, clientMessageId });
    if (!reply) return false;
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
  };

  for (let attempt = 0; attempt < 10; attempt += 1) {
    await new Promise<void>((resolve) => window.setTimeout(resolve, attempt === 0 ? 800 : Math.min(1200 * attempt, 5000)));
    try {
      if (await restoreFromHistory()) return true;
    } catch (error) {
      console.warn('[chat] stream recovery history poll failed:', error);
    }
  }

  // Stage 3: the backend itself may be restarting (observed 3+ minutes). Wait
  // patiently, then resume from the run checkpoint automatically instead of
  // dropping the user onto a manual "继续此任务" button.
  if (
    runId &&
    options.autoResumeFromCheckpoint &&
    !autoResumedRunIds.has(runId) &&
    autoResumeCount < MAX_AUTO_RESUMES_PER_SESSION
  ) {
    keepPending(translate('chat.recovery.waiting'));
    const deadline = Date.now() + AUTO_RESUME_WINDOW_MS;
    let delay = 2000;
    while (Date.now() < deadline) {
      await new Promise<void>((resolve) => window.setTimeout(resolve, delay));
      delay = Math.min(Math.round(delay * 1.5), 10000);
      try {
        const active = await chatApi.getActiveRun(apiSessionId);
        const run = selectActiveChatRun(active.data, runId);
        if (run) {
          keepPending(translate('chat.recovery.reconnecting'));
          options.get().setActiveRunId(processingKey, runId);
          await options.resumeRun(runId);
          return true;
        }
        if (await restoreFromHistory()) return true;
        const info = await chatApi.getResumeInfo(runId, apiSessionId);
        if (!info.can_resume || info.automatic_resume_allowed === false) return false;
        autoResumedRunIds.add(runId);
        autoResumeCount += 1;
        keepPending(translate('chat.recovery.serverBack'));
        const ok = await options.autoResumeFromCheckpoint(runId);
        if (ok) return true;
        autoResumedRunIds.delete(runId);
        autoResumeCount -= 1;
        return false;
      } catch (error) {
        // A 4xx means the backend is up and has a definitive answer (unknown
        // run, forbidden) — stop waiting. Anything else reads as "still
        // restarting" and stays inside the patience window.
        const status = (error as any)?.response?.status;
        if (typeof status === 'number' && status >= 400 && status < 500) {
          return false;
        }
        console.warn('[chat] auto-resume poll failed (backend restarting?):', error);
      }
    }
  }
  return false;
}
