import csv
import hashlib
import json
import statistics
from types import SimpleNamespace

import pytest

from app.services.harness_eval.config import EvalSuiteConfig
from app.services.harness_eval.fixtures import prepare
from app.services.harness_eval.conversation import run_native_turns
from app.services.harness_eval.supervisor import score_result


@pytest.mark.asyncio
async def test_two_turn_orchestration_has_history_current_contract_and_preserved_receipts(tmp_path,monkeypatch):
    from app.services import deep_think_agent
    work=tmp_path/'workspace';case=prepare('correction_journey',work);calls=[]
    class Agent:
        def __init__(self,*args,**kwargs):pass
        async def think(self,prompt,context):
            calls.append(context)
            groups={}
            for row in csv.DictReader((work/'input.csv').open()):
                try:value=float(row['score'])
                except ValueError:continue
                groups.setdefault(row['group'],[]).append(value)
            if len(calls)==2:(work/'summary-v1.json').write_bytes((work/'summary.json').read_bytes())
            field,calc=('mean',statistics.mean) if len(calls)==1 else ('median',statistics.median)
            (work/'summary.json').write_text(json.dumps({k:{'count':len(v),field:calc(v)} for k,v in groups.items()}))
            return SimpleNamespace(final_answer='Files produced',output_verification={'authoritative':True,'status':'passed'},execution_issues=[])
    monkeypatch.setattr(deep_think_agent,'DeepThinkAgent',Agent)
    _,turns=await run_native_turns(None,None,{'session_id':'s','owner_id':'u'},EvalSuiteConfig(),case['prompt'],case,work)
    assert len(calls)==2 and len(calls[1]['chat_history'])==2
    assert 'median' in calls[1]['user_message']
    assert len(calls[0]['output_spec']['required_outputs'])==1
    assert len(calls[1]['output_spec']['required_outputs'])==2
    row={'case':'correction_journey','turn_results':turns,'input_unchanged':True,'answer_completion_passed':True,
         'production_status':'succeeded','total_tokens':0,'usage_source':'provider',
         'artifacts':[{'path':str(work/n),'name':n,'sha256':hashlib.sha256((work/n).read_bytes()).hexdigest()} for n in case['outputs']]}
    (tmp_path/'result.json').write_text(json.dumps(row))
    assert score_result(tmp_path,{'case':'correction_journey'})['passed']
    row['turn_results'][0]['files']['summary.json']='wrong-first-output'
    (tmp_path/'result.json').write_text(json.dumps(row))
    assert not score_result(tmp_path,{'case':'correction_journey'})['passed']


def test_default_suite_does_not_grow_when_optional_journeys_are_added():
    assert len(EvalSuiteConfig().schedule())==18
    with pytest.raises(ValueError):EvalSuiteConfig(cases=['correction_journey'],entries=['plan-native']).validate()


def test_hermes_second_turn_receives_real_first_turn_history(tmp_path,monkeypatch):
    from app.services.harness_eval.hermes_worker import execute
    monkeypatch.setenv('HERMES_HARNESS_BASE_URL','http://127.0.0.1:1/v1')
    calls=[];history=[{'role':'user','content':'first'},{'role':'assistant','content':'first output'}]
    class Agent:
        def __init__(self,**kwargs):pass
        def run_conversation(self,prompt,**kwargs):
            calls.append(kwargs)
            return {'completed':True,'final_response':'done','messages':history}
        def close(self):pass
    request={'source_root':'unused','model':'test','session_id':'s','toolsets':['file'], 'max_iterations':4,'max_tokens':4096,
             'active_seconds':10,'workspace':str(tmp_path),'prompt':'first','output_root':str(tmp_path),
             'turns':[{'prompt':'first','outputs':[]},{'prompt':'correct it','outputs':[]}]}
    result=execute(request,agent_factory=Agent,db_factory=lambda:None)
    assert result['completed'] and len(result['turn_results'])==2
    assert 'conversation_history' not in calls[0] and calls[1]['conversation_history']==history


@pytest.mark.asyncio
async def test_single_turn_worker_finalizes_and_serializes_without_turn_events(isolated_app_env,monkeypatch):
    from app.services import deep_think_agent,path_router
    from app.services.harness_eval.trial import run_trial
    monkeypatch.setattr(path_router,'_default_router',None)
    class Agent:
        def __init__(self,*args,**kwargs):pass
        async def think(self,prompt,context):
            from pathlib import Path
            work=Path(context['output_spec_base_dir'])
            (work/'clean.csv').write_text('id,group,score\na,A,10\nb,A,20\nc,B,30\nd,B,50\n')
            (work/'summary.json').write_text('{"A":{"count":2,"mean":15},"B":{"count":2,"mean":40}}')
            return SimpleNamespace(final_answer='clean.csv and summary.json',output_verification={'status':'passed','authoritative':True},execution_issues=[])
    monkeypatch.setattr(deep_think_agent,'DeepThinkAgent',Agent)
    result=await run_trial('table_clean','chat-native',isolated_app_env['runtime_root'].parent,EvalSuiteConfig())
    assert result['turn_results']==[] and result['production_status']=='succeeded'
    assert result['cleanup_status']['lease_released'] and len(result['artifacts'])==2
