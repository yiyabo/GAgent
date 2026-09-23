from __future__ import annotations

import asyncio

import pytest

from tool_box.tools_impl import manuscript_writer
from tool_box.tools_impl.web_search.providers import builtin as builtin_provider
from tool_box.tools_impl.web_search.exceptions import WebSearchError


class _StreamOkLLM:
    async def stream_chat_async(self, prompt, **kwargs):
        for chunk in ("第一部分 ", "第二部分 ", "第三部分"):
            yield chunk

    async def chat_async(self, prompt, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("chat_async must not be called when streaming works")


class _StreamFailLLM:
    async def stream_chat_async(self, prompt, **kwargs):
        raise RuntimeError("LLM client does not support streaming")
        yield  # pragma: no cover

    async def chat_async(self, prompt, **kwargs):
        return "fallback-result"


class _StreamEmptyLLM:
    async def stream_chat_async(self, prompt, **kwargs):
        yield "   "

    async def chat_async(self, prompt, **kwargs):
        return "fallback-after-empty"


class _NoStreamLLM:
    async def chat_async(self, prompt, **kwargs):
        return "no-stream-result"


def test_chat_prefers_streaming_and_joins_chunks(monkeypatch) -> None:
    monkeypatch.setattr(manuscript_writer, "update_usage_context", lambda **kwargs: None)
    result = asyncio.run(manuscript_writer._chat(_StreamOkLLM(), "prompt", None, max_tokens=100))
    assert result == "第一部分 第二部分 第三部分"


def test_chat_falls_back_when_streaming_raises(monkeypatch) -> None:
    monkeypatch.setattr(manuscript_writer, "update_usage_context", lambda **kwargs: None)
    result = asyncio.run(manuscript_writer._chat(_StreamFailLLM(), "prompt", None))
    assert result == "fallback-result"


def test_chat_falls_back_when_streaming_empty(monkeypatch) -> None:
    monkeypatch.setattr(manuscript_writer, "update_usage_context", lambda **kwargs: None)
    result = asyncio.run(manuscript_writer._chat(_StreamEmptyLLM(), "prompt", None))
    assert result == "fallback-after-empty"


def test_chat_uses_chat_async_when_no_stream_fn(monkeypatch) -> None:
    monkeypatch.setattr(manuscript_writer, "update_usage_context", lambda **kwargs: None)
    result = asyncio.run(manuscript_writer._chat(_NoStreamLLM(), "prompt", None))
    assert result == "no-stream-result"


class _FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self):
        return '{"output": [{"type": "message", "content": [{"type": "output_text", "text": "answer"}]}]}'


class _FakeSession:
    captured: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        _FakeSession.captured = {"url": url, "headers": headers, "json": json}
        return _FakeResponse()


class _Settings:
    qwen_api_key = "test-key"
    qwen_responses_api_url = "https://sub2api.medicalheart.cn/v1/responses"
    qwen_responses_model = "qwen3.8-flash"
    qwen_model = "qwen3.7-max"
    builtin_provider = "qwen"
    builtin_request_timeout = 300.0


def test_local_url_override_wins_over_settings_url(monkeypatch) -> None:
    monkeypatch.setattr(builtin_provider.aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setenv("QWEN_RESPONSES_LOCAL_URL", "http://172.17.0.1:40002/v1/responses")
    result = asyncio.run(
        builtin_provider.search(query="q", max_results=2, settings=_Settings())
    )
    assert _FakeSession.captured["url"] == "http://172.17.0.1:40002/v1/responses"
    assert result.provider == "builtin"


def test_settings_url_used_when_no_override(monkeypatch) -> None:
    monkeypatch.setattr(builtin_provider.aiohttp, "ClientSession", _FakeSession)
    monkeypatch.delenv("QWEN_RESPONSES_LOCAL_URL", raising=False)
    try:
        asyncio.run(builtin_provider.search(query="q", max_results=2, settings=_Settings()))
    except WebSearchError:
        pass
    assert _FakeSession.captured["url"] == "https://sub2api.medicalheart.cn/v1/responses"
