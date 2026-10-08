"""Usage-attribution leak guards: contextvars must survive every call path."""

from __future__ import annotations

from app.llm import (
    _usage_context,
    clear_usage_context,
    set_usage_context,
    stream_chat_collect_async,
)


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
