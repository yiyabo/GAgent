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
from app.services.plans.acceptance_criteria import (
    derive_acceptance_criteria_from_text,
    derive_relative_output_dirs,
    resolve_glob_min_count,
    resolve_glob_pattern,
)
from app.services.plans.decomposition_jobs import get_current_job, log_job_event
from app.services.session_paths import get_runtime_root, get_runtime_session_dir
from app.services.path_router import get_path_router
from app.config.executor_config import (
    DEFAULT_CODE_EXECUTION_DOCKER_IMAGE,
    DEFAULT_CODE_EXECUTION_LOCAL_RUNTIME,
    resolve_code_execution_docker_image,
    resolve_code_execution_local_runtime,
)
from app.services.interpreter.runtime_guardrails import (
    ENV_GUARD_BIN as _ENV_GUARD_BIN,
    inject_env_mutation_guard as _inject_env_mutation_guard,
    looks_like_engineering_task as _looks_like_engineering_task,
)

logger = logging.getLogger(__name__)

_BLOCK_SCOPE_STATUS = "STATUS: BLOCKED_SCOPE"
_BLOCK_SCOPE_REASON = "REASON: NEED_ATOMIC_TASK"
_DEFAULT_TASK_SUBDIRECTORIES = ("results", "code", "data", "docs")
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


def _clear_stale_contract_failure_state(
    *,
    success: bool,
    verification_status: Optional[str],
    contract_error_summary: Optional[str],
    contract_fix_guidance: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Drop stale contract failure details once verification has passed."""
    if success and verification_status == "passed":
        return None, None
    return contract_error_summary, contract_fix_guidance


def _derive_task_subdirectories(
    execution_spec: Optional[Dict[str, Any]],
) -> List[str]:
    criteria = execution_spec.get("acceptance_criteria") if isinstance(execution_spec, dict) else None
    return derive_relative_output_dirs(
        criteria,
        default_dirs=_DEFAULT_TASK_SUBDIRECTORIES,
    )


def _format_task_subdirectories(subdirs: Sequence[str]) -> str:
    return " ".join(f"{name}/" for name in subdirs)


def _format_directory_choices(subdirs: Sequence[str]) -> str:
    items = [f"{name}/" for name in subdirs if name]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return f"{', '.join(items[:-1])}, or {items[-1]}"


def _compact_cli_text(value: Optional[str], *, limit: int = 320) -> str:
    text = " ".join((value or "").split()).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


_QWEN_DEBUG_ENABLED_LINE_RE = re.compile(
    r"^Debug mode enabled(?:\s+Logging to:\s*(?P<path>\S+))?\s*$",
    re.IGNORECASE,
)
_QWEN_LOGGING_TO_LINE_RE = re.compile(
    r"^Logging to:\s*(?P<path>\S+)\s*$",
    re.IGNORECASE,
)
_QWEN_TRANSCRIPTS_ROOT = "/tmp/gagent_home/.qwen/projects"
_QWEN_SHELL_FALLBACK_TIMEOUT_MS = 600000
_QWEN_SHELL_FALLBACK_MAX_TIMEOUT_MS = 3600000


def _partition_cli_stderr_lines(stderr: str) -> tuple[List[str], str]:
    actionable_lines: List[str] = []
    debug_log_path = ""

    for raw_line in stderr.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = _QWEN_DEBUG_ENABLED_LINE_RE.match(line)
        if match:
            maybe_path = str(match.group("path") or "").strip()
            if maybe_path:
                debug_log_path = maybe_path
            continue

        match = _QWEN_LOGGING_TO_LINE_RE.match(line)
        if match:
            maybe_path = str(match.group("path") or "").strip()
            if maybe_path:
                debug_log_path = maybe_path
            continue

        actionable_lines.append(line)

    return actionable_lines, debug_log_path


async def _iter_stream_lines_unbounded(
    stream: asyncio.StreamReader,
    *,
    chunk_size: int = 65536,
) -> AsyncIterator[str]:
    """Yield decoded lines without relying on StreamReader.readline limits."""

    pending = ""
    while True:
        chunk = await stream.read(chunk_size)
        if not chunk:
            break
        pending += chunk.decode(errors="replace")
        while True:
            newline_index = pending.find("\n")
            if newline_index < 0:
                break
            line = pending[:newline_index]
            if line.endswith("\r"):
                line = line[:-1]
            yield line
            pending = pending[newline_index + 1 :]

    if pending:
        if pending.endswith("\r"):
            pending = pending[:-1]
        yield pending


def _extract_readable_error(stderr: str) -> str:
    """Extract a human-readable error from CLI stderr.

    When the CLI crashes, stderr may contain a minified JS stack trace that is
    useless for debugging.  This function detects that pattern and produces a
    concise summary instead.
    """
    if not stderr or not stderr.strip():
        return ""

    lines, _debug_log_path = _partition_cli_stderr_lines(stderr)
    if not lines:
        return ""

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


def _extract_qwen_debug_log_path(stderr: str) -> str:
    if not stderr or not stderr.strip():
        return ""
    _lines, debug_log_path = _partition_cli_stderr_lines(stderr)
    return debug_log_path


def _extract_pending_qwen_function_call(transcript_text: str) -> Optional[Dict[str, Any]]:
    """Return the latest assistant function call without a matching tool result."""
    if not transcript_text or not transcript_text.strip():
        return None

    completed_call_ids: set[str] = set()
    pending_calls: list[Dict[str, Any]] = []

    for raw_line in transcript_text.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue

        payload_type = str(payload.get("type") or "").strip().lower()
        if payload_type == "tool_result":
            tool_call_result = payload.get("toolCallResult")
            if isinstance(tool_call_result, dict):
                call_id = str(tool_call_result.get("callId") or "").strip()
                if call_id:
                    completed_call_ids.add(call_id)
                    continue
            message = payload.get("message")
            if isinstance(message, dict):
                for part in message.get("parts") or []:
                    if not isinstance(part, dict):
                        continue
                    function_response = part.get("functionResponse")
                    if not isinstance(function_response, dict):
                        continue
                    call_id = str(function_response.get("id") or "").strip()
                    if call_id:
                        completed_call_ids.add(call_id)
                        break
            continue

        if payload_type != "assistant":
            continue

        message = payload.get("message")
        if not isinstance(message, dict):
            continue
        for part in message.get("parts") or []:
            if not isinstance(part, dict):
                continue
            function_call = part.get("functionCall")
            if not isinstance(function_call, dict):
                continue
            call_id = str(function_call.get("id") or "").strip()
            name = str(function_call.get("name") or "").strip()
            args = function_call.get("args")
            if not call_id or not name or not isinstance(args, dict):
                continue
            pending_calls.append(
                {
                    "id": call_id,
                    "name": name,
                    "args": json.loads(json.dumps(args, ensure_ascii=False)),
                }
            )

    for function_call in reversed(pending_calls):
        if function_call["id"] not in completed_call_ids:
            return function_call
    return None


def _extract_pending_qwen_shell_command(transcript_text: str) -> Optional[Dict[str, Any]]:
    pending_call = _extract_pending_qwen_function_call(transcript_text)
    if not isinstance(pending_call, dict):
        return None
    if str(pending_call.get("name") or "").strip() != "run_shell_command":
        return None

    args = pending_call.get("args")
    if not isinstance(args, dict):
        return None

    command = str(args.get("command") or "").strip()
    if not command:
        return None

    raw_timeout = args.get("timeout")
    timeout_ms = _QWEN_SHELL_FALLBACK_TIMEOUT_MS
    if raw_timeout is not None:
        try:
            timeout_ms = int(str(raw_timeout).strip())
        except (TypeError, ValueError):
            timeout_ms = _QWEN_SHELL_FALLBACK_TIMEOUT_MS
    timeout_ms = max(timeout_ms, _QWEN_SHELL_FALLBACK_TIMEOUT_MS)
    timeout_ms = min(timeout_ms, _QWEN_SHELL_FALLBACK_MAX_TIMEOUT_MS)

    return {
        "call_id": str(pending_call.get("id") or "").strip(),
        "command": command,
        "description": str(args.get("description") or "").strip(),
        "timeout_ms": timeout_ms,
    }


async def _run_subprocess_capture(
    command: Sequence[str],
    *,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout_s: Optional[float] = None,
) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        if timeout_s and timeout_s > 0:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_s,
            )
        else:
            stdout_bytes, stderr_bytes = await process.communicate()
        return_code = int(process.returncode or 0)
    except asyncio.TimeoutError:
        try:
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
        except Exception:
            stdout_bytes = b""
            stderr_bytes = b""
        timeout_note = f"\n[TIMEOUT] pending qwen shell call exceeded {int(timeout_s or 0)}s"
        stderr_text = stderr_bytes.decode("utf-8", errors="replace") + timeout_note
        return -1, stdout_bytes.decode("utf-8", errors="replace"), stderr_text

    return (
        return_code,
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
    )


async def _read_qwen_transcript_text(
    *,
    qwen_session_id: Optional[str],
    container_name: Optional[str] = None,
) -> str:
    session_token = str(qwen_session_id or "").strip()
    if not session_token:
        return ""

    if container_name:
        transcript_glob = f"*/chats/{session_token}.jsonl"
        shell_cmd = (
            f'path="$(find {_QWEN_TRANSCRIPTS_ROOT} -path {shlex.quote(transcript_glob)} -print -quit)" && '
            '[ -n "$path" ] && cat "$path"'
        )
        return_code, stdout, _stderr = await _run_subprocess_capture(
            ["docker", "exec", container_name, "sh", "-lc", shell_cmd],
            timeout_s=5.0,
        )
        if return_code == 0:
            return stdout
        return ""

    host_projects_root = Path.home() / ".qwen" / "projects"
    if not host_projects_root.exists():
        return ""
    for candidate in host_projects_root.glob(f"**/chats/{session_token}.jsonl"):
        try:
            return candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


async def _recover_pending_qwen_shell_call(
    *,
    qwen_session_id: Optional[str],
    container_name: Optional[str],
    task_work_dir: str,
) -> Optional[Dict[str, Any]]:
    transcript_text = await _read_qwen_transcript_text(
        qwen_session_id=qwen_session_id,
        container_name=container_name,
    )
    pending_shell = _extract_pending_qwen_shell_command(transcript_text)
    if not isinstance(pending_shell, dict):
        return None

    shell_command = str(pending_shell.get("command") or "").strip()
    if not shell_command:
        return None

    timeout_ms = int(pending_shell.get("timeout_ms") or _QWEN_SHELL_FALLBACK_TIMEOUT_MS)
    timeout_s = max(1.0, timeout_ms / 1000.0)

    if container_name:
        from app.services.terminal.docker_pty_backend import CONTAINER_EXEC_PATH

        command = [
            "docker",
            "exec",
            "-e",
            f"PATH={CONTAINER_EXEC_PATH}",
            "-w",
            str(task_work_dir),
            container_name,
            "/bin/bash",
            "-c",
            shell_command,
        ]
        return_code, stdout, stderr = await _run_subprocess_capture(
            command,
            timeout_s=timeout_s,
        )
    else:
        return_code, stdout, stderr = await _run_subprocess_capture(
            ["/bin/bash", "-c", shell_command],
            cwd=str(task_work_dir),
            timeout_s=timeout_s,
        )

    return {
        "exit_code": return_code,
        "stdout": stdout,
        "stderr": stderr,
        "command": shell_command,
        "timeout_ms": timeout_ms,
        "call_id": str(pending_shell.get("call_id") or "").strip(),
        "description": str(pending_shell.get("description") or "").strip(),
    }


def _build_cli_failure_error(
    *,
    return_code: Optional[int],
    stderr: str,
    stdout: str,
    backend_label: str = "Claude Code",
) -> str:
    parts: List[str] = []
    if return_code is not None:
        parts.append(f"exit_code={return_code}")
    stderr_excerpt = _extract_readable_error(stderr)
    if stderr_excerpt:
        parts.append(f"stderr={stderr_excerpt}")
    else:
        debug_log_path = _extract_qwen_debug_log_path(stderr)
        if debug_log_path:
            parts.append(f"debug_log={debug_log_path}")
    stdout_excerpt = _compact_cli_text(stdout, limit=220)
    if stdout_excerpt:
        parts.append(f"stdout={stdout_excerpt}")
    if not parts:
        return f"{backend_label} execution failed (success=false)."
    return f"{backend_label} execution failed: {'; '.join(parts)}"


_PARTIAL_COMPLETION_PATTERNS: List[re.Pattern] = [
    re.compile(r"(?:processed|completed|finished|done)\s+(\d+)\s*(?:of|/)\s*(\d+)", re.IGNORECASE),
    re.compile(r"(\d+)\s*/\s*(\d+)\s+(?:cell\s*types?|samples?|items?|files?|tasks?)", re.IGNORECASE),
]

_WARNING_LINE_PATTERNS: List[re.Pattern] = [
    re.compile(r"(?:^|\s)(?:warning|warn)\s*[:：]", re.IGNORECASE),
    re.compile(r"(?:^|\s)error\s*[:：]", re.IGNORECASE),
    re.compile(r"(?:^|\s)(?:skipping|skipped)\s", re.IGNORECASE),
    re.compile(r"(?:^|\s)failed\s+to\s+(?:process|load|read|write|open|parse)", re.IGNORECASE),
    re.compile(r"Traceback\s*\(most\s+recent\s+call\s+last\)", re.IGNORECASE),
]


def _detect_partial_completion(
    stdout: str,
    stderr: str,
    produced_files: List[str],
    *,
    success: bool,
) -> Dict[str, Any]:
    """Scan execution output for signs of incomplete processing.

    Returns a dict with:
      - ``warnings``: list of warning-like lines found in output
      - ``partial_completion_suspected``: True if patterns suggest partial work
      - ``partial_ratio``: e.g. "2/6" if a progress pattern was found
    """
    warnings: List[str] = []
    partial_ratio: Optional[str] = None
    partial_suspected = False

    combined = (stdout or "") + "\n" + (stderr or "")
    lines = [ln.strip() for ln in combined.splitlines() if ln.strip()]

    for line in lines:
        for pat in _WARNING_LINE_PATTERNS:
            if pat.search(line):
                compact = line[:200]
                if compact not in warnings:
                    warnings.append(compact)
                break

    if success:
        for pat in _PARTIAL_COMPLETION_PATTERNS:
            m = pat.search(combined)
            if m:
                done_count = int(m.group(1))
                total_count = int(m.group(2))
                if 0 < done_count < total_count:
                    partial_ratio = f"{done_count}/{total_count}"
                    partial_suspected = True
                    break

    if success and not produced_files:
        partial_suspected = True

    # Cap warnings to avoid payload bloat
    warnings = warnings[:20]

    result: Dict[str, Any] = {}
    if warnings:
        result["output_warnings"] = warnings
    if partial_suspected:
        result["partial_completion_suspected"] = True
    if partial_ratio:
        result["partial_ratio"] = partial_ratio
    return result


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
_DEFAULT_API_MODEL = "qwen3.6-plus"
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

def _build_qwen_code_subprocess_env() -> Dict[str, str]:
    """Build a subprocess environment for Qwen Code CLI.

    Uses OpenAI-compatible auth pointing at dashscope.

    The ``qwen`` CLI shebang is ``#!/usr/bin/env node`` and requires
    Node >= 20.  When nvm is loaded in the shell, its node path may
    shadow the conda environment's node (v20).  We detect the conda
    env's bin directory and prepend it to PATH so the correct node is
    resolved first.
    """
    env_map = dict(os.environ)

    # --- Ensure conda-env node takes priority over nvm ---
    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    if conda_prefix:
        conda_bin = os.path.join(conda_prefix, "bin")
        current_path = env_map.get("PATH", "")
        # Only prepend if not already at the front.
        if not current_path.startswith(conda_bin):
            env_map["PATH"] = conda_bin + os.pathsep + current_path

    # Inject OpenAI-compatible credentials for QC.
    qwen_key = str(os.getenv("QWEN_API_KEY", "")).strip()
    if qwen_key:
        env_map["OPENAI_API_KEY"] = qwen_key
    env_map["OPENAI_BASE_URL"] = (
        str(os.getenv("QWEN_CODE_BASE_URL", "")).strip()
        or _DEFAULT_QC_BASE_URL
    )
    # Remove variables that might confuse QC.
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


def _qwen_code_cli_available() -> bool:
    if shutil.which("qwen") is None:
        return False
    env_map = _build_qwen_code_subprocess_env()
    return _validate_qwen_code_config(env_map) is None


def _resolve_code_executor_backend(task: str) -> tuple[str, str, str]:
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


def _build_qwen_code_command(
    *,
    task: str,
    work_dir: str,
    file_prefix: str,
    output_format: str,
    allowed_tools: List[str],
    allowed_dirs: List[str],
    model: Optional[str],
    debug: bool,
    allowed_dirs_info: str,
    qwen_session_id: Optional[str] = None,
    task_subdirs: Sequence[str] = _DEFAULT_TASK_SUBDIRECTORIES,
    execution_spec: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Build the ``qwen`` CLI command for non-interactive prompt execution."""
    task_text = _build_cli_task_contract(task, execution_spec)
    writable_dirs = [name for name in task_subdirs if str(name).strip().lower() != "code"]
    try:
        from app.config.executor_config import get_executor_settings as _get_settings
        _settings = _get_settings()
        _max_turns = str(_settings.qc_max_session_turns)
        _shell_timeout_ms = max(
            1000,
            min(600000, int(getattr(_settings, "qc_shell_timeout_ms", 600000))),
        )
    except Exception:
        _max_turns = "50"
        _shell_timeout_ms = 600000
    enhanced_task = (
        f"[ATOMIC TASK]\n"
        f"Execute the task below as a single unit. Multi-step code execution "
        f"within the task (read data → process → save results → plot) is "
        f"expected and normal — do NOT report that as needing decomposition.\n"
        f"If upstream data or dependencies are missing, unreadable, or schema-"
        f"incompatible, report BLOCKED_DEPENDENCY so the orchestration layer "
        f"can re-run the upstream tasks first.\n"
        f"If an upstream artifact exists and is readable but contains zero rows "
        f"or no significant hits, treat that as a valid zero-result outcome for "
        f"downstream aggregation, visualization, and export tasks. Continue and "
        f"produce empty-but-valid outputs at the required paths (for example "
        f"empty tables, serialized empty objects, placeholder figures, and a "
        f"short summary that explicitly documents zero findings). Do NOT "
        f"fabricate positive signals.\n"
        f"Do NOT silently fabricate or fix upstream outputs yourself — unless "
        f"the task instruction explicitly authorizes you to produce them.\n"
        f"The plan/task contract is authoritative. Required deliverables must "
        f"be produced exactly at the specified paths/patterns. Extra outputs "
        f"are allowed, but they do NOT replace missing required outputs.\n"
        f"Only output BLOCKED_SCOPE if the request is fundamentally outside "
        f"the scope of code execution (e.g. 'plan the entire project' or "
        f"'manage my calendar').\n"
        f"If blocked by missing, unreadable, or schema-incompatible dependencies, output exactly:\n"
        f"  STATUS: BLOCKED_DEPENDENCY\n"
        f"  DETAIL: <which upstream task/data is missing>\n"
        f"If truly out of scope, output exactly:\n"
        f"  {_BLOCK_SCOPE_STATUS}\n"
        f"  {_BLOCK_SCOPE_REASON}\n"
        f"  DETAIL: <one sentence>\n\n"
        f"Workspace: {work_dir}\n"
        f"Output dirs: {_format_task_subdirectories(task_subdirs)}\n"
        f"File prefix: {file_prefix}\n"
        f"Task:\n{task_text}\n\n"
        f"Deliverables:\n"
        f"1. Write scripts under code/ only when needed.\n"
        f"2. Run them and save outputs under {_format_directory_choices(writable_dirs)}.\n"
        f"3. Put publishable deliverable code under results/submission/ "
        f"or results/deliverable/.\n"
        f"4. Return a summary of actual outputs produced.\n"
        f"5. Progress Reporting: When processing multiple items in a loop, "
        f"print progress after each item: print(f'Processed {{i+1}}/{{total}} items'). "
        f"Print final summary: print(f'Completed {{done}}/{{total}} items'). "
        f"Save results after each item, not only at the end.\n"
        f"6. When using the shell tool for installs, builds, tests, or other "
        f"one-shot commands that may exceed two minutes, set its `timeout` "
        f"parameter explicitly to {_shell_timeout_ms} milliseconds instead of "
        f"relying on the default 120000ms timeout.\n"
        f"7. Use background execution only for processes that are meant to "
        f"keep running (servers, watchers, daemons), not for one-shot installs "
        f"or analysis commands.\n"
        f"8. Do NOT modify shared host environments: no global `conda install`, "
        f"`pip install`, `npm install -g`, or writes into shared site-packages.\n"
        f"9. Inside qwen_code, do NOT create a new virtual environment with "
        f"`python -m venv`; lightweight Python dependencies should be installed "
        f"with `python3 -m pip install --user ...` or `python3 -m pip install "
        f"--target <workspace>/vendor ...` instead.\n"
        f"10. If a lightweight dependency is needed before running a script, "
        f"combine the install step and the main script execution in the same "
        f"`run_shell_command` call so the task finishes in one tool invocation.\n"
        f"11. If the dependency requires a heavy solver, compiled stack, or a "
        f"new runtime image/profile, stop and report BLOCKED_DEPENDENCY instead "
        f"of mutating the shared host environment."
        f"{allowed_dirs_info}"
    )
    cmd: List[str] = [
        "qwen",
        "-p", enhanced_task,
        "-o", output_format,
        "--max-session-turns", _max_turns,
        "--approval-mode", "yolo",
        "--auth-type", "openai",
    ]
    if qwen_session_id:
        cmd.extend(["--session-id", qwen_session_id])
    if model:
        cmd.extend(["-m", model])
    if debug:
        cmd.append("-d")
    # QC --allowed-tools takes space-separated array (not comma-joined).
    if allowed_tools:
        cmd.extend(["--allowed-tools"] + list(allowed_tools))
    for abs_path in allowed_dirs:
        cmd.extend(["--add-dir", abs_path])
    return cmd


def _build_qwen_container_mounts(
    *,
    task_work_dir: Path,
    session_dir: Path,
    allowed_dirs: Sequence[str],
) -> List[tuple[str, str]]:
    """Return bind mounts needed for containerized qwen access.

    Every directory exposed to qwen via ``--add-dir`` must also exist inside
    the container at the same absolute path. We mount the minimal covering set:
    skip directories already covered by ``task_work_dir`` or an earlier parent
    mount, while preserving same-path host/container mapping.
    """

    task_root = task_work_dir.resolve()
    candidates: List[Path] = []
    if session_dir.exists():
        candidates.append(session_dir.resolve())
    for raw_dir in allowed_dirs:
        token = str(raw_dir or "").strip()
        if not token:
            continue
        try:
            resolved = Path(token).resolve()
            if not resolved.exists() or not resolved.is_dir():
                continue
        except OSError:
            continue
        candidates.append(resolved)

    ordered: List[Path] = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=lambda item: (len(item.parts), str(item))):
        candidate_str = str(candidate)
        if candidate_str in seen:
            continue
        if _is_path_within(candidate, task_root):
            continue
        if any(_is_path_within(candidate, mounted_parent) for mounted_parent in ordered):
            continue
        ordered.append(candidate)
        seen.add(candidate_str)

    return [(str(path), str(path)) for path in ordered]


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


def _format_cli_acceptance_checks(criteria: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(criteria, dict):
        return []
    checks = criteria.get("checks")
    if not isinstance(checks, list):
        return []

    formatted: List[str] = []
    for raw_check in checks[:12]:
        if not isinstance(raw_check, dict):
            continue
        check_type = str(raw_check.get("type") or "").strip()
        if check_type == "file_exists":
            formatted.append(f"file must exist: {raw_check.get('path')}")
        elif check_type == "file_nonempty":
            formatted.append(f"file must be non-empty: {raw_check.get('path')}")
        elif check_type == "glob_count_at_least":
            pattern = resolve_glob_pattern(raw_check)
            min_count = resolve_glob_min_count(raw_check)
            formatted.append(
                f"at least {min_count} matches for glob: {pattern}"
            )
        elif check_type == "text_contains":
            formatted.append(
                f"text file {raw_check.get('path')} must contain: {raw_check.get('pattern')}"
            )
        else:
            formatted.append(json.dumps(raw_check, ensure_ascii=False))
    return formatted


def _build_cli_task_contract(
    task: str,
    execution_spec: Optional[Dict[str, Any]],
) -> str:
    task_text = str(task or "").strip()
    if not isinstance(execution_spec, dict):
        return task_text

    lines: List[str] = ["[BOUND TASK CONTEXT]"]
    task_id = execution_spec.get("task_id")
    task_name = str(execution_spec.get("task_name") or "").strip()
    task_instruction = str(execution_spec.get("task_instruction") or "").strip()
    dependency_outputs = execution_spec.get("dependency_outputs")

    if task_id is not None:
        lines.append(f"Task ID: {task_id}")
    if task_name:
        lines.append(f"Task Name: {task_name}")

    if task_instruction:
        lines.extend(["", "Atomic task objective:", task_instruction])

    if task_text and task_text != task_instruction:
        lines.extend(["", "Requested execution action:", task_text])

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

    return "\n".join(lines).strip() or task_text


def _format_contract_diff_for_cli(contract_diff: Optional[Dict[str, Any]]) -> str:
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
        ("Wrong-format outputs", "wrong_format_outputs"),
        ("Unexpected extra outputs", "unexpected_outputs"),
        ("Actual outputs observed", "actual_outputs"),
    ):
        joined = _join(key)
        if joined:
            lines.append(f"- {label}: {joined}")
    return "\n".join(lines)


def _build_cli_contract_repair_task(
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
_MAX_STALE_SESSION_ROOT_FILE_BYTES = 1


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
        dest = output_dir / rel
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
) -> List[str]:
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
        return []

    try:
        task_scope_rel = task_resolved.relative_to(session_resolved)
    except ValueError:
        task_scope_rel = Path(task_resolved.parent.name) / task_resolved.name

    dst_root = (session_resolved / "results" / task_scope_rel).resolve()
    if not _is_path_within(dst_root, session_resolved):
        return []

    dst_root.mkdir(parents=True, exist_ok=True)
    promoted: List[str] = []
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
    return promoted


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
        if root.exists():
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
            resolved = candidate.resolve()
            target_dir = resolved if resolved.is_dir() else resolved.parent
            if not target_dir.exists() or not target_dir.is_dir():
                return
        except OSError:
            return
        if not (
            _is_path_within(target_dir, _PROJECT_ROOT)
            or _is_path_within(target_dir, session_dir)
        ):
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
) -> Optional[Dict[str, Any]]:
    if plan_id is None and task_id is None:
        return _build_ad_hoc_execution_spec(task_text)
    if plan_id is None or task_id is None:
        return None

    try:
        from app.routers.chat.code_executor_helpers import extract_task_artifact_paths
        from app.routers.chat.services import plan_repository
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
    dependency_outputs: List[Dict[str, Any]] = []
    dependency_artifact_paths: List[str] = []
    dependency_blockers: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()

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
) -> Dict[str, Any]:
    """Execute a task using the unified local code execution backend.

    Delegates to ``execute_code_locally()`` which handles code generation,
    file-persistent execution, error classification, and LLM-based fixing.
    """
    from app.services.interpreter.code_execution import CodeExecutionSpec, execute_code_locally
    from app.services.llm.llm_service import get_llm_service

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
    if execution_spec and execution_spec.get("dependency_artifact_paths"):
        dependency_paths = execution_spec.get("dependency_artifact_paths") or []
        task_desc += (
            "\nExplicit upstream artifact paths (ABSOLUTE, authoritative):\n"
            + "\n".join(f"- {path}" for path in dependency_paths[:20])
        )

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

    await _report("running", f"Executing generated code via {effective_runtime_mode} runtime")
    structured_spec = None
    if execution_spec:
        structured_spec = CodeExecutionSpec(
            plan_id=execution_spec.get("plan_id"),
            task_id=execution_spec.get("task_id"),
            task_name=execution_spec.get("task_name"),
            task_instruction=execution_spec.get("task_instruction"),
            acceptance_criteria=execution_spec.get("acceptance_criteria"),
            dependency_outputs=list(execution_spec.get("dependency_outputs") or []),
            dependency_artifact_paths=list(execution_spec.get("dependency_artifact_paths") or []),
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
        llm_service=get_llm_service(),
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
        _resolve_code_executor_backend(task)
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
                    if not resolved.exists() or not resolved.is_dir():
                        logger.warning("Ignoring non-directory add_dir path: %s", resolved)
                        continue
                except (OSError, Exception):
                    logger.warning("Ignoring invalid add_dir path: %s", dir_path)
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
                + "\n".join(f"  - {d}" for d in allowed_dirs)
            )

        local_data_dir: Optional[str] = None
        if len(resolved_add_dirs) == 1:
            local_data_dir = resolved_add_dirs[0]
        elif not resolved_add_dirs and len(inferred_task_dirs) == 1:
            local_data_dir = inferred_task_dirs[0]
        elif not resolved_add_dirs and default_data_dir.exists():
            local_data_dir = str(default_data_dir)

        cli_task = _build_cli_task_contract(task, execution_spec)

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

            # Legacy promotion (backward compat)
            session_artifact_paths = _promote_task_results_to_session_root(
                session_dir=session_dir,
                task_work_dir=task_work_dir,
                subdirs=task_subdirs,
            )
            verification_artifact_paths = _build_verification_artifact_paths(
                task_work_dir=task_work_dir,
                subdirs=task_subdirs,
                produced_files=produced_files,
                session_artifact_paths=session_artifact_paths,
                session_dir=session_dir,
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
                "allowed_tools_effective": normalized_allowed_tools,
                "claude_model_effective": None,
                "claude_setting_sources_effective": None,
                "claude_auth_mode_effective": None,
                "code_directory": code_directory,
                "code_file": local_result.get("code_file") or primary_code_file,
                "produced_files": produced_files,
                "produced_files_count": len(produced_files),
                "artifact_paths": verification_artifact_paths,
                "session_artifact_paths": session_artifact_paths,
                # Unified output path (new)
                "output_location": {
                    "type": "task" if resolved_task_id is not None else "tmp",
                    "session_id": effective_session_id,
                    "task_id": resolved_task_id,
                    "ancestor_chain": ancestor_chain,
                    "base_dir": str(unified_output_dir) if unified_output_dir else None,
                    "files": unified_promoted_files,
                },
            }
            if "docker_image_effective" in local_result:
                result_payload["docker_image_effective"] = local_result.get("docker_image_effective")
            if "runtime_failure" in local_result:
                result_payload["runtime_failure"] = bool(local_result.get("runtime_failure"))
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

            # Detect partial completion signals
            local_completion_info = _detect_partial_completion(
                str(local_result.get("stdout") or ""),
                str(local_result.get("stderr") or ""),
                produced_files,
                success=success,
            )
            if local_completion_info:
                result_payload.update(local_completion_info)

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
            subprocess_env = _build_qwen_code_subprocess_env()
            _inject_env_mutation_guard(subprocess_env, str(task_work_dir))
            qc_config_error = _validate_qwen_code_config(subprocess_env)
            if qc_config_error:
                return {"success": False, "error": qc_config_error, "task": task}
            effective_model = (
                str(
                    model
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
                )
                # Wrap with docker exec if running inside a container
                if _docker_container_name:
                    from app.services.terminal.docker_pty_backend import (
                        CONTAINER_EXEC_PATH,
                        QWEN_EXECUTABLE,
                    )

                    return [
                        "docker",
                        "exec",
                        "-e",
                        f"PATH={CONTAINER_EXEC_PATH}",
                        "-w",
                        str(task_work_dir),
                        _docker_container_name,
                        QWEN_EXECUTABLE,
                    ] + bare_cmd[1:]
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
            writable_task_subdirs = [
                name for name in task_subdirs if str(name).strip().lower() != "code"
            ]
            enhanced_task = (
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
                f"mutating the shared host environment."
                f"{allowed_dirs_info}"
            )
            def _rebuild_cli_command(task_override: str) -> List[str]:
                cli_task_override = _build_cli_task_contract(task_override, execution_spec)
                enhanced_task_override = (
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
                    f"Task:\n{cli_task_override}\n\n"
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
                    f"mutating the shared host environment."
                    f"{allowed_dirs_info}"
                )
                rebuilt = [
                    'claude', '-p', enhanced_task_override,
                    '--output-format', output_format,
                    '--max-turns', '50',
                ]
                if debug_log_path is not None:
                    rebuilt.extend(['--debug-file', str(debug_log_path)])
                if effective_model:
                    rebuilt.extend(['--model', effective_model])
                if effective_setting_sources:
                    rebuilt.extend(['--setting-sources', effective_setting_sources])
                rebuilt.extend(['--allowed-tools', ",".join(normalized_allowed_tools)])
                for abs_path in allowed_dirs:
                    rebuilt.extend(['--add-dir', abs_path])
                if skip_permissions:
                    rebuilt.append('--dangerously-skip-permissions')
                return rebuilt
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

        async def _record_stream_line(decoded_line: str, lines, callback, stream_name: str):
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
            nonlocal _qwen_session_id, qwen_shell_recovery
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

                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(task_work_dir),
                    env=subprocess_env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                stdout_task = asyncio.create_task(
                    _read_stream(process.stdout, local_stdout_lines, on_stdout, "stdout")
                )
                stderr_task = asyncio.create_task(
                    _read_stream(process.stderr, local_stderr_lines, on_stderr, "stderr")
                )

                try:
                    await asyncio.gather(stdout_task, stderr_task)
                    local_return_code = await process.wait()
                except (asyncio.CancelledError, Exception) as _wait_exc:
                    try:
                        process.kill()
                        await process.wait()
                    except Exception:
                        pass
                    if isinstance(_wait_exc, asyncio.CancelledError):
                        raise
                    raise

                if local_return_code == 0:
                    break

                _is_scope_block = _detect_scope_blocked("\n".join(local_stdout_lines), None)
                if _is_scope_block:
                    logger.info("[CODE_EXECUTOR_RETRY] Scope block detected, not retrying.")
                    break

                if use_qwen_code_backend:
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

            blocked_detail = _detect_scope_blocked(stdout, output_data)
            if blocked_detail:
                success = False

            produced_files = _collect_run_artifacts(run_dir=task_work_dir, subdirs=task_subdirs)

            # --- Promote results to unified output directory ---
            unified_promoted_files_qwen: List[str] = []
            if unified_output_dir:
                unified_promoted_files_qwen = _promote_results_to_unified_dir(
                    scratch_dir=task_work_dir,
                    output_dir=unified_output_dir,
                    subdirs=task_subdirs,
                    session_dir=session_dir,
                )

            session_artifact_paths = _promote_task_results_to_session_root(
                session_dir=session_dir,
                task_work_dir=task_work_dir,
                subdirs=task_subdirs,
            )
            verification_artifact_paths = _build_verification_artifact_paths(
                task_work_dir=task_work_dir,
                subdirs=task_subdirs,
                produced_files=produced_files,
                session_artifact_paths=session_artifact_paths,
                session_dir=session_dir,
            )
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
                        session_artifact_paths = _promote_task_results_to_session_root(
                            session_dir=session_dir,
                            task_work_dir=task_work_dir,
                            subdirs=task_subdirs,
                        )
                        verification_artifact_paths = _build_verification_artifact_paths(
                            task_work_dir=task_work_dir,
                            subdirs=task_subdirs,
                            produced_files=produced_files,
                            session_artifact_paths=session_artifact_paths,
                            session_dir=session_dir,
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
                            if finalization.final_status == "failed":
                                success = False
                                contract_error_summary = _summarize_verification_failures(verification)
                                contract_fix_guidance = _format_verification_guidance(verification)
                        else:
                            verification_status = "not_run"
                            failure_kind = "execution_failed"
                            contract_diff = None
                            verification = None
                            plan_patch_suggestion = None
                            contract_error_summary = None
                            contract_fix_guidance = None
                        if not success and not blocked_detail and contract_error_summary is None and verification_status == "failed":
                            contract_error_summary = _summarize_verification_failures(verification)
                            contract_fix_guidance = _format_verification_guidance(verification)
                    elif finalization.final_status == "failed":
                        success = False
                        contract_error_summary = _summarize_verification_failures(verification)
                        contract_fix_guidance = _format_verification_guidance(verification)
                except Exception as contract_exc:
                    logger.warning("CLI contract verification failed unexpectedly: %s", contract_exc)

            contract_error_summary, contract_fix_guidance = _clear_stale_contract_failure_state(
                success=success,
                verification_status=verification_status,
                contract_error_summary=contract_error_summary,
                contract_fix_guidance=contract_fix_guidance,
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
            "code_directory": code_directory,
            "code_file": primary_code_file,
            "produced_files": produced_files,
            "produced_files_count": len(produced_files),
            "artifact_paths": verification_artifact_paths,
            "session_artifact_paths": session_artifact_paths,
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
