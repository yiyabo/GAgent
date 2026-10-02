"""Atomic delegation receipts, not an invented CLI-internal tool history.

A verified result can be reused. An uncertain write never restarts itself.
The plan's existing verifier remains the authority for completion.
"""
from __future__ import annotations
import hashlib
from dataclasses import asdict
from typing import Callable,Any
from app.repository.run_steps import list_steps
from app.services.chat_run_state import chat_run_claim
from app.services.deep_think.checkpointing import ControllerRestoreError
from app.services.execution.step_ledger import StepLedger,ControllerCheckpoint,ResultUnavailable,params_fingerprint
from app.services.run_resume import current_resume_source
from app.services.run_budget import check_run_active


def checkpoint_key(plan_id:int,task_id:int,query:str)->str:
    return f'task:{plan_id}:{task_id}:'+hashlib.sha256(query.encode()).hexdigest()


def ensure_delegate_resume(plan_id:int,task_id:int,query:str,*,previously_entered:bool)->None:
    claim=chat_run_claim.get()
    if not claim:return
    ledger=StepLedger(claim[0]);checkpoint=ledger.load_checkpoint(checkpoint_key=checkpoint_key(plan_id,task_id,query))
    if checkpoint and checkpoint.controller_state.get('engine')=='delegate':return
    prefix=f'delegate:{plan_id}:{task_id}:'
    if previously_entered or any(step.key.tool_call_id.startswith(prefix) for step in list_steps(claim[0])):
        raise ControllerRestoreError('Delegated task has no usable continuation receipt; reconciliation is required')


def run_delegation(spec:Any,execute:Callable[[],Any],finalize:Callable[[Any],Any])->Any:
    """Wrap the actual backend plus verification/materialization in one scope."""
    claim=chat_run_claim.get()
    if not claim:return finalize(execute())
    ledger=StepLedger(claim[0])
    params={name:getattr(spec,name) for name in ('plan_id','task_id','task_instruction','executor_backend','session_id','owner_id','work_dir','artifact_contract','acceptance_criteria','resolved_input_artifacts')}
    fingerprint=params_fingerprint('delegate_task',params)
    query=(spec.task_instruction or spec.task_name).strip()
    key=checkpoint_key(spec.plan_id,spec.task_id,query)
    slot=f'delegate:{spec.plan_id}:{spec.task_id}:'+hashlib.sha256(query.encode()).hexdigest()[:16]
    checkpoint=ledger.load_checkpoint(checkpoint_key=key)
    if current_resume_source() and checkpoint and checkpoint.controller_state.get('engine')!='delegate':
        raise ControllerRestoreError('Cannot continue a task through a different execution backend')
    if current_resume_source() and checkpoint and checkpoint.controller_state.get('params_fingerprint')!=fingerprint:
        raise ControllerRestoreError('Delegation contract or workspace changed; continuation cannot repeat its effects')
    decision=ledger.prepare(slot,'delegate_task',params,replay_policy='mutating',resume=bool(current_resume_source()))
    if decision.action=='replay':
        from app.services.plans.executor_models import ExecutionResult
        raw=decision.result
        if not isinstance(raw,dict) or not isinstance(raw.get('execution_result'),dict):
            raise ControllerRestoreError('Delegation result cannot be restored')
        result=ExecutionResult(**raw['execution_result'])
        from app.services.plans.task_delegate_executor import TaskDelegationResult
        restored=TaskDelegationResult(status='completed',summary=result.content,
            artifact_paths=list((result.metadata or {}).get('artifact_paths') or []),
            executor=spec.executor_backend,metadata={**(result.metadata or {}),'delegation_replayed':True},
            raw_result=dict(result.metadata or {}))
        check_run_active()
        return finalize(restored)
    if decision.action!='execute' or not ledger.claim(decision.step.key):
        raise ControllerRestoreError('Uncertain delegated operation requires reconciliation before retry')
    state={'engine':'delegate','plan_id':spec.plan_id,'task_id':spec.task_id,
           'query_sha256':hashlib.sha256(query.encode()).hexdigest(),
           'namespace':slot,'bound_plan_id':spec.plan_id,'params_fingerprint':fingerprint,'delegation_parameters':params}
    ledger.save_checkpoint(ControllerCheckpoint(run_id=claim[0],phase='delegate_pending',controller_state=state),checkpoint_key=key)
    try:
        check_run_active();result=finalize(execute());check_run_active()
        if result.status in {'completed','done'}:
            paths=(result.metadata or {}).get('artifact_paths') or []
            ledger.complete(decision.step.key,{'execution_result':result.to_dict()},output_refs=paths)
            phase='delegate_verified'
        else:
            ledger.fail(decision.step.key,'delegation_not_verified');phase='delegate_failed'
        ledger.save_checkpoint(ControllerCheckpoint(run_id=claim[0],phase=phase,controller_state=state),checkpoint_key=key)
        return result
    except BaseException:
        try:ledger.interrupt(decision.step.key,'delegation_interrupted')
        except Exception:pass  # Closed owners cannot amend the ledger; pending/running stays uncertain.
        raise
