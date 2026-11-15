"""Native-loop plan repair: soft hints retry once, then keep the answer."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.deep_think.controller import _native_final_call_cycle
from app.services.deep_think.iteration_state import _NativeCycleState
from app.services.deep_think.models import ThinkingStep

ANSWER = "这里是研究方案正文。"


def _agent(outcome: dict[str, Any]) -> Any:
    return SimpleNamespace(
        on_thinking=None,
        on_final_delta=None,
        _is_execute_task_request=lambda: False,
        _has_bound_task_context=lambda _ctx: False,
        _looks_like_blocked_dependency_answer=lambda _text: False,
        _explicit_task_override_active=lambda _ctx: False,
        _is_valid_final_answer=lambda text, user_query="": bool(str(text).strip()),
        _summarize_structured_plan_outcome=lambda _steps, user_query="": dict(outcome),
        _needs_directory_profile_before_final=lambda **_: None,
        _needs_phagescope_deep_profile_before_final=lambda **_: None,
        _get_structured_plan_retry_prompt=lambda: "请先调用 plan_operation create。",
    )


def _step() -> ThinkingStep:
    return ThinkingStep(iteration=1, thought="answer", action=None, action_result=None, self_correction=None)


async def _run(agent: Any, cycle: _NativeCycleState) -> tuple[str, str]:
    steps: list[ThinkingStep] = []
    messages: list[dict[str, Any]] = []
    flow, answer = await _native_final_call_cycle(
        agent,
        result=SimpleNamespace(content="assistant final"),
        final_call=SimpleNamespace(arguments={"answer": ANSWER, "confidence": 0.9}, name="submit_final_answer"),
        iteration=1,
        current_step=_step(),
        thinking_steps=steps,
        tools_used=[],
        messages=messages,
        task_context=None,
        user_query="制定完整的研究计划吧",
        cycle=cycle,
    )
    assert messages == (
        [{"role": "assistant", "content": "assistant final"},
         {"role": "user", "content": "请先调用 plan_operation create。"}]
        if flow == "continue" else []
    )
    return flow, answer


@pytest.mark.asyncio
async def test_soft_plan_hint_retries_once_then_accepts_answer() -> None:
    outcome = {"required": True, "satisfied": False, "hard": False, "state": "not_called"}
    agent = _agent(outcome)
    cycle = _NativeCycleState()

    flow, answer = await _run(agent, cycle)
    assert flow == "continue" and answer == ""
    assert cycle.structured_plan_soft_retries == 1

    flow, answer = await _run(agent, cycle)
    assert flow == "break" and answer == ANSWER
    assert cycle.structured_plan_soft_retries == 1


@pytest.mark.asyncio
async def test_hard_plan_contract_keeps_retrying() -> None:
    outcome = {"required": True, "satisfied": False, "hard": True, "state": "not_called"}
    agent = _agent(outcome)
    cycle = _NativeCycleState(structured_plan_soft_retries=1)

    flow, answer = await _run(agent, cycle)
    assert flow == "continue" and answer == ""
    assert cycle.structured_plan_soft_retries == 1


@pytest.mark.asyncio
async def test_satisfied_plan_accepts_answer_immediately() -> None:
    agent = _agent({"required": True, "satisfied": True, "hard": False, "state": "satisfied"})
    flow, answer = await _run(agent, _NativeCycleState())
    assert flow == "break" and answer == ANSWER
