"""Tests for the deep_think context working-set budget (compaction trigger)."""
from __future__ import annotations

import asyncio

import pytest

from app.services.context.context_manager import ContextWindowManager


def _make_messages(n: int, tokens_per_msg: int = 2000) -> list:
    """n messages; first is system, rest are user/assistant filler."""
    msgs = [{"role": "system", "content": "You are a research agent."}]
    for i in range(n - 1):
        msgs.append({
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"turn {i}: " + ("数据 " * tokens_per_msg),
        })
    return msgs


def _estimate_all(messages: list) -> int:
    from app.services.context.context_manager import estimate_messages_tokens
    return estimate_messages_tokens(messages)


def test_budget_triggers_warning_below_model_window_ratio() -> None:
    # qwen3.7-max has a 1M window; 75% ratio would never fire for ~50K contexts.
    mgr = ContextWindowManager(model="qwen3.7-max", budget_tokens=32_000)
    messages = _make_messages(30)  # roughly 30 * 2K = 60K tokens
    total = _estimate_all(messages)
    assert total > 32_000, "fixture should exceed the budget"
    usage = mgr.check_usage(messages)
    assert usage.warning is True, "budget must trigger warning below the 750K model-ratio line"
    assert usage.critical is False


def test_no_budget_keeps_ratio_only_behavior() -> None:
    mgr = ContextWindowManager(model="qwen3.7-max")
    messages = _make_messages(30)  # ~60K << 750K warning line
    usage = mgr.check_usage(messages)
    assert usage.warning is False


def test_budget_none_disables_budget() -> None:
    mgr = ContextWindowManager(model="qwen3.7-max", budget_tokens=0)
    assert mgr.budget_tokens is None
    mgr2 = ContextWindowManager(model="qwen3.7-max", budget_tokens="abc")
    assert mgr2.budget_tokens is None


@pytest.mark.asyncio
async def test_compact_if_needed_fires_on_budget() -> None:
    mgr = ContextWindowManager(model="qwen3.7-max", budget_tokens=32_000)
    messages = _make_messages(30)  # ~60K tokens, 30 messages (>= MIN_MESSAGES)

    async def fake_summarizer(text: str) -> str:
        return "（测试摘要）此前对话讨论了噬菌体疗法的研究现状与实验设计。"

    compacted = await mgr.compact_if_needed(messages, summarizer=fake_summarizer)
    assert mgr._compaction_count == 1
    # System prompt preserved first
    assert compacted[0]["role"] == "system"
    # A summary marker present
    assert any("[Context Summary" in str(m.get("content", "")) for m in compacted)
    # Recent messages kept intact
    kept = [m for m in compacted if "[Context Summary" not in str(m.get("content", ""))]
    assert kept[1:] == messages[-mgr.KEEP_RECENT:]
    # Compacted size materially smaller
    assert _estimate_all(compacted) < _estimate_all(messages) / 2


@pytest.mark.asyncio
async def test_compact_if_needed_skips_below_budget() -> None:
    mgr = ContextWindowManager(model="qwen3.7-max", budget_tokens=32_000)
    messages = _make_messages(10, tokens_per_msg=100)  # well below budget
    total = _estimate_all(messages)
    assert total < 32_000

    async def fake_summarizer(text: str) -> str:
        raise AssertionError("summarizer must not be called below budget")

    compacted = await mgr.compact_if_needed(messages, summarizer=fake_summarizer)
    assert mgr._compaction_count == 0
    assert compacted == messages


@pytest.mark.asyncio
async def test_compact_skips_when_too_few_messages() -> None:
    mgr = ContextWindowManager(model="qwen3.7-max", budget_tokens=8_000)
    messages = _make_messages(5, tokens_per_msg=4000)  # over budget but < 8 messages

    async def fake_summarizer(text: str) -> str:
        raise AssertionError("summarizer must not run for tiny conversations")

    compacted = await mgr.compact_if_needed(messages, summarizer=fake_summarizer)
    assert mgr._compaction_count == 0
    assert compacted == messages
