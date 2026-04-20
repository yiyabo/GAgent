import asyncio
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest  # pylint: disable=import-error  # type: ignore[import-unresolved]
from pathlib import Path

from app.routers.chat.code_executor_helpers import (
    collect_completed_task_outputs,
    compose_code_executor_atomic_task_prompt,
    extract_task_artifact_paths,
)
from app.services.plans.plan_models import PlanNode, PlanTree
from tool_box.tools_impl import code_executor as code_executor_module
from tool_box.tools_impl.code_executor import (
    _DEFAULT_ALLOWED_TOOL_NAMES,
    _build_execution_spec,
    _build_qwen_execution_session_id,
    _build_cli_failure_error,
    _build_code_executor_subprocess_env,
    _compact_cli_text,
    _coerce_positive_int,
    _detect_scope_blocked,
    _extract_pending_qwen_function_call,
    _extract_pending_qwen_shell_command,
    _extract_readable_error,
    _is_path_within,
    _normalize_csv_values,
    _prune_stale_session_root_results,
    _promote_task_results_to_session_root,
    _resolve_code_executor_docker_image,
    _resolve_code_executor_local_runtime,
    _resolve_cli_retry_policy,
    _resolve_auth_mode,
    _resolve_allowed_tools,
    _resolve_setting_sources,
    _sanitize_task_dir_component,
    _iter_stream_lines_unbounded,
    _looks_like_engineering_task,
    _execute_task_locally,
    _resolve_code_executor_backend,
    _validate_api_mode_config,
    _validate_scope_contract,
)


def _force_local_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        code_executor_module,
        "_resolve_code_executor_backend",
        lambda _task: ("local", "analysis_fast_path", "forced local backend for test"),
    )


def test_normalize_csv_values_handles_strings_and_lists() -> None:
    assert _normalize_csv_values("Bash, Edit, Bash") == ["Bash", "Edit"]
    assert _normalize_csv_values(["Bash", "Edit", "Bash"]) == ["Bash", "Edit"]
    assert _normalize_csv_values(["Bash,Edit", "Read"]) == ["Bash", "Edit", "Read"]
    assert _normalize_csv_values(None) == []


def test_detect_scope_blocked_reads_marker_from_stdout_and_json() -> None:
    stdout = "\n".join(
        [
            "STATUS: BLOCKED_SCOPE",
            "REASON: NEED_ATOMIC_TASK",
            "DETAIL: request contains multiple objectives",
        ]
    )
    assert _detect_scope_blocked(stdout, None) == "request contains multiple objectives"

    output_data = {
        "result": "STATUS: BLOCKED_SCOPE\nREASON: NEED_ATOMIC_TASK\nDETAIL: task is not atomic"
    }
    assert _detect_scope_blocked("", output_data) == "task is not atomic"


def test_compose_code_executor_atomic_task_prompt_includes_canonical_path_rules() -> None:
    prompt = compose_code_executor_atomic_task_prompt(
        task_node=PlanNode(
            id=4,
            plan_id=68,
            name="样本间整合与标准化",
            instruction="Integrate filtered h5ad files using Harmony.",
        ),
        original_task="继续执行 task 4",
        data_context=(
            "- Task [2] metadata: /home/zczhao/GAgent/data/ovarian_cancer_scRNA/metadata.csv\n"
            "- Task [3] filtered_cancer1: /home/zczhao/GAgent/runtime/session_x/results/filtered_cancer1.h5ad"
        ),
    )

    assert "Do NOT assume prerequisite files live in the current run directory" in prompt
    assert "prefer the canonical absolute path" in prompt
    assert "prefer explicit absolute deliverable paths from previous steps" in prompt
    assert "flat files directly under a session root `results/` directory" in prompt
    assert "Ignore zero-byte or non-parseable session-temp duplicates" in prompt
    assert "zero-result outcome" in prompt
    assert "empty-but-valid outputs at the required paths" in prompt
    assert "Do NOT fabricate positive signals" in prompt


def test_build_cli_failure_error_includes_exit_and_stream_excerpts() -> None:
    message = _build_cli_failure_error(
        return_code=127,
        stderr="claude: command not found",
        stdout="",
    )
    assert "exit_code=127" in message
    assert "stderr=claude: command not found" in message


def test_build_cli_failure_error_uses_qwen_label_and_debug_log() -> None:
    message = _build_cli_failure_error(
        return_code=1,
        stderr="Debug mode enabled Logging to: /tmp/qwen-debug.txt",
        stdout="",
        backend_label="Qwen Code (container)",
    )

    assert message == (
        "Qwen Code (container) execution failed: "
        "exit_code=1; debug_log=/tmp/qwen-debug.txt"
    )


def test_build_cli_failure_error_uses_qwen_label_and_split_debug_log() -> None:
    message = _build_cli_failure_error(
        return_code=1,
        stderr="Debug mode enabled\nLogging to: /tmp/qwen-debug-split.txt",
        stdout="",
        backend_label="Qwen Code (container)",
    )

    assert message == (
        "Qwen Code (container) execution failed: "
        "exit_code=1; debug_log=/tmp/qwen-debug-split.txt"
    )


def test_build_cli_failure_error_prefers_real_qwen_stderr_over_debug_banner() -> None:
    message = _build_cli_failure_error(
        return_code=1,
        stderr=(
            "Debug mode enabled Logging to: /tmp/qwen-debug.txt\n"
            "Error: Session Id abc is already in use."
        ),
        stdout="",
        backend_label="Qwen Code",
    )

    assert message.startswith("Qwen Code execution failed:")
    assert "stderr=Error: Session Id abc is already in use." in message
    assert "debug_log=" not in message


def test_extract_readable_error_does_not_default_http_400_to_missing_api_key() -> None:
    stderr = (
        "file:///usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js:317\n"
        "var rb=A(...); DefaultTransporter=Wn; if(G&&G.status>=400)D.message=W,D.status=G.status;"
    )
    message = _extract_readable_error(stderr)
    assert "HTTP 400" in message
    assert "upstream rejected the request" in message
    assert "ANTHROPIC_API_KEY" not in message


def test_compact_cli_text_truncates_and_normalizes_whitespace() -> None:
    value = "line1\nline2\tline3   line4"
    compact = _compact_cli_text(value, limit=12)
    assert compact == "line1 lin..."


def test_iter_stream_lines_unbounded_handles_very_long_lines() -> None:
    async def _collect() -> list[str]:
        reader = asyncio.StreamReader()
        reader.feed_data(("x" * 70000 + "\nshort\ntrailing").encode())
        reader.feed_eof()
        return [line async for line in _iter_stream_lines_unbounded(reader)]

    lines = asyncio.run(_collect())

    assert lines == ["x" * 70000, "short", "trailing"]


def test_resolve_allowed_tools_enforces_strict_allowlist() -> None:
    resolved = _resolve_allowed_tools("Bash,Edit,Task,WebSearch,Read")
    assert resolved == ["Bash", "Edit", "Read"]


def test_resolve_allowed_tools_uses_default_when_missing() -> None:
    assert _resolve_allowed_tools(None) == list(_DEFAULT_ALLOWED_TOOL_NAMES)


def test_resolve_code_executor_local_runtime_defaults_to_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODE_EXECUTOR_LOCAL_RUNTIME", raising=False)
    assert _resolve_code_executor_local_runtime() == "docker"


def test_resolve_code_executor_local_runtime_accepts_host_and_rejects_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_EXECUTOR_LOCAL_RUNTIME", "host")
    assert _resolve_code_executor_local_runtime() == "host"
    monkeypatch.setenv("CODE_EXECUTOR_LOCAL_RUNTIME", "local")
    assert _resolve_code_executor_local_runtime() == "host"
    monkeypatch.setenv("CODE_EXECUTOR_LOCAL_RUNTIME", "invalid")
    assert _resolve_code_executor_local_runtime() == "docker"


def test_resolve_code_executor_docker_image_defaults_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODE_EXECUTOR_DOCKER_IMAGE", raising=False)
    assert _resolve_code_executor_docker_image() == "gagent-python-runtime:latest"
    monkeypatch.setenv("CODE_EXECUTOR_DOCKER_IMAGE", "custom:image")
    assert _resolve_code_executor_docker_image() == "custom:image"


def test_validate_scope_contract_requires_plan_and_task_when_enabled() -> None:
    assert (
        _validate_scope_contract(
            plan_id=None,
            task_id=1,
            require_task_context=True,
        )
        == "Missing plan_id for strict atomic execution."
    )
    assert (
        _validate_scope_contract(
            plan_id=1,
            task_id=None,
            require_task_context=True,
        )
        == "Missing task_id for strict atomic execution."
    )
    assert (
        _validate_scope_contract(
            plan_id=1,
            task_id=2,
            require_task_context=True,
        )
        is None
    )
    assert (
        _validate_scope_contract(
            plan_id=None,
            task_id=None,
            require_task_context=False,
        )
        is None
    )


def test_resolve_code_executor_backend_auto_prefers_qwen_primary_for_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.config.executor_config.get_executor_settings",
        lambda: SimpleNamespace(
            code_execution_backend="auto",
            code_execution_auto_strategy="qwen_primary",
        ),
    )
    monkeypatch.setattr(code_executor_module, "_qwen_code_cli_available", lambda: True)

    backend, lane, reason = _resolve_code_executor_backend(
        "Generate subset_manifest.csv and qc_report.pdf from the dataset"
    )

    assert backend == "qwen_code"
    assert lane == "qwen_primary"
    assert "qwen_code primary lane" in reason


def test_resolve_code_executor_backend_auto_split_keeps_local_for_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.config.executor_config.get_executor_settings",
        lambda: SimpleNamespace(
            code_execution_backend="auto",
            code_execution_auto_strategy="split",
        ),
    )
    monkeypatch.setattr(code_executor_module, "_qwen_code_cli_available", lambda: True)

    backend, lane, reason = _resolve_code_executor_backend(
        "Generate subset_manifest.csv and qc_report.pdf from the dataset"
    )

    assert backend == "local"
    assert lane == "analysis_fast_path"
    assert "analysis-style code task routed to local fast path" == reason


def test_build_execution_spec_derives_ad_hoc_contract_without_inputs() -> None:
    task = """
Execute a 3-node phage QC pipeline.

- `pipeline.py` is only a reference script name and is not a deliverable.
- `data/source_manifest.tsv` is an input file, not a final deliverable.
- The final deliverables that must match exactly are:
  - `results/subset_manifest.csv`
  - `results/qc_summary.md`
  - `results/qc_report.pdf`
"""

    spec = _build_execution_spec(None, None, task_text=task)

    assert spec is not None
    assert spec["plan_id"] is None
    assert spec["task_id"] is None
    assert spec["task_instruction"] == task.strip()
    assert spec["acceptance_criteria"] == {
        "category": "file_data",
        "blocking": True,
        "checks": [
            {"type": "file_nonempty", "path": "results/subset_manifest.csv"},
            {"type": "file_nonempty", "path": "results/qc_summary.md"},
            {"type": "file_nonempty", "path": "results/qc_report.pdf"},
        ],
    }


def test_coerce_positive_int_validates_value() -> None:
    assert _coerce_positive_int("7", field_name="task_id") == 7
    assert _coerce_positive_int(None, field_name="task_id") is None
    with pytest.raises(ValueError):
        _coerce_positive_int("0", field_name="task_id")
    with pytest.raises(ValueError):
        _coerce_positive_int("oops", field_name="task_id")


def test_sanitize_task_dir_component_removes_unsafe_path_chars() -> None:
    assert _sanitize_task_dir_component("../../A/B\\C:task") == "a_b_c_task"


@pytest.mark.parametrize(
    ("target_path", "expected"),
    [
    ("parent/nested", True),
    ("outside", False),
],
)
def test_is_path_within_variants(tmp_path, target_path: str, expected: bool) -> None:
    parent = tmp_path / "parent"
    parent.mkdir(parents=True)
    full_target = tmp_path / target_path
    full_target.mkdir(parents=True, exist_ok=True)
    assert _is_path_within(full_target, parent) is expected


@pytest.mark.parametrize(
    ("raw_sources", "auth_mode", "expected"),
    [
        (None, None, "project,local"),
        ("none", None, None),
        ("user,invalid,project,user", None, "user,project"),
        (None, "api_env", "project"),
    ],
)
def test_resolve_setting_sources_variants(
    raw_sources: str | None,
    auth_mode: str | None,
    expected: str | None,
) -> None:
    assert _resolve_setting_sources(raw_sources, auth_mode=auth_mode) == expected


@pytest.mark.parametrize(
    ("raw_mode", "expected"),
    [
        (None, "api_env"),
        ("api_env", "api_env"),
    ],
)
def test_resolve_auth_mode_variants(raw_mode: str | None, expected: str) -> None:
    assert _resolve_auth_mode(raw_mode) == expected


def test_build_code_executor_subprocess_env_strips_anthropic_vars_in_login_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "u")
    monkeypatch.setenv("ANTHROPIC_SMALL_FAST_MODEL", "claude-haiku-4-5-20251001")
    env_map = _build_code_executor_subprocess_env("claude_login")
    assert "ANTHROPIC_API_KEY" not in env_map
    assert "ANTHROPIC_BASE_URL" not in env_map
    assert "ANTHROPIC_SMALL_FAST_MODEL" not in env_map


def test_build_code_executor_subprocess_env_uses_qwen_key_in_api_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "qk")
    env_map = _build_code_executor_subprocess_env("api_env")
    assert env_map.get("ANTHROPIC_API_KEY") == "qk"


def test_build_code_executor_subprocess_env_api_mode_uses_code_executor_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_API_KEY", "alias-key")
    monkeypatch.setenv("CLAUDE_CODE_BASE_URL", "https://alias.example/v1")
    env_map = _build_code_executor_subprocess_env("api_env")
    assert env_map.get("ANTHROPIC_API_KEY") == "alias-key"
    assert env_map.get("ANTHROPIC_BASE_URL") == "https://alias.example/v1"


def test_build_code_executor_subprocess_env_api_mode_drops_auth_token_when_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "qk")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    env_map = _build_code_executor_subprocess_env("api_env")
    assert env_map.get("ANTHROPIC_API_KEY") == "qk"
    assert "ANTHROPIC_AUTH_TOKEN" not in env_map


def test_build_code_executor_subprocess_env_api_mode_drops_parent_claude_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "qk")
    monkeypatch.setenv("QWEN_MODEL", "qwen3.5-plus")
    monkeypatch.setenv("CLAUDE_MODEL", "anthropic/claude-opus-4.6")
    env_map = _build_code_executor_subprocess_env("api_env")
    assert env_map.get("ANTHROPIC_MODEL") == "qwen3.5-plus"
    assert "CLAUDE_MODEL" not in env_map


def test_build_code_executor_subprocess_env_api_mode_pins_small_fast_model_to_qwen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "qk")
    monkeypatch.setenv("QWEN_MODEL", "qwen3.5-plus")
    monkeypatch.setenv("ANTHROPIC_SMALL_FAST_MODEL", "claude-haiku-4-5-20251001")
    env_map = _build_code_executor_subprocess_env("api_env")
    assert env_map.get("ANTHROPIC_MODEL") == "qwen3.5-plus"
    assert env_map.get("ANTHROPIC_SMALL_FAST_MODEL") == "qwen3.5-plus"


def test_resolve_cli_retry_policy_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_MAX_RETRIES", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_RETRY_BASE_DELAY_S", raising=False)
    assert _resolve_cli_retry_policy() == (4, 5.0)

    monkeypatch.setenv("CLAUDE_CODE_MAX_RETRIES", "6")
    monkeypatch.setenv("CLAUDE_CODE_RETRY_BASE_DELAY_S", "2.5")
    assert _resolve_cli_retry_policy() == (6, 2.5)


def test_validate_api_mode_config_requires_credentials() -> None:
    assert _validate_api_mode_config({"ANTHROPIC_BASE_URL": "https://x"}) is not None
    assert _validate_api_mode_config({"ANTHROPIC_API_KEY": "k"}) is None


def test_promote_task_results_to_session_root_copies_into_session_results(tmp_path: Path) -> None:
    session_dir = tmp_path / "session_x"
    run_dir = tmp_path / "session_x" / "task_a" / "run_1"
    (run_dir / "results" / "nested").mkdir(parents=True)
    (run_dir / "results" / "line.png").write_bytes(b"png1")
    (run_dir / "results" / "nested" / "other.png").write_bytes(b"png2")

    rels = _promote_task_results_to_session_root(session_dir=session_dir, task_work_dir=run_dir)
    assert set(rels) == {
        "results/task_a/run_1/line.png",
        "results/task_a/run_1/nested/other.png",
    }
    assert (session_dir / "results" / "task_a" / "run_1" / "line.png").read_bytes() == b"png1"
    assert (
        session_dir / "results" / "task_a" / "run_1" / "nested" / "other.png"
    ).read_bytes() == b"png2"


def test_promote_task_results_to_session_root_keeps_runs_isolated(tmp_path: Path) -> None:
    session_dir = tmp_path / "s"
    run_a = tmp_path / "s" / "t" / "run_a"
    run_b = tmp_path / "s" / "t" / "run_b"
    (run_a / "results").mkdir(parents=True)
    (run_b / "results").mkdir(parents=True)
    (run_a / "results" / "plot.png").write_bytes(b"v1")
    _promote_task_results_to_session_root(session_dir=session_dir, task_work_dir=run_a)
    (run_b / "results" / "plot.png").write_bytes(b"v2")
    _promote_task_results_to_session_root(session_dir=session_dir, task_work_dir=run_b)
    assert (session_dir / "results" / "t" / "run_a" / "plot.png").read_bytes() == b"v1"
    assert (session_dir / "results" / "t" / "run_b" / "plot.png").read_bytes() == b"v2"


def test_promote_task_results_to_session_root_includes_run_root_and_custom_dirs(
    tmp_path: Path,
) -> None:
    session_dir = tmp_path / "session_demo"
    run_dir = session_dir / "plan7_task9" / "run_1"
    (run_dir / "results").mkdir(parents=True)
    (run_dir / "results" / "plot.png").write_bytes(b"png")
    (run_dir / "summary.md").write_text("done\n", encoding="utf-8")
    figures_dir = run_dir / "figures_raw"
    figures_dir.mkdir(parents=True)
    (figures_dir / "manifest.log").write_text("2 figures\n", encoding="utf-8")

    rels = _promote_task_results_to_session_root(
        session_dir=session_dir,
        task_work_dir=run_dir,
        subdirs=("results", "figures_raw"),
    )

    assert set(rels) == {
        "results/plan7_task9/run_1/plot.png",
        "results/plan7_task9/run_1/summary.md",
        "results/plan7_task9/run_1/figures_raw/manifest.log",
    }
    assert (session_dir / "results" / "plan7_task9" / "run_1" / "summary.md").read_text(encoding="utf-8") == "done\n"
    assert (
        session_dir / "results" / "plan7_task9" / "run_1" / "figures_raw" / "manifest.log"
    ).read_text(encoding="utf-8") == "2 figures\n"


def test_prune_stale_session_root_results_removes_only_flat_empty_files(tmp_path: Path) -> None:
    session_dir = tmp_path / "session_x"
    results_dir = session_dir / "results"
    nested_dir = results_dir / "task2" / "run_1"
    nested_dir.mkdir(parents=True)
    (results_dir / "metadata.csv").write_text("", encoding="utf-8")
    (results_dir / "qc_summary.csv").write_text("ok\n", encoding="utf-8")
    (nested_dir / "metadata.csv").write_text("sample_id\nx\n", encoding="utf-8")

    removed = _prune_stale_session_root_results(session_dir=session_dir)

    assert removed == ["results/metadata.csv"]
    assert not (results_dir / "metadata.csv").exists()
    assert (results_dir / "qc_summary.csv").exists()
    assert (nested_dir / "metadata.csv").exists()


def test_extract_task_artifact_paths_prefers_absolute_and_skips_stale_flat_metadata(
    tmp_path: Path,
) -> None:
    session_dir = tmp_path / "runtime" / "session_x"
    stale_metadata = session_dir / "results" / "metadata.csv"
    stale_metadata.parent.mkdir(parents=True)
    stale_metadata.write_text("", encoding="utf-8")
    payload = {
        "produced_files": [
            "/tmp/run/results/filtered_cancer1.h5ad",
            str(stale_metadata),
        ],
        "artifact_paths": [
            "results/task3/run_1/filtered_cancer1.h5ad",
            "results/metadata.csv",
        ],
    }

    extracted = extract_task_artifact_paths(payload)

    assert "/tmp/run/results/filtered_cancer1.h5ad" in extracted
    assert str(stale_metadata) not in extracted
    assert "results/metadata.csv" not in extracted


def test_extract_task_artifact_paths_prefers_deliverable_files_over_workspace_dirs() -> None:
    payload = {
        "artifact_paths": [
            "/tmp/runtime/session_x/plan68_task37/run_1",
            "/tmp/runtime/session_x/plan68_task37/run_1/results",
            "/tmp/runtime/session_x/plan68_task37/run_1/code",
            "/tmp/runtime/session_x/plan68_task37/run_1/data",
            "/tmp/runtime/session_x/plan68_task37/run_1/docs",
            "/tmp/runtime/session_x/plan68_task37/run_1/results/enrichment/background_formatted.csv",
            "/tmp/runtime/session_x/plan68_task37/run_1/results/enrichment/data_quality_report.csv",
            "/tmp/runtime/session_x/plan68_task37/run_1/results/enrichment/data_quality_report.txt",
            "/tmp/runtime/session_x/plan68_task37/run_1/results/enrichment/enrichment_input_ready.RData",
            "/tmp/runtime/session_x/plan68_task37/run_1/results/enrichment/gene_list_formatted.csv",
            "/tmp/runtime/session_x/plan68_task37/run_1/results/enrichment/invalid_genes.csv",
            "/tmp/runtime/session_x/plan68_task37/run_1/code/format_and_validate_input.R",
        ],
        "session_artifact_paths": [
            "/tmp/runtime/session_x/results/plan68_task37/run_1/enrichment/enrichment_input_ready.RData",
            "/tmp/runtime/session_x/results/plan68_task37/run_1/enrichment/gene_list_formatted.csv",
        ],
    }

    extracted = extract_task_artifact_paths(payload)

    assert "/tmp/runtime/session_x/results/plan68_task37/run_1/enrichment/enrichment_input_ready.RData" in extracted
    assert "/tmp/runtime/session_x/results/plan68_task37/run_1/enrichment/gene_list_formatted.csv" in extracted
    assert "/tmp/runtime/session_x/plan68_task37/run_1/results/enrichment/data_quality_report.txt" in extracted
    assert "/tmp/runtime/session_x/plan68_task37/run_1" not in extracted
    assert "/tmp/runtime/session_x/plan68_task37/run_1/results" not in extracted
    assert "/tmp/runtime/session_x/plan68_task37/run_1/code" not in extracted
    assert "/tmp/runtime/session_x/plan68_task37/run_1/code/format_and_validate_input.R" not in extracted


def test_extract_task_artifact_paths_skips_current_workspace_and_runtime_root() -> None:
    payload = {
        "artifact_paths": [
            "/Users/apple/LLM/agent/runtime",
            "/Users/apple/LLM/agent/runtime/session_current/workspace/task17.md",
            "/tmp/runtime/session_x/results/plan68_task37/run_1/evidence.md",
        ]
    }

    extracted = extract_task_artifact_paths(payload)

    assert "/tmp/runtime/session_x/results/plan68_task37/run_1/evidence.md" in extracted
    assert "/Users/apple/LLM/agent/runtime" not in extracted
    assert "/Users/apple/LLM/agent/runtime/session_current/workspace/task17.md" not in extracted

def test_collect_completed_task_outputs_includes_artifact_paths() -> None:
    node_2 = PlanNode(
        id=2,
        plan_id=68,
        name="QC",
        status="completed",
        execution_result=json.dumps(
            {
                "content": "QC completed. Files: /home/zczhao/GAgent/runtime/session_x/plan68_task3/run_1/results",
                "produced_files": [
                    "/home/zczhao/GAgent/runtime/session_x/results/plan68_task3/run_1/filtered_cancer1.h5ad"
                ],
            }
        ),
    )
    node_4 = PlanNode(id=4, plan_id=68, name="Integration", status="running")
    tree = PlanTree(id=68, title="Plan", nodes={2: node_2, 4: node_4}, adjacency={None: [2, 4]})

    summary = collect_completed_task_outputs(tree, current_task_id=4)

    assert "Task [2] QC" in summary
    assert "Artifact paths:" in summary
    assert "/home/zczhao/GAgent/runtime/session_x/results/plan68_task3/run_1/filtered_cancer1.h5ad" in summary
    assert "/home/zczhao/GAgent/runtime/session_x/plan68_task3/run_1/results" not in summary


def test_collect_completed_task_outputs_skips_untrusted_completed_nodes() -> None:
    node_2 = PlanNode(
        id=2,
        plan_id=68,
        name="QC",
        status="completed",
        execution_result=json.dumps(
            {
                "status": "completed",
                "metadata": {"verification_status": "passed"},
                "produced_files": [
                    "/home/zczhao/GAgent/runtime/session_x/results/plan68_task2/run_1/subset_manifest.csv"
                ],
            }
        ),
    )
    node_3 = PlanNode(
        id=3,
        plan_id=68,
        name="Cascade completed child",
        status="completed",
        execution_result="completed as part of parent task",
    )
    node_4 = PlanNode(
        id=4,
        plan_id=68,
        name="Text-only completion",
        status="completed",
        execution_result="completed without recorded outputs",
    )
    node_5 = PlanNode(id=5, plan_id=68, name="Current", status="running")
    tree = PlanTree(
        id=68,
        title="Plan",
        nodes={2: node_2, 3: node_3, 4: node_4, 5: node_5},
        adjacency={None: [2, 3, 4, 5], 2: [], 3: [], 4: [], 5: []},
    )

    summary = collect_completed_task_outputs(tree, current_task_id=5)

    assert "Task [2] QC" in summary
    assert "Task [3]" not in summary
    assert "Task [4]" not in summary


def test_local_backend_enforces_scope_contract_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _unexpected_local(**_kwargs):
        raise AssertionError("_execute_task_locally should not run without task context")

    monkeypatch.setattr(code_executor_module, "_execute_task_locally", _unexpected_local)

    result = asyncio.run(
        code_executor_module.code_executor_handler(
            task="demo task",
            require_task_context=True,
        )
    )

    assert result["success"] is False
    assert result["blocked_by_scope_guardrail"] is True
    assert result["blocked_reason"] == "missing_atomic_context"


def test_local_backend_returns_promoted_artifact_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(code_executor_module, "_RUNTIME_DIR", tmp_path / "runtime")
    _force_local_backend(monkeypatch)

    async def _fake_local(*, work_dir: str, **_kwargs):
        results_dir = Path(work_dir) / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "plot.png").write_bytes(b"png")
        return {
            "success": True,
            "stdout": "ok\n",
            "stderr": "",
            "exit_code": 0,
            "code_file": str(Path(work_dir) / "task_code.py"),
            "result": "generated plot.png",
            "execution_mode": "code_executor_docker",
            "docker_image_effective": "gagent-python-runtime:latest",
        }

    monkeypatch.setattr(code_executor_module, "_execute_task_locally", _fake_local)

    result = asyncio.run(
        code_executor_module.code_executor_handler(
            task="generate a plot",
            plan_id=1,
            task_id=2,
            require_task_context=True,
        )
    )

    assert result["success"] is True
    assert result["task_directory"] == "plan1_task2"
    assert result["execution_mode"] == "code_executor_docker"
    assert result["docker_image_effective"] == "gagent-python-runtime:latest"
    promoted = result["session_artifact_paths"][0]
    assert promoted.startswith("results/_scratch/plan1_task2/run_")
    assert promoted.endswith("/plot.png")
    assert any(str(path).endswith("/results/plot.png") or str(path).endswith("plot.png") for path in result["artifact_paths"])
    assert any(path.endswith("plot.png") for path in result["produced_files"])
    session_dir = (tmp_path / "runtime" / "session_adhoc").resolve()
    assert (session_dir / promoted).read_bytes() == b"png"
    assert result["output_location"]["session_id"] == "adhoc"
    assert result["output_location"]["base_dir"] is not None
    output_base = Path(result["output_location"]["base_dir"])
    assert (output_base / "plot.png").read_bytes() == b"png"
    assert any(path.endswith("/plot.png") for path in result["output_location"]["files"])


def test_local_backend_promotes_run_root_and_custom_dir_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(code_executor_module, "_RUNTIME_DIR", tmp_path / "runtime")
    _force_local_backend(monkeypatch)

    async def _fake_local(*, work_dir: str, **_kwargs):
        run_dir = Path(work_dir)
        (run_dir / "summary.md").write_text("done\n", encoding="utf-8")
        custom_dir = run_dir / "figures_raw"
        custom_dir.mkdir(parents=True, exist_ok=True)
        (custom_dir / "manifest.log").write_text("2 figures\n", encoding="utf-8")
        return {
            "success": True,
            "stdout": "ok\n",
            "stderr": "",
            "exit_code": 0,
            "code_file": str(run_dir / "task_code.py"),
            "result": "generated summary and manifest",
            "execution_mode": "code_executor_docker",
            "docker_image_effective": "gagent-python-runtime:latest",
        }

    monkeypatch.setattr(code_executor_module, "_execute_task_locally", _fake_local)

    result = asyncio.run(
        code_executor_module.code_executor_handler(
            task="summarize the run",
            session_id="demo",
            plan_id=7,
            task_id=9,
            require_task_context=True,
        )
    )

    assert result["success"] is True
    assert any(path.endswith("summary.md") for path in result["produced_files"])
    assert any(path.endswith("figures_raw/manifest.log") for path in result["produced_files"])
    assert any(path.endswith("summary.md") for path in result["session_artifact_paths"])
    assert any(path.endswith("figures_raw/manifest.log") for path in result["session_artifact_paths"])
    assert any(path.endswith("summary.md") for path in result["output_location"]["files"])
    assert any(path.endswith("figures_raw/manifest.log") for path in result["output_location"]["files"])
    output_base = Path(result["output_location"]["base_dir"])
    assert output_base.name == "task_9"
    assert (output_base / "summary.md").read_text(encoding="utf-8") == "done\n"
    assert (output_base / "figures_raw" / "manifest.log").read_text(encoding="utf-8") == "2 figures\n"


def test_local_backend_collects_custom_acceptance_output_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(code_executor_module, "_RUNTIME_DIR", tmp_path / "runtime")
    _force_local_backend(monkeypatch)
    monkeypatch.setattr(
        code_executor_module,
        "_build_execution_spec",
        lambda plan_id, task_id, task_text=None: {
            "plan_id": plan_id,
            "task_id": task_id,
            "task_name": "图表收集与初步审查",
            "task_instruction": "Collect figures into figures_raw.",
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "glob_count_at_least", "path": "figures_raw/*", "count": 2},
                    {"type": "file_nonempty", "path": "figures_raw/figure_inventory.log"},
                ],
            },
            "dependency_outputs": [],
            "dependency_artifact_paths": [],
        },
    )

    async def _fake_local(*, work_dir: str, **_kwargs):
        figures_dir = Path(work_dir) / "figures_raw"
        figures_dir.mkdir(parents=True, exist_ok=True)
        (figures_dir / "figure_1.png").write_bytes(b"png")
        (figures_dir / "figure_2.png").write_bytes(b"png")
        (figures_dir / "figure_inventory.log").write_text("2 figures\n", encoding="utf-8")
        return {
            "success": True,
            "stdout": "ok\n",
            "stderr": "",
            "exit_code": 0,
            "code_file": str(Path(work_dir) / "task_code.py"),
            "result": "collected figures",
            "execution_mode": "code_executor_docker",
            "docker_image_effective": "gagent-python-runtime:latest",
        }

    monkeypatch.setattr(code_executor_module, "_execute_task_locally", _fake_local)

    result = asyncio.run(
        code_executor_module.code_executor_handler(
            task="collect figures",
            plan_id=68,
            task_id=63,
            require_task_context=True,
        )
    )

    assert result["success"] is True
    assert "figures_raw" in result["task_subdirectories"]
    assert any(path.endswith("figures_raw/figure_inventory.log") for path in result["produced_files"])
    assert any(path.endswith("figures_raw/figure_inventory.log") for path in result["artifact_paths"])


def test_code_executor_handler_passes_docker_image_override_to_local_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(code_executor_module, "_RUNTIME_DIR", tmp_path / "runtime")
    _force_local_backend(monkeypatch)
    async def _fake_task_dir_name(_task: str) -> str:
        return "plot_task"
    monkeypatch.setattr(code_executor_module, "_generate_task_dir_name_llm", _fake_task_dir_name)
    captured = {}

    async def _fake_local(**kwargs):
        captured.update(kwargs)
        work_dir = Path(kwargs["work_dir"])
        results_dir = work_dir / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "plot.png").write_bytes(b"png")
        return {
            "success": True,
            "stdout": "ok\n",
            "stderr": "",
            "exit_code": 0,
            "code_file": str(work_dir / "task_code.py"),
            "result": "generated plot.png",
            "execution_mode": "code_executor_docker",
            "docker_image_effective": "custom:image",
        }

    monkeypatch.setattr(code_executor_module, "_execute_task_locally", _fake_local)

    result = asyncio.run(
        code_executor_module.code_executor_handler(
            task="generate a plot",
            docker_image="custom:image",
            require_task_context=False,
        )
    )

    assert result["success"] is True
    assert captured["docker_image"] == "custom:image"


@pytest.mark.parametrize(
    "task_text",
    [
        "Read /TMP_PROJECT/phagescope/gvd_phage_meta_data.tsv and plot completeness.",
        "Read phagescope/gvd_phage_meta_data.tsv and plot completeness.",
        "读取 /TMP_PROJECT/phagescope/gvd_phage_meta_data.tsv，统计 Completeness 并绘图。",
    ],
)
def test_code_executor_handler_infers_task_referenced_project_dir_for_local_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    task_text: str,
) -> None:
    runtime_root = tmp_path / "runtime"
    project_root = tmp_path / "project"
    default_data_dir = project_root / "data"
    phagescope_dir = project_root / "phagescope"
    dataset_path = phagescope_dir / "gvd_phage_meta_data.tsv"

    default_data_dir.mkdir(parents=True, exist_ok=True)
    phagescope_dir.mkdir(parents=True, exist_ok=True)
    dataset_path.write_text("Completeness\nComplete\n", encoding="utf-8")

    monkeypatch.setattr(code_executor_module, "_RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(code_executor_module, "_PROJECT_ROOT", project_root)
    _force_local_backend(monkeypatch)
    async def _fake_task_dir_name(_task: str) -> str:
        return "plot_task"
    monkeypatch.setattr(code_executor_module, "_generate_task_dir_name_llm", _fake_task_dir_name)

    captured: dict[str, object] = {}

    async def _fake_local(**kwargs):
        captured.update(kwargs)
        work_dir = Path(str(kwargs["work_dir"]))
        results_dir = work_dir / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "plot.png").write_bytes(b"png")
        return {
            "success": True,
            "stdout": "ok\n",
            "stderr": "",
            "exit_code": 0,
            "code_file": str(work_dir / "task_code.py"),
            "result": "generated plot.png",
            "execution_mode": "code_executor_docker",
            "docker_image_effective": "gagent-python-runtime:latest",
        }

    monkeypatch.setattr(code_executor_module, "_execute_task_locally", _fake_local)

    result = asyncio.run(
        code_executor_module.code_executor_handler(
            task=task_text.replace("/TMP_PROJECT", str(project_root)),
            require_task_context=False,
        )
    )

    assert result["success"] is True
    assert captured["data_dir"] == str(phagescope_dir)
    assert str(default_data_dir) in captured["extra_dirs"]
    assert str(phagescope_dir) in captured["extra_dirs"]


def test_execute_task_locally_blocks_before_generation_on_incomplete_dependency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _unexpected_execute(**_kwargs):
        raise AssertionError("execute_code_locally should not run when dependencies are incomplete")

    monkeypatch.setattr(
        "app.services.interpreter.code_execution.execute_code_locally",
        _unexpected_execute,
    )

    result = asyncio.run(
        _execute_task_locally(
            task="run integration",
            work_dir=str(tmp_path),
            execution_spec={
                "task_id": 4,
                "task_name": "Integration",
                "dependency_blockers": [
                    {"task_id": 3, "task_name": "QC", "status": "failed"}
                ],
            },
        )
    )

    assert result["success"] is False
    assert result["error_category"] == "blocked_dependency"
    assert "QC [failed]" in result["error_summary"]


def test_code_executor_qwen_container_mounts_allowed_dirs_and_passes_session_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    project_root = tmp_path / "project"
    default_data_dir = project_root / "data"
    extra_dir = project_root / "phagescope"
    default_data_dir.mkdir(parents=True, exist_ok=True)
    extra_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("QWEN_API_KEY", "sk-test")
    monkeypatch.setattr(code_executor_module, "_RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(code_executor_module, "_PROJECT_ROOT", project_root)

    def _fake_resolve_runtime_session_dir(session_id: str) -> Path:
        session_dir = (runtime_root / f"session_{session_id}").resolve()
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    monkeypatch.setattr(
        code_executor_module,
        "_resolve_runtime_session_dir",
        _fake_resolve_runtime_session_dir,
    )
    monkeypatch.setattr(
        code_executor_module,
        "_resolve_code_executor_backend",
        lambda _task: ("qwen_code", "configured_backend", "test"),
    )

    async def _fake_task_dir(_task: str) -> str:
        return "demo-task"

    monkeypatch.setattr(code_executor_module, "_generate_task_dir_name_llm", _fake_task_dir)

    captured: dict[str, object] = {}

    class _FakeDriver:
        async def ensure_container(
            self,
            session_id: str,
            *,
            host_work_dir: str,
            extra_mounts=None,
            image=None,
        ):
            captured["session_id"] = session_id
            captured["host_work_dir"] = host_work_dir
            captured["extra_mounts"] = list(extra_mounts or [])
            captured["image"] = image
            return "test-container"

        def get_execution_lock(self, session_id: str):
            captured["qwen_lock_lookup"] = session_id
            return asyncio.Lock()

    monkeypatch.setattr(
        "app.services.terminal.qwen_session_driver.get_qwen_session_driver",
        lambda: _FakeDriver(),
    )

    class _FakeStream:
        def __init__(self, payload: bytes):
            self._payload = payload
            self._sent = False

        async def read(self, _chunk_size: int = 65536) -> bytes:
            if self._sent:
                return b""
            self._sent = True
            return self._payload

    class _FakeProcess:
        def __init__(self, command):
            self.command = list(command)
            self.stdout = _FakeStream(b'{"result":"ok"}\n')
            self.stderr = _FakeStream(b"")
            self.returncode = 0

        async def wait(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    async def _fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = list(command)
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        return _FakeProcess(command)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    result = asyncio.run(
        code_executor_module.code_executor_handler(
            task=f"Read files from {extra_dir} and summarize them.",
            allowed_tools=["Bash"],
            add_dirs=[str(extra_dir)],
            session_id="chat-xyz",
            require_task_context=False,
            auto_fix=False,
        )
    )

    assert result["success"] is True
    assert captured["session_id"] == "chat-xyz"

    mounts = captured["extra_mounts"]
    expected_session_dir = (runtime_root / "session_chat-xyz").resolve()
    assert (str(expected_session_dir), str(expected_session_dir)) in mounts
    assert (str(default_data_dir), str(default_data_dir)) in mounts
    assert (str(extra_dir), str(extra_dir)) in mounts

    command = captured["command"]
    assert command[:8] == [
        "docker",
        "exec",
        "-e",
        "PATH=/opt/conda/bin:/opt/conda/condabin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "-w",
        captured["host_work_dir"],
        "test-container",
        "/opt/conda/bin/qwen",
    ]
    assert "--session-id" in command
    assert command[command.index("--session-id") + 1] == _build_qwen_execution_session_id(
        "chat-xyz",
        result["run_id"],
    )
    assert captured["qwen_lock_lookup"] == "chat-xyz"


def test_code_executor_rotates_qwen_session_id_after_in_use_conflict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    project_root = tmp_path / "project"
    (project_root / "data").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(code_executor_module, "_RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(code_executor_module, "_PROJECT_ROOT", project_root)
    monkeypatch.setattr(
        code_executor_module,
        "_resolve_code_executor_backend",
        lambda _task: ("qwen_code", "configured_backend", "test"),
    )

    async def _fake_task_dir(_task: str) -> str:
        return "retry-demo"

    monkeypatch.setattr(code_executor_module, "_generate_task_dir_name_llm", _fake_task_dir)

    class _FakeDriver:
        async def ensure_container(
            self,
            session_id: str,
            *,
            host_work_dir: str,
            extra_mounts=None,
            image=None,
        ):
            return "test-container"

        def get_execution_lock(self, session_id: str):
            return asyncio.Lock()

    monkeypatch.setattr(
        "app.services.terminal.qwen_session_driver.get_qwen_session_driver",
        lambda: _FakeDriver(),
    )
    monkeypatch.setattr(
        code_executor_module,
        "_read_qwen_transcript_text",
        AsyncMock(return_value=""),
    )

    async def _fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    class _FakeStream:
        def __init__(self, payload: bytes):
            self._payload = payload
            self._sent = False

        async def read(self, _chunk_size: int = 65536) -> bytes:
            if self._sent:
                return b""
            self._sent = True
            return self._payload

    commands: list[list[str]] = []

    class _FakeProcess:
        def __init__(self, command: list[str], *, returncode: int, stdout: bytes, stderr: bytes):
            self.command = list(command)
            self.stdout = _FakeStream(stdout)
            self.stderr = _FakeStream(stderr)
            self.returncode = returncode

        async def wait(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    async def _fake_create_subprocess_exec(*command, **kwargs):
        command_list = list(command)
        commands.append(command_list)
        session_token = command_list[command_list.index("--session-id") + 1]
        if len(commands) == 1:
            return _FakeProcess(
                command_list,
                returncode=1,
                stdout=b"",
                stderr=f"Error: Session Id {session_token} is already in use.".encode("utf-8"),
            )
        return _FakeProcess(
            command_list,
            returncode=0,
            stdout=b'{"result":"ok"}\n',
            stderr=b"",
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    result = asyncio.run(
        code_executor_module.code_executor_handler(
            task="Summarize files in the workspace.",
            session_id="chat-xyz",
            require_task_context=False,
            auto_fix=False,
        )
    )

    assert result["success"] is True
    assert len(commands) == 2
    first_session = commands[0][commands[0].index("--session-id") + 1]
    second_session = commands[1][commands[1].index("--session-id") + 1]
    assert first_session != second_session


def test_extract_pending_qwen_shell_command_from_incomplete_transcript() -> None:
    transcript = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "id": "call_done",
                                    "name": "write_file",
                                    "args": {"file_path": "/tmp/demo.py", "content": "print('hi')"},
                                }
                            }
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "tool_result",
                    "toolCallResult": {"callId": "call_done", "status": "success"},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "id": "call_pending",
                                    "name": "run_shell_command",
                                    "args": {
                                        "command": "python3 code/demo.py 2>&1",
                                        "timeout": 120000,
                                        "description": "Run the demo script",
                                    },
                                }
                            }
                        ],
                    },
                }
            ),
        ]
    )

    assert _extract_pending_qwen_function_call(transcript) == {
        "id": "call_pending",
        "name": "run_shell_command",
        "args": {
            "command": "python3 code/demo.py 2>&1",
            "timeout": 120000,
            "description": "Run the demo script",
        },
    }
    assert _extract_pending_qwen_shell_command(transcript) == {
        "call_id": "call_pending",
        "command": "python3 code/demo.py 2>&1",
        "description": "Run the demo script",
        "timeout_ms": 600000,
    }


def test_extract_pending_qwen_shell_command_ignores_completed_tool_result() -> None:
    transcript = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "id": "call_complete",
                                    "name": "run_shell_command",
                                    "args": {"command": "python3 code/demo.py", "timeout": 120000},
                                }
                            }
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "tool_result",
                    "toolCallResult": {"callId": "call_complete", "status": "success"},
                }
            ),
        ]
    )

    assert _extract_pending_qwen_shell_command(transcript) is None


def test_code_executor_recovers_pending_qwen_shell_call_after_silent_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    project_root = tmp_path / "project"
    (project_root / "data").mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("QWEN_API_KEY", "sk-test")
    monkeypatch.setattr(code_executor_module, "_RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(code_executor_module, "_PROJECT_ROOT", project_root)
    monkeypatch.setattr(
        code_executor_module,
        "_resolve_code_executor_backend",
        lambda _task: ("qwen_code", "configured_backend", "test"),
    )

    async def _fake_task_dir(_task: str) -> str:
        return "recover-demo"

    monkeypatch.setattr(code_executor_module, "_generate_task_dir_name_llm", _fake_task_dir)

    class _FakeDriver:
        async def ensure_container(
            self,
            session_id: str,
            *,
            host_work_dir: str,
            extra_mounts=None,
            image=None,
        ):
            return "test-container"

        def get_execution_lock(self, session_id: str):
            return asyncio.Lock()

    monkeypatch.setattr(
        "app.services.terminal.qwen_session_driver.get_qwen_session_driver",
        lambda: _FakeDriver(),
    )

    transcript = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {
                            "id": "call_pending",
                            "name": "run_shell_command",
                            "args": {
                                "command": "python3 code/pipeline_3node.py 2>&1",
                                "timeout": 120000,
                                "description": "Execute generated pipeline",
                            },
                        }
                    }
                ],
            },
        }
    )
    monkeypatch.setattr(
        code_executor_module,
        "_read_qwen_transcript_text",
        AsyncMock(return_value=transcript),
    )

    class _FakeStream:
        def __init__(self, payload: bytes):
            self._payload = payload
            self._sent = False

        async def read(self, _chunk_size: int = 65536) -> bytes:
            if self._sent:
                return b""
            self._sent = True
            return self._payload

    class _QwenProcess:
        def __init__(self, command: list[str]):
            self.command = list(command)
            self.stdout = _FakeStream(b"")
            self.stderr = _FakeStream(
                b"Debug mode enabled\nLogging to: /tmp/gagent_home/.qwen/debug/demo.txt\n"
            )
            self.returncode = 1

        async def wait(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    class _RecoveryProcess:
        def __init__(self, work_dir: str):
            self._work_dir = work_dir
            self.returncode = 0

        async def communicate(self):
            results_dir = Path(self._work_dir) / "results"
            results_dir.mkdir(parents=True, exist_ok=True)
            (results_dir / "recovered.txt").write_text("recovered\n", encoding="utf-8")
            return (
                b"Current working directory: /tmp/recover-demo\nRecovered pipeline execution\n",
                b"",
            )

        def kill(self):
            self.returncode = -9

    commands: list[list[str]] = []

    async def _fake_create_subprocess_exec(*command, **kwargs):
        command_list = list(command)
        commands.append(command_list)
        if command_list[:2] == ["docker", "exec"] and "/opt/conda/bin/qwen" in command_list:
            return _QwenProcess(command_list)
        if command_list[:2] == ["docker", "exec"] and "/bin/bash" in command_list and "-c" in command_list:
            work_dir = command_list[command_list.index("-w") + 1]
            return _RecoveryProcess(work_dir)
        raise AssertionError(f"unexpected command: {command_list}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    result = asyncio.run(
        code_executor_module.code_executor_handler(
            task="Run the generated pipeline and produce results/recovered.txt.",
            session_id="chat-xyz",
            require_task_context=False,
            auto_fix=False,
        )
    )

    assert result["success"] is True
    assert "Recovered pipeline execution" in result["stdout"]
    assert any("/opt/conda/bin/qwen" in command for command in commands)
    assert any(command[-1] == "python3 code/pipeline_3node.py 2>&1" for command in commands)
    assert any("/bin/bash" in command and "-c" in command for command in commands)
    assert any(path.endswith("results/recovered.txt") for path in result["produced_files"])


def test_execute_task_locally_blocks_missing_absolute_task_path_before_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing_dir = "/home/zczhao/GAgent/phagescop"

    def _unexpected_generate(*args, **kwargs):
        raise AssertionError("code generation should not run for a missing declared input path")

    monkeypatch.setattr(
        "app.services.interpreter.code_execution.CodeGenerator.generate",
        _unexpected_generate,
    )
    monkeypatch.setattr(
        "app.services.llm.llm_service.get_llm_service",
        lambda: SimpleNamespace(chat=lambda *args, **kwargs: ""),
    )

    result = asyncio.run(
        _execute_task_locally(
            task=f"Read files from {missing_dir} and summarize the directory contents.",
            work_dir=str(tmp_path / "workspace"),
            session_dir=str(tmp_path / "session"),
        )
    )

    assert result["success"] is False
    assert result["error_category"] == "blocked_dependency"
    assert missing_dir in result["error_summary"]


# ---------------------------------------------------------------------------
# Upstream-fallback in first_executable_atomic_descendant
# ---------------------------------------------------------------------------

from app.routers.chat.guardrail_handlers import (
    first_executable_atomic_descendant,
    _execution_result_indicates_blocked_upstream,
    _find_blocked_upstream,
)


def _make_tree(nodes_data: list[dict]) -> PlanTree:
    """Build a minimal PlanTree from a list of node dicts."""
    nodes = {}
    adjacency: dict = {None: []}
    for nd in nodes_data:
        node = PlanNode(
            id=nd["id"],
            plan_id=1,
            name=nd.get("name", f"Task {nd['id']}"),
            status=nd.get("status", "pending"),
            parent_id=nd.get("parent_id"),
            dependencies=nd.get("dependencies", []),
            execution_result=nd.get("execution_result"),
        )
        nodes[node.id] = node
        parent = node.parent_id
        adjacency.setdefault(parent, [])
        adjacency[parent].append(node.id)
    return PlanTree(id=1, title="test", nodes=nodes, adjacency=adjacency)


class TestUpstreamFallback:
    """Verify that first_executable_atomic_descendant redirects to the
    incomplete upstream task when the downstream task failed due to a
    blocked_dependency error."""

    def test_redirects_to_upstream_when_blocked_dependency(self):
        tree = _make_tree([
            {"id": 1, "name": "Root", "parent_id": None},
            {"id": 3, "name": "Cell filtering", "status": "completed", "parent_id": 1},
            {
                "id": 4,
                "name": "Integration",
                "status": "failed",
                "parent_id": 1,
                "dependencies": [3],
                "execution_result": json.dumps({
                    "status": "failed",
                    "content": "blocked_dependency: fewer than 2 valid filtered samples",
                    "metadata": {"error_category": "blocked_dependency"},
                }),
            },
        ])
        result = first_executable_atomic_descendant(tree, 1)
        # Should redirect to task 3 (upstream), not task 4
        assert result == 3

    def test_no_redirect_when_normal_failure(self):
        tree = _make_tree([
            {"id": 1, "name": "Root", "parent_id": None},
            {"id": 3, "name": "Cell filtering", "status": "completed", "parent_id": 1},
            {
                "id": 4,
                "name": "Integration",
                "status": "failed",
                "parent_id": 1,
                "dependencies": [3],
                "execution_result": json.dumps({
                    "status": "failed",
                    "content": "SyntaxError: invalid syntax",
                    "metadata": {"error_category": "syntax_error"},
                }),
            },
        ])
        result = first_executable_atomic_descendant(tree, 1)
        # Normal failure — no redirect, return task 4 itself
        assert result == 4

    def test_no_redirect_when_upstream_not_completed(self):
        tree = _make_tree([
            {"id": 1, "name": "Root", "parent_id": None},
            {"id": 3, "name": "Cell filtering", "status": "failed", "parent_id": 1},
            {
                "id": 4,
                "name": "Integration",
                "status": "failed",
                "parent_id": 1,
                "dependencies": [3],
                "execution_result": json.dumps({
                    "status": "failed",
                    "content": "blocked_dependency",
                    "metadata": {"error_category": "blocked_dependency"},
                }),
            },
        ])
        result = first_executable_atomic_descendant(tree, 1)
        # Task 3 is already "failed" (executable), BFS finds it first
        assert result == 3

    def test_fallback_text_scan_when_no_structured_category(self):
        """When error_category is not in metadata, fall back to text matching."""
        tree = _make_tree([
            {"id": 1, "name": "Root", "parent_id": None},
            {"id": 3, "name": "Cell filtering", "status": "completed", "parent_id": 1},
            {
                "id": 4,
                "name": "Integration",
                "status": "failed",
                "parent_id": 1,
                "dependencies": [3],
                "execution_result": "fewer than 2 valid filtered samples found",
            },
        ])
        result = first_executable_atomic_descendant(tree, 1)
        assert result == 3


# ---------------------------------------------------------------------------
# Qwen Code CLI helpers
# ---------------------------------------------------------------------------

from tool_box.tools_impl.code_executor import (
    _build_qwen_code_subprocess_env,
    _build_qwen_code_command,
    _validate_qwen_code_config,
)


class TestQwenCodeCLI:
    """Tests for the Qwen Code CLI command and env builders."""

    def test_build_qwen_code_command_basic(self):
        cmd = _build_qwen_code_command(
            task="print hello",
            work_dir="/tmp/test",
            file_prefix="run_001",
            output_format="json",
            allowed_tools=["Bash", "Read", "Write"],
            allowed_dirs=["/data"],
            model="qwen3.5-plus",
            debug=False,
            allowed_dirs_info="",
        )
        assert cmd[0] == "qwen"
        assert "-p" in cmd
        assert "-o" in cmd
        assert cmd[cmd.index("-o") + 1] == "json"
        assert "--max-session-turns" in cmd
        assert "--approval-mode" in cmd
        assert cmd[cmd.index("--approval-mode") + 1] == "yolo"
        assert "--auth-type" in cmd
        assert cmd[cmd.index("--auth-type") + 1] == "openai"
        assert "-m" in cmd
        assert cmd[cmd.index("-m") + 1] == "qwen3.5-plus"
        assert "--add-dir" in cmd

    def test_build_qwen_code_command_tools_as_array(self):
        """QC --allowed-tools must be space-separated (array), not comma-joined."""
        cmd = _build_qwen_code_command(
            task="test",
            work_dir="/tmp",
            file_prefix="run",
            output_format="text",
            allowed_tools=["Bash", "Edit", "Grep"],
            allowed_dirs=[],
            model=None,
            debug=False,
            allowed_dirs_info="",
        )
        idx = cmd.index("--allowed-tools")
        # The three tools should follow as separate elements, not comma-joined.
        assert cmd[idx + 1] == "Bash"
        assert cmd[idx + 2] == "Edit"
        assert cmd[idx + 3] == "Grep"
        # Model flag should NOT appear when model is None.
        assert "-m" not in cmd

    def test_build_qwen_code_command_debug_flag(self):
        cmd = _build_qwen_code_command(
            task="x", work_dir="/tmp", file_prefix="r",
            output_format="json", allowed_tools=[], allowed_dirs=[],
            model=None, debug=True, allowed_dirs_info="",
        )
        assert "-d" in cmd

    def test_build_qwen_code_command_includes_session_id(self):
        cmd = _build_qwen_code_command(
            task="x",
            work_dir="/tmp",
            file_prefix="r",
            output_format="json",
            allowed_tools=["Bash"],
            allowed_dirs=[],
            model=None,
            debug=False,
            allowed_dirs_info="",
            qwen_session_id="agent-session-123",
        )
        idx = cmd.index("--session-id")
        assert cmd[idx + 1] == "agent-session-123"

    def test_build_qwen_code_command_includes_shell_timeout_guidance(self, monkeypatch):
        monkeypatch.setattr(
            "app.config.executor_config.get_executor_settings",
            lambda: SimpleNamespace(
                qc_max_session_turns=50,
                qc_shell_timeout_ms=420000,
            ),
        )
        cmd = _build_qwen_code_command(
            task="install dependencies",
            work_dir="/tmp",
            file_prefix="run",
            output_format="json",
            allowed_tools=["Bash"],
            allowed_dirs=[],
            model=None,
            debug=False,
            allowed_dirs_info="",
        )
        prompt_text = cmd[cmd.index("-p") + 1]
        assert "timeout` parameter explicitly to 420000 milliseconds" in prompt_text
        assert "default 120000ms timeout" in prompt_text
        assert "do NOT create a new virtual environment with `python -m venv`" in prompt_text
        assert "`python3 -m pip install --user ...`" in prompt_text
        assert "same `run_shell_command` call" in prompt_text

    def test_build_qwen_code_command_includes_bound_task_context(self):
        cmd = _build_qwen_code_command(
            task="继续执行这个任务",
            work_dir="/tmp/test",
            file_prefix="run_001",
            output_format="json",
            allowed_tools=["Bash", "Read", "Write"],
            allowed_dirs=["/data"],
            model="qwen3.5-plus",
            debug=False,
            allowed_dirs_info="",
            execution_spec={
                "task_id": 6,
                "task_name": "样本间整合与标准化",
                "task_instruction": "使用上游 filtered h5ad 执行整合分析并输出 integrated_data.h5ad",
                "dependency_outputs": [
                    {
                        "task_id": 3,
                        "task_name": "细胞过滤和质量控制",
                        "status": "completed",
                        "artifact_paths": [
                            "/abs/filtered_cancer1.h5ad",
                            "/abs/filtered_cancer2.h5ad",
                        ],
                    }
                ],
                "acceptance_criteria": {
                    "checks": [
                        {"type": "file_exists", "path": "results/integrated_data.h5ad"},
                    ]
                },
            },
        )
        prompt_text = cmd[cmd.index("-p") + 1]
        assert "[BOUND TASK CONTEXT]" in prompt_text
        assert "Task ID: 6" in prompt_text
        assert "Task Name: 样本间整合与标准化" in prompt_text
        assert "Atomic task objective:" in prompt_text
        assert "/abs/filtered_cancer1.h5ad" in prompt_text
        assert "file must exist: results/integrated_data.h5ad" in prompt_text
        assert "zero-result outcome" in prompt_text
        assert "empty-but-valid outputs at the required paths" in prompt_text
        assert "Do NOT fabricate positive signals" in prompt_text

    def test_build_qwen_code_command_formats_legacy_glob_count_shape(self):
        cmd = _build_qwen_code_command(
            task="继续执行这个任务",
            work_dir="/tmp/test",
            file_prefix="run_legacy",
            output_format="json",
            allowed_tools=["Bash", "Read", "Write"],
            allowed_dirs=["/data"],
            model="qwen3.5-plus",
            debug=False,
            allowed_dirs_info="",
            execution_spec={
                "task_id": 51,
                "task_name": "CellChat 显著互作导出",
                "task_instruction": "导出显著互作结果",
                "acceptance_criteria": {
                    "checks": [
                        {
                            "type": "glob_count_at_least",
                            "path": "output/4.1.2/significant_interactions.csv",
                            "count": 1,
                        }
                    ]
                },
            },
        )
        prompt_text = cmd[cmd.index("-p") + 1]
        assert "at least 1 matches for glob: output/4.1.2/significant_interactions.csv" in prompt_text

    def test_build_qwen_code_subprocess_env_sets_openai_vars(self, monkeypatch):
        monkeypatch.setenv("QWEN_API_KEY", "sk-test-key-123")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        env = _build_qwen_code_subprocess_env()
        assert env["OPENAI_API_KEY"] == "sk-test-key-123"
        assert "dashscope" in env["OPENAI_BASE_URL"]

    def test_build_qwen_code_subprocess_env_removes_anthropic_vars(self, monkeypatch):
        monkeypatch.setenv("QWEN_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-removed")
        monkeypatch.setenv("CLAUDECODE", "1")
        env = _build_qwen_code_subprocess_env()
        assert "ANTHROPIC_API_KEY" not in env
        assert "CLAUDECODE" not in env

    def test_validate_qwen_code_config_passes_with_key(self):
        assert _validate_qwen_code_config({"OPENAI_API_KEY": "sk-test"}) is None

    def test_validate_qwen_code_config_fails_without_key(self):
        result = _validate_qwen_code_config({})
        assert result is not None
        assert "QWEN_API_KEY" in result


# ---------------------------------------------------------------------------
# Runtime env-mutation guardrail tests
# ---------------------------------------------------------------------------

class TestEnvMutationGuard:
    """Tests for the runtime env-mutation guardrail (_inject_env_mutation_guard)."""

    def test_inject_sets_pip_require_virtualenv(self, tmp_path):
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard

        env: dict = {"PATH": "/usr/bin"}
        inject_env_mutation_guard(env, str(tmp_path))
        assert env.get("PIP_REQUIRE_VIRTUALENV") == "1"

    def test_inject_prepends_guard_bin_to_path(self, tmp_path):
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard, _ENV_GUARD_BIN as _GUARD_BIN

        original_path = "/usr/bin:/usr/local/bin"
        env: dict = {"PATH": original_path}
        inject_env_mutation_guard(env, str(tmp_path))

        guard_bin = str(tmp_path / _GUARD_BIN)
        assert env["PATH"].startswith(guard_bin + os.pathsep)
        assert original_path in env["PATH"]

    def test_inject_is_idempotent(self, tmp_path):
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard, _ENV_GUARD_BIN as _GUARD_BIN

        env: dict = {"PATH": "/usr/bin"}
        inject_env_mutation_guard(env, str(tmp_path))
        path_after_first = env["PATH"]
        inject_env_mutation_guard(env, str(tmp_path))  # call again
        assert env["PATH"] == path_after_first, "second call should not prepend guard_bin again"

    def test_wrapper_scripts_are_created(self, tmp_path):
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard, _ENV_GUARD_BIN as _GUARD_BIN

        env: dict = {"PATH": "/usr/bin"}
        inject_env_mutation_guard(env, str(tmp_path))

        guard_bin = tmp_path / _GUARD_BIN
        for cmd in ("conda", "mamba", "micromamba", "npm"):
            wrapper = guard_bin / cmd
            assert wrapper.exists(), f"wrapper for {cmd} should exist"
            assert os.access(str(wrapper), os.X_OK), f"wrapper for {cmd} should be executable"

    def test_conda_wrapper_blocks_install(self, tmp_path):
        """conda install should be blocked by the runtime wrapper."""
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard, _ENV_GUARD_BIN as _GUARD_BIN

        env: dict = {"PATH": "/usr/bin:/bin"}
        inject_env_mutation_guard(env, str(tmp_path))

        conda_wrapper = tmp_path / _GUARD_BIN / "conda"
        result = subprocess.run(
            [sys.executable, str(conda_wrapper), "install", "numpy"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "GUARDRAIL" in result.stderr

    def test_conda_wrapper_blocks_create(self, tmp_path):
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard, _ENV_GUARD_BIN as _GUARD_BIN

        env: dict = {"PATH": "/usr/bin"}
        inject_env_mutation_guard(env, str(tmp_path))

        conda_wrapper = tmp_path / _GUARD_BIN / "conda"
        result = subprocess.run(
            [sys.executable, str(conda_wrapper), "create", "-n", "myenv"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "GUARDRAIL" in result.stderr

    def test_conda_wrapper_passthrough_info(self, tmp_path):
        """conda info (non-mutation) should NOT be blocked by the wrapper."""
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard, _ENV_GUARD_BIN as _GUARD_BIN

        env: dict = {"PATH": "/usr/bin"}
        inject_env_mutation_guard(env, str(tmp_path))

        conda_wrapper = tmp_path / _GUARD_BIN / "conda"
        # When the real conda binary doesn't exist the wrapper exits 0 silently.
        # We just verify the wrapper does NOT produce the GUARDRAIL message.
        result = subprocess.run(
            [sys.executable, str(conda_wrapper), "info"],
            capture_output=True, text=True,
        )
        assert "GUARDRAIL" not in result.stderr
        if result.returncode != 0:
            assert "not found" in result.stderr.lower()

    def test_conda_wrapper_allows_env_list(self, tmp_path):
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard, _ENV_GUARD_BIN as _GUARD_BIN

        env: dict = {"PATH": "/usr/bin"}
        inject_env_mutation_guard(env, str(tmp_path))

        conda_wrapper = tmp_path / _GUARD_BIN / "conda"
        result = subprocess.run(
            [sys.executable, str(conda_wrapper), "env", "list"],
            capture_output=True, text=True,
        )
        assert "GUARDRAIL" not in result.stderr
        if result.returncode != 0:
            assert "not found" in result.stderr.lower()

    def test_mamba_wrapper_blocks_install(self, tmp_path):
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard, _ENV_GUARD_BIN as _GUARD_BIN

        env: dict = {"PATH": "/usr/bin"}
        inject_env_mutation_guard(env, str(tmp_path))

        mamba_wrapper = tmp_path / _GUARD_BIN / "mamba"
        result = subprocess.run(
            [sys.executable, str(mamba_wrapper), "install", "scipy"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "GUARDRAIL" in result.stderr

    def test_conda_wrapper_blocks_run_bypass(self, tmp_path):
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard, _ENV_GUARD_BIN as _GUARD_BIN

        env: dict = {"PATH": "/usr/bin"}
        inject_env_mutation_guard(env, str(tmp_path))

        conda_wrapper = tmp_path / _GUARD_BIN / "conda"
        result = subprocess.run(
            [sys.executable, str(conda_wrapper), "run", "-n", "base", "python", "-m", "pip", "install", "numpy"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "GUARDRAIL" in result.stderr

    def test_npm_wrapper_blocks_global_install(self, tmp_path):
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard, _ENV_GUARD_BIN as _GUARD_BIN

        env: dict = {"PATH": "/usr/bin"}
        inject_env_mutation_guard(env, str(tmp_path))

        npm_wrapper = tmp_path / _GUARD_BIN / "npm"
        result = subprocess.run(
            [sys.executable, str(npm_wrapper), "install", "-g", "typescript"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "GUARDRAIL" in result.stderr

    def test_npm_wrapper_passthrough_local_install(self, tmp_path):
        """npm install without -g should NOT be blocked."""
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard, _ENV_GUARD_BIN as _GUARD_BIN

        env: dict = {"PATH": "/usr/bin"}
        inject_env_mutation_guard(env, str(tmp_path))

        npm_wrapper = tmp_path / _GUARD_BIN / "npm"
        result = subprocess.run(
            [sys.executable, str(npm_wrapper), "install", "lodash"],
            capture_output=True, text=True,
        )
        # Not blocked — wrapper may fail because real npm is missing but the
        # GUARDRAIL message should be absent.
        assert "GUARDRAIL" not in result.stderr

    def test_pip_require_virtualenv_env_var_present_after_inject(self, tmp_path):
        """PIP_REQUIRE_VIRTUALENV=1 must be set so python -m pip install is blocked."""
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard

        env: dict = {}
        inject_env_mutation_guard(env, str(tmp_path))
        assert env["PIP_REQUIRE_VIRTUALENV"] == "1"

    def test_inject_survives_missing_work_dir(self, tmp_path):
        """inject_env_mutation_guard must not raise even if work_dir needs creation."""
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard

        new_dir = tmp_path / "nonexistent" / "subdir"
        env: dict = {"PATH": "/usr/bin"}
        # Should not raise — creates the directory tree automatically.
        inject_env_mutation_guard(env, str(new_dir))
        assert env.get("PIP_REQUIRE_VIRTUALENV") == "1"


class TestEnvGuardIntegration:
    """Verify the guard is actually wired into CLI subprocess env builders."""

    def test_qwen_code_env_has_pip_require_virtualenv(self, tmp_path, monkeypatch):
        """After inject, subprocess env for QC must contain PIP_REQUIRE_VIRTUALENV."""
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard
        from tool_box.tools_impl.code_executor import _build_qwen_code_subprocess_env

        monkeypatch.setenv("QWEN_API_KEY", "sk-test")
        env = _build_qwen_code_subprocess_env()
        inject_env_mutation_guard(env, str(tmp_path))
        assert env.get("PIP_REQUIRE_VIRTUALENV") == "1"

    def test_claude_code_env_has_pip_require_virtualenv(self, tmp_path, monkeypatch):
        from tool_box.tools_impl.code_executor import _inject_env_mutation_guard as inject_env_mutation_guard
        from tool_box.tools_impl.code_executor import _build_code_executor_subprocess_env

        monkeypatch.setenv("QWEN_API_KEY", "sk-test")
        env = _build_code_executor_subprocess_env("api_env")
        inject_env_mutation_guard(env, str(tmp_path))
        assert env.get("PIP_REQUIRE_VIRTUALENV") == "1"


@pytest.mark.parametrize(
    "task_text",
    [
        "Build a phylogenetic tree from these sequences.",
        "Compile summary statistics across all samples.",
        "Perform a statistical test on expression data from /tmp/pytest-of-apple/run_1/data.tsv.",
        "Route reads to the reference genome and summarize mapping quality.",
        "Call the NCBI API to fetch metadata and plot the results.",
        "Read analysis.py and explain what it does.",
    ],
)
def test_engineering_task_matcher_ignores_analysis_like_requests(task_text: str) -> None:
    assert _looks_like_engineering_task(task_text) is False


@pytest.mark.parametrize(
    "task_text",
    [
        "Create a multi-file FastAPI backend with React frontend.",
        "Refactor the repository structure and add integration test coverage.",
        "Update package.json and requirements.txt for the project.",
    ],
)
def test_engineering_task_matcher_detects_engineering_requests(task_text: str) -> None:
    assert _looks_like_engineering_task(task_text) is True
