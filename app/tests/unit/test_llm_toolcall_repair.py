"""Repair a streamed tool call whose arguments never arrived.

Measured 2026-09-26 on `.8`: the gateway (sub2api -> ``qwen3.8-flash``) streams
``{"name": "web_search", "arguments": ""}`` and then ``finish_reason:
"tool_calls"`` with no argument fragments at all, while the identical request
sent without ``stream`` returns ``{"query": "...", "max_results": 3}``. Every
affected call therefore died as ``missing_query`` and the turn ended in a
forced-synthesis answer that told the user no search had run.

The repair re-asks once, non-streaming, and only when the stream really lost the
arguments, so a healthy turn pays nothing.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import httpx
import pytest

import app.llm as llm_mod
from app.llm import LLMClient, NativeToolCall


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "_log_usage", lambda **_: None)
    monkeypatch.setattr(llm_mod, "_log_call_metrics", lambda **_: None)


class _NoopLimiter:
    async def acquire_async(self) -> None:
        return None

    def acquire(self) -> None:
        return None


def _client(**kwargs: Any) -> LLMClient:
    client = LLMClient(
        provider="platform",
        api_key="sk-test",
        url="https://example.invalid/v1/chat/completions",
        model="qwen-test",
        retries=0,
        **kwargs,
    )
    client.backoff_base = 0
    return client


def _tool_call_line(name: str, arguments: str, index: int = 0, call_id: str = "call_1") -> str:
    return "data: " + json.dumps(
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": index,
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        }
    )


def _finish_line() -> str:
    return "data: " + json.dumps(
        {"choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "tool_calls"}]}
    )


class _FakeStreamResponse:
    def __init__(self, lines: List[str]) -> None:
        self.status_code = 200
        self._lines = list(lines)

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    def raise_for_status(self) -> None:
        return None


class _FakeStreamContext:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._response

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakePostedResponse:
    def __init__(self, status_code: int = 200, body: Optional[Dict[str, Any]] = None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self) -> Dict[str, Any]:
        if self._body is None:
            raise ValueError("no json body")
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
            response = httpx.Response(self.status_code, text=self.text, request=request)
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=request, response=response)


class _FakeAsyncClient:
    """Serves one streamed response and records every non-streaming POST."""

    def __init__(
        self,
        lines: List[str],
        *,
        posted: Optional[_FakePostedResponse] = None,
        post_raises: Optional[Exception] = None,
    ) -> None:
        self._lines = lines
        self._posted = posted
        self._post_raises = post_raises
        self.stream_calls: List[Dict[str, Any]] = []
        self.post_calls: List[Dict[str, Any]] = []

    def stream(self, method: str, url: str, **kwargs: Any) -> _FakeStreamContext:
        self.stream_calls.append({"method": method, "url": url, **kwargs})
        return _FakeStreamContext(_FakeStreamResponse(self._lines))

    async def post(self, url: str, **kwargs: Any) -> _FakePostedResponse:
        self.post_calls.append({"url": url, **kwargs})
        if self._post_raises is not None:
            raise self._post_raises
        assert self._posted is not None, "test did not provide a posted response"
        return self._posted


def _run(client: LLMClient) -> llm_mod.NativeStreamResult:
    return asyncio.run(
        client.stream_chat_with_tools_async(
            messages=[{"role": "user", "content": "检索肺癌免疫治疗论文"}],
            tools=[{"type": "function", "function": {"name": "web_search"}}],
        )
    )


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeAsyncClient) -> None:
    monkeypatch.setattr(llm_mod, "_get_shared_async_client", lambda: fake)
    monkeypatch.setattr(llm_mod, "_outbound_limiter", _NoopLimiter())


# ---------------------------------------------------------------------------
# the two pure helpers
# ---------------------------------------------------------------------------


class TestLostArguments:
    def test_named_call_with_no_arguments_is_a_loss(self) -> None:
        calls = [NativeToolCall(id="1", name="web_search", arguments={})]
        assert llm_mod._tool_calls_lost_their_arguments(calls) is True

    def test_arguments_present_is_not_a_loss(self) -> None:
        calls = [NativeToolCall(id="1", name="web_search", arguments={"query": "x"})]
        assert llm_mod._tool_calls_lost_their_arguments(calls) is False

    def test_one_empty_among_many_still_counts(self) -> None:
        calls = [
            NativeToolCall(id="1", name="file_operations", arguments={"operation": "read"}),
            NativeToolCall(id="2", name="web_search", arguments={}),
        ]
        assert llm_mod._tool_calls_lost_their_arguments(calls) is True

    def test_no_calls_is_not_a_loss(self) -> None:
        assert llm_mod._tool_calls_lost_their_arguments([]) is False


class TestRepairSwitch:
    def test_on_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLM_NONSTREAM_TOOLCALL_REPAIR", raising=False)
        assert llm_mod._nonstream_toolcall_repair_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no"])
    def test_can_be_turned_off(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_NONSTREAM_TOOLCALL_REPAIR", value)
        assert llm_mod._nonstream_toolcall_repair_enabled() is False


class TestParseMessageToolCalls:
    def test_reads_a_json_string(self) -> None:
        parsed = llm_mod._parse_message_tool_calls(
            [
                {
                    "id": "call_9",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": '{"query": "lung cancer", "max_results": 3}',
                    },
                }
            ]
        )
        assert parsed == [
            NativeToolCall(
                id="call_9",
                name="web_search",
                arguments={"query": "lung cancer", "max_results": 3},
            )
        ]

    def test_accepts_already_parsed_arguments(self) -> None:
        parsed = llm_mod._parse_message_tool_calls(
            [{"id": "c", "function": {"name": "x", "arguments": {"k": 1}}}]
        )
        assert parsed[0].arguments == {"k": 1}

    def test_empty_argument_string_stays_empty(self) -> None:
        parsed = llm_mod._parse_message_tool_calls(
            [{"id": "c", "function": {"name": "x", "arguments": ""}}]
        )
        assert parsed[0].arguments == {}

    def test_unparseable_arguments_keep_the_raw_text(self) -> None:
        parsed = llm_mod._parse_message_tool_calls(
            [{"id": "c", "function": {"name": "x", "arguments": "not json"}}]
        )
        assert parsed[0].arguments == {"_raw": "not json"}

    @pytest.mark.parametrize("payload", [None, [], ["x"], [{"function": {}}], "nonsense"])
    def test_junk_yields_nothing(self, payload: Any) -> None:
        assert llm_mod._parse_message_tool_calls(payload) == []


# ---------------------------------------------------------------------------
# the repair, end to end over a faked transport
# ---------------------------------------------------------------------------


class TestRepairOnTheWire:
    def test_recovers_the_arguments_a_stream_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeAsyncClient(
            [_tool_call_line("web_search", ""), _finish_line()],
            posted=_FakePostedResponse(
                body={
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "call_9",
                                        "type": "function",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": '{"query": "肺癌免疫治疗 最新论文 2026", "max_results": 3}',
                                        },
                                    }
                                ]
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                }
            ),
        )
        _install(monkeypatch, fake)

        result = _run(_client())

        assert len(fake.post_calls) == 1
        # The re-ask must not itself be a streaming request.
        assert fake.post_calls[0]["json"]["stream"] is False
        assert result.tool_calls == [
            NativeToolCall(
                id="call_9",
                name="web_search",
                arguments={"query": "肺癌免疫治疗 最新论文 2026", "max_results": 3},
            )
        ]

    def test_the_repair_is_billed_as_a_second_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logged: List[Dict[str, Any]] = []
        attempts: List[tuple] = []
        monkeypatch.setattr(llm_mod, "_log_usage", lambda **kw: logged.append(kw))
        monkeypatch.setattr(
            llm_mod,
            "_record_attempt_context",
            lambda call_id, attempt_no, *a, **k: attempts.append((call_id, attempt_no)),
        )
        fake = _FakeAsyncClient(
            [_tool_call_line("web_search", ""), _finish_line()],
            posted=_FakePostedResponse(
                body={
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "call_9",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": '{"query": "q"}',
                                        },
                                    }
                                ]
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                }
            ),
        )
        _install(monkeypatch, fake)

        _run(_client())

        assert [kw["total_tokens"] for kw in logged] == [18]
        # The streamed attempt is 1, the repair is attempt 2 of the same
        # logical call — one logical call, two billed attempts.
        assert [attempt for _, attempt in attempts] == [1, 2]
        assert len({call_id for call_id, _ in attempts}) == 1

    def test_a_healthy_stream_is_left_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeAsyncClient(
            [
                _tool_call_line("web_search", '{"query": "already fine"}'),
                _finish_line(),
            ],
            posted=_FakePostedResponse(body={"choices": []}),
        )
        _install(monkeypatch, fake)

        result = _run(_client())

        assert fake.post_calls == []
        assert result.tool_calls[0].arguments == {"query": "already fine"}

    def test_the_switch_disables_the_extra_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_NONSTREAM_TOOLCALL_REPAIR", "0")
        fake = _FakeAsyncClient(
            [_tool_call_line("web_search", ""), _finish_line()],
            posted=_FakePostedResponse(body={"choices": []}),
        )
        _install(monkeypatch, fake)

        result = _run(_client())

        assert fake.post_calls == []
        assert result.tool_calls[0].arguments == {}

    def test_an_equally_empty_re_ask_keeps_the_stream_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeAsyncClient(
            [_tool_call_line("web_search", ""), _finish_line()],
            posted=_FakePostedResponse(
                body={
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "call_9",
                                        "function": {"name": "web_search", "arguments": ""},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
        )
        _install(monkeypatch, fake)

        result = _run(_client())

        assert len(fake.post_calls) == 1
        assert result.tool_calls == [
            NativeToolCall(id="call_1", name="web_search", arguments={})
        ]

    def test_a_failing_re_ask_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeAsyncClient(
            [_tool_call_line("web_search", ""), _finish_line()],
            post_raises=httpx.ConnectError("gateway down"),
        )
        _install(monkeypatch, fake)

        result = _run(_client())

        assert len(fake.post_calls) == 1
        assert result.tool_calls == [
            NativeToolCall(id="call_1", name="web_search", arguments={})
        ]

    def test_an_http_error_re_ask_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeAsyncClient(
            [_tool_call_line("web_search", ""), _finish_line()],
            posted=_FakePostedResponse(status_code=504, text="gateway timeout"),
        )
        _install(monkeypatch, fake)

        result = _run(_client())

        assert len(fake.post_calls) == 1
        assert result.tool_calls == [
            NativeToolCall(id="call_1", name="web_search", arguments={})
        ]
