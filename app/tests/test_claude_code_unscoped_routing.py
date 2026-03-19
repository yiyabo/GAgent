from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.routers import chat_routes
from app.routers.chat_routes import StructuredChatAgent
from app.services.llm.structured_response import LLMAction
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.task_verification import TaskVerificationService


class _RepoStub:
    def __init__(self, tree: PlanTree) -> None:
        self._tree = tree

    def get_plan_tree(self, _plan_id: int) -> PlanTree:
        return self._tree


class _RepoTaskSyncStub:
    def __init__(self) -> None:
        self.updated: list[tuple[int, int, str, str]] = []
        self.cascaded: list[tuple[int, int, str, str]] = []

    def update_task(
        self,
        plan_id: int,
        task_id: int,
        *,
        status: str,
        execution_result: str,
    ) -> None:
        self.updated.append((plan_id, task_id, status, execution_result))

    def cascade_update_descendants_status(
        self,
        plan_id: int,
        task_id: int,
        *,
        status: str,
        execution_result: str,
    ) -> int:
        self.cascaded.append((plan_id, task_id, status, execution_result))
        return 1


class _RepoTaskSyncWithTreeStub(_RepoTaskSyncStub):
    def __init__(self, tree: PlanTree) -> None:
        super().__init__()
        self._tree = tree

    def get_plan_tree(self, _plan_id: int) -> PlanTree:
        return self._tree


def _build_tree() -> PlanTree:
    root = PlanNode(
        id=1,
        plan_id=49,
        name="Root",
        status="pending",
    )
    leaf = PlanNode(
        id=30,
        plan_id=49,
        name="subtask: ",
        instruction="Agent. ",
        parent_id=1,
        status="pending",
    )
    tree = PlanTree(
        id=49,
        title="Plan 49",
        nodes={1: root, 30: leaf},
        adjacency={None: [1], 1: [30], 30: []},
    )
    return tree


def _build_agent() -> StructuredChatAgent:
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=_RepoStub(_build_tree()))
    agent.extra_context = {
        "current_task_id": 1,
        "_current_task_source": "session",
    }
    agent.session_id = "session_test"
    agent.mode = "assistant"
    agent._sync_job_id = None
    agent.conversation_id = "conv_test"
    return agent


def test_resolve_claude_code_task_context_does_not_auto_redirect_session_composite() -> None:
    agent = _build_agent()

    node, error = agent._resolve_claude_code_task_context()

    assert node is None
    assert error == "target_task_not_atomic"


def test_prepare_claude_code_params_routes_unscoped_for_session_stale_composite(
    monkeypatch,
) -> None:
    agent = _build_agent()
    monkeypatch.setattr(chat_routes, "get_current_job", lambda: "job_test")

    action = LLMAction(
        kind="tool_operation",
        name="claude_code",
        parameters={
            "task": "Write a Python Fibonacci sequence program and run it",
            "allowed_tools": ["Write", "Bash", "Read"],
        },
        order=1,
    )

    prepared = asyncio.run(
        agent._prepare_claude_code_params(
            action=action,
            tool_name="claude_code",
            params=dict(action.parameters),
        )
    )

    assert isinstance(prepared, tuple)
    prepared_params, original_task = prepared

    assert original_task == "Write a Python Fibonacci sequence program and run it"
    assert prepared_params.get("task") == original_task
    assert prepared_params.get("require_task_context") is False
    assert prepared_params.get("auth_mode") == "api_env"
    assert prepared_params.get("setting_sources") == "project"
    assert "plan_id" not in prepared_params
    assert "task_id" not in prepared_params
    assert prepared_params.get("allowed_tools") == "Write,Bash,Read"


def test_sync_task_status_skips_for_unscoped_claude_code() -> None:
    repo = _RepoTaskSyncStub()
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=repo)
    agent.extra_context = {"current_task_id": 1}
    agent._dirty = False

    agent._sync_task_status_after_tool_execution(
        tool_name="claude_code",
        success=True,
        summary="ok",
        message="ok",
        params={"require_task_context": False},
    )

    assert repo.updated == []
    assert repo.cascaded == []


def test_sync_task_status_runs_for_scoped_claude_code() -> None:
    repo = _RepoTaskSyncStub()
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=repo)
    agent.extra_context = {"current_task_id": 1}
    agent._dirty = False

    agent._sync_task_status_after_tool_execution(
        tool_name="claude_code",
        success=True,
        summary="ok",
        message="ok",
        params={"require_task_context": True},
    )

    assert len(repo.updated) == 1
    assert len(repo.cascaded) == 1


def test_sync_task_status_marks_failed_when_verification_fails(tmp_path) -> None:
    missing_path = tmp_path / "missing.txt"
    tree = PlanTree(
        id=49,
        title="Plan 49",
        nodes={
            1: PlanNode(
                id=1,
                plan_id=49,
                name="Collect output",
                status="pending",
                metadata={
                    "acceptance_criteria": {
                        "category": "file_data",
                        "blocking": True,
                        "checks": [{"type": "file_exists", "path": str(missing_path)}],
                    }
                },
            )
        },
        adjacency={None: [1], 1: []},
    )
    repo = _RepoTaskSyncWithTreeStub(tree)
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=repo)
    agent.extra_context = {"current_task_id": 1}
    agent._dirty = False
    agent._task_verifier = TaskVerificationService()

    agent._sync_task_status_after_tool_execution(
        tool_name="claude_code",
        success=True,
        summary="finished",
        message="finished",
        params={"require_task_context": True, "output_path": str(missing_path)},
        result={"output_path": str(missing_path)},
    )

    assert len(repo.updated) == 1
    assert repo.updated[0][2] == "failed"
    payload = json.loads(repo.updated[0][3])
    assert payload["metadata"]["verification"]["status"] == "failed"
    assert repo.cascaded == []
