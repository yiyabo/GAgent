"""Background task classification helpers for chat action runs."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Background task classification
# ---------------------------------------------------------------------------

# Action names that correspond to long-running background tasks.
_BACKGROUND_TOOL_NAMES: Dict[str, str] = {
    "phagescope": "phagescope",
    "claude_code": "claude_code",
}

# PhageScope actions that should run synchronously (not dispatched to background).
# Only "submit" is truly long-running; save_all/result/task_detail are fast downloads.
_PHAGESCOPE_SYNC_ACTIONS = {"save_all", "result", "quality", "task_detail", "task_list", "task_log", "input_check", "download"}

_BACKGROUND_PLAN_OPS = {"create_plan", "optimize_plan"}


def _classify_background_category(
    actions: List[Any],
    job_snapshot: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Classify an action run as a long-running background category.

    Returns ``"phagescope"``, ``"claude_code"``, or ``"task_creation"``
    when the actions indicate a long-running background task, otherwise
    ``None``.
    """
    # 1. Check job_type from snapshot (most reliable if available)
    job_type = str((job_snapshot or {}).get("job_type") or "").strip().lower()
    if job_type == "phagescope_track":
        return "phagescope"
    if job_type == "plan_decompose":
        return "task_creation"

    # 2. Scan individual actions
    for action in actions:
        kind = getattr(action, "kind", None) or ""
        name = str(getattr(action, "name", None) or "").strip().lower()
        if kind == "tool_operation" and name in _BACKGROUND_TOOL_NAMES:
            # PhageScope: only "submit" is truly long-running.
            # save_all/result/quality etc. are fast and should run synchronously
            # so the analysis chain (save_all → read files → synthesize) stays in one turn.
            if name == "phagescope":
                params = getattr(action, "parameters", None) or {}
                ps_action = str(params.get("action") or "").strip().lower()
                if ps_action in _PHAGESCOPE_SYNC_ACTIONS:
                    continue  # Don't classify as background
            return _BACKGROUND_TOOL_NAMES[name]
        if kind == "plan_operation" and name in _BACKGROUND_PLAN_OPS:
            return "task_creation"

    return None


def _sse_message(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
