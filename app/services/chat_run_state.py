"""Chat-run lifecycle state machine — single choke point for status writes.

All ``chat_runs.status`` mutations go through :func:`transition_chat_run_status`,
which enforces the transition table atomically (``UPDATE ... WHERE status IN
<allowed-from>``). Illegal transitions are logged and skipped; set
``CHAT_RUN_STATE_STRICT=1`` to raise instead (used by the test-suite).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional, Set

logger = logging.getLogger(__name__)

ACTIVE_STATUSES: Set[str] = {"queued", "running"}
TERMINAL_STATUSES: Set[str] = {"succeeded", "failed", "cancelled"}
ALL_STATUSES: Set[str] = ACTIVE_STATUSES | TERMINAL_STATUSES

ALLOWED_TRANSITIONS = {
    "queued": {"running", "failed", "cancelled"},
    "running": {"succeeded", "failed", "cancelled"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
}


class IllegalChatRunTransition(RuntimeError):
    """Raised in strict mode when a status write violates the transition table."""


def is_transition_allowed(from_status: Optional[str], to_status: str) -> bool:
    if from_status == to_status:
        return True  # idempotent self-loop (no-op at the SQL layer)
    if from_status is None:
        return False
    return to_status in ALLOWED_TRANSITIONS.get(from_status or "", set())


def _strict_enabled() -> bool:
    return str(os.getenv("CHAT_RUN_STATE_STRICT") or "").strip() in ("1", "true", "yes")


def check_transition(run_id: str, from_status: Optional[str], to_status: str) -> None:
    """Log (or raise in strict mode) on an illegal transition attempt."""
    message = (
        "illegal chat_run transition run=%s from=%s to=%s"
        % (run_id, from_status, to_status)
    )
    if _strict_enabled():
        raise IllegalChatRunTransition(message)
    logger.warning(message)


def transition_chat_run_status(
    conn: Any,
    run_id: str,
    to_status: str,
    *,
    error: Optional[str] = None,
    assistant_message_id: Optional[int] = None,
    started: bool = False,
) -> bool:
    """Atomically move ``run_id`` to ``to_status`` if the transition is legal.

    ``started=True`` additionally stamps ``started_at`` (first transition into
    ``running``); terminal transitions stamp ``finished_at``. A transition to
    the current status is an idempotent no-op (terminal timestamps are not
    rewritten). Returns True when the row now holds ``to_status``.
    """
    from_sets = {
        from_status
        for from_status, targets in ALLOWED_TRANSITIONS.items()
        if to_status in targets
    }
    if not from_sets:
        # Unknown target state or terminal target with no inbound edges.
        check_transition(run_id, None, to_status)
        return False

    placeholders = ",".join("?" for _ in sorted(from_sets))
    set_clauses = ["status = ?"]
    params: list[Any] = [to_status]
    if started:
        set_clauses.append("started_at = COALESCE(started_at, CURRENT_TIMESTAMP)")
    if to_status in TERMINAL_STATUSES:
        set_clauses.append("error = ?")
        set_clauses.append(
            "assistant_message_id = COALESCE(?, assistant_message_id)"
        )
        set_clauses.append("finished_at = CURRENT_TIMESTAMP")
        params.extend([error, assistant_message_id])
    params.append(run_id)
    params.extend(sorted(from_sets))

    cursor = conn.execute(
        f"""
        UPDATE chat_runs
        SET {", ".join(set_clauses)}
        WHERE run_id = ? AND status IN ({placeholders})
        """,
        tuple(params),
    )
    if cursor.rowcount == 1:
        return True

    row = conn.execute(
        "SELECT status FROM chat_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        logger.warning("chat_run transition target missing run=%s to=%s", run_id, to_status)
        return False
    current = str(row["status"])
    if current == to_status:
        return True  # idempotent repeat (e.g. double finish paths)
    check_transition(run_id, current, to_status)
    return False
