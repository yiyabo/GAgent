"""
Claude CLI Executor Tool

Integrates Anthropic's Claude Code CLI for local code execution with full file access.
Uses the official 'claude' command-line tool.
"""

import logging
import subprocess
import json
import hashlib
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Awaitable, Sequence
import asyncio
from uuid import uuid4
from app.services.plans.decomposition_jobs import get_current_job, log_job_event
from app.services.session_paths import get_runtime_root, get_runtime_session_dir

logger = logging.getLogger(__name__)

_BLOCK_SCOPE_STATUS = "STATUS: BLOCKED_SCOPE"
_BLOCK_SCOPE_REASON = "REASON: NEED_ATOMIC_TASK"


def _get_available_skills() -> List[str]:
    """ skills """
    try:
        from app.services.skills import get_skills_loader
        loader = get_skills_loader(auto_sync=False)
        skills = loader.list_skills()
        return [s.get("name", "") for s in skills if s.get("name")]
    except Exception as e:
        logger.debug(f"Failed to load skills list: {e}")
        return []


def _normalize_csv_values(value: Any) -> List[str]:
    if value is None:
        return []

    raw_items: List[str] = []
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if not text:
                continue
            if "," in text:
                raw_items.extend(text.split(","))
            else:
                raw_items.append(text)
    else:
        text = str(value).strip()
        if text:
            raw_items = [text]

    tokens: List[str] = []
    seen = set()
    for item in raw_items:
        token = str(item).strip()
        if not token:
            continue
        normalized = token.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(token)
    return tokens


def _detect_scope_blocked(stdout: str, output_data: Optional[Dict[str, Any]]) -> Optional[str]:
    candidates: List[str] = []
    if stdout:
        candidates.append(stdout)
    if isinstance(output_data, dict):
        for key in ("result", "content", "message", "raw_output"):
            value = output_data.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value)

    for text in candidates:
        if _BLOCK_SCOPE_STATUS not in text:
            continue
        detail_match = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("DETAIL:"):
                detail_match = stripped[len("DETAIL:") :].strip()
                break
        if detail_match:
            return detail_match
        if _BLOCK_SCOPE_REASON in text:
            return "Need atomic task decomposition."
        return "Blocked by execution scope guardrail."
    return None


def _compact_cli_text(value: Optional[str], *, limit: int = 320) -> str:
    text = " ".join((value or "").split()).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _extract_readable_error(stderr: str) -> str:
    """Extract a human-readable error from Claude CLI stderr.

    When the CLI crashes, stderr may contain a minified JS stack trace that is
    useless for debugging.  This function detects that pattern and produces a
    concise summary instead.
    """
    if not stderr or not stderr.strip():
        return ""

    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]

    # 1. Detect known structured error messages first.
    for line in lines:
        lower = line.lower()
        if "cannot be launched inside another claude code session" in lower:
            return "Nested Claude Code session detected. Unset the CLAUDECODE env var."
        if "error:" in lower and len(line) < 300:
            return line

    # 2. Detect minified JavaScript dump (CLI crash).
    joined = " ".join(lines)
    is_minified_js = (
        "cli.js:" in joined
        and any(kw in joined for kw in (
            "function(", "var ", "Object.defineProperty",
            "exports.", "DefaultTransporter", "status>=400",
        ))
    )
    if is_minified_js:
        # Try to extract HTTP status hint from the minified code context.
        import re
        status_match = re.search(r'status[>=]+\s*(\d{3})', joined)
        if status_match:
            status_code = status_match.group(1)
            if status_code in {"401", "403"}:
                return (
                    f"Claude CLI crashed (HTTP {status_code} from upstream Anthropic-compatible API). "
                    "Check provider credentials and authorization settings."
                )
            if status_code == "429":
                return (
                    "Claude CLI crashed (HTTP 429 from upstream Anthropic-compatible API). "
                    "The provider likely rate-limited the request."
                )
            if status_code == "400":
                return (
                    "Claude CLI crashed (HTTP 400 from upstream Anthropic-compatible API). "
                    "The upstream rejected the request; this is not necessarily a local API-key/base-URL problem."
                )
            return (
                f"Claude CLI crashed (HTTP {status_code} from upstream Anthropic-compatible API). "
                "Check provider debug logs and request compatibility."
            )
        return (
            "Claude CLI crashed with an unhandled JS exception. "
            "This usually indicates an API connectivity or authentication error."
        )

    # 3. Fallback: truncate to a readable length.
    return _compact_cli_text(joined, limit=360)


def _build_cli_failure_error(*, return_code: Optional[int], stderr: str, stdout: str) -> str:
    parts: List[str] = []
    if return_code is not None:
        parts.append(f"exit_code={return_code}")
    stderr_excerpt = _extract_readable_error(stderr)
    if stderr_excerpt:
        parts.append(f"stderr={stderr_excerpt}")
    stdout_excerpt = _compact_cli_text(stdout, limit=220)
    if stdout_excerpt:
        parts.append(f"stdout={stdout_excerpt}")
    if not parts:
        return "Claude CLI execution failed (success=false)."
    return f"Claude CLI execution failed: {'; '.join(parts)}"

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
_DEFAULT_CODE_EXECUTOR_LOCAL_RUNTIME = "docker"
_DEFAULT_CODE_EXECUTOR_DOCKER_IMAGE = "gagent-python-runtime:latest"
_SUPPORTED_SETTING_SOURCES = {"user", "project", "local"}
_SUPPORTED_AUTH_MODES = {"claude_login", "api_env"}
_DEFAULT_API_BASE_URL = "https://dashscope.aliyuncs.com/apps/anthropic"
_DEFAULT_API_MODEL = "qwen3.5-plus"
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
    raw = str(value or os.getenv("CODE_EXECUTOR_LOCAL_RUNTIME") or _DEFAULT_CODE_EXECUTOR_LOCAL_RUNTIME).strip().lower()
    if raw == "local":
        return "host"
    if raw in {"docker", "host"}:
        return raw
    return _DEFAULT_CODE_EXECUTOR_LOCAL_RUNTIME


def _resolve_code_executor_docker_image(value: Optional[str] = None) -> str:
    raw = str(value or os.getenv("CODE_EXECUTOR_DOCKER_IMAGE") or _DEFAULT_CODE_EXECUTOR_DOCKER_IMAGE).strip()
    return raw or _DEFAULT_CODE_EXECUTOR_DOCKER_IMAGE


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


def _validate_scope_contract(
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


def _resolve_runtime_session_dir(session_id: Optional[str]) -> Path:
    token = str(session_id or "").strip()
    if not token:
        adhoc_dir = (_RUNTIME_DIR / "session_adhoc").resolve()
        adhoc_dir.mkdir(parents=True, exist_ok=True)
        return adhoc_dir
    return get_runtime_session_dir(token, create=True)


# Skip absurdly large binaries when mirroring into session-level results/ (artifact URLs).
_MAX_SESSION_PROMOTE_FILE_BYTES = 250 * 1024 * 1024
_MAX_SESSION_PROMOTE_FILES = 500


def _promote_task_results_to_session_root(
    *,
    session_dir: Path,
    task_work_dir: Path,
    max_files: int = _MAX_SESSION_PROMOTE_FILES,
) -> List[str]:
    """
    Copy everything under ``<run>/results/`` into ``<session>/results/`` so that
    Markdown like ``![](results/plot.png)`` resolves via GET .../artifacts/.../file?path=results/plot.png.

    Claude Code cwd is an isolated ``run_<id>/`` tree; without this step outputs only exist under nested paths.
    """
    session_resolved = session_dir.resolve()
    task_resolved = task_work_dir.resolve()
    src_results = (task_resolved / "results").resolve()
    if not src_results.is_dir() or not _is_path_within(src_results, task_resolved):
        return []

    dst_root = (session_resolved / "results").resolve()
    if not _is_path_within(dst_root, session_resolved):
        return []

    dst_root.mkdir(parents=True, exist_ok=True)
    promoted: List[str] = []
    count = 0
    for path in sorted(src_results.rglob("*")):
        if not path.is_file():
            continue
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
            continue
        try:
            rel = path.relative_to(src_results)
        except ValueError:
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
            "Promoted %s file(s) from %s/results/ to session results/ for artifact URLs",
            len(promoted),
            task_resolved.name,
        )
    return promoted


def _collect_run_artifacts(
    *,
    run_dir: Path,
    subdirs: Sequence[str],
    max_files: int = 2000,
) -> List[str]:
    collected: List[str] = []
    seen = set()
    for name in subdirs:
        root = (run_dir / str(name)).resolve()
        if not root.exists() or not root.is_dir():
            continue
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
    return collected


def _is_path_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


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
        
        # Run LLM call in executor to avoid blocking
        loop = asyncio.get_event_loop()
        llm_response = await loop.run_in_executor(None, client.chat, prompt)
        
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


async def _execute_task_locally(
    task: str,
    *,
    work_dir: Optional[str] = None,
    data_dir: Optional[str] = None,
    extra_dirs: Optional[Sequence[str]] = None,
    tool_context: Optional[Any] = None,
    auto_fix: bool = True,
    session_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a task using the unified local code execution backend.

    Delegates to ``execute_code_locally()`` which handles code generation,
    file-persistent execution, error classification, and LLM-based fixing.
    """
    from app.services.interpreter.code_execution import execute_code_locally
    from app.services.llm.llm_service import get_llm_service

    runtime_mode = _resolve_code_executor_local_runtime()
    execution_backend = "docker" if runtime_mode == "docker" else "local"
    docker_image = _resolve_code_executor_docker_image() if execution_backend == "docker" else None

    logger.info(
        "[CODE_EXECUTOR_LOCAL] Using %s runtime backend for task",
        runtime_mode,
    )

    async def _report(stage: str, message: str, **extra: Any) -> None:
        if tool_context is not None and tool_context.on_progress:
            await tool_context.on_progress({"stage": stage, "message": message, **extra})

    await _report("started", f"Generating code for task ({runtime_mode} runtime)")

    if not work_dir:
        import tempfile
        work_dir = tempfile.mkdtemp(prefix="cc_local_")
    else:
        work_dir = str(work_dir).strip()

    os.makedirs(work_dir, exist_ok=True)

    # Build task description with directory context.
    results_dir = os.path.join(work_dir, "results")
    task_desc = (
        f"{task}\n\n"
        f"Working directory: {work_dir}\n"
        f"Save outputs to: {results_dir}"
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

    readable_dirs: List[str] = []
    seen_dirs: set = set()
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

    await _report("running", f"Executing generated code via {runtime_mode} runtime")
    outcome = await execute_code_locally(
        task_title="Code execution task",
        task_description=task_desc,
        metadata_list=[],
        llm_service=get_llm_service(),
        work_dir=work_dir,
        data_dir=data_dir,
        auto_fix=auto_fix,
        execution_backend=execution_backend,
        docker_image=docker_image,
        readable_dirs=readable_dirs,
        writable_dirs=writable_dirs,
    )

    if outcome.success:
        await _report("completed", "Code execution succeeded")
    else:
        await _report("failed", f"Execution failed: {outcome.error_category or 'unknown'}")

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
        "stdout_file": outcome.stdout_file,
        "stderr_file": outcome.stderr_file,
        "execution_mode": f"code_executor_{runtime_mode}",
        "docker_image_effective": docker_image,
        "runtime_failure": outcome.runtime_failure,
    }
    if not outcome.success:
        if outcome.runtime_failure:
            result["error"] = (
                str(outcome.error_summary or "").strip()
                or str(outcome.stderr or "").strip()
                or f"{runtime_mode.capitalize()} runtime failed."
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


async def code_executor_handler(
    task: str,
    allowed_tools: Optional[Any] = None,
    add_dirs: Optional[Any] = None,
    skip_permissions: bool = True,
    output_format: str = "json",
    session_id: Optional[str] = None,
    plan_id: Optional[int] = None,
    task_id: Optional[int] = None,
    model: Optional[str] = None,
    setting_sources: Optional[str] = None,
    auth_mode: Optional[str] = None,
    require_task_context: bool = True,
    on_stdout: Optional[Callable[[str], Awaitable[None]]] = None,
    on_stderr: Optional[Callable[[str], Awaitable[None]]] = None,
    tool_context: Optional[Any] = None,
    auto_fix: bool = True,
) -> Dict[str, Any]:
    """
    Execute a task using Claude Code (official CLI) with local file access.
    
    Args:
        task: Task description for Claude to complete
        allowed_tools: Comma-separated list of allowed tools (e.g. "Bash Edit")
        add_dirs: Comma-separated list of additional directories to allow access
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
    use_local_backend = False
    try:
        from app.config.executor_config import get_executor_settings
        use_local_backend = get_executor_settings().code_execution_backend == "local"
    except Exception:
        use_local_backend = False

    log_file = None
    log_path = None
    log_lock = asyncio.Lock()

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

        # Keep a stable per-task root and isolate each execution by run_<timestamp>.
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f") + f"_{uuid4().hex[:8]}"
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

        task_root_dir = session_dir / task_dir_base
        task_root_dir.mkdir(parents=True, exist_ok=True)

        run_dir_name = f"run_{run_id}"
        task_work_dir = task_root_dir / run_dir_name
        task_work_dir.mkdir(parents=True, exist_ok=True)

        task_subdirs = ["results", "code", "data", "docs"]
        for subdir in task_subdirs:
            (task_work_dir / subdir).mkdir(parents=True, exist_ok=True)

        file_prefix = run_dir_name

        logger.info(f"Using task workspace: {task_work_dir}")

        debug_log_path: Optional[Path] = None

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
        normalized_add_dirs = _normalize_csv_values(add_dirs)
        # Process additional directories to allow access, convert to absolute paths
        allowed_dirs = []
        resolved_add_dirs: List[str] = []
        
        # Always include project's data directory by default
        default_data_dir = _PROJECT_ROOT / "data"
        if default_data_dir.exists():
            allowed_dirs.append(str(default_data_dir))
            logger.info(f"Auto-added default data directory: {default_data_dir}")
        
        # Auto-add session's runtime directory for cross-task access within same session
        if session_dir.exists():
            allowed_dirs.append(str(session_dir))
            logger.info(f"Auto-added session runtime directory: {session_dir}")
        
        if normalized_add_dirs:
            for dir_path in normalized_add_dirs:
                dir_path = dir_path.strip()
                candidate = Path(dir_path)
                if not candidate.is_absolute():
                    candidate = _PROJECT_ROOT / dir_path
                try:
                    resolved = candidate.resolve()
                except Exception:
                    logger.warning("Ignoring invalid add_dir path: %s", dir_path)
                    continue
                if not resolved.exists() or not resolved.is_dir():
                    logger.warning("Ignoring non-directory add_dir path: %s", resolved)
                    continue
                if require_task_context:
                    if not (
                        _is_path_within(resolved, _PROJECT_ROOT)
                        or _is_path_within(resolved, session_dir)
                    ):
                        logger.warning(
                            "Ignoring add_dir outside strict task scope: %s",
                            resolved,
                        )
                        continue
                resolved_str = str(resolved)
                if resolved_str not in allowed_dirs:
                    allowed_dirs.append(resolved_str)
                if resolved_str not in resolved_add_dirs:
                    resolved_add_dirs.append(resolved_str)
            
        allowed_dirs_info = ""
        if allowed_dirs:
            allowed_dirs_info = (
                "\n\nExtra readable directories (ABSOLUTE paths):\n"
                + "\n".join(f"  - {d}" for d in allowed_dirs)
            )

        local_data_dir: Optional[str] = None
        if len(resolved_add_dirs) == 1:
            local_data_dir = resolved_add_dirs[0]
        elif not resolved_add_dirs and default_data_dir.exists():
            local_data_dir = str(default_data_dir)

        if use_local_backend:
            local_result = await _execute_task_locally(
                task=task,
                work_dir=str(task_work_dir),
                data_dir=local_data_dir,
                extra_dirs=allowed_dirs,
                tool_context=tool_context,
                auto_fix=auto_fix,
                session_dir=str(session_dir),
            )
            produced_files = _collect_run_artifacts(
                run_dir=task_work_dir,
                subdirs=task_subdirs,
            )
            session_artifact_paths = _promote_task_results_to_session_root(
                session_dir=session_dir,
                task_work_dir=task_work_dir,
            )
            success = bool(local_result.get("success", False))
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
                "stdout": str(local_result.get("stdout") or ""),
                "stderr": str(local_result.get("stderr") or ""),
                "exit_code": local_result.get("exit_code", -1),
                "execution_mode": str(local_result.get("execution_mode") or "code_executor_host"),
                "working_directory": str(task_work_dir),
                "log_path": str(log_path) if log_path else None,
                "debug_log_path": None,
                "allowed_tools_effective": normalized_allowed_tools,
                "claude_model_effective": None,
                "claude_setting_sources_effective": None,
                "claude_auth_mode_effective": None,
                "produced_files": produced_files,
                "produced_files_count": len(produced_files),
                "artifact_paths": session_artifact_paths,
            }
            if "docker_image_effective" in local_result:
                result_payload["docker_image_effective"] = local_result.get("docker_image_effective")
            if "runtime_failure" in local_result:
                result_payload["runtime_failure"] = bool(local_result.get("runtime_failure"))
            code_file = str(local_result.get("code_file") or "").strip()
            if code_file:
                result_payload["code_file"] = code_file
            result_text = str(local_result.get("result") or "").strip()
            if result_text:
                result_payload["result"] = result_text
            if not success:
                result_payload["error"] = (
                    str(local_result.get("error") or "").strip()
                    or str(local_result.get("stderr") or "").strip()
                    or "Local code execution failed."
                )
            return result_payload

        effective_auth_mode = _resolve_auth_mode(auth_mode)
        subprocess_env = _build_code_executor_subprocess_env(effective_auth_mode)
        if effective_auth_mode == "api_env":
            api_mode_error = _validate_api_mode_config(subprocess_env)
            if api_mode_error:
                return {
                    "success": False,
                    "error": api_mode_error,
                    "task": task,
                }
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
            setting_sources,
            auth_mode=effective_auth_mode,
        )

        enhanced_task = (
            f"[ATOMIC TASK]\n"
            f"Execute only the task below. Do not broaden scope or create extra tasks.\n"
            f"If the request still needs planning or decomposition, output exactly:\n"
            f"  {_BLOCK_SCOPE_STATUS}\n"
            f"  {_BLOCK_SCOPE_REASON}\n"
            f"  DETAIL: <one sentence>\n"
            f"Use direct execution; skip standalone environment diagnostics unless an observed failure requires them.\n\n"
            f"Workspace: {task_work_dir}\n"
            f"Output dirs: results/ code/ data/ docs/\n"
            f"File prefix: {file_prefix}\n"
            f"Task:\n{task}\n\n"
            f"Deliverables:\n"
            f"1. Write scripts under code/ only when needed.\n"
            f"2. Run them and save outputs under results/, data/, or docs/.\n"
            f"3. Put publishable deliverable code under results/submission/ or results/deliverable/.\n"
            f"4. Return a summary of actual outputs produced."
            f"{allowed_dirs_info}"
        )
        
        # Build command
        cmd = [
            'claude',
            '-p',  # Print mode (non-interactive)
            enhanced_task,
            '--output-format', output_format,
            '--max-turns', '50',  # Allow more turns for complex tasks
        ]

        if debug_log_path is not None:
            cmd.extend(['--debug-file', str(debug_log_path)])

        if effective_model:
            cmd.extend(['--model', effective_model])
        if effective_setting_sources:
            cmd.extend(['--setting-sources', effective_setting_sources])
        
        # Enforce strict allowlist; never run Claude with unrestricted tool set.
        cmd.extend(['--allowed-tools', ",".join(normalized_allowed_tools)])
        
        # Add directory access permissions (paths relative to project root)
        for abs_path in allowed_dirs:
            cmd.extend(['--add-dir', abs_path])
        
        # Skip permission checks (research environment)
        if skip_permissions:
            cmd.append('--dangerously-skip-permissions')
        
        logger.info(f"Executing Claude CLI in task workspace: {task_work_dir}")

        # Diagnostic: log key env vars (redacted) to debug API connectivity issues.
        _diag_key = subprocess_env.get("ANTHROPIC_API_KEY", "")
        _diag_url = subprocess_env.get("ANTHROPIC_BASE_URL", "")
        _diag_model = subprocess_env.get("ANTHROPIC_MODEL", "")
        _diag_small_fast_model = subprocess_env.get("ANTHROPIC_SMALL_FAST_MODEL", "")
        logger.info(
            "[CODE_EXECUTOR_DIAG] api_key_len=%d base_url=%s model=%s small_fast_model=%s auth_mode=%s setting_sources=%s",
            len(_diag_key), _diag_url, _diag_model, _diag_small_fast_model,
            effective_auth_mode, effective_setting_sources,
        )

        # Retry logic for transient provider / CLI failures.
        max_cli_retries, cli_retry_base_delay_s = _resolve_cli_retry_policy()

        async def _read_stream(stream, lines, callback, stream_name: str):
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded_line = line.decode(errors="replace").rstrip()
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

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        return_code = -1

        for _attempt in range(1, max_cli_retries + 2):
            stdout_lines.clear()
            stderr_lines.clear()

            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(task_work_dir),
                env=subprocess_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_task = asyncio.create_task(
                _read_stream(process.stdout, stdout_lines, on_stdout, "stdout")
            )
            stderr_task = asyncio.create_task(
                _read_stream(process.stderr, stderr_lines, on_stderr, "stderr")
            )

            try:
                await asyncio.wait([stdout_task, stderr_task])
                return_code = await process.wait()
            except (asyncio.CancelledError, Exception) as _wait_exc:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
                if isinstance(_wait_exc, asyncio.CancelledError):
                    raise
                raise

            if return_code == 0:
                break  # Success — no retry needed.

            # Check if this is a retryable failure (CLI crash, not a scope block).
            _stderr_preview = " ".join(stderr_lines)[:500]
            _is_scope_block = _detect_scope_blocked(
                "\n".join(stdout_lines), None,
            )
            if _is_scope_block:
                logger.info("[CODE_EXECUTOR_RETRY] Scope block detected, not retrying.")
                break

            if _attempt <= max_cli_retries:
                retry_delay_s = min(cli_retry_base_delay_s * (2 ** (_attempt - 1)), 30.0)
                logger.warning(
                    "[CODE_EXECUTOR_RETRY] CLI failed (attempt %d/%d, exit=%d). "
                    "Retrying in %.1fs... stderr_hint=%s",
                    _attempt, max_cli_retries + 1, return_code,
                    retry_delay_s, _extract_readable_error("\n".join(stderr_lines))[:200],
                )
                if log_file:
                    try:
                        log_file.write(
                            f"[{datetime.utcnow().isoformat()}Z] Retry {_attempt}/{max_cli_retries} "
                            f"after exit={return_code}, waiting {retry_delay_s:.1f}s\n"
                        )
                        log_file.flush()
                    except Exception:
                        pass
                await asyncio.sleep(retry_delay_s)
            else:
                logger.error(
                    "[CODE_EXECUTOR_RETRY] CLI failed after %d attempts (exit=%d).",
                    _attempt, return_code,
                )

        success = return_code == 0
        stdout = "\n".join(stdout_lines)
        stderr = "\n".join(stderr_lines)
        
        # Parse output
        output_data = None
        if output_format == "json" and stdout:
            try:
                output_data = json.loads(stdout)
            except json.JSONDecodeError:
                logger.warning("Failed to parse JSON output, using raw text")
                output_data = {"raw_output": stdout}

        blocked_detail = _detect_scope_blocked(stdout, output_data)
        if blocked_detail:
            success = False

        produced_files = _collect_run_artifacts(run_dir=task_work_dir, subdirs=task_subdirs)
        session_artifact_paths = _promote_task_results_to_session_root(
            session_dir=session_dir,
            task_work_dir=task_work_dir,
        )

        if log_file:
            try:
                log_file.write(f"[{datetime.utcnow().isoformat()}Z] Claude Code finished (exit={return_code})\n")
                log_file.flush()
            except Exception as log_err:
                logger.warning(f"Failed to finalize Claude Code log file: {log_err}")

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
            "execution_mode": "code_executor_local",
            "working_directory": str(task_work_dir),
            "log_path": str(log_path) if log_path else None,
            "debug_log_path": str(debug_log_path) if debug_log_path else None,
            "allowed_tools_effective": normalized_allowed_tools,
            "claude_model_effective": effective_model,
            "claude_setting_sources_effective": effective_setting_sources,
            "claude_auth_mode_effective": effective_auth_mode,
            "produced_files": produced_files,
            "produced_files_count": len(produced_files),
            "artifact_paths": session_artifact_paths,
        }
        if not success and not blocked_detail:
            result_payload["error"] = _build_cli_failure_error(
                return_code=return_code,
                stderr=stderr,
                stdout=stdout,
            )
        if blocked_detail:
            result_payload["blocked_by_scope_guardrail"] = True
            result_payload["blocked_reason"] = blocked_detail
            result_payload["error"] = f"Blocked by scope guardrail: {blocked_detail}"
        return result_payload
        
    except subprocess.TimeoutExpired:
        # Should not trigger since timeout=None, but kept as a safeguard
        return {
            "success": False,
            "error": "Claude CLI execution was interrupted unexpectedly",
            "task": task,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": "Claude CLI not found. Please install it: npm install -g @anthropic-ai/claude-code",
            "task": task,
        }
    except Exception as e:
        logger.exception(f"Claude CLI execution failed: {e}")
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
