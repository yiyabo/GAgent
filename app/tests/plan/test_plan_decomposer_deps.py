"""Tests for PlanDecomposer._validate_dependencies — sibling-index
resolution, cycle detection, and hallucinated ID rejection."""
from __future__ import annotations

import pytest
from app.services.plans.plan_decomposer import PlanDecomposer
from app.services.plans.plan_models import PlanNode, PlanTree


def _minimal_tree(nodes: dict[int, int | None]) -> PlanTree:
    """Build a tiny PlanTree from {node_id: parent_id} mapping."""
    plan_nodes: dict[int, PlanNode] = {}
    adjacency: dict[int | None, list[int]] = {}
    for nid, pid in nodes.items():
        plan_nodes[nid] = PlanNode(
            id=nid,
            plan_id=99,
            name=f"Task {nid}",
            parent_id=pid,
        )
        adjacency.setdefault(pid, []).append(nid)

    tree = PlanTree(id=99, title="test", nodes=plan_nodes, adjacency=adjacency)
    return tree


@pytest.fixture()
def decomposer():
    return PlanDecomposer()


# ---- 0-based sibling index resolution ----


def test_sibling_index_resolves_correctly(decomposer: PlanDecomposer):
    """Index 0 should map to the first created sibling's real ID."""
    tree = _minimal_tree({1: None, 2: 1})
    # Siblings already created: IDs 10, 11, 12
    created = [10, 11, 12]
    result = decomposer._validate_dependencies(
        tree=tree, parent_id=2, raw_deps=[0, 1], created_sibling_ids=created,
    )
    assert result == [10, 11]


def test_sibling_index_out_of_range_falls_back(decomposer: PlanDecomposer):
    """Index beyond len(created_sibling_ids) falls through to literal ID check."""
    tree = _minimal_tree({1: None, 5: 1})
    created = [10, 11]
    # dep_val=5 is out of index range → literal ID; node 5 exists in tree
    result = decomposer._validate_dependencies(
        tree=tree, parent_id=1, raw_deps=[5], created_sibling_ids=created,
    )
    assert result == [5]


# ---- Ancestor cycle prevention ----


def test_ancestor_id_rejected(decomposer: PlanDecomposer):
    """LLM referencing parent/ancestor ID should be silently dropped."""
    tree = _minimal_tree({1: None, 2: 1, 3: 2})
    created = [10]
    # dep_val=2 is an ancestor of the node being created under parent_id=3
    result = decomposer._validate_dependencies(
        tree=tree, parent_id=3, raw_deps=[2], created_sibling_ids=created,
    )
    assert result == []


def test_root_ancestor_id_rejected(decomposer: PlanDecomposer):
    tree = _minimal_tree({1: None, 2: 1})
    created = [10]
    # dep_val=1 is the root ancestor
    result = decomposer._validate_dependencies(
        tree=tree, parent_id=2, raw_deps=[1], created_sibling_ids=created,
    )
    assert result == []


# ---- Hallucinated / non-existent ID ----


def test_nonexistent_id_rejected(decomposer: PlanDecomposer):
    """ID that doesn't exist in tree or siblings should be dropped."""
    tree = _minimal_tree({1: None, 2: 1})
    created = [10]
    result = decomposer._validate_dependencies(
        tree=tree, parent_id=2, raw_deps=[999], created_sibling_ids=created,
    )
    assert result == []


def test_ambiguous_singleton_existing_task_id_does_not_map_to_sibling_index(
    decomposer: PlanDecomposer,
):
    """Backward compatibility: a lone small existing task id must not remap to a sibling."""
    tree = _minimal_tree({1: None, 2: 1})
    created = [10, 11, 12]
    result = decomposer._validate_dependencies(
        tree=tree, parent_id=2, raw_deps=[1], created_sibling_ids=created,
    )
    assert result == []


def test_mixed_valid_and_invalid(decomposer: PlanDecomposer):
    """Mix of valid index, ancestor, and hallucinated IDs."""
    tree = _minimal_tree({1: None, 2: 1})
    created = [10, 11, 12]
    result = decomposer._validate_dependencies(
        tree=tree,
        parent_id=2,
        raw_deps=[0, 1, 35, 1],  # index 0→10, index 1→11 (valid), 35 (invalid), 1 (ancestor)
        created_sibling_ids=created,
    )
    # Should contain 10 and 11; 35 and ancestor 1 dropped
    assert result == [10, 11]


# ---- Empty deps ----


def test_empty_deps_returns_empty(decomposer: PlanDecomposer):
    tree = _minimal_tree({1: None})
    result = decomposer._validate_dependencies(
        tree=tree, parent_id=1, raw_deps=[], created_sibling_ids=[],
    )
    assert result == []


# ---- Dedup ----


def test_duplicate_index_deduped(decomposer: PlanDecomposer):
    """Same sibling index referenced twice should not produce duplicate."""
    tree = _minimal_tree({1: None, 2: 1})
    created = [10, 11]
    result = decomposer._validate_dependencies(
        tree=tree, parent_id=2, raw_deps=[0, 0], created_sibling_ids=created,
    )
    assert result == [10]
