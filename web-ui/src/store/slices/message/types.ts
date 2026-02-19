import type { ChatActionStatus, ChatResponsePayload } from '@/types';

export type StoreGet = () => any;
export type StoreSet = (updater: any) => void;

export interface StreamMutableState {
  streamedContent: string;
  lastFlushedContent: string;
  flushHandle: number | null;
  finalPayload: ChatResponsePayload | null;
  jobFinalized: boolean;
  isBackgroundDispatch: boolean;
}

export interface StreamHandlerContext {
  get: StoreGet;
  set: StoreSet;
  assistantMessageId: string;
  mergedMetadata: Record<string, any>;
  currentSession: any;
  state: StreamMutableState;
  startActionStatusPolling: (
    trackingId: string | null | undefined,
    messageId: string,
    initialStatus?: ChatActionStatus,
    initialContent?: string | null,
  ) => void;
  flushAnalysisText: (force?: boolean) => void;
  scheduleFlush: () => void;
}
