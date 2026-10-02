"""Isolated real-model workflow trials against the production native controller."""
from __future__ import annotations
import asyncio
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4
from .corpus import CASES,CORPUS_VERSION,prepare,check


class MeteredLLM:
    def __init__(self,client):self.client=client;self.calls=0
    def __getattr__(self,name):return getattr(self.client,name)
    async def stream_chat_with_tools_async(self,**kwargs):
        self.calls+=1
        if self.calls>5:raise RuntimeError('evaluation provider call limit reached')
        return await self.client.stream_chat_with_tools_async(**kwargs)
    async def stream_chat_async(self,*args,**kwargs):
        self.calls+=1
        if self.calls>5:raise RuntimeError('evaluation provider call limit reached')
        async for piece in self.client.stream_chat_async(*args,**kwargs):yield piece


async def run_case(case_id:str,root:Path,*,entry:str='chat-native',timeout:float=120)->dict:
    from app.database import get_db
    from app.repository import chat_runs
    from app.llm import LLMClient,set_usage_context,clear_usage_context
    from app.services.deep_think_agent import DeepThinkAgent,TaskExecutionContext
    from app.services.chat_run_state import chat_run_claim
    from app.services.cancellation import CancelToken,set_cancel_token,reset_cancel_token
    from app.services.run_budget import RunBudget,bind_run_budget,reset_run_budget
    from tool_box.context import ToolContext
    from tool_box.tools_impl.execute_code.tool import execute_code_handler
    from tool_box.tools_impl.execute_code.kernel import shutdown_kernels_for_session
    from app.services.plans.output_spec import OutputSpec,RequiredOutput
    identity='eval-'+uuid4().hex
    case=prepare(case_id,root)
    with get_db() as conn:
        conn.execute('INSERT INTO chat_sessions(id,owner_id,name) VALUES(?,?,?)',(identity,'harness-eval',case_id));conn.commit()
    query=(case['prompt']+'\nUse execute_code to inspect the provided inputs and produce the required files. '
           'Do not use remote services. Work only in this directory: '+str(root.resolve())+
           '\nJSON values must be numbers. Group summaries must use {group: {count: number, mean/median: number}}.')
    request={'message':query,'session_id':identity}
    chat_runs.create_chat_run(identity,identity,json.dumps(request),owner_id='harness-eval')
    worker='eval-worker-'+identity
    chat_runs.claim_chat_run_lease(identity,worker,ttl_seconds=timeout+90);chat_runs.mark_chat_run_started(identity,worker_id=worker)
    spec=OutputSpec(required_outputs=[RequiredOutput(kind='image' if name.endswith('.png') else 'document' if name.endswith('.md') else 'data',
                          extensions=[Path(name).suffix],target_path=str(root/name)) for name in case['outputs']],source='explicit')
    token=CancelToken();cancel_handle=set_cancel_token(token);claim_handle=chat_run_claim.set((identity,worker))
    budget=RunBudget(timeout,5,token);budget_handle=bind_run_budget(budget)
    usage_handle=set_usage_context(session_id=identity,run_id=identity,phase='evaluation',call_purpose='harness_workflow_eval')
    client=MeteredLLM(LLMClient(timeout=40,retries=0))
    tool_calls=[]
    async def execute(name,params):
        tool_calls.append(name)
        if name!='execute_code':return {'success':False,'error':'evaluation permits execute_code only'}
        if not isinstance(params.get('code'),str):return {'success':False,'error':'missing_or_malformed_code_argument'}
        body={key:params[key] for key in ('code','reset') if key in params}
        return await execute_code_handler(**body,tool_context=ToolContext(session_id=identity,owner_id='harness-eval',work_dir=str(root)))
    agent=DeepThinkAgent(client,['execute_code'],execute,max_iterations=4,tool_timeout=30,
                         request_profile={'session_id':identity,'owner_id':'harness-eval'})
    agent.enable_thinking=False
    agent.thinking_budget=0
    context={'session_id':identity,'output_spec':spec.to_dict(),'output_spec_base_dir':str(root),'user_message':query}
    task=TaskExecutionContext(task_id=1,task_name=case_id,task_instruction=query,output_spec=spec.to_dict()) if entry=='plan-native' else None
    started=time.perf_counter();error=None;result=None
    try:result=await asyncio.wait_for(agent.think(query,context,task_context=task),timeout=timeout+5)
    except Exception as exc:error=type(exc).__name__+': '+str(exc)[:300]
    finally:
        token.close();reset_cancel_token(cancel_handle);chat_run_claim.reset(claim_handle);reset_run_budget(budget_handle);clear_usage_context(usage_handle)
        shutdown_kernels_for_session(identity)
    verdict=check(case_id,root)
    with get_db() as conn:
        rows=conn.execute('SELECT prompt_tokens,completion_tokens,total_tokens FROM llm_usage_log WHERE run_id=?',(identity,)).fetchall()
    answer=str(getattr(result,'final_answer','') or '')
    answer_completed=all(name in answer for name in case['outputs'])
    return {'case':case_id,'entry':entry,'corpus_version':CORPUS_VERSION,'passed':verdict['passed'] and error is None and answer_completed,'delivery_passed':verdict['passed'],'answer_completion_passed':answer_completed,
            'oracle':verdict,'duration_seconds':round(time.perf_counter()-started,3),'provider_calls':client.calls,
            'tool_calls':tool_calls,'prompt_tokens':sum(row[0] for row in rows),'completion_tokens':sum(row[1] for row in rows),
            'total_tokens':sum(row[2] for row in rows),'cost_usd':None,'error':error,
            'controller_verification':getattr(result,'output_verification',None) if result else None,
            'answer_excerpt':str(getattr(result,'final_answer','') or '')[:500]}
