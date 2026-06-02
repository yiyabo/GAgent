from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

from app.config.executor_config import get_executor_settings
from app.repository.plan_repository import PlanRepository
from app.services.plans.plan_executor import ExecutionConfig, PlanExecutor, PlanExecutorLLMService
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.task_delegate_executor import CodeAgentTaskDelegateExecutor
from app.services.plans.task_delegate_executor import TaskDelegationResult
from app.services.plans.task_delegate_executor import TaskDelegationSpec
from app.services.plans.status_resolver import PlanStatusResolver


class _RepoStub:
    def __init__(self, tree: PlanTree) -> None:
        self.tree: PlanTree = tree
        self.update_calls: list[tuple[int, int, dict[str, object]]] = []

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        assert plan_id == self.tree.id
        return self.tree

    def update_task(self, plan_id: int, task_id: int, **kwargs: object) -> PlanNode:
        self.update_calls.append((plan_id, task_id, dict(kwargs)))
        node = self.tree.nodes[task_id]
        for key, value in kwargs.items():
            if value is not None:
                setattr(node, key, value)
        return node


class _NoLlm:
    def generate(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("internal LLM should not run when task delegation is enabled")


class _DelegateStub:
    def __init__(self, result: TaskDelegationResult) -> None:
        self.result: TaskDelegationResult = result
        self.calls: list[TaskDelegationSpec] = []

    def execute(self, spec: TaskDelegationSpec) -> TaskDelegationResult:
        self.calls.append(spec)
        return self.result


def _make_tree(node: PlanNode, *extra_nodes: PlanNode) -> PlanTree:
    tree = PlanTree(id=node.plan_id, title="Delegation Plan")
    for item in (node, *extra_nodes):
        tree.nodes[item.id] = item
    tree.rebuild_adjacency()
    return tree


def _make_executor(repo: _RepoStub, delegate: _DelegateStub) -> PlanExecutor:
    settings = replace(
        get_executor_settings(),
        plan_task_execution_backend="external_agent",
        plan_task_agent_backend="local",
    )
    return PlanExecutor(
        repo=cast(PlanRepository, cast(object, repo)),
        llm_service=cast(PlanExecutorLLMService, cast(object, _NoLlm())),
        settings=settings,
        task_delegate_executor=cast(CodeAgentTaskDelegateExecutor, cast(object, delegate)),
    )


def test_plan_executor_delegates_task_and_verifies_outputs(tmp_path: Path) -> None:
    output_path = tmp_path / "answer.txt"
    _ = output_path.write_text("delegated result\n", encoding="utf-8")
    node = PlanNode(
        id=1,
        plan_id=101,
        name="Write delegated output",
        instruction="Create answer.txt",
        metadata={
            "acceptance_criteria": {
                "blocking": True,
                "checks": [{"type": "file_nonempty", "path": str(output_path)}],
            }
        },
    )
    repo = _RepoStub(_make_tree(node))
    delegate = _DelegateStub(
        TaskDelegationResult(
            status="completed",
            summary="created output",
            artifact_paths=[str(output_path)],
            executor="local",
            raw_result={"run_id": "attempt-1", "artifact_paths": [str(output_path)]},
        )
    )
    executor = _make_executor(repo, delegate)

    result = executor.execute_task(101, 1, config=ExecutionConfig(session_context={"session_id": "s1"}))

    assert result.status == "completed"
    assert delegate.calls
    assert delegate.calls[0].executor_backend == "local"
    assert result.metadata["delegated_task_execution"] is True
    assert result.metadata["delegation_status"] == "completed"
    assert str(output_path) in result.metadata["artifact_paths"]


def test_plan_executor_delegation_prompt_omits_internal_tool_hints() -> None:
    node = PlanNode(
        id=1,
        plan_id=105,
        name="Delegated prompt",
        instruction="Create answer.txt",
        metadata={
            "acceptance_criteria": {
                "blocking": True,
                "checks": [{"type": "file_nonempty", "path": "answer.txt"}],
            },
            "artifact_contract": {"publishes": ["answer"]},
        },
    )
    repo = _RepoStub(_make_tree(node))
    delegate = _DelegateStub(TaskDelegationResult(status="completed", summary="ok"))
    executor = _make_executor(repo, delegate)

    _ = executor.execute_task(105, 1, config=ExecutionConfig(session_context={"session_id": "s1"}))

    assert delegate.calls
    prompt = delegate.calls[0].task_prompt
    assert "=== TOOL SELECTION HINTS ===" not in prompt
    assert "code_executor" not in prompt


def test_plan_executor_delegated_missing_output_fails_verification(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.txt"
    node = PlanNode(
        id=1,
        plan_id=102,
        name="Miss output",
        instruction="Create missing.txt",
        metadata={
            "acceptance_criteria": {
                "blocking": True,
                "checks": [{"type": "file_nonempty", "path": str(missing_path)}],
            }
        },
    )
    repo = _RepoStub(_make_tree(node))
    delegate = _DelegateStub(
        TaskDelegationResult(
            status="completed",
            summary="claimed completion",
            artifact_paths=[],
            executor="local",
            raw_result={"run_id": "attempt-2"},
        )
    )
    executor = _make_executor(repo, delegate)

    result = executor.execute_task(102, 1, config=ExecutionConfig(session_context={"session_id": "s1"}))

    assert result.status == "failed"
    assert delegate.calls
    payload = cast(dict[str, object], json.loads(result.raw_response or "{}"))
    metadata = cast(dict[str, str | bool], payload["metadata"])
    assert metadata["verification_status"] == "failed"
    assert metadata["delegated_task_execution"] is True


def test_plan_executor_delegated_completed_task_ignores_prompt_block_marker(tmp_path: Path) -> None:
    output_path = tmp_path / "answer.txt"
    _ = output_path.write_text("delegated result\n", encoding="utf-8")
    node = PlanNode(
        id=1,
        plan_id=107,
        name="Write delegated output",
        instruction="Create answer.txt",
        metadata={
            "acceptance_criteria": {
                "blocking": True,
                "checks": [{"type": "file_nonempty", "path": str(output_path)}],
            }
        },
    )
    repo = _RepoStub(_make_tree(node))
    delegate = _DelegateStub(
        TaskDelegationResult(
            status="completed",
            summary=(
                "Prompt instructions said: If inputs are missing, report "
                "BLOCKED_DEPENDENCY with a concise DETAIL. Actual output succeeded."
            ),
            artifact_paths=[str(output_path)],
            executor="qwen_code",
            raw_result={
                "success": True,
                "run_id": "attempt-3",
                "artifact_paths": [str(output_path)],
                "contract_artifacts": [
                    {"path": str(output_path), "exists": True, "expected": "answer.txt"}
                ],
            },
        )
    )
    executor = _make_executor(repo, delegate)

    result = executor.execute_task(107, 1, config=ExecutionConfig(session_context={"session_id": "s1"}))

    assert result.status == "completed"
    payload = cast(dict[str, object], json.loads(result.raw_response or "{}"))
    metadata = cast(dict[str, object], payload["metadata"])
    assert metadata["delegated_task_execution"] is True
    assert metadata.get("blocked_by_dependencies") is not True


def test_plan_executor_delegated_blocked_task_stays_skipped() -> None:
    node = PlanNode(
        id=1,
        plan_id=108,
        name="Blocked delegated task",
        instruction="Use missing upstream data",
    )
    repo = _RepoStub(_make_tree(node))
    delegate = _DelegateStub(
        TaskDelegationResult(
            status="blocked",
            summary="STATUS: BLOCKED_DEPENDENCY\nDETAIL: missing upstream file",
            executor="qwen_code",
            raw_result={"success": False, "error_category": "blocked_dependency"},
        )
    )
    executor = _make_executor(repo, delegate)

    result = executor.execute_task(108, 1, config=ExecutionConfig(session_context={"session_id": "s1"}))

    assert result.status == "skipped"
    payload = cast(dict[str, object], json.loads(result.raw_response or "{}"))
    metadata = cast(dict[str, object], payload["metadata"])
    assert metadata["blocked_by_dependencies"] is True
    assert metadata["blocked_dependency_reported_by_task"] is True


def test_plan_executor_dependency_block_prevents_delegation() -> None:
    dependency = PlanNode(id=1, plan_id=103, name="Upstream", status="pending")
    node = PlanNode(
        id=2,
        plan_id=103,
        name="Downstream",
        instruction="Use upstream output",
        dependencies=[1],
    )
    repo = _RepoStub(_make_tree(dependency, node))
    delegate = _DelegateStub(TaskDelegationResult(status="completed", summary="should not run"))
    executor = _make_executor(repo, delegate)

    result = executor.execute_task(103, 2, config=ExecutionConfig(session_context={"session_id": "s1"}))

    assert result.status == "skipped"
    assert not delegate.calls
    assert result.metadata["blocked_by_dependencies"] is True


def test_delegating_raw_status_resolves_as_running() -> None:
    tree = _make_tree(PlanNode(id=1, plan_id=104, name="Delegated", status="delegating"))

    states = PlanStatusResolver().resolve_plan_states(104, tree)

    assert states[1]["effective_status"] == "running"
    assert states[1]["reason_code"] == "delegating"


def test_delegate_prompt_serializes_contracts_as_json() -> None:
    spec = TaskDelegationSpec(
        plan_id=106,
        task_id=1,
        task_name="JSON contract",
        task_instruction="Create answer.txt",
        task_prompt="Task prompt body",
        executor_backend="local",
        artifact_contract={"publishes": ["answer"]},
        acceptance_criteria={
            "blocking": True,
            "checks": [{"type": "file_nonempty", "path": "answer.txt"}],
        },
        resolved_input_artifacts={"input": "/tmp/input.txt"},
    )

    prompt = CodeAgentTaskDelegateExecutor()._build_delegate_prompt(spec)

    assert "Return the strict final response schema" in prompt
    assert "Do not claim that internal Phage-Agent tools were called" in prompt
    assert '"publishes": [' in prompt
    assert '"checks": [' in prompt
    assert "{'publishes':" not in prompt
