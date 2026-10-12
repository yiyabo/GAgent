import { BaseApi } from './client';
export interface PlanArtifactEntry extends Record<string, unknown> {
  alias: string;
  producer_task_id: number | null;
  session_id?: string | null;
}
export interface PlanArtifactPage {
  plan_id: number;
  session_id: string | null;
  schema_version: number;
  total: number;
  items: PlanArtifactEntry[];
  next_cursor: string | null;
  snapshot: string;
}
class PlanArtifactsApi extends BaseApi {
  list = (planId: number, taskId?: number | null, cursor?: string | null): Promise<PlanArtifactPage> =>
    this.get(`/plans/${planId}/artifacts`, { task_id: taskId ?? undefined, cursor: cursor ?? undefined, limit: 50 });
}
export const planArtifactsApi = new PlanArtifactsApi();
