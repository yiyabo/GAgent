"""Shared execution semantics for chat, native tasks and backend adapters.

Domain planning remains outside this kernel. Backends own their model/tool
loops; ownership, context inheritance and terminal truth use one policy.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Any,Callable,Awaitable
from app.services.run_budget import check_run_active,run_stage

_COMPLETED={'completed','done','success','succeeded'}
_FAILED={'failed','error','blocked','skipped','incomplete'}
_CANCELLED={'cancelled','canceled'}


@dataclass(frozen=True)
class ExecutionVerdict:
    status:str
    reason:str|None=None
    verification:str='not_required'

    @property
    def completed(self)->bool:return self.status=='succeeded'


def execution_verdict(status:str|None,metadata:dict|None=None,*,cancelled=False,default_success=True)->ExecutionVerdict:
    meta=metadata or {};value=str(status or '').lower()
    if meta.get('failure_kind')=='deadline_exceeded':return ExecutionVerdict('failed','deadline_exceeded','incomplete')
    if cancelled or value in _CANCELLED or meta.get('cancelled') is True:return ExecutionVerdict('cancelled','cancelled','incomplete')
    if value in _FAILED:return ExecutionVerdict('failed',str(meta.get('failure_kind') or 'backend_failed'),'incomplete')
    if meta.get('execution_issues'):return ExecutionVerdict('failed','step_reconciliation_required','incomplete')
    report=meta.get('output_verification')
    if isinstance(report,dict) and report.get('authoritative'):
        if report.get('status')!='passed':return ExecutionVerdict('failed','output_contract_mismatch','incomplete')
        return ExecutionVerdict('succeeded',verification='outputs_verified')
    verification=meta.get('verification') or meta.get('verification_record')
    if isinstance(verification,dict) and verification.get('blocking') is True and verification.get('status')=='failed':
        return ExecutionVerdict('failed','verification_failed','incomplete')
    if meta.get('failure_kind') in {'output_contract_mismatch','contract_mismatch','step_reconciliation_required'}:
        return ExecutionVerdict('failed',meta['failure_kind'],'incomplete')
    if value in _COMPLETED or default_success:return ExecutionVerdict('succeeded')
    return ExecutionVerdict('failed','missing_terminal_outcome','incomplete')


def execution_context(agent:Any,*,user_message:str|None=None,overrides:dict|None=None)->dict:
    extra=getattr(agent,'extra_context',None) or {}
    # Runtime references retain identity; no JSON roundtrip/deepcopy of registries.
    context=dict(extra)
    context.update({'session_id':getattr(agent,'session_id',None),
                    'chat_history':list(getattr(agent,'history',[]) or [])})
    message=user_message if user_message is not None else getattr(agent,'_current_user_message',None)
    if message is not None:context['user_message']=message
    if overrides:context.update(overrides)
    return context


async def run_execution(factory:Callable[[],Awaitable[Any]],*,stage:str)->Any:
    check_run_active()
    result=await run_stage(factory(),stage=stage)
    check_run_active()
    return result


def assert_backend_completed(status:str,metadata:dict|None=None)->None:
    verdict=execution_verdict(status,metadata,default_success=False)
    if verdict.status=='cancelled':
        from app.services.cancellation import current_cancel_token
        token=current_cancel_token()
        if token:
            token.set('backend_cancelled')
            raise asyncio.CancelledError('Backend execution was cancelled')
        return  # Standalone caller records a failed task with a distinct cancelled backend status.
    check_run_active()


def repair_verified_delivery_answer(result:Any,query:str)->Any:
    """Deliver checked files if synthesis leaves only a process/retry message.

    This reports the output-check boundary, never an unverified scientific claim.
    """
    report=getattr(result,'output_verification',None)
    if not isinstance(report,dict) or not report.get('authoritative') or report.get('status')!='passed':return result
    if getattr(result,'execution_issues',None):return result
    tools=set(getattr(result,'tools_used',[]) or [])
    if not tools.intersection({'execute_code','code_executor','file_operations','manuscript_writer','scientific_figure_generator'}):return result
    text=str(getattr(result,'final_answer','') or '').strip()
    from app.services.deep_think_agent import is_process_only_answer
    process=is_process_only_answer(text) or (len(text)<160 and any(word in text.lower() for word in ('retrying','malformed','i will','开始检查','正在','稍等')))
    if text and not process:return result
    from pathlib import Path
    paths=report.get('artifact_paths') or []
    if not paths:return result
    chinese=any('\u4e00'<=char<='\u9fff' for char in query)
    intro='已产生以下文件，声明的产物检查已通过：' if chinese else 'The following files were produced and passed the declared output checks:'
    result.final_answer=intro+'\n\n'+'\n'.join(f'- [{Path(path).name}]({path})' for path in paths[:12])
    return result
