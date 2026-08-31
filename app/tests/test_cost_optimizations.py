"""Tests for the 2026-08-31 cost-optimization patches:
- P0a deep_think circuit breaker on non-retryable LLM provider errors
- P0b graceful shutdown cancels in-flight LLM tasks
- P1 literature fulltext truncation + PMCID persistent cache
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest


# ---------------------------------------------------------------------------
# P0a: circuit breaker
# ---------------------------------------------------------------------------

class _FailingLLMClient:
    """stream_chat_with_tools_async always raises the configured exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    async def stream_chat_with_tools_async(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise self._exc


def _make_agent(llm_client: Any, max_iterations: int = 30):
    from app.services.deep_think_agent import DeepThinkAgent

    return DeepThinkAgent(
        llm_client=llm_client,
        available_tools=[],
        tool_executor=None,
        max_iterations=max_iterations,
    )


async def _run_agent(agent: Any) -> Any:
    return await agent.think("测试任务：生成一段文字")


@pytest.mark.asyncio
async def test_deep_think_aborts_immediately_on_403_balance_error() -> None:
    from app.services import deep_think_agent as dta

    if not hasattr(dta, "_classify_llm_provider_error"):
        pytest.skip("circuit breaker patch not applied")

    client = _FailingLLMClient(
        RuntimeError("LLM HTTP 403: Insufficient account balance")
    )
    agent = _make_agent(client, max_iterations=30)
    result = await _run_agent(agent)

    # Must abort on the FIRST non-retryable failure, not spin 30 iterations.
    assert client.calls == 1, f"expected 1 LLM call, got {client.calls}"
    assert result.fallback_used is True
    assert "额度不足" in result.final_answer or "403" in result.final_answer


@pytest.mark.asyncio
async def test_deep_think_aborts_after_consecutive_transient_failures() -> None:
    from app.services import deep_think_agent as dta

    if not hasattr(dta, "_classify_llm_provider_error"):
        pytest.skip("circuit breaker patch not applied")

    client = _FailingLLMClient(RuntimeError("connection reset by peer"))
    agent = _make_agent(client, max_iterations=30)
    result = await _run_agent(agent)

    # Transient errors: circuit breaker trips after N consecutive failures (default 5),
    # far below max_iterations.
    assert client.calls <= 6, f"expected <=6 LLM calls, got {client.calls}"
    assert result.fallback_used is True
    assert "连续调用失败" in result.final_answer or "暂停" in result.final_answer


def test_classify_llm_provider_error_marks_403_non_retryable() -> None:
    from app.services.deep_think_agent import _classify_llm_provider_error

    classified = _classify_llm_provider_error(
        RuntimeError("LLM HTTP 403: Insufficient account balance")
    )
    assert classified is not None
    assert classified.retryable is False
    assert classified.status_code == 403

    # Unknown/transient failure -> None (retryable)
    assert _classify_llm_provider_error(RuntimeError("tcp connection closed")) is None


# ---------------------------------------------------------------------------
# P0b: graceful shutdown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_inflight_llm_calls_cancels_registered_task() -> None:
    import app.llm as llm_mod

    if not hasattr(llm_mod, "cancel_inflight_llm_calls"):
        pytest.skip("graceful shutdown patch not applied")

    started = asyncio.Event()

    async def fake_llm_call() -> None:
        task = llm_mod._register_inflight_task()
        started.set()
        try:
            await asyncio.sleep(3600)
        finally:
            llm_mod._unregister_inflight_task(task)

    worker = asyncio.create_task(fake_llm_call())
    await asyncio.wait_for(started.wait(), timeout=2)

    cancelled = await llm_mod.cancel_inflight_llm_calls(grace_sec=2.0)
    assert cancelled == 1
    assert worker.done()
    with pytest.raises(asyncio.CancelledError):
        await worker


@pytest.mark.asyncio
async def test_track_inflight_decorator_registers_and_unregisters() -> None:
    import app.llm as llm_mod

    if not hasattr(llm_mod, "_track_inflight"):
        pytest.skip("graceful shutdown patch not applied")

    seen: List[bool] = []

    @llm_mod._track_inflight
    async def dummy() -> str:
        current = asyncio.current_task()
        with llm_mod._inflight_llm_lock:
            seen.append(current in set(llm_mod._inflight_llm_tasks))
        return "ok"

    assert await dummy() == "ok"
    assert seen == [True]
    with llm_mod._inflight_llm_lock:
        assert len(llm_mod._inflight_llm_tasks) == 0


# ---------------------------------------------------------------------------
# P1: literature truncation + cache
# ---------------------------------------------------------------------------

def test_fulltext_truncation_uses_env_default_12k() -> None:
    from tool_box.tools_impl import literature_pipeline as lp

    assert lp._MAX_FULLTEXT_CHARS == 12000


def test_evidence_card_excerpt_respects_truncation() -> None:
    from tool_box.tools_impl import literature_pipeline as lp

    record = SimpleNamespace(
        citekey="test2024",
        title="Test paper",
        authors=["A"],
        year=2024,
        journal="J",
        doi="10.1/x",
        pmid="123",
        pmcid="PMC123",
        url="http://x",
        abstract="abstract text",
    )
    huge_text = "x" * 80_000
    card = lp._build_study_card(record, full_text=huge_text)
    snippets = json.dumps(card, ensure_ascii=False)
    assert "x" * 12001 not in snippets
    assert card["evidence_tier"] == "full_text"


def test_literature_cache_roundtrip(tmp_path, monkeypatch) -> None:
    from tool_box.tools_impl import literature_pipeline as lp

    monkeypatch.setattr(lp, "_LITERATURE_CACHE_DIR", tmp_path)
    assert lp._literature_cache_read("PMC999") is None

    lp._literature_cache_write("PMC999", "full text body")
    assert lp._literature_cache_read("PMC999") == "full text body"


def test_literature_cache_expires(tmp_path, monkeypatch) -> None:
    from tool_box.tools_impl import literature_pipeline as lp

    monkeypatch.setattr(lp, "_LITERATURE_CACHE_DIR", tmp_path)
    lp._literature_cache_write("PMC999", "old text")

    # Backdate the meta file beyond TTL
    _, meta_path = lp._literature_cache_paths("PMC999")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["fetched_at"] = time.time() - (8 * 24 * 3600)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert lp._literature_cache_read("PMC999") is None


@pytest.mark.asyncio
async def test_download_pmc_pdf_uses_cache(tmp_path, monkeypatch) -> None:
    from tool_box.tools_impl import literature_pipeline as lp

    monkeypatch.setattr(lp, "_LITERATURE_CACHE_DIR", tmp_path / "cache")
    lp._literature_cache_write("PMC777", "cached full text")

    class _BoomClient:
        async def get(self, *a: Any, **k: Any) -> Any:
            raise AssertionError("network must not be called on cache hit")

    ok, err, text = await lp._download_pmc_pdf(_BoomClient(), "PMC777", tmp_path / "x.pdf")
    assert ok is True
    assert err is None
    assert text == "cached full text"
