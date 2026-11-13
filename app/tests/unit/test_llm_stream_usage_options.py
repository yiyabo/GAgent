"""Streaming requests must ask for token usage, without breaking gateways.

Phase 0 (2026-11-13): the native DeepThink tool loop streams via
``_stream_chat_with_tools_inner``. OpenAI-compatible gateways only include a
``usage`` object in streamed chunks when the request carries
``stream_options.include_usage`` — without it, the run executed dozens of
billable LLM iterations while ``llm_usage_log`` stayed empty. The client now
sends the field by default (``LLM_STREAM_INCLUDE_USAGE=1``) and, when a gateway
rejects it with a 400 naming the field, retries once without it and remembers
that gateway for the rest of the process.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

import app.llm as llm_mod
from app.llm import LLMClient


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(llm_mod, "_log_usage", lambda **_: None)
    monkeypatch.setattr(llm_mod, "_log_call_metrics", lambda **_: None)
    llm_mod._STREAM_OPTIONS_UNSUPPORTED_URLS.clear()
    yield
    llm_mod._STREAM_OPTIONS_UNSUPPORTED_URLS.clear()


class _NoopLimiter:
    async def acquire_async(self) -> None:
        return None

    def acquire(self) -> None:
        return None


def _client() -> LLMClient:
    client = LLMClient(
        provider="platform",
        api_key="sk-test",
        url="https://example.invalid/v1/chat/completions",
        model="qwen-test",
        retries=0,
    )
    client.backoff_base = 0
    return client


def _done_lines() -> list[str]:
    return [
        "data: " + json.dumps(
            {"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}
        ),
        "data: [DONE]",
    ]


class _FakeStreamResponse:
    def __init__(self, lines: list[str], *, status_code: int = 200, body: bytes = b"") -> None:
        self.status_code = status_code
        self._lines = list(lines)
        self._body = body
        self.headers: dict[str, str] = {}

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
            response = httpx.Response(self.status_code, content=self._body, request=request)
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=request, response=response)


class _FakeStreamContext:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._response

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeAsyncClient:
    """Plays a scripted list of streamed responses and records each request."""

    def __init__(self, responses: list[_FakeStreamResponse]) -> None:
        self._responses = list(responses)
        self.stream_calls: list[dict[str, Any]] = []

    def stream(self, method: str, url: str, **kwargs: Any) -> _FakeStreamContext:
        # Snapshot the payload: the client mutates it when retrying without
        # stream_options, and the first call's record must not change.
        record = {"method": method, "url": url, **kwargs}
        record["json"] = json.loads(json.dumps(kwargs["json"]))
        self.stream_calls.append(record)
        assert self._responses, "test did not script enough streamed responses"
        return _FakeStreamContext(self._responses.pop(0))


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeAsyncClient) -> None:
    monkeypatch.setattr(llm_mod, "_get_shared_async_client", lambda: fake)
    monkeypatch.setattr(llm_mod, "_outbound_limiter", _NoopLimiter())


def _run(client: LLMClient) -> llm_mod.NativeStreamResult:
    return asyncio.run(
        client.stream_chat_with_tools_async(
            messages=[{"role": "user", "content": "查询噬菌体"}],
            tools=[{"type": "function", "function": {"name": "phagescope_data"}}],
        )
    )


class TestStreamOptionsInjection:
    def test_include_usage_is_requested_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeAsyncClient([_FakeStreamResponse(_done_lines())])
        _install(monkeypatch, fake)
        result = _run(_client())
        assert result.content == "ok"
        assert len(fake.stream_calls) == 1
        payload = fake.stream_calls[0]["json"]
        assert payload["stream_options"] == {"include_usage": True}
        assert payload["stream"] is True

    def test_env_switch_disables_the_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_STREAM_INCLUDE_USAGE", "0")
        fake = _FakeAsyncClient([_FakeStreamResponse(_done_lines())])
        _install(monkeypatch, fake)
        _run(_client())
        assert "stream_options" not in fake.stream_calls[0]["json"]


class TestStreamOptionsFallback:
    def test_400_naming_stream_options_retries_without_and_remembers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeAsyncClient(
            [
                _FakeStreamResponse(
                    [], status_code=400,
                    body=b'{"error":{"message":"unknown field `stream_options`"}}',
                ),
                _FakeStreamResponse(_done_lines()),
                _FakeStreamResponse(_done_lines()),
            ]
        )
        _install(monkeypatch, fake)
        client = _client()

        first = _run(client)
        assert first.content == "ok"
        assert len(fake.stream_calls) == 2
        assert "stream_options" in fake.stream_calls[0]["json"]
        assert "stream_options" not in fake.stream_calls[1]["json"]
        assert client.url in llm_mod._STREAM_OPTIONS_UNSUPPORTED_URLS

        # The gateway is remembered: later calls skip the field entirely.
        _run(client)
        assert len(fake.stream_calls) == 3
        assert "stream_options" not in fake.stream_calls[2]["json"]

    def test_unrelated_400_still_raises_without_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeAsyncClient(
            [
                _FakeStreamResponse(
                    [], status_code=400,
                    body=b'{"error":{"message":"model not found"}}',
                ),
            ]
        )
        _install(monkeypatch, fake)
        with pytest.raises(RuntimeError):
            _run(_client())
        assert len(fake.stream_calls) == 1
        assert not llm_mod._STREAM_OPTIONS_UNSUPPORTED_URLS


class TestRepairPayload:
    def test_nonstream_repair_drops_stream_options(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: dict[str, Any] = {}

        class _Posted:
            status_code = 200
            text = ""

            def json(self) -> dict[str, Any]:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "phagescope_data",
                                            "arguments": '{"operation":"overview"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }

        class _PostClient(_FakeAsyncClient):
            async def post(self, url: str, **kwargs: Any) -> _Posted:
                recorded.update(kwargs)
                return _Posted()

        # Stream returns a named tool call with empty arguments, forcing repair.
        lines = [
            "data: " + json.dumps(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "phagescope_data", "arguments": ""},
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ),
        ]
        fake = _PostClient([_FakeStreamResponse(lines)])
        _install(monkeypatch, fake)
        monkeypatch.setenv("LLM_NONSTREAM_TOOLCALL_REPAIR", "1")
        result = _run(_client())
        assert result.tool_calls and result.tool_calls[0].arguments
        assert recorded["json"]["stream"] is False
        assert "stream_options" not in recorded["json"]
