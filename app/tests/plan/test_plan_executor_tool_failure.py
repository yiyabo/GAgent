from types import SimpleNamespace

import app.services.path_router as path_router_module
from app.services.path_router import PathRouter, PathRouterConfig
from app.services.plans.plan_executor import ExecutionConfig, PlanExecutor, ToolCallRequest
from app.services.plans.plan_models import PlanNode, PlanTree


def _executor() -> PlanExecutor:
    return PlanExecutor(repo=object(), llm_service=object())


def test_build_tool_failure_error_prefers_explicit_error() -> None:
    executor = _executor()
    message = executor._build_tool_failure_error(
        "code_executor",
        {"success": False, "error": "explicit failure"},
    )
    assert message == "explicit failure"


def test_build_tool_failure_error_uses_exit_and_stream_context() -> None:
    executor = _executor()
    message = executor._build_tool_failure_error(
        "code_executor",
        {
            "success": False,
            "exit_code": 127,
            "stderr": "claude: command not found",
            "stdout": "attempted fallback command",
        },
    )

    assert message.startswith("code_executor failed:")
    assert "exit_code=127" in message
    assert "stderr=claude: command not found" in message
    assert "stdout=attempted fallback command" in message


def test_extract_path_like_values_prefers_artifact_lists_and_skips_internal_paths() -> None:
    executor = _executor()
    payload = {
        "metadata": {
            "artifact_paths": [
                "/tmp/runtime/session_x/results/plan68_task34/NK_cell_upregulated_genes.csv",
                "/tmp/runtime/session_x/tool_outputs/job_dt_x/step_1_code_executor_ab/result.json",
                "/tmp/runtime/session_x/plan68_task34/run_1/code",
                "/tmp/runtime/session_x/plan68_task34/run_1/results",
            ],
            "storage": {
                "result_path": "/tmp/runtime/session_x/tool_outputs/job_dt_x/step_1_code_executor_ab/result.json",
            },
        },
        "produced_files": [
            "/tmp/runtime/session_x/results/plan68_task34/Fibroblast_upregulated_genes.csv",
        ],
    }

    paths = executor._extract_path_like_values(payload)

    assert "/tmp/runtime/session_x/results/plan68_task34/NK_cell_upregulated_genes.csv" in paths
    assert "/tmp/runtime/session_x/results/plan68_task34/Fibroblast_upregulated_genes.csv" in paths
    assert all("/tool_outputs/" not in path for path in paths)
    assert "/tmp/runtime/session_x/plan68_task34/run_1/code" not in paths
    assert "/tmp/runtime/session_x/plan68_task34/run_1/results" not in paths


def test_extract_tool_result_context_preserves_verification_artifacts_and_run_dir() -> None:
    executor = _executor()
    payload = {
        "success": True,
        "result": {
            "tool": "code_executor",
            "run_directory": "/tmp/runtime/session_x/plan68_task35/run_1",
            "working_directory": "/tmp/runtime/session_x/plan68_task35/run_1",
            "artifact_paths": [
                "/tmp/runtime/session_x/plan68_task35/run_1/results/enrichment/gene_id_mapping.csv",
                "/tmp/runtime/session_x/tool_outputs/job_dt_x/step_1_code_executor_ab/result.json",
            ],
            "session_artifact_paths": [
                "/tmp/runtime/session_x/results/plan68_task35/run_1/enrichment/gene_id_mapping.csv",
                "/tmp/runtime/session_x/results/plan68_task35/run_1/enrichment/upregulated_genes_entrez.csv",
            ],
        },
    }

    context = executor._extract_tool_result_context(payload)

    assert context["run_directory"] == "/tmp/runtime/session_x/plan68_task35/run_1"
    assert context["working_directory"] == "/tmp/runtime/session_x/plan68_task35/run_1"
    assert "/tmp/runtime/session_x/plan68_task35/run_1/results/enrichment/gene_id_mapping.csv" in context["artifact_paths"]
    assert "/tmp/runtime/session_x/results/plan68_task35/run_1/enrichment/gene_id_mapping.csv" in context["artifact_paths"]
    assert "/tmp/runtime/session_x/results/plan68_task35/run_1/enrichment/upregulated_genes_entrez.csv" in context["session_artifact_paths"]
    assert all("/tool_outputs/" not in path for path in context["artifact_paths"])


def test_extract_path_like_values_skips_runtime_root_and_current_workspace_paths() -> None:
    executor = _executor()
    payload = {
        "artifact_paths": [
            "/Users/apple/LLM/agent/runtime",
            "/Users/apple/LLM/agent/runtime/session_current/workspace/task14.md",
            "/tmp/runtime/session_x/results/plan68_task35/run_1/evidence.md",
        ]
    }

    paths = executor._extract_path_like_values(payload)

    assert "/tmp/runtime/session_x/results/plan68_task35/run_1/evidence.md" in paths
    assert "/Users/apple/LLM/agent/runtime" not in paths
    assert "/Users/apple/LLM/agent/runtime/session_current/workspace/task14.md" not in paths


def test_execute_tool_call_uses_task_scoped_work_dir(monkeypatch, tmp_path) -> None:
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(id=1, plan_id=7, name="Root", status="pending"),
            2: PlanNode(id=2, plan_id=7, name="Leaf", parent_id=1, status="pending"),
        },
        adjacency={None: [1], 1: [2], 2: []},
    )
    captured = {}

    def _fake_execute_sync(tool_name, params, context):
        captured["tool_name"] = tool_name
        captured["params"] = params
        captured["context"] = context
        return {"success": True}

    executor = PlanExecutor.__new__(PlanExecutor)
    executor._repo = SimpleNamespace(get_plan_tree=lambda _plan_id: tree)
    executor._tool_executor = SimpleNamespace(execute_sync=_fake_execute_sync)
    executor._current_job_id = lambda: None

    monkeypatch.setattr(
        path_router_module,
        "_default_router",
        PathRouter(PathRouterConfig(runtime_root=tmp_path / "runtime")),
    )

    payload = executor._execute_tool_call(
        ToolCallRequest(name="file_operations", parameters={"operation": "list", "path": "."}),
        tree.nodes[2],
        ExecutionConfig(session_context={}),
    )

    expected = (
        tmp_path / "runtime" / "session_adhoc" / "raw_files" / "task_1" / "task_2"
    ).resolve()
    assert payload["success"] is True
    assert captured["tool_name"] == "file_operations"
    assert captured["context"].ancestor_chain == [1]
    assert captured["context"].work_dir == str(expected)


def test_promote_workspace_artifacts_to_task_dir(monkeypatch, tmp_path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)

    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            5: PlanNode(id=5, plan_id=7, name="Root", status="completed"),
            12: PlanNode(id=12, plan_id=7, name="Leaf", parent_id=5, status="completed"),
        },
        adjacency={None: [5], 5: [12], 12: []},
    )

    workspace_file = runtime_root / "session_demo" / "workspace" / "task36_report.md"
    workspace_file.parent.mkdir(parents=True, exist_ok=True)
    workspace_file.write_text("task 36 report", encoding="utf-8")

    executor = PlanExecutor.__new__(PlanExecutor)
    executor._repo = SimpleNamespace(get_plan_tree=lambda _plan_id: tree)

    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_root.resolve()))
    monkeypatch.setattr(
        path_router_module,
        "_default_router",
        PathRouter(PathRouterConfig(runtime_root=runtime_root)),
    )

    payload = {
        "status": "completed",
        "content": "Task 36 completed.",
        "metadata": {},
        "artifact_paths": [str(workspace_file)],
    }

    promoted = executor._promote_workspace_artifacts_to_task_dir(
        node=tree.nodes[12],
        payload=payload,
        session_context={"session_id": "demo"},
    )

    expected = (
        runtime_root / "session_demo" / "raw_files" / "task_5" / "task_12" / "task36_report.md"
    ).resolve()
    assert expected.exists()
    assert str(expected) in promoted["artifact_paths"]
    assert promoted["produced_files"] == promoted["artifact_paths"]
    assert promoted["session_artifact_paths"] == ["raw_files/task_5/task_12/task36_report.md"]
    assert promoted["metadata"]["session_artifact_paths"] == ["raw_files/task_5/task_12/task36_report.md"]
