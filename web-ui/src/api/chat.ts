import { BaseApi } from './client';
import type {
  ActionStatusResponse,
  ChatResponsePayload,
  ChatSessionsResponse,
  ChatSessionSummary,
  ChatStatusResponse,
  ChatSessionUpdatePayload,
  ChatSessionAutoTitleResult,
  ChatSessionAutoTitleBulkResponse,
} from '@/types';

interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp?: string;
}

interface ChatRequest {
  message: string;
  history?: ChatMessage[];
  context?: Record<string, any>;
  mode?: 'assistant' | 'planner' | 'analyzer';
  session_id?: string;
  project_id?: number;
  user_id?: number;
}

export interface ModelEntry {
  id: string;
  name: string;
  provider: string;
  description?: string | null;
  available: boolean;
}

export interface ProviderEntry {
  id: string;
  name: string;
  available: boolean;
}

export interface AvailableModelsResponse {
  providers: ProviderEntry[];
  models: ModelEntry[];
  current_provider: string;
  current_model: string;
}

export class ChatApi extends BaseApi {
  sendMessage = async (message: string, context?: {
  task_id?: number;
  plan_id?: number | null;
  plan_title?: string;
  history?: ChatMessage[];
  mode?: 'assistant' | 'planner' | 'analyzer';
  workflow_id?: string;
  session_id?: string;
  metadata?: Record<string, any>;
  project_id?: number;
  user_id?: number;
  }): Promise<ChatResponsePayload> => {
  const request: ChatRequest = {
  message,
  mode: context?.mode || 'assistant',
  history: context?.history || [],
  session_id: context?.session_id,
  project_id: context?.project_id,
  user_id: context?.user_id,
  context: {
  plan_id: context?.plan_id,
  task_id: context?.task_id,
  plan_title: context?.plan_title,
  workflow_id: context?.workflow_id,
  project_id: context?.project_id,
  }
  };

  if (context?.metadata) {
  request.context = {
  ...request.context,
  ...context.metadata,
  };
  }
  
  return this.post<ChatResponsePayload>('/chat/message', request);
  }

  getChatStatus = async (): Promise<ChatStatusResponse> => {
  return this.get('/chat/status');
  };

  getAvailableModels = async (): Promise<AvailableModelsResponse> => {
  return this.get('/api/models');
  };

  getSessions = async (params?: {
  limit?: number;
  offset?: number;
  active?: boolean;
  project_id?: number;
  }): Promise<ChatSessionsResponse> => {
  return this.get('/chat/sessions', params);
  };

  updateSession = async (
  sessionId: string,
  payload: ChatSessionUpdatePayload
  ): Promise<ChatSessionSummary> => {
  return this.patch(`/chat/sessions/${sessionId}`, payload);
  };

  deleteSession = async (
  sessionId: string,
  options?: { archive?: boolean }
  ): Promise<void> => {
  await this.delete<void>(
  `/chat/sessions/${sessionId}`,
  options?.archive ? { archive: options.archive } : undefined
  );
  };

  getActionStatus = async (trackingId: string): Promise<ActionStatusResponse> => {
  return this.get(`/chat/actions/${trackingId}`);
  };

  retryActionRun = async (trackingId: string): Promise<ActionStatusResponse> => {
  return this.post(`/chat/actions/${trackingId}/retry`, {});
  };

  autotitleSession = async (
  sessionId: string,
  payload?: { force?: boolean; strategy?: string | null }
  ): Promise<ChatSessionAutoTitleResult> => {
  return this.post(`/chat/sessions/${sessionId}/autotitle`, payload ?? {});
  };

  bulkAutotitleSessions = async (
  payload?: { session_ids?: string[]; force?: boolean; strategy?: string | null; limit?: number }
  ): Promise<ChatSessionAutoTitleBulkResponse> => {
  return this.post('/chat/sessions/autotitle/bulk', payload ?? {});
  };

  steerRun = async (
  runId: string,
  message: string,
  sessionId?: string,
  ): Promise<{ run_id: string; status: string }> => {
  return this.post<{ run_id: string; status: string }>(`/chat/runs/${runId}/steer`, { message, session_id: sessionId });
  };

  /** Request cancellation of a background chat run (sets cancel_event; may take effect after current await). */
  cancelRun = async (runId: string): Promise<{ run_id: string; status: string }> => {
    return this.post<{ run_id: string; status: string }>(`/chat/runs/${encodeURIComponent(runId)}/cancel`, {});
  };

  getHistory = async (sessionId: string, params?: Record<string, any>) => {
    return this.client.get(`/chat/history/${sessionId}`, { params });
  };

  getActiveRun = async (sessionId: string) => {
    return this.client.get(
      `/chat/sessions/${encodeURIComponent(sessionId)}/runs`,
      { params: { limit: 5 } }
    );
  };
}

export const chatApi = new ChatApi();
