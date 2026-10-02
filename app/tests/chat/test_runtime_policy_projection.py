import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services.deep_think import runtime_policy as policy
from app.services.deep_think.receipt_projection import project_text
from app.services.deep_think import dispatch, native_validation as nv
from app.llm import NativeToolCall


@pytest.mark.parametrize('umbrella,arguments,schemas,want', [(False,True,False,(True,False)),(True,False,None,(False,True)),(False,None,True,(False,True))])
def test_independent_controls_and_stable_old_resume(monkeypatch, umbrella, arguments, schemas, want):
    monkeypatch.setattr(policy,'get_settings',lambda:SimpleNamespace(agent_runtime_v2_enabled=umbrella,
        agent_argument_validation_enabled=arguments,agent_schema_disclosure_v2_enabled=schemas))
    agent=SimpleNamespace()
    frozen=policy.policy_for(agent)
    assert (frozen['arguments'],frozen['schemas'])==want
    monkeypatch.setattr(policy,'get_settings',lambda:SimpleNamespace(agent_runtime_v2_enabled=not umbrella))
    assert policy.policy_for(agent)==frozen
    restored=SimpleNamespace()
    policy.restore_policy(restored, {'runtime_policy':frozen})
    assert policy.policy_for(restored)==frozen
    policy.restore_policy(restored, {})
    assert policy.policy_for(restored)=={'version':1,'arguments':False,'schemas':False,'receipts':False}


def test_argument_barrier_does_not_enable_schema_policy(monkeypatch):
    settings=SimpleNamespace(agent_runtime_v2_enabled=False,agent_argument_validation_enabled=True,agent_schema_disclosure_v2_enabled=False)
    monkeypatch.setattr(nv,'get_settings',lambda:settings)
    agent=SimpleNamespace(_schema_disclosure=SimpleNamespace(_full=[{'function':{'name':'write','parameters':{'required':['code'],'properties':{'code':{'type':'string'}}}}}],meta_schema=lambda:{}))
    rejected=nv.rejected_call(agent,NativeToolCall('a','write',{'_raw':'{'}),1,0)
    assert rejected['executed'] is False
    assert policy.policy_for(agent)['schemas'] is False
    agent._native_repair_cap=8192
    assert nv.request_kwargs(agent,'query',{},[],1,[])=={'output_reserve_tokens':8192}


def test_projection_changes_model_messages_only_and_preserves_all_data():
    agent=SimpleNamespace(_runtime_policy={'version':1,'arguments':False,'schemas':False,'receipts':True})
    data={'success':True,'error':None,'content':'id,score\na,10\n','path':'/artifact/a.csv',
          'version':'v7','input_refs':['immutable/input.csv'],'partial':True,'omitted_items':3}
    raw=json.dumps({'success':True,'tool':'file_operations','result':data,'error':None},ensure_ascii=False)
    item={'tool_call_id':'call','tool_name':'file_operations','tool_params':{'operation':'read'},'tool_result_text':raw}
    before=deepcopy(item); messages=[];step=SimpleNamespace()
    dispatch._append_tool_cycle_messages(agent=agent,messages=messages,tool_results=[item],assistant_content='',current_step=step)
    assert json.loads(messages[1]['content'])=={'tool':'file_operations','result':data}
    assert len(messages[1]['content'])<len(raw)
    assert item==before and step.action_result=='[file_operations] '+raw
    assert messages[0]['tool_calls'][0]['id']==messages[1]['tool_call_id']=='call'
    assert project_text(agent,'file_operations',messages[1]['content'])==messages[1]['content']


def test_conflicting_error_status_and_false_zero_are_not_discarded():
    agent=SimpleNamespace(_runtime_policy={'receipts':True})
    payload={'tool':'x','success':False,'result':{'success':0,'error':'partial write','job_id':'j'},'error':'timeout'}
    assert json.loads(project_text(agent,'x',json.dumps(payload)))==payload
    assert project_text(agent,'x','not JSON')=='not JSON'
    assert project_text(SimpleNamespace(_runtime_policy={'receipts':False}),'x',json.dumps(payload))==json.dumps(payload)


def test_unsupported_checkpoint_policy_fails_closed():
    with pytest.raises(ValueError):policy.restore_policy(SimpleNamespace(),{'runtime_policy':{'version':2}})


async def test_independent_argument_flag_rejects_bad_calls_without_replaying_valid_handler(monkeypatch):
    import asyncio
    from app.services.deep_think_agent import DeepThinkAgent
    monkeypatch.setattr(nv,'get_settings',lambda:SimpleNamespace(agent_runtime_v2_enabled=False,agent_argument_validation_enabled=True))
    handled=[]
    async def execute(name,params,**kwargs):
        handled.append(params['code']);return {'success':True,'output':'ok'}
    agent=DeepThinkAgent(SimpleNamespace(),['execute_code'],execute)
    agent._schema_disclosure=SimpleNamespace(_full=[{'function':{'name':'execute_code','parameters':{'required':['code'],'properties':{'code':{'type':'string'}}}}}],meta_schema=lambda:{},enabled=False)
    calls=[NativeToolCall('bad','execute_code',{'_raw':'{'}),NativeToolCall('good','execute_code',{'code':'print(1)'})]
    results=await asyncio.gather(*(agent._execute_native_tool_call(call,1,i) for i,call in enumerate(calls)))
    assert results[0]['executed'] is False and handled==['print(1)']
    messages=[];step=SimpleNamespace()
    agent._append_tool_cycle_messages(agent=agent,messages=messages,tool_results=results,assistant_content='',current_step=step)
    assert len(messages)==3 and messages[1]['tool_call_id']=='bad' and messages[2]['tool_call_id']=='good'
    assert json.loads(messages[1]['content'])['executed'] is False
    assert handled==['print(1)']


def test_restored_disclosure_and_repair_allowance_do_not_follow_new_environment():
    agent=SimpleNamespace(_schema_disclosure=SimpleNamespace(enabled=False,_force_full=False))
    policy.restore_disclosure_controls(agent,{'schema_enabled':True,'schema_force_full':True,'native_repair_cap':8192})
    assert agent._schema_disclosure.enabled and agent._schema_disclosure._force_full and agent._native_repair_cap==8192
    with pytest.raises(ValueError):policy.restore_disclosure_controls(agent,{'native_repair_cap':32768})


def test_schema_only_policy_shortens_code_docs_and_remembers_plan_tools(monkeypatch):
    from app.services.deep_think.schema_disclosure import SchemaDisclosure,PLAN_BOUND_KEEP_TOOLS
    monkeypatch.setattr(policy,'get_settings',lambda:SimpleNamespace(agent_runtime_v2_enabled=False,agent_schema_disclosure_v2_enabled=True))
    names=['execute_code',*sorted(PLAN_BOUND_KEEP_TOOLS)]
    full=[{'type':'function','function':{'name':n,'description':'full parameters here','parameters':{'type':'object'}}} for n in names]
    disclosure=SchemaDisclosure(full,names)
    first=disclosure.effective(iteration=1,tools_used=[],plan_bound=True)
    second=disclosure.effective(iteration=2,tools_used=[],plan_bound=False)
    assert {s['function']['name'] for s in first}<={s['function']['name'] for s in second}
    assert 'gagent_tools.describe' in next(s for s in first if s['function']['name']=='execute_code')['function']['description']
    assert full[0]['function']['description']=='full parameters here'
    assert disclosure.record_load('execute_code')['schema']==full[0]
