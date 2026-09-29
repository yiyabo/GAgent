"""Billing-key wiring pins: every registered billable capability tags its usage.

Covers the 2026-09-29 口径: plan.decompose / plan.review / plan.optimize /
chat.routing pins, plan-lane deep-think attribution (billing_lane), the
usage_context_override helper, and per-invocation tool fee metering
(tool.web_search / tool.literature_pipeline).
"""
from __future__ import annotations

import json

import pytest

import app.llm as llm_mod
from app.llm import (
    LLMClient,
    clear_usage_context,
    current_usage_context,
    set_usage_context,
    update_usage_context,
    usage_context_override,
)


@pytest.fixture(autouse=True)
def _clean_usage_context():
    llm_mod._usage_context.set(None)
    yield
    llm_mod._usage_context.set(None)


class TestUsageContextOverride:
    def test_merges_and_restores(self) -> None:
        token = set_usage_context(session_id="s1", call_purpose="chat_main")
        try:
            with usage_context_override(call_purpose="plan_review", phase="plan"):
                ctx = current_usage_context()
                assert ctx["call_purpose"] == "plan_review"
                assert ctx["billing_key"] == "plan.review"
                assert ctx["session_id"] == "s1"  # ambient attribution preserved
            ctx = current_usage_context()
            assert ctx["call_purpose"] == "chat_main"
            assert ctx["billing_key"] == "chat.main"
        finally:
            clear_usage_context(token)

    def test_restores_on_exception(self) -> None:
        token = set_usage_context(call_purpose="chat_main")
        try:
            with pytest.raises(RuntimeError):
                with usage_context_override(call_purpose="plan_optimize"):
                    raise RuntimeError("boom")
            assert current_usage_context()["call_purpose"] == "chat_main"
        finally:
            clear_usage_context(token)

    def test_restores_to_empty_when_no_ambient(self) -> None:
        with usage_context_override(call_purpose="request_routing"):
            assert current_usage_context()["billing_key"] == "chat.routing"
        assert current_usage_context() == {}


class TestBillingLane:
    def test_lane_survives_update_and_purpose_pollution(self) -> None:
        token = set_usage_context(call_purpose="plan_task_execution", billing_lane="plan_task")
        try:
            update_usage_context(call_purpose="agent_run", tool_name="web_search")
            assert current_usage_context()["billing_lane"] == "plan_task"
        finally:
            clear_usage_context(token)

    def test_pin_helper_plan_lane_repins_purpose(self) -> None:
        from app.services.deep_think.controller import _pin_iteration_billing_context

        token = set_usage_context(call_purpose="plan_task_execution", billing_lane="plan_task")
        try:
            # simulate purpose pollution (forced synthesis / action run inside the lane)
            update_usage_context(call_purpose="deep_think_forced_synthesis")
            _pin_iteration_billing_context()
            ctx = current_usage_context()
            assert ctx["call_purpose"] == "plan_task_execution"
            assert ctx["billing_key"] == "plan.task_execution"
        finally:
            clear_usage_context(token)

    def test_pin_helper_chat_lane_marks_deep_think(self) -> None:
        from app.services.deep_think.controller import _pin_iteration_billing_context

        token = set_usage_context(session_id="s1", call_purpose="chat_main")
        try:
            _pin_iteration_billing_context()
            ctx = current_usage_context()
            assert ctx["call_purpose"] == "deep_think_iteration"
            assert ctx["billing_key"] == "deep_think.iteration"
            assert ctx["tool_name"] == "deep_think"
        finally:
            clear_usage_context(token)


class TestDecomposerPins:
    def _svc(self, captured, reply: str):
        from app.services.llm.decomposer_service import PlanDecomposerLLMService

        class _FakeLLM:
            def stream_chat(self, prompt, model=None):
                captured.append(current_usage_context())
                return iter([reply])

        return PlanDecomposerLLMService(llm=_FakeLLM())

    def test_generate_pins_plan_decompose(self) -> None:
        captured = []
        svc = self._svc(
            captured,
            '{"mode": "expand", "target_node_id": null, '
            '"children": [{"name": "t1", "instruction": "do"}]}',
        )
        result = svc.generate("decompose this")
        assert captured and captured[0]["call_purpose"] == "plan_decomposition"
        assert captured[0]["billing_key"] == "plan.decompose"
        assert len(result.children) == 1
        assert current_usage_context() == {}

    def test_decide_search_pins_plan_decompose(self) -> None:
        captured = []
        svc = self._svc(captured, "yes")
        assert svc.decide_search("need search?") == "yes"
        assert captured[0]["billing_key"] == "plan.decompose"
        assert current_usage_context() == {}


class TestRubricReviewPin:
    def test_invoke_evaluator_pins_plan_review(self, monkeypatch) -> None:
        from app.services.plans import plan_rubric_evaluator as rubric

        client = LLMClient(
            provider="custom",
            api_key="k",
            url="http://localhost:1/v1/chat/completions",
            model="m",
        )
        captured = []

        def _fake_stream(prompt, *, messages, model=None):
            captured.append(current_usage_context())
            return iter(["{}"])

        monkeypatch.setattr(client, "stream_chat", _fake_stream)
        token = set_usage_context(session_id="s1", call_purpose="chat_main")
        try:
            assert rubric._invoke_evaluator_client(client, prompt="p", evaluator_model="m") == "{}"
            assert captured and captured[0]["call_purpose"] == "plan_review"
            assert captured[0]["billing_key"] == "plan.review"
            assert captured[0]["session_id"] == "s1"
            assert current_usage_context()["call_purpose"] == "chat_main"
        finally:
            clear_usage_context(token)


class TestOptimizerPin:
    def test_call_optimizer_llm_pins_plan_optimize(self, monkeypatch) -> None:
        from app.services.plans import plan_optimizer as opt

        captured = []

        class _FakeService:
            def __init__(self, client) -> None:
                pass

            def chat(self, prompt, model=None, temperature=0.0):
                captured.append(current_usage_context())
                return '{"summary": "s", "rationale": [], "changes": []}'

            def parse_json_response(self, text):
                return json.loads(text)

        monkeypatch.setattr(opt, "LLMService", _FakeService)
        opt._call_optimizer_llm(
            "optimize this",
            provider="qwen",
            model="m",
            model_provider={"base_url": "http://localhost:1", "api_key": "k", "model": "m"},
        )
        assert captured and captured[0]["call_purpose"] == "plan_optimize"
        assert captured[0]["billing_key"] == "plan.optimize"
        assert current_usage_context() == {}


class TestRoutingPin:
    def test_llm_routing_fallback_pins_chat_routing(self, monkeypatch) -> None:
        from app.routers.chat import request_routing as rr

        captured = []

        class _FakeClient:
            def chat(self, prompt=None, messages=None, **kwargs):
                captured.append(current_usage_context())
                return '{"tier": "standard", "is_plan_modification": false, "reason": "x"}'

        monkeypatch.setattr(rr, "get_default_client", lambda: _FakeClient())
        result = rr._llm_routing_fallback("an ambiguous message")
        assert result is not None and result[0] == "standard"
        assert captured and captured[0]["call_purpose"] == "request_routing"
        assert captured[0]["billing_key"] == "chat.routing"
        assert current_usage_context() == {}


class TestInvocationMeter:
    def test_records_zero_token_fee_row(self, monkeypatch) -> None:
        from tool_box.tools_impl import invocation_meter as meter

        rows = []
        monkeypatch.setattr("app.repository.llm_usage.log_llm_usage", lambda **kw: rows.append(kw))
        token = set_usage_context(
            session_id="sess_x", plan_id=9, run_id="run_1", call_purpose="deep_think_iteration"
        )
        try:
            meter.record_tool_invocation(tool_name="web_search", call_status="ok", duration_ms=12.0)
        finally:
            clear_usage_context(token)
        assert len(rows) == 1
        row = rows[0]
        assert row["billing_key"] == "tool.web_search"
        assert row["provider"] == "tool_invocation"
        assert row["call_purpose"] == "tool_invocation"
        assert row["total_tokens"] == 0
        assert row["estimated_cost"] == pytest.approx(0.004)
        assert row["session_id"] == "sess_x"
        assert row["plan_id"] == 9
        assert row["run_id"] == "run_1"

    def test_literature_pipeline_key_and_default_fee(self, monkeypatch) -> None:
        from tool_box.tools_impl import invocation_meter as meter

        rows = []
        monkeypatch.setattr("app.repository.llm_usage.log_llm_usage", lambda **kw: rows.append(kw))
        meter.record_tool_invocation(tool_name="literature_pipeline")
        assert rows[0]["billing_key"] == "tool.literature_pipeline"
        assert rows[0]["estimated_cost"] == 0.0

    def test_fee_env_override(self, monkeypatch) -> None:
        from tool_box.tools_impl import invocation_meter as meter

        monkeypatch.setenv("WEB_SEARCH_INVOCATION_CNY", "0.01")
        assert meter._invocation_fee_cny("web_search") == pytest.approx(0.01)
        monkeypatch.setenv("WEB_SEARCH_INVOCATION_CNY", "junk")
        assert meter._invocation_fee_cny("web_search") == pytest.approx(0.004)

    def test_never_raises_on_log_failure(self, monkeypatch) -> None:
        from tool_box.tools_impl import invocation_meter as meter

        def _boom(**kw):
            raise RuntimeError("db down")

        monkeypatch.setattr("app.repository.llm_usage.log_llm_usage", _boom)
        meter.record_tool_invocation(tool_name="web_search")  # must not raise

    def test_invocation_timer_marks_status(self, monkeypatch) -> None:
        from tool_box.tools_impl import invocation_meter as meter

        seen = []
        monkeypatch.setattr(meter, "record_tool_invocation", lambda **kw: seen.append(kw))
        with meter.invocation_timer("web_search"):
            pass
        assert seen[0]["call_status"] == "ok"
        with pytest.raises(ValueError):
            with meter.invocation_timer("web_search"):
                raise ValueError("x")
        assert seen[1]["call_status"] == "error"
        assert seen[1]["duration_ms"] >= 0


class TestWebSearchDispatchMetering:
    async def test_dispatch_meters_success(self, monkeypatch) -> None:
        from tool_box.tools_impl.web_search import router as ws_router
        from tool_box.tools_impl.web_search.result import WebSearchResult

        calls = []
        monkeypatch.setattr(ws_router, "record_tool_invocation", lambda **kw: calls.append(kw))
        monkeypatch.setattr(ws_router, "_INITIALISED", True)

        async def _ok_provider(**kwargs):
            return WebSearchResult(query=kwargs["query"], provider="builtin", response="ok")

        monkeypatch.setattr(ws_router, "get_provider", lambda name: _ok_provider)
        result = await ws_router.dispatch(query="q", provider=None, max_results=3)
        assert result.success is True
        assert len(calls) == 1
        assert calls[0]["tool_name"] == "web_search"
        assert calls[0]["call_status"] == "ok"
        assert calls[0]["duration_ms"] >= 0

    async def test_dispatch_meters_failure(self, monkeypatch) -> None:
        from tool_box.tools_impl.web_search import router as ws_router
        from tool_box.tools_impl.web_search.exceptions import WebSearchError

        calls = []
        monkeypatch.setattr(ws_router, "record_tool_invocation", lambda **kw: calls.append(kw))
        monkeypatch.setattr(ws_router, "_INITIALISED", True)

        async def _bad_provider(**kwargs):
            raise WebSearchError(code="timeout", message="t", provider="builtin")

        monkeypatch.setattr(ws_router, "get_provider", lambda name: _bad_provider)
        with pytest.raises(WebSearchError):
            await ws_router.dispatch(query="q", provider=None, max_results=3)
        assert len(calls) == 1
        assert calls[0]["call_status"] == "error"


class TestLiteraturePipelineMetering:
    async def test_wrapper_meters_success(self, monkeypatch) -> None:
        import tool_box.tools_impl.literature_pipeline as lp

        calls = []
        monkeypatch.setattr(
            "tool_box.tools_impl.invocation_meter.record_tool_invocation",
            lambda **kw: calls.append(kw),
        )

        async def _fake_impl(query, **kwargs):
            return {"tool": "literature_pipeline", "success": True, "query": query}

        monkeypatch.setattr(lp, "_literature_pipeline_handler_impl", _fake_impl)
        result = await lp.literature_pipeline_handler("phage host interaction")
        assert result["success"] is True
        assert len(calls) == 1
        assert calls[0]["tool_name"] == "literature_pipeline"
        assert calls[0]["call_status"] == "ok"

    async def test_wrapper_meters_failure_payload_and_exception(self, monkeypatch) -> None:
        import tool_box.tools_impl.literature_pipeline as lp

        calls = []
        monkeypatch.setattr(
            "tool_box.tools_impl.invocation_meter.record_tool_invocation",
            lambda **kw: calls.append(kw),
        )

        async def _fail_payload(query, **kwargs):
            return {"tool": "literature_pipeline", "success": False, "error": "missing_query"}

        monkeypatch.setattr(lp, "_literature_pipeline_handler_impl", _fail_payload)
        result = await lp.literature_pipeline_handler("")
        assert result["success"] is False
        assert calls[0]["call_status"] == "error"

        async def _raise_impl(query, **kwargs):
            raise RuntimeError("net down")

        monkeypatch.setattr(lp, "_literature_pipeline_handler_impl", _raise_impl)
        with pytest.raises(RuntimeError):
            await lp.literature_pipeline_handler("q")
        assert calls[1]["call_status"] == "error"
