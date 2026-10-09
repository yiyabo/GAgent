"""Deterministic pre-compaction reclaim, cache hysteresis and summary framing.

Covers the Hermes-derived compaction trilogy on ``ContextWindowManager``:

* B1 — reclaim deterministically (dedupe identical tool results, demote oversized
  tool bodies, retire stale images) before deciding a boundary, and only call the
  summarizer when that is not enough.
* B2 — a commit breaks the prompt-cache prefix, so a sub-threshold reclaim batch
  is deferred until the context has regrown or pressure forces the issue.
* B3 — the summary is framed as REFERENCE ONLY and its role alternates with the
  retained tail instead of inserting a second ``system`` row.
"""

from __future__ import annotations

import json

import pytest

from app.services.context.context_manager import (
    SUMMARY_PREFIX,
    ContextWindowManager,
    estimate_messages_tokens,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _batch(call_id: str, body: str, tool_name: str = "document_reader") -> list:
    return [
        {"role": "user", "content": f"read {call_id}.txt"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps({"path": f"{call_id}.txt"}),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": body},
    ]


def _image(index: int, payload_chars: int = 800) -> dict:
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{index}" + "A" * payload_chars},
    }


def _conversation(
    batches: list,
    *,
    tail_roles: list | None = None,
    tail_content=None,
) -> list:
    roles = tail_roles or ["user", "assistant", "user", "assistant", "user", "assistant"]
    messages = [{"role": "system", "content": "You are a research agent."}]
    for batch in batches:
        messages.extend(batch)
    for index, role in enumerate(roles):
        content = tail_content(index, role) if tail_content else f"tail {index}"
        messages.append({"role": role, "content": content})
    return messages


def _usage_after_reclaim(mgr: ContextWindowManager, messages: list) -> int:
    """Tokens the manager would keep if it committed the deterministic reclaim."""
    system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
    start = 1 if system_msg else 0
    keep = min(mgr.KEEP_RECENT, len(messages) - start)
    split = mgr._tool_safe_split_point(messages, len(messages) - keep, start)
    retired, _, _ = mgr._retire_old_images(messages)
    compactable, _ = mgr._dedupe_tool_results(retired[start:split])
    compactable, _ = mgr._demote_oversized_tool_bodies(compactable)
    return estimate_messages_tokens(
        ([system_msg] if system_msg else []) + compactable + retired[split:]
    )


def _manager(messages: list, **knobs) -> ContextWindowManager:
    """Manager already above the warning line (1M window keeps it non-critical)."""
    mgr = ContextWindowManager(model="qwen3.7-max")
    mgr.budget_tokens = max(1, estimate_messages_tokens(messages) - 1)
    for key, value in knobs.items():
        setattr(mgr, key, value)
    return mgr


def _recording_summarizer(calls: list):
    async def summarizer(text: str) -> str:
        calls.append(text)
        return "Summary of earlier work."

    return summarizer


# ---------------------------------------------------------------------------
# B1 — deterministic reclaim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_tool_results_keep_only_the_newest_and_skip_the_llm() -> None:
    body = "D" * 4000
    messages = _conversation([_batch("c0", body), _batch("c1", body)])
    mgr = _manager(messages, prune_min_chars=5000, min_reclaim_tokens=1)
    calls: list = []

    result = await mgr.compact_if_needed(messages, summarizer=_recording_summarizer(calls))

    assert result is not messages
    tools = [m for m in result if m["role"] == "tool"]
    assert len(tools) == 2
    assert tools[0]["content"].startswith("[duplicate of latest document_reader result")
    assert tools[1]["content"] == body
    assert calls == []
    assert mgr._compaction_count == 0


@pytest.mark.asyncio
async def test_oversized_tool_body_is_demoted_to_a_bounded_stub() -> None:
    messages = _conversation([_batch("c0", "H" * 3000 + "T" * 3000)])
    mgr = _manager(messages, prune_min_chars=2000, min_reclaim_tokens=1)
    calls: list = []

    result = await mgr.compact_if_needed(messages, summarizer=_recording_summarizer(calls))

    tool = next(m for m in result if m["role"] == "tool")
    assert len(tool["content"]) <= 2000
    assert "demoted before compaction" in tool["content"]
    assert tool["content"].startswith("H")
    assert tool["content"].endswith("T")
    assert calls == []


@pytest.mark.asyncio
async def test_reclaim_keeps_tool_call_arguments_exact_and_pairing_valid() -> None:
    body = "R" * 6000
    messages = _conversation(
        [_batch("c0", body), _batch("c1", "Q" * 6000), _batch("c2", body)]
    )
    original_calls = [m["tool_calls"] for m in messages if m.get("tool_calls")]
    mgr = _manager(messages, prune_min_chars=2000, min_reclaim_tokens=1)

    result = await mgr.compact_if_needed(
        messages, summarizer=_recording_summarizer([])
    )

    assert result is not messages  # committed deterministic reclaim
    kept_calls = [m["tool_calls"] for m in result if m.get("tool_calls")]
    for calls_list in original_calls:
        assert calls_list in kept_calls  # byte-exact call arguments
    for tool in (m for m in result if m["role"] == "tool"):
        assert len(tool["content"]) <= 2000  # body demoted, id/pairing untouched

    pending = set()
    for message in result:
        for call in message.get("tool_calls") or []:
            pending.add(call["id"])
        if message["role"] == "tool":
            assert message["tool_call_id"] in pending
            pending.discard(message["tool_call_id"])
    assert not pending


@pytest.mark.asyncio
async def test_stale_images_retire_keeping_the_newest_three() -> None:
    def tail_content(index: int, role: str):
        if index < 5:
            return [{"type": "text", "text": f"look {index}"}, _image(index)]
        return f"tail {index}"

    messages = _conversation(
        [_batch("c0", "B" * 6000)],
        tail_content=tail_content,
    )
    mgr = _manager(messages, prune_min_chars=5000, min_reclaim_tokens=1)

    result = await mgr.compact_if_needed(
        messages, summarizer=_recording_summarizer([])
    )

    live = [
        part
        for message in result
        for part in (message.get("content") if isinstance(message.get("content"), list) else [])
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    notes = [
        part
        for message in result
        for part in (message.get("content") if isinstance(message.get("content"), list) else [])
        if isinstance(part, dict) and "older image omitted" in str(part.get("text") or "")
    ]
    assert len(live) == ContextWindowManager.KEEP_TOOL_IMAGES
    assert len(notes) == 2
    assert live[0]["image_url"]["url"].endswith("2" + "A" * 800)
    assert live[-1]["image_url"]["url"].endswith("4" + "A" * 800)


@pytest.mark.asyncio
async def test_image_only_reclaim_is_not_blocked_by_the_token_blind_estimator() -> None:
    """Image parts score 0 tokens, so their reclaim is weighed separately.

    Without that weighing the batch would look like a 0-token reclaim and the
    hysteresis gate would refuse to retire stale frames forever.
    """
    big = 20_000

    def tail_content(index: int, role: str):
        if index < 5:
            return [{"type": "text", "text": f"look {index}"}, _image(index, big)]
        return f"tail {index}"

    messages = _conversation([_batch("c0", "small body")], tail_content=tail_content)
    mgr = _manager(messages, min_reclaim_tokens=4096)
    calls: list = []

    result = await mgr.compact_if_needed(messages, summarizer=_recording_summarizer(calls))

    assert calls  # committed, so it may also summarize
    live = [
        part
        for message in result
        for part in (message.get("content") if isinstance(message.get("content"), list) else [])
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    notes = [
        part
        for message in result
        for part in (message.get("content") if isinstance(message.get("content"), list) else [])
        if isinstance(part, dict) and "older image omitted" in str(part.get("text") or "")
    ]
    assert len(live) == ContextWindowManager.KEEP_TOOL_IMAGES
    assert len(notes) == 2


# ---------------------------------------------------------------------------
# B2 — cache hysteresis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_small_reclaim_batch_is_deferred() -> None:
    messages = _conversation([_batch("c0", "S" * 3000)])
    mgr = _manager(
        messages, prune_min_chars=500, min_reclaim_tokens=100_000
    )
    calls: list = []

    result = await mgr.compact_if_needed(messages, summarizer=_recording_summarizer(calls))

    assert result is messages
    assert calls == []
    assert mgr._last_commit_used_tokens is None


@pytest.mark.asyncio
async def test_reclaim_commits_after_the_context_regrows() -> None:
    messages = _conversation([_batch("c0", "S" * 3000)])
    mgr = _manager(messages, prune_min_chars=500, min_reclaim_tokens=5000)
    # Warning line sits at the post-reclaim size: firing needs a commit, and the
    # reclaim alone is not enough to clear the threshold.
    mgr.budget_tokens = _usage_after_reclaim(mgr, messages)
    # A previous commit happened; the context has regrown well past min_reclaim.
    mgr._last_commit_used_tokens = estimate_messages_tokens(messages) - 6000
    calls: list = []

    result = await mgr.compact_if_needed(messages, summarizer=_recording_summarizer(calls))

    assert len(calls) == 1
    assert result is not messages
    assert mgr._last_commit_used_tokens is not None


@pytest.mark.asyncio
async def test_critical_pressure_bypasses_hysteresis() -> None:
    messages = _conversation([_batch("c0", "S" * 3000)])
    mgr = ContextWindowManager(
        model="qwen3.7-max", max_context_tokens=900, warning_ratio=0.75
    )
    mgr.prune_min_chars = 2500
    mgr.min_reclaim_tokens = 100_000
    assert mgr.check_usage(messages).critical is True
    calls: list = []

    result = await mgr.compact_if_needed(messages, summarizer=_recording_summarizer(calls))

    assert calls and result is not messages


@pytest.mark.asyncio
async def test_force_bypasses_hysteresis() -> None:
    messages = _conversation([_batch("c0", "S" * 3000)])
    mgr = _manager(messages, prune_min_chars=500, min_reclaim_tokens=100_000)
    calls: list = []

    result = await mgr.compact_if_needed(
        messages, summarizer=_recording_summarizer(calls), force=True
    )

    assert calls and result is not messages


# ---------------------------------------------------------------------------
# B3 — summary framing and placement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_is_reference_only_with_exactly_one_system_message() -> None:
    messages = _conversation([_batch("c0", "small body")])
    mgr = ContextWindowManager(model="qwen3.7-max")
    mgr.budget_tokens = estimate_messages_tokens(messages) - 1
    calls: list = []

    result = await mgr.compact_if_needed(messages, summarizer=_recording_summarizer(calls))

    assert calls  # nothing deterministic to reclaim: the LLM path runs
    assert result[0]["role"] == "system"
    assert [m["role"] for m in result].count("system") == 1
    summary = result[1]["content"]
    assert "REFERENCE ONLY" in summary
    assert "Respond ONLY to the latest user message" in summary
    assert "Topic overlap" in summary
    assert "Context Summary" in summary
    # recent[0] is a user turn → the summary alternates as assistant.
    assert result[1]["role"] == "assistant"
    assert result[2]["role"] == "user"


@pytest.mark.asyncio
async def test_summary_role_is_user_when_the_tail_starts_with_assistant() -> None:
    messages = _conversation(
        [_batch("c0", "small body")],
        tail_roles=["assistant", "user", "assistant", "user", "assistant", "user"],
    )
    mgr = ContextWindowManager(model="qwen3.7-max")
    mgr.budget_tokens = estimate_messages_tokens(messages) - 1

    result = await mgr.compact_if_needed(
        messages, summarizer=_recording_summarizer([])
    )

    assert result[1]["role"] == "user"
    assert result[2]["role"] == "assistant"
    assert [m["role"] for m in result].count("system") == 1


def test_summary_prefix_states_both_reference_only_meanings() -> None:
    assert "REFERENCE ONLY" in SUMMARY_PREFIX
    assert "latest user message" in SUMMARY_PREFIX
    assert "Topic overlap" in SUMMARY_PREFIX


# ---------------------------------------------------------------------------
# Knobs
# ---------------------------------------------------------------------------


def test_env_knobs_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXT_PRUNE_MIN_CHARS", "1234")
    monkeypatch.setenv("CONTEXT_COMPACTION_MIN_RECLAIM_TOKENS", "5678")
    mgr = ContextWindowManager()
    assert mgr.prune_min_chars == 1234
    assert mgr.min_reclaim_tokens == 5678


def test_malformed_env_knobs_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXT_PRUNE_MIN_CHARS", "not-a-number")
    monkeypatch.setenv("CONTEXT_COMPACTION_MIN_RECLAIM_TOKENS", "")
    mgr = ContextWindowManager()
    assert mgr.prune_min_chars == ContextWindowManager.PRUNE_MIN_CHARS
    assert mgr.min_reclaim_tokens == ContextWindowManager.MIN_RECLAIM_TOKENS
