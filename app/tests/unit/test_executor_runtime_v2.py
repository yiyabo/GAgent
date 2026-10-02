from types import SimpleNamespace
import pytest
from app.services.deep_think import native_validation as nv
from app.llm import NativeStreamResult,NativeToolCall


def test_argument_barrier_distinguishes_errors_and_never_executes(monkeypatch):
    monkeypatch.setattr(nv,'get_settings',lambda:SimpleNamespace(agent_runtime_v2_enabled=True))
    agent=SimpleNamespace(_schema_disclosure=SimpleNamespace(_full=[{'function':{'name':'write','parameters':{'required':['code'],'properties':{'code':{'type':'string'}}}}}],meta_schema=lambda:{}),_last_native_finish_reason='stop')
    assert nv.validate({}, {}) is None
    for args,reason in [([], 'non_object'),({'_raw':'{'},'invalid_json'),({},'missing_required'),({'code':7},'invalid_type')]:
        r=nv.rejected_call(agent,NativeToolCall('c','write',args),1,0)
        assert r['executed'] is False and r['error']==reason
    assert nv.rejected_call(agent,NativeToolCall('c','write',{'code':'print(1)'}),1,0) is None
    nv.observe_result(agent,NativeStreamResult(tool_calls=[NativeToolCall('c','write',{'_raw':'{'})],finish_reason='length'))
    assert nv.next_call_options(agent)=={'max_tokens':8192}
    nv.observe_result(agent,NativeStreamResult(tool_calls=[NativeToolCall('c','write',{})],finish_reason='stop'))
    assert nv.next_call_options(agent)=={}


async def test_whole_request_budget_and_nonshrinking_summary():
    from app.services.context.context_manager import ContextWindowManager
    from app.services.context.request_budget import ContextBudgetExceeded
    mgr=ContextWindowManager(max_context_tokens=100)
    with pytest.raises(ContextBudgetExceeded):await mgr.compact_if_needed([{'role':'user','content':'hello'}],summarizer=lambda _:None,tool_schemas=[{'description':'x'*1000}],output_reserve_tokens=20)
    usage=mgr.check_usage([{'role':'user','content':'a'}],tool_schemas=[{'name':'tool'}],output_reserve_tokens=20)
    assert usage.breakdown['output_reserve']==20 and usage.breakdown['tool_schemas']>0


def test_soft_window_does_not_cancel_or_extend(monkeypatch):
    from app.services.run_budget import RunBudget
    from app.services.foundation import settings
    from app.services.cancellation import CancelToken
    monkeypatch.setattr(settings,'get_settings',lambda:SimpleNamespace(chat_run_synthesis_reserve_seconds=120))
    token=CancelToken();budget=RunBudget(600,10,token)
    deadline=budget.deadline_at
    budget.started_at-=500
    assert budget.should_finalize() and not token.cancelled
    assert budget.deadline_at==deadline-500
    token.close()


def test_code_introspection_is_local_and_schema_driven():
    from tool_box.tools_impl.execute_code.stub_gen import generate_stub_module
    namespace={};exec(generate_stub_module(['file_operations']),namespace)
    assert namespace['list_tools']()==['file_operations']
    assert 'parameters' in namespace['describe']('file_operations')


def test_plan_tool_offer_and_execution_sets_agree(monkeypatch):
    from app.services.plans.executor_deepthink import _execution_tools
    from app.services import tool_schemas
    monkeypatch.setattr(nv,'get_settings',lambda:SimpleNamespace(agent_runtime_v2_enabled=True))
    monkeypatch.setattr(tool_schemas,'code_mode_enabled',lambda:True)
    assert {'execute_code','load_skill'}.issubset(_execution_tools(['file_operations']))


def test_planner_outputs_are_precise_declarations_not_inferred_success():
    from app.services.plans.plan_decomposer import PlanDecomposer
    from app.services.plans.output_spec import parse_output_spec
    child=SimpleNamespace(context_meta={},name='Prepare a table',instruction='Write results/table.csv',metadata={'required_outputs':[{'kind':'data','extensions':['.csv'],'min_count':1,'target_path':'results/table.csv'}]})
    planner=PlanDecomposer.__new__(PlanDecomposer)
    metadata=planner._derive_paper_metadata(child)
    spec=parse_output_spec(metadata['output_spec'],strict=True)
    assert spec.authoritative and spec.required_outputs[0].target_path=='results/table.csv'
