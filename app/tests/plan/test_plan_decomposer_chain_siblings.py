"""Tests for _chain_sequential_siblings in plan decomposer."""
from __future__ import annotations

from typing import List, Optional
from unittest.mock import MagicMock

import pytest

from app.services.plans.plan_decomposer import PlanDecomposer
from app.services.plans.plan_models import PlanNode, PlanTree


def _node(
    node_id: int,
    name: str,
    *,
    parent_id: Optional[int] = None,
    position: int = 0,
    deps: Optional[List[int]] = None,
) -> PlanNode:
    return PlanNode(
        id=node_id,
        plan_id=1,
        name=name,
        parent_id=parent_id,
        position=position,
        dependencies=deps or [],
    )


def _make_tree(nodes: List[PlanNode]) -> PlanTree:
    tree = PlanTree(id=1, title="Test", nodes={n.id: n for n in nodes})
    tree.rebuild_adjacency()
    return tree


def test_chain_sequential_siblings_adds_position_order_deps():
    repo = MagicMock()
    repo.update_task.side_effect = lambda plan_id, task_id, **kwargs: _node(
        task_id,
        "updated",
        parent_id=1,
        position=kwargs.get("position", 0),
        deps=kwargs.get("dependencies", []),
    )
    decomposer = PlanDecomposer(repo=repo)

    nodes = [
        _node(6, "Read data", parent_id=2, position=0),
        _node(7, "Clean data", parent_id=2, position=1),
        _node(8, "Export dictionary", parent_id=2, position=2),
    ]
    tree = _make_tree([_node(2, "Parent")] + nodes)

    decomposer._chain_sequential_siblings(plan_id=1, siblings=nodes, tree=tree)

    assert repo.update_task.call_count == 2
    calls = repo.update_task.call_args_list
    assert calls[0].kwargs["dependencies"] == [6]
    assert calls[1].kwargs["dependencies"] == [7]


def test_chain_sequential_siblings_skips_when_sibling_dep_exists():
    repo = MagicMock()
    decomposer = PlanDecomposer(repo=repo)

    nodes = [
        _node(6, "Read data", parent_id=2, position=0),
        _node(7, "Clean data", parent_id=2, position=1, deps=[6]),
        _node(8, "Export dictionary", parent_id=2, position=2),
    ]
    tree = _make_tree([_node(2, "Parent")] + nodes)

    decomposer._chain_sequential_siblings(plan_id=1, siblings=nodes, tree=tree)

    assert repo.update_task.call_count == 1
    call = repo.update_task.call_args
    assert call.args[1] == 8
    assert call.kwargs["dependencies"] == [7]


def test_chain_sequential_siblings_noop_for_single_sibling():
    repo = MagicMock()
    decomposer = PlanDecomposer(repo=repo)

    nodes = [_node(6, "Only task", parent_id=2, position=0)]
    tree = _make_tree([_node(2, "Parent")] + nodes)

    decomposer._chain_sequential_siblings(plan_id=1, siblings=nodes, tree=tree)

    repo.update_task.assert_not_called()


def test_chain_sequential_siblings_skips_non_leaf_nodes():
    repo = MagicMock()
    decomposer = PlanDecomposer(repo=repo)

    parent = _node(2, "Parent")
    leaf1 = _node(6, "Leaf 1", parent_id=2, position=0)
    leaf2 = _node(7, "Leaf 2", parent_id=2, position=1)
    branch = _node(8, "Branch", parent_id=2, position=2)
    child = _node(9, "Child of branch", parent_id=8, position=0)

    tree = _make_tree([parent, leaf1, leaf2, branch, child])

    decomposer._chain_sequential_siblings(
        plan_id=1, siblings=[leaf1, leaf2, branch], tree=tree
    )

    assert repo.update_task.call_count == 1
    call = repo.update_task.call_args
    assert call.args[1] == 7
    assert call.kwargs["dependencies"] == [6]
