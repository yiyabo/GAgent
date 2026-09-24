"""Code-mode (execute_code) configuration knobs.

Everything here is env-driven and defaults to OFF/safe: the tool is only
offered to the LLM when ``CODE_MODE_ENABLED=1``. Limit knobs exist so tests
and operators can tighten the envelope without code changes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

ENV_ENABLED = "CODE_MODE_ENABLED"
ENV_ALLOWED_TOOLS = "CODE_MODE_ALLOWED_TOOLS"
ENV_CELL_TIMEOUT = "CODE_MODE_CELL_TIMEOUT_SECONDS"
ENV_KILL_GRACE = "CODE_MODE_KILL_GRACE_SECONDS"
ENV_MAX_TOOL_CALLS = "CODE_MODE_MAX_TOOL_CALLS_PER_CELL"
ENV_MAX_KERNELS = "CODE_MODE_MAX_KERNELS"
ENV_KERNEL_IDLE = "CODE_MODE_KERNEL_IDLE_SECONDS"
ENV_SCRATCH_DIR = "CODE_MODE_SCRATCH_DIR"

# v1 allowlist: read-only / information tools only. Enforcement lives in the
# RPC server (tool_box/tools_impl/execute_code/rpc.py), not in the stubs.
# graph_rag is deliberately absent: its own schema declares LEGACY and
# lightrag_query covers the same ground; CODE_MODE_ALLOWED_TOOLS can add it back.
DEFAULT_ALLOWED_TOOLS = (
    "web_search",
    "literature_pipeline",
    "document_reader",
    "vision_reader",
    "lightrag_query",
    "sequence_fetch",
    "url_fetch",
)

DEFAULT_CELL_TIMEOUT_SECONDS = 300
DEFAULT_KILL_GRACE_SECONDS = 5.0
DEFAULT_MAX_TOOL_CALLS_PER_CELL = 50
DEFAULT_TOOL_CALL_TIMEOUT_SECONDS = 300
DEFAULT_MAX_KERNELS = 4
DEFAULT_KERNEL_IDLE_SECONDS = 1800

MAX_STDOUT_BYTES = 50_000
MAX_STDERR_BYTES = 10_000
MAX_SPILLED_STDOUT_BYTES = 5_000_000
# Runner-side cap on captured Python-level stdout/stderr; the host re-applies
# MAX_STDOUT_BYTES/MAX_STDERR_BYTES on top.
RUNNER_CAPTURE_BYTES = 1_000_000

_REPO_ROOT = Path(__file__).resolve().parents[3]


def code_mode_enabled() -> bool:
    return os.environ.get(ENV_ENABLED, "").strip() == "1"


def allowed_tools() -> List[str]:
    raw = os.environ.get(ENV_ALLOWED_TOOLS, "").strip()
    if not raw:
        return list(DEFAULT_ALLOWED_TOOLS)
    names = [part.strip() for part in raw.split(",") if part.strip()]
    # preserve order, drop duplicates
    return list(dict.fromkeys(names))


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return default


def cell_timeout_seconds() -> int:
    return _int_env(ENV_CELL_TIMEOUT, DEFAULT_CELL_TIMEOUT_SECONDS)


def kill_grace_seconds() -> float:
    return _float_env(ENV_KILL_GRACE, DEFAULT_KILL_GRACE_SECONDS)


def max_tool_calls_per_cell() -> int:
    return _int_env(ENV_MAX_TOOL_CALLS, DEFAULT_MAX_TOOL_CALLS_PER_CELL)


def max_kernels() -> int:
    return _int_env(ENV_MAX_KERNELS, DEFAULT_MAX_KERNELS)


def kernel_idle_seconds() -> int:
    return _int_env(ENV_KERNEL_IDLE, DEFAULT_KERNEL_IDLE_SECONDS)


def resolve_scratch_dir(work_dir: str = "") -> Path:
    """Scratch root for code mode: kernels, RPC stubs and stdout spills.

    Lives under the session work_dir (never /tmp — production excludes /tmp
    from scratch retention). Falls back to CODE_MODE_SCRATCH_DIR, then a
    repo-local runtime dir for contexts without a session workspace.
    """
    if work_dir:
        return Path(work_dir) / "scratch" / "code_mode"
    override = os.environ.get(ENV_SCRATCH_DIR, "").strip()
    if override:
        return Path(override)
    return _REPO_ROOT / "runtime" / "code_mode"


def resolve_child_cwd(work_dir: str, fallback_dir: Path) -> Path:
    """Cell working directory: the session work_dir when it exists."""
    if work_dir:
        candidate = Path(work_dir)
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            pass
    return fallback_dir


def session_identity(tool_context: Optional[object]) -> str:
    """Kernel owner key: the chat session id, stable across turns."""
    session_id = getattr(tool_context, "session_id", None) if tool_context is not None else None
    return str(session_id or "").strip() or "default"
