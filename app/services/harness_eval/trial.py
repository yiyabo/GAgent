"""One isolated production-controller/service trial. Never imports the oracle."""
import asyncio,hashlib,json,os,signal,time
from pathlib import Path
from uuid import uuid4
from dataclasses import replace
from .config import EvalSuiteConfig
from .fixtures import prepare
from .accounting import TrialAccounting
from .conversation import file_hash


def configure(root:Path,cfg:EvalSuiteConfig,entry:str):
    if cfg.campaign_root:
        # This function runs only inside a fresh trial worker. Give its CLI
        # children a real isolated home so personal QWEN.md, plugins and saved
        # sessions cannot change the comparison or receive evaluation writes.
        trial_home = root / 'home'
        trial_home.mkdir(parents=True, exist_ok=True)
        os.environ.update(HOME=str(trial_home), XDG_CONFIG_HOME=str(trial_home / '.config'),
                          XDG_CACHE_HOME=str(trial_home / '.cache'),
                          QWEN_RUNTIME_DIR=str(trial_home / '.qwen'))
    for name in ('DB_ROOT','APP_RUNTIME_ROOT','APP_INFO_SESSIONS_ROOT','EXECUTION_WORKSPACES_ROOT','TERMINAL_AUDIT_ROOT'):
        os.environ[name]=str(root/name.lower())
    os.environ['CODE_MODE_ALLOWED_TOOLS']='file_operations,document_reader,load_skill,deliverable_submit'
    os.environ.update({key:'0' for key in ('AGENT_RUNTIME_V2_ENABLED','ARTIFACT_VERSIONING_ENABLED','SKILL_RECOMMENDATION_V2_ENABLED','SKILL_CONTEXT_PROGRESSIVE_ENABLED','CHAT_RUN_SYNTHESIS_RESERVE_SECONDS')})
    for key in ('AGENT_ARGUMENT_VALIDATION_ENABLED','AGENT_SCHEMA_DISCLOSURE_V2_ENABLED'):
        os.environ.pop(key, None)  # inherit the explicitly frozen umbrella unless overridden
    os.environ['AGENT_TOOL_RECEIPT_COMPACTION_ENABLED']='0'
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
    # A trial is a virtual project: configure the existing publisher dependency
    # identically for both revisions rather than weakening host path policies.
    from app.services.deliverables import publisher as publisher_module
    publisher_module._publisher=publisher_module.DeliverablePublisher(project_root=root,runtime_dir=root/"app_runtime_root")
    identity='eval-'+uuid4().hex;worker='worker-'+identity
    with get_db() as con:
        con.execute('INSERT INTO chat_sessions(id,owner_id,name) VALUES(?,?,?)',(identity,'harness-eval',case_id));con.commit()
    from app.services.path_router import get_path_router
    work=get_path_router().get_session_dir(identity,create=True)/'raw_files'/'inputs'
    case=prepare(case_id,work)
    input_hash_before=file_hash(work/'input.csv') if case.get('turns') else None
    if cfg.skills_arm is not None:
        from .skill_fixtures import seed
        seed(identity)
        if case_id=='skill_reuse':
            (work/'SKILL.md').unlink(missing_ok=True)
            case={**case,'prompt':CASES_NATURAL_CLEANING}

    query=case['prompt']+'\nUse only the provided local inputs. Deliver all required files and include their links in the answer. Work directory: '+str(work)+'\nJSON values must be numbers. Group summaries use {group: {count: number, mean/median: number}}.'
    chat_runs.create_chat_run(identity,identity,json.dumps({'message':query,'session_id':identity}),owner_id='harness-eval')
    chat_runs.claim_chat_run_lease(identity,worker,ttl_seconds=cfg.trial_wall_seconds+60);chat_runs.mark_chat_run_started(identity,worker_id=worker)
    token=CancelToken();handles=[set_cancel_token(token),chat_run_claim.set((identity,worker))]
    total_seconds=cfg.trial_wall_seconds+(cfg.close_reserve_seconds if cfg.campaign_root else 0)
    budget=RunBudget(total_seconds,cfg.close_reserve_seconds,token);bh=bind_run_budget(budget)
    uh=set_usage_context(session_id=identity,run_id=identity,phase='evaluation',call_purpose='harness_workflow_eval')
    signal.signal(signal.SIGTERM,lambda *_:token.set('evaluation_supervisor_cancel'))
    accounting=TrialAccounting(root,cfg,external_remaining)
    accounting.observe({'kind':'trial_started','session_id':identity,'worker_id':worker})
    oh=observer.set(accounting.observe)
    client=LLMClient(timeout=120,retries=0);started=time.monotonic();result=None;error=None;plan_id=task_id=None;turn_results=[]
    context={'learned_skills_disabled':cfg.skills_arm=='none','session_id':identity,'owner_id':'harness-eval','user_message':query,'output_spec_base_dir':str(work)}
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
            if case.get('turns'):
                from .conversation import run_native_turns
                result,turn_results=await run_native_turns(client,execute,context,cfg,query,case,work)
            else:result=await agent.think(query,context)
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
    accounting.reconcile_rows(rows)
    accounting.observe({'kind':'trial_finished','status':verdict.status,'error':error})
    metering=accounting.summary()
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
    exposures=[];deliveries=[]
    if cfg.skills_arm is not None:
      with get_db() as con:
        exposures=[dict(r) for r in con.execute('SELECT skill_id,version,rank,retrieval_mode FROM learned_skill_exposures WHERE run_id=?',(identity,))]
        deliveries=[dict(r) for r in con.execute('SELECT skill_id,version,delivery,status FROM learned_skill_usage WHERE run_id=?',(identity,))]
    return {'input_unchanged':file_hash(work/'input.csv')==input_hash_before if input_hash_before else None,'turn_results':turn_results or [e for e in events if e['kind']=='journey_turn'],'skill_exposures':exposures,'skill_deliveries':deliveries,'skills_arm':cfg.skills_arm,'case':case_id,'entry':entry,'entry_implementation':'native-controller' if entry=='chat-native' else 'PlanExecutor.execute_task','session_id':identity,'chat_run_id':identity,'plan_id':plan_id,'task_id':task_id,'linked_run_ids':sorted({r['run_id'] for r in rows if r.get('run_id')}),'production_status':verdict.status,'termination_reason':verdict.reason or error,'declared_verification':report,'answer':answer,'answer_completion_passed':all(n in answer for n in case['outputs']) and verdict.completed,'duration_seconds':round(time.monotonic()-started,3),'call_events':accounting.events,'artifacts':artifacts,'output_root':str(output_root),**metering,'cost_usd':None,'error':error,'cleanup_status':{'run_status':chat_runs.get_chat_run(identity)['status'],'lease_released':not chat_runs.is_chat_run_lease_live(identity)},'revision':cfg.revision,'model':client._effective_model(),'provider':client.provider}


CASES_NATURAL_CLEANING="Read input.csv, discard missing or non-numeric scores and duplicate IDs keeping first. Write clean.csv and summary.json with group count and mean."
