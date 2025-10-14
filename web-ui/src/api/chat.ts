import { BaseApi } from './client';
import { Plan } from '../types/index';

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

interface ChatResponse {
  response: string;
  suggestions?: string[];
  actions?: Array<{
    type: string;
    label: string;
    data: any;
  }>;
  metadata?: Record<string, any>;
}

export class ChatApi extends BaseApi {
  // 发送聊天消息并获取AI回复 - 使用真实的LLM API
  sendMessage = async (message: string, context?: {
    task_id?: number;
    plan_title?: string;
    history?: ChatMessage[];
    mode?: 'assistant' | 'planner' | 'analyzer';
    workflow_id?: string;
    session_id?: string;
    metadata?: Record<string, any>;
  }): Promise<ChatResponse> => {
    const request: ChatRequest = {
      message,
      mode: context?.mode || 'assistant',
      history: context?.history || [],
      session_id: context?.session_id, // 🔒 专事专办：将session_id提升为顶级参数
      context: {
        task_id: context?.task_id,
        plan_title: context?.plan_title,
        workflow_id: context?.workflow_id,
        ...context?.metadata, // 🔒 包含metadata信息
      }
    };
    
    return this.post<ChatResponse>('/chat/message', request);
  }

  // 获取聊天建议
  getSuggestions = async (): Promise<{
    quick_actions: string[];
    conversation_starters: string[];
  }> => {
    return this.get('/chat/suggestions');
  }

  // 获取聊天服务状态
  getChatStatus = async (): Promise<{
    status: string;
    provider: string;
    model: string;
    mock_mode: boolean;
    features: Record<string, boolean>;
  }> => {
    return this.get('/chat/status');
  }
}

export const chatApi = new ChatApi();
