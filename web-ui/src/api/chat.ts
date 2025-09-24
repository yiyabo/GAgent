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
  }): Promise<ChatResponse> => {
    const request: ChatRequest = {
      message,
      mode: context?.mode || 'assistant',
      history: context?.history || [],
      context: {
        task_id: context?.task_id,
        plan_title: context?.plan_title,
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

  // 获取系统状态摘要  
  getSystemStatus = async (): Promise<{
    active_tasks: number;
    pending_plans: number;
    system_health: 'good' | 'warning' | 'critical';
    recent_activity: string[];
  }> => {
    return this.get('/chat/system-status');
  }

  // 执行聊天命令
  executeCommand = async (command: string, params?: Record<string, any>): Promise<{
    success: boolean;
    result: any;
    message: string;
  }> => {
    return this.post('/chat/command', {
      command,
      params,
    });
  }

  // 创建计划从聊天
  createPlanFromChat = async (description: string): Promise<Plan> => {
    return this.post('/chat/create-plan', {
      description,
    });
  }

  // 获取任务建议
  getTaskSuggestions = async (goal: string): Promise<{
    suggested_tasks: Array<{
      name: string;
      description: string;
      estimated_time: string;
      priority: 'high' | 'medium' | 'low';
    }>;
  }> => {
    return this.post('/chat/task-suggestions', {
      goal,
    });
  }

  // 分析用户输入意图
  analyzeIntent = async (message: string): Promise<{
    intent: 'create_plan' | 'view_status' | 'execute_task' | 'general_chat' | 'help';
    confidence: number;
    entities: Record<string, any>;
    suggestions: string[];
  }> => {
    return this.post('/chat/analyze-intent', {
      message,
    });
  }
}

export const chatApi = new ChatApi();
