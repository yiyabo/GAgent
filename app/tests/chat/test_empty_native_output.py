"""Confirmed empty truncations terminate without an unbounded fallback chain."""
from types import SimpleNamespace

import pytest

from app.llm import NativeStreamResult
from app.services.deep_think import native_validation as validation
from app.services.deep_think.models import ThinkingStep


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setattr(validation, 'get_settings', lambda: SimpleNamespace(agent_runtime_v2_enabled=False))
    return SimpleNamespace(_runtime_policy={'arguments': True, 'schemas': False, 'receipts': False},
                           request_profile={}, on_thinking=None)


async def test_only_consecutive_confirmed_empty_truncations_stop(agent):
    empty = NativeStreamResult(content='', tool_calls=[], finish_reason='length')
    steps = []
    for iteration in (1, 2):
        validation.observe_result(agent, empty)
        stopped = await validation.stop_empty_truncation(agent, ThinkingStep(iteration, '', None, None, None), steps)
        assert stopped is (iteration == 2)
    assert len(steps) == 1
    assert 'truncated' in steps[0].self_correction
    assert validation.bounded_synthesis(agent)
    assert agent._native_repair_cap is None  # no incomplete tool arguments to replay


async def test_content_resets_streak_and_incomplete_stream_is_not_truncation(agent):
    validation.observe_result(agent, NativeStreamResult(content='', finish_reason='length'))
    validation.observe_result(agent, NativeStreamResult(content='A usable answer.', finish_reason='stop'))
    validation.observe_result(agent, NativeStreamResult(content='', finish_reason=None, diagnostics={'done_seen': False}))
    assert not await validation.stop_empty_truncation(agent, ThinkingStep(3, '', None, None, None), [])


async def test_plan_synthesis_failure_uses_one_model_call_when_runtime_v2_is_off(monkeypatch):
    from app.services.deep_think_agent import DeepThinkAgent
    from app.services.deep_think import synthesis
    monkeypatch.setattr(validation, 'get_settings', lambda: SimpleNamespace(agent_runtime_v2_enabled=False))
    calls = []

    async def text(*args, **kwargs):
        calls.append(kwargs)
        return ''

    instance = DeepThinkAgent(SimpleNamespace(), [], text,
        request_profile={'plan_budget_enabled': True, 'intent_type': 'execute_task'})
    monkeypatch.setattr(instance, '_chat_messages_streaming', text)
    steps = [ThinkingStep(iteration=1, thought='Tool input is unavailable.', action='file_operations', action_result='not found', self_correction=None)]
    result = await synthesis._forced_synthesis_from_steps(instance, steps, 'Finish the task', [])
    assert result == ''
    fallback = await synthesis._fallback_answer_from_steps(instance, steps, 'Finish the task')
    assert fallback
    assert len(calls) == 1
    assert calls[0]['max_tokens'] == 16384
