"""Run-budget wrap-up notice (Hermes ``RUN_BUDGET_WRAPUP_NOTICE``).

When 80% of the run's time budget is spent, the model is told once to finish up.
Like Hermes, the notice rides on the newest tool result instead of becoming a
new user turn, so the prompt prefix before it stays byte-identical for the
provider's cache.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

WRAPUP_FRACTION = 0.8

RUN_BUDGET_WRAPUP_NOTICE = (
    "\n\n[Run budget notice: about 80% of the time allowed for this run is used. "
    "Wrap up now: finish only what is essential, save any file you still owe the user, "
    "and then give your final answer. Do not start new long-running work.]"
)


def wrapup_due(fraction: float = WRAPUP_FRACTION) -> bool:
    from app.services.run_budget import current_run_budget

    budget = current_run_budget()
    if budget is None:
        return False
    work_seconds = (
        budget.total_seconds
        - budget.close_reserve_seconds
        - budget.effective_synthesis_reserve_seconds
    )
    if work_seconds <= 0:
        return False
    return time.monotonic() - budget.started_at >= fraction * work_seconds


def append_wrapup_notice(messages: List[Dict[str, Any]]) -> bool:
    """Append the notice to the newest tool message; False when there is none."""
    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = content + RUN_BUDGET_WRAPUP_NOTICE
        elif isinstance(content, list):
            message["content"] = [*content, {"type": "text", "text": RUN_BUDGET_WRAPUP_NOTICE.strip()}]
        else:
            return False
        return True
    return False
