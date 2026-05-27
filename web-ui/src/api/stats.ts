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

class StatsApi extends BaseApi {
  async getTokenUsage(hours: number = 24): Promise<TokenUsageResponse> {
    return this.get<TokenUsageResponse>('/system/stats/token-usage', { hours });
  }
}

export const statsApi = new StatsApi();
