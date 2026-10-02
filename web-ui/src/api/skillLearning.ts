import { BaseApi } from './client';
export interface LearnedSkill {
  id: string; current_version: number; state: 'candidate'|'trial'|'stable'|'suspended'|'disabled';
  version_stats?:{exposures:number;body_deliveries:number;independent_passed_materials:number;co_used_run_tokens?:number|null;outcomes:Record<string,number>};
  similar_skills?:Array<{id:string;name:string;version:number}>;
  review_status: string; reason?: string; public_name: string; source_run_id?: string;
  draft: { name:string; description:string; domain:string; when_to_use:string; inputs:string[];
    steps:Array<{instruction:string;evidence_ids:string[]}>;verification:string[];limitations:string[];pitfalls:string[];keywords:string[] };
  evidence: {source_outputs_verified:boolean;run_status:string;validated_dimensions:string[];requires_human_review?:boolean;input_basis?:string;steps?:unknown[]};
  usage?:Array<{run_id:string;status:string;version:number;evidence_json?:string;feedback_rating?:string}>;
}
export interface LearningRunInfo {
  run_id:string;session_id:string;run_status:string;
  job?: {status:string;error_code?:string};skills:LearnedSkill[];feedback?:{rating:string;comment:string};
}
class SkillLearningApi extends BaseApi {
  getRun = (runId:string,sessionId:string):Promise<LearningRunInfo> => this.get(`/skill-learning/runs/${encodeURIComponent(runId)}`,{session_id:sessionId});
  capture = (runId:string,sessionId:string) => this.post(`/skill-learning/runs/${encodeURIComponent(runId)}/capture`,{session_id:sessionId});
  feedback = (runId:string,sessionId:string,rating:'useful'|'needs_work',comment='') => this.post(`/skill-learning/runs/${encodeURIComponent(runId)}/feedback`,{session_id:sessionId,rating,comment});
  list = (sessionId:string,query=""):Promise<{skills:LearnedSkill[];recommended_skills?:LearnedSkill[];recommendations?:Array<{id:string;reason:string}>}> => this.get(`/skill-learning/sessions/${encodeURIComponent(sessionId)}`,{query});
  detail = (id:string,sessionId:string):Promise<LearnedSkill> => this.get(`/skill-learning/skills/${encodeURIComponent(id)}`,{session_id:sessionId});
  review = (skill:LearnedSkill,sessionId:string,decision:'accept'|'reject'|'disable'):Promise<LearnedSkill> => this.post(`/skill-learning/skills/${skill.id}/review`,{session_id:sessionId,version:skill.current_version,decision});
  edit = (skill:LearnedSkill,sessionId:string,draft:LearnedSkill['draft']):Promise<LearnedSkill> => this.put(`/skill-learning/skills/${skill.id}`,{session_id:sessionId,version:skill.current_version,draft});
}
export const skillLearningApi = new SkillLearningApi();
