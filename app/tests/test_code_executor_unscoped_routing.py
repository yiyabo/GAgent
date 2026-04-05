from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.routers import chat_routes
from app.routers.chat.agent import _build_deep_think_task_context
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


def test_resolve_code_executor_task_context_does_not_auto_redirect_session_composite() -> None:
    agent = _build_agent()

    node, error = agent._resolve_code_executor_task_context()

    assert node is None
    assert error == "target_task_not_atomic"


def test_prepare_code_executor_params_routes_unscoped_for_session_stale_composite(
    monkeypatch,
) -> None:
    agent = _build_agent()
    monkeypatch.setattr(chat_routes, "get_current_job", lambda: "job_test")

    action = LLMAction(
        kind="tool_operation",
        name="code_executor",
        parameters={
            "task": "Write a Python Fibonacci sequence program and run it",
            "allowed_tools": ["Write", "Bash", "Read"],
        },
        order=1,
    )

    prepared = asyncio.run(
        agent._prepare_code_executor_params(
            action=action,
            tool_name="code_executor",
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


def test_sync_task_status_skips_for_unscoped_code_executor() -> None:
    repo = _RepoTaskSyncStub()
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=repo)
    agent.extra_context = {"current_task_id": 1}
    agent._dirty = False

    agent._sync_task_status_after_tool_execution(
        tool_name="code_executor",
        success=True,
        summary="ok",
        message="ok",
        params={"require_task_context": False},
    )

    assert repo.updated == []
    assert repo.cascaded == []


def test_sync_task_status_runs_for_scoped_code_executor() -> None:
    repo = _RepoTaskSyncStub()
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=repo)
    agent.extra_context = {"current_task_id": 1}
    agent._dirty = False

    agent._sync_task_status_after_tool_execution(
        tool_name="code_executor",
        success=True,
        summary="ok",
        message="ok",
        params={"require_task_context": True},
    )

    assert len(repo.updated) == 1
    assert len(repo.cascaded) == 1


def test_sync_task_status_skips_for_read_only_file_operations() -> None:
    repo = _RepoTaskSyncStub()
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=repo)
    agent.extra_context = {"current_task_id": 1}
    agent._dirty = False

    agent._sync_task_status_after_tool_execution(
        tool_name="file_operations",
        success=False,
        summary="Directory not found",
        message="Directory not found",
        params={"operation": "list", "path": "/tmp/missing"},
    )

    assert repo.updated == []
    assert repo.cascaded == []


def test_sync_task_status_skips_for_document_reader() -> None:
    repo = _RepoTaskSyncStub()
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=repo)
    agent.extra_context = {"current_task_id": 1}
    agent._dirty = False

    agent._sync_task_status_after_tool_execution(
        tool_name="document_reader",
        success=False,
        summary="File not found",
        message="File not found",
        params={"file_path": "/tmp/missing.pdf"},
    )

    assert repo.updated == []
    assert repo.cascaded == []


def test_sync_task_status_skips_for_vision_reader() -> None:
    repo = _RepoTaskSyncStub()
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=repo)
    agent.extra_context = {"current_task_id": 1}
    agent._dirty = False

    agent._sync_task_status_after_tool_execution(
        tool_name="vision_reader",
        success=False,
        summary="OCR failed",
        message="OCR failed",
        params={"operation": "read_image", "file_path": "/tmp/missing.png"},
    )

    assert repo.updated == []
    assert repo.cascaded == []


def test_sync_task_status_skips_for_result_interpreter() -> None:
    repo = _RepoTaskSyncStub()
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=repo)
    agent.extra_context = {"current_task_id": 1}
    agent._dirty = False

    agent._sync_task_status_after_tool_execution(
        tool_name="result_interpreter",
        success=False,
        summary="No result files found",
        message="No result files found",
        params={"operation": "metadata", "file_path": "/tmp/missing.csv"},
    )

    assert repo.updated == []
    assert repo.cascaded == []


def test_sync_task_status_skips_for_unverified_terminal_session_write() -> None:
    repo = _RepoTaskSyncStub()
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=repo)
    agent.extra_context = {"current_task_id": 1}
    agent._dirty = False

    agent._sync_task_status_after_tool_execution(
        tool_name="terminal_session",
        success=True,
        summary="command dispatched",
        message="command dispatched",
        params={"operation": "write", "data": "cat plan.json\n"},
        result={
            "operation": "write",
            "status": "completed",
            "verification_state": "not_attempted",
        },
    )

    assert repo.updated == []
    assert repo.cascaded == []


def test_sync_task_status_runs_for_verified_terminal_session_write() -> None:
    repo = _RepoTaskSyncStub()
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=repo)
    agent.extra_context = {"current_task_id": 1}
    agent._dirty = False

    agent._sync_task_status_after_tool_execution(
        tool_name="terminal_session",
        success=True,
        summary="verified mutation",
        message="verified mutation",
        params={"operation": "write", "data": "touch done.txt\n"},
        result={
            "operation": "write",
            "status": "completed",
            "verification_state": "verified_success",
            "artifact_paths": ["/tmp/run/results/done.txt"],
        },
    )

    assert len(repo.updated) == 1
    assert len(repo.cascaded) == 1


def test_sync_task_status_preserves_run_directory_for_relative_checks(tmp_path) -> None:
    run_dir = tmp_path / "runtime" / "session_x" / "plan68_task63" / "run_abc123"
    figures_dir = run_dir / "figures_raw"
    figures_dir.mkdir(parents=True)
    for index in range(20):
        (figures_dir / f"figure_{index}.png").write_bytes(b"png")
    (figures_dir / "figure_inventory.log").write_text("ok\n", encoding="utf-8")

    promoted_result = (
        tmp_path
        / "runtime"
        / "session_x"
        / "results"
        / "plan68_task63"
        / "run_abc123"
        / "results"
        / "figure_collection_summary.json"
    )
    promoted_result.parent.mkdir(parents=True)
    promoted_result.write_text("{}", encoding="utf-8")

    task = PlanNode(
        id=63,
        plan_id=49,
        name="图表收集与初步审查",
        status="pending",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "glob_count_at_least", "path": "figures_raw/*", "count": 20},
                    {"type": "file_nonempty", "path": "figures_raw/figure_inventory.log"},
                ],
            }
        },
    )
    tree = PlanTree(
        id=49,
        title="Plan 49",
        nodes={63: task},
        adjacency={None: [63], 63: []},
    )
    repo = _RepoTaskSyncWithTreeStub(tree)
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=repo)
    agent.extra_context = {"current_task_id": 63}
    agent._dirty = False

    agent._sync_task_status_after_tool_execution(
        tool_name="code_executor",
        success=True,
        summary="ok",
        message="ok",
        params={"require_task_context": True},
        result={
            "execution_status": "completed",
            "run_directory": str(run_dir),
            "working_directory": str(run_dir),
            "task_directory_full": str(run_dir),
            "task_root_directory": str(run_dir.parent),
            "artifact_paths": [
                str(run_dir),
                str(promoted_result),
            ],
        },
    )

    assert len(repo.updated) == 1
    _, _, status, execution_result = repo.updated[0]
    payload = json.loads(execution_result)
    assert status == "completed"
    assert payload["metadata"]["run_directory"] == str(run_dir)
    assert payload["metadata"]["verification_status"] == "passed"


def test_build_deep_think_task_context_uses_bound_atomic_task() -> None:
    leaf = PlanNode(
        id=30,
        plan_id=49,
        name="subtask",
        instruction="Run the integration analysis for task 30.",
        parent_id=1,
        status="pending",
    )
    tree = PlanTree(
        id=49,
        title="Plan 49",
        nodes={
            1: PlanNode(id=1, plan_id=49, name="Root", status="pending"),
            30: leaf,
        },
        adjacency={None: [1], 1: [30], 30: []},
    )
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=_RepoStub(tree))
    agent.plan_tree = tree
    agent.extra_context = {"current_task_id": 30}

    task_context = _build_deep_think_task_context(
        agent,
        user_message="continue task 30",
    )

    assert task_context is not None
    assert task_context.task_id == 30
    assert task_context.task_instruction == "Run the integration analysis for task 30."
    assert task_context.plan_outline == "Plan 49"


def test_build_deep_think_task_context_includes_dependency_outputs() -> None:
    dependency = PlanNode(
        id=10,
        plan_id=49,
        name="QC filtering",
        instruction="Produce filtered h5ad outputs.",
        status="completed",
        execution_result=json.dumps(
            {
                "status": "completed",
                "content": "filtered_cancer1.h5ad generated",
            },
            ensure_ascii=False,
        ),
    )
    leaf = PlanNode(
        id=30,
        plan_id=49,
        name="subtask",
        instruction="Run the integration analysis for task 30.",
        parent_id=1,
        status="pending",
        dependencies=[10],
    )
    tree = PlanTree(
        id=49,
        title="Plan 49",
        nodes={
            1: PlanNode(id=1, plan_id=49, name="Root", status="pending"),
            10: dependency,
            30: leaf,
        },
        adjacency={None: [1], 1: [10, 30], 10: [], 30: []},
    )
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=49, repo=_RepoStub(tree))
    agent.plan_tree = tree
    agent.extra_context = {"current_task_id": 30}

    task_context = _build_deep_think_task_context(
        agent,
        user_message="continue task 30",
    )

    assert task_context is not None
    assert task_context.dependency_outputs
    assert task_context.dependency_outputs[0]["task_id"] == 10
    assert "filtered_cancer1.h5ad" in task_context.dependency_outputs[0]["execution_result"]
    assert "Completed task outputs:" in str(task_context.context_summary or "")


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
        tool_name="code_executor",
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
