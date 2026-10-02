"""Recorded execution, fresh material and user feedback drive skill state, not LLM scores."""
from __future__ import annotations
import asyncio
import hashlib
import json
from types import SimpleNamespace
import pytest
from app.database import init_db,get_db
from app.repository import chat_runs,skill_learning as repo
from app.repository.context_recall import session_scope
from app.routers.chat.models import ChatRequest
from app.services.chat_run_state import chat_run_claim
from app.services.execution.step_ledger import StepLedger,ControllerCheckpoint
from app.services.plans.output_spec import OutputSpec,RequiredOutput,validate_output_spec
from app.services.skill_learning.context import hydrate_context,load_learned_skill
from app.services.skill_learning.evidence import build_evidence,validate_draft
from app.services.skill_learning.models import SkillDraft,DistillationResult
from app.services.skill_learning.service import SkillLearningService


def draft():
    return SkillDraft(name='json-row-report',description='将样例数据整理为 JSON，并检查行数',domain='routine',
        when_to_use='需要整理一份数据并验证行数时使用',inputs=['输入样例'],steps=[{'instruction':'整理数据，输出 JSON；验证 rows 字段。','evidence_ids':['step-1']}],
        verification=['用任务指定的预期行数检查 rows 字段'],limitations=['不证明科学结论正确'],pitfalls=[],keywords=['JSON','行数'])


@pytest.fixture
def db(isolated_app_env,monkeypatch):
    init_db()
    with get_db() as c:
        c.executemany('INSERT INTO chat_sessions(id,owner_id,project_id) VALUES(?,?,?)',[('s','u',1),('same-project','u',1),('other','u',2),('stranger','v',1)])
        c.commit()
    return isolated_app_env


def start(run_id,query='整理 JSON 行数',context=None):
    request=ChatRequest(message=query,session_id='s',context=context or {})
    chat_runs.create_chat_run(run_id,'s',request.model_dump_json(),owner_id='u')
    assert chat_runs.claim_chat_run_lease(run_id,'worker-'+run_id,ttl_seconds=300)
    assert chat_runs.mark_chat_run_started(run_id,worker_id='worker-'+run_id)
    return request


def finish(db,run_id,*,good=True,content_check=True):
    ledger=StepLedger(run_id,worker_id='worker-'+run_id)
    target=db['runtime_root']/f'{run_id}.json';target.write_text('{"rows": 5}')
    args={'operation':'write','path':str(target)}
    decision=ledger.prepare('call','file_operations',args,replay_policy='mutating')
    assert ledger.claim(decision.step.key)
    ledger.complete(decision.step.key,{'success':True,'output_file':str(target)},output_refs=[target])
    if content_check:
        d=ledger.prepare('verify','verify_task',{},replay_policy='read_only');assert ledger.claim(d.step.key)
        ledger.complete(d.step.key,{'success':True,'checks':[{'type':'json_field_equals','success':True,'actual':5,'expected':5,'key_path':'rows','path':str(target)}]})
    ledger.save_checkpoint(ControllerCheckpoint(run_id=run_id,messages=[{'role':'assistant','tool_calls':[{'function':{'name':'file_operations','arguments':json.dumps(args)}}]}]))
    spec=OutputSpec(required_outputs=[RequiredOutput(kind='data',extensions=['.json'])],source='explicit',acceptance_criteria={'checks':[{'type':'json_field_equals','key_path':'rows','path':str(target),'expected':5}]} if content_check else None)
    verification=validate_output_spec(spec,[str(target)] if good else [],base_dir=db['runtime_root'])
    payload={'type':'final','payload':{'response':'已整理数据','metadata':{'status':'completed' if good else 'failed','output_spec':spec.to_dict(),'output_verification':verification}}}
    assert chat_runs.finish_chat_run_with_event(run_id,'succeeded' if good else 'failed',payload,worker_id='worker-'+run_id) is not None


class FakeDistiller:
    calls=0
    async def distill(self,evidence):
        self.calls+=1
        return DistillationResult(reusable=True,reason='可复用的格式整理',draft=draft())


def seed(db,*,content_check=True):
    start('source');repo.save_run_context('source','source-material','input_files',[],scope=session_scope('s'),worker_id='worker-source');finish(db,'source',content_check=content_check)
    fake=FakeDistiller();service=SkillLearningService(fake)
    service.capture('source',forced=True)
    assert asyncio.run(service.process_one('source'))
    skill=repo.candidates_for_run('source')[0]
    return service,skill,fake


def reuse(db,service,skill,run_id,material,*,good=True,resumed=False,content_check=True):
    context={'learned_skill_ids':[skill['id']]}
    if resumed:context['resume_from_run_id']='source'
    start(run_id,context=context)
    repo.save_run_context(run_id,material,'input_files',[{'id':skill['id'],'version':skill['current_version']}],scope=session_scope('s'),worker_id='worker-'+run_id)
    token=chat_run_claim.set((run_id,'worker-'+run_id))
    try:load_learned_skill(skill['public_name'],'s')
    finally:chat_run_claim.reset(token)
    finish(db,run_id,good=good,content_check=content_check)
    service.complete_usages(run_id)


def test_model_creates_candidate_without_promoting_and_source_params_have_provenance(db):
    service,skill,fake=seed(db)
    assert fake.calls==1 and skill['state']=='candidate'
    assert skill['evidence']['source_outputs_verified'] and not skill['evidence']['requires_human_review']
    assert 'operation' in skill['evidence']['steps'][0]['parameters']
    assert asyncio.run(service.process_one('source')) is False and fake.calls==1


def test_two_distinct_checked_samples_promote_and_duplicate_material_does_not(db):
    service,skill,_=seed(db)
    reuse(db,service,skill,'one','new-input-one')
    assert repo.get_skill(skill['id'])['state']=='trial'
    reuse(db,service,skill,'duplicate','new-input-one')
    assert repo.get_skill(skill['id'])['state']=='trial'
    reuse(db,service,skill,'two','new-input-two')
    assert repo.get_skill(skill['id'])['state']=='stable'


def test_resumed_result_is_never_an_independent_sample(db):
    service,skill,_=seed(db)
    reuse(db,service,skill,'resumed','different',resumed=True)
    assert repo.get_skill(skill['id'])['state']=='candidate'
    evidence=json.loads(repo.validation_runs(skill['id'],1)[0]['evidence_json'])
    assert evidence['independent'] is False


def test_format_only_success_requires_method_review(db):
    service,skill,_=seed(db,content_check=False)
    assert skill['evidence']['requires_human_review'] is True
    reuse(db,service,skill,'one','sample-one',content_check=False);reuse(db,service,skill,'two','sample-two',content_check=False)
    assert repo.get_skill(skill['id'])['state']=='trial'
    service.review(skill['id'],1,'accept')
    assert repo.get_skill(skill['id'])['state']=='stable'


def test_positive_user_feedback_never_overrides_failed_contract(db):
    service,skill,_=seed(db)
    reuse(db,service,skill,'bad','sample-one',good=False)
    service.feedback('bad','useful','还可以')
    service.review(skill['id'],1,'accept')
    assert repo.get_skill(skill['id'])['state']=='candidate'
    assert repo.validation_runs(skill['id'],1)[0]['status']=='failed'


def test_negative_feedback_suspends_and_edit_resets_old_validation_and_review(db):
    service,skill,_=seed(db)
    reuse(db,service,skill,'one','input-one');reuse(db,service,skill,'two','input-two')
    service.feedback('one','needs_work','行数不对')
    assert repo.get_skill(skill['id'])['state']=='suspended'
    fixed=draft();fixed.steps[0].instruction='先处理缺失值，再整理 JSON；按需求验证行数。'
    service.edit(skill['id'],1,fixed)
    updated=repo.get_skill(skill['id'])
    assert updated['current_version']==2 and updated['state']=='candidate' and updated['review_status']=='pending'
    service.recompute(skill['id'],1)
    assert repo.get_skill(skill['id'])['state']=='candidate'
    assert repo.get_skill(skill['id'],1)['draft']['steps'][0]['instruction']!=fixed.steps[0].instruction


def test_scope_version_and_unrequested_candidate_are_checked(db):
    service,skill,_=seed(db)
    with pytest.raises(ValueError):load_learned_skill(skill['public_name'],'other')
    with pytest.raises(ValueError):load_learned_skill(skill['public_name'],'stranger')
    with pytest.raises(ValueError,match='explicit trial'):load_learned_skill(skill['public_name'],'s')
    start('trial');token=chat_run_claim.set(('trial','worker-trial'))
    try:
        context={'learned_skill_ids':[skill['id']],'learned_skill_versions':{skill['id']:1}}
        hydrate_context(context,'s','新样例 JSON','trial')
        assert repo.validation_runs(skill['id'],1)[0]['delivery']=='prompt'
        service.edit(skill['id'],1,draft())
        with pytest.raises(ValueError,match='version changed'):load_learned_skill(skill['public_name'],'s')
    finally:chat_run_claim.reset(token)


def test_exposure_without_terminal_usage_never_promotes(db):
    service,skill,_=seed(db)
    start('trial');repo.save_run_context('trial','new','input_files',[{'id':skill['id'],'version':1}],scope=session_scope('s'),worker_id='worker-trial')
    finish(db,'trial');service.complete_usages('trial')
    assert not repo.validation_runs(skill['id'],1) and repo.get_skill(skill['id'])['state']=='candidate'


def test_model_cannot_invent_evidence_ids(db):
    service,skill,_=seed(db)
    invalid=draft();invalid.steps[0].evidence_ids=['not-executed']
    with pytest.raises(ValueError,match='does not exist'):validate_draft(invalid,skill['evidence'])


def test_claim_recovery_fences_old_worker_and_global_hour_budget(db):
    start('source');finish(db,'source');scope=session_scope('s');repo.enqueue(chat_runs.get_chat_run('source'),scope,True)
    first=repo.claim_job('source');assert first and repo.claim_job('source') is None
    assert repo.reserve_model_call('source',first['claim'],1)
    with get_db() as c:c.execute("UPDATE skill_learning_jobs SET lease_until='2000-01-01' WHERE run_id='source'");c.commit()
    second=repo.claim_job('source');assert second['claim']!=first['claim']
    assert repo.finish_job('source',first['claim'],status='completed') is False
    assert repo.reserve_model_call('source',second['claim'],1) is False
    repo.finish_job('source',second['claim'],status='pending',error_code='hourly_budget')
    assert repo.due_jobs(True)==[] and repo.job('source')['attempts']==1


def test_session_rebind_does_not_move_skill_project_and_saved_skill_survives_chat_deletion(db):
    service,skill,_=seed(db)
    with get_db() as c:c.execute("UPDATE chat_sessions SET project_id=2 WHERE id='s'");c.commit()
    assert not repo.list_skills(session_scope('s'))
    assert repo.list_skills(session_scope('same-project'))[0]['id']==skill['id']
    with get_db() as c:c.execute("DELETE FROM chat_runs WHERE run_id='source'");c.commit()
    assert repo.get_skill(skill['id']) and repo.get_skill(skill['id'])['source_run_id'] is None


def test_corrupt_receipt_is_not_reported_as_verified_tool_evidence(db):
    start('corrupt');finish(db,'corrupt')
    from app.repository.run_steps import list_steps
    step=next(step for step in list_steps('corrupt') if step.tool_name=='file_operations')
    ledger=StepLedger('corrupt')
    (ledger.blobs.root/(step.result_ref+'.json')).write_text('{"success":true,"changed":true}')
    evidence=build_evidence('corrupt')
    assert evidence['unavailable_receipts']==1 and all(step['tool']!='file_operations' for step in evidence['steps'])


def test_auxiliary_model_cannot_return_lifecycle_or_verification_fields():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        DistillationResult.model_validate({'reusable':True,'reason':'I succeeded','draft':draft().model_dump(),'state':'stable'})


def test_explicit_old_version_is_not_silently_replaced_on_trial(db):
    service,skill,_=seed(db)
    service.edit(skill['id'],1,draft())
    with pytest.raises(ValueError,match='version changed'):
        hydrate_context({'learned_skill_ids':[skill['id']],'learned_skill_versions':{skill['id']:1}},'s','新样例')


def test_pre_execution_file_digest_is_content_based_and_bounded(db):
    from app.services.skill_learning.evidence import fingerprint_input
    a=db['runtime_root']/'a.csv';b=db['runtime_root']/'b.csv';a.write_text('x\n1');b.write_text('x\n1')
    first=fingerprint_input('任务 A',{'attachments':[{'path':str(a)}]})
    second=fingerprint_input('任务 B',{'attachments':[{'path':str(b)}]})
    assert first==second and first[1]=='input_files'
    b.write_text('x\n2');assert fingerprint_input('任务 B',{'attachments':[{'path':str(b)}]})[0]!=first[0]
    assert fingerprint_input('任务',{'attachments':[{'path':str(b)+'missing'}]})[1]=='request_text_unverified_files'


def test_old_source_feedback_does_not_suspend_an_edited_version(db):
    service,skill,_=seed(db);service.edit(skill['id'],1,draft())
    service.feedback('source','needs_work','原任务未完成')
    assert repo.get_skill(skill['id'])['current_version']==2 and repo.get_skill(skill['id'])['state']=='candidate'


def test_stale_run_cannot_record_late_learning_inputs(db):
    from app.repository.run_steps import StaleRunClaim
    start('late');finish(db,'late')
    with pytest.raises(StaleRunClaim):
        repo.save_run_context('late','digest','request_text',[],scope=session_scope('s'),worker_id='worker-late')
    assert repo.get_run_context('late') is None
