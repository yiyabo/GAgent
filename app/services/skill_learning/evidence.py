"""Freeze execution facts. Saved answers and LLM ratings are never execution proof."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from app.repository import chat_runs, run_steps
from app.repository.context_recall import session_scope
from app.repository import skill_learning as repository
from app.services.execution.step_ledger import StepLedger, params_fingerprint
from app.services.plans.output_spec import parse_output_spec

_NON_WORK = {'load_skill','load_tool_schema','get_bound_plan','bind_plan','check_task_status'}
_CONTENT_CHECKS = {'json_field_equals','json_field_at_least','sequence_count','table_row_count','table_columns'}


def fingerprint_input(query: str, context: dict) -> tuple[str,str]:
    from app.services.run_budget import check_run_active
    check_run_active()
    attachments = context.get('attachments') or []
    hashes=[]
    total=0
    for item in attachments[:10]:
        path = Path(str(item.get('path') or '')) if isinstance(item,dict) else None
        if path is None or not path.is_file():
            return hashlib.sha256(query.strip().encode()).hexdigest(),'request_text_unverified_files'
        size=path.stat().st_size
        total+=size
        if total>64*1024*1024:
            return hashlib.sha256(query.strip().encode()).hexdigest(),'request_text_unverified_files'
        digest=hashlib.sha256()
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024*1024),b''):
                check_run_active();digest.update(block)
        hashes.append(digest.hexdigest())
    if hashes:
        return hashlib.sha256(repository.encode(sorted(hashes)).encode()).hexdigest(),'input_files'
    return hashlib.sha256(' '.join(query.split()).encode()).hexdigest(),'request_text'


def _bounded(value, limit=900):
    text=repository.encode(value)
    return text if len(text)<=limit else text[:limit]+' [excerpt]'


def _calls(checkpoint):
    calls=[]
    for message in checkpoint.messages:
        calls.extend(message.get('tool_calls') or [])
    calls.extend((checkpoint.controller_state.get('pending_result') or {}).get('tool_calls') or [])
    out={}
    state=checkpoint.controller_state
    if state.get('engine')=='delegate' and isinstance(state.get('delegation_parameters'),dict):
        params=state['delegation_parameters']
        fingerprint=params_fingerprint('delegate_task',params)
        if fingerprint==state.get('params_fingerprint'):out[('delegate_task',fingerprint)]=params
    for call in calls:
        if not isinstance(call,dict):continue
        function=call.get('function') or call
        name=function.get('name'); args=function.get('arguments') or {}
        try:
            if isinstance(args,str):args=json.loads(args)
            if name and isinstance(args,dict):out[(name,params_fingerprint(name,args))]=args
        except (TypeError,ValueError):continue
    return out


def _checks(result):
    found=[]
    stack=[result]
    while stack and len(found)<30:
        value=stack.pop()
        if isinstance(value,dict):
            if value.get('type') in _CONTENT_CHECKS and value.get('success') is True:
                found.append({key:value.get(key) for key in ('type','path','key_path','key','expected','actual','min_value','columns') if key in value})
            stack.extend(v for v in value.values() if isinstance(v,(dict,list)))
        elif isinstance(value,list):stack.extend(value[:50])
    return found


def build_evidence(run_id: str) -> dict | None:
    run=chat_runs.get_chat_run(run_id)
    if not run or run['status'] not in {'succeeded','failed','cancelled'}:return None
    scope=session_scope(run['session_id'])
    if not scope or scope['owner_id'] != run['owner_id']:return None
    request=json.loads(run.get('request_json') or '{}')
    final={}
    for _,event in reversed(chat_runs.fetch_events_after(run_id,-1)):
        if event.get('type')=='final':
            final=event.get('payload') or {};break
    meta=final.get('metadata') or {}
    report=meta.get('output_verification') or {}
    ledger=StepLedger(run_id)
    args_by_key={}
    for pointer in run_steps.list_checkpoint_pointers(run_id)[:20]:
        try:
            checkpoint=ledger.load_checkpoint(checkpoint_key=pointer['checkpoint_key'])
            if checkpoint:args_by_key.update(_calls(checkpoint))
        except (OSError,ValueError,RuntimeError):continue
    latest={}
    for step in run_steps.list_steps(run_id):
        key=(step.key.tool_call_id,step.key.params_fingerprint)
        if key not in latest or step.key.attempt>latest[key].key.attempt:latest[key]=step
    steps=[]; unavailable=0; content_checks=[]
    for step in latest.values():
        if step.status!='succeeded' or step.tool_name in _NON_WORK:continue
        try:
            # Immutable receipt integrity, not an assertion old files still exist.
            result=ledger.blobs.load(step.result_ref,step.result_checksum)
        except (OSError,ValueError,RuntimeError):unavailable+=1;continue
        args=args_by_key.get((step.tool_name,step.key.params_fingerprint))
        if step.tool_name in {'verify_task','task_operation'}:content_checks.extend(_checks(result))
        steps.append({'id':'step-'+str(len(steps)+1),'tool':step.tool_name,
                      'params_hash':step.key.params_fingerprint,'parameters':_bounded(args,1800) if args is not None else None,
                      'result':_bounded(result,900),'output_receipts':[{'sha256':ref['checksum']} for ref in step.output_refs],
                      'replay_policy':step.replay_policy})
        if len(steps)>=24:break
    spec=parse_output_spec(meta.get('output_spec'))
    contract=[]
    if spec:
        for output in spec.required_outputs:
            contract.append({'kind':output.kind,'extensions':output.extensions,'min_count':output.min_count,'constraints':output.constraints})
    declared=[check for check in ((spec.acceptance_criteria or {}).get('checks') or [])
              if isinstance(check,dict) and check.get('type') in _CONTENT_CHECKS] if spec else []
    def matches(check, observed):
        key=check.get('key_path',check.get('key',check.get('field')))
        if check.get('type')!=observed.get('type') or key!=observed.get('key_path',observed.get('key')):return False
        if check.get('path')!=observed.get('path'):return False
        return all(check[key]==observed.get(key) for key in ('expected','min_value','columns') if key in check)
    covered=bool(declared) and all(any(matches(check,observed) for observed in content_checks) for check in declared)
    shapes=[{'type':check['type'],'key_path':check.get('key_path',check.get('key',check.get('field')))} for check in declared]
    contract_hash=hashlib.sha256(repository.encode({'outputs':contract,'checks':shapes}).encode()).hexdigest() if contract else None
    authoritative=report.get('authoritative') is True
    hard_failure=(authoritative and report.get('status')=='failed') or bool(meta.get('execution_issues'))
    verified=run['status']=='succeeded' and authoritative and report.get('status')=='passed' and not hard_failure
    context=repository.get_run_context(run_id)
    input_digest=context.get('input_digest') if context else None
    input_basis=context.get('input_basis') if context else 'unrecorded'
    snapshot=meta.get('output_input_snapshot') or {}
    if not input_digest and isinstance(snapshot,dict):
        hashes=sorted(value['sha256'] for value in snapshot.values() if isinstance(value,dict) and value.get('exists') is True and value.get('sha256'))
        if hashes:input_digest=hashlib.sha256(repository.encode(hashes).encode()).hexdigest();input_basis='input_snapshot'
    evidence={'schema_version':1,'run_id':run_id,'owner_id':run['owner_id'],'session_id':run['session_id'],'project_id':context['project_id'] if context else scope['project_id'],
              'run_status':run['status'],'user_goal':str(request.get('message') or '')[:4000],
              'answer_excerpt':str(final.get('response') or '')[:1200], 'steps':steps,
              'source_outputs_verified':verified,'spec_provenance':spec.source if spec else None,'hard_failure':bool(hard_failure),'contract':contract,'contract_hash':contract_hash,
              'unchecked_constraints':report.get('unchecked_constraints') or [],'content_checks':content_checks,'declared_content_checks_covered':covered,
              'input_digest':input_digest,'input_basis':input_basis,'resumed':bool((request.get('context') or {}).get('resume_from_run_id')),
              'unavailable_receipts':unavailable,'truncated_steps':len(latest)>24,
              'validated_dimensions':['tool_receipt_integrity']+(['output_contract'] if verified else [])+(['structured_content_checks'] if content_checks else []),
              'feedback':repository.feedback(run_id),'request_tier':meta.get('request_tier')}
    encoded=repository.encode(evidence)
    if len(encoded)>24000:
        evidence['steps']=steps[:8];evidence['truncated_steps']=True
    return evidence


def requires_review(evidence: dict, domain: str) -> bool:
    return evidence.get('input_basis') not in {'input_files','input_snapshot'} or bool(evidence.get('truncated_steps') or evidence.get('unavailable_receipts')) or evidence.get('spec_provenance')!='explicit' or not evidence.get('source_outputs_verified') or domain!='routine' or evidence.get('request_tier')=='research' or not evidence.get('declared_content_checks_covered') or bool(evidence.get('unchecked_constraints')) or bool(re.search(r'统计|显著|预测|模型|平均|假设|p.?value|regression|scientific|mean\b',evidence.get('user_goal',''),re.I))


def validate_draft(draft, evidence: dict) -> None:
    known={step['id'] for step in evidence['steps']}
    references={identity for step in draft.steps for identity in step.evidence_ids}
    if references-known:raise ValueError('draft references execution evidence that does not exist')
    if evidence['steps'] and not references:raise ValueError('workflow must reference its supporting execution receipts')
    if len(repository.encode(draft.model_dump()))>9000:raise ValueError('skill draft is too large')
