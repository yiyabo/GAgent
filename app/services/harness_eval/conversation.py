"""Public multi-turn inputs and receipts; independent answers stay in the supervisor."""
import hashlib
from pathlib import Path


def file_hash(path):
    try:return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:return None


def output_hashes(directory, names):
    result={}
    for name in names:
        path=Path(directory)/name
        if path.is_file():result[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


async def run_native_turns(client, execute, context, cfg, query, case, work):
    from app.services.deep_think_agent import DeepThinkAgent
    from app.services.execution.runtime import execution_verdict
    from app.services.execution.llm_observation import emit
    from app.services.plans.output_spec import OutputSpec, RequiredOutput
    history=[];records=[];result=None
    for index,turn in enumerate(case['turns']):
        prompt=query if index==0 else turn['prompt']+'\nWork directory: '+str(work)
        turn_context={**context,'chat_history':list(history),'user_message':prompt,
            'output_spec':OutputSpec(required_outputs=[RequiredOutput(kind='data',extensions=[Path(n).suffix],target_path=str(work/n)) for n in turn['outputs']],source='explicit').to_dict()}
        agent=DeepThinkAgent(client,['execute_code'],execute,max_iterations=cfg.native_max_iterations,tool_timeout=60,
                             request_profile={'session_id':context['session_id'],'owner_id':context['owner_id']})
        agent.enable_thinking=False;agent.thinking_budget=0
        result=await agent.think(prompt,turn_context)
        verdict=execution_verdict(None,{'output_verification':result.output_verification,'execution_issues':result.execution_issues})
        record={'index':index,'status':verdict.status,'answer':result.final_answer,'files':output_hashes(work,turn['outputs'])}
        records.append(record);emit('journey_turn',**record)
        if not verdict.completed:break
        history.extend([{'role':'user','content':prompt},{'role':'assistant','content':result.final_answer}])
    return result,records
