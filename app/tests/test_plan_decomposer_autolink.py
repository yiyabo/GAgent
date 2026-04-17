"""Tests for the evidence→consumer auto-linking and parent-dep inheritance
behaviours added to PlanDecomposer.

Covers:
- `_classify_sibling` heuristics (paper_role and keyword-based)
- `_inherit_parent_dependencies` propagates parent deps to new children
  and skips ancestor ids to avoid cycles
- `_auto_link_evidence_to_writers` augments consumer deps with evidence
  siblings when the LLM omitted them
"""
from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

import pytest

from app.services.plans.plan_decomposer import PlanDecomposer
from app.services.plans.plan_models import PlanNode, PlanTree


def _mk_node(
    node_id: int,
    *,
    parent_id: int | None = None,
    name: str = "",
    instruction: str = "",
    dependencies: List[int] | None = None,
    metadata: dict | None = None,
) -> PlanNode:
    return PlanNode(
        id=node_id,
        plan_id=99,
        name=name or f"Task {node_id}",
        instruction=instruction,
        parent_id=parent_id,
        dependencies=list(dependencies or []),
        metadata=metadata or {},
    )


def _mk_tree(nodes: List[PlanNode]) -> PlanTree:
    plan_nodes = {n.id: n for n in nodes}
    adjacency: dict[int | None, list[int]] = {}
    for n in nodes:
        adjacency.setdefault(n.parent_id, []).append(n.id)
    return PlanTree(id=99, title="t", nodes=plan_nodes, adjacency=adjacency)


@pytest.fixture()
def decomposer() -> PlanDecomposer:
    d = PlanDecomposer()
    # The repo is only needed by _auto_link_evidence_to_writers; stub it.
    d._repo = MagicMock()  # type: ignore[assignment]
    return d


# ---------- _classify_sibling --------------------------------------------------


def test_classify_sibling_by_paper_role(decomposer: PlanDecomposer):
    ev = _mk_node(1, metadata={"paper_role": "evidence_collector"})
    wr = _mk_node(2, metadata={"paper_role": "section_writer"})
    assert decomposer._classify_sibling(ev) == "evidence"
    assert decomposer._classify_sibling(wr) == "consumer"


def test_classify_sibling_by_chinese_keywords(decomposer: PlanDecomposer):
    ev = _mk_node(1, name="整理NMR核心概念与文献证据")
    wr = _mk_node(2, name="撰写NMR章节初稿")
    assert decomposer._classify_sibling(ev) == "evidence"
    assert decomposer._classify_sibling(wr) == "consumer"


def test_classify_sibling_prefers_consumer_on_mixed_keywords(
    decomposer: PlanDecomposer,
):
    """A task that both integrates evidence AND writes should be treated as
    consumer so that auto-link still attaches evidence siblings to it."""
    node = _mk_node(1, name="整合证据并撰写章节初稿")
    assert decomposer._classify_sibling(node) == "consumer"


def test_classify_sibling_returns_other_when_unrelated(
    decomposer: PlanDecomposer,
):
    node = _mk_node(1, name="构建关键词集合并执行 PubMed 检索")
    assert decomposer._classify_sibling(node) == "other"


# ---------- _inherit_parent_dependencies -------------------------------------


def test_inherit_parent_deps_appends_new_ones(decomposer: PlanDecomposer):
    root = _mk_node(1, parent_id=None)
    dep_a = _mk_node(10, parent_id=None)
    parent = _mk_node(20, parent_id=1, dependencies=[10])
    tree = _mk_tree([root, dep_a, parent])
    merged = decomposer._inherit_parent_dependencies(
        tree=tree, parent_id=20, current_deps=[],
    )
    assert merged == [10]


def test_inherit_parent_deps_dedupes(decomposer: PlanDecomposer):
    root = _mk_node(1)
    dep_a = _mk_node(10)
    parent = _mk_node(20, parent_id=1, dependencies=[10])
    tree = _mk_tree([root, dep_a, parent])
    # Child already lists the parent dep; inheritance must not duplicate it.
    merged = decomposer._inherit_parent_dependencies(
        tree=tree, parent_id=20, current_deps=[10],
    )
    assert merged == [10]


def test_inherit_parent_deps_skips_ancestor_ids(decomposer: PlanDecomposer):
    """If the parent (somehow) had an ancestor listed as a dep, skip it to
    avoid introducing a cycle on the new child."""
    root = _mk_node(1)
    mid = _mk_node(5, parent_id=1)
    # Pathological: mid.dependencies contains root (its own ancestor)
    mid.dependencies = [1]
    tree = _mk_tree([root, mid])
    merged = decomposer._inherit_parent_dependencies(
        tree=tree, parent_id=5, current_deps=[],
    )
    # Root (1) is an ancestor of the new child → must NOT be inherited.
    assert 1 not in merged


def test_inherit_parent_deps_handles_missing_parent(decomposer: PlanDecomposer):
    tree = _mk_tree([_mk_node(1)])
    assert decomposer._inherit_parent_dependencies(
        tree=tree, parent_id=None, current_deps=[7],
    ) == [7]


# ---------- _auto_link_evidence_to_writers -----------------------------------


def test_autolink_adds_missing_evidence_edges(decomposer: PlanDecomposer):
    evidence = _mk_node(100, parent_id=9, name="整理MSM核心概念与证据")
    writer = _mk_node(101, parent_id=9, name="撰写MSM章节初稿", dependencies=[])
    tree = _mk_tree([_mk_node(9), evidence, writer])

    updated_writer = _mk_node(
        101, parent_id=9, name=writer.name, dependencies=[100],
    )
    decomposer._repo.update_task = MagicMock(return_value=updated_writer)

    decomposer._auto_link_evidence_to_writers(
        plan_id=99, siblings=[evidence, writer], tree=tree,
    )

    decomposer._repo.update_task.assert_called_once_with(
        99, 101, dependencies=[100],
    )
    assert tree.nodes[101].dependencies == [100]


def test_autolink_preserves_existing_deps(decomposer: PlanDecomposer):
    evidence_a = _mk_node(100, parent_id=9, name="整理证据A")
    evidence_b = _mk_node(101, parent_id=9, name="整理证据B")
    writer = _mk_node(
        102, parent_id=9, name="撰写章节初稿", dependencies=[100],
    )
    tree = _mk_tree([_mk_node(9), evidence_a, evidence_b, writer])

    def _fake_update(plan_id, task_id, *, dependencies):
        node = _mk_node(task_id, parent_id=9, name=writer.name,
                        dependencies=dependencies)
        return node

    decomposer._repo.update_task = MagicMock(side_effect=_fake_update)

    decomposer._auto_link_evidence_to_writers(
        plan_id=99, siblings=[evidence_a, evidence_b, writer], tree=tree,
    )

    # LLM-provided edge (100) must stay; missing edge (101) must be appended.
    call_args = decomposer._repo.update_task.call_args
    assert call_args.kwargs["dependencies"] == [100, 101]


def test_autolink_noop_when_no_consumer(decomposer: PlanDecomposer):
    ev_a = _mk_node(100, parent_id=9, name="整理A证据")
    ev_b = _mk_node(101, parent_id=9, name="整理B证据")
    tree = _mk_tree([_mk_node(9), ev_a, ev_b])
    decomposer._repo.update_task = MagicMock()
    decomposer._auto_link_evidence_to_writers(
        plan_id=99, siblings=[ev_a, ev_b], tree=tree,
    )
    decomposer._repo.update_task.assert_not_called()


def test_autolink_noop_when_no_evidence(decomposer: PlanDecomposer):
    wr_a = _mk_node(100, parent_id=9, name="撰写章节A初稿")
    wr_b = _mk_node(101, parent_id=9, name="撰写章节B初稿")
    tree = _mk_tree([_mk_node(9), wr_a, wr_b])
    decomposer._repo.update_task = MagicMock()
    decomposer._auto_link_evidence_to_writers(
        plan_id=99, siblings=[wr_a, wr_b], tree=tree,
    )
    decomposer._repo.update_task.assert_not_called()


def test_autolink_ignores_single_sibling(decomposer: PlanDecomposer):
    only = _mk_node(100, parent_id=9, name="撰写章节初稿")
    tree = _mk_tree([_mk_node(9), only])
    decomposer._repo.update_task = MagicMock()
    decomposer._auto_link_evidence_to_writers(
        plan_id=99, siblings=[only], tree=tree,
    )
    decomposer._repo.update_task.assert_not_called()
