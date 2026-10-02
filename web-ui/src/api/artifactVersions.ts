import { BaseApi } from './client';
export interface ArtifactVersion {
  artifact_version_id:string; alias:string; path:string; created_at:number;
  freshness?:"fresh"|"stale"|"unknown"|"reconciling";
  producer_task_id?:number; validated:boolean; origin:string;
  binding?:{instruction?:string;inputs?:Record<string,string>}|null;
}
export interface ArtifactVersions {
  schema_version:number;manifest_revision:number;versions:ArtifactVersion[];
  current_artifacts:Record<string,ArtifactVersion>;next_cursor?:number|null;
}
export interface RecomputePreview {
  manifest_revision:number;preview_fingerprint:string;ordered_task_ids:number[];
  affected_task_ids:number[];blocked_task_ids:number[];
}
class ArtifactVersionsApi extends BaseApi {
  list=(planId:number,alias?:string,cursor=0):Promise<ArtifactVersions>=>this.get(`/plans/${planId}/artifact-versions`,{alias,cursor});
  preview=(planId:number,payload:Record<string,unknown>):Promise<RecomputePreview>=>this.post(`/plans/${planId}/recompute-preview`,payload);
  execute=(planId:number,payload:Record<string,unknown>):Promise<{job_id:string}>=>this.post(`/plans/${planId}/recompute`,payload);
}
export const artifactVersionsApi=new ArtifactVersionsApi();
