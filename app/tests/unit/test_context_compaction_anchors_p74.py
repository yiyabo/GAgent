"""p74: deterministic compaction anchors carry produced paths and plan assumptions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from app.services.context.context_manager import (
    SUMMARY_PREFIX,
    ContextWindowManager,
    _refresh_summary_anchors,
)
from app.services.context.request_budget import (
    ANCHOR_HEADER,
    MAX_ANCHOR_PRODUCED_PATHS,
    anchor_text,
)
from app.services.deep_think.native_validation import compaction_anchors


def _agent(produced=None, profile=None):
    return SimpleNamespace(
        _produced_deliverable_paths=produced or [],
        request_profile=profile or {},
        _output_spec=None,
    )


def test_anchor_text_is_deterministic_and_sectioned() -> None:
    anchors = {
        "request": "plot it",
        "produced_paths": ["results/a.png", "results/b.csv", "results/a.png"],
        "assumptions": ["Use hg38", "  "],
    }
    text = anchor_text(anchors)
    assert text == anchor_text(dict(reversed(list(anchors.items()))))
    assert text.splitlines() == [
        ANCHOR_HEADER,
        "Produced deliverables (already written; reuse or edit, do not recreate):",
        "- results/a.png",
        "- results/b.csv",
        "Active plan assumptions (unconfirmed defaults; keep consistent):",
        "- Use hg38",
        '{"request": "plot it"}',
    ]
    assert anchor_text({}) == "" and anchor_text(None) == ""


def test_anchor_text_caps_produced_paths() -> None:
    text = anchor_text({"produced_paths": [f"p{i}" for i in range(40)]})
    assert text.count("\n- p") == MAX_ANCHOR_PRODUCED_PATHS


def test_compaction_anchors_merge_agent_and_continuation_paths() -> None:
    agent = _agent(
        produced=["results/a.png", "results/b.csv"],
        profile={"plan_assumptions": ["Use hg38", "Use hg38"]},
    )
    context = {"execution_continuation": {"produced_paths": ["results/b.csv", "old/c.txt"]}}
    anchors = compaction_anchors(agent, "q", context, [{"role": "user", "content": "q"}])
    assert anchors["produced_paths"] == ["results/a.png", "results/b.csv", "old/c.txt"]
    assert anchors["assumptions"] == ["Use hg38"]
    assert anchors["latest_user_turns"] == ["q"]
    assert "output_spec" not in anchors


def test_compaction_anchors_cap_and_plan_metadata_fallback() -> None:
    agent = _agent(produced=[f"p{i}" for i in range(30)])
    context = {"plan_metadata": {"assumptions": ["Default alpha 0.05"]}}
    anchors = compaction_anchors(agent, "q", context, [], output_spec=True)
    assert len(anchors["produced_paths"]) == MAX_ANCHOR_PRODUCED_PATHS
    assert anchors["assumptions"] == ["Default alpha 0.05"]
    assert "output_spec" in anchors


def test_compaction_anchors_omit_empty_sections() -> None:
    anchors = compaction_anchors(_agent(), "q", None, [])
    assert "produced_paths" not in anchors and "assumptions" not in anchors


def test_llm_summary_carries_anchor_block() -> None:
    mgr = ContextWindowManager(max_context_tokens=100000)
    msgs = [{"role": "system", "content": "sys"}]
    msgs += [{"role": "user", "content": f"msg {i} " + "detail " * 400} for i in range(mgr.KEEP_RECENT + 4)]
    anchors = {"request": "q", "produced_paths": ["results/fig.png"], "assumptions": ["hg38"]}

    async def summarize(_text):
        return "Summary."

    result = asyncio.run(
        mgr.compact_if_needed(msgs, summarizer=summarize, force=True, anchors=anchors)
    )
    summary = result[1]["content"]
    assert summary.startswith(SUMMARY_PREFIX)
    assert summary.endswith(anchor_text(anchors))
    assert "- results/fig.png" in summary and "- hg38" in summary


def test_reclaim_refreshes_stale_anchor_block_in_prior_summary() -> None:
    stale = f"{SUMMARY_PREFIX}\nolder facts\n\n" + anchor_text({"produced_paths": ["a.png"]})
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": stale},
        {"role": "assistant", "content": "ok"},
    ]
    fresh = anchor_text({"produced_paths": ["a.png", "b.png"]})
    out = _refresh_summary_anchors(messages, fresh)
    assert out is not messages and messages[1]["content"] == stale
    assert out[1]["content"] == f"{SUMMARY_PREFIX}\nolder facts\n\n" + fresh
    assert out[1]["role"] == "user" and out[2] is messages[2]
    # Already current or no anchors: the list is returned untouched.
    assert _refresh_summary_anchors(out, fresh) is out
    assert _refresh_summary_anchors(messages, "") is messages


def test_reclaim_commit_path_refreshes_anchors() -> None:
    mgr = ContextWindowManager(max_context_tokens=4000)
    mgr.min_reclaim_tokens = 0
    stale = f"{SUMMARY_PREFIX}\nfacts\n\n" + anchor_text({"produced_paths": ["a.png"]})
    big = "row," * 3000
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": stale},
    ]
    for i in range(3):
        messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": "read", "arguments": "{}"}}],
        })
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": big})
    messages += [{"role": "user", "content": f"u{i}"} for i in range(mgr.KEEP_RECENT)]
    anchors = {"produced_paths": ["a.png", "b.png"]}

    async def summarize(_text):
        raise AssertionError("reclaim alone should suffice")

    result = asyncio.run(
        mgr.compact_if_needed(messages, summarizer=summarize, force=False, anchors=anchors)
    )
    assert result is not messages
    assert result[1]["content"].endswith(anchor_text(anchors))
