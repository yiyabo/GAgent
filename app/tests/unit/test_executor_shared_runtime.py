from __future__ import annotations
import asyncio
from dataclasses import replace
from types import SimpleNamespace
import pytest
from app.database import init_db,get_db
from app.repository import chat_runs
from app.services.chat_run_state import chat_run_claim,ChatRunOutcome
from app.services.execution.runtime import execution_verdict,execution_context
from app.services.execution.delegate_ledger import run_delegation,ensure_delegate_resume
from app.services.execution.step_ledger import StepLedger
from app.services.deep_think.checkpointing import ControllerRestoreError
from app.services.plans.executor_models import ExecutionResult
from app.services.plans.task_delegate_executor import TaskDelegationSpec,TaskDelegationResult
from app.routers.chat.models import ChatRequest


def test_same_terminal_truth_for_native_plan_and_chat():
    metadata={'output_verification':{'authoritative':True,'status':'failed'}}
    assert execution_verdict('completed',metadata).status=='failed'
    assert ChatRunOutcome.from_event({'type':'final','payload':{'metadata':metadata}}).status=='failed'
    assert execution_verdict('completed',{'execution_issues':[{'code':'uncertain'}]}).status=='failed'
    assert execution_verdict('cancelled').status=='cancelled'
    assert execution_verdict('failed',{'output_verification':{'authoritative':True,'status':'passed'}}).status=='failed'


def test_context_inherits_provider_skills_owner_and_history_without_copying_registries():
    registry=object()
    agent=SimpleNamespace(session_id='s',history=[{'role':'user','content':'start'}],_current_user_message='execute task 8',
        extra_context={'owner_id':'alice','model_provider':{'model':'m'},'memory_enabled':False,'learned_skill_context':{'skills':[1]},'_artifact_registry':registry})
    context=execution_context(agent)
    assert context['owner_id']=='alice' and context['memory_enabled'] is False and context['user_message']=='execute task 8'
    assert context['_artifact_registry'] is registry and context['chat_history'] is not agent.history


@pytest.fixture
def db(isolated_app_env):
    init_db()
    with get_db() as c:c.execute("INSERT INTO chat_sessions(id,owner_id) VALUES('s','u')");c.commit()
    yield isolated_app_env


def start(identity,source=None):
    request=ChatRequest(message='produce output',session_id='s',context={'resume_from_run_id':source} if source else {})
    chat_runs.create_chat_run(identity,'s',request.model_dump_json(),owner_id='u')
    assert chat_runs.claim_chat_run_lease(identity,'worker-'+identity,ttl_seconds=300)
    assert chat_runs.mark_chat_run_started(identity,worker_id='worker-'+identity)
    return chat_run_claim.set((identity,'worker-'+identity))


def spec(root):
    return TaskDelegationSpec(plan_id=1,task_id=8,task_name='produce',task_instruction='produce output',task_prompt='prompt',executor_backend='local',session_id='s',owner_id='u',work_dir=str(root))


def test_verified_delegate_reuses_result_and_reverifies_without_invoking_backend(db):
    path=db['runtime_root']/'output.json';path.write_text('{"value":2}')
    calls=[];finalizations=[]
    def execute():calls.append(1);return TaskDelegationResult(status='completed',summary='produced',executor='local',artifact_paths=[str(path)])
    def finalize(value):
        finalizations.append(1);assert path.is_file()
        return ExecutionResult(plan_id=1,task_id=8,status='completed',content=value.summary,metadata={'artifact_paths':value.artifact_paths})
    token=start('source')
    try:run_delegation(spec(db['runtime_root']),execute,finalize)
    finally:chat_run_claim.reset(token)
    assert chat_runs.mark_chat_run_finished('source','failed',worker_id='worker-source')
    token=start('child','source')
    try:
        StepLedger('child').import_from('source')
        ensure_delegate_resume(1,8,'produce output',previously_entered=True)
        value=run_delegation(replace(spec(db['runtime_root']),current_job_id='new-job',task_prompt='updated history'),execute,finalize)
        assert value.status=='completed' and calls==[1] and finalizations==[1,1]
        with pytest.raises(ControllerRestoreError,match='contract or workspace changed'):
            run_delegation(replace(spec(db['runtime_root']),acceptance_criteria={'checks':[1]}),execute,finalize)
    finally:chat_run_claim.reset(token)


def test_uncertain_delegate_never_repeats_mutating_work(db):
    calls=[]
    def execute():calls.append(1);raise RuntimeError('disconnected after external action')
    token=start('source')
    try:
        with pytest.raises(RuntimeError):run_delegation(spec(db['runtime_root']),execute,lambda value:value)
    finally:chat_run_claim.reset(token)
    assert chat_runs.mark_chat_run_finished('source','failed',worker_id='worker-source')
    token=start('child','source')
    try:
        StepLedger('child').import_from('source')
        with pytest.raises(ControllerRestoreError,match='reconciliation'):
            run_delegation(spec(db['runtime_root']),execute,lambda value:value)
        assert calls==[1]
    finally:chat_run_claim.reset(token)


def test_changed_or_missing_output_does_not_trigger_delegate_reexecution(db):
    path=db['runtime_root']/'output.txt';path.write_text('original')
    token=start('source');calls=[]
    try:
        run_delegation(spec(db['runtime_root']),lambda:calls.append(1),lambda _:ExecutionResult(1,8,'completed','done',metadata={'artifact_paths':[str(path)]}))
    finally:chat_run_claim.reset(token)
    assert chat_runs.mark_chat_run_finished('source','failed',worker_id='worker-source')
    path.write_text('changed')
    token=start('child','source')
    try:
        StepLedger('child').import_from('source')
        with pytest.raises(ControllerRestoreError):run_delegation(spec(db['runtime_root']),lambda:calls.append(1),lambda value:value)
        assert calls==[1]
    finally:chat_run_claim.reset(token)


def test_checked_delivery_does_not_finish_with_retry_prose_or_upgrade_failed_outputs():
    from app.services.execution.runtime import repair_verified_delivery_answer
    result=SimpleNamespace(final_answer='The call was malformed; retrying with correct parameters',
        output_verification={'authoritative':True,'status':'passed','artifact_paths':['/tmp/result.json']},execution_issues=[],tools_used=['execute_code'])
    repair_verified_delivery_answer(result,'produce file')
    assert 'result.json' in result.final_answer and 'passed the declared output checks' in result.final_answer
    failed=SimpleNamespace(final_answer='retrying',output_verification={'authoritative':True,'status':'failed','artifact_paths':['/tmp/old.json']},execution_issues=[],tools_used=['execute_code'])
    assert repair_verified_delivery_answer(failed,'produce file').final_answer=='retrying'
