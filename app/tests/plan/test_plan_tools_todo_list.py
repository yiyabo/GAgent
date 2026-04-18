"""Tests for the todo_list operation in plan_operation_handler."""
import asyncio
from unittest.mock import patch

import pytest

from app.services.plans.plan_models import PlanNode, PlanTree
from tool_box.tools_impl.plan_tools import plan_operation_handler, _get_todo_list


def _make_tree() -> PlanTree:
    """Build a small tree: ROOT(1) → A(2) → B(3) → C(4)."""
    tree = PlanTree(id=1, title="Test Plan", description="desc")
    tree.nodes[1] = PlanNode(
        id=1, plan_id=1, name="ROOT", status="pending",
        instruction="root", parent_id=None, dependencies=[],
    )
    tree.nodes[2] = PlanNode(
        id=2, plan_id=1, name="Data Prep", status="completed",
        instruction="prepare data", parent_id=1, dependencies=[],
    )
    tree.nodes[3] = PlanNode(
        id=3, plan_id=1, name="Analysis", status="pending",
        instruction="run analysis", parent_id=1, dependencies=[2],
    )
    tree.nodes[4] = PlanNode(
        id=4, plan_id=1, name="Report", status="pending",
        instruction="write report", parent_id=1, dependencies=[3],
    )
    tree.rebuild_adjacency()
    return tree


class _RepoStub:
    def __init__(self, tree: PlanTree):
        self.tree = tree

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        return self.tree


def test_get_todo_list_basic():
    """todo_list operation returns phased execution plan."""
    tree = _make_tree()
    stub = _RepoStub(tree)

    with patch(
        "app.repository.plan_repository.PlanRepository",
        return_value=stub,
    ):
        result = asyncio.run(
            _get_todo_list(plan_id=1, target_task_id=4)
        )

    assert result["success"] is True
    assert result["operation"] == "todo_list"
    assert result["plan_id"] == 1
    assert result["target_task_id"] == 4
    assert result["total_tasks"] >= 3
    assert "phases" in result
    assert len(result["phases"]) >= 1
    assert "summary" in result
    assert "execution_order" in result
    assert "pending_order" in result


def test_get_todo_list_missing_plan_id():
    """todo_list returns error when plan_id is None."""
    result = asyncio.run(
        _get_todo_list(plan_id=None, target_task_id=4)
    )
    assert result["success"] is False
    assert "plan_id" in result["error"]


def test_get_todo_list_missing_target_task_id():
    """todo_list returns error when target_task_id is None."""
    result = asyncio.run(
        _get_todo_list(plan_id=1, target_task_id=None)
    )
    assert result["success"] is False
    assert "target_task_id" in result["error"]


def test_get_todo_list_invalid_task():
    """todo_list returns error for non-existent task."""
    tree = _make_tree()
    stub = _RepoStub(tree)

    with patch(
        "app.repository.plan_repository.PlanRepository",
        return_value=stub,
    ):
        result = asyncio.run(
            _get_todo_list(plan_id=1, target_task_id=999)
        )
    assert result["success"] is False
    assert "999" in result["error"]


def test_handler_dispatches_todo_list():
    """plan_operation_handler routes 'todo_list' operation correctly."""
    tree = _make_tree()
    stub = _RepoStub(tree)

    with patch(
        "app.repository.plan_repository.PlanRepository",
        return_value=stub,
    ):
        result = asyncio.run(
            plan_operation_handler(
                operation="todo_list",
                plan_id=1,
                target_task_id=4,
            )
        )
    assert result["success"] is True
    assert result["operation"] == "todo_list"


def test_todo_list_phase_structure():
    """Each phase has expected fields."""
    tree = _make_tree()
    stub = _RepoStub(tree)

    with patch(
        "app.repository.plan_repository.PlanRepository",
        return_value=stub,
    ):
        result = asyncio.run(
            _get_todo_list(plan_id=1, target_task_id=4)
        )

    for phase in result["phases"]:
        assert "phase_id" in phase
        assert "label" in phase
        assert "status" in phase
        assert "total" in phase
        assert "completed" in phase
        assert "items" in phase
        for item in phase["items"]:
            assert "task_id" in item
            assert "name" in item
            assert "status" in item
