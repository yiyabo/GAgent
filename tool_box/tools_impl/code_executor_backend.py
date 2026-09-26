"""Backend configuration and local execution helpers for ``code_executor``.

This module is an implementation sibling of the public facade. Calls that
participate in the facade monkeypatch contract resolve through ``_ce()`` at
call time; existing lazy imports remain inside their original functions.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.config.executor_config import (
    DEFAULT_CODE_EXECUTION_DOCKER_IMAGE,
    DEFAULT_CODE_EXECUTION_LOCAL_RUNTIME,
    resolve_code_execution_docker_image,
    resolve_code_execution_local_runtime,
)
from .code_executor_cli_parse import _format_directory_choices, _format_task_subdirectories
from .code_executor_semantic import (
    _BLOCK_SCOPE_REASON,
    _BLOCK_SCOPE_STATUS,
    _normalize_csv_values,
)
from .code_executor_qwen import _is_qwen_truncated_tool_failure_text

logger = logging.getLogger(__name__)


def _ce():
    from tool_box.tools_impl import code_executor

    return code_executor


_DEFAULT_CODE_EXECUTOR_LOCAL_RUNTIME = DEFAULT_CODE_EXECUTION_LOCAL_RUNTIME
_DEFAULT_CODE_EXECUTOR_DOCKER_IMAGE = DEFAULT_CODE_EXECUTION_DOCKER_IMAGE
_DEFAULT_SETTING_SOURCES = "project,local"
_DEFAULT_API_SETTING_SOURCES = "project"
_DEFAULT_AUTH_MODE = "api_env"
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
_HARD_ALLOWED_TOOL_NAMES: Sequence[str] = (
    "Bash", "BashOutput", "Edit", "MultiEdit", "Read", "Write", "Glob",
    "Grep", "LS", "NotebookRead", "NotebookEdit",
)
_DEFAULT_ALLOWED_TOOL_NAMES: Sequence[str] = (
    "Bash", "BashOutput", "Edit", "MultiEdit", "Read", "Write", "Glob",
    "Grep", "LS",
)
_HARD_ALLOWED_TOOL_MAP = {name.lower(): name for name in _HARD_ALLOWED_TOOL_NAMES}
_DEFAULT_EXTERNAL_READ_ROOTS: Sequence[Path] = (Path("/mnt/sdm/zczhao"),)

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
    env_map.pop("CLAUDECODE", None)
    if auth_mode == "claude_login":
        for key in _CLAUDE_ENV_KEYS_FOR_LOGIN_MODE:
            env_map.pop(key, None)
    elif auth_mode == "api_env":
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


def _build_qwen_code_subprocess_env(model_provider: Optional[Dict] = None) -> Dict[str, str]:
    """Build a subprocess environment for Qwen Code CLI."""
    env_map = dict(os.environ)
    pi_shim_dir = os.getenv("PI_SHIM_DIR", "/app/data/tools/pi-shim")
    if os.path.isdir(pi_shim_dir):
        env_map["PATH"] = pi_shim_dir + os.pathsep + env_map.get("PATH", "")
    for _proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env_map.pop(_proxy_key, None)
    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    if conda_prefix:
        conda_bin = os.path.join(conda_prefix, "bin")
        current_path = env_map.get("PATH", "")
        if not current_path.startswith(conda_bin):
            env_map["PATH"] = conda_bin + os.pathsep + current_path
    if _ce().is_production():
        profile = _ce().platform_profile()
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
            env_map["OPENAI_BASE_URL"] = str(os.getenv("QWEN_CODE_BASE_URL", "")).strip() or _DEFAULT_QC_BASE_URL
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE"):
        env_map.pop(key, None)
    return env_map


def _validate_qwen_code_config(env_map: Dict[str, str]) -> Optional[str]:
    """Return an error string if QC env is missing credentials."""
    api_key = str(env_map.get("OPENAI_API_KEY", "")).strip()
    if api_key:
        return None
    return "Qwen Code requires credentials. Set QWEN_API_KEY in the environment."


def _build_qwen_execution_session_id(session_id: Optional[str], run_id: str, *, phase: str = "primary", retry_attempt: int = 0) -> str:
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
    return "qwen_cli_no_output_timeout" in text or "qwen cli produced no stdout/stderr" in text


def _is_qwen_recoverable_cli_failure(stderr_text: Any, stdout_text: Any = "") -> bool:
    text = f"{stderr_text or ''}\n{stdout_text or ''}"
    return _is_qwen_no_output_timeout(stderr_text, stdout_text) or _is_qwen_truncated_tool_failure_text(text)


def _is_qwen_container_infrastructure_error(stderr_text: Any, stdout_text: Any = "") -> bool:
    """Return True for qwen Docker/container failures that are safe to retry elsewhere."""
    text = f"{stderr_text or ''}\n{stdout_text or ''}".lower()
    patterns = ("no such container", "container is not running", "cannot connect to the docker daemon", "error response from daemon", "context deadline exceeded", "qwen_cli_no_output_timeout", "qwen cli produced no stdout/stderr")
    return any(pattern in text for pattern in patterns)


def _qwen_infrastructure_fallback_allowed(execution_lane: str, execution_lane_reason: str) -> bool:
    """Only auto-fallback when qwen was selected by auto-routing, not explicit config."""
    lane = str(execution_lane or "").strip().lower()
    reason = str(execution_lane_reason or "").strip().lower()
    return not (lane == "configured_backend" or "code_execution_backend=qwen_code" in reason)


def _should_fallback_from_qwen_infra_failure(*, use_qwen_code_backend: bool, success: bool, stderr: Any, stdout: Any, execution_lane: str, execution_lane_reason: str) -> bool:
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
        auto_strategy = str(getattr(settings, "code_execution_auto_strategy", "qwen_primary") or "qwen_primary").strip().lower() or "qwen_primary"
    except Exception:
        backend = "auto"
        auto_strategy = "qwen_primary"
    if backend in {"local", "qwen_code", "claude_code"}:
        return backend, "configured_backend", f"CODE_EXECUTION_BACKEND={backend}"
    qwen_available = _ce()._qwen_code_cli_available()
    engineering_task = _ce()._looks_like_engineering_task(task)
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
    "- Cycle PALETTE for multi-series; a single series uses '#3C5488' (never a lone bright-red bar/point cloud).\n"
    "- ALL text in English; every axis labeled with units; descriptive title; legend whenever more than one series.\n"
    "- Bars: width <= 0.7, thin or no edgecolor; prefer horizontal bars when category labels are long.\n\n"
)


def _figure_style_prompt(task: str) -> str:
    """Style rules appended to the delegation prompt for figure-producing tasks."""
    if _FIGURE_INTENT_RE.search(task or ""):
        return _FIGURE_STYLE_PROMPT
    return ""


def _build_claude_code_prompt(*, task: str, task_work_dir: Path, file_prefix: str, task_subdirs: Sequence[str], execution_spec: Optional[Dict[str, Any]], resolved_resources: Optional[Dict[str, Dict[str, Any]]], allowed_dirs_info: str) -> str:
    writable_task_subdirs = [name for name in task_subdirs if str(name).strip().lower() != "code"]
    cli_task = _ce()._build_cli_task_contract(task, execution_spec, resolved_resources)
    return (
        f"[ATOMIC TASK]\nExecute only the task below. Do not broaden scope or create extra tasks.\n"
        f"If the request still needs planning or decomposition, output exactly:\n  {_BLOCK_SCOPE_STATUS}\n  {_BLOCK_SCOPE_REASON}\n  DETAIL: <one sentence>\n"
        f"Use direct execution; skip standalone environment diagnostics unless an observed failure requires them.\n\n"
        f"Workspace: {task_work_dir}\nOutput dirs: {_format_task_subdirectories(task_subdirs)}\nFile prefix: {file_prefix}\nTask:\n{cli_task}\n\n"
        f"Deliverables:\n1. Write scripts under code/ only when needed.\n2. Run them and save outputs under {_format_directory_choices(writable_task_subdirs)}.\n"
        f"3. Put publishable deliverable code under results/submission/ or results/deliverable/.\n4. Return a summary of actual outputs produced.\n"
        f"5. Do NOT modify shared host environments: no global `conda install`, `pip install`, or writes into shared site-packages. Use task-local workspace environments only.\n"
        f"6. If the task needs a heavy dependency solve, compiled stack, or a new runtime image/profile, report BLOCKED_DEPENDENCY instead of mutating the shared host environment.\n\n"
        f"{_ce()._rerun_update_mode_prompt()}\n{_ce()._final_response_contract_prompt()}{allowed_dirs_info}"
        f"{_ce()._figure_style_prompt(cli_task)}{_ce()._get_skill_guidance(cli_task)}"
    )


def _build_claude_code_command(*, task: str, task_work_dir: Path, file_prefix: str, output_format: str, normalized_allowed_tools: Sequence[str], allowed_dirs: Sequence[str], task_subdirs: Sequence[str], execution_spec: Optional[Dict[str, Any]], resolved_resources: Optional[Dict[str, Dict[str, Any]]], allowed_dirs_info: str, debug_log_path: Optional[Path], effective_model: Optional[str], effective_setting_sources: Optional[str], skip_permissions: bool) -> List[str]:
    enhanced_task = _build_claude_code_prompt(task=task, task_work_dir=task_work_dir, file_prefix=file_prefix, task_subdirs=task_subdirs, execution_spec=execution_spec, resolved_resources=resolved_resources, allowed_dirs_info=allowed_dirs_info)
    command = ["claude", "-p", enhanced_task, "--output-format", output_format, "--max-turns", "50"]
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


def build_local_task_description(*, task: str, work_dir: str, results_dir: str) -> str:
    """Assemble the instruction handed to the code-generating model.

    The wording is load-bearing. ``execute_code_locally`` receives this same text
    and scans it for absolute paths that do not exist, refusing to execute when it
    finds one (``_find_missing_absolute_input_paths``). An earlier version
    illustrated the "use relative paths" rule with ``/home/.../results`` and
    ``/home/.../output`` — the scanner read those as referenced inputs, so *every*
    local-lane call was refused with BLOCKED_DEPENDENCY naming exactly those two
    strings (measured in production 2026-09-26). The rule stays; the
    absolute-looking example does not.
    """
    return (
        f"{task}\n\n"
        f"Working directory: {work_dir}\n"
        f"Save outputs to: {results_dir}\n"
        "IMPORTANT: Use RELATIVE paths from your working directory (e.g. "
        "results/output.csv). Never use absolute paths — not even for the outputs "
        "directory."
    )


async def _execute_task_locally(task: str, *, work_dir: Optional[str] = None, data_dir: Optional[str] = None, extra_dirs: Optional[Sequence[str]] = None, docker_image: Optional[str] = None, runtime_mode: Optional[str] = None, tool_context: Optional[Any] = None, auto_fix: bool = True, session_dir: Optional[str] = None, execution_spec: Optional[Dict[str, Any]] = None, resolved_resources: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Execute a task using the unified local code execution backend."""
    from app.services.interpreter.code_execution import CodeExecutionSpec, execute_code_locally
    from app.services.llm.llm_service import get_llm_service
    _mp = (getattr(tool_context, 'model_provider', None) or {}) if tool_context else {}
    if _mp.get("base_url") and _mp.get("api_key"):
        from app.llm import LLMClient
        from app.services.llm.llm_service import LLMService
        _llm_override = LLMService(LLMClient(provider=_mp.get("type") or "openai", url=_mp["base_url"].rstrip("/") + "/v1/chat/completions", api_key=_mp["api_key"], model=_mp.get("model") or "qwen3.7-max"))
    else:
        _llm_override = get_llm_service()
    effective_runtime_mode = _ce()._resolve_code_executor_local_runtime(runtime_mode)
    execution_backend = "docker" if effective_runtime_mode == "docker" else "local"
    effective_docker_image = _ce()._resolve_code_executor_docker_image(docker_image) if execution_backend == "docker" else None
    logger.info("[CODE_EXECUTOR_LOCAL] Using %s runtime backend for task", effective_runtime_mode)
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
    blocked_reason = _ce()._summarize_dependency_blockers(execution_spec)
    if blocked_reason:
        await _report("failed", blocked_reason, error_category="blocked_dependency")
        return {"success": False, "stdout": "", "stderr": "", "exit_code": 1, "result": blocked_reason, "error": blocked_reason, "error_category": "blocked_dependency", "error_summary": blocked_reason, "execution_mode": f"code_executor_{effective_runtime_mode}", "docker_image_effective": effective_docker_image, "runtime_failure": False}
    results_dir = os.path.join(work_dir, "results")
    task_desc = build_local_task_description(task=task, work_dir=work_dir, results_dir=results_dir)
    if session_dir:
        session_results = os.path.join(session_dir, "results")
        if os.path.isdir(session_results):
            prior_files = os.listdir(session_results)
            if prior_files:
                task_desc += f"\nPrior session outputs (from earlier tasks): {session_results}\nFiles: {', '.join(sorted(prior_files)[:20])}"
    if data_dir:
        task_desc += f"\nPrimary data directory: {data_dir}"
    resource_text = _ce()._format_resolved_resources_for_prompt(resolved_resources or {})
    if resource_text:
        task_desc += "\n" + resource_text
    if execution_spec and execution_spec.get("dependency_artifact_paths"):
        task_desc += "\nExplicit upstream artifact paths (ABSOLUTE, authoritative):\n" + "\n".join(f"- {path}" for path in (execution_spec.get("dependency_artifact_paths") or [])[:20])
    readable_dirs: List[str] = []
    seen_dirs: set[str] = set()
    for item in extra_dirs or ():
        candidate = str(item or "").strip()
        if not candidate or candidate in seen_dirs:
            continue
        seen_dirs.add(candidate)
        readable_dirs.append(candidate)
    if readable_dirs:
        task_desc += "\nReadable directories:\n" + "\n".join(f"- {path}" for path in readable_dirs)
    writable_dirs: List[str] = []
    try:
        work_dir_path = Path(work_dir).resolve()
        for directory in readable_dirs:
            candidate = Path(directory).resolve()
            if _ce()._is_path_within(work_dir_path, candidate):
                writable_dirs.append(str(candidate))
    except Exception:
        writable_dirs = []
    await _report("running", f"Executing generated code via {effective_runtime_mode} runtime")
    structured_spec = None
    if execution_spec:
        metadata = dict(execution_spec.get("metadata") or {})
        if resolved_resources:
            metadata["resolved_resources"] = resolved_resources
        structured_spec = CodeExecutionSpec(plan_id=execution_spec.get("plan_id"), task_id=execution_spec.get("task_id"), task_name=execution_spec.get("task_name"), task_instruction=execution_spec.get("task_instruction"), acceptance_criteria=execution_spec.get("acceptance_criteria"), dependency_outputs=list(execution_spec.get("dependency_outputs") or []), dependency_artifact_paths=list(execution_spec.get("dependency_artifact_paths") or []), metadata=metadata)
    try:
        from app.config.executor_config import get_executor_settings as _get_exec_settings
        _exec_timeout = _get_exec_settings().code_execution_timeout
    except Exception:
        _exec_timeout = 120
    outcome = await execute_code_locally(task_title="Code execution task", task_description=task_desc, metadata_list=[], llm_service=_llm_override, work_dir=work_dir, data_dir=data_dir, auto_fix=auto_fix, timeout=_exec_timeout, execution_backend=execution_backend, docker_image=effective_docker_image, readable_dirs=readable_dirs, writable_dirs=writable_dirs, execution_spec=structured_spec)
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
    result: Dict[str, Any] = {"success": outcome.success, "task": task, "stdout": outcome.stdout, "stderr": outcome.stderr, "exit_code": outcome.exit_code, "task_directory_full": work_dir, "code_file": outcome.code_file, "result": outcome.stdout if outcome.success else (outcome.error_summary or outcome.stderr), "generated_code": outcome.code, "error_category": outcome.error_category, "error_summary": outcome.error_summary, "fix_guidance": outcome.fix_guidance, "execution_status": outcome.execution_status, "verification_status": outcome.verification_status, "failure_kind": outcome.failure_kind, "contract_diff": outcome.contract_diff, "verification": outcome.verification, "artifact_verification": outcome.artifact_verification, "repair_attempts": outcome.repair_attempts, "plan_patch_suggestion": outcome.plan_patch_suggestion, "stdout_file": outcome.stdout_file, "stderr_file": outcome.stderr_file, "execution_mode": f"code_executor_{effective_runtime_mode}", "docker_image_effective": effective_docker_image, "runtime_failure": outcome.runtime_failure, "produced_files": produced_files, "produced_files_count": len(produced_files)}
    if not outcome.success:
        if outcome.runtime_failure:
            result["error"] = str(outcome.error_summary or "").strip() or str(outcome.stderr or "").strip() or f"{str(runtime_mode or 'code').capitalize()} runtime failed."
        else:
            result["error"] = str(outcome.error_summary or "").strip() or str(outcome.stderr or "").strip() or "Code execution failed."
    if outcome.success and outcome.visualization_files:
        result["deliverable_submit"] = {"publish": True, "artifacts": [{"path": f, "module": "image_tabular", "reason": "auto-submit from code_executor"} for f in outcome.visualization_files]}
    return result


def _resolve_promoted_output_files(promoted: Sequence[str], *, session_dir: Path, output_dir: Optional[Path] = None) -> List[str]:
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
                rel_path = prefix / _ce()._collapse_rooted_rel_path(rel=remainder, output_dir=output_dir, session_dir=session_dir)
            else:
                collapsed = _ce()._collapse_rooted_rel_path(rel=rel_path, output_dir=output_dir, session_dir=session_dir)
                if collapsed != rel_path:
                    rel_path = prefix / collapsed
        resolved.append(str((session_dir / rel_path).resolve()))
    return resolved


def _build_local_backend_result_payload(*, task: str, local_result: Dict[str, Any], resolved_plan_id: Optional[int], resolved_task_id: Optional[int], require_task_context: bool, task_dir_base: Optional[str], task_work_dir: Path, task_root_dir: Path, run_id: str, file_prefix: str, task_subdirs: Sequence[str], session_dir: Path, execution_lane: str, execution_lane_reason: str, log_path: Optional[Path], normalized_allowed_tools: Sequence[str], code_directory: Optional[str], primary_code_file: Optional[str], produced_files: Sequence[str], verification_artifact_paths: Sequence[str], contract_artifacts: Sequence[Dict[str, Any]], session_artifact_paths: Sequence[str], unified_output_dir: Optional[Path], unified_promoted_files: Sequence[str], effective_session_id: str, ancestor_chain: Optional[Sequence[int]], execution_spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    success, execution_failure = _ce()._classify_execution_success(stdout=str(local_result.get("stdout") or ""), output_data=local_result.get("output_data"), execution_spec=execution_spec, produced_files=produced_files, success=bool(local_result.get("success", False)), task_work_dir=task_work_dir)
    result_payload: Dict[str, Any] = {"tool": "code_executor", "task": task, "plan_id": resolved_plan_id, "task_id": resolved_task_id, "require_task_context": require_task_context, "task_directory": task_dir_base, "task_directory_full": str(task_work_dir), "task_root_directory": str(task_root_dir), "run_directory": str(task_work_dir), "run_id": run_id, "task_subdirectories": list(task_subdirs), "file_prefix": file_prefix, "session_directory": str(session_dir), "success": success, "stdout": str(local_result.get("stdout") or ""), "stderr": str(local_result.get("stderr") or ""), "exit_code": local_result.get("exit_code", -1), "execution_backend": str(local_result.get("execution_backend") or local_result.get("execution_mode") or "local"), "execution_mode": str(local_result.get("execution_mode") or "code_executor_host"), "execution_lane": execution_lane, "execution_lane_reason": execution_lane_reason, "working_directory": str(task_work_dir), "log_path": str(log_path) if log_path else None, "debug_log_path": None, "allowed_tools_effective": list(normalized_allowed_tools), "claude_model_effective": None, "claude_setting_sources_effective": None, "claude_auth_mode_effective": None, "code_directory": code_directory, "code_file": local_result.get("code_file") or primary_code_file, "produced_files": list(produced_files), "produced_files_count": len(produced_files), "artifact_paths": list(verification_artifact_paths), "contract_artifacts": list(contract_artifacts), "session_artifact_paths": list(session_artifact_paths), "output_files": _resolve_promoted_output_files(unified_promoted_files, session_dir=session_dir, output_dir=unified_output_dir), "output_location": {"type": "task" if resolved_task_id is not None else "tmp", "session_id": effective_session_id, "task_id": resolved_task_id, "ancestor_chain": list(ancestor_chain) if ancestor_chain is not None else None, "base_dir": str(unified_output_dir) if unified_output_dir else None, "files": list(unified_promoted_files)}}
    if "docker_image_effective" in local_result:
        result_payload["docker_image_effective"] = local_result.get("docker_image_effective")
    if "runtime_failure" in local_result:
        result_payload["runtime_failure"] = bool(local_result.get("runtime_failure"))
    for key in ("error_category", "error_summary", "fix_guidance"):
        if key in local_result and local_result.get(key) is not None:
            result_payload[key] = local_result.get(key)
    for key in ("execution_status", "verification_status", "failure_kind", "contract_diff", "repair_attempts", "plan_patch_suggestion"):
        if key in local_result and local_result.get(key) is not None:
            result_payload[key] = local_result.get(key)
    _ce()._apply_execution_failure_to_payload(result_payload, execution_failure)
    code_file = str(local_result.get("code_file") or "").strip()
    if code_file:
        result_payload["code_file"] = code_file
    result_text = str(local_result.get("result") or "").strip()
    if result_text:
        result_payload["result"] = result_text
    if not result_payload.get("success") and not result_payload.get("error"):
        result_payload["error"] = str(local_result.get("error") or "").strip() or str(local_result.get("stderr") or "").strip() or "Local code execution failed."
    local_completion_info = _ce()._detect_partial_completion(str(local_result.get("stdout") or ""), str(local_result.get("stderr") or ""), list(produced_files), success=bool(result_payload.get("success")))
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
        if not isinstance(event, dict) or str(event.get("type") or "").lower() != "result":
            continue
        usage = event.get("usage")
        if isinstance(usage, dict):
            prompt = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            completion = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            total = int(usage.get("total_tokens") or (prompt + completion))
            if prompt > 0 or completion > 0:
                return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}
    return None


def _resolve_delegation_parent_run_id(child_run_id: Optional[str]) -> Optional[str]:
    """Parent run id for a delegated run, read from the ambient usage context.

    ``asyncio.to_thread`` / ``asyncio.run`` copy contextvars into the worker
    that runs the delegation, so the surrounding chat-run / plan-task run is
    visible here.  Fail-open: an unreadable context means "no parent", never a
    failed accounting row.
    """
    try:
        from app.llm import resolve_parent_run_id

        return resolve_parent_run_id(child_run_id)
    except Exception:
        return None


def _record_external_cli_usage(*, provider: str, model: Optional[str], prompt_tokens: int, completion_tokens: int, session_id: Optional[str], plan_id: Optional[int], task_id: Optional[int], call_purpose: str, duration_ms: Optional[float] = None, run_id: Optional[str] = None, tool_name: Optional[str] = None, call_status: Optional[str] = None, parent_run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        from app.repository.llm_usage import estimate_llm_cost, log_llm_usage
        model_name = str(model or "unknown").strip() or "unknown"
        total_tokens = max(0, int(prompt_tokens or 0)) + max(0, int(completion_tokens or 0))
        if parent_run_id is None:
            parent_run_id = _ce()._resolve_delegation_parent_run_id(run_id)
        cost = estimate_llm_cost(provider=provider, model=model_name, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        log_llm_usage(provider=provider, model=model_name, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=total_tokens, session_id=session_id, plan_id=plan_id, task_id=task_id, call_purpose=call_purpose, duration_ms=duration_ms, run_id=run_id, parent_run_id=parent_run_id, tool_name=tool_name, call_status=call_status, input_cost=cost["input_cost"], output_cost=cost["output_cost"], estimated_cost=cost["estimated_cost"], cost_currency=cost["cost_currency"])
        return {"provider": provider, "model": model_name, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total_tokens, **cost}
    except Exception as exc:
        logger.warning("[CODE_EXECUTOR] Failed to record external CLI usage: %s", exc)
        return None


def _prepare_code_executor_read_context(*, task: str, add_dirs: Optional[Any], resolved_resources: Optional[Dict[str, Any]], require_task_context: bool, execution_spec: Optional[Dict[str, Any]], session_dir: Path) -> Dict[str, Any]:
    normalized_add_dirs = _normalize_csv_values(add_dirs)
    normalized_resources = _ce()._normalize_resolved_resources(resolved_resources)
    resource_add_dirs = _ce()._resource_read_dirs(normalized_resources)
    allowed_dirs: List[str] = []
    resolved_add_dirs: List[str] = []
    default_data_dir = _ce()._PROJECT_ROOT / "data"
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
            candidate = _ce()._PROJECT_ROOT / candidate_text
        try:
            lexical = candidate.absolute()
            if not lexical.exists() or not lexical.is_dir():
                logger.warning("Ignoring non-directory add_dir path: %s", lexical)
                continue
        except (OSError, Exception):
            logger.warning("Ignoring invalid add_dir path: %s", candidate_text)
            continue
        if require_task_context and not _ce()._is_allowed_task_read_path(lexical, session_dir):
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
        if resolved != lexical and resolved.exists() and resolved.is_dir() and resolved_str not in allowed_dirs:
            allowed_dirs.append(resolved_str)
    for resource_dir in resource_add_dirs:
        if resource_dir not in allowed_dirs:
            allowed_dirs.append(resource_dir)
            logger.info("Auto-added resolved resource directory: %s", resource_dir)
        if resource_dir not in resolved_add_dirs:
            resolved_add_dirs.append(resource_dir)
    inferred_task_dirs = _ce()._extract_task_referenced_read_dirs(task, execution_spec=execution_spec, session_dir=session_dir)
    for inferred_dir in inferred_task_dirs:
        if inferred_dir not in allowed_dirs:
            allowed_dirs.append(inferred_dir)
            logger.info("Auto-added task-referenced directory: %s", inferred_dir)
    allowed_dirs_info = ""
    if allowed_dirs:
        allowed_dirs_info = "\n\nExtra readable directories (ABSOLUTE paths):\n" + "\n".join(f"  - {directory}" for directory in allowed_dirs)
    local_data_dir: Optional[str] = None
    if len(resolved_add_dirs) == 1:
        local_data_dir = resolved_add_dirs[0]
    elif not resolved_add_dirs and len(inferred_task_dirs) == 1:
        local_data_dir = inferred_task_dirs[0]
    elif not resolved_add_dirs and default_data_dir.exists():
        local_data_dir = str(default_data_dir)
    return {"normalized_resources": normalized_resources, "allowed_dirs": allowed_dirs, "resolved_add_dirs": resolved_add_dirs, "allowed_dirs_info": allowed_dirs_info, "local_data_dir": local_data_dir}
