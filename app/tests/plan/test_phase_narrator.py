"""Tests for the Layer3 phase narrator and its todo-list wiring.

Guards the safety invariant: narration only relabels phases by ``phase_id`` and
never reorders tasks, and any malformed, errored, or phase-id-mismatched LLM
output is discarded so callers keep neutral ``Phase N`` labels.
"""
from __future__ import annotations

from typing import List, Optional

import pytest

from app.services.plans import phase_narrator
from app.services.plans.phase_narrator import (
    extract_phase_labels,
    generate_phase_titles,
)
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.todo_list import build_full_plan_todo_list


def _node(
    task_id: int,
    name: str,
    *,
    deps: Optional[List[int]] = None,
) -> PlanNode:
    return PlanNode(
        id=task_id,
        plan_id=1,
        name=name,
        instruction=f"Do {name}",
        dependencies=deps or [],
    )


def _two_phase_tree() -> PlanTree:
    nodes = [_node(1, "Collect Data"), _node(2, "Analyze", deps=[1])]
    tree = PlanTree(id=1, title="Test Plan", nodes={n.id: n for n in nodes})
    tree.rebuild_adjacency()
    return tree


class _FakeClient:
    def __init__(self, *, response: Optional[str] = None, error: Optional[Exception] = None):
        self._response = response
        self._error = error

    async def chat_async(self, prompt: str, **kwargs) -> str:
        if self._error is not None:
            raise self._error
        return self._response or ""


def test_extract_phase_labels_parses_and_coerces() -> None:
    metadata = {"layer3_phase_titles": {"0": "Data Prep", "1": " Analysis ", "2": ""}}
    assert extract_phase_labels(metadata) == {0: "Data Prep", 1: "Analysis"}


def test_extract_phase_labels_handles_missing_or_malformed() -> None:
    assert extract_phase_labels(None) == {}
    assert extract_phase_labels({}) == {}
    assert extract_phase_labels({"layer3_phase_titles": "nope"}) == {}


async def test_generate_phase_titles_returns_validated_map(monkeypatch) -> None:
    monkeypatch.setattr(
        phase_narrator,
        "get_default_client",
        lambda: _FakeClient(response='{"0": "Data Collection", "1": "Analysis"}'),
    )
    titles = await generate_phase_titles(_two_phase_tree())
    assert titles == {0: "Data Collection", 1: "Analysis"}


async def test_generate_phase_titles_falls_back_on_llm_error(monkeypatch) -> None:
    monkeypatch.setattr(
        phase_narrator,
        "get_default_client",
        lambda: _FakeClient(error=RuntimeError("boom")),
    )
    assert await generate_phase_titles(_two_phase_tree()) == {}


async def test_generate_phase_titles_discards_phase_id_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        phase_narrator,
        "get_default_client",
        lambda: _FakeClient(response='{"0": "Only One Phase"}'),
    )
    assert await generate_phase_titles(_two_phase_tree()) == {}


async def test_generate_phase_titles_skips_single_phase(monkeypatch) -> None:
    called = {"hit": False}

    def _fail():
        called["hit"] = True
        raise AssertionError("LLM must not be called for single-phase plans")

    monkeypatch.setattr(phase_narrator, "get_default_client", _fail)
    single = PlanTree(id=1, title="Solo", nodes={1: _node(1, "Only Task")})
    single.rebuild_adjacency()
    assert await generate_phase_titles(single) == {}
    assert called["hit"] is False


def test_build_full_plan_todo_list_applies_cached_titles() -> None:
    tree = _two_phase_tree()
    labeled = build_full_plan_todo_list(tree, phase_labels={0: "Data Collection"})
    assert labeled.phases[0].label == "Data Collection"
    assert labeled.phases[1].label == "Phase 2"


def test_build_full_plan_todo_list_keeps_neutral_without_labels() -> None:
    tree = _two_phase_tree()
    neutral = build_full_plan_todo_list(tree)
    assert [p.label for p in neutral.phases] == ["Phase 1", "Phase 2"]
