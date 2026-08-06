from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import httpx
import pytest

from app.llm import LLMClient, _log_call_metrics


def _client(retries: int = 0) -> LLMClient:
    return LLMClient(
        provider="qwen",
        api_key="test-key",
        url="https://example.com/v1/chat/completions",
        model="qwen-test",
        timeout=5,
        retries=retries,
    )


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        },
        request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
    )


def test_log_call_metrics_never_raises_and_emits(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="app.llm"):
        _log_call_metrics(
            method="chat", provider="qwen", model="m", status="ok", latency_ms=12.3,
        )
        _log_call_metrics(
            method="chat", provider="qwen", model="m", status="error",
            latency_ms=1.0, usage="not-a-dict", ttft_ms=None, error="Boom",
        )
    lines = [r.getMessage() for r in caplog.records if "[LLM][metrics]" in r.getMessage()]
    assert len(lines) == 2
    assert "method=chat" in lines[0]
    assert "status=ok" in lines[0]
    assert "latency_ms=12" in lines[0]
    assert "status=error" in lines[1]
    assert "error=Boom" in lines[1]


def test_chat_success_logs_metrics(monkeypatch, caplog) -> None:
    monkeypatch.setattr("app.llm._log_usage", lambda **_: None)
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = _ok_response()
    monkeypatch.setattr("app.llm._get_shared_sync_client", lambda: mock_client)

    with caplog.at_level(logging.INFO, logger="app.llm"):
        assert _client().chat("hello") == "ok"

    metrics = [r.getMessage() for r in caplog.records if "[LLM][metrics]" in r.getMessage()]
    assert len(metrics) == 1
    line = metrics[0]
    assert "method=chat" in line
    assert "status=ok" in line
    assert "attempts=1" in line
    assert "prompt_tokens=3" in line
    assert "total_tokens=8" in line
    assert "latency_ms=" in line


def test_chat_failure_logs_metrics(monkeypatch, caplog) -> None:
    monkeypatch.setattr("app.llm._log_usage", lambda **_: None)
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.side_effect = httpx.ConnectError("boom")
    monkeypatch.setattr("app.llm._get_shared_sync_client", lambda: mock_client)

    with caplog.at_level(logging.INFO, logger="app.llm"):
        with pytest.raises(RuntimeError):
            _client(retries=0).chat("hello")

    metrics = [r.getMessage() for r in caplog.records if "[LLM][metrics]" in r.getMessage()]
    assert len(metrics) == 1
    line = metrics[0]
    assert "method=chat" in line
    assert "status=error" in line
    assert "error=ConnectError" in line


def test_chat_async_success_logs_metrics(monkeypatch, caplog) -> None:
    monkeypatch.setattr("app.llm._log_usage", lambda **_: None)
    mock_client = MagicMock(spec=httpx.AsyncClient)

    async def _post(*args, **kwargs):
        return _ok_response()

    mock_client.post = _post
    monkeypatch.setattr("app.llm._get_shared_async_client", lambda: mock_client)

    with caplog.at_level(logging.INFO, logger="app.llm"):
        result = asyncio.run(_client().chat_async("hello"))
    assert result == "ok"

    metrics = [r.getMessage() for r in caplog.records if "[LLM][metrics]" in r.getMessage()]
    assert len(metrics) == 1
    assert "method=chat_async" in metrics[0]
    assert "status=ok" in metrics[0]


class _FakeStreamResponse:
    status_code = 200

    async def aiter_lines(self):
        yield 'data: {"choices": [{"delta": {"content": "hi"}}]}'
        yield 'data: {"choices": [{"finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}'
        yield "data: [DONE]"


class _FakeStreamCM:
    async def __aenter__(self):
        return _FakeStreamResponse()

    async def __aexit__(self, *args):
        return False


def test_stream_chat_async_logs_ttft_and_usage(monkeypatch, caplog) -> None:
    monkeypatch.setattr("app.llm._log_usage", lambda **_: None)
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.stream = lambda *args, **kwargs: _FakeStreamCM()
    monkeypatch.setattr("app.llm._get_shared_async_client", lambda: mock_client)

    async def _collect() -> str:
        chunks = []
        async for delta in _client().stream_chat_async("hello"):
            chunks.append(delta)
        return "".join(chunks)

    with caplog.at_level(logging.INFO, logger="app.llm"):
        assert asyncio.run(_collect()) == "hi"

    metrics = [r.getMessage() for r in caplog.records if "[LLM][metrics]" in r.getMessage()]
    assert len(metrics) == 1
    line = metrics[0]
    assert "method=stream_chat" in line
    assert "status=ok" in line
    assert "ttft_ms=-" not in line
    assert "total_tokens=3" in line
