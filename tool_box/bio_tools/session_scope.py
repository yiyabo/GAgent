"""A bio_tools call may only read what the calling session may read.

The file tool and the code kernel keep a session out of other sessions' files, but a bio_tools run reads
whatever its host runner can reach, which includes every session's directory. Without this check
``seqkit head`` on another session's upload printed it back to the user.
"""

from __future__ import annotations

import os
from typing import Any, Iterator, Optional, Sequence


def _path_values(input_file: Optional[str], params: Optional[dict]) -> Iterator[str]:
    """Every value that is an absolute (or home-relative) path, whatever the parameter is called."""
    raw: list = [input_file]
    for value in (params or {}).values():
        if isinstance(value, (list, tuple)):
            raw.extend(value)
        else:
            raw.append(value)
    for item in raw:
        if not isinstance(item, str):
            continue
        # bam_files and bins take several paths in one comma-separated value.
        for part in item.split(","):
            part = part.strip()
            if part.startswith("/") or part.startswith("~"):
                yield part


def _within(path: str, root: str) -> bool:
    root = root.rstrip("/") or "/"
    return path == root or path.startswith(root + "/")


def scope_error(
    input_file: Optional[str],
    params: Optional[dict],
    tool_context: Any,
    *,
    session_id: Optional[str] = None,
    shared_roots: Sequence[str] = (),
) -> Optional[str]:
    """The reason a path in this call is off limits for the session, or None.

    ``shared_roots`` are directories every session may read (the tool runs' own directories, which a later
    step uses as input and whose names cannot be guessed).
    """
    from tool_box.context import ToolContext
    from tool_box.tools_impl import file_operations

    context = tool_context if getattr(tool_context, "session_id", None) else None
    if context is None and session_id:
        context = ToolContext(session_id=session_id)
    if context is None:
        return None

    shared = [os.path.realpath(root) for root in shared_roots if root]
    for value in _path_values(input_file, params):
        expanded = os.path.expanduser(value)
        real = os.path.realpath(expanded)
        if any(_within(real, root) for root in shared):
            continue
        error = file_operations.path_scope_error(expanded, context)
        if error:
            return error
    return None
