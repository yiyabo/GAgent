from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .context import ToolContext

_REPO_ROOT_RELATIVE_PREFIXES = {"results", "runtime", "data"}


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
    if first in _REPO_ROOT_RELATIVE_PREFIXES:
        return (repo_root / candidate).resolve(strict=False)

    if treat_bare_as_results_output and len(parts) == 1 and text not in {".", ".."}:
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
