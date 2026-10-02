"""Proposals, human method review and evidence-bound independent reuse."""
from __future__ import annotations
import asyncio
import logging
import json
from app.repository import skill_learning as repository
from app.repository.chat_runs import get_chat_run
from app.repository.context_recall import session_scope
from app.services.foundation.settings import get_settings
from .distiller import SkillDistiller
from .evidence import build_evidence,validate_draft,requires_review
from .models import SkillDraft

logger=logging.getLogger(__name__)


class SkillLearningService:
    def __init__(self,distiller=None):self.distiller=distiller or SkillDistiller()

    def capture(self,run_id,*,forced=False):
        run=get_chat_run(run_id)
        if not run or run['status'] not in {'succeeded','failed','cancelled'}:raise ValueError('task has not finished')
        if not forced and not get_settings().skill_learning_enabled:return
        scope=session_scope(run['session_id'])
        if not scope or scope['owner_id']!=run['owner_id']:raise ValueError('source session binding unavailable')
        if not forced:
            if not get_settings().skill_learning_enabled:return
            from app.repository.run_steps import list_steps
            useful=[step for step in list_steps(run_id) if step.status=='succeeded' and step.tool_name not in {'load_skill','load_tool_schema','get_bound_plan','bind_plan','check_task_status'}]
            if len(useful)<3:return
        stored=repository.get_run_context(run_id)
        if stored:
            scope={**scope,'project_id':stored['project_id']}
            if not forced and json.loads(stored['selected_json']):return  # reuse is evaluated, not cloned into a near-duplicate
        repository.enqueue(run,scope,forced)

    async def process_one(self,run_id):
        row=await asyncio.to_thread(repository.claim_job,run_id)
        if not row:return False
        try:
            evidence=await asyncio.to_thread(build_evidence,run_id)
            if not evidence:raise ValueError('execution evidence unavailable')
            evidence['project_id']=row['project_id']  # frozen capture scope, not later session rebind
            if not row['forced'] and (len(evidence['steps'])<3 or (evidence['feedback'] or {}).get('rating')=='needs_work'):
                repository.finish_job(run_id,row['claim'],status='skipped',error_code='insufficient_positive_evidence');return False
            if not repository.reserve_model_call(run_id,row['claim'],get_settings().skill_learning_calls_per_hour):
                repository.finish_job(run_id,row['claim'],status='pending',error_code='hourly_budget');return False
            result=await self.distiller.distill(evidence)
            if not result.reusable:
                repository.finish_job(run_id,row['claim'],status='skipped',error_code='not_reusable');return False
            if result.draft is None:raise ValueError('missing reusable draft')
            validate_draft(result.draft,evidence)
            evidence['requires_human_review']=requires_review(evidence,result.draft.domain)
            skill_id=repository.create_candidate(row,result.draft.model_dump(),result.draft.fingerprint(),evidence)
            if skill_id and (repository.feedback(run_id) or {}).get('rating')=='needs_work':
                repository.set_state(skill_id,1,'suspended','negative_user_feedback',actor='user')
            return bool(skill_id)
        except asyncio.CancelledError:
            repository.finish_job(run_id,row['claim'],status='pending',error_code='worker_stopped');raise
        except Exception as exc:
            status='failed' if row['attempts']>=3 else 'pending'
            repository.finish_job(run_id,row['claim'],status=status,error_code=type(exc).__name__)
            logger.warning('Skill distillation failed run=%s error=%s',run_id,type(exc).__name__);return False

    def review(self,skill_id,version,decision):
        skill=repository.get_skill(skill_id)
        if not skill or skill['current_version']!=version:raise ValueError('skill version changed')
        if decision=='disable':repository.set_state(skill_id,version,'disabled','user_disabled',actor='user');return
        if decision=='reject':repository.set_state(skill_id,version,'suspended','method_needs_revision',actor='user',review_status='rejected');return
        repository.set_state(skill_id,version,'candidate','method_reviewed_requires_independent_use',actor='user',review_status='accepted')
        self.recompute(skill_id,version)

    def edit(self,skill_id,version,draft:SkillDraft):
        skill=repository.get_skill(skill_id)
        if not skill or skill['current_version']!=version:raise ValueError('skill version changed')
        validate_draft(draft,skill['evidence'])
        if not repository.edit_skill(skill_id,version,draft.model_dump(),draft.fingerprint()):raise ValueError('skill version changed')

    def complete_usages(self,run_id):
        usages=repository.pending_usages(run_id)
        if not usages:return
        evidence=build_evidence(run_id)
        if not evidence:return
        for usage in usages:
            skill=repository.get_skill(usage['skill_id'],usage['version'])
            if not skill:continue
            source=skill['evidence']
            independent=bool(evidence['input_digest'] and source.get('input_digest') and evidence['input_digest']!=source['input_digest'] and run_id!=skill['source_run_id'] and not evidence['resumed'])
            required={step['tool'] for step in source['steps'] if step['id'] in {identity for item in skill['draft']['steps'] for identity in item['evidence_ids']}}
            observed={step['tool'] for step in evidence['steps']}
            matching=bool(source.get('contract_hash') and source['contract_hash']==evidence.get('contract_hash'))
            passed=independent and matching and evidence['source_outputs_verified'] and required.issubset(observed)
            if evidence['run_status']!='succeeded' or evidence['hard_failure']:
                status='failed' if required.intersection(observed) and evidence['hard_failure'] and not evidence['resumed'] else 'unverified'
            elif passed and usage.get('delivery')!='external_delivered':status='passed'
            else:status='unverified'
            summary={'delivery':usage.get('delivery','tool'),'independent':independent,'contract_matches':matching,'required_tools_observed':required.issubset(observed),
                     'input_digest':evidence['input_digest'],'input_basis':evidence['input_basis'],
                     'output_contract_passed':evidence['source_outputs_verified'],'validated_dimensions':evidence['validated_dimensions'],
                     'hard_failure':evidence['hard_failure'],'run_status':evidence['run_status'],'method_tools_observed':bool(required.intersection(observed))}
            if repository.save_usage_outcome(usage['skill_id'],usage['version'],run_id,status,summary):self.recompute(usage['skill_id'],usage['version'])

    def recompute(self,skill_id,version):
        skill=repository.get_skill(skill_id)
        if not skill or skill['current_version']!=version or skill['state'] in {'disabled','suspended'}:return
        source=skill['evidence'];validated=[];failures=0
        for usage in repository.validation_runs(skill_id,version):
            feedback=repository.feedback(usage['run_id']) or {}
            if feedback.get('rating')=='needs_work':repository.set_state(skill_id,version,'suspended','negative_user_feedback');return
            if usage['status']=='failed':failures+=1;continue
            detail=json.loads(usage.get('evidence_json') or '{}')
            mechanical=usage['status']=='passed'
            # Human feedback can verify text/content, but never erase a failed run/check.
            human=(skill['review_status']=='accepted' and feedback.get('rating')=='useful'
                   and usage['status']=='unverified' and detail.get('run_status')=='succeeded'
                   and not detail.get('hard_failure') and detail.get('independent') and detail.get('required_tools_observed'))
            if mechanical or human:
                if detail.get('input_digest') not in {item['input_digest'] for item in validated}:
                    validated.append({'run_id':usage['run_id'],'input_digest':detail['input_digest'],'basis':detail.get('input_basis'),'human':human})
        if failures>=2:repository.set_state(skill_id,version,'suspended','repeated_execution_failure');return
        if failures:
            repository.set_state(skill_id,version,'candidate','failed_trial_requires_revision');return
        if not validated:return
        state='trial'
        review_needed=source.get('edited_procedure') or requires_review(source,skill['draft']['domain'])
        approved=not review_needed or skill['review_status']=='accepted'
        # Text-only "different prompts" do not count as automatic different data samples.
        distinct_material=all(item['basis'] in {'input_files','input_snapshot'} for item in validated)
        positive_all=all((repository.feedback(item['run_id']) or {}).get('rating')=='useful' for item in validated)
        if len(validated)>=2 and approved and (distinct_material or (skill['review_status']=='accepted' and positive_all)):
            state='stable'
        repository.set_state(skill_id,version,state,'independent_uses_'+str(len(validated)))

    def feedback(self,run_id,rating,comment):
        repository.save_feedback(run_id,rating,comment)
        for skill in repository.candidates_for_run(run_id):self.recompute(skill['id'],skill['current_version'])
        for usage in repository.validation_runs_for_run(run_id):self.recompute(usage['skill_id'],usage['version'])


_service=None

def get_skill_learning_service():
    global _service
    if _service is None:_service=SkillLearningService()
    return _service
