import pytest

from tool_box.tools_impl.claude_code import (
    _DEFAULT_ALLOWED_TOOL_NAMES,
    _build_claude_subprocess_env,
    _coerce_positive_int,
    _detect_scope_blocked,
    _is_path_within,
    _normalize_csv_values,
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


def test_build_claude_subprocess_env_strips_anthropic_vars_in_login_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "u")
    env_map = _build_claude_subprocess_env("claude_login")
    assert "ANTHROPIC_API_KEY" not in env_map
    assert "ANTHROPIC_BASE_URL" not in env_map


def test_build_claude_subprocess_env_uses_qwen_key_in_api_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "qk")
    env_map = _build_claude_subprocess_env("api_env")
    assert env_map.get("ANTHROPIC_API_KEY") == "qk"


def test_build_claude_subprocess_env_api_mode_uses_claude_code_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_API_KEY", "alias-key")
    monkeypatch.setenv("CLAUDE_CODE_BASE_URL", "https://alias.example/v1")
    env_map = _build_claude_subprocess_env("api_env")
    assert env_map.get("ANTHROPIC_API_KEY") == "alias-key"
    assert env_map.get("ANTHROPIC_BASE_URL") == "https://alias.example/v1"


def test_build_claude_subprocess_env_api_mode_drops_auth_token_when_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "qk")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    env_map = _build_claude_subprocess_env("api_env")
    assert env_map.get("ANTHROPIC_API_KEY") == "qk"
    assert "ANTHROPIC_AUTH_TOKEN" not in env_map


def test_validate_api_mode_config_requires_credentials() -> None:
    assert _validate_api_mode_config({"ANTHROPIC_BASE_URL": "https://x"}) is not None
    assert _validate_api_mode_config({"ANTHROPIC_API_KEY": "k"}) is None
