"""Response checks never manufacture execution or guess incomplete arguments."""
import hashlib,json
from app.services.foundation.settings import get_settings
from .runtime_policy import policy_for


def enabled():return bool(getattr(get_settings(),'agent_runtime_v2_enabled',False))


def validate(arguments,schema):
    if not isinstance(arguments,dict):return {'error_code':'non_object','executed':False}
    if '_raw' in arguments and '_raw' not in schema.get('properties',{}):return {'error_code':'invalid_json','executed':False}
    missing=[k for k in schema.get('required',[]) if k not in arguments]
    if missing:return {'error_code':'missing_required','missing_fields':missing,'executed':False}
    kinds={'string':str,'object':dict,'array':list,'boolean':bool,'integer':int,'number':(int,float)}
    for name,value in arguments.items():
        field=schema.get('properties',{}).get(name,{})
        kind=field.get('type');expected=kinds.get(kind)
        if expected and (not isinstance(value,expected) or (kind in {'integer','number'} and isinstance(value,bool))):return {'error_code':'invalid_type','field':name,'executed':False}
        if field.get('enum') and value not in field['enum']:return {'error_code':'invalid_enum','field':name,'executed':False}
    return None


def schema_for(agent,name):
    disclosure=getattr(agent,'_schema_disclosure',None)
    schemas=list(getattr(disclosure,'_full',[]) or [])
    if disclosure:schemas.append(disclosure.meta_schema())
    for s in schemas:
        if s.get('function',{}).get('name')==name:return s['function'].get('parameters',{})
    return {}


def rejected_call(agent,call,iteration,index):
    if not policy_for(agent, get_settings())['arguments']:return None
    error=validate(call.arguments,schema_for(agent,call.name))
    if not error:return None
    finish=getattr(agent,'_last_native_finish_reason',None)
    incomplete=error['error_code'] in {'invalid_json','missing_required'}
    reason='output_truncated' if finish=='length' and incomplete else 'stream_incomplete' if finish is None and getattr(agent,'_last_native_done_seen',True) is False else error['error_code']
    payload={'success':False,**error,'error_code':reason,'error':reason}
    params=call.arguments if isinstance(call.arguments,dict) else {'_raw':json.dumps(call.arguments,ensure_ascii=False)}
    return {'index':index,'tool_call_id':call.id or f'native_{iteration}_{index}','tool_name':call.name,
            'parameters':call.arguments,'tool_params':params,'success':False,'error':reason,'result':payload,
            'tool_result':payload,'tool_result_text':json.dumps(payload,ensure_ascii=False),'evidence':[],
            'summary':reason,'executed':False}


def next_call_options(agent):
    cap=getattr(agent,'_native_repair_cap',None)
    return {'max_tokens':cap} if cap else {}


def observe_result(agent,result):
    agent._last_native_finish_reason=getattr(result,"finish_reason",None)
    diagnostics=getattr(result,"diagnostics",None) or {}
    agent._last_native_done_seen=diagnostics.get("done_seen",True) or diagnostics.get('repair_complete',False)
    if not policy_for(agent, get_settings())['arguments']:return
    invalid=False
    for call in result.tool_calls:
        error=validate(call.arguments,schema_for(agent,call.name))
        call.argument_status=error['error_code'] if error else 'valid'
        raw=json.dumps(call.arguments,ensure_ascii=False)
        call.argument_chars=len(raw);call.argument_sha256=hashlib.sha256(raw.encode()).hexdigest()
        if error and not isinstance(call.arguments,dict):call.arguments={'_raw':raw}
        invalid=invalid or bool(error and error['error_code'] in {'invalid_json','missing_required'})
    count=getattr(agent,'_native_cap_escalations',0)
    if invalid and getattr(result,'finish_reason',None)=='length' and count<2:
        agent._native_repair_cap=8192;agent._native_cap_escalations=count+1
    else:agent._native_repair_cap=None


class ToolCallRepair(list):
    def __init__(self,calls,response):
        super().__init__(calls);self.response=response


def invalid_call(agent,call):
    return policy_for(agent, get_settings())['arguments'] and validate(call.arguments,schema_for(agent,call.name)) is not None


def request_kwargs(agent,user_query,context,messages,iteration,tools_used):
    if not enabled():
        cap=getattr(agent,'_native_repair_cap',None)
        return {'output_reserve_tokens':cap} if cap else {}
    from app.llm import _default_max_tokens
    from app.services.skill_learning.context import format_skill_context
    schemas=agent._schema_disclosure.effective(iteration=iteration+1,tools_used=tools_used,plan_bound=agent._current_plan_id() is not None)
    from app.services.memory.context_recall import format_recall_context
    return {'component_texts':{'skills':format_skill_context(context),'recall':format_recall_context(context)},'tool_schemas':schemas,'output_reserve_tokens':getattr(agent,'_native_repair_cap',None) or _default_max_tokens(),
        'anchors':{'request':user_query,'output_spec':str(getattr(agent,'_output_spec',None)),'profile':agent.request_profile,'latest_user_turns':[m.get('content') for m in messages if m.get('role')=='user'][-2:]}}


async def compact_strict(agent,messages,user_query):
    if not enabled():return messages
    from app.services.context.context_manager import ContextWindowManager
    from app.services.run_budget import run_work_stage
    from app.llm import _default_max_tokens
    manager=getattr(agent,'_strict_context_manager',None)
    if manager is None:
        manager=ContextWindowManager(model=getattr(agent.llm_client,'model',''))
        agent._strict_context_manager=manager
    async def summarize(text):
        return await run_work_stage(agent.llm_client.chat_async(prompt='Summarize execution facts and preserve source references:\n'+text,max_tokens=1200),stage='strict-compaction')
    return await manager.compact_if_needed(messages,summarizer=summarize,output_reserve_tokens=_default_max_tokens(),anchors={'request':user_query,'profile':agent.request_profile,'latest_user_turns':[m.get('content') for m in messages if m.get('role')=='user'][-2:]})
