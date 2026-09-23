"""Prompt-cache contract tests: history as role messages + model-aware budget.

The system prompt builders must not embed chat history; history enters the
LLM call as OpenAI-style role messages so the system prompt keeps a
byte-stable prefix across turns. The compaction budget is resolved from the
model's real context window instead of a fixed 32k.
"""

from __future__ import annotations

import pytest

from app.services.deep_think.prompts import (
    _append_recent_chat_history,
    _extract_history_messages,
)
from app.services.deep_think.text_utils import _resolve_context_budget_tokens


def _ctx(history, **extra):
    base = {"chat_history": history}
    base.update(extra)
    return base


# --- _extract_history_messages ----------------------------------------------


def test_extract_history_messages_roles_and_clip() -> None:
    history = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "system", "content": "must be skipped"},
        {"role": "user", "content": "x" * 600},
    ]
    messages = _extract_history_messages(_ctx(history))
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "u1"
    assert messages[1]["content"] == "a1"
    assert len(messages[2]["content"]) == 503  # 500 clip + "..."
    assert messages[2]["content"].endswith("...")


def test_extract_history_messages_merges_consecutive_same_role() -> None:
    history = [
        {"role": "assistant", "content": "a1"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u1"},
    ]
    messages = _extract_history_messages(_ctx(history))
    assert messages == [
        {"role": "assistant", "content": "a1\na2"},
        {"role": "user", "content": "u1"},
    ]


def test_extract_history_messages_drops_trailing_current_query() -> None:
    history = [
        {"role": "user", "content": "之前的问题"},
        {"role": "assistant", "content": "之前的回答"},
        {"role": "user", "content": "当前问题"},
    ]
    messages = _extract_history_messages(_ctx(history), current_user_query="当前问题")
    assert [m["content"] for m in messages] == ["之前的问题", "之前的回答"]
    # Without the query hint the trailing user item is kept.
    kept = _extract_history_messages(_ctx(history))
    assert kept[-1] == {"role": "user", "content": "当前问题"}


def test_extract_history_messages_empty_context() -> None:
    assert _extract_history_messages(None) == []
    assert _extract_history_messages({}) == []
    assert _extract_history_messages({"chat_history": []}) == []


def test_extract_history_messages_respects_context_limit() -> None:
    history = [
        {"role": "user", "content": f"q{i}"} for i in range(10)
    ]
    messages = _extract_history_messages(_ctx(history, chat_history_max_messages=3))
    # same-role items merge into one message after the limit is applied
    assert messages == [{"role": "user", "content": "q7\nq8\nq9"}]


def test_extract_history_messages_brief_followup_policy() -> None:
    history = [
        {"role": "user", "content": f"q{i}"} for i in range(8)
    ] + [{"role": "assistant", "content": ""}, {"role": "assistant", "content": "final"}]
    context = _ctx(
        history,
        request_tier="execute",
        brevity_hint=True,
    )
    messages = _extract_history_messages(context)
    # empty assistant dropped before slicing; last 6 survive
    contents = [m["content"] for m in messages]
    assert "q0" not in contents and "q1" not in contents and "q2" not in contents
    assert "final" in contents
    assert len(messages) <= 6


def test_append_recent_chat_history_legacy_form_unchanged() -> None:
    history = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]
    prompt = _append_recent_chat_history("BASE", _ctx(history))
    assert prompt.startswith("BASE")
    assert "=== RECENT CONVERSATION ===" in prompt
    assert "[user]: u1" in prompt
    assert "[assistant]: a1" in prompt
    assert _append_recent_chat_history("BASE", None) == "BASE"


# --- _resolve_context_budget_tokens ------------------------------------------


def test_budget_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEP_THINK_CONTEXT_BUDGET_TOKENS", "48000")
    assert _resolve_context_budget_tokens("qwen3.7-max") == 48000
    # legacy semantics: 0 disables the budget
    monkeypatch.setenv("DEEP_THINK_CONTEXT_BUDGET_TOKENS", "0")
    assert _resolve_context_budget_tokens("qwen3.7-max") == 0


def test_budget_model_window_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEP_THINK_CONTEXT_BUDGET_TOKENS", raising=False)
    monkeypatch.delenv("DEEP_THINK_CONTEXT_BUDGET_RATIO", raising=False)
    monkeypatch.delenv("DEEP_THINK_CONTEXT_BUDGET_MAX", raising=False)
    # qwen3.7-max: 1M * 0.5 = 500k -> capped at 131072
    assert _resolve_context_budget_tokens("qwen3.7-max") == 131072
    # qwen-max: 262144 * 0.5 = 131072
    assert _resolve_context_budget_tokens("qwen-max-latest") == 131072
    # gpt-4o-mini: 128000 * 0.5 = 64000
    assert _resolve_context_budget_tokens("gpt-4o-mini") == 64000
    # unknown model falls back to the default window
    assert _resolve_context_budget_tokens("some-new-model") == 64000
    assert _resolve_context_budget_tokens("") == 64000


def test_budget_floor_and_ratio_clamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEP_THINK_CONTEXT_BUDGET_TOKENS", raising=False)
    monkeypatch.delenv("DEEP_THINK_CONTEXT_BUDGET_MAX", raising=False)
    # tiny ratio still respects the 32k floor
    monkeypatch.setenv("DEEP_THINK_CONTEXT_BUDGET_RATIO", "0.05")
    assert _resolve_context_budget_tokens("gpt-4o-mini") == 32000
    # ratio above 0.9 is clamped, then the cap applies
    monkeypatch.setenv("DEEP_THINK_CONTEXT_BUDGET_RATIO", "0.95")
    assert _resolve_context_budget_tokens("qwen3.7-max") == 131072


def test_budget_custom_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEP_THINK_CONTEXT_BUDGET_TOKENS", raising=False)
    monkeypatch.delenv("DEEP_THINK_CONTEXT_BUDGET_RATIO", raising=False)
    monkeypatch.setenv("DEEP_THINK_CONTEXT_BUDGET_MAX", "64000")
    assert _resolve_context_budget_tokens("qwen3.7-max") == 64000


def test_budget_invalid_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEP_THINK_CONTEXT_BUDGET_TOKENS", "not-a-number")
    # invalid override is ignored, model-aware resolution applies
    assert _resolve_context_budget_tokens("gpt-4o-mini") == 64000
