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
  BaseModelOption,
  WebSearchProvider,
  LLMProviderOption,
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
  session_id?: string; // 🔒 : sessionparameter
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
  default_search_provider?: WebSearchProvider | null;
  default_base_model?: BaseModelOption | null;
  default_llm_provider?: LLMProviderOption | null;
  }): Promise<ChatResponsePayload> => {
  const request: ChatRequest = {
  message,
  mode: context?.mode || 'assistant',
  history: context?.history || [],
  session_id: context?.session_id, // 🔒 : session_idparameter
  context: {
  plan_id: context?.plan_id,
  task_id: context?.task_id,
  plan_title: context?.plan_title,
  workflow_id: context?.workflow_id,
  default_search_provider:
  context?.default_search_provider ??
  context?.metadata?.default_search_provider,
  default_base_model:
  context?.default_base_model ??
  context?.metadata?.default_base_model,
  default_llm_provider:
  context?.default_llm_provider ??
  context?.metadata?.default_llm_provider,
  }
  };

  if (context?.metadata) {
  const {
  default_search_provider: _ignoredProvider,
  default_base_model: _ignoredBaseModel,
  default_llm_provider: _ignoredLLMProvider,
  ...restMetadata
  } = context.metadata;
  request.context = {
  ...request.context,
  ...restMetadata,
  };
  }
  
  return this.post<ChatResponsePayload>('/chat/message', request);
  }

  getChatStatus = async (): Promise<ChatStatusResponse> => {
  return this.get('/chat/status');
  };

  getSessions = async (params?: {
  limit?: number;
  offset?: number;
  active?: boolean;
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
}

export const chatApi = new ChatApi();
