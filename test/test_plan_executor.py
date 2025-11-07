from __future__ import annotations

import json
import re

import pytest

from app.services.plans.plan_executor import (
    ExecutionConfig,
    ExecutionResponse,
    PlanExecutor,
)


def _parse_task_id(prompt: str) -> int:
    match = re.search(r"Task ID:\s*(\d+)", prompt)
    if not match:  # pragma: no cover - defensive
        raise AssertionError(f"prompt missing task id: {prompt!r}")
    return int(match.group(1))


class RecordingExecutorStub:
    """Stub PlanExecutorLLMService that records task execution order."""

    def __init__(self, *, failed_ids: set[int] | None = None) -> None:
        self.failed_ids = failed_ids or set()
        self.calls: list[int] = []

    def generate(self, prompt: str, config: ExecutionConfig) -> ExecutionResponse:
        task_id = _parse_task_id(prompt)
        self.calls.append(task_id)
        if task_id in self.failed_ids:
            return ExecutionResponse(
                status="failed",
                content=f"task {task_id} failed",
                notes=["stub failure"],
                metadata={"stub": True},
            )
        return ExecutionResponse(
            status="success",
            content=f"completed {task_id}",
            notes=[],
            metadata={"stub": True},
        )


class FlakyExecutorStub(RecordingExecutorStub):
    """Stub that raises once per task before succeeding to test retry logic."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts: dict[int, int] = {}

    def generate(self, prompt: str, config: ExecutionConfig) -> ExecutionResponse:
        task_id = _parse_task_id(prompt)
        self.calls.append(task_id)
        count = self.attempts.get(task_id, 0) + 1
        self.attempts[task_id] = count
        if count == 1:
            raise RuntimeError(f"transient failure for task {task_id}")
        return ExecutionResponse(
            status="success",
            content=f"recovered {task_id}",
            notes=["retry succeeded"],
            metadata={"attempts": count},
        )


def test_execute_plan_runs_leaf_first(plan_repo):
    plan = plan_repo.create_plan("Leaf First")
    root = plan_repo.create_task(plan.id, name="Root")
    child_a = plan_repo.create_task(plan.id, name="Child A", parent_id=root.id)
    child_b = plan_repo.create_task(plan.id, name="Child B", parent_id=root.id)

    stub = RecordingExecutorStub()
    executor = PlanExecutor(repo=plan_repo, llm_service=stub)

    summary = executor.execute_plan(plan.id)

    assert stub.calls == [child_a.id, child_b.id, root.id]
    assert summary.executed_task_ids == stub.calls
    tree = plan_repo.get_plan_tree(plan.id)
    for node_id in stub.calls:
        node = tree.get_node(node_id)
        payload = json.loads(node.execution_result)
        assert payload["status"] == "success"
        assert payload["content"] == f"completed {node_id}"
        assert node.status == "completed"


def test_execute_plan_stops_on_failure(plan_repo):
    plan = plan_repo.create_plan("Failure Case")
    root = plan_repo.create_task(plan.id, name="Root")
    child_a = plan_repo.create_task(plan.id, name="Child A", parent_id=root.id)
    child_b = plan_repo.create_task(plan.id, name="Child B", parent_id=root.id)

    stub = RecordingExecutorStub(failed_ids={child_b.id})
    executor = PlanExecutor(repo=plan_repo, llm_service=stub)

    summary = executor.execute_plan(plan.id)

    assert summary.executed_task_ids == [child_a.id]
    assert summary.failed_task_ids == [child_b.id]
    assert root.id not in summary.executed_task_ids
    assert stub.calls == [child_a.id, child_b.id]

    tree = plan_repo.get_plan_tree(plan.id)
    failed_node = tree.get_node(child_b.id)
    payload = json.loads(failed_node.execution_result)
    assert payload["status"] == "failed"
    assert "task" in payload["content"]
    assert failed_node.status == "failed"
    root_node = tree.get_node(root.id)
    assert root_node.status == "pending"


def test_execute_task_retries_on_failure(plan_repo):
    plan = plan_repo.create_plan("Retry Case")
    node = plan_repo.create_task(plan.id, name="Solo Task")

    stub = FlakyExecutorStub()
    executor = PlanExecutor(repo=plan_repo, llm_service=stub)

    result = executor.execute_task(plan.id, node.id)

    assert result.status == "completed"
    assert result.attempts == 2
    assert stub.calls == [node.id, node.id]
    tree = plan_repo.get_plan_tree(plan.id)
    stored = tree.get_node(node.id)
    payload = json.loads(stored.execution_result)
    assert payload["status"] == "success"
    assert payload["notes"] == ["retry succeeded"]
    assert stored.status == "completed"
