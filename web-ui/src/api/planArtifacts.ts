import { BaseApi } from './client';
export interface PlanArtifactEntry extends Record<string, unknown> {
  alias: string;
  producer_task_id: number | null;
  session_id?: string | null;
}
export interface DeclaredTaskOutput extends PlanArtifactEntry {
  origin: 'declared_output';
  output_status: 'accepted' | 'partial';
  path: string;
  required_count?: number;
  matched_count?: number | null;
}
export interface PlanArtifactPage {
  plan_id: number;
  session_id: string | null;
  schema_version: number;
  total: number;
  items: PlanArtifactEntry[];
  declared_outputs?: DeclaredTaskOutput[];
  declared_outputs_truncated?: boolean;
  next_cursor: string | null;
  snapshot: string;
}
class PlanArtifactsApi extends BaseApi {
  list = (planId: number, taskId?: number | null, cursor?: string | null): Promise<PlanArtifactPage> =>
    this.get(`/plans/${planId}/artifacts`, { task_id: taskId ?? undefined, cursor: cursor ?? undefined, limit: 50 });
}
export const planArtifactsApi = new PlanArtifactsApi();
