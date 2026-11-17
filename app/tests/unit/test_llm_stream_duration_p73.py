"""p73: streaming LLM calls write exactly one usage row with ``duration_ms``."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from app.llm import LLMClient

_USAGE = '"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}'


def _client() -> LLMClient:
    return LLMClient(
        provider="qwen",
        api_key="test-key",
        url="https://example.com/v1/chat/completions",
        model="qwen-test",
        timeout=5,
        retries=0,
    )


class _Resp:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _CM:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return _Resp(self._lines)

    async def __aexit__(self, *args):
        return False


@pytest.fixture()
def usage_calls(monkeypatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("app.llm._log_usage", lambda **kw: calls.append(kw))
    return calls


def _install_stream(monkeypatch, lines: list[str]) -> MagicMock:
    mock = MagicMock(spec=httpx.AsyncClient)
    mock.stream = lambda *a, **k: _CM(lines)
    monkeypatch.setattr("app.llm._get_shared_async_client", lambda: mock)
    return mock


def _assert_single_row(calls: list[dict[str, Any]]) -> None:
    assert len(calls) == 1
    row = calls[0]
    assert row["total_tokens"] == 3 and row["call_status"] == "ok"
    assert isinstance(row["duration_ms"], float) and row["duration_ms"] >= 0


def test_stream_chat_async_logs_once_even_when_usage_repeats(monkeypatch, usage_calls) -> None:
    # Providers that repeat ``usage`` on every chunk must still yield one row.
    _install_stream(
        monkeypatch,
        [
            'data: {"choices": [{"delta": {"content": "a"}}], ' + _USAGE + "}",
            'data: {"choices": [{"delta": {"content": "b"}}], ' + _USAGE + "}",
            "data: [DONE]",
        ],
    )

    async def collect() -> str:
        return "".join([d async for d in _client().stream_chat_async("hi")])

    assert asyncio.run(collect()) == "ab"
    _assert_single_row(usage_calls)


def test_stream_chat_with_tools_logs_once_with_duration(monkeypatch, usage_calls) -> None:
    _install_stream(
        monkeypatch,
        [
            'data: {"choices": [{"delta": {"content": "x"}}], ' + _USAGE + "}",
            'data: {"choices": [{"delta": {}, "finish_reason": "stop"}], ' + _USAGE + "}",
            "data: [DONE]",
        ],
    )
    result = asyncio.run(
        _client().stream_chat_with_tools_async([{"role": "user", "content": "hi"}], tools=[])
    )
    assert result.content == "x"
    _assert_single_row(usage_calls)


def test_repair_nonstream_logs_duration(monkeypatch, usage_calls) -> None:
    mock = MagicMock(spec=httpx.AsyncClient)

    async def post(*args, **kwargs):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "", "tool_calls": []}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            },
            request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
        )

    mock.post = post
    monkeypatch.setattr("app.llm._get_shared_async_client", lambda: mock)
    asyncio.run(_client()._repair_tool_calls_nonstream({"messages": [], "stream": True}, "call_1"))
    _assert_single_row(usage_calls)
