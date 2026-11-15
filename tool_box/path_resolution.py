from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .context import ToolContext

_REPO_ROOT_RELATIVE_PREFIXES = {"results", "runtime", "data"}


def _resolve_task_output_base(tool_context: Optional[ToolContext]) -> Optional[Path]:
    if tool_context is None:
        return None
    if not any(
        (
            tool_context.session_id,
            tool_context.plan_id is not None,
            tool_context.task_id is not None,
        )
    ):
        return None
    work_dir = str(tool_context.work_dir or "").strip()
    if not work_dir:
        return None
    return Path(work_dir).expanduser().resolve(strict=False)


def get_repo_root() -> Path:
    return Path(os.getcwd()).resolve()


def _session_cwd(tool_context: ToolContext | None) -> Path | None:
    """Session runtime dir that relative inputs mean in the chat lane.

    execute_code already runs with this directory as its cwd, so relative paths
    passed to other tools in the same session must mean the same place. The
    chat action bridge gives each tool a scratch work_dir for its own outputs
    (``<session>/raw_files/chat_tools/<tool>``); that scratch is not where the
    user's relative paths point. Any other explicit work_dir (plan task lanes)
    wins and keeps its own rules.
    """
    if tool_context is None:
        return None
    session_id = str(tool_context.session_id or "").strip()
    if not session_id:
        return None
    try:
        from app.services.session_paths import get_runtime_session_dir

        session_dir = get_runtime_session_dir(session_id).resolve(strict=False)
    except Exception:
        return None
    work_dir = str(tool_context.work_dir or "").strip()
    if not work_dir:
        return session_dir
    chat_scratch = session_dir / "raw_files" / "chat_tools"
    try:
        Path(work_dir).expanduser().resolve(strict=False).relative_to(chat_scratch)
    except ValueError:
        return None
    return session_dir


def _resolve_results_path(
    candidate: Path,
    *,
    repo_root: Path,
    task_output_base: Path | None,
    session_cwd: Path | None,
    treat_bare_as_results_output: bool,
) -> Path:
    parts = candidate.parts
    if task_output_base is not None and treat_bare_as_results_output:
        relative = Path(*parts[1:]) if len(parts) > 1 else Path()
        return (task_output_base / relative).resolve(strict=False)
    # In a chat session, results/ is the session's own folder (where execute_code
    # writes); the repo-level results/ is outside session scope and is denied.
    if session_cwd is not None and not treat_bare_as_results_output:
        return (session_cwd / candidate).resolve(strict=False)
    return (repo_root / candidate).resolve(strict=False)


def resolve_tool_path(
    raw_path: str,
    *,
    tool_context: Optional[ToolContext] = None,
    treat_bare_as_results_output: bool = False,
) -> Path:
    text = str(raw_path or "").strip()
    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False)

    repo_root = get_repo_root()
    if not text:
        return repo_root

    parts = candidate.parts
    first = parts[0] if parts else ""
    task_output_base = _resolve_task_output_base(tool_context)
    session_cwd = _session_cwd(tool_context)
    if first == "results":
        return _resolve_results_path(
            candidate,
            repo_root=repo_root,
            task_output_base=task_output_base,
            session_cwd=session_cwd,
            treat_bare_as_results_output=treat_bare_as_results_output,
        )
    if first in _REPO_ROOT_RELATIVE_PREFIXES:
        return (repo_root / candidate).resolve(strict=False)

    if treat_bare_as_results_output and len(parts) == 1 and text not in {".", ".."}:
        if task_output_base is not None:
            return (task_output_base / candidate.name).resolve(strict=False)
        return (repo_root / "results" / candidate.name).resolve(strict=False)

    if session_cwd is not None:
        base_dir = session_cwd
    elif tool_context and str(tool_context.work_dir or "").strip():
        base_dir = Path(tool_context.work_dir).expanduser().resolve(strict=False)
    else:
        base_dir = repo_root
    return (base_dir / candidate).resolve(strict=False)


def resolve_tool_path_str(
    raw_path: str,
    *,
    tool_context: Optional[ToolContext] = None,
    treat_bare_as_results_output: bool = False,
) -> str:
    return str(
        resolve_tool_path(
            raw_path,
            tool_context=tool_context,
            treat_bare_as_results_output=treat_bare_as_results_output,
        )
    )
