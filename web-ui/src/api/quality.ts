import { BaseApi } from './client';

export type SatisfactionLevel = 'satisfied' | 'acceptable' | 'negative' | 'angry';

export interface QualityBreakdownItem {
  name: string;
  count: number;
}

export interface QualitySummary {
  total: number;
  pending: number;
  evaluated: number;
  average_confidence: number;
  by_satisfaction_level: QualityBreakdownItem[];
  failure_modes: QualityBreakdownItem[];
  responsible_stages: QualityBreakdownItem[];
  request_tiers: QualityBreakdownItem[];
  tools: QualityBreakdownItem[];
}

export interface QualityEvidence {
  source: string;
  quote: string;
  explanation: string;
}

export interface QualityCase {
  id: number;
  target_run_id: string;
  session_id: string;
  status: string;
  evaluation_basis?: string | null;
  satisfaction_level?: SatisfactionLevel | null;
  confidence?: number | null;
  failure_modes: string[];
  responsible_stages: string[];
  evidence: QualityEvidence[];
  user_goal: string;
  created_at?: string | null;
  evaluated_at?: string | null;
}

class QualityApi extends BaseApi {
  getSummary(hours = 168): Promise<QualitySummary> {
    return this.get<QualitySummary>('/quality/summary', { hours });
  }

  getCases(params?: {
    hours?: number;
    limit?: number;
    offset?: number;
    status?: string;
    satisfaction_level?: SatisfactionLevel;
    failure_mode?: string;
  }): Promise<QualityCase[]> {
    return this.get<QualityCase[]>('/quality/cases', params);
  }
}

export const qualityApi = new QualityApi();
