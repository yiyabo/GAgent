"""Tests for the todo_list operation in plan_operation_handler."""
import asyncio
from unittest.mock import patch

import pytest

from app.routers import plan_routes
from app.services.plans.artifact_preflight import ArtifactPreflightIssue, ArtifactPreflightResult
from app.services.plans.plan_models import PlanNode, PlanTree
from tool_box.tools_impl.plan_tools import plan_operation_handler, _get_todo_list, _update_task


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
        self.update_calls: list[tuple[int, int, dict]] = []

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        return self.tree

    def update_task(self, plan_id: int, task_id: int, **kwargs):
        self.update_calls.append((plan_id, task_id, dict(kwargs)))
        node = self.tree.nodes[task_id]
        if kwargs.get("status") is not None:
            node.status = kwargs["status"]
        if kwargs.get("execution_result") is not None:
            node.execution_result = kwargs["execution_result"]
        if kwargs.get("name") is not None:
            node.name = kwargs["name"]
        if kwargs.get("instruction") is not None:
            node.instruction = kwargs["instruction"]
        if kwargs.get("metadata") is not None:
            node.metadata = kwargs["metadata"]
        return node


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


def test_execute_all_blocks_on_artifact_preflight_failure():
    tree = _make_tree()
    stub = _RepoStub(tree)

    with patch(
        "app.repository.plan_repository.PlanRepository",
        return_value=stub,
    ), patch.object(
        plan_routes,
        "_resolve_effective_task_states",
        lambda _plan_id, _tree, **_kwargs: {node_id: {"effective_status": "pending"} for node_id in tree.nodes},
    ), patch.object(
        plan_routes,
        "_todo_list_to_dict",
        lambda _todo, _plan_id, **_kwargs: {
            "pending_order": [3, 4],
            "completed_tasks": 1,
            "total_tasks": 3,
        },
    ), patch(
        "tool_box.tools_impl.plan_tools._artifact_preflight_service.validate_plan",
        return_value=ArtifactPreflightResult(
            plan_id=1,
            ok=False,
            errors=[
                ArtifactPreflightIssue(
                    code="missing_producer",
                    severity="error",
                    task_id=3,
                    message="Task #3 requires missing artifact alias 'ai_dl.references_bib'.",
                )
            ],
        ),
    ):
        result = asyncio.run(
            plan_operation_handler(
                operation="execute_all",
                plan_id=1,
            )
        )

    assert result["success"] is False
    assert result["status"] == "artifact_preflight_failed"
    assert result["operation"] == "execute_all"
    assert result["preflight"]["errors"][0]["code"] == "missing_producer"


def test_update_task_rejects_completed_without_execution_result() -> None:
    tree = _make_tree()
    tree.nodes[3].status = "pending"
    tree.nodes[3].execution_result = None
    stub = _RepoStub(tree)

    with patch(
        "app.repository.plan_repository.PlanRepository",
        return_value=stub,
    ):
        result = asyncio.run(
            _update_task(
                plan_id=1,
                task_id=3,
                new_status="completed",
                note="manual review",
            )
        )

    assert result["success"] is False
    assert "execution result" in result["error"].lower()
    assert stub.update_calls == []
    assert tree.nodes[3].status == "pending"
