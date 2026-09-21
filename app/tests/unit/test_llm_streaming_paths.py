"""Streaming transport for long LLM calls (LOCAL_INFRA §7 / journey finding F1).

Long generations must travel over SSE: buffered non-streaming calls get cut by
the upstream gateway with 504s (observed 2026-09-19/21 in production). These
tests pin the streaming variants of the previously non-streaming call sites:
plan decomposer, context-compaction summarizer (via the shared collector),
conversation-quality evaluator, and phase narrator.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional

import httpx
import pytest

import app.llm as llm_mod
from app.llm import LLMClient, stream_chat_collect_async


@pytest.fixture(autouse=True)
def _no_usage_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod, "_log_usage", lambda **_: None)
    monkeypatch.setattr(llm_mod, "_log_call_metrics", lambda **_: None)


class _NoopLimiter:
    def acquire(self) -> None:
        return None

    async def acquire_async(self) -> None:
        return None


def _sse_lines(parts: List[str]) -> List[str]:
    return [
        "data: " + json.dumps({"choices": [{"delta": {"content": part}}]})
        for part in parts
    ] + ["data: [DONE]"]


class _FakeStreamResponse:
    def __init__(self, status_code: int = 200, lines: Optional[List[str]] = None, body: str = "") -> None:
        self.status_code = status_code
        self._lines = list(lines or [])
        self._body = body
        self.headers: Dict[str, str] = {}

    def read(self) -> "_FakeStreamResponse":
        return self

    @property
    def text(self) -> str:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
            response = httpx.Response(self.status_code, text=self._body, request=request)
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=request, response=response)

    def iter_lines(self) -> Iterator[str]:
        return iter(self._lines)

    def __enter__(self) -> "_FakeStreamResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeSyncClient:
    def __init__(self, responses: List[_FakeStreamResponse]) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def stream(self, method: str, url: str, headers: Any = None, json: Any = None, timeout: Any = None) -> _FakeStreamResponse:
        self.calls.append({"method": method, "url": url, "json": json})
        return self._responses.pop(0)


def _client() -> LLMClient:
    client = LLMClient(
        provider="platform",
        api_key="sk-test",
        url="https://example.invalid/v1/chat/completions",
        model="qwen-test",
    )
    client.backoff_base = 0
    return client


class TestLLMClientStreamChat:
    def test_streams_deltas_and_marks_payload_stream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeSyncClient([_FakeStreamResponse(200, _sse_lines(["Hello, ", "world"]))])
        monkeypatch.setattr(llm_mod, "_get_shared_sync_client", lambda: fake)
        monkeypatch.setattr(llm_mod, "_outbound_limiter", _NoopLimiter())

        out = "".join(_client().stream_chat("ping"))

        assert out == "Hello, world"
        assert fake.calls[0]["json"]["stream"] is True

    def test_retries_504_before_any_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeSyncClient([
            _FakeStreamResponse(504, body="gateway timeout"),
            _FakeStreamResponse(200, _sse_lines(["done"])),
        ])
        monkeypatch.setattr(llm_mod, "_get_shared_sync_client", lambda: fake)
        monkeypatch.setattr(llm_mod, "_outbound_limiter", _NoopLimiter())

        out = "".join(_client().stream_chat("ping", retries=1))

        assert out == "done"
        assert len(fake.calls) == 2

    def test_no_retry_after_content_emitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _DyingResponse(_FakeStreamResponse):
            def iter_lines(self) -> Iterator[str]:
                yield "data: " + json.dumps({"choices": [{"delta": {"content": "partial"}}]})
                raise ConnectionError("stream dropped mid-body")

        fake = _FakeSyncClient([_DyingResponse(200), _DyingResponse(200)])
        monkeypatch.setattr(llm_mod, "_get_shared_sync_client", lambda: fake)
        monkeypatch.setattr(llm_mod, "_outbound_limiter", _NoopLimiter())

        with pytest.raises(RuntimeError):
            "".join(_client().stream_chat("ping", retries=3))
        # No retry once deltas were emitted — retrying would duplicate content.
        assert len(fake.calls) == 1


class TestStreamChatCollectAsync:
    async def test_prefers_streaming(self) -> None:
        class _StreamingClient:
            def __init__(self) -> None:
                self.streamed = False

            async def stream_chat_async(self, prompt: str, **kwargs: Any) -> Any:
                self.streamed = True
                yield "a"
                yield "b"

        client = _StreamingClient()
        assert await stream_chat_collect_async(client, "p") == "ab"
        assert client.streamed

    async def test_falls_back_to_chat_async(self) -> None:
        class _NoStream:
            async def chat_async(self, prompt: str, **kwargs: Any) -> str:
                return "fallback"

        assert await stream_chat_collect_async(_NoStream(), "p") == "fallback"

    async def test_falls_back_to_sync_chat(self) -> None:
        class _SyncOnly:
            def chat(self, prompt: str, **kwargs: Any) -> str:
                return "sync"

        assert await stream_chat_collect_async(_SyncOnly(), "p") == "sync"


class TestLLMServiceStreamChat:
    def test_passthrough(self) -> None:
        from app.services.llm.llm_service import LLMService

        class _Client:
            provider = "platform"
            model = "m"

            def stream_chat(self, prompt: str, **kwargs: Any) -> Iterator[str]:
                yield "x"
                yield "y"

        svc = LLMService(_Client())
        assert "".join(svc.stream_chat("p")) == "xy"

    def test_requires_streaming_support(self) -> None:
        from app.services.llm.llm_service import LLMService

        svc = LLMService(object())
        with pytest.raises(RuntimeError):
            "".join(svc.stream_chat("p"))


class TestDecomposerStreaming:
    def _service(self) -> Any:
        from app.services.llm.decomposer_service import PlanDecomposerLLMService

        service = PlanDecomposerLLMService.__new__(PlanDecomposerLLMService)
        service._settings = SimpleNamespace(model="qwen-test")
        return service

    def test_generate_uses_streaming(self) -> None:
        class _Svc:
            def __init__(self) -> None:
                self.stream_called = False

            def stream_chat(self, prompt: str, model: Any = None) -> Iterator[str]:
                self.stream_called = True
                yield json.dumps({"target_node_id": 3, "mode": "nodes", "children": []})

            def chat(self, prompt: str, **kwargs: Any) -> str:
                raise AssertionError("buffered chat() must not be used for decomposition")

        svc = _Svc()
        service = self._service()
        service._llm = svc
        result = service.generate("prompt")
        assert svc.stream_called
        assert result.children == []

    def test_decide_search_uses_streaming(self) -> None:
        class _Svc:
            def __init__(self) -> None:
                self.stream_called = False

            def stream_chat(self, prompt: str, model: Any = None) -> Iterator[str]:
                self.stream_called = True
                yield "yes"

            def chat(self, prompt: str, **kwargs: Any) -> str:
                raise AssertionError("buffered chat() must not be used")

        svc = _Svc()
        service = self._service()
        service._llm = svc
        assert service.decide_search("prompt") == "yes"
        assert svc.stream_called


class TestEvaluatorStreaming:
    async def test_quality_evaluator_uses_streaming_collector(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.services.conversation_quality.evaluator as ev

        calls: Dict[str, Any] = {}

        async def _fake_collect(client: Any, prompt: str, **kwargs: Any) -> str:
            calls["kwargs"] = kwargs
            return json.dumps({
                "satisfaction_level": "satisfied",
                "confidence": 0.9,
                "feedback_relation": "satisfied_confirmation",
                "evidence": [{"source": "run_fact", "quote": "q", "explanation": "e"}],
            })

        monkeypatch.setattr(ev, "stream_chat_collect_async", _fake_collect)

        class _Client:
            provider = "platform"
            model = "m"

        evaluator = ev.ConversationQualityEvaluator(client=_Client())
        result, _ = await evaluator.evaluate(
            snapshot={},
            feedback_message=None,
            evaluation_basis="final",
            session_id="s1",
        )
        assert result.satisfaction_level == "satisfied"
        assert calls["kwargs"].get("max_tokens") == 1800


class TestPhaseNarratorStreaming:
    async def test_narrator_uses_streaming_collector(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.services.plans.phase_narrator as pn

        called = {"n": 0}

        async def _fake_collect(client: Any, prompt: str, **kwargs: Any) -> str:
            called["n"] += 1
            return '{"1": "阶段一"}'

        class _Item:
            def __init__(self, name: str) -> None:
                self.name = name

        class _Phase:
            def __init__(self, phase_id: int, items: List[_Item]) -> None:
                self.phase_id = phase_id
                self.items = items

        todo = SimpleNamespace(phases=[_Phase(1, [_Item("a")]), _Phase(2, [_Item("b")])])
        monkeypatch.setattr(pn, "stream_chat_collect_async", _fake_collect)
        monkeypatch.setattr(pn, "get_default_client", lambda: object())
        monkeypatch.setattr(
            pn,
            "build_full_plan_todo_list",
            lambda tree, ordering_mode=None: todo,
        )

        titles = await pn.generate_phase_titles(object())
        assert called["n"] == 1
        assert isinstance(titles, dict)
