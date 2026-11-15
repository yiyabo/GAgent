"""Read-only queries backing the Run Inspector.

Nothing here writes. Streaming text events (token deltas, reasoning deltas,
tool stdout) are counted but not loaded, so a long run with tens of thousands
of delta rows stays cheap to inspect. Every query is a literal SQL string with
bound parameters.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.database import get_db

# Keep in sync with the NOT IN list in fetch_inspection_events (asserted by tests).
STREAMING_EVENT_TYPES: tuple[str, ...] = (
    "delta",
    "thinking_delta",
    "reasoning_delta",
    "tool_output",
)


def get_run_for_inspection(run_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT run_id, session_id, owner_id, status, user_message_id, assistant_message_id,
                   error, request_json, created_at, started_at, finished_at, last_event_seq
            FROM chat_runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
    return dict(row) if row else None


def get_run_by_assistant_message(
    session_id: str, message_id: int
) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT run_id, session_id, owner_id, status, user_message_id, assistant_message_id,
                   error, request_json, created_at, started_at, finished_at, last_event_seq
            FROM chat_runs
            WHERE session_id = ? AND assistant_message_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id, message_id),
        ).fetchone()
    return dict(row) if row else None


def _decode_payload(raw: Any) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {"type": "parse_error"}
    return payload if isinstance(payload, dict) else {"type": "parse_error"}


def fetch_inspection_events(run_id: str) -> list[tuple[int, str, dict[str, Any]]]:
    """Return (seq, created_at, payload) for structural events only."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT seq, created_at, payload_json
            FROM chat_run_events
            WHERE run_id = ?
              AND event_type NOT IN ('delta', 'thinking_delta', 'reasoning_delta', 'tool_output')
            ORDER BY seq ASC
            """,
            (run_id,),
        ).fetchall()
    return [
        (row["seq"], str(row["created_at"] or ""), _decode_payload(row["payload_json"]))
        for row in rows
    ]


def count_run_event_types(run_id: str) -> dict[str, int]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT event_type, COUNT(*) AS n
            FROM chat_run_events
            WHERE run_id = ?
            GROUP BY event_type
            """,
            (run_id,),
        ).fetchall()
    return {str(row["event_type"]): row["n"] for row in rows}


def fetch_run_usage(run_id: str) -> list[dict[str, Any]]:
    """Usage rows attributed to the run; an absent usage table means no usage."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT phase, call_purpose, tool_name, call_status, model,
                       prompt_tokens, completion_tokens, total_tokens,
                       cache_read_tokens, duration_ms, estimated_cost, cost_currency
                FROM llm_usage_log
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        text = str(exc).lower()
        if "no such table" in text or "no such column" in text:
            return []
        raise
    return [dict(row) for row in rows]
