"""Usage-attribution leak guards: contextvars must survive every call path."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm import (
    _usage_context,
    clear_usage_context,
    set_usage_context,
    stream_chat_collect_async,
)
from app.routers.chat.agent import StructuredChatAgent


class _SyncOnlyClient:
    """No stream_chat_async/chat_async: forces the sync-executor fallback."""

    def __init__(self, sink: list) -> None:
        self._sink = sink

    def chat(self, prompt: str, **kwargs) -> str:
        self._sink.append(_usage_context.get())
        return "collected"


async def test_executor_fallback_propagates_usage_context() -> None:
    sink: list = []
    client = _SyncOnlyClient(sink)
    token = set_usage_context(session_id="sess_attr", call_purpose="unit_aux", phase="chat")
    try:
        out = await stream_chat_collect_async(client, "hello")
    finally:
        clear_usage_context(token)
    assert out == "collected"
    assert sink, "sync chat was not invoked"
    assert sink[0] is not None
    assert sink[0]["session_id"] == "sess_attr"
    assert sink[0]["call_purpose"] == "unit_aux"


def test_routing_resolution_tags_usage_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers.chat import agent as agent_module

    captured: list = []

    def fake_resolve(**kwargs):
        captured.append(_usage_context.get())
        return SimpleNamespace(tier="standard")

    monkeypatch.setattr(agent_module, "resolve_request_routing", fake_resolve)
    monkeypatch.setattr(
        agent_module, "build_request_tier_profile", lambda *a, **k: "PROFILE"
    )

    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.session_id = "sess_route"
    agent.plan_session = SimpleNamespace(plan_id=7)
    agent.extra_context = {"current_task_id": 42}
    agent.history = []

    outer = set_usage_context(session_id="outer_sess", call_purpose="chat_main")
    try:
        decision, profile = agent._resolve_request_routing("分析一下")
        # outer context restored after the wrap exits
        assert _usage_context.get()["session_id"] == "outer_sess"
    finally:
        clear_usage_context(outer)

    assert profile == "PROFILE"
    assert captured, "resolve_request_routing was not invoked"
    ctx = captured[0]
    assert ctx["session_id"] == "sess_route"
    assert ctx["plan_id"] == 7
    assert ctx["task_id"] == 42
    assert ctx["call_purpose"] == "request_routing"
    assert ctx["phase"] == "routing"
    assert ctx["billing_key"]


def test_usage_context_nesting_restores_previous() -> None:
    outer = set_usage_context(session_id="s1", call_purpose="chat_main", phase="chat")
    try:
        inner = set_usage_context(session_id="s2", call_purpose="request_routing", phase="routing")
        try:
            assert _usage_context.get()["session_id"] == "s2"
        finally:
            clear_usage_context(inner)
        assert _usage_context.get()["session_id"] == "s1"
        assert _usage_context.get()["call_purpose"] == "chat_main"
    finally:
        clear_usage_context(outer)
