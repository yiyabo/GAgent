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
  WebSearchProvider,
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
  session_id?: string; // 🔒 专事专办：会话隔离参数
}

export class ChatApi extends BaseApi {
  // 发送聊天消息并获取AI回复 - 使用真实的LLM API
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
  }): Promise<ChatResponsePayload> => {
    const request: ChatRequest = {
      message,
      mode: context?.mode || 'assistant',
      history: context?.history || [],
      session_id: context?.session_id, // 🔒 专事专办：将session_id提升为顶级参数
      context: {
        plan_id: context?.plan_id,
        task_id: context?.task_id,
        plan_title: context?.plan_title,
        workflow_id: context?.workflow_id,
        default_search_provider:
          context?.default_search_provider ??
          context?.metadata?.default_search_provider,
      }
    };

    if (context?.metadata) {
      const { default_search_provider: _ignored, ...restMetadata } = context.metadata;
      request.context = {
        ...request.context,
        ...restMetadata,
      };
    }
    
    return this.post<ChatResponsePayload>('/chat/message', request);
  }

  // 获取聊天服务状态
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
}

export const chatApi = new ChatApi();
