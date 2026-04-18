from app.services.plans.plan_executor import PlanExecutor


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
