"""
Claude CLI Executor Tool

Integrates Anthropic's Claude Code CLI for local code execution with full file access.
Uses the official 'claude' command-line tool.
"""

import logging
import subprocess
import json
import fnmatch as _fnmatch
import hashlib
import os
import re
import shlex
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Callable, Awaitable, Sequence
import asyncio
from uuid import uuid4
from app.services.plans.artifact_validation import get_artifact_validation_prompt_specs
from app.services.plans.acceptance_criteria import (
    derive_acceptance_criteria_from_text,
    derive_expected_deliverables,
    derive_relative_output_dirs,
    resolve_glob_min_count,
    resolve_glob_pattern,
)
from app.services.plans.decomposition_jobs import get_current_job, log_job_event
from app.services.session_paths import get_runtime_root, get_runtime_session_dir
from app.services.path_router import get_path_router
from app.services.resources.resource_registry import resolve_resources as _resolve_registered_resources
from app.config.executor_config import (
    DEFAULT_CODE_EXECUTION_DOCKER_IMAGE,
    DEFAULT_CODE_EXECUTION_LOCAL_RUNTIME,
    resolve_code_execution_docker_image,
    resolve_code_execution_local_runtime,
)
from app.services.foundation.llm_config import is_production, platform_profile
from app.services.interpreter.runtime_guardrails import (
    ENV_GUARD_BIN as _ENV_GUARD_BIN,
    inject_env_mutation_guard as _inject_env_mutation_guard,
    looks_like_engineering_task as _looks_like_engineering_task,
)

# --- Extracted siblings (facade re-export; design/2026-09-24 §4.1) ---------
# Names below live in the sibling modules now; this module re-exports them so
# every existing import site (production + 53 private-name test imports) and
# every bare-name call site in this facade keep working unchanged. Siblings
# resolve cross-cluster calls back through THIS module (late binding).
from .code_executor_semantic import (
    _BLOCK_SCOPE_REASON,
    _BLOCK_SCOPE_STATUS,
    _SEMANTIC_FAILURE_DETAIL_RE,
    _SEMANTIC_FAILURE_STATUS_DETAILS,
    _SEMANTIC_FAILURE_STATUS_RE,
    _SKILL_GUIDANCE_MAX_CHARS,
    _SKILL_GUIDANCE_MAX_SKILLS,
    _apply_execution_failure_to_payload,
    _classify_execution_success,
    _clear_stale_contract_failure_state,
    _detect_execution_semantic_or_output_failure,
    _detect_missing_required_outputs,
    _detect_scope_blocked,
    _detect_semantic_execution_failure,
    _execution_failure_error_category,
    _extract_semantic_failure_from_text,
    _get_skill_guidance,
    _iter_structured_semantic_text,
    _normalize_csv_values,
    _required_output_is_present,
    _semantic_failure_error,
    _semantic_failure_kind,
)
from .code_executor_cli_parse import (
    _DEFAULT_TASK_SUBDIRECTORIES,
    _QWEN_DEBUG_ENABLED_LINE_RE,
    _QWEN_LOGGING_TO_LINE_RE,
    _build_summary_from_parsed_json,
    _compact_cli_text,
    _derive_task_subdirectories,
    _extract_deliverables_from_jsonl,
    _extract_qwen_debug_log_path,
    _extract_readable_error,
    _extract_result_from_jsonl,
    _format_directory_choices,
    _format_task_subdirectories,
    _is_qwen_truncated_tool_failure_text,
    _iter_stream_lines_unbounded,
    _partition_cli_stderr_lines,
    _qwen_truncated_tool_failure_note,
)
from .code_executor_qwen import (
    _PARTIAL_COMPLETION_PATTERNS,
    _QWEN_CLI_NO_OUTPUT_TIMEOUT_SECONDS,
    _QWEN_COMPLETED_OUTPUT_EXIT_CHECK_SECONDS,
    _QWEN_COMPLETED_OUTPUT_EXIT_GRACE_SECONDS,
    _QWEN_FATAL_DEBUG_SCAN_BYTES,
    _QWEN_PROCESS_EXIT_WAIT_SECONDS,
    _QWEN_PROCESS_KILL_WAIT_SECONDS,
    _QWEN_SHELL_FALLBACK_MAX_TIMEOUT_MS,
    _QWEN_SHELL_FALLBACK_TIMEOUT_MS,
    _QWEN_TRANSCRIPTS_ROOT,
    _WARNING_LINE_PATTERNS,
    _build_cli_failure_error,
    _build_qwen_code_command,
    _build_qwen_container_mounts,
    _detect_partial_completion_cli,
    _detect_qwen_debug_fatal_failure,
    _extract_pending_qwen_function_call,
    _extract_pending_qwen_shell_command,
    _find_unique_run_prefixed_contract_source,
    _materialize_contract_outputs_for_standard_paths,
    _materialize_contract_view_for_flat_outputs,
    _qwen_outputs_pass_contract_for_early_exit,
    _qwen_single_work_dir_passes_contract_for_early_exit,
    _read_qwen_debug_log_text,
    _read_qwen_transcript_text,
    _recover_pending_qwen_shell_call,
    _resolve_qwen_cli_no_output_timeout_seconds,
    _resolve_qwen_completed_output_exit_check_seconds,
    _resolve_qwen_completed_output_exit_grace_seconds,
    _resolve_qwen_process_exit_wait_seconds,
    _resolve_qwen_process_kill_wait_seconds,
    _run_subprocess_capture,
    _verify_contract_for_qwen_early_exit,
    _wait_for_cli_process_return_code,
    _wait_for_qwen_cli_drain_or_watchdog,
)
from .code_executor_contracts import (
    _append_contract_artifact_paths,
    _build_ad_hoc_execution_spec,
    _build_cli_contract_repair_task,
    _build_cli_task_contract,
    _build_execution_spec,
    _build_verification_artifact_paths,
    _contract_required_artifact_records,
    _extract_acceptance_criteria_from_node,
    _extract_code_workspace_metadata,
    _extract_task_referenced_read_dirs,
    _final_response_contract_prompt,
    _format_cli_acceptance_checks,
    _format_contract_diff_for_cli,
    _format_resolved_resources_for_prompt,
    _format_supervised_ml_contract_for_prompt,
    _generate_task_dir_name_llm,
    _is_allowed_task_read_path,
    _is_path_within,
    _is_path_within_lexical,
    _is_verification_only_task,
    _normalize_resolved_resources,
    _resource_read_dirs,
    _rerun_update_mode_prompt,
    _sanitize_task_dir_component,
    _summarize_dependency_blockers,
    _validate_scope_contract,
)

# Compat alias: the CLI-stdout partial-completion detector was renamed to
# _detect_partial_completion_cli in the qwen sibling (see its docstring) so it
# can never be confused with gating_probe's tool-result-layer
# _detect_partial_completion_in_tool_results. The historical facade name stays
# available for existing import sites and bare-name callers in this module.
_detect_partial_completion = _detect_partial_completion_cli

logger = logging.getLogger(__name__)

_TASK_READ_DIR_PREFIXES: Sequence[str] = (
    "app",
    "code",
    "data",
    "docker",
    "docs",
    "paper",
    "phagescope",
    "reference",
    "results",
    "runtime",
    "scripts",
    "test",
    "tests",
    "tool_box",
    "web-ui",
)
_TASK_PATH_TOKEN_RE = r"[^\s'\"`<>\(\)\[\]\{\},;:，。；：！？、（）【】《》「」『』“”‘’]+"
_DEFAULT_EXTERNAL_READ_ROOTS: Sequence[Path] = (Path("/mnt/sdm/zczhao"),)


# Project root directory
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Claude Code runtime directory
_RUNTIME_DIR = get_runtime_root()
_LOG_DIR = _RUNTIME_DIR / "code_executor_logs"

# Strict execution boundary: only a constrained subset of tools is allowed.
_HARD_ALLOWED_TOOL_NAMES: Sequence[str] = (
    "Bash",
    "BashOutput",
    "Edit",
    "MultiEdit",
    "Read",
    "Write",
    "Glob",
    "Grep",
    "LS",
    "NotebookRead",
    "NotebookEdit",
)
_DEFAULT_ALLOWED_TOOL_NAMES: Sequence[str] = (
    "Bash",
    "BashOutput",
    "Edit",
    "MultiEdit",
    "Read",
    "Write",
    "Glob",
    "Grep",
    "LS",
)
_HARD_ALLOWED_TOOL_MAP = {name.lower(): name for name in _HARD_ALLOWED_TOOL_NAMES}
_DEFAULT_SETTING_SOURCES = "project,local"
_DEFAULT_API_SETTING_SOURCES = "project"
_DEFAULT_AUTH_MODE = "api_env"
_DEFAULT_CODE_EXECUTOR_LOCAL_RUNTIME = DEFAULT_CODE_EXECUTION_LOCAL_RUNTIME
_DEFAULT_CODE_EXECUTOR_DOCKER_IMAGE = DEFAULT_CODE_EXECUTION_DOCKER_IMAGE
_SUPPORTED_SETTING_SOURCES = {"user", "project", "local"}
_SUPPORTED_AUTH_MODES = {"claude_login", "api_env"}
_DEFAULT_API_BASE_URL = "https://dashscope.aliyuncs.com/apps/anthropic"
_DEFAULT_API_MODEL = "qwen3.7-max"
_DEFAULT_QC_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_CLAUDE_ENV_KEYS_FOR_LOGIN_MODE: Sequence[str] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_AUTH_TOKEN",
)
_CLAUDE_ENV_KEYS_FOR_API_MODE: Sequence[str] = (
    *_CLAUDE_ENV_KEYS_FOR_LOGIN_MODE,
    "CLAUDE_API_KEY",
    "CLAUDE_API_URL",
    "CLAUDE_BASE_URL",
    "CLAUDE_MODEL",
)
_CLAUDE_ENV_ALIAS_FOR_API_MODE: Sequence[tuple[str, str]] = (
    ("CLAUDE_CODE_API_KEY", "ANTHROPIC_API_KEY"),
    ("CLAUDE_CODE_BASE_URL", "ANTHROPIC_BASE_URL"),
    ("CLAUDE_CODE_AUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"),
    ("CLAUDE_CODE_API_MODEL", "ANTHROPIC_MODEL"),
    ("CLAUDE_CODE_SMALL_FAST_MODEL", "ANTHROPIC_SMALL_FAST_MODEL"),
)


def _resolve_code_executor_local_runtime(value: Optional[str] = None) -> str:
    raw = value if value is not None else os.getenv("CODE_EXECUTOR_LOCAL_RUNTIME")
    return resolve_code_execution_local_runtime(
        raw,
        default=_DEFAULT_CODE_EXECUTOR_LOCAL_RUNTIME,
    )


def _resolve_code_executor_docker_image(value: Optional[str] = None) -> str:
    raw = value if value is not None else os.getenv("CODE_EXECUTOR_DOCKER_IMAGE")
    return resolve_code_execution_docker_image(
        raw,
        default=_DEFAULT_CODE_EXECUTOR_DOCKER_IMAGE,
    )


def _resolve_allowed_tools(value: Any) -> List[str]:
    source = _normalize_csv_values(value) or list(_DEFAULT_ALLOWED_TOOL_NAMES)
    resolved: List[str] = []
    seen = set()
    dropped: List[str] = []
    for token in source:
        raw = str(token).strip()
        if not raw:
            continue
        canonical = _HARD_ALLOWED_TOOL_MAP.get(raw.lower())
        if canonical is None:
            dropped.append(raw)
            continue
        key = canonical.lower()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(canonical)
    if dropped:
        logger.warning(
            "Dropped disallowed Claude Code tools from allowlist: %s",
            ", ".join(dropped),
        )
    return resolved


def _resolve_setting_sources(value: Any, *, auth_mode: Optional[str] = None) -> Optional[str]:
    raw = ""
    if value is not None:
        raw = str(value).strip()
    if not raw:
        raw = str(os.getenv("CLAUDE_CODE_SETTING_SOURCES", "")).strip()
    if not raw and str(auth_mode or "").strip().lower() == "api_env":
        raw = str(os.getenv("CLAUDE_CODE_API_SETTING_SOURCES", "")).strip()
        if not raw:
            raw = _DEFAULT_API_SETTING_SOURCES
    if not raw:
        raw = _DEFAULT_SETTING_SOURCES

    if raw.lower() in {"none", "off", "disabled", "disable"}:
        return None

    resolved: List[str] = []
    seen = set()
    for token in _normalize_csv_values(raw):
        key = token.lower()
        if key not in _SUPPORTED_SETTING_SOURCES:
            logger.warning("Ignoring unsupported Claude setting source: %s", token)
            continue
        if key in seen:
            continue
        seen.add(key)
        resolved.append(key)

    if not resolved:
        return None
    return ",".join(resolved)


def _resolve_auth_mode(value: Any) -> str:
    raw = ""
    if value is not None:
        raw = str(value).strip().lower()
    if not raw:
        raw = str(os.getenv("CLAUDE_CODE_AUTH_MODE", "")).strip().lower()
    if not raw:
        return _DEFAULT_AUTH_MODE
    if raw in {"claude", "claude_pro", "login"}:
        raw = "claude_login"
    if raw in _SUPPORTED_AUTH_MODES:
        return raw
    logger.warning(
        "Unsupported CLAUDE_CODE_AUTH_MODE '%s'; falling back to %s.",
        raw,
        _DEFAULT_AUTH_MODE,
    )
    return _DEFAULT_AUTH_MODE


def _ensure_writable_subprocess_cache_env(env_map: Dict[str, str], task_work_dir: Path) -> None:
    cache_root = task_work_dir / ".cache"
    for name, path in (
        ("MPLCONFIGDIR", cache_root / "matplotlib"),
        ("XDG_CACHE_HOME", cache_root / "xdg"),
        ("NUMBA_CACHE_DIR", cache_root / "numba"),
    ):
        value = str(env_map.get(name) or "").strip()
        if value and os.access(os.path.expanduser(value), os.W_OK):
            continue
        path.mkdir(parents=True, exist_ok=True)
        env_map[name] = str(path)


def _build_code_executor_subprocess_env(auth_mode: str) -> Dict[str, str]:
    env_map = dict(os.environ)

    # Always remove CLAUDECODE to prevent "nested session" detection when the
    # backend itself runs inside a Claude Code session (e.g. during testing).
    env_map.pop("CLAUDECODE", None)

    if auth_mode == "claude_login":
        for key in _CLAUDE_ENV_KEYS_FOR_LOGIN_MODE:
            env_map.pop(key, None)
    elif auth_mode == "api_env":
        # In API mode, build a deterministic runtime env and never inherit provider
        # or model settings from the parent shell. Claude Code itself reads some
        # CLAUDE_* variables, so scrub those too before wiring our explicit config.
        for key in _CLAUDE_ENV_KEYS_FOR_API_MODE:
            env_map.pop(key, None)
        for source_key, target_key in _CLAUDE_ENV_ALIAS_FOR_API_MODE:
            value = str(os.getenv(source_key, "")).strip()
            if value:
                env_map[target_key] = value

        if not env_map.get("ANTHROPIC_API_KEY"):
            qwen_api_key = str(os.getenv("QWEN_API_KEY", "")).strip()
            if qwen_api_key:
                env_map["ANTHROPIC_API_KEY"] = qwen_api_key
        if not env_map.get("ANTHROPIC_BASE_URL"):
            env_map["ANTHROPIC_BASE_URL"] = (
                str(os.getenv("CLAUDE_CODE_API_BASE_URL", "")).strip()
                or _DEFAULT_API_BASE_URL
            )
        if not env_map.get("ANTHROPIC_MODEL"):
            env_map["ANTHROPIC_MODEL"] = (
                str(os.getenv("QWEN_MODEL", "")).strip()
                or _DEFAULT_API_MODEL
            )
        if not env_map.get("ANTHROPIC_SMALL_FAST_MODEL"):
            env_map["ANTHROPIC_SMALL_FAST_MODEL"] = (
                env_map.get("ANTHROPIC_MODEL", "").strip()
                or str(os.getenv("QWEN_MODEL", "")).strip()
                or _DEFAULT_API_MODEL
            )

        # Avoid auth conflict in API mode: prefer API key when both exist; only keep
        # ANTHROPIC_AUTH_TOKEN when explicitly provided via CLAUDE_CODE_AUTH_TOKEN.
        if env_map.get("ANTHROPIC_API_KEY"):
            env_map.pop("ANTHROPIC_AUTH_TOKEN", None)
        else:
            explicit_auth_token = str(os.getenv("CLAUDE_CODE_AUTH_TOKEN", "")).strip()
            if explicit_auth_token:
                env_map["ANTHROPIC_AUTH_TOKEN"] = explicit_auth_token
            else:
                env_map.pop("ANTHROPIC_AUTH_TOKEN", None)

    return env_map


def _validate_api_mode_config(env_map: Dict[str, str]) -> Optional[str]:
    api_key = str(env_map.get("ANTHROPIC_API_KEY", "")).strip()
    auth_token = str(env_map.get("ANTHROPIC_AUTH_TOKEN", "")).strip()
    if api_key or auth_token:
        return None
    return (
        "Claude Code API mode requires credentials. "
        "Set CLAUDE_CODE_API_KEY or QWEN_API_KEY."
    )


# ---------------------------------------------------------------------------
# Qwen Code CLI helpers
# ---------------------------------------------------------------------------

def _build_qwen_code_subprocess_env(model_provider: Optional[Dict] = None) -> Dict[str, str]:
    """Build a subprocess environment for Qwen Code CLI.

    Uses OpenAI-compatible auth pointing at dashscope.
    """
    env_map = dict(os.environ)

    # pi shim: when the shim dir exists, `qwen` resolves to the pi translator
    # (data/tools/pi-shim/qwen); PI_SHIM_DISABLED=1 inside the shim restores
    # the real qwen binary. Proxy vars are stripped — the stale localhost
    # proxy breaks pi's API connection and nothing else uses them.
    pi_shim_dir = os.getenv("PI_SHIM_DIR", "/app/data/tools/pi-shim")
    if os.path.isdir(pi_shim_dir):
        env_map["PATH"] = pi_shim_dir + os.pathsep + env_map.get("PATH", "")
    for _proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                       "ALL_PROXY", "all_proxy"):
        env_map.pop(_proxy_key, None)

    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    if conda_prefix:
        conda_bin = os.path.join(conda_prefix, "bin")
        current_path = env_map.get("PATH", "")
        if not current_path.startswith(conda_bin):
            env_map["PATH"] = conda_bin + os.pathsep + current_path

    if is_production():
        profile = platform_profile()
        api_key = profile.api_key
        base_url = profile.api_url.rsplit("/chat/completions", 1)[0]
        try:
            from app.llm import get_project_llm_credentials
            creds = get_project_llm_credentials()
        except Exception:
            creds = None
        if creds:
            api_key = str(creds["api_key"])
            base_url = str(creds["chat_url"]).rsplit("/chat/completions", 1)[0]
        env_map["OPENAI_API_KEY"] = api_key
        env_map["OPENAI_BASE_URL"] = base_url
        env_map["QWEN_CODE_MODEL"] = profile.model
    else:
        mp = model_provider or {}
        mp_api_key = mp.get("api_key")
        mp_base_url = mp.get("base_url")
        if mp_api_key and mp_base_url:
            env_map["OPENAI_API_KEY"] = mp_api_key
            env_map["OPENAI_BASE_URL"] = mp_base_url.rstrip("/") + "/v1"
        else:
            qwen_key = str(os.getenv("QWEN_API_KEY", "")).strip()
            if qwen_key:
                env_map["OPENAI_API_KEY"] = qwen_key
            env_map["OPENAI_BASE_URL"] = (
                str(os.getenv("QWEN_CODE_BASE_URL", "")).strip()
                or _DEFAULT_QC_BASE_URL
            )
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
                "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE"):
        env_map.pop(key, None)
    return env_map


def _validate_qwen_code_config(env_map: Dict[str, str]) -> Optional[str]:
    """Return an error string if QC env is missing credentials."""
    api_key = str(env_map.get("OPENAI_API_KEY", "")).strip()
    if api_key:
        return None
    return (
        "Qwen Code requires credentials. "
        "Set QWEN_API_KEY in the environment."
    )


# Shared runtime guardrails are applied to both CLI and local execution paths.


def _build_qwen_execution_session_id(
    session_id: Optional[str],
    run_id: str,
    *,
    phase: str = "primary",
    retry_attempt: int = 0,
) -> str:
    from app.services.terminal.docker_pty_backend import _sanitise_qwen_session_id

    session_token = str(session_id or "adhoc").strip() or "adhoc"
    raw = f"agent:{session_token}:{run_id}:{phase}"
    if retry_attempt > 0:
        raw = f"{raw}:retry:{retry_attempt}"
    return _sanitise_qwen_session_id(raw)


def _is_qwen_session_in_use_error(stderr_text: Any) -> bool:
    text = str(stderr_text or "").strip().lower()
    return "session id" in text and "already in use" in text


def _is_qwen_no_output_timeout(stderr_text: Any, stdout_text: Any = "") -> bool:
    text = f"{stderr_text or ''}\n{stdout_text or ''}".lower()
    return (
        "qwen_cli_no_output_timeout" in text
        or "qwen cli produced no stdout/stderr" in text
    )


def _is_qwen_recoverable_cli_failure(stderr_text: Any, stdout_text: Any = "") -> bool:
    text = f"{stderr_text or ''}\n{stdout_text or ''}"
    return _is_qwen_no_output_timeout(stderr_text, stdout_text) or _is_qwen_truncated_tool_failure_text(text)


def _is_qwen_container_infrastructure_error(stderr_text: Any, stdout_text: Any = "") -> bool:
    """Return True for qwen Docker/container failures that are safe to retry elsewhere."""
    text = f"{stderr_text or ''}\n{stdout_text or ''}".lower()
    patterns = (
        "no such container",
        "container is not running",
        "cannot connect to the docker daemon",
        "error response from daemon",
        "context deadline exceeded",
        "qwen_cli_no_output_timeout",
        "qwen cli produced no stdout/stderr",
    )
    return any(pattern in text for pattern in patterns)


def _qwen_infrastructure_fallback_allowed(execution_lane: str, execution_lane_reason: str) -> bool:
    """Only auto-fallback when qwen was selected by auto-routing, not explicit config."""
    lane = str(execution_lane or "").strip().lower()
    reason = str(execution_lane_reason or "").strip().lower()
    if lane == "configured_backend" or "code_execution_backend=qwen_code" in reason:
        return False
    return True


def _should_fallback_from_qwen_infra_failure(
    *,
    use_qwen_code_backend: bool,
    success: bool,
    stderr: Any,
    stdout: Any,
    execution_lane: str,
    execution_lane_reason: str,
) -> bool:
    if not use_qwen_code_backend or success:
        return False
    if _is_qwen_recoverable_cli_failure(stderr, stdout):
        return False
    if not _is_qwen_container_infrastructure_error(stderr, stdout):
        return False
    return _qwen_infrastructure_fallback_allowed(execution_lane, execution_lane_reason)


def _qwen_code_cli_available() -> bool:
    if shutil.which("qwen") is None:
        return False
    env_map = _build_qwen_code_subprocess_env()
    return _validate_qwen_code_config(env_map) is None


def _resolve_code_executor_backend(task: str, backend_override: Optional[str] = None) -> tuple[str, str, str]:
    override = str(backend_override or "").strip().lower()
    if override in {"local", "qwen_code", "claude_code"}:
        return override, "plan_task_delegation", f"execution_backend={override}"

    backend = "auto"
    auto_strategy = "qwen_primary"
    try:
        from app.config.executor_config import get_executor_settings

        settings = get_executor_settings()
        backend = str(getattr(settings, "code_execution_backend", "auto") or "auto").strip().lower() or "auto"
        auto_strategy = (
            str(getattr(settings, "code_execution_auto_strategy", "qwen_primary") or "qwen_primary").strip().lower()
            or "qwen_primary"
        )
    except Exception:
        backend = "auto"
        auto_strategy = "qwen_primary"

    if backend in {"local", "qwen_code", "claude_code"}:
        return backend, "configured_backend", f"CODE_EXECUTION_BACKEND={backend}"

    qwen_available = _qwen_code_cli_available()
    engineering_task = _looks_like_engineering_task(task)

    if auto_strategy == "split":
        if engineering_task:
            if qwen_available:
                return "qwen_code", "engineering_primary", "engineering-style task detected"
            return "local", "local_fallback", "engineering-style task detected but qwen_code is unavailable"
        return "local", "analysis_fast_path", "analysis-style code task routed to local fast path"

    if qwen_available:
        if engineering_task:
            return "qwen_code", "qwen_primary", "engineering-style task routed to shared qwen_code session"
        return "qwen_code", "qwen_primary", "code task routed to qwen_code primary lane"

    if engineering_task:
        return "local", "local_fallback", "engineering-style task detected but qwen_code is unavailable"

    return "local", "analysis_fast_path", "qwen_code unavailable for analysis-style code task"


_FIGURE_INTENT_RE = re.compile(
    r"(饼图|柱状图|条形图|折线图|散点图|直方图|热力图|箱线图|流程图|示意图|曲线图|森林图|火山图|轨迹图|图表|画图|绘制|可视化"
    r"|plot|chart|figure|histogram|scatter|heatmap|bar\s?chart|pie\s?chart|line\s?chart|forest\s?plot|volcano|visualiz)",
    re.IGNORECASE,
)

_FIGURE_STYLE_PROMPT = (
    "Figure style (MANDATORY when the task produces any plot/chart/figure):\n"
    "- Copy this setup verbatim before plotting:\n"
    "  import matplotlib.pyplot as plt\n"
    "  PALETTE = ['#E64B35','#4DBBD5','#00A087','#3C5488','#F39B7F','#8491B4','#91D1C2']\n"
    "  plt.rcParams.update({\n"
    "      'savefig.dpi': 300, 'savefig.bbox': 'tight',\n"
    "      'font.size': 10, 'axes.titlesize': 12, 'axes.labelsize': 10.5,\n"
    "      'axes.spines.top': False, 'axes.spines.right': False,\n"
    "      'axes.grid': True, 'grid.alpha': 0.28, 'grid.linestyle': '--', 'axes.axisbelow': True,\n"
    "      'axes.prop_cycle': plt.cycler(color=PALETTE),\n"
    "      'legend.frameon': False, 'figure.facecolor': 'white',\n"
    "  })\n"
    "- Cycle PALETTE for multi-series; a single series uses '#3C5488' "
    "(never a lone bright-red bar/point cloud).\n"
    "- ALL text in English; every axis labeled with units; descriptive title; "
    "legend whenever more than one series.\n"
    "- Bars: width <= 0.7, thin or no edgecolor; prefer horizontal bars when "
    "category labels are long.\n\n"
)


def _figure_style_prompt(task: str) -> str:
    """Style rules appended to the delegation prompt for figure-producing tasks.

    The delegated CLI agent writes matplotlib code from its own defaults when
    the prompt says nothing about style — that is why figures came out with
    stock colors and cramped typography. Injecting a copy-pasteable setup
    block keeps every delegated figure on the publication palette without the
    agent needing to import anything.
    """
    if _FIGURE_INTENT_RE.search(task or ""):
        return _FIGURE_STYLE_PROMPT
    return ""


def _build_claude_code_prompt(
    *,
    task: str,
    task_work_dir: Path,
    file_prefix: str,
    task_subdirs: Sequence[str],
    execution_spec: Optional[Dict[str, Any]],
    resolved_resources: Optional[Dict[str, Dict[str, Any]]],
    allowed_dirs_info: str,
) -> str:
    writable_task_subdirs = [
        name for name in task_subdirs if str(name).strip().lower() != "code"
    ]
    cli_task = _build_cli_task_contract(task, execution_spec, resolved_resources)
    return (
        f"[ATOMIC TASK]\n"
        f"Execute only the task below. Do not broaden scope or create extra tasks.\n"
        f"If the request still needs planning or decomposition, output exactly:\n"
        f"  {_BLOCK_SCOPE_STATUS}\n"
        f"  {_BLOCK_SCOPE_REASON}\n"
        f"  DETAIL: <one sentence>\n"
        f"Use direct execution; skip standalone environment diagnostics "
        f"unless an observed failure requires them.\n\n"
        f"Workspace: {task_work_dir}\n"
        f"Output dirs: {_format_task_subdirectories(task_subdirs)}\n"
        f"File prefix: {file_prefix}\n"
        f"Task:\n{cli_task}\n\n"
        f"Deliverables:\n"
        f"1. Write scripts under code/ only when needed.\n"
        f"2. Run them and save outputs under {_format_directory_choices(writable_task_subdirs)}.\n"
        f"3. Put publishable deliverable code under results/submission/ "
        f"or results/deliverable/.\n"
        f"4. Return a summary of actual outputs produced.\n"
        f"5. Do NOT modify shared host environments: no global `conda install`, "
        f"`pip install`, or writes into shared site-packages. Use task-local "
        f"workspace environments only.\n"
        f"6. If the task needs a heavy dependency solve, compiled stack, or a "
        f"new runtime image/profile, report BLOCKED_DEPENDENCY instead of "
        f"mutating the shared host environment.\n\n"
        f"{_rerun_update_mode_prompt()}\n"
        f"{_final_response_contract_prompt()}"
        f"{allowed_dirs_info}"
        f"{_figure_style_prompt(cli_task)}"
        f"{_get_skill_guidance(cli_task)}"
    )


def _build_claude_code_command(
    *,
    task: str,
    task_work_dir: Path,
    file_prefix: str,
    output_format: str,
    normalized_allowed_tools: Sequence[str],
    allowed_dirs: Sequence[str],
    task_subdirs: Sequence[str],
    execution_spec: Optional[Dict[str, Any]],
    resolved_resources: Optional[Dict[str, Dict[str, Any]]],
    allowed_dirs_info: str,
    debug_log_path: Optional[Path],
    effective_model: Optional[str],
    effective_setting_sources: Optional[str],
    skip_permissions: bool,
) -> List[str]:
    enhanced_task = _build_claude_code_prompt(
        task=task,
        task_work_dir=task_work_dir,
        file_prefix=file_prefix,
        task_subdirs=task_subdirs,
        execution_spec=execution_spec,
        resolved_resources=resolved_resources,
        allowed_dirs_info=allowed_dirs_info,
    )
    command = [
        "claude",
        "-p",
        enhanced_task,
        "--output-format",
        output_format,
        "--max-turns",
        "50",
    ]
    if debug_log_path is not None:
        command.extend(["--debug-file", str(debug_log_path)])
    if effective_model:
        command.extend(["--model", effective_model])
    if effective_setting_sources:
        command.extend(["--setting-sources", effective_setting_sources])
    command.extend(["--allowed-tools", ",".join(normalized_allowed_tools)])
    for abs_path in allowed_dirs:
        command.extend(["--add-dir", abs_path])
    if skip_permissions:
        command.append("--dangerously-skip-permissions")
    return command


def _coerce_positive_int(value: Any, *, field_name: str) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be an integer")
    if parsed <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def _resolve_cli_retry_policy() -> tuple[int, float]:
    """Return (max_retries, base_delay_seconds) for Claude CLI transient failures."""
    raw_retries = str(os.getenv("CLAUDE_CODE_MAX_RETRIES", "")).strip()
    raw_delay = str(os.getenv("CLAUDE_CODE_RETRY_BASE_DELAY_S", "")).strip()

    max_retries = 4
    if raw_retries:
        try:
            max_retries = max(0, min(8, int(raw_retries)))
        except ValueError:
            max_retries = 4

    base_delay_s = 5.0
    if raw_delay:
        try:
            base_delay_s = max(0.5, min(60.0, float(raw_delay)))
        except ValueError:
            base_delay_s = 5.0

    return max_retries, base_delay_s


def _legacy_format_supervised_ml_contract_for_prompt(
    artifact_contract: Optional[Dict[str, Any]],
    resolved_inputs: Optional[Dict[str, str]],
) -> str:
    if not isinstance(artifact_contract, dict):
        return ""
    aliases = {
        str(item).strip()
        for item in [
            *(artifact_contract.get("requires") or []),
            *(artifact_contract.get("publishes") or []),
        ]
        if str(item).strip()
    }
    if not aliases.intersection(_SUPERVISED_ML_PROMPT_ALIASES):
        return ""
    resolved = resolved_inputs if isinstance(resolved_inputs, dict) else {}
    lines = [
        "",
        "Supervised ML contract requirements:",
        "- Use only real labels from required label/metadata artifacts. Do NOT synthesize, randomize, balance-fabricate, or dummy-generate labels.",
        "- Align labels to feature rows using the required row-id/alignment artifacts; if alignment cannot be proven, fail explicitly instead of training.",
        "- Metrics must record label_source, label_alignment, and training_samples so downstream verification can audit provenance.",
    ]
    for alias in ("phage_ml.training_metadata_parquet", "phage_ml.feature_row_ids_json"):
        if alias in aliases:
            path = str(resolved.get(alias) or "").strip()
            if path:
                lines.append(f"- Required supervised input {alias}: {path}")
            else:
                lines.append(f"- Required supervised input {alias} must resolve before valid training; report BLOCKED_DEPENDENCY if unavailable.")
    if "phage_ml.label_alignment_json" in aliases:
        lines.append("- Publish phage_ml.label_alignment_json with real label provenance and row-alignment evidence.")
    return "\n".join(lines)

def _legacy_build_cli_task_contract(
    task: str,
    execution_spec: Optional[Dict[str, Any]],
    resolved_resources: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    task_text = str(task or "").strip()
    if not isinstance(execution_spec, dict):
        return task_text

    lines: List[str] = ["[BOUND TASK CONTEXT]"]
    is_verification_only = _is_verification_only_task(task_text)
    task_id = execution_spec.get("task_id")
    task_name = str(execution_spec.get("task_name") or "").strip()
    task_instruction = str(execution_spec.get("task_instruction") or "").strip()
    dependency_outputs = execution_spec.get("dependency_outputs")

    if task_id is not None:
        lines.append(f"Task ID: {task_id}")
    if task_name:
        lines.append(f"Task Name: {task_name}")

    if task_instruction and not is_verification_only:
        lines.extend(["", "Atomic task objective:", task_instruction])

    if task_text and task_text != task_instruction:
        lines.extend(["", "Requested execution action:", task_text])
        if is_verification_only:
            lines.extend([
                "",
                "Verification-only mode:",
                "- Execute only the requested inspection of existing files/artifacts.",
                "- Do not regenerate task outputs, rerun the original task objective, or create replacement deliverables.",
                "- Report the observed file headers, row counts, columns, and any validation failures.",
            ])

    if isinstance(dependency_outputs, list) and dependency_outputs:
        lines.extend(["", "Upstream dependencies:"])
        for dep in dependency_outputs[:6]:
            if not isinstance(dep, dict):
                continue
            dep_name = str(dep.get("task_name") or dep.get("task_id") or "unknown").strip()
            dep_status = str(dep.get("status") or "unknown").strip()
            artifact_paths = dep.get("artifact_paths")
            if isinstance(artifact_paths, list) and artifact_paths:
                joined = "; ".join(
                    str(item).strip() for item in artifact_paths[:4] if str(item).strip()
                )
                if len(artifact_paths) > 4:
                    joined += "; ..."
                lines.append(f"- {dep_name} [{dep_status}] -> {joined}")
            else:
                lines.append(f"- {dep_name} [{dep_status}]")

    artifact_contract = execution_spec.get("artifact_contract")
    if isinstance(artifact_contract, dict):
        requires = [str(item).strip() for item in artifact_contract.get("requires") or [] if str(item).strip()]
        publishes = [str(item).strip() for item in artifact_contract.get("publishes") or [] if str(item).strip()]
        if requires or publishes:
            lines.extend(["", "Artifact contract aliases:"])
            if requires:
                lines.append("- requires: " + ", ".join(requires))
            if publishes:
                lines.append("- publishes: " + ", ".join(publishes))
    resolved_inputs = execution_spec.get("resolved_input_artifacts")
    if isinstance(resolved_inputs, dict) and resolved_inputs:
        lines.extend(["", "Resolved required input artifacts:"])
        for alias, path in list(resolved_inputs.items())[:12]:
            lines.append(f"- {alias}: {path}")
    dependency_paths = execution_spec.get("dependency_artifact_paths")
    if isinstance(dependency_paths, list) and dependency_paths:
        lines.extend(["", "Dependency artifact paths:"])
        for path in dependency_paths[:12]:
            text = str(path or "").strip()
            if text:
                lines.append(f"- {text}")

    formatted_checks = _format_cli_acceptance_checks(
        execution_spec.get("acceptance_criteria")
    )
    if formatted_checks:
        lines.extend(["", "Deterministic acceptance criteria:"])
        lines.extend(f"- {item}" for item in formatted_checks)
        lines.extend([
            "- The plan contract is authoritative: required deliverables must match these criteria exactly.",
            "- Extra outputs are allowed, but they do NOT substitute for missing required outputs.",
        ])

    artifact_contract = execution_spec.get("artifact_contract")
    publishes = artifact_contract.get("publishes") if isinstance(artifact_contract, dict) else None
    schema_specs = get_artifact_validation_prompt_specs(publishes or [])
    if schema_specs:
        lines.extend(["", "Expected artifact schemas:"])
        for alias, spec in sorted(schema_specs.items()):
            lines.append(f"- {alias}: {json.dumps(spec, ensure_ascii=False)}")
        lines.extend([
            "- These schemas are authoritative: artifacts must be structurally loadable, not merely present/non-empty.",
            "- For sparse_npz outputs, write a SciPy sparse matrix using scipy.sparse.save_npz and ensure shape rows/columns are non-zero.",
            "- For numpy_npy outputs, write a non-empty NumPy array with numpy.save.",
            "- For directory_glob outputs, create the directory and at least the required checkpoint/metric files inside it.",
        ])

    supervised_text = _format_supervised_ml_contract_for_prompt(artifact_contract, resolved_inputs if isinstance(resolved_inputs, dict) else {})
    if supervised_text:
        lines.append(supervised_text)

    resource_text = _format_resolved_resources_for_prompt(resolved_resources or {})
    if resource_text:
        lines.append(resource_text)

    return "\n".join(lines).strip() or task_text


def _legacy_final_response_contract_prompt() -> str:
    return (
        "Final response contract:\n"
        "- End with a JSON object in a fenced ```json block.\n"
        "- Use this exact shape:\n"
        "  {\n"
        "    \"status\": \"COMPLETED | BLOCKED_DEPENDENCY | FAILED | PARTIAL\",\n"
        "    \"summary\": \"short human-readable summary\",\n"
        "    \"produced_files\": [\n"
        "      {\n"
        "        \"path\": \"absolute-or-workspace-relative path\",\n"
        "        \"artifact_alias\": \"alias-or-null\",\n"
        "        \"description\": \"what this file contains\",\n"
        "        \"deliverable\": true | false,\n"
        "        \"module\": \"image_tabular | paper | code | docs | refs | null\"\n"
        "      }\n"
        "    ],\n"
        "    \"missing_inputs\": [\n"
        "      {\"artifact_alias\": \"alias-or-null\", \"reason\": \"why unavailable\"}\n"
        "    ],\n"
        "    \"acceptance_check\": {\"passed\": true, \"notes\": \"brief verification notes\"}\n"
        "  }\n"
        "- For BLOCKED_DEPENDENCY, also include the exact two-line marker before the JSON block:\n"
        "  STATUS: BLOCKED_DEPENDENCY\n"
        "  DETAIL: <which upstream task/data is missing>\n"
        "- For completed tasks, produced_files must list actual files you created or verified.\n"
        "- Mark files as deliverable=true ONLY for final outputs (visualizations, reports, papers).\n"
        "  Do NOT mark intermediate data, logs, or raw outputs as deliverables.\n"
        "- Module types: image_tabular (charts/figures), paper (manuscripts), code (scripts), docs (reports), refs (references).\n"
    )


def _legacy_rerun_update_mode_prompt() -> str:
    return (
        "Rerun/update mode:\n"
        "- If previous outputs already exist, inspect them first when useful.\n"
        "- Reuse valid existing work where it satisfies the current contract.\n"
        "- Regenerate or overwrite only files needed to satisfy the current task contract.\n"
        "- Do not treat pre-existing files alone as success unless you verified they satisfy the acceptance criteria.\n"
    )


def _legacy_format_contract_diff_for_cli(contract_diff: Optional[Dict[str, Any]]) -> str:
    if not isinstance(contract_diff, dict):
        return ""

    def _join(key: str, limit: int = 6) -> str:
        values = contract_diff.get(key)
        if not isinstance(values, list) or not values:
            return ""
        cleaned = [str(item).strip() for item in values if str(item).strip()]
        if not cleaned:
            return ""
        if len(cleaned) > limit:
            cleaned = cleaned[:limit] + ["..."]
        return ", ".join(cleaned)

    lines: List[str] = []
    for label, key in (
        ("Expected deliverables", "expected_deliverables"),
        ("Missing required outputs", "missing_required_outputs"),
        ("Invalid artifacts", "invalid_artifacts"),
        ("Wrong-format outputs", "wrong_format_outputs"),
        ("Unexpected extra outputs", "unexpected_outputs"),
        ("Actual outputs observed", "actual_outputs"),
    ):
        joined = _join(key)
        if joined:
            lines.append(f"- {label}: {joined}")
    return "\n".join(lines)


def _legacy_is_verification_only_task(task_text: str) -> bool:
    """Detect tool calls that should inspect existing artifacts, not rerun the task."""

    text = " ".join(str(task_text or "").lower().split())
    if not text:
        return False

    verification_terms = (
        "verify",
        "validate",
        "check",
        "inspect",
        "read the first",
        "count total",
        "count rows",
        "header",
        "columns exist",
        "列是否存在",
        "验证",
        "检查",
    )
    artifact_terms = (
        ".tsv",
        ".csv",
        ".json",
        ".parquet",
        ".txt",
        ".fasta",
        ".fa",
        ".h5ad",
        "file",
        "artifact",
        "output",
        "文件",
        "产物",
        "输出",
    )
    generation_terms = (
        "generate",
        "create",
        "produce",
        "write",
        "save",
        "map ",
        "compute",
        "train",
        "run analysis",
        "生成",
        "创建",
        "产出",
    )
    has_verification = any(term in text for term in verification_terms)
    has_artifact = any(term in text for term in artifact_terms)
    has_generation = any(term in text for term in generation_terms)
    return has_verification and has_artifact and not has_generation


def _legacy_build_cli_contract_repair_task(
    task: str,
    execution_spec: Optional[Dict[str, Any]],
    *,
    contract_diff: Optional[Dict[str, Any]],
    guidance: str,
) -> str:
    lines: List[str] = [
        "[STRICT CONTRACT REPAIR]",
        "The previous execution ran, but the required deliverables did not match the authoritative task contract.",
        "Do NOT change task scope, task meaning, methods, thresholds, or upstream/downstream responsibilities.",
        "Preserve useful extra outputs if you want, but they do NOT replace missing required outputs.",
        "Regenerate or supplement outputs so that the required deliverables exist exactly at the expected paths/patterns.",
    ]
    contract_text = _format_contract_diff_for_cli(contract_diff)
    if contract_text:
        lines.extend(["", "Contract mismatch:", contract_text])
    if guidance:
        lines.extend(["", "Verification guidance:", guidance.strip()])
    if task:
        lines.extend(["", "Original execution request:", str(task).strip()])
    if execution_spec:
        lines.extend([
            "",
            "Use the bound task context below as the single source of truth. Do not patch the plan.",
        ])
    return "\n".join(lines).strip()


def _legacy_validate_scope_contract(
    *,
    plan_id: Optional[int],
    task_id: Optional[int],
    require_task_context: bool,
) -> Optional[str]:
    if not require_task_context:
        return None
    if plan_id is None:
        return "Missing plan_id for strict atomic execution."
    if task_id is None:
        return "Missing task_id for strict atomic execution."
    return None


_MAX_SESSION_PROMOTE_FILE_BYTES = 250 * 1024 * 1024
_MAX_SESSION_PROMOTE_FILES = 500
_MAX_STALE_SESSION_ROOT_FILE_BYTES = 1


def _resolve_runtime_session_dir(session_id: Optional[str]) -> Path:
    token = str(session_id or "").strip()
    if not token:
        adhoc_dir = (_RUNTIME_DIR / "session_adhoc").resolve()
        adhoc_dir.mkdir(parents=True, exist_ok=True)
        return adhoc_dir
    return get_runtime_session_dir(token, create=True)


def _prune_stale_session_root_results(
    *,
    session_dir: Path,
    max_bytes: int = _MAX_STALE_SESSION_ROOT_FILE_BYTES,
) -> List[str]:
    """Delete legacy flat session-root files that are effectively empty.

    Historical runs copied every ``<run>/results/*`` file directly into
    ``<session>/results/``. Empty placeholder files in that flat namespace can
    later shadow canonical source inputs with the same basename. Only prune
    direct children and only when they are effectively empty to avoid touching
    meaningful task outputs.
    """
    session_resolved = session_dir.resolve()
    results_root = (session_resolved / "results").resolve()
    if not results_root.is_dir() or not _is_path_within(results_root, session_resolved):
        return []

    removed: List[str] = []
    for path in sorted(results_root.iterdir()):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > max_bytes:
            continue
        try:
            relative = str(path.relative_to(session_resolved)).replace("\\", "/")
        except ValueError:
            continue
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Failed to remove stale session-root artifact %s: %s", path, exc)
            continue
        removed.append(relative)

    if removed:
        logger.info(
            "Pruned %s stale flat session-root artifact(s): %s",
            len(removed),
            removed,
        )
    return removed


# Patterns to exclude from unified output promotion (debug/log artifacts)
_UNIFIED_PROMOTE_EXCLUDE_PATTERNS = {"*_code_executor.log", "*_debug.*", "*_claude_debug.*", "*.pyc"}


def _has_hidden_path_component(path: Path, *, relative_to: Path) -> bool:
    try:
        parts = path.relative_to(relative_to).parts
    except ValueError:
        parts = path.parts
    return any(part.startswith(".") for part in parts)


def _collect_non_semantic_run_files(
    *,
    run_dir: Path,
    semantic_roots: Sequence[Path],
) -> List[Path]:
    if not run_dir.exists() or not run_dir.is_dir():
        return []

    collected: List[Path] = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(_is_path_within(path, root) for root in semantic_roots):
            continue
        if _has_hidden_path_component(path, relative_to=run_dir):
            continue
        collected.append(path)
    return collected


def _should_skip_unified_promoted_file(path: Path, *, root: Path) -> bool:
    if _has_hidden_path_component(path, relative_to=root):
        return True
    name = path.name
    for pattern in _UNIFIED_PROMOTE_EXCLUDE_PATTERNS:
        if _fnmatch.fnmatch(name, pattern):
            return True
    return False


def _iter_promotable_run_files(
    *,
    scratch_dir: Path,
    subdirs: Sequence[str],
) -> List[tuple[Path, Path]]:
    """Collect deliverable files from a run and map them to promoted paths."""
    results: List[tuple[Path, Path]] = []
    seen_sources: set[str] = set()

    results_dir = (scratch_dir / "results").resolve()
    source_dirs = [results_dir] if results_dir.is_dir() else []

    for subdir_name in subdirs:
        if subdir_name == "results":
            continue
        candidate = (scratch_dir / subdir_name).resolve()
        if candidate.is_dir():
            source_dirs.append(candidate)

    for source_dir in source_dirs:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or _should_skip_unified_promoted_file(path, root=scratch_dir):
                continue
            source_key = str(path.resolve())
            if source_key in seen_sources:
                continue
            try:
                rel = path.relative_to(source_dir)
            except ValueError:
                continue

            try:
                subdir_rel = source_dir.relative_to(scratch_dir)
                dest_subdir = str(subdir_rel)
            except ValueError:
                dest_subdir = ""

            if dest_subdir == "results":
                dest_rel = rel
            else:
                dest_rel = Path(dest_subdir) / rel if dest_subdir else rel
            seen_sources.add(source_key)
            results.append((path, dest_rel))

    for path in _collect_non_semantic_run_files(run_dir=scratch_dir, semantic_roots=source_dirs):
        if _should_skip_unified_promoted_file(path, root=scratch_dir):
            continue
        source_key = str(path.resolve())
        if source_key in seen_sources:
            continue
        try:
            rel = path.relative_to(scratch_dir)
        except ValueError:
            continue
        seen_sources.add(source_key)
        results.append((path, rel))

    return results


def _collapse_rooted_rel_path(*, rel: Path, output_dir: Path, session_dir: Path) -> Path:
    """Strip the destination prefix when ``rel`` already carries it.

    Delegated agents sometimes mirror the session layout inside their scratch
    cwd (e.g. write ``raw_files/tmp/<run>/x.png`` relative to the run dir).
    Joining such a ``rel`` onto ``output_dir`` would double-root the path, so
    collapse it back onto the canonical single-prefix destination.

    Branch 1 (exact) collapses ``rel`` when it starts with the full prefix.
    Branch 2 (mirror) handles partial mirrors: the delegated agent often
    recreates only a leading sub-sequence of the prefix (``raw_files/tmp/``
    without the run segment, or nested mirrors like
    ``raw_files/tmp/a/raw_files/tmp/b/x.png``). Repeatedly strip the longest
    matching head block — bounded by the prefix length — so the destination
    stays single-rooted (e.g. ``raw_files/tmp/a/raw_files/tmp/b/x.png``
    collapses to ``b/x.png``; the preserved trailing segment is fine because
    we copy the file ourselves, and a single-rooted destination never 404s).
    """
    try:
        prefix = output_dir.resolve().relative_to(session_dir.resolve())
    except (ValueError, OSError):
        return rel
    # Branch 1: exact full-prefix collapse.
    try:
        return rel.relative_to(prefix)
    except ValueError:
        pass
    # Branch 2: partial-mirror collapse.
    prefix_parts = prefix.parts
    parts = list(rel.parts)
    changed = False
    for _ in range(len(prefix_parts)):
        head = 0
        limit = min(len(prefix_parts), len(parts))
        while head < limit and parts[head] == prefix_parts[head]:
            head += 1
        if head:
            parts = parts[head:]
            changed = True
            if not parts:
                return rel
            continue
        dropped = False
        max_block = min(len(prefix_parts), len(parts))
        for size in range(max_block, 0, -1):
            block = prefix_parts[:size]
            idx: Optional[int] = None
            for start in range(0, len(parts) - size + 1):
                if tuple(parts[start : start + size]) == block:
                    idx = start
                    break
            if idx is None:
                continue
            parts = parts[idx + size :]
            changed = True
            dropped = True
            if not parts:
                return rel
            break
        if not dropped:
            break
    if not changed or not parts:
        return rel
    return Path(*parts)


def _promote_results_to_unified_dir(
    *,
    scratch_dir: Path,
    output_dir: Path,
    subdirs: Sequence[str],
    session_dir: Path,
    max_files: int = 500,
) -> List[str]:
    """Promote final result files from scratch workspace to unified output dir.

    Copies files from ``results/``, ``code/``, ``data/``, ``docs/`` subdirs
    in the scratch workspace to the unified output directory, excluding
    debug/log files.

    Returns:
        List of promoted file paths relative to the session root directory.
    """
    promoted: List[str] = []
    count = 0

    for path, rel in _iter_promotable_run_files(scratch_dir=scratch_dir, subdirs=subdirs):
        if count >= max_files:
            logger.warning(
                "Unified promotion stopped after %s files (cap=%s)", count, max_files
            )
            break
        dest = output_dir / _collapse_rooted_rel_path(
            rel=rel, output_dir=output_dir, session_dir=session_dir
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, dest)
        except OSError as exc:
            logger.warning("Failed to promote %s -> %s: %s", path, dest, exc)
            continue
        try:
            rel_to_session = str(dest.relative_to(session_dir)).replace("\\", "/")
        except ValueError:
            rel_to_session = str(dest).replace("\\", "/")
        promoted.append(rel_to_session)
        count += 1

    # Fallback: when the run workspace produced no files but the session-level
    # results/ directory has content (e.g. qwen_code wrote to absolute session
    # results/ path instead of the run-scoped results/), promote those too.
    if not promoted:
        session_results = (session_dir / "results").resolve()
        if session_results.is_dir() and session_results != (scratch_dir / "results").resolve():
            for path in sorted(session_results.rglob("*")):
                if not path.is_file() or _should_skip_unified_promoted_file(path, root=session_results):
                    continue
                if count >= max_files:
                    logger.warning(
                        "Unified promotion (session fallback) stopped after %s files (cap=%s)",
                        count,
                        max_files,
                    )
                    break
                try:
                    rel = path.relative_to(session_results)
                except ValueError:
                    continue
                dest = output_dir / _collapse_rooted_rel_path(
                    rel=rel, output_dir=output_dir, session_dir=session_dir
                )
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(path, dest)
                except OSError as exc:
                    logger.warning("Failed to promote (session fallback) %s -> %s: %s", path, dest, exc)
                    continue
                try:
                    rel_to_session = str(dest.relative_to(session_dir)).replace("\\", "/")
                except ValueError:
                    rel_to_session = str(dest).replace("\\", "/")
                promoted.append(rel_to_session)
                count += 1
            if promoted:
                logger.info(
                    "Promoted %s file(s) from session results/ fallback to unified output dir %s",
                    len(promoted),
                    output_dir,
                )

    if promoted:
        logger.info(
            "Promoted %s file(s) to unified output dir %s",
            len(promoted),
            output_dir,
        )
    return promoted


def _promote_task_results_to_session_root(
    *,
    session_dir: Path,
    task_work_dir: Path,
    subdirs: Optional[Sequence[str]] = None,
    max_files: int = _MAX_SESSION_PROMOTE_FILES,
) -> tuple[List[str], List[str]]:
    """
    Copy deliverable run outputs into a task/run-scoped namespace under
    ``<session>/results/``.

    Claude Code cwd is an isolated ``run_<id>/`` tree; without this step outputs
    only exist under nested paths. We keep that nesting in the promoted session
    tree to avoid flat-name collisions like ``metadata.csv`` shadowing a
    canonical source file for later tasks.
    """
    session_resolved = session_dir.resolve()
    task_resolved = task_work_dir.resolve()
    effective_subdirs = tuple(subdirs) if subdirs else _DEFAULT_TASK_SUBDIRECTORIES
    promotable_files = _iter_promotable_run_files(
        scratch_dir=task_resolved,
        subdirs=effective_subdirs,
    )
    if not promotable_files:
        return [], []

    try:
        task_scope_rel = task_resolved.relative_to(session_resolved)
    except ValueError:
        task_scope_rel = Path(task_resolved.parent.name) / task_resolved.name

    dst_root = (session_resolved / "results" / task_scope_rel).resolve()
    if not _is_path_within(dst_root, session_resolved):
        return [], []

    dst_root.mkdir(parents=True, exist_ok=True)
    promoted: List[str] = []
    skipped_large: List[str] = []
    count = 0
    for path, rel in promotable_files:
        if count >= max_files:
            logger.warning(
                "Session results promotion stopped after %s files (cap=%s)",
                count,
                max_files,
            )
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_SESSION_PROMOTE_FILE_BYTES:
            logger.info(
                "Skipping large file for session results promotion: %s (%s bytes)",
                path,
                size,
            )
            skipped_large.append(str(path.resolve()))
            continue
        dest = (dst_root / rel).resolve()
        if not _is_path_within(dest, dst_root):
            logger.warning("Skipping promotion path outside results/: %s", rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, dest)
        except OSError as exc:
            logger.warning("Failed to promote %s -> %s: %s", path, dest, exc)
            continue
        promoted.append(str(dest.relative_to(session_resolved)).replace("\\", "/"))
        count += 1

    if promoted:
        logger.info(
            "Promoted %s file(s) from %s/ to session results/%s for artifact URLs",
            len(promoted),
            task_resolved.name,
            str(task_scope_rel).replace("\\", "/"),
        )
    return promoted, skipped_large


def _promote_external_contract_artifacts(
    *,
    contract_artifacts: List[Dict[str, Any]],
    task_work_dir: Path,
    unified_output_dir: Optional[Path],
) -> List[str]:
    """Promote contract artifact files that exist outside the workspace to unified output dir.

    When qwen_code writes files to absolute paths (e.g. /results/report.md) instead
    of the workspace, the normal workspace-based promote misses them. This function
    catches those external files and copies them to the unified output directory.
    """
    if not unified_output_dir or not contract_artifacts:
        return []
    promoted: List[str] = []
    workspace_resolved = task_work_dir.resolve()
    for artifact in contract_artifacts:
        if not isinstance(artifact, dict):
            continue
        if not artifact.get("exists"):
            continue
        path_str = str(artifact.get("path") or "").strip()
        if not path_str:
            continue
        try:
            artifact_path = Path(path_str).resolve()
        except OSError:
            continue
        if not artifact_path.exists() or not artifact_path.is_file():
            continue
        try:
            artifact_path.relative_to(workspace_resolved)
            continue
        except ValueError:
            pass
        dest = unified_output_dir / artifact_path.name
        if dest.exists():
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(artifact_path), str(dest))
            promoted.append(str(dest))
            logger.info(
                "Promoted external contract artifact %s -> %s",
                path_str, dest,
            )
        except OSError as exc:
            logger.warning("Failed to promote external artifact %s: %s", path_str, exc)
    return promoted


def _promote_project_level_strays(
    *,
    contract_artifacts: List[Dict[str, Any]],
    unified_output_dir: Optional[Path],
    project_root: Path,
) -> List[str]:
    """Move files that LLM wrote to project-level results/output/ into raw_files/.

    Scans contract_artifacts for paths under project_root/results/ or
    project_root/output/, copies them to unified_output_dir, and records
    the mapping for future reference.
    """
    if not unified_output_dir or not contract_artifacts:
        return []
    promoted: List[str] = []
    workspace_resolved = unified_output_dir.resolve()
    project_resolved = project_root.resolve()

    for artifact in contract_artifacts:
        if not isinstance(artifact, dict):
            continue
        if not artifact.get("exists"):
            continue
        path_str = str(artifact.get("path") or "").strip()
        if not path_str:
            continue
        try:
            artifact_path = Path(path_str).resolve()
        except OSError:
            continue
        if not artifact_path.exists() or not artifact_path.is_file():
            continue
        # Only handle files under project-level results/ or output/
        try:
            rel = artifact_path.relative_to(project_resolved)
        except ValueError:
            continue
        top_level = rel.parts[0] if rel.parts else ""
        if top_level not in ("results", "output"):
            continue
        # Skip if already inside unified_output_dir
        try:
            artifact_path.relative_to(workspace_resolved)
            continue
        except ValueError:
            pass
        # Preserve subdirectory structure relative to results/ or output/
        sub_rel = Path(*rel.parts[1:]) if len(rel.parts) > 1 else Path(rel.name)
        dest = unified_output_dir / sub_rel
        if dest.exists():
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(artifact_path), str(dest))
            promoted.append(str(dest))
            logger.info(
                "Promoted project-level stray %s -> %s",
                path_str, dest,
            )
        except OSError as exc:
            logger.warning("Failed to promote project-level stray %s: %s", path_str, exc)
    return promoted


_RUN_PREFIX_RE = re.compile(r"^run_\d{8}_\d{6}_\d+_[0-9a-f]+_")


def _reconcile_deliverables(
    *,
    execution_spec: Optional[Dict[str, Any]],
    task_work_dir: Path,
    unified_output_dir: Optional[Path] = None,
    session_results_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Align actual produced files with expected deliverable names.

    Scans ``results/`` under *task_work_dir* for files whose names differ
    from acceptance_criteria expectations only by a ``run_*_`` prefix or
    directory nesting, and creates symlinks so verification finds them.

    Returns a report dict with ``aligned``, ``missing``, and ``already_ok``
    lists.
    """
    report: Dict[str, Any] = {"aligned": [], "missing": [], "already_ok": []}
    if not isinstance(execution_spec, dict):
        return report
    criteria = execution_spec.get("acceptance_criteria")
    if not isinstance(criteria, dict):
        return report

    expected_names = derive_expected_deliverables(criteria, include_globs=False, relative_only=True)
    if not expected_names:
        return report

    results_dir = task_work_dir / "results"
    if not results_dir.is_dir():
        return report

    actual_files: Dict[str, Path] = {}
    for path in sorted(results_dir.rglob("*")):
        if not path.is_file():
            continue
        actual_files[path.name] = path

    search_dirs: List[Path] = [results_dir]
    if unified_output_dir and unified_output_dir.is_dir():
        search_dirs.append(unified_output_dir)
    if session_results_dir and session_results_dir.is_dir():
        search_dirs.append(session_results_dir)

    for raw_expected in expected_names:
        expected_name = Path(raw_expected).name
        expected_stem = Path(expected_name).stem
        expected_suffix = Path(expected_name).suffix

        found = False
        for search_dir in search_dirs:
            candidate = search_dir / expected_name
            if candidate.exists():
                found = True
                break
        if found:
            report["already_ok"].append(expected_name)
            continue

        matched_source: Optional[Path] = None
        for actual_name, actual_path in actual_files.items():
            if actual_name == expected_name:
                matched_source = actual_path
                break
            stripped = _RUN_PREFIX_RE.sub("", actual_name)
            if stripped == expected_name:
                matched_source = actual_path
                break
            if (
                actual_name.endswith(expected_suffix)
                and actual_name.endswith("_" + expected_name)
            ):
                matched_source = actual_path
                break

        if matched_source is None:
            report["missing"].append(expected_name)
            continue

        for target_dir in search_dirs:
            link_path = target_dir / expected_name
            if link_path.exists() or link_path.is_symlink():
                continue
            try:
                link_path.parent.mkdir(parents=True, exist_ok=True)
                link_path.symlink_to(matched_source.resolve())
                logger.info(
                    "[RECONCILE] %s -> %s (in %s)",
                    expected_name,
                    matched_source.name,
                    target_dir.name,
                )
            except OSError as exc:
                logger.warning("[RECONCILE] Failed to create symlink %s: %s", link_path, exc)

        report["aligned"].append({
            "expected": expected_name,
            "actual": matched_source.name,
        })

    if report["aligned"] or report["missing"]:
        logger.info(
            "[RECONCILE] task_work_dir=%s aligned=%d already_ok=%d missing=%d",
            task_work_dir.name,
            len(report["aligned"]),
            len(report["already_ok"]),
            len(report["missing"]),
        )
    return report


def _build_search_and_generate_prompt(
    missing_files: List[str],
    session_dir: Path,
    execution_spec: Optional[Dict[str, Any]],
    *,
    is_timeout: bool = False,
) -> str:
    task_name = ""
    task_instruction = ""
    if isinstance(execution_spec, dict):
        task_name = str(execution_spec.get("task_name") or "")
        task_instruction = str(execution_spec.get("task_instruction") or "")

    file_list = "\n".join(f"  - {f}" for f in missing_files)

    if is_timeout:
        return (
            "DELIVERABLE RECOVERY TASK (PREVIOUS EXECUTION TIMED OUT OR CLI TOOL CALL FAILED)\n\n"
            "The previous execution was killed due to a timeout/no output or a fatal CLI tool-call failure. "
            "The analysis code may have been partially written or not written at all.\n\n"
            f"Missing files:\n{file_list}\n\n"
            f"Task context:\n"
            f"  Name: {task_name}\n"
            f"  Instruction: {task_instruction[:500]}\n\n"
            f"Step 1 — CHECK: Look in {session_dir} for any existing code or partial results "
            f"from the previous run. Check code/ and results/ directories.\n\n"
            "Step 2 — SEARCH: Use file_operations to search for each missing file in "
            "other task outputs (plan*/task*/run_*/results/). Files may exist with "
            "a run_*_ prefix.\n\n"
            "Step 3 — COPY: If you find a file, copy it to results/ with the "
            "exact expected name (no prefix).\n\n"
            "Step 4 — RE-EXECUTE: If files are truly not found anywhere, you MUST "
            "re-run the analysis. Write the Python script based on the task "
            "instruction above, save it to code/, and execute it with code_executor. "
            "Output must go to results/.\n"
            "When writing scripts, avoid one huge write_file call. Create a small skeleton first, "
            "then append or edit in focused chunks so tool-call output is never truncated.\n\n"
            "IMPORTANT: This is a recovery task after a timeout. You need to complete "
            "the work that was interrupted. Do NOT just report files as missing."
        )

    return (
        "DELIVERABLE SEARCH TASK\n\n"
        "The main analysis already ran. Do NOT re-run it. Your ONLY job is to "
        "find the missing deliverable files listed below and copy them to results/.\n\n"
        f"Missing files:\n{file_list}\n\n"
        f"Task context:\n"
        f"  Name: {task_name}\n"
        f"  Instruction: {task_instruction[:500]}\n\n"
        f"Step 1 — SEARCH: Use file_operations to search {session_dir} "
        f"for each missing file. Check all subdirectories including other "
        f"task outputs (plan*/task*/run_*/results/). Files may exist with "
        f"a run_*_ prefix or in a different task's results/ directory.\n\n"
        "Step 2 — COPY: If you find a file, copy it to results/ with the "
        "exact expected name (no prefix). Use file_operations copy.\n\n"
        "If a file is truly not found anywhere, just report it as missing. "
        "Do NOT attempt to generate or re-run any analysis.\n\n"
        "Finish quickly. This is a file search and copy operation, not an analysis task."
    )


def _collect_run_artifacts(
    *,
    run_dir: Path,
    subdirs: Sequence[str],
    max_files: int = 2000,
) -> List[str]:
    collected: List[str] = []
    seen = set()
    semantic_roots: List[Path] = []
    for name in subdirs:
        root = (run_dir / str(name)).resolve()
        if not root.exists() or not root.is_dir():
            continue
        semantic_roots.append(root)
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            collected.append(resolved)
            if len(collected) >= max_files:
                return collected
    for path in _collect_non_semantic_run_files(run_dir=run_dir, semantic_roots=semantic_roots):
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        collected.append(resolved)
        if len(collected) >= max_files:
            return collected
    return collected


def _recover_files_from_historical_runs(
    *,
    task_root_dir: Path,
    current_run_dir: Path,
    execution_spec: Optional[Dict[str, Any]],
    task_subdirs: Sequence[str],
) -> List[str]:
    """Search historical run directories for required files and copy them to current run.
    
    When a task fails due to missing outputs but files exist in previous runs,
    this function recovers them to avoid unnecessary re-execution.
    
    Returns list of recovered file paths in the current run directory.
    """
    if not execution_spec or not task_root_dir.exists():
        return []

    criteria = execution_spec.get("acceptance_criteria")
    expected_deliverables = derive_expected_deliverables(criteria)
    if not expected_deliverables:
        return []

    historical_runs = [
        run_dir for run_dir in sorted(task_root_dir.glob("run_*"))
        if run_dir.is_dir() and run_dir.resolve() != current_run_dir.resolve()
    ]

    if not historical_runs:
        return []

    recovered_files = []

    for expected in expected_deliverables:
        expected_text = str(expected or "").strip().replace("\\", "/")
        if not expected_text:
            continue

        expected_path = Path(expected_text)
        has_glob = any(token in expected_text for token in ("*", "?", "["))

        for hist_run in reversed(historical_runs):
            found = False
            
            if not has_glob:
                candidate = hist_run / expected_path
                if candidate.exists() and candidate.is_file():
                    dest = current_run_dir / expected_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(candidate, dest)
                        recovered_files.append(str(dest.resolve()))
                        logger.info(
                            f"[CODE_EXECUTOR] Recovered {expected_text} from historical run {hist_run.name}"
                        )
                        found = True
                    except Exception as exc:
                        logger.warning(f"Failed to recover {expected_text}: {exc}")
            
            if has_glob:
                pattern = str(expected_path)
                matches = list(hist_run.glob(pattern))
                for match in matches:
                    if match.is_file():
                        rel_path = match.relative_to(hist_run)
                        dest = current_run_dir / rel_path
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(match, dest)
                            recovered_files.append(str(dest.resolve()))
                            logger.info(
                                f"[CODE_EXECUTOR] Recovered {rel_path} from historical run {hist_run.name}"
                            )
                            found = True
                        except Exception as exc:
                            logger.warning(f"Failed to recover {rel_path}: {exc}")
            
            if found:
                break

    return recovered_files


def _extract_code_workspace_metadata(
    *,
    run_dir: Path,
    produced_files: Sequence[str],
) -> tuple[Optional[str], Optional[str]]:
    code_dir = (run_dir / "code").resolve()
    code_dir_value = str(code_dir) if code_dir.exists() and code_dir.is_dir() else None
    primary_code_file: Optional[str] = None
    for item in produced_files:
        try:
            candidate = Path(str(item)).resolve()
        except Exception:
            continue
        if not candidate.is_file():
            continue
        if code_dir_value is not None:
            try:
                candidate.relative_to(code_dir)
            except ValueError:
                continue
        if candidate.suffix.lower() in {".py", ".r", ".sh", ".js", ".ts", ".tsx"}:
            primary_code_file = str(candidate)
            break
    return code_dir_value, primary_code_file


def _contract_required_artifact_records(
    *,
    execution_spec: Optional[Dict[str, Any]],
    task_work_dir: Path,
    produced_files: Sequence[str],
    max_items: int = 100,
) -> List[Dict[str, Any]]:
    """Return authoritative records for contract-required output files.

    These records are independent from UI/session promotion. Large files may be
    intentionally skipped for browser-facing artifact URLs, but deterministic
    verification and downstream plan state still need a durable statement that
    a contract-required file exists at its real task-run path.
    """
    criteria = execution_spec.get("acceptance_criteria") if isinstance(execution_spec, dict) else None
    expected_deliverables = derive_expected_deliverables(criteria)
    if not expected_deliverables:
        return []

    produced_paths: List[Path] = []
    for raw_path in produced_files:
        text = str(raw_path or "").strip()
        if not text:
            continue
        path = Path(text).expanduser()
        try:
            if path.exists() and path.is_file():
                produced_paths.append(path.resolve())
        except OSError:
            continue

    records: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _normalize(value: Any) -> str:
        return str(value or "").strip().replace(chr(92), "/").strip("/")

    for expected in expected_deliverables:
        expected_text = _normalize(expected)
        if not expected_text or any(token in expected_text for token in ("*", "?", "[")):
            continue
        expected_path = Path(str(expected or "").strip()).expanduser()
        direct = expected_path if expected_path.is_absolute() else (task_work_dir / expected_path)
        candidates: List[Path] = [direct]
        if not expected_path.is_absolute():
            prefixed_source = _find_unique_run_prefixed_contract_source(task_work_dir, expected_path)
            if prefixed_source is not None:
                candidates.append(prefixed_source)
        for produced in produced_paths:
            produced_norm = _normalize(str(produced))
            if produced_norm == expected_text or produced_norm.endswith(f"/{expected_text}"):
                candidates.append(produced)
        selected: Optional[Path] = None
        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_file():
                    selected = candidate.resolve()
                    break
            except OSError:
                continue
        if selected is None:
            continue
        key = f"{expected_text}|{selected}"
        if key in seen:
            continue
        seen.add(key)
        try:
            size = selected.stat().st_size
        except OSError:
            size = None
        try:
            relative_to_task = str(selected.relative_to(task_work_dir.resolve())).replace(chr(92), "/")
        except Exception:
            relative_to_task = None
        records.append({
            "expected": expected_text,
            "path": str(selected),
            "size": size,
            "exists": True,
            "relative_to_task": relative_to_task,
            "verification_source": "contract_required_output",
        })
        if len(records) >= max_items:
            break
    return records


def _append_contract_artifact_paths(
    verification_artifact_paths: List[str],
    contract_artifacts: Sequence[Dict[str, Any]],
) -> None:
    """Expose contract-required file paths to deterministic verification."""
    seen = set(verification_artifact_paths)
    for record in contract_artifacts:
        if not isinstance(record, dict):
            continue
        path = str(record.get("path") or "").strip()
        if not path or path in seen:
            continue
        verification_artifact_paths.append(path)
        seen.add(path)


def _build_verification_artifact_paths(
    *,
    task_work_dir: Path,
    subdirs: Sequence[str],
    produced_files: Sequence[str],
    session_artifact_paths: Sequence[str],
    session_dir: Path,
    max_items: int = 200,
) -> List[str]:
    """Return artifact hints that deterministic verification can trust.

    Verification should resolve relative acceptance-criteria paths against the
    real task run directory first, then use produced files as fallbacks.  The
    promoted ``session/results/...`` copies are kept only as secondary hints for
    UI/artifact discovery.
    """
    ordered: List[str] = []
    seen: set[str] = set()

    def _append(value: Optional[str]) -> None:
        if not isinstance(value, str):
            return
        text = value.strip()
        if not text or text in seen:
            return
        seen.add(text)
        ordered.append(text)

    _append(str(task_work_dir.resolve()))
    for name in subdirs:
        root = (task_work_dir / str(name)).resolve()
        _append(str(root))
    for path in produced_files:
        _append(str(path))
    for rel in session_artifact_paths:
        try:
            abs_path = (session_dir / str(rel)).resolve()
        except Exception:
            continue
        _append(str(abs_path))
    return ordered[:max_items]


def _is_path_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _is_path_within_lexical(child: Path, parent: Path) -> bool:
    """Return True when *child* is lexically under *parent* without resolving symlinks."""
    try:
        child.absolute().relative_to(parent.absolute())
        return True
    except Exception:
        return False


def _is_allowed_task_read_path(path: Path, session_dir: Path) -> bool:
    """Allow paths in the project/session, including project-local symlink aliases."""
    return (
        _is_path_within(path, _PROJECT_ROOT)
        or _is_path_within(path, session_dir)
        or any(_is_path_within(path, root) for root in _DEFAULT_EXTERNAL_READ_ROOTS)
        or _is_path_within_lexical(path, _PROJECT_ROOT)
        or _is_path_within_lexical(path, session_dir)
        or any(_is_path_within_lexical(path, root) for root in _DEFAULT_EXTERNAL_READ_ROOTS)
    )


def _extract_task_referenced_read_dirs(
    task: str,
    *,
    execution_spec: Optional[Dict[str, Any]],
    session_dir: Path,
) -> List[str]:
    texts: List[str] = [str(task or "")]
    if isinstance(execution_spec, dict) and execution_spec:
        try:
            texts.append(json.dumps(execution_spec, ensure_ascii=False))
        except Exception:
            logger.debug("Failed to serialize execution_spec for task path inference.")

    if not any(text.strip() for text in texts):
        return []

    escaped_root = re.escape(str(_PROJECT_ROOT))
    absolute_pattern = re.compile(rf"{escaped_root}(?:/{_TASK_PATH_TOKEN_RE})+")
    relative_roots = "|".join(re.escape(prefix) for prefix in _TASK_READ_DIR_PREFIXES)
    relative_pattern = re.compile(
        rf"(?<![\w.-])(?:{relative_roots})(?:/{_TASK_PATH_TOKEN_RE})+"
    )

    inferred_dirs: List[str] = []
    seen: set[str] = set()

    def _register(raw_path: str) -> None:
        token = str(raw_path or "").strip()
        if not token or len(token) > 1024:
            return
        candidate = Path(token)
        if not candidate.is_absolute():
            candidate = _PROJECT_ROOT / candidate
        try:
            lexical = candidate.absolute()
            target_dir = lexical if lexical.is_dir() else lexical.parent
            if not target_dir.exists() or not target_dir.is_dir():
                return
        except OSError:
            return
        if not _is_allowed_task_read_path(target_dir, session_dir):
            return
        dir_str = str(target_dir)
        if dir_str in seen:
            return
        seen.add(dir_str)
        inferred_dirs.append(dir_str)

    for text in texts:
        for match in absolute_pattern.finditer(text):
            _register(match.group(0))
        for match in relative_pattern.finditer(text):
            _register(match.group(0))

    return inferred_dirs


def _sanitize_task_dir_component(value: str) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return "llm_task"

    normalized_chars: List[str] = []
    prev_is_sep = False
    for ch in token:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            normalized_chars.append(ch)
            prev_is_sep = False
            continue
        if ch in {"_", "-", " ", "/", "\\", ".", ":"}:
            if not prev_is_sep:
                normalized_chars.append("_")
                prev_is_sep = True
            continue
        # Drop other punctuation and unicode symbols.
        if not prev_is_sep:
            normalized_chars.append("_")
            prev_is_sep = True

    sanitized = "".join(normalized_chars).strip("_")
    if not sanitized:
        return "llm_task"
    if len(sanitized) > 80:
        sanitized = sanitized[:80].rstrip("_")
    return sanitized or "llm_task"


async def _generate_task_dir_name_llm(task: str) -> str:
    """
    Generate a directory name using pure LLM semantic understanding.
    NO regex, NO keyword matching - fully LLM-based as per research requirements.
    
    Args:
        task: Task description
        
    Returns:
        Directory name like "train_baseline_model_a3f2b1"
    """
    try:
        # Use unified LLM client for semantic analysis
        from app.llm import get_default_client
        import asyncio

        client = get_default_client()

        prompt = f"""Analyze the following task and generate a concise directory name.

Task: {task}

Requirements:
1. Extract the core semantic meaning of the task
2. Generate 2-4 English words that capture the essence
3. Use lowercase with underscores (e.g., train_model, analyze_data)
4. Be specific and descriptive
5. Return ONLY the directory name, nothing else

Examples:
- Task: " data/code_task ， baseline ，" → analyze_train_baseline
- Task: "Generate a report on user behavior" → user_behavior_report
- Task: "Debug the authentication system" → debug_authentication

Directory name:"""

        # Run LLM call in a thread without blocking the loop; asyncio.to_thread
        # propagates contextvars (usage context + project LLM credentials).
        llm_response = await asyncio.to_thread(client.chat, prompt)

        # Clean and validate LLM response.
        dir_name = llm_response.strip().lower()

        # Remove any extra text (LLM might add explanation)
        # Take only the first line if multiple lines
        dir_name = dir_name.split('\n')[0].strip()

        # Remove common prefixes that LLM might add
        for prefix in ['directory name:', 'name:', 'output:', '→', '-', '>', '*']:
            if dir_name.startswith(prefix):
                dir_name = dir_name[len(prefix):].strip()

        # Ensure a filesystem-safe directory name component.
        dir_name = _sanitize_task_dir_component(dir_name)

        # If LLM failed to generate a valid name, use a fallback
        if not dir_name or len(dir_name) < 3:
            logger.warning(f"LLM generated invalid directory name: '{llm_response}', using semantic fallback")
            # Use a simple hash-based name as last resort
            dir_name = "llm_task"

        # Add hash to keep semantic grouping stable while avoiding collisions.
        task_hash = hashlib.md5(task.encode('utf-8')).hexdigest()[:6]

        return f"{dir_name}_{task_hash}"

    except Exception as e:
        logger.error(f"LLM-based directory name generation failed: {e}")
        # Research requirement: fail explicitly rather than silently degrade
        # But for directory naming, we need a fallback to avoid breaking the system
        task_hash = hashlib.md5(task.encode('utf-8')).hexdigest()[:6]
        return f"task_{task_hash}"


_COMPLETED_TASK_STATUSES = {"completed", "done", "success"}


def _extract_acceptance_criteria_from_node(node: Any) -> Optional[Dict[str, Any]]:
    metadata = getattr(node, "metadata", None)
    if isinstance(metadata, dict):
        criteria = metadata.get("acceptance_criteria")
        if isinstance(criteria, dict):
            return json.loads(json.dumps(criteria, ensure_ascii=False))

    raw_execution_result = getattr(node, "execution_result", None)
    if isinstance(raw_execution_result, str):
        try:
            raw_execution_result = json.loads(raw_execution_result)
        except (TypeError, json.JSONDecodeError):
            raw_execution_result = None
    if isinstance(raw_execution_result, dict):
        payload_meta = raw_execution_result.get("metadata")
        if isinstance(payload_meta, dict):
            criteria = payload_meta.get("acceptance_criteria")
            if isinstance(criteria, dict):
                return json.loads(json.dumps(criteria, ensure_ascii=False))
    derived = derive_acceptance_criteria_from_text(getattr(node, "instruction", None))
    if isinstance(derived, dict) and derived.get("checks"):
        return derived
    return None


def _build_ad_hoc_execution_spec(task_text: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(task_text, str) or not task_text.strip():
        return None

    acceptance_criteria = derive_acceptance_criteria_from_text(task_text)
    checks = acceptance_criteria.get("checks") if isinstance(acceptance_criteria, dict) else None
    if not isinstance(checks, list) or not checks:
        return None

    task_name = "Ad-hoc execution task"
    for raw_line in task_text.splitlines():
        line = " ".join(str(raw_line or "").split()).strip()
        if not line:
            continue
        task_name = line[:93] + "..." if len(line) > 96 else line
        break

    return {
        "plan_id": None,
        "task_id": None,
        "task_name": task_name,
        "task_instruction": task_text.strip(),
        "acceptance_criteria": acceptance_criteria,
        "dependency_outputs": [],
        "dependency_artifact_paths": [],
        "dependency_blockers": [],
    }


def _build_execution_spec(
    plan_id: Optional[int],
    task_id: Optional[int],
    *,
    task_text: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if plan_id is None and task_id is None:
        return _build_ad_hoc_execution_spec(task_text)
    if plan_id is None or task_id is None:
        return None

    try:
        from app.routers.chat.code_executor_helpers import extract_task_artifact_paths
        from app.routers.chat.services import plan_repository
        from app.services.plans.artifact_contracts import (
            load_artifact_manifest,
            resolve_artifact_contract_with_provenance,
            resolve_manifest_aliases,
        )
    except Exception as exc:
        logger.warning("Failed to load plan-aware execution context: %s", exc)
        return None

    try:
        tree = plan_repository.get_plan_tree(int(plan_id))
    except Exception as exc:
        logger.warning("Failed to load plan tree %s for code executor: %s", plan_id, exc)
        return None

    if not tree.has_node(int(task_id)):
        return None

    node = tree.get_node(int(task_id))
    node_metadata = node.metadata if isinstance(getattr(node, "metadata", None), dict) else {}
    artifact_contract = resolve_artifact_contract_with_provenance(
        task_name=str(node.display_name()).strip(),
        instruction=str(getattr(node, "instruction", "") or ""),
        metadata=node_metadata,
    ).as_contract_dict()
    manifest = load_artifact_manifest(int(plan_id), session_id)
    resolved_input_artifacts = resolve_manifest_aliases(
        manifest,
        list(artifact_contract.get("requires") or []),
    )
    dependency_outputs: List[Dict[str, Any]] = []
    dependency_artifact_paths: List[str] = []
    dependency_blockers: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()
    for path in resolved_input_artifacts.values():
        text = str(path or "").strip()
        if text and text not in seen_paths:
            seen_paths.add(text)
            dependency_artifact_paths.append(text)

    for dep_id in list(getattr(node, "dependencies", []) or []):
        try:
            dep_id_int = int(dep_id)
        except (TypeError, ValueError):
            continue
        if not tree.has_node(dep_id_int):
            continue
        dep_node = tree.get_node(dep_id_int)
        dep_status = str(getattr(dep_node, "status", "") or "").strip().lower()
        dep_artifacts = extract_task_artifact_paths(dep_node)
        for path in dep_artifacts:
            text = str(path or "").strip()
            if not text or text in seen_paths:
                continue
            seen_paths.add(text)
            dependency_artifact_paths.append(text)
        dep_entry = {
            "task_id": dep_id_int,
            "task_name": str(dep_node.display_name()).strip(),
            "status": dep_status,
            "artifact_paths": dep_artifacts,
            "execution_result": str(getattr(dep_node, "execution_result", "") or "").strip(),
        }
        dependency_outputs.append(dep_entry)
        if dep_status not in _COMPLETED_TASK_STATUSES:
            dependency_blockers.append(dep_entry)

    return {
        "plan_id": int(plan_id),
        "task_id": int(task_id),
        "task_name": str(node.display_name()).strip(),
        "task_instruction": str(getattr(node, "instruction", "") or "").strip(),
        "acceptance_criteria": _extract_acceptance_criteria_from_node(node),
        "artifact_contract": artifact_contract,
        "resolved_input_artifacts": resolved_input_artifacts,
        "dependency_outputs": dependency_outputs,
        "dependency_artifact_paths": dependency_artifact_paths,
        "dependency_blockers": dependency_blockers,
    }


def _summarize_dependency_blockers(execution_spec: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(execution_spec, dict):
        return None
    blockers = execution_spec.get("dependency_blockers")
    if not isinstance(blockers, list) or not blockers:
        return None

    details: List[str] = []
    for blocker in blockers[:4]:
        if not isinstance(blocker, dict):
            continue
        name = str(blocker.get("task_name") or blocker.get("task_id") or "unknown").strip()
        status = str(blocker.get("status") or "unknown").strip()
        details.append(f"{name} [{status}]")
    if not details:
        return "Blocked by incomplete upstream dependencies."
    return "Blocked by incomplete upstream dependencies: " + ", ".join(details)


async def _execute_task_locally(
    task: str,
    *,
    work_dir: Optional[str] = None,
    data_dir: Optional[str] = None,
    extra_dirs: Optional[Sequence[str]] = None,
    docker_image: Optional[str] = None,
    runtime_mode: Optional[str] = None,
    tool_context: Optional[Any] = None,
    auto_fix: bool = True,
    session_dir: Optional[str] = None,
    execution_spec: Optional[Dict[str, Any]] = None,
    resolved_resources: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Execute a task using the unified local code execution backend.

    Delegates to ``execute_code_locally()`` which handles code generation,
    file-persistent execution, error classification, and LLM-based fixing.
    """
    from app.services.interpreter.code_execution import CodeExecutionSpec, execute_code_locally
    from app.services.llm.llm_service import get_llm_service

    _mp = (getattr(tool_context, 'model_provider', None) or {}) if tool_context else {}
    if _mp.get("base_url") and _mp.get("api_key"):
        from app.llm import LLMClient
        from app.services.llm.llm_service import LLMService
        _llm_override = LLMService(LLMClient(
            provider=_mp.get("type") or "openai",
            url=_mp["base_url"].rstrip("/") + "/v1/chat/completions",
            api_key=_mp["api_key"],
            model=_mp.get("model") or "qwen3.7-max",
        ))
    else:
        _llm_override = get_llm_service()

    effective_runtime_mode = _resolve_code_executor_local_runtime(runtime_mode)
    execution_backend = "docker" if effective_runtime_mode == "docker" else "local"
    effective_docker_image = (
        _resolve_code_executor_docker_image(docker_image)
        if execution_backend == "docker"
        else None
    )

    logger.info(
        "[CODE_EXECUTOR_LOCAL] Using %s runtime backend for task",
        effective_runtime_mode,
    )

    async def _report(stage: str, message: str, **extra: Any) -> None:
        if tool_context is not None and tool_context.on_progress:
            await tool_context.on_progress({"stage": stage, "message": message, **extra})

    await _report("started", f"Generating code for task ({effective_runtime_mode} runtime)")

    if not work_dir:
        import tempfile
        work_dir = tempfile.mkdtemp(prefix="cc_local_")
    else:
        work_dir = str(work_dir).strip()

    os.makedirs(work_dir, exist_ok=True)

    blocked_reason = _summarize_dependency_blockers(execution_spec)
    if blocked_reason:
        await _report("failed", blocked_reason, error_category="blocked_dependency")
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "exit_code": 1,
            "result": blocked_reason,
            "error": blocked_reason,
            "error_category": "blocked_dependency",
            "error_summary": blocked_reason,
            "execution_mode": f"code_executor_{effective_runtime_mode}",
            "docker_image_effective": effective_docker_image,
            "runtime_failure": False,
        }

    # Build task description with directory context.
    results_dir = os.path.join(work_dir, "results")
    task_desc = (
        f"{task}\n\n"
        f"Working directory: {work_dir}\n"
        f"Save outputs to: {results_dir}\n"
        f"IMPORTANT: Use RELATIVE paths from your working directory (e.g. results/output.csv). "
        f"Do NOT use absolute paths like /home/.../results/ or /home/.../output/."
    )
    if session_dir:
        session_results = os.path.join(session_dir, "results")
        if os.path.isdir(session_results):
            prior_files = os.listdir(session_results)
            if prior_files:
                task_desc += (
                    f"\nPrior session outputs (from earlier tasks): {session_results}\n"
                    f"Files: {', '.join(sorted(prior_files)[:20])}"
                )
    if data_dir:
        task_desc += f"\nPrimary data directory: {data_dir}"
    resource_text = _format_resolved_resources_for_prompt(resolved_resources or {})
    if resource_text:
        task_desc += "\n" + resource_text
    if execution_spec and execution_spec.get("dependency_artifact_paths"):
        dependency_paths = execution_spec.get("dependency_artifact_paths") or []
        task_desc += (
            "\nExplicit upstream artifact paths (ABSOLUTE, authoritative):\n"
            + "\n".join(f"- {path}" for path in dependency_paths[:20])
        )

    readable_dirs: List[str] = []
    seen_dirs: set[str] = set()
    for item in extra_dirs or ():
        candidate = str(item or "").strip()
        if not candidate or candidate in seen_dirs:
            continue
        seen_dirs.add(candidate)
        readable_dirs.append(candidate)
    if readable_dirs:
        task_desc += (
            "\nReadable directories:\n"
            + "\n".join(f"- {path}" for path in readable_dirs)
        )

    writable_dirs: List[str] = []
    try:
        work_dir_path = Path(work_dir).resolve()
        for directory in readable_dirs:
            candidate = Path(directory).resolve()
            if _is_path_within(work_dir_path, candidate):
                writable_dirs.append(str(candidate))
    except Exception:
        writable_dirs = []

    await _report("running", f"Executing generated code via {effective_runtime_mode} runtime")
    structured_spec = None
    if execution_spec:
        metadata = dict(execution_spec.get("metadata") or {})
        if resolved_resources:
            metadata["resolved_resources"] = resolved_resources
        structured_spec = CodeExecutionSpec(
            plan_id=execution_spec.get("plan_id"),
            task_id=execution_spec.get("task_id"),
            task_name=execution_spec.get("task_name"),
            task_instruction=execution_spec.get("task_instruction"),
            acceptance_criteria=execution_spec.get("acceptance_criteria"),
            dependency_outputs=list(execution_spec.get("dependency_outputs") or []),
            dependency_artifact_paths=list(execution_spec.get("dependency_artifact_paths") or []),
            metadata=metadata,
        )
    try:
        from app.config.executor_config import get_executor_settings as _get_exec_settings
        _exec_timeout = _get_exec_settings().code_execution_timeout
    except Exception:
        _exec_timeout = 120
    outcome = await execute_code_locally(
        task_title="Code execution task",
        task_description=task_desc,
        metadata_list=[],
        llm_service=_llm_override,
        work_dir=work_dir,
        data_dir=data_dir,
        auto_fix=auto_fix,
        timeout=_exec_timeout,
        execution_backend=execution_backend,
        docker_image=effective_docker_image,
        readable_dirs=readable_dirs,
        writable_dirs=writable_dirs,
        execution_spec=structured_spec,
    )

    if outcome.success:
        await _report("completed", "Code execution succeeded")
    else:
        await _report("failed", f"Execution failed: {outcome.error_category or 'unknown'}")

    produced_files: List[str] = []
    if isinstance(outcome.artifact_verification, dict):
        for item in outcome.artifact_verification.get("actual_outputs") or []:
            if not isinstance(item, str) or not item.strip():
                continue
            candidate = Path(item)
            resolved = candidate if candidate.is_absolute() else (Path(work_dir) / candidate)
            text = str(resolved.resolve())
            if text not in produced_files:
                produced_files.append(text)
    for item in outcome.visualization_files:
        text = str(item or "").strip()
        if text and text not in produced_files:
            produced_files.append(text)

    result: Dict[str, Any] = {
        "success": outcome.success,
        "task": task,
        "stdout": outcome.stdout,
        "stderr": outcome.stderr,
        "exit_code": outcome.exit_code,
        "task_directory_full": work_dir,
        "code_file": outcome.code_file,
        "result": outcome.stdout if outcome.success else (outcome.error_summary or outcome.stderr),
        "generated_code": outcome.code,
        "error_category": outcome.error_category,
        "error_summary": outcome.error_summary,
        "fix_guidance": outcome.fix_guidance,
        "execution_status": outcome.execution_status,
        "verification_status": outcome.verification_status,
        "failure_kind": outcome.failure_kind,
        "contract_diff": outcome.contract_diff,
        "verification": outcome.verification,
        "artifact_verification": outcome.artifact_verification,
        "repair_attempts": outcome.repair_attempts,
        "plan_patch_suggestion": outcome.plan_patch_suggestion,
        "stdout_file": outcome.stdout_file,
        "stderr_file": outcome.stderr_file,
        "execution_mode": f"code_executor_{effective_runtime_mode}",
        "docker_image_effective": effective_docker_image,
        "runtime_failure": outcome.runtime_failure,
        "produced_files": produced_files,
        "produced_files_count": len(produced_files),
    }
    if not outcome.success:
        if outcome.runtime_failure:
            result["error"] = (
                str(outcome.error_summary or "").strip()
                or str(outcome.stderr or "").strip()
                or f"{str(runtime_mode or 'code').capitalize()} runtime failed."
            )
        else:
            result["error"] = (
                str(outcome.error_summary or "").strip()
                or str(outcome.stderr or "").strip()
                or "Code execution failed."
            )

    # Auto-submit visualization files to Deliverables (explicit mode compatible).
    if outcome.success and outcome.visualization_files:
        result["deliverable_submit"] = {
            "publish": True,
            "artifacts": [
                {"path": f, "module": "image_tabular", "reason": "auto-submit from code_executor"}
                for f in outcome.visualization_files
            ],
        }

    return result


def _resolve_promoted_output_files(
    promoted: Sequence[str],
    *,
    session_dir: Path,
    output_dir: Optional[Path] = None,
) -> List[str]:
    """Resolve session-relative promoted entries to absolute on-disk paths.

    ``_promote_results_to_unified_dir`` returns paths relative to the session
    root (e.g. ``raw_files/tmp/<run>/x.png``); they must be rooted at
    ``session_dir``, not at the unified output dir, or the prefix appears
    twice. Entries that are already absolute pass through unchanged.

    When ``output_dir`` is given, entries are first normalised with
    ``_collapse_rooted_rel_path`` so residual double-rooted or partial-mirror
    entries from delegated agents collapse back onto the canonical
    single-prefix location:

    - already-correct ``<prefix>/x.png`` entries pass through unchanged;
    - double-rooted ``<prefix>/<mirror>/x.png`` collapse to ``<prefix>/x.png``;
    - partial mirrors (``raw_files/tmp/x.png``) remap onto ``<prefix>/x.png``;
    - unrelated paths are left untouched.
    """
    prefix: Optional[Path] = None
    if output_dir is not None:
        try:
            prefix = output_dir.resolve().relative_to(session_dir.resolve())
        except (ValueError, OSError):
            prefix = None

    resolved: List[str] = []
    for rel in promoted:
        rel_path = Path(str(rel))
        if rel_path.is_absolute():
            resolved.append(str(rel_path))
            continue
        if prefix is not None and output_dir is not None:
            try:
                remainder: Optional[Path] = rel_path.relative_to(prefix)
            except ValueError:
                remainder = None
            if remainder is not None:
                collapsed_rem = _collapse_rooted_rel_path(
                    rel=remainder, output_dir=output_dir, session_dir=session_dir
                )
                rel_path = prefix / collapsed_rem
            else:
                collapsed = _collapse_rooted_rel_path(
                    rel=rel_path, output_dir=output_dir, session_dir=session_dir
                )
                if collapsed != rel_path:
                    rel_path = prefix / collapsed
        resolved.append(str((session_dir / rel_path).resolve()))
    return resolved


def _build_local_backend_result_payload(
    *,
    task: str,
    local_result: Dict[str, Any],
    resolved_plan_id: Optional[int],
    resolved_task_id: Optional[int],
    require_task_context: bool,
    task_dir_base: Optional[str],
    task_work_dir: Path,
    task_root_dir: Path,
    run_id: str,
    file_prefix: str,
    task_subdirs: Sequence[str],
    session_dir: Path,
    execution_lane: str,
    execution_lane_reason: str,
    log_path: Optional[Path],
    normalized_allowed_tools: Sequence[str],
    code_directory: Optional[str],
    primary_code_file: Optional[str],
    produced_files: Sequence[str],
    verification_artifact_paths: Sequence[str],
    contract_artifacts: Sequence[Dict[str, Any]],
    session_artifact_paths: Sequence[str],
    unified_output_dir: Optional[Path],
    unified_promoted_files: Sequence[str],
    effective_session_id: str,
    ancestor_chain: Optional[Sequence[int]],
    execution_spec: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    success, execution_failure = _classify_execution_success(
        stdout=str(local_result.get("stdout") or ""),
        output_data=local_result.get("output_data"),
        execution_spec=execution_spec,
        produced_files=produced_files,
        success=bool(local_result.get("success", False)),
        task_work_dir=task_work_dir,
    )
    result_payload: Dict[str, Any] = {
        "tool": "code_executor",
        "task": task,
        "plan_id": resolved_plan_id,
        "task_id": resolved_task_id,
        "require_task_context": require_task_context,
        "task_directory": task_dir_base,
        "task_directory_full": str(task_work_dir),
        "task_root_directory": str(task_root_dir),
        "run_directory": str(task_work_dir),
        "run_id": run_id,
        "task_subdirectories": list(task_subdirs),
        "file_prefix": file_prefix,
        "session_directory": str(session_dir),
        "success": success,
        "stdout": str(local_result.get("stdout") or ""),
        "stderr": str(local_result.get("stderr") or ""),
        "exit_code": local_result.get("exit_code", -1),
        "execution_backend": str(
            local_result.get("execution_backend")
            or local_result.get("execution_mode")
            or "local"
        ),
        "execution_mode": str(local_result.get("execution_mode") or "code_executor_host"),
        "execution_lane": execution_lane,
        "execution_lane_reason": execution_lane_reason,
        "working_directory": str(task_work_dir),
        "log_path": str(log_path) if log_path else None,
        "debug_log_path": None,
        "allowed_tools_effective": list(normalized_allowed_tools),
        "claude_model_effective": None,
        "claude_setting_sources_effective": None,
        "claude_auth_mode_effective": None,
        "code_directory": code_directory,
        "code_file": local_result.get("code_file") or primary_code_file,
        "produced_files": list(produced_files),
        "produced_files_count": len(produced_files),
        "artifact_paths": list(verification_artifact_paths),
        "contract_artifacts": list(contract_artifacts),
        "session_artifact_paths": list(session_artifact_paths),
        "output_files": _resolve_promoted_output_files(
            unified_promoted_files, session_dir=session_dir, output_dir=unified_output_dir
        ),
        "output_location": {
            "type": "task" if resolved_task_id is not None else "tmp",
            "session_id": effective_session_id,
            "task_id": resolved_task_id,
            "ancestor_chain": list(ancestor_chain) if ancestor_chain is not None else None,
            "base_dir": str(unified_output_dir) if unified_output_dir else None,
            "files": list(unified_promoted_files),
        },
    }

    if "docker_image_effective" in local_result:
        result_payload["docker_image_effective"] = local_result.get("docker_image_effective")
    if "runtime_failure" in local_result:
        result_payload["runtime_failure"] = bool(local_result.get("runtime_failure"))
    for key in ("error_category", "error_summary", "fix_guidance"):
        if key in local_result and local_result.get(key) is not None:
            result_payload[key] = local_result.get(key)
    for key in (
        "execution_status",
        "verification_status",
        "failure_kind",
        "contract_diff",
        "repair_attempts",
        "plan_patch_suggestion",
    ):
        if key in local_result and local_result.get(key) is not None:
            result_payload[key] = local_result.get(key)
    _apply_execution_failure_to_payload(result_payload, execution_failure)

    code_file = str(local_result.get("code_file") or "").strip()
    if code_file:
        result_payload["code_file"] = code_file
    result_text = str(local_result.get("result") or "").strip()
    if result_text:
        result_payload["result"] = result_text
    if not result_payload.get("success") and not result_payload.get("error"):
        result_payload["error"] = (
            str(local_result.get("error") or "").strip()
            or str(local_result.get("stderr") or "").strip()
            or "Local code execution failed."
        )

    local_completion_info = _detect_partial_completion(
        str(local_result.get("stdout") or ""),
        str(local_result.get("stderr") or ""),
        list(produced_files),
        success=bool(result_payload.get("success")),
    )
    if local_completion_info:
        result_payload.update(local_completion_info)

    return result_payload




def _estimate_cli_prompt_tokens(command: Sequence[str]) -> int:
    total_chars = 0
    for idx, part in enumerate(command):
        if str(part) == "-p" and idx + 1 < len(command):
            total_chars = len(str(command[idx + 1]))
            break
    if total_chars <= 0:
        total_chars = sum(len(str(part)) for part in command)
    return max(1, int(total_chars / 4))


def _estimate_cli_completion_tokens(stdout: str, stderr: str) -> int:
    text = f"{stdout or ''}\n{stderr or ''}".strip()
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def _parse_cli_usage_from_jsonl(stdout: str) -> Optional[Dict[str, int]]:
    if not stdout or not stdout.strip():
        return None
    for line in reversed(stdout.strip().split("\n")):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        if str(event.get("type") or "").lower() != "result":
            continue
        usage = event.get("usage")
        if isinstance(usage, dict):
            prompt = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            completion = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            total = int(usage.get("total_tokens") or (prompt + completion))
            if prompt > 0 or completion > 0:
                return {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": total,
                }
    return None


def _record_external_cli_usage(
    *,
    provider: str,
    model: Optional[str],
    prompt_tokens: int,
    completion_tokens: int,
    session_id: Optional[str],
    plan_id: Optional[int],
    task_id: Optional[int],
    call_purpose: str,
) -> Optional[Dict[str, Any]]:
    try:
        from app.repository.llm_usage import estimate_llm_cost, log_llm_usage
        model_name = str(model or "unknown").strip() or "unknown"
        total_tokens = max(0, int(prompt_tokens or 0)) + max(0, int(completion_tokens or 0))
        cost = estimate_llm_cost(
            provider=provider,
            model=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        log_llm_usage(
            provider=provider,
            model=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            session_id=session_id,
            plan_id=plan_id,
            task_id=task_id,
            call_purpose=call_purpose,
            input_cost=cost["input_cost"],
            output_cost=cost["output_cost"],
            estimated_cost=cost["estimated_cost"],
            cost_currency=cost["cost_currency"],
        )
        return {
            "provider": provider,
            "model": model_name,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            **cost,
        }
    except Exception as exc:
        logger.warning("[CODE_EXECUTOR] Failed to record external CLI usage: %s", exc)
        return None


def _prepare_code_executor_read_context(
    *,
    task: str,
    add_dirs: Optional[Any],
    resolved_resources: Optional[Dict[str, Any]],
    require_task_context: bool,
    execution_spec: Optional[Dict[str, Any]],
    session_dir: Path,
) -> Dict[str, Any]:
    normalized_add_dirs = _normalize_csv_values(add_dirs)
    normalized_resources = _normalize_resolved_resources(resolved_resources)
    resource_add_dirs = _resource_read_dirs(normalized_resources)
    allowed_dirs: List[str] = []
    resolved_add_dirs: List[str] = []

    default_data_dir = _PROJECT_ROOT / "data"
    if default_data_dir.exists():
        allowed_dirs.append(str(default_data_dir))
        logger.info("Auto-added default data directory: %s", default_data_dir)

    for default_external_root in _DEFAULT_EXTERNAL_READ_ROOTS:
        try:
            external_resolved = default_external_root.expanduser().resolve(strict=False)
        except OSError:
            continue
        if external_resolved.exists() and external_resolved.is_dir():
            external_str = str(external_resolved)
            if external_str not in allowed_dirs:
                allowed_dirs.append(external_str)
                logger.info("Auto-added default external readable directory: %s", external_str)

    for raw_extra in os.getenv("FILE_OPERATIONS_ALLOWED_BASE_PATHS", "").split(os.pathsep):
        extra_path = str(raw_extra or "").strip()
        if not extra_path:
            continue
        try:
            extra_resolved = Path(extra_path).expanduser().resolve(strict=False)
        except OSError:
            continue
        if extra_resolved.exists() and extra_resolved.is_dir():
            extra_str = str(extra_resolved)
            if extra_str not in allowed_dirs:
                allowed_dirs.append(extra_str)
                logger.info("Auto-added FILE_OPERATIONS_ALLOWED_BASE_PATHS entry: %s", extra_str)

    if session_dir.exists():
        allowed_dirs.append(str(session_dir))
        logger.info("Auto-added session runtime directory: %s", session_dir)

    for dir_path in normalized_add_dirs:
        candidate_text = str(dir_path or "").strip()
        if not candidate_text:
            continue
        candidate = Path(candidate_text)
        if not candidate.is_absolute():
            candidate = _PROJECT_ROOT / candidate_text
        try:
            lexical = candidate.absolute()
            if not lexical.exists() or not lexical.is_dir():
                logger.warning("Ignoring non-directory add_dir path: %s", lexical)
                continue
        except (OSError, Exception):
            logger.warning("Ignoring invalid add_dir path: %s", candidate_text)
            continue
        if require_task_context and not _is_allowed_task_read_path(lexical, session_dir):
            logger.warning("Ignoring add_dir outside strict task scope: %s", lexical)
            continue
        lexical_str = str(lexical)
        if lexical_str not in allowed_dirs:
            allowed_dirs.append(lexical_str)
        if lexical_str not in resolved_add_dirs:
            resolved_add_dirs.append(lexical_str)

        try:
            resolved = lexical.resolve()
        except OSError:
            resolved = lexical
        resolved_str = str(resolved)
        if resolved != lexical and resolved.exists() and resolved.is_dir():
            if resolved_str not in allowed_dirs:
                allowed_dirs.append(resolved_str)

    for resource_dir in resource_add_dirs:
        if resource_dir not in allowed_dirs:
            allowed_dirs.append(resource_dir)
            logger.info("Auto-added resolved resource directory: %s", resource_dir)
        if resource_dir not in resolved_add_dirs:
            resolved_add_dirs.append(resource_dir)

    inferred_task_dirs = _extract_task_referenced_read_dirs(
        task,
        execution_spec=execution_spec,
        session_dir=session_dir,
    )
    for inferred_dir in inferred_task_dirs:
        if inferred_dir not in allowed_dirs:
            allowed_dirs.append(inferred_dir)
            logger.info("Auto-added task-referenced directory: %s", inferred_dir)

    allowed_dirs_info = ""
    if allowed_dirs:
        allowed_dirs_info = (
            "\n\nExtra readable directories (ABSOLUTE paths):\n"
            + "\n".join(f"  - {directory}" for directory in allowed_dirs)
        )

    local_data_dir: Optional[str] = None
    if len(resolved_add_dirs) == 1:
        local_data_dir = resolved_add_dirs[0]
    elif not resolved_add_dirs and len(inferred_task_dirs) == 1:
        local_data_dir = inferred_task_dirs[0]
    elif not resolved_add_dirs and default_data_dir.exists():
        local_data_dir = str(default_data_dir)

    return {
        "normalized_resources": normalized_resources,
        "allowed_dirs": allowed_dirs,
        "resolved_add_dirs": resolved_add_dirs,
        "allowed_dirs_info": allowed_dirs_info,
        "local_data_dir": local_data_dir,
    }


# Contracts sibling re-exports are placed immediately before consumers so all
# facade bare-name calls and test monkeypatches resolve to the extracted code.
from .code_executor_contracts import (
    _append_contract_artifact_paths,
    _build_ad_hoc_execution_spec,
    _build_cli_contract_repair_task,
    _build_cli_task_contract,
    _build_execution_spec,
    _build_verification_artifact_paths,
    _contract_required_artifact_records,
    _extract_acceptance_criteria_from_node,
    _extract_code_workspace_metadata,
    _extract_task_referenced_read_dirs,
    _final_response_contract_prompt,
    _format_cli_acceptance_checks,
    _format_contract_diff_for_cli,
    _format_resolved_resources_for_prompt,
    _format_supervised_ml_contract_for_prompt,
    _generate_task_dir_name_llm,
    _is_allowed_task_read_path,
    _is_path_within,
    _is_path_within_lexical,
    _is_verification_only_task,
    _normalize_resolved_resources,
    _resource_read_dirs,
    _rerun_update_mode_prompt,
    _sanitize_task_dir_component,
    _summarize_dependency_blockers,
    _validate_scope_contract,
)
from .code_executor_promotion import (
    _build_search_and_generate_prompt,
    _collect_non_semantic_run_files,
    _collect_run_artifacts,
    _collapse_rooted_rel_path,
    _has_hidden_path_component,
    _iter_promotable_run_files,
    _prune_stale_session_root_results,
    _promote_external_contract_artifacts,
    _promote_project_level_strays,
    _promote_results_to_unified_dir,
    _promote_task_results_to_session_root,
    _reconcile_deliverables,
    _recover_files_from_historical_runs,
    _resolve_promoted_output_files,
    _resolve_runtime_session_dir,
    _should_skip_unified_promoted_file,
)


async def code_executor_handler(
    task: str,
    allowed_tools: Optional[Any] = None,
    add_dirs: Optional[Any] = None,
    docker_image: Optional[str] = None,
    skip_permissions: bool = True,
    output_format: str = "json",
    session_id: Optional[str] = None,
    plan_id: Optional[int] = None,
    task_id: Optional[int] = None,
    ancestor_chain: Optional[List[int]] = None,
    model: Optional[str] = None,
    setting_sources: Optional[str] = None,
    auth_mode: Optional[str] = None,
    require_task_context: bool = True,
    on_stdout: Optional[Callable[[str], Awaitable[None]]] = None,
    on_stderr: Optional[Callable[[str], Awaitable[None]]] = None,
    tool_context: Optional[Any] = None,
    auto_fix: bool = True,
    resolved_resources: Optional[Dict[str, Any]] = None,
    execution_backend: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute a task using Claude Code (official CLI) with local file access.
    
    Args:
        task: Task description for Claude to complete
        allowed_tools: Comma-separated list of allowed tools (e.g. "Bash Edit")
        add_dirs: Comma-separated list of additional directories to allow access
        docker_image: Optional Docker image override for local Docker execution.
        skip_permissions: Skip permission checks (recommended for trusted environments)
        output_format: Output format: "text" or "json"
        session_id: Session ID for workspace isolation
        plan_id: Plan ID for workspace isolation
        task_id: Task ID for workspace isolation
        model: Optional explicit Claude model (or env CLAUDE_CODE_MODEL)
        setting_sources: Optional sources for Claude settings loading
        auth_mode: "api_env" (default) or "claude_login"
        require_task_context: Whether to require plan/task binding for strict atomic execution
        on_stdout: Async callback for stdout lines
        on_stderr: Async callback for stderr lines
        
    Returns:
        Dict containing execution results
    """
    selected_backend, execution_lane, execution_lane_reason = (
        _resolve_code_executor_backend(task, backend_override=execution_backend)
        if execution_backend is not None
        else _resolve_code_executor_backend(task)
    )
    use_local_backend = selected_backend == "local"
    use_qwen_code_backend = selected_backend == "qwen_code"

    log_file = None
    log_path = None
    log_lock = asyncio.Lock()
    logger.info(
        "code_executor selected backend=%s lane=%s reason=%s",
        selected_backend,
        execution_lane,
        execution_lane_reason,
    )

    try:
        try:
            resolved_plan_id = _coerce_positive_int(plan_id, field_name="plan_id")
            resolved_task_id = _coerce_positive_int(task_id, field_name="task_id")
        except ValueError as exc:
            return {
                "success": False,
                "error": f"Invalid task context: {exc}",
                "task": task,
            }

        scope_error = _validate_scope_contract(
            plan_id=resolved_plan_id,
            task_id=resolved_task_id,
            require_task_context=require_task_context,
        )
        if scope_error:
            return {
                "success": False,
                "error": scope_error,
                "blocked_by_scope_guardrail": True,
                "blocked_reason": "missing_atomic_context",
                "task": task,
            }

        # Ensure runtime directory exists
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

        try:
            session_dir = _resolve_runtime_session_dir(session_id)
        except ValueError as exc:
            return {
                "success": False,
                "error": f"Invalid session_id: {exc}",
                "task": task,
            }
        _prune_stale_session_root_results(session_dir=session_dir)

        # Keep a stable per-task root and isolate each execution by run_<timestamp>.
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f") + f"_{uuid4().hex[:8]}"

        effective_session_id = session_id or "adhoc"

        # --- Unified output path: determine final output directory via PathRouter ---
        path_router = get_path_router()
        unified_output_dir: Optional[Path] = None
        if resolved_task_id is not None:
            unified_output_dir = path_router.get_task_output_dir(
                effective_session_id, resolved_task_id, ancestor_chain, create=True
            )
        else:
            unified_output_dir = path_router.get_tmp_output_dir(
                effective_session_id, run_id=run_id, create=True
            )

        # Legacy task_dir_base for scratch workspace naming (execution happens here)
        task_dir_base = None
        if resolved_task_id is not None:
            task_dir_base = f"task{resolved_task_id}"
            if resolved_plan_id is not None:
                task_dir_base = f"plan{resolved_plan_id}_{task_dir_base}"
        else:
            task_dir_name = await _generate_task_dir_name_llm(task)
            if resolved_plan_id is not None:
                task_dir_name = f"plan{resolved_plan_id}_{task_dir_name}"
            task_dir_base = task_dir_name

        execution_spec = _build_execution_spec(
            resolved_plan_id,
            resolved_task_id,
            task_text=task,
            session_id=session_id,
        )

        # Scratch workspace: execution happens here, results promoted to unified_output_dir
        scratch_root = session_dir / "_scratch"
        task_root_dir = scratch_root / task_dir_base
        task_root_dir.mkdir(parents=True, exist_ok=True)

        run_dir_name = f"run_{run_id}"
        task_work_dir = task_root_dir / run_dir_name
        task_work_dir.mkdir(parents=True, exist_ok=True)

        task_subdirs = _derive_task_subdirectories(execution_spec)
        for subdir in task_subdirs:
            (task_work_dir / subdir).mkdir(parents=True, exist_ok=True)

        file_prefix = run_dir_name

        logger.info(f"Using task workspace: {task_work_dir}")
        if unified_output_dir:
            logger.info(f"Unified output directory: {unified_output_dir}")

        debug_log_path: Optional[Path] = None

        effective_execution_session_id = str(session_id or "adhoc").strip() or "adhoc"

        try:
            job_id = get_current_job()
            if job_id:
                _LOG_DIR.mkdir(parents=True, exist_ok=True)
                log_path = _LOG_DIR / f"{job_id}.log"
            else:
                log_path = task_work_dir / "results" / f"{file_prefix}_code_executor.log"
            debug_log_path = task_work_dir / "results" / f"{file_prefix}_claude_debug.log"

            log_file = open(log_path, "a", encoding="utf-8")
            log_file.write(f"[{datetime.utcnow().isoformat()}Z] Claude Code started\n")
            log_file.write(f"task: {task}\n")
            log_file.write(f"workspace: {task_work_dir}\n")
            log_file.write(f"debug_log: {debug_log_path}\n")
            log_file.flush()
            log_job_event("info", "Claude Code log file initialized.", {"log_path": str(log_path)})
            log_job_event("info", "Claude Code process starting.", {"workspace": str(task_work_dir)})
        except Exception as log_exc:
            logger.warning(f"Failed to initialize Claude Code log file: {log_exc}")

        # Normalize optional CLI params (supports both string and list inputs)
        normalized_allowed_tools = _resolve_allowed_tools(allowed_tools)
        if not normalized_allowed_tools:
            return {
                "success": False,
                "error": "No allowed tools remain after strict allowlist filtering.",
                "task": task,
            }
        read_context = _prepare_code_executor_read_context(
            task=task,
            add_dirs=add_dirs,
            resolved_resources=resolved_resources,
            require_task_context=require_task_context,
            execution_spec=execution_spec,
            session_dir=session_dir,
        )
        normalized_resources = read_context["normalized_resources"]
        allowed_dirs = read_context["allowed_dirs"]
        allowed_dirs_info = read_context["allowed_dirs_info"]
        local_data_dir = read_context["local_data_dir"]

        cli_task = _build_cli_task_contract(task, execution_spec, normalized_resources)

        if use_local_backend:
            local_result = await _execute_task_locally(
                task=task,
                work_dir=str(task_work_dir),
                data_dir=local_data_dir,
                extra_dirs=allowed_dirs,
                docker_image=docker_image,
                tool_context=tool_context,
                auto_fix=auto_fix,
                session_dir=str(session_dir),
                execution_spec=execution_spec,
                resolved_resources=normalized_resources,
            )
            produced_files = _collect_run_artifacts(
                run_dir=task_work_dir,
                subdirs=task_subdirs,
            )
            code_directory, primary_code_file = _extract_code_workspace_metadata(
                run_dir=task_work_dir,
                produced_files=produced_files,
            )

            # --- Promote results to unified output directory ---
            unified_promoted_files: List[str] = []
            if unified_output_dir:
                unified_promoted_files = _promote_results_to_unified_dir(
                    scratch_dir=task_work_dir,
                    output_dir=unified_output_dir,
                    subdirs=task_subdirs,
                    session_dir=session_dir,
                )

            # Legacy promotion (deprecated - will be removed in Phase 7)
            logger.debug(
                "DEPRECATED: _promote_task_results_to_session_root called for task %s. "
                "This redundant copy will be removed in a future phase.",
                resolved_task_id,
            )
            session_artifact_paths, _skipped_large = _promote_task_results_to_session_root(
                session_dir=session_dir,
                task_work_dir=task_work_dir,
                subdirs=task_subdirs,
            )
            reconcile_report = _reconcile_deliverables(
                execution_spec=execution_spec,
                task_work_dir=task_work_dir,
                unified_output_dir=unified_output_dir,
            )
            if reconcile_report.get("missing"):
                logger.warning(
                    "[CODE_EXECUTOR_LOCAL] %d deliverables still missing after reconciliation: %s",
                    len(reconcile_report["missing"]),
                    reconcile_report["missing"][:5],
                )
            verification_artifact_paths = _build_verification_artifact_paths(
                task_work_dir=task_work_dir,
                subdirs=task_subdirs,
                produced_files=produced_files,
                session_artifact_paths=session_artifact_paths,
                session_dir=session_dir,
            )
            contract_artifacts = _contract_required_artifact_records(
                execution_spec=execution_spec,
                task_work_dir=task_work_dir,
                produced_files=produced_files,
            )
            _append_contract_artifact_paths(verification_artifact_paths, contract_artifacts)
            for skipped_path in _skipped_large:
                if skipped_path not in verification_artifact_paths:
                    verification_artifact_paths.append(skipped_path)
            if unified_output_dir and contract_artifacts:
                _promote_external_contract_artifacts(
                    contract_artifacts=contract_artifacts,
                    task_work_dir=task_work_dir,
                    unified_output_dir=unified_output_dir,
                )
                _promote_project_level_strays(
                    contract_artifacts=contract_artifacts,
                    unified_output_dir=unified_output_dir,
                    project_root=_PROJECT_ROOT,
                )
            result_payload = _build_local_backend_result_payload(
                task=task,
                local_result=local_result,
                resolved_plan_id=resolved_plan_id,
                resolved_task_id=resolved_task_id,
                require_task_context=require_task_context,
                task_dir_base=task_dir_base,
                task_work_dir=task_work_dir,
                task_root_dir=task_root_dir,
                run_id=run_id,
                file_prefix=file_prefix,
                task_subdirs=task_subdirs,
                session_dir=session_dir,
                execution_lane=execution_lane,
                execution_lane_reason=execution_lane_reason,
                log_path=log_path,
                normalized_allowed_tools=normalized_allowed_tools,
                code_directory=code_directory,
                primary_code_file=primary_code_file,
                produced_files=produced_files,
                verification_artifact_paths=verification_artifact_paths,
                contract_artifacts=contract_artifacts,
                session_artifact_paths=session_artifact_paths,
                unified_output_dir=unified_output_dir,
                unified_promoted_files=unified_promoted_files,
                effective_session_id=effective_session_id,
                ancestor_chain=ancestor_chain,
                execution_spec=execution_spec,
            )

            if log_file:
                try:
                    log_file.write(
                        f"[{datetime.utcnow().isoformat()}Z] Local code execution finished\n"
                    )
                    log_file.flush()
                    log_file.close()
                except Exception as log_err:
                    logger.warning("Failed to finalize local code executor log file: %s", log_err)

            return result_payload

        # ---- Build CLI command and environment ----
        _docker_container_name: Optional[str] = None  # set when running inside container
        _qwen_session_id: Optional[str] = None
        _container_execution_lock: Optional[asyncio.Lock] = None
        if use_qwen_code_backend:
            # Qwen Code path
            subprocess_env = _build_qwen_code_subprocess_env(
                model_provider=getattr(tool_context, 'model_provider', None) if tool_context else None
            )
            _ensure_writable_subprocess_cache_env(subprocess_env, task_work_dir)
            _inject_env_mutation_guard(subprocess_env, str(task_work_dir))
            qc_config_error = _validate_qwen_code_config(subprocess_env)
            if qc_config_error:
                return {"success": False, "error": qc_config_error, "task": task}
            _mp = (getattr(tool_context, 'model_provider', None) or {}) if tool_context else {}
            effective_model = (
                str(
                    model
                    or _mp.get("model", "")
                    or os.getenv("QWEN_CODE_MODEL", "")
                    or os.getenv("QWEN_MODEL", "")
                ).strip()
                or None
            )
            _qwen_session_id = _build_qwen_execution_session_id(
                effective_execution_session_id,
                run_id,
            )
            def _rebuild_cli_command(task_override: str) -> List[str]:
                bare_cmd = _build_qwen_code_command(
                    task=task_override,
                    work_dir=str(task_work_dir),
                    file_prefix=file_prefix,
                    output_format=output_format,
                    allowed_tools=normalized_allowed_tools,
                    allowed_dirs=allowed_dirs,
                    model=effective_model,
                    debug=debug_log_path is not None,
                    allowed_dirs_info=allowed_dirs_info,
                    qwen_session_id=_qwen_session_id,
                    task_subdirs=task_subdirs,
                    execution_spec=execution_spec,
                    resolved_resources=normalized_resources,
                )
                # Wrap with docker exec if running inside a container
                if _docker_container_name:
                    from app.services.terminal.docker_pty_backend import (
                        CONTAINER_EXEC_PATH,
                        QWEN_EXECUTABLE,
                    )
                    docker_exec_cmd = [
                        "docker",
                        "exec",
                        "-e",
                        f"PATH={CONTAINER_EXEC_PATH}",
                    ]
                    # The project .env may contain host-local proxy settings such
                    # as 127.0.0.1:7890. Inside the qwen_code container that
                    # address points at the container itself and breaks Node/OpenAI
                    # fetches. Clear proxy variables for the qwen process while
                    # preserving the container's OPENAI_* credentials.
                    for _proxy_key in (
                        "HTTP_PROXY",
                        "HTTPS_PROXY",
                        "ALL_PROXY",
                        "http_proxy",
                        "https_proxy",
                        "all_proxy",
                    ):
                        docker_exec_cmd.extend(["-e", f"{_proxy_key}="])
                    docker_exec_cmd.extend([
                        "-w",
                        str(task_work_dir),
                        _docker_container_name,
                        QWEN_EXECUTABLE,
                    ])
                    return docker_exec_cmd + bare_cmd[1:]
                return bare_cmd
            cmd = _rebuild_cli_command(task)
            _cli_label = "Qwen Code"
            _diag_key = subprocess_env.get("OPENAI_API_KEY", "")
            _diag_url = subprocess_env.get("OPENAI_BASE_URL", "")
            logger.info(
                "[CODE_EXECUTOR_DIAG] backend=qwen_code api_key_len=%d base_url=%s model=%s",
                len(_diag_key), _diag_url, effective_model or "(default)",
            )

            # --- Try Docker container execution for isolation + persistence ---
            try:
                from app.services.terminal.qwen_session_driver import get_qwen_session_driver
                _qc_driver = get_qwen_session_driver()
                _container = await _qc_driver.ensure_container(
                    effective_execution_session_id,
                    host_work_dir=str(task_work_dir),
                    extra_mounts=_build_qwen_container_mounts(
                        task_work_dir=task_work_dir,
                        session_dir=session_dir,
                        allowed_dirs=allowed_dirs,
                    ),
                )
                _docker_container_name = _container
                _container_execution_lock = _qc_driver.get_execution_lock(effective_execution_session_id)
                # Rebuild cmd — _rebuild_cli_command now wraps with docker exec
                cmd = _rebuild_cli_command(task)
                # Env is inside the container; host subprocess only needs docker binary
                subprocess_env = dict(os.environ)
                _ensure_writable_subprocess_cache_env(subprocess_env, task_work_dir)
                _cli_label = "Qwen Code (container)"
                logger.info(
                    "[CODE_EXECUTOR] Using Docker container %s for qwen_code execution",
                    _container,
                )
            except (RuntimeError, OSError, asyncio.TimeoutError) as _docker_err:
                logger.warning(
                    "[CODE_EXECUTOR] Docker container unavailable (%s), "
                    "falling back to host subprocess",
                    _docker_err,
                )
        else:
            # Legacy Claude Code path
            effective_auth_mode = _resolve_auth_mode(auth_mode)
            subprocess_env = _build_code_executor_subprocess_env(effective_auth_mode)
            _ensure_writable_subprocess_cache_env(subprocess_env, task_work_dir)
            _inject_env_mutation_guard(subprocess_env, str(task_work_dir))
            if effective_auth_mode == "api_env":
                api_mode_error = _validate_api_mode_config(subprocess_env)
                if api_mode_error:
                    return {"success": False, "error": api_mode_error, "task": task}
            effective_model = (
                str(
                    model
                    or os.getenv("CLAUDE_CODE_MODEL", "")
                    or os.getenv("CLAUDE_CODE_API_MODEL", "")
                    or subprocess_env.get("ANTHROPIC_MODEL", "")
                ).strip()
                or None
            )
            effective_setting_sources = _resolve_setting_sources(
                setting_sources, auth_mode=effective_auth_mode,
            )
            def _rebuild_cli_command(task_override: str) -> List[str]:
                return _build_claude_code_command(
                    task=task_override,
                    task_work_dir=task_work_dir,
                    file_prefix=file_prefix,
                    output_format=output_format,
                    normalized_allowed_tools=normalized_allowed_tools,
                    allowed_dirs=allowed_dirs,
                    task_subdirs=task_subdirs,
                    execution_spec=execution_spec,
                    resolved_resources=normalized_resources,
                    allowed_dirs_info=allowed_dirs_info,
                    debug_log_path=debug_log_path,
                    effective_model=effective_model,
                    effective_setting_sources=effective_setting_sources,
                    skip_permissions=skip_permissions,
                )
            cmd = _rebuild_cli_command(task)
            _cli_label = "Claude Code"
            _diag_key = subprocess_env.get("ANTHROPIC_API_KEY", "")
            _diag_url = subprocess_env.get("ANTHROPIC_BASE_URL", "")
            _diag_model = subprocess_env.get("ANTHROPIC_MODEL", "")
            _diag_small_fast_model = subprocess_env.get("ANTHROPIC_SMALL_FAST_MODEL", "")
            logger.info(
                "[CODE_EXECUTOR_DIAG] backend=claude_code api_key_len=%d base_url=%s model=%s "
                "small_fast_model=%s auth_mode=%s setting_sources=%s",
                len(_diag_key), _diag_url, _diag_model, _diag_small_fast_model,
                effective_auth_mode, effective_setting_sources,
            )

        logger.info("[CODE_EXECUTOR_CLI] Executing %s in: %s", _cli_label, task_work_dir)

        # Retry logic for transient provider / CLI failures.
        max_cli_retries, cli_retry_base_delay_s = _resolve_cli_retry_policy()
        qwen_shell_recovery: Optional[Dict[str, Any]] = None

        cli_progress = {"last_output_at": 0.0}
        cli_prompt_tokens_accumulated = 0

        async def _record_stream_line(decoded_line: str, lines, callback, stream_name: str):
            try:
                cli_progress["last_output_at"] = asyncio.get_running_loop().time()
            except RuntimeError:
                pass
            lines.append(decoded_line)
            formatted_line = f"[{stream_name}] {decoded_line}" if decoded_line else f"[{stream_name}]"
            if log_file:
                try:
                    async with log_lock:
                        log_file.write(formatted_line + "\n")
                        log_file.flush()
                except Exception as log_err:
                    logger.warning(f"Failed to write Claude Code log line: {log_err}")
            if callback:
                try:
                    capped_line = formatted_line
                    if len(capped_line) > 4000:
                        capped_line = capped_line[:3997] + "..."
                    await callback(capped_line)
                except Exception as e:
                    logger.error(f"Error in stream callback: {e}")

        async def _read_stream(stream, lines, callback, stream_name: str):
            async for decoded_line in _iter_stream_lines_unbounded(stream):
                await _record_stream_line(decoded_line, lines, callback, stream_name)

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        return_code = -1

        @asynccontextmanager
        async def _maybe_hold_execution_lock():
            if _container_execution_lock is None:
                yield
                return
            async with _container_execution_lock:
                yield

        async def _run_cli_with_retry(
            task_override: str,
            *,
            phase: str = "primary",
        ) -> tuple[int, str, str, Optional[Dict[str, Any]]]:
            nonlocal _qwen_session_id, qwen_shell_recovery, cli_prompt_tokens_accumulated
            local_stdout_lines: list[str] = []
            local_stderr_lines: list[str] = []
            local_return_code = -1

            for _attempt in range(1, max_cli_retries + 2):
                if use_qwen_code_backend:
                    if _attempt == 1:
                        _qwen_session_id = _build_qwen_execution_session_id(
                            effective_execution_session_id,
                            run_id,
                            phase=phase,
                        )
                    command = _rebuild_cli_command(task_override)
                else:
                    command = _rebuild_cli_command(task_override)
                local_stdout_lines.clear()
                local_stderr_lines.clear()
                if use_qwen_code_backend:
                    cli_prompt_tokens_accumulated += _estimate_cli_prompt_tokens(command)

                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(task_work_dir),
                    env=subprocess_env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    cli_progress["last_output_at"] = asyncio.get_running_loop().time()
                except RuntimeError:
                    pass

                stdout_task = asyncio.create_task(
                    _read_stream(process.stdout, local_stdout_lines, on_stdout, "stdout")
                )
                stderr_task = asyncio.create_task(
                    _read_stream(process.stderr, local_stderr_lines, on_stderr, "stderr")
                )

                try:
                    drain_task = asyncio.gather(stdout_task, stderr_task)
                    grace_deadline: Optional[float] = None
                    can_finish_from_contract_outputs = (
                        use_qwen_code_backend
                        and isinstance(execution_spec, dict)
                        and bool(execution_spec.get("acceptance_criteria"))
                    )
                    if use_qwen_code_backend:
                        return_code_override = await _wait_for_qwen_cli_drain_or_watchdog(
                            process=process,
                            drain_task=drain_task,
                            stdout_task=stdout_task,
                            stderr_task=stderr_task,
                            local_stdout_lines=local_stdout_lines,
                            local_stderr_lines=local_stderr_lines,
                            cli_progress=cli_progress,
                            can_finish_from_contract_outputs=can_finish_from_contract_outputs,
                            execution_spec=execution_spec,
                            task_work_dir=task_work_dir,
                            unified_output_dir=unified_output_dir,
                            cli_label=_cli_label,
                            container_name=_docker_container_name,
                            log_file=log_file,
                            log_lock=log_lock,
                        )
                        if return_code_override is not None:
                            local_return_code = return_code_override
                    else:
                        await drain_task
                    if local_return_code != 0:
                        local_return_code = await _wait_for_cli_process_return_code(
                            process,
                            backend_label=_cli_label,
                        )
                except (asyncio.CancelledError, Exception) as _wait_exc:
                    try:
                        process.kill()
                        await asyncio.wait_for(process.wait(), timeout=10.0)
                    except Exception:
                        pass
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
                            timeout=10.0,
                        )
                    except asyncio.TimeoutError:
                        stdout_task.cancel()
                        stderr_task.cancel()
                        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                    if isinstance(_wait_exc, asyncio.CancelledError):
                        raise
                    raise

                if local_return_code == 0:
                    break

                _is_scope_block = _detect_scope_blocked("\n".join(local_stdout_lines), None)
                if _is_scope_block:
                    logger.info("[CODE_EXECUTOR_RETRY] Scope block detected, not retrying.")
                    break

                if use_qwen_code_backend and _is_qwen_truncated_tool_failure_text("\n".join(local_stderr_lines)):
                    logger.warning(
                        "[CODE_EXECUTOR_RETRY] Qwen truncated tool call detected; "
                        "skipping transcript shell recovery and retries."
                    )
                    break

                if use_qwen_code_backend and not _is_qwen_no_output_timeout("\n".join(local_stderr_lines)):
                    qwen_shell_recovery = await _recover_pending_qwen_shell_call(
                        qwen_session_id=_qwen_session_id,
                        container_name=_docker_container_name,
                        task_work_dir=str(task_work_dir),
                    )
                    if isinstance(qwen_shell_recovery, dict):
                        logger.warning(
                            "[CODE_EXECUTOR] Replaying pending qwen run_shell_command after CLI exit "
                            "(session=%s run=%s phase=%s attempt=%d)",
                            effective_execution_session_id,
                            run_id,
                            phase,
                            _attempt,
                        )
                        if log_file:
                            try:
                                async with log_lock:
                                    log_file.write(
                                        f"[{datetime.utcnow().isoformat()}Z] Replaying pending qwen shell call "
                                        f"(attempt={_attempt}, timeout_ms={qwen_shell_recovery.get('timeout_ms')})\n"
                                    )
                                    log_file.write(
                                        f"[recovery] command={qwen_shell_recovery.get('command')}\n"
                                    )
                                    log_file.flush()
                            except Exception:
                                pass
                        local_return_code = int(qwen_shell_recovery.get("exit_code") or 0)
                        local_stdout_lines.clear()
                        local_stderr_lines.clear()
                        recovered_stdout = str(qwen_shell_recovery.get("stdout") or "")
                        recovered_stderr = str(qwen_shell_recovery.get("stderr") or "")
                        for decoded_line in recovered_stdout.splitlines():
                            await _record_stream_line(decoded_line, local_stdout_lines, on_stdout, "stdout")
                        for decoded_line in recovered_stderr.splitlines():
                            await _record_stream_line(decoded_line, local_stderr_lines, on_stderr, "stderr")
                        break

                if _is_qwen_no_output_timeout("\n".join(local_stderr_lines)):
                    logger.warning(
                        "[CODE_EXECUTOR_RETRY] Qwen no-output watchdog fired; "
                        "skipping transcript recovery and retries."
                    )
                    break

                if _attempt <= max_cli_retries:
                    stderr_text = "\n".join(local_stderr_lines)
                    if use_qwen_code_backend and _is_qwen_session_in_use_error(stderr_text):
                        _qwen_session_id = _build_qwen_execution_session_id(
                            effective_execution_session_id,
                            run_id,
                            phase=phase,
                            retry_attempt=_attempt,
                        )
                        logger.warning(
                            "[CODE_EXECUTOR_RETRY] Rotating qwen session-id after in-use conflict "
                            "(session=%s run=%s phase=%s attempt=%d)",
                            effective_execution_session_id,
                            run_id,
                            phase,
                            _attempt,
                        )
                    retry_delay_s = min(cli_retry_base_delay_s * (2 ** (_attempt - 1)), 30.0)
                    logger.warning(
                        "[CODE_EXECUTOR_RETRY] CLI failed (attempt %d/%d, exit=%d). "
                        "Retrying in %.1fs... stderr_hint=%s",
                        _attempt, max_cli_retries + 1, local_return_code,
                        retry_delay_s, _extract_readable_error(stderr_text)[:200],
                    )
                    if log_file:
                        try:
                            log_file.write(
                                f"[{datetime.utcnow().isoformat()}Z] Retry {_attempt}/{max_cli_retries} "
                                f"after exit={local_return_code}, waiting {retry_delay_s:.1f}s\n"
                            )
                            log_file.flush()
                        except Exception:
                            pass
                    await asyncio.sleep(retry_delay_s)
                else:
                    logger.error(
                        "[CODE_EXECUTOR_RETRY] CLI failed after %d attempts (exit=%d).",
                        _attempt, local_return_code,
                    )

            local_stdout = "\n".join(local_stdout_lines)
            local_stderr = "\n".join(local_stderr_lines)
            local_output_data = None
            if output_format == "json" and local_stdout:
                try:
                    local_output_data = json.loads(local_stdout)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse JSON output, using raw text")
                    local_output_data = {"raw_output": local_stdout}
            return local_return_code, local_stdout, local_stderr, local_output_data

        async with _maybe_hold_execution_lock():
            return_code, stdout, stderr, output_data = await _run_cli_with_retry(task)

            success = return_code == 0
            is_no_output_timeout = _is_qwen_no_output_timeout(stderr, stdout)
            is_qwen_truncated_tool_failure = _is_qwen_truncated_tool_failure_text(f"{stderr}\n{stdout}")

            blocked_detail = _detect_scope_blocked(stdout, output_data)
            if blocked_detail:
                success = False

            qwen_infra_failure_detected = (
                use_qwen_code_backend
                and not success
                and (
                    _is_qwen_container_infrastructure_error(stderr, stdout)
                    or is_qwen_truncated_tool_failure
                )
            )
            qwen_infra_fallback_used = False
            fallback_result: Optional[Dict[str, Any]] = None
            if _should_fallback_from_qwen_infra_failure(
                use_qwen_code_backend=use_qwen_code_backend,
                success=success,
                stderr=stderr,
                stdout=stdout,
                execution_lane=execution_lane,
                execution_lane_reason=execution_lane_reason,
            ):
                logger.warning(
                    "[CODE_EXECUTOR_FALLBACK] qwen_code infrastructure failure detected; "
                    "retrying task with local executor (session=%s run=%s)",
                    effective_execution_session_id,
                    run_id,
                )
                if log_file:
                    try:
                        async with log_lock:
                            log_file.write(
                                f"[{datetime.utcnow().isoformat()}Z] "
                                "qwen_code infrastructure failure detected; retrying with local executor\n"
                            )
                            log_file.flush()
                    except Exception:
                        pass
                fallback_result = await _execute_task_locally(
                    task=task,
                    work_dir=str(task_work_dir),
                    data_dir=local_data_dir,
                    extra_dirs=allowed_dirs,
                    docker_image=docker_image,
                    tool_context=tool_context,
                    auto_fix=auto_fix,
                    session_dir=str(session_dir),
                    execution_spec=execution_spec,
                    resolved_resources=normalized_resources,
                )
                fallback_stdout = str(fallback_result.get("stdout") or "")
                fallback_stderr = str(fallback_result.get("stderr") or "")
                stdout = (stdout + "\n" if stdout else "") + "[fallback:local] " + fallback_stdout
                stderr = (stderr + "\n" if stderr else "") + fallback_stderr
                return_code = int(fallback_result.get("exit_code", 0 if fallback_result.get("success") else 1) or 0)
                success = bool(fallback_result.get("success", False))
                qwen_infra_fallback_used = True
                output_data = fallback_result.get("output_data") or output_data
                blocked_detail = _detect_scope_blocked(stdout, output_data)
                if blocked_detail:
                    success = False

            produced_files = _collect_run_artifacts(run_dir=task_work_dir, subdirs=task_subdirs)
            success, execution_failure = _classify_execution_success(
                stdout=stdout,
                output_data=output_data,
                execution_spec=execution_spec,
                produced_files=produced_files,
                success=success,
                task_work_dir=task_work_dir,
            )

            if (
                execution_failure
                and execution_failure.get("failure_kind") == "missing_required_outputs"
                and task_root_dir.exists()
            ):
                recovered = _recover_files_from_historical_runs(
                    task_root_dir=task_root_dir,
                    current_run_dir=task_work_dir,
                    execution_spec=execution_spec,
                    task_subdirs=task_subdirs,
                )
                if recovered:
                    logger.info(
                        "[CODE_EXECUTOR] Recovered %d files from historical runs, re-verifying",
                        len(recovered),
                    )
                    produced_files = _collect_run_artifacts(run_dir=task_work_dir, subdirs=task_subdirs)
                    success, execution_failure = _classify_execution_success(
                        stdout=stdout,
                        output_data=output_data,
                        execution_spec=execution_spec,
                        produced_files=produced_files,
                        success=success,
                        task_work_dir=task_work_dir,
                    )
                    if success:
                        logger.info("[CODE_EXECUTOR] Verification passed after historical file recovery")

            # --- Promote results to unified output directory ---
            unified_promoted_files_qwen: List[str] = []
            if unified_output_dir:
                unified_promoted_files_qwen = _promote_results_to_unified_dir(
                    scratch_dir=task_work_dir,
                    output_dir=unified_output_dir,
                    subdirs=task_subdirs,
                    session_dir=session_dir,
                )

            session_artifact_paths, _skipped_large = _promote_task_results_to_session_root(
                session_dir=session_dir,
                task_work_dir=task_work_dir,
                subdirs=task_subdirs,
            )
            reconcile_report = _reconcile_deliverables(
                execution_spec=execution_spec,
                task_work_dir=task_work_dir,
                unified_output_dir=unified_output_dir,
            )
            if reconcile_report.get("missing"):
                sg_prompt = _build_search_and_generate_prompt(
                    reconcile_report["missing"],
                    session_dir,
                    execution_spec,
                    is_timeout=is_no_output_timeout or is_qwen_truncated_tool_failure,
                )
                logger.info(
                    "[CODE_EXECUTOR] %d missing deliverables, delegating to Qwen Code for %s",
                    len(reconcile_report["missing"]),
                    "re-execution (previous timeout/fatal CLI failure)" if (is_no_output_timeout or is_qwen_truncated_tool_failure) else "search",
                )
                try:
                    sg_rc, sg_stdout, sg_stderr, sg_output = await asyncio.wait_for(
                        _run_cli_with_retry(
                            sg_prompt, phase="search_generate",
                        ),
                        timeout=300.0,
                    )
                    produced_files = _collect_run_artifacts(run_dir=task_work_dir, subdirs=task_subdirs)
                    if unified_output_dir:
                        unified_promoted_files_qwen = _promote_results_to_unified_dir(
                            scratch_dir=task_work_dir,
                            output_dir=unified_output_dir,
                            subdirs=task_subdirs,
                            session_dir=session_dir,
                        )
                    session_artifact_paths, _skipped_large = _promote_task_results_to_session_root(
                        session_dir=session_dir,
                        task_work_dir=task_work_dir,
                        subdirs=task_subdirs,
                    )
                    reconcile_report = _reconcile_deliverables(
                        execution_spec=execution_spec,
                        task_work_dir=task_work_dir,
                        unified_output_dir=unified_output_dir,
                    )
                    logger.info(
                        "[CODE_EXECUTOR] %s complete: rc=%d still_missing=%d",
                        "re-execution" if is_no_output_timeout else "search",
                        sg_rc,
                        len(reconcile_report.get("missing", [])),
                    )
                except asyncio.TimeoutError:
                    logger.warning("[CODE_EXECUTOR] search/generate delegation timed out after 300s")
                except Exception as sg_exc:
                    logger.warning("[CODE_EXECUTOR] search/generate delegation failed: %s", sg_exc)
            verification_artifact_paths = _build_verification_artifact_paths(
                task_work_dir=task_work_dir,
                subdirs=task_subdirs,
                produced_files=produced_files,
                session_artifact_paths=session_artifact_paths,
                session_dir=session_dir,
            )
            contract_artifacts = _contract_required_artifact_records(
                execution_spec=execution_spec,
                task_work_dir=task_work_dir,
                produced_files=produced_files,
            )
            _append_contract_artifact_paths(verification_artifact_paths, contract_artifacts)
            for skipped_path in _skipped_large:
                if skipped_path not in verification_artifact_paths:
                    verification_artifact_paths.append(skipped_path)
            code_directory, primary_code_file = _extract_code_workspace_metadata(
                run_dir=task_work_dir,
                produced_files=produced_files,
            )
            execution_status = "completed" if return_code == 0 else "failed"
            verification_status: Optional[str] = None
            failure_kind: Optional[str] = None
            contract_diff: Optional[Dict[str, Any]] = None
            verification: Optional[Dict[str, Any]] = None
            repair_attempts = 0
            plan_patch_suggestion: Optional[str] = None
            contract_error_summary: Optional[str] = None
            contract_fix_guidance: Optional[str] = None

            if success and execution_spec and execution_spec.get("acceptance_criteria"):
                try:
                    from app.services.interpreter.code_execution import (
                        CodeExecutionSpec,
                        _extract_verification_state,
                        _format_verification_guidance,
                        _summarize_verification_failures,
                        _verify_execution_against_contract,
                    )

                    finalization = _verify_execution_against_contract(
                        execution_spec=CodeExecutionSpec(
                            plan_id=execution_spec.get("plan_id"),
                            task_id=execution_spec.get("task_id"),
                            task_name=execution_spec.get("task_name"),
                            task_instruction=execution_spec.get("task_instruction"),
                            acceptance_criteria=execution_spec.get("acceptance_criteria"),
                            dependency_outputs=list(execution_spec.get("dependency_outputs") or []),
                            dependency_artifact_paths=list(execution_spec.get("dependency_artifact_paths") or []),
                        ),
                        work_dir=str(task_work_dir),
                    )
                    verification, verification_status, failure_kind, contract_diff, plan_patch_suggestion = (
                        _extract_verification_state(finalization)
                    )
                    if finalization.final_status == "failed" and auto_fix:
                        repair_attempts = 1
                        contract_error_summary = _summarize_verification_failures(verification)
                        contract_fix_guidance = _format_verification_guidance(verification)
                        repair_task = _build_cli_contract_repair_task(
                            task,
                            execution_spec,
                            contract_diff=contract_diff,
                            guidance=contract_fix_guidance,
                        )
                        return_code, stdout, stderr, output_data = await _run_cli_with_retry(
                            repair_task,
                            phase="repair",
                        )
                        success = return_code == 0
                        execution_status = "completed" if return_code == 0 else "failed"
                        blocked_detail = _detect_scope_blocked(stdout, output_data)
                        if blocked_detail:
                            success = False
                        produced_files = _collect_run_artifacts(run_dir=task_work_dir, subdirs=task_subdirs)
                        # Re-promote to unified output dir after repair
                        if unified_output_dir:
                            unified_promoted_files_qwen = _promote_results_to_unified_dir(
                                scratch_dir=task_work_dir,
                                output_dir=unified_output_dir,
                                subdirs=task_subdirs,
                                session_dir=session_dir,
                            )
                        session_artifact_paths, _skipped_large = _promote_task_results_to_session_root(
                            session_dir=session_dir,
                            task_work_dir=task_work_dir,
                            subdirs=task_subdirs,
                        )
                        _reconcile_deliverables(
                            execution_spec=execution_spec,
                            task_work_dir=task_work_dir,
                            unified_output_dir=unified_output_dir,
                        )
                        verification_artifact_paths = _build_verification_artifact_paths(
                            task_work_dir=task_work_dir,
                            subdirs=task_subdirs,
                            produced_files=produced_files,
                            session_artifact_paths=session_artifact_paths,
                            session_dir=session_dir,
                        )
                        contract_artifacts = _contract_required_artifact_records(
                            execution_spec=execution_spec,
                            task_work_dir=task_work_dir,
                            produced_files=produced_files,
                        )
                        _append_contract_artifact_paths(verification_artifact_paths, contract_artifacts)
                        for skipped_path in _skipped_large:
                            if skipped_path not in verification_artifact_paths:
                                verification_artifact_paths.append(skipped_path)
                        success, execution_failure = _classify_execution_success(
                            stdout=stdout,
                            output_data=output_data,
                            execution_spec=execution_spec,
                            produced_files=produced_files,
                            success=success,
                            task_work_dir=task_work_dir,
                        )
                        if success:
                            finalization = _verify_execution_against_contract(
                                execution_spec=CodeExecutionSpec(
                                    plan_id=execution_spec.get("plan_id"),
                                    task_id=execution_spec.get("task_id"),
                                    task_name=execution_spec.get("task_name"),
                                    task_instruction=execution_spec.get("task_instruction"),
                                    acceptance_criteria=execution_spec.get("acceptance_criteria"),
                                    dependency_outputs=list(execution_spec.get("dependency_outputs") or []),
                                    dependency_artifact_paths=list(execution_spec.get("dependency_artifact_paths") or []),
                                ),
                                work_dir=str(task_work_dir),
                            )
                            verification, verification_status, failure_kind, contract_diff, plan_patch_suggestion = (
                                _extract_verification_state(finalization)
                            )
                            artifact_summary = verification.get("artifact_verification") if isinstance(verification, dict) else {}
                            unresolved_required_outputs = (
                                artifact_summary.get("missing_required_outputs")
                                if isinstance(artifact_summary, dict)
                                else []
                            )
                            if (
                                finalization.final_status == "failed"
                                or verification_status == "failed"
                                or bool(unresolved_required_outputs)
                            ):
                                success = False
                                verification_payload = verification if isinstance(verification, dict) else {}
                                contract_error_summary = _summarize_verification_failures(verification_payload)
                                contract_fix_guidance = _format_verification_guidance(verification_payload)
                        else:
                            verification_status = "not_run"
                            failure_kind = "execution_failed"
                            contract_diff = None
                            verification = None
                            plan_patch_suggestion = None
                            contract_error_summary = None
                            contract_fix_guidance = None
                        if not success and not blocked_detail and contract_error_summary is None and verification_status == "failed":
                            verification_payload = verification if isinstance(verification, dict) else {}
                            contract_error_summary = _summarize_verification_failures(verification_payload)
                            contract_fix_guidance = _format_verification_guidance(verification_payload)
                    elif finalization.final_status == "failed":
                        success = False
                        verification_payload = verification if isinstance(verification, dict) else {}
                        contract_error_summary = _summarize_verification_failures(verification_payload)
                        contract_fix_guidance = _format_verification_guidance(verification_payload)
                except Exception as contract_exc:
                    logger.warning("CLI contract verification failed unexpectedly: %s", contract_exc)

            contract_error_summary, contract_fix_guidance = _clear_stale_contract_failure_state(
                success=success,
                verification_status=verification_status,
                contract_error_summary=contract_error_summary,
                contract_fix_guidance=contract_fix_guidance,
            )

        if unified_output_dir and contract_artifacts:
            _promote_external_contract_artifacts(
                contract_artifacts=contract_artifacts,
                task_work_dir=task_work_dir,
                unified_output_dir=unified_output_dir,
            )

        if log_file:
            try:
                log_file.write(f"[{datetime.utcnow().isoformat()}Z] Claude Code finished (exit={return_code})\n")
                log_file.flush()
            except Exception as log_err:
                logger.warning(f"Failed to finalize Claude Code log file: {log_err}")

        cli_usage = None
        if use_qwen_code_backend:
            real_usage = _parse_cli_usage_from_jsonl(stdout)
            cli_usage = _record_external_cli_usage(
                provider="qwen_code_cli",
                model=effective_model or os.getenv("QWEN_CODE_MODEL") or os.getenv("QWEN_MODEL") or "unknown",
                prompt_tokens=real_usage["prompt_tokens"] if real_usage else cli_prompt_tokens_accumulated,
                completion_tokens=real_usage["completion_tokens"] if real_usage else _estimate_cli_completion_tokens(stdout, stderr),
                session_id=effective_session_id,
                plan_id=resolved_plan_id,
                task_id=resolved_task_id,
                call_purpose="qwen_code_cli_execution",
            )

        # Build return result
        result_payload = {
            "tool": "code_executor",
            "task": task,
            "plan_id": resolved_plan_id,
            "task_id": resolved_task_id,
            "require_task_context": require_task_context,
            "task_directory": task_dir_base,
            "task_directory_full": str(task_work_dir),
            "task_root_directory": str(task_root_dir),
            "run_directory": str(task_work_dir),
            "run_id": run_id,
            "task_subdirectories": task_subdirs,
            "file_prefix": file_prefix,
            "session_directory": str(session_dir),
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "output_data": output_data,
            "exit_code": return_code,
            "execution_backend": "qwen_code" if use_qwen_code_backend else "claude_code",
            "execution_mode": "code_executor_local",
            "execution_lane": execution_lane,
            "execution_lane_reason": execution_lane_reason,
            "working_directory": str(task_work_dir),
            "log_path": str(log_path) if log_path else None,
            "debug_log_path": str(debug_log_path) if debug_log_path else None,
            "allowed_tools_effective": normalized_allowed_tools,
            "cli_model_effective": effective_model,
            "cli_backend": "qwen_code" if use_qwen_code_backend else "claude_code",
            "cli_usage": cli_usage,
            "code_directory": code_directory,
            "code_file": primary_code_file,
            "produced_files": produced_files,
            "produced_files_count": len(produced_files),
            "artifact_paths": verification_artifact_paths,
            "contract_artifacts": contract_artifacts,
            "session_artifact_paths": session_artifact_paths,
            "output_files": _resolve_promoted_output_files(
                unified_promoted_files_qwen, session_dir=session_dir, output_dir=unified_output_dir
            ),
            # Unified output path (new)
            "output_location": {
                "type": "task" if resolved_task_id is not None else "tmp",
                "session_id": effective_session_id,
                "task_id": resolved_task_id,
                "ancestor_chain": ancestor_chain,
                "base_dir": str(unified_output_dir) if unified_output_dir else None,
                "files": unified_promoted_files_qwen,
            },
            "execution_status": execution_status,
                "verification_status": verification_status,
                "failure_kind": failure_kind,
                "contract_diff": contract_diff,
                "verification": verification,
                "artifact_verification": (
                    verification.get("artifact_verification")
                    if isinstance(verification, dict)
                    else None
                ),
                "repair_attempts": repair_attempts,
                "plan_patch_suggestion": plan_patch_suggestion,
            }
        if use_qwen_code_backend and success:
            extracted_result = _extract_result_from_jsonl(stdout)
            if extracted_result:
                result_payload["result"] = extracted_result
            deliverables = _extract_deliverables_from_jsonl(stdout)
            if deliverables:
                result_payload["deliverable_submit"] = {
                    "publish": True,
                    "artifacts": [
                        {
                            "path": d["path"],
                            "module": d["module"],
                            "reason": d.get("description") or "Agent-marked deliverable",
                        }
                        for d in deliverables
                    ],
                }
        if qwen_infra_failure_detected and not qwen_infra_fallback_used:
            result_payload["runtime_failure"] = True
            result_payload["error_category"] = "executor_infrastructure"
            if is_qwen_truncated_tool_failure:
                result_payload["failure_kind"] = result_payload.get("failure_kind") or "qwen_tool_call_truncated"
                result_payload["error_summary"] = (
                    "Qwen Code produced a truncated tool call; retry with split file writes or smaller scripts."
                )
                result_payload["fix_guidance"] = (
                    "Ask Qwen Code to write large scripts in smaller chunks: first create a skeleton, "
                    "then use incremental edits/appends instead of one huge write_file call."
                )
            else:
                result_payload["error_summary"] = _extract_readable_error(stderr) or "qwen_code container infrastructure failure"
        if qwen_infra_fallback_used:
            result_payload["fallback_used"] = True
            fallback_payload = fallback_result or {}
            result_payload["fallback_backend"] = str(fallback_payload.get("execution_backend") or fallback_payload.get("execution_mode") or "local")
            result_payload["fallback_reason"] = "qwen_code_container_infrastructure_failure"
            for key in ("docker_image_effective", "runtime_failure", "error_category", "error_summary", "fix_guidance"):
                if key in fallback_payload and fallback_payload.get(key) is not None and key not in result_payload:
                    result_payload[key] = fallback_payload.get(key)
        if contract_error_summary:
            result_payload["error_category"] = "acceptance_criteria_failed"
            result_payload["error_summary"] = contract_error_summary
            if contract_fix_guidance:
                result_payload["fix_guidance"] = contract_fix_guidance
        if contract_error_summary and not blocked_detail:
            result_payload["error"] = contract_error_summary
        elif not success and not blocked_detail:
            result_payload["error"] = _build_cli_failure_error(
                return_code=return_code,
                stderr=stderr,
                stdout=stdout,
                backend_label=_cli_label,
            )
        _apply_execution_failure_to_payload(result_payload, execution_failure)
        if blocked_detail:
            result_payload["blocked_by_scope_guardrail"] = True
            result_payload["blocked_reason"] = blocked_detail
            result_payload["error"] = f"Blocked by scope guardrail: {blocked_detail}"

        # Detect partial completion signals even when exit_code==0
        completion_info = _detect_partial_completion(
            stdout, stderr, produced_files, success=success,
        )
        if completion_info:
            result_payload.update(completion_info)
            if completion_info.get("partial_completion_suspected"):
                logger.warning(
                    "[CODE_EXECUTOR] Partial completion suspected: ratio=%s warnings=%d files=%d",
                    completion_info.get("partial_ratio", "N/A"),
                    len(completion_info.get("output_warnings", [])),
                    len(produced_files),
                )

        return result_payload

    except subprocess.TimeoutExpired:
        # Should not trigger since timeout=None, but kept as a safeguard
        return {
            "success": False,
            "error": "Code agent CLI execution was interrupted unexpectedly",
            "task": task,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": "Code agent CLI (claude/qwen) not found. Install claude-code or qwen-code.",
            "task": task,
        }
    except Exception as e:
        logger.exception(f"Code agent CLI execution failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "task": task,
        }
    finally:
        if log_file:
            try:
                log_file.flush()
                log_file.close()
            except Exception:
                pass


# ToolBox tool definition
code_executor_tool = {
    "name": "code_executor",
    "description": (
        "**PRIMARY TOOL FOR COMPLEX CODING TASKS** - Execute one atomic implementation task using Claude Code. "
        "The runtime enforces a strict tool allowlist and task-scoped workspace isolation. "
        "Use this for data analysis, code generation, model implementation, debugging, and multi-step engineering execution."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Detailed task description for Claude to complete"
            },
            "allowed_tools": {
                "type": "string",
                "description": "Optional comma-separated allowlist request (e.g. 'Bash,Edit'). Values are always filtered by the hard allowlist."
            },
            "add_dirs": {
                "type": "string",
                "description": "Comma-separated list of additional directories to allow access (e.g. 'data/code_task,models')"
            },
        },
        "required": ["task"]
    },
    "handler": code_executor_handler,
}
