"""Run deadlines, thread inheritance, cancellation, and producer teardown."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.services import cancellation
from app.services.cancellation import CancelToken
from app.services.run_budget import (
    DEADLINE_REASON, RunBudget, RunDeadlineExceeded, bind_run_budget,
    configured_run_budget, current_run_budget, iterate_stage, reset_run_budget, run_stage,
)


@contextmanager
def budget_scope(seconds=0.1, reserve=0.02):
    token = CancelToken()
    budget = RunBudget(seconds, reserve, token)
    cancel_handle = cancellation.set_cancel_token(token)
    budget_handle = bind_run_budget(budget)
    try:
        yield budget
    finally:
        reset_run_budget(budget_handle)
        cancellation.reset_cancel_token(cancel_handle)


def test_config_preserves_legacy_budget_and_explicit_disable(monkeypatch):
    monkeypatch.delenv("CHAT_RUN_BUDGET_SECONDS", raising=False)
    monkeypatch.setenv("DEEP_THINK_TIME_BUDGET_BREAK", "2400")
    assert configured_run_budget(CancelToken()).total_seconds == 2400
    monkeypatch.setenv("CHAT_RUN_BUDGET_SECONDS", "0")
    assert configured_run_budget(CancelToken()) is None
    monkeypatch.setenv("CHAT_RUN_BUDGET_SECONDS", "3600")
    assert configured_run_budget(CancelToken()).total_seconds == 3600
    for invalid in ("-1", "nan", "inf"):
        monkeypatch.setenv("CHAT_RUN_BUDGET_SECONDS", invalid)
        assert configured_run_budget(CancelToken()).total_seconds == 900


def test_direct_budget_binding_exposes_shared_token_to_thread_consumers():
    budget = RunBudget(1, 0.1)
    handle = bind_run_budget(budget)
    try:
        assert cancellation.current_cancel_token() is budget.cancel_token
        nested = RunBudget(5, 0.1)
        assert nested.cancel_token is budget.cancel_token
        assert nested.deadline_at <= budget.deadline_at
    finally:
        reset_run_budget(handle)


async def test_deadline_interrupts_active_work_and_joins_it():
    settled = asyncio.Event()

    async def work():
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            settled.set()

    with budget_scope() as budget:
        with pytest.raises(RunDeadlineExceeded):
            await run_stage(work(), stage="hanging-tool", timeout=7200)
        assert settled.is_set()
        assert budget.cancel_token.reason == DEADLINE_REASON
        assert budget.remaining_seconds(closeout=True) > 0


async def test_thread_wait_observes_deadline_without_event_loop_supervisor():
    with budget_scope() as budget:
        assert await asyncio.to_thread(budget.cancel_token.wait, 60)
        assert budget.cancel_token.reason == DEADLINE_REASON


async def test_user_cancel_is_distinct_from_deadline():
    with budget_scope(1, 0.1) as budget:
        async def cancel():
            await asyncio.sleep(0.01)
            budget.cancel_token.set("chat_run_cancelled")

        controller = asyncio.create_task(cancel())
        with pytest.raises(asyncio.CancelledError):
            await run_stage(asyncio.Event().wait(), stage="llm")
        await controller
        assert budget.cancel_token.reason == "chat_run_cancelled"


@pytest.mark.parametrize("cap", [0.01, 0])
async def test_stage_cap_is_not_a_run_deadline(cap):
    with budget_scope(1, 0.1) as budget:
        with pytest.raises(asyncio.TimeoutError):
            await run_stage(asyncio.Event().wait(), stage="short-tool", timeout=cap)
        assert not budget.cancel_token.cancelled


async def test_paused_time_counts_and_pause_is_cancel_aware():
    from app.services.deep_think_agent import DeepThinkAgent

    calls = []

    class LLM:
        async def stream_chat_with_tools_async(self, **kwargs):
            calls.append(True)
            return None

    agent = DeepThinkAgent(LLM(), [], lambda *args: None)
    agent.pause()
    with budget_scope() as budget:
        with pytest.raises(RunDeadlineExceeded):
            await agent._think_native("hello")
        assert budget.cancel_token.reason == DEADLINE_REASON
        assert calls == []

    event = asyncio.Event()
    agent.cancel_event = event
    event.set()
    with pytest.raises(asyncio.CancelledError):
        await agent._think_native("hello")
    assert calls == []


async def test_active_native_llm_deadline_is_not_retried():
    from app.services.deep_think.controller import _native_llm_step
    from app.services.deep_think_agent import DeepThinkAgent, ThinkingStep

    settled = asyncio.Event()
    calls = []

    class LLM:
        async def stream_chat_with_tools_async(self, **kwargs):
            calls.append(True)
            try:
                await asyncio.Event().wait()
            finally:
                settled.set()

    agent = DeepThinkAgent(LLM(), [], lambda *args: None)
    step = ThinkingStep(1, "", None, None, None)
    with budget_scope():
        with pytest.raises(RunDeadlineExceeded):
            await _native_llm_step(
                agent, messages=[], tool_schemas=[], iteration=1, current_step=step,
                thinking_steps=[], consecutive_llm_failures=0, max_consecutive_llm_failures=3,
            )
    assert calls == [True] and settled.is_set()


async def test_provider_iterator_is_closed_on_deadline():
    closed = asyncio.Event()

    async def provider():
        try:
            yield "first"
            await asyncio.Event().wait()
        finally:
            closed.set()

    with budget_scope():
        with pytest.raises(RunDeadlineExceeded):
            async for _ in iterate_stage(provider(), stage="prompt-stream"):
                pass
    assert closed.is_set()


async def test_hanging_provider_close_cannot_extend_total_budget():
    cleanup = asyncio.Event()

    class Provider:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()

        async def aclose(self):
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.set()

    with budget_scope(0.16, 0.06):
        with pytest.raises(RunDeadlineExceeded):
            async for _ in iterate_stage(Provider(), stage="hanging-close"):
                pass
    assert cleanup.is_set()


async def test_nested_thread_sync_bridge_shares_deadline_and_all_context(monkeypatch):
    import tool_box
    from app.services.chat_run_state import chat_run_claim
    from app.services.execution.tool_executor import UnifiedToolExecutor

    seen = []
    settled = []

    async def execute(tool_name, **kwargs):
        seen.append((current_run_budget(), cancellation.current_cancel_token(), chat_run_claim.get()))
        try:
            await asyncio.Event().wait()
        finally:
            settled.append(True)

    monkeypatch.setattr(tool_box, "execute_tool", execute)
    claim_handle = chat_run_claim.set(("run-thread", "attempt-a"))
    try:
        with budget_scope(0.15, 0.03) as budget:
            await asyncio.sleep(0.03)  # nested execution cannot start a fresh budget
            with pytest.raises(RunDeadlineExceeded):
                UnifiedToolExecutor().execute_sync("code_executor", {"task": "t"})
            assert seen == [(budget, budget.cancel_token, ("run-thread", "attempt-a"))]
            assert settled == [True]
    finally:
        chat_run_claim.reset(claim_handle)


async def test_sync_bridge_deadline_survives_blocking_temporary_loop(monkeypatch):
    import time
    import tool_box
    from app.services.execution.tool_executor import UnifiedToolExecutor

    async def blocking_handler(*args, **kwargs):
        time.sleep(0.35)
        return {"success": True}

    monkeypatch.setattr(tool_box, "execute_tool", blocking_handler)
    with budget_scope(0.12, 0.03) as budget:
        started = time.monotonic()
        with pytest.raises(RunDeadlineExceeded):
            UnifiedToolExecutor().execute_sync("code_executor", {"task": "t"})
        assert time.monotonic() - started < 0.3
        budget.close()
    # Wait for the deliberately uncooperative host thread; its late success is
    # rejected inside the captured context. It cannot be forcibly killed.
    await asyncio.sleep(0.3)


async def test_internal_rubric_bridge_inherits_budget_and_stops_llm():
    from app.services.plans.plan_rubric_evaluator import _invoke_evaluator_client

    seen = []
    settled = []

    async def chat(*args, **kwargs):
        seen.append(current_run_budget())
        try:
            await asyncio.Event().wait()
        finally:
            settled.append(True)

    with budget_scope(0.12, 0.03) as budget:
        with pytest.raises(RunDeadlineExceeded):
            _invoke_evaluator_client(SimpleNamespace(chat_async=chat), prompt="review", evaluator_model=None)
        assert seen == [budget] and settled == [True]


async def test_standalone_tool_preserves_behavior(monkeypatch):
    import tool_box
    from app.services.execution.tool_executor import UnifiedToolExecutor

    async def execute(*args, **kwargs):
        return {"success": True, "value": 7}

    monkeypatch.setattr(tool_box, "execute_tool", execute)
    assert current_run_budget() is None
    assert (await UnifiedToolExecutor().execute("code_executor", {"task": "t"}))["result"]["value"] == 7


async def test_closed_scope_cannot_start_another_stage():
    started = []

    async def work():
        started.append(True)

    with budget_scope(1, 0.1) as budget:
        budget.close()
        assert budget.cancel_token.closed
        with pytest.raises(asyncio.CancelledError):
            await run_stage(work(), stage="late-producer")
    assert started == []


async def test_closing_simple_chat_joins_producer_and_stops_sink(monkeypatch):
    from app.routers.chat import agent as module

    settled = asyncio.Event()
    provider_tasks = []
    events = []

    async def provider(*args, **kwargs):
        provider_tasks.append(asyncio.current_task())
        try:
            yield "first"
            await asyncio.Event().wait()
        finally:
            settled.set()

    async def sink(payload):
        events.append(payload)

    agent = module.StructuredChatAgent.__new__(module.StructuredChatAgent)
    agent.extra_context = {}
    agent.llm_service = SimpleNamespace(stream_chat_async=provider)
    agent._resolve_thinking_enabled = lambda: False
    monkeypatch.setattr(module, "_build_simple_stream_chat_prompt_fn", lambda *args: "hello")
    stream = agent.stream_simple_chat(
        "hello", routing_decision=SimpleNamespace(),
        route_profile=SimpleNamespace(thinking_budget=0), event_sink=sink,
    )
    await stream.__anext__()  # initial thinking event
    await stream.__anext__()  # first provider delta, producer now waits
    await stream.aclose()
    assert settled.is_set()
    assert provider_tasks[0].done()
    before = len(events)
    await asyncio.sleep(0.02)
    assert len(events) == before == 2
