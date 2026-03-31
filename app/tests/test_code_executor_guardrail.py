import asyncio
import json
import pytest  # pylint: disable=import-error  # type: ignore[import-unresolved]
from pathlib import Path

from tool_box.tools_impl import code_executor as code_executor_module
from tool_box.tools_impl.code_executor import (
    _DEFAULT_ALLOWED_TOOL_NAMES,
    _build_cli_failure_error,
    _build_code_executor_subprocess_env,
    _compact_cli_text,
    _coerce_positive_int,
    _detect_scope_blocked,
    _extract_readable_error,
    _is_path_within,
    _normalize_csv_values,
    _promote_task_results_to_session_root,
    _resolve_cli_retry_policy,
    _resolve_auth_mode,
    _resolve_allowed_tools,
    _resolve_setting_sources,
    _sanitize_task_dir_component,
    _validate_api_mode_config,
    _validate_scope_contract,
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


def test_build_cli_failure_error_includes_exit_and_stream_excerpts() -> None:
    message = _build_cli_failure_error(
        return_code=127,
        stderr="claude: command not found",
        stdout="",
    )
    assert "exit_code=127" in message
    assert "stderr=claude: command not found" in message


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


def test_resolve_allowed_tools_enforces_strict_allowlist() -> None:
    resolved = _resolve_allowed_tools("Bash,Edit,Task,WebSearch,Read")
    assert resolved == ["Bash", "Edit", "Read"]


def test_resolve_allowed_tools_uses_default_when_missing() -> None:
    assert _resolve_allowed_tools(None) == list(_DEFAULT_ALLOWED_TOOL_NAMES)


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
    assert set(rels) == {"results/line.png", "results/nested/other.png"}
    assert (session_dir / "results" / "line.png").read_bytes() == b"png1"
    assert (session_dir / "results" / "nested" / "other.png").read_bytes() == b"png2"


def test_promote_task_results_to_session_root_overwrites_stable_paths(tmp_path: Path) -> None:
    session_dir = tmp_path / "s"
    run_a = tmp_path / "s" / "t" / "run_a"
    run_b = tmp_path / "s" / "t" / "run_b"
    (run_a / "results").mkdir(parents=True)
    (run_b / "results").mkdir(parents=True)
    (run_a / "results" / "plot.png").write_bytes(b"v1")
    _promote_task_results_to_session_root(session_dir=session_dir, task_work_dir=run_a)
    (run_b / "results" / "plot.png").write_bytes(b"v2")
    _promote_task_results_to_session_root(session_dir=session_dir, task_work_dir=run_b)
    assert (session_dir / "results" / "plot.png").read_bytes() == b"v2"


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
    assert "results/plot.png" in result["artifact_paths"]
    assert any(path.endswith("plot.png") for path in result["produced_files"])
    session_dir = (tmp_path / "runtime" / "session_adhoc").resolve()
    assert (session_dir / "results" / "plot.png").read_bytes() == b"png"
