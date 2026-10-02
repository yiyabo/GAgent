"""One isolated production-controller/service trial. Never imports the oracle."""
import asyncio,hashlib,json,os,signal,time
from pathlib import Path
from uuid import uuid4
from dataclasses import replace
from .config import EvalSuiteConfig
from .fixtures import prepare


def configure(root:Path,cfg:EvalSuiteConfig,entry:str):
    for name in ('DB_ROOT','APP_RUNTIME_ROOT','APP_INFO_SESSIONS_ROOT','EXECUTION_WORKSPACES_ROOT','TERMINAL_AUDIT_ROOT'):
        os.environ[name]=str(root/name.lower())
    os.environ['CODE_MODE_ALLOWED_TOOLS']='file_operations,document_reader,load_skill,deliverable_submit'
    os.environ.update(cfg.feature_overrides)
    os.environ.update(DATABASE_URL='sqlite:///'+str(root/'db_root/main/plan_registry.db'),SKILL_LEARNING_ENABLED='0',QUALITY_EVALUATION_ENABLED='0',CODE_MODE_ENABLED='1',CODE_MODE_CELL_TIMEOUT_SECONDS='60',LLM_MAX_TOKENS=str(cfg.output_max_tokens),DEEP_THINK_MAX_ITERATIONS=str(cfg.native_max_iterations),QC_MAX_SESSION_TURNS=str(cfg.external_max_session_turns),PLAN_TASK_EXECUTION_BACKEND='external_agent' if entry=='plan-external' else 'internal')


async def run_trial(case_id,entry,root:Path,cfg:EvalSuiteConfig,external_remaining=None):
    from app.database import get_db,init_db
    from app.repository import chat_runs
    from app.repository.llm_usage import init_llm_usage_table
    from app.llm import LLMClient,set_usage_context,clear_usage_context
    from app.services.chat_run_state import chat_run_claim
    from app.services.cancellation import CancelToken,set_cancel_token,reset_cancel_token
    from app.services.run_budget import RunBudget,bind_run_budget,reset_run_budget
    from app.services.execution.runtime import execution_verdict
    from app.services.execution.llm_observation import observer
    from app.services.plans.output_spec import OutputSpec,RequiredOutput
    from tool_box.tools_impl.execute_code.kernel import shutdown_kernels_for_session
    init_db();init_llm_usage_table()
    identity='eval-'+uuid4().hex;worker='worker-'+identity
    with get_db() as con:
        con.execute('INSERT INTO chat_sessions(id,owner_id,name) VALUES(?,?,?)',(identity,'harness-eval',case_id));con.commit()
    from app.services.path_router import get_path_router
    work=get_path_router().get_session_dir(identity,create=True)/'raw_files'/'inputs'
    case=prepare(case_id,work)
    query=case['prompt']+'\nUse only the provided local inputs. Deliver all required files and include their links in the answer. Work directory: '+str(work)+'\nJSON values must be numbers. Group summaries use {group: {count: number, mean/median: number}}.'
    chat_runs.create_chat_run(identity,identity,json.dumps({'message':query,'session_id':identity}),owner_id='harness-eval')
    chat_runs.claim_chat_run_lease(identity,worker,ttl_seconds=cfg.trial_wall_seconds+60);chat_runs.mark_chat_run_started(identity,worker_id=worker)
    token=CancelToken();handles=[set_cancel_token(token),chat_run_claim.set((identity,worker))]
    budget=RunBudget(cfg.trial_wall_seconds,cfg.close_reserve_seconds,token);bh=bind_run_budget(budget)
    uh=set_usage_context(session_id=identity,run_id=identity,phase='evaluation',call_purpose='harness_workflow_eval')
    signal.signal(signal.SIGTERM,lambda *_:token.set('evaluation_supervisor_cancel'))
    attempts={};events=[];external_launches=0
    def observe(event):
        nonlocal external_launches
        if event['kind']=='external_launch':
            if external_launches>=min(1,cfg.external_launch_limit if external_remaining is None else external_remaining):raise RuntimeError('external_launch_limit')
            external_launches+=1
        if event['kind']=='attempt':
            key=(event['logical_call_id'],event['attempt_no'])
            if key not in attempts and len(attempts)>=cfg.provider_attempt_limit:raise RuntimeError('provider_attempt_limit')
            attempts[key]=event
        if event['kind']=='native_result':
            args=event.pop('arguments',[]);raw=json.dumps(args,ensure_ascii=False)
            filename='arguments-'+str(len(events))+'.json';(root/filename).write_text(raw)
            event['arguments_ref']=filename;event['arguments_sha256']=hashlib.sha256(raw.encode()).hexdigest();event['arguments_chars']=len(raw)
        events.append(event)
    oh=observer.set(observe)
    client=LLMClient(timeout=120,retries=0);started=time.monotonic();result=None;error=None;plan_id=task_id=None
    context={'session_id':identity,'owner_id':'harness-eval','user_message':query,'output_spec_base_dir':str(work)}
    def spec_for(directory):
        return OutputSpec(required_outputs=[RequiredOutput(kind='image' if n.endswith('.png') else 'document' if n.endswith('.md') else 'data',extensions=[Path(n).suffix],target_path=str(directory/n)) for n in case['outputs']],source='explicit').to_dict()
    try:
        if entry=='chat-native':
            from app.services.deep_think_agent import DeepThinkAgent
            from tool_box.context import ToolContext
            from tool_box.tools_impl.execute_code.tool import execute_code_handler
            async def execute(name,params):
                if name!='execute_code':return {'success':False,'error':'evaluation_tool_unavailable'}
                if not isinstance(params,dict) or not isinstance(params.get('code'),str):return {'success':False,'error':'missing_or_malformed_code_argument','executed':False}
                return await execute_code_handler(**{k:params[k] for k in ('code','reset') if k in params},tool_context=ToolContext(session_id=identity,owner_id='harness-eval',work_dir=str(work)))
            agent=DeepThinkAgent(client,['execute_code'],execute,max_iterations=cfg.native_max_iterations,tool_timeout=60,request_profile={'session_id':identity,'owner_id':'harness-eval'})
            agent.enable_thinking=False;agent.thinking_budget=0;context['output_spec']=spec_for(work)
            result=await agent.think(query,context)
            answer=result.final_answer;meta={'output_verification':result.output_verification,'execution_issues':result.execution_issues};status=None
            output_root=work
        else:
            from app.repository.plan_repository import PlanRepository
            from app.services.plans.plan_executor import PlanExecutor,ExecutionConfig
            from app.config.executor_config import get_executor_settings
            from app.services.path_router import get_path_router
            repo=PlanRepository();tree=repo.create_plan('eval '+case_id,owner='harness-eval')
            plan_id=tree.id
            node=repo.create_task(plan_id,name=case_id,instruction=query)
            task_id=node.id;output_root=get_path_router().get_task_output_dir_from_tree(identity,task_id,repo.get_plan_tree(plan_id),create=True)
            context['output_spec']=spec_for(output_root);context['output_spec_base_dir']=str(output_root)
            query+='\nRead the supplied inputs above, and write all required output files in: '+str(output_root)
            context['user_message']=query
            repo.update_task(plan_id,task_id,instruction=query,metadata={'required_outputs':context['output_spec']['required_outputs'],'output_spec':context['output_spec'],'source_inputs':{p.name:str(p) for p in work.iterdir() if p.name not in case['outputs']}})
            with get_db() as con:con.execute('UPDATE chat_sessions SET plan_id=? WHERE id=?',(plan_id,identity));con.commit()
            settings=replace(get_executor_settings(),plan_task_execution_backend='external_agent' if entry=='plan-external' else 'internal',deep_think_max_iterations=cfg.native_max_iterations,qc_max_session_turns=cfg.external_max_session_turns)
            executor=PlanExecutor(repo=repo,settings=settings)
            config=ExecutionConfig(session_context=context,max_retries=1,contract_repair_attempts=0,force_rerun=True)
            result=await asyncio.to_thread(executor.execute_task,plan_id,task_id,config=config)
            answer=result.content;meta=result.metadata;status=result.status
        verdict=execution_verdict(status,meta)
    except BaseException as exc:
        error=type(exc).__name__+': '+str(exc)[:400];answer='';meta={};output_root=work
        verdict=execution_verdict('cancelled' if token.cancelled and token.reason!='run_deadline_exceeded' else 'failed')
    finally:
        # A terminal row is closed even after cooperative cancellation; do not write tool facts here.
        chat_runs.mark_chat_run_finished(identity,verdict.status,error=error,worker_id=worker)
        chat_runs.release_chat_run_lease(identity,worker)
        shutdown_kernels_for_session(identity)
        observer.reset(oh);clear_usage_context(uh);reset_run_budget(bh);chat_run_claim.reset(handles[1]);reset_cancel_token(handles[0]);token.close()
    with get_db() as con:
        rows=[dict(r) for r in con.execute('SELECT * FROM llm_usage_log WHERE session_id=? ORDER BY id',(identity,))]
        unique={}
        for row in rows:
            key=(row['logical_call_id'],row['attempt_no']) if row.get('logical_call_id') else ('row',row['id'])
            unique[key]=row
        rows=list(unique.values())
    report=meta.get('output_verification') or {}
    paths=[str(p) for p in report.get('artifact_paths',[])]+list(meta.get('artifact_paths') or [])
    artifacts=[]
    for name in case['outputs']:
        candidates=[Path(p) for p in paths if Path(p).name==name and Path(p).is_file()]
        direct=output_root/name
        if direct.is_file():candidates.append(direct)
        for path in dict.fromkeys(candidates):
            if root not in path.resolve().parents:raise RuntimeError('artifact_outside_trial')
            artifacts.append({'name':name,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'size':path.stat().st_size})
    return {'case':case_id,'entry':entry,'entry_implementation':'native-controller' if entry=='chat-native' else 'PlanExecutor.execute_task','session_id':identity,'chat_run_id':identity,'plan_id':plan_id,'task_id':task_id,'linked_run_ids':sorted({r['run_id'] for r in rows if r.get('run_id')}),'production_status':verdict.status,'termination_reason':verdict.reason or error,'declared_verification':report,'answer':answer,'answer_completion_passed':all(n in answer for n in case['outputs']) and verdict.completed,'duration_seconds':round(time.monotonic()-started,3),'external_launches':external_launches,'provider_attempts':len(attempts),'call_events':events,'artifacts':artifacts,'output_root':str(output_root),'usage_source':('estimated' if any(e.get('usage_source')=='estimated' for e in events) else 'provider' if rows else 'missing'),'prompt_tokens':sum(r['prompt_tokens'] for r in rows),'completion_tokens':sum(r['completion_tokens'] for r in rows),'total_tokens':sum(r['total_tokens'] for r in rows),'cost_usd':None,'error':error,'cleanup_status':{'run_status':chat_runs.get_chat_run(identity)['status'],'lease_released':not chat_runs.is_chat_run_lease_live(identity)},'revision':cfg.revision,'model':client._effective_model(),'provider':client.provider}
