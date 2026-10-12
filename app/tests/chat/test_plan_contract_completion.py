"""Live journey regressions: negative intent and honest structured completion."""
from types import SimpleNamespace
import pytest
from app.routers.chat.request_routing import resolve_request_routing
from app.routers.chat.response_metadata import _build_deep_think_response_metadata
from app.services.deep_think.models import DeepThinkResult, structured_plan_contract_failed
from app.services.llm import decomposer_service


@pytest.mark.parametrize('message',[
    '先检查这份数据。现在只诊断，不生成计划，不联网。',
    '现在不构建计划树，先读取数据。',
    '请汇总已有结果，不新建计划。',
    'Do not generate a plan. Inspect the file only.',
    'Inspect the file without creating any plan.',
])
def test_negated_creation_does_not_demand_plan_tool(message):
    decision = resolve_request_routing(message=message)
    assert not decision.plan_create_required
    assert 'plan_create_request' not in decision.route_reason_codes


def test_creation_with_deferred_execution_still_requires_real_plan():
    decision = resolve_request_routing(message='请生成计划树，先不执行计划。')
    assert decision.plan_create_required and not decision.plan_execute_required


@pytest.mark.parametrize('state,satisfied,expected',[
    ('not_called',False,'failed'),('failed',False,'failed'),
    ('created',True,'completed'),('confirmation_required',False,'completed'),
])
def test_plan_contract_failure_is_propagated_into_terminal_metadata(state,satisfied,expected):
    result=DeepThinkResult('result',[],1,[],.5,'summary',structured_plan_required=True,
        structured_plan_satisfied=satisfied,structured_plan_state=state)
    metadata=_build_deep_think_response_metadata(result=result,routing_metadata={},plan_id=None,
        plan_title=None,reasoning_language='en',thinking_visible=True,progress_visible=False)
    assert metadata['status']==expected
    assert structured_plan_contract_failed(result)==(expected=='failed')
    if expected=='failed':assert metadata['failure_kind']=='structured_plan_contract_mismatch'


@pytest.mark.parametrize('thinking',[True,False])
def test_plan_decomposer_and_material_decision_honor_effective_thinking(monkeypatch,thinking):
    requests=[]
    def stream(prompt,**kwargs):
        requests.append(kwargs)
        yield '{"target_node_id":1,"mode":"leaf","is_leaf":true,"children":[]}'
    monkeypatch.setattr(decomposer_service,'get_settings',lambda:SimpleNamespace(thinking_enabled=thinking))
    service=decomposer_service.PlanDecomposerLLMService(llm=SimpleNamespace(stream_chat=stream),
        settings=SimpleNamespace(model='model'))
    service.generate('decompose')
    service.decide_search('decide')
    assert len(requests)==2 and all(r['enable_thinking'] is thinking for r in requests)
