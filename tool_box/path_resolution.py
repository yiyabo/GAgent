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
    if first == "results":
        relative = Path(*parts[1:]) if len(parts) > 1 else Path()
        if task_output_base is not None and treat_bare_as_results_output:
            return (task_output_base / relative).resolve(strict=False)
        return (repo_root / candidate).resolve(strict=False)
    if first in _REPO_ROOT_RELATIVE_PREFIXES:
        return (repo_root / candidate).resolve(strict=False)

    if treat_bare_as_results_output and len(parts) == 1 and text not in {".", ".."}:
        if task_output_base is not None:
            return (task_output_base / candidate.name).resolve(strict=False)
        return (repo_root / "results" / candidate.name).resolve(strict=False)

    base_dir = repo_root
    if tool_context and str(tool_context.work_dir or "").strip():
        base_dir = Path(tool_context.work_dir).expanduser().resolve(strict=False)
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
