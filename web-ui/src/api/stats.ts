import { BaseApi } from './client';

export interface ModelUsage {
  model: string;
  call_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface TokenUsageResponse {
  period_hours: number;
  call_count: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  by_model: ModelUsage[];
}

export interface TaskTokenUsageItem {
  task_id: number;
  call_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost: number;
}

export interface PlanTasksTokenUsageResponse {
  plan_id: number;
  tasks: TaskTokenUsageItem[];
  total_tokens: number;
  estimated_cost: number;
}

class StatsApi extends BaseApi {
  async getTokenUsage(hours: number = 24): Promise<TokenUsageResponse> {
    return this.get<TokenUsageResponse>('/system/stats/token-usage', { hours });
  }

  async getSessionTokenUsage(sessionId: string): Promise<TokenUsageResponse> {
    return this.get<TokenUsageResponse>(`/system/stats/token-usage/session/${sessionId}`);
  }

  async getPlanTaskTokenUsage(planId: number): Promise<PlanTasksTokenUsageResponse> {
    return this.get<PlanTasksTokenUsageResponse>(`/system/stats/token-usage/plan/${planId}`);
  }
}

export const statsApi = new StatsApi();
