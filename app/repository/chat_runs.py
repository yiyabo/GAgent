"""Persistence for resilient chat runs (SSE replay + session resume)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_db


def create_chat_run(
    run_id: str,
    session_id: str,
    request_json: str,
    *,
    idempotency_key: Optional[str] = None,
    user_message_id: Optional[int] = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO chat_runs (
                run_id, session_id, status, request_json,
                idempotency_key, user_message_id
            )
            VALUES (?, ?, 'queued', ?, ?, ?)
            """,
            (run_id, session_id, request_json, idempotency_key, user_message_id),
        )
        conn.commit()


def mark_chat_run_started(run_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE chat_runs
            SET status = 'running',
                started_at = COALESCE(started_at, CURRENT_TIMESTAMP)
            WHERE run_id = ?
            """,
            (run_id,),
        )
        conn.commit()


def mark_chat_run_finished(
    run_id: str,
    status: str,
    *,
    error: Optional[str] = None,
    assistant_message_id: Optional[int] = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE chat_runs
            SET status = ?,
                error = ?,
                assistant_message_id = COALESCE(?, assistant_message_id),
                finished_at = CURRENT_TIMESTAMP
            WHERE run_id = ?
            """,
            (status, error, assistant_message_id, run_id),
        )
        conn.commit()


def get_chat_run(run_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT run_id, session_id, status, user_message_id, assistant_message_id,
                   idempotency_key, error, request_json, created_at, started_at,
                   finished_at, last_event_seq
            FROM chat_runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_session_runs(
    session_id: str,
    *,
    status: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 50))
    clauses = ["session_id = ?"]
    params: List[Any] = [session_id]
    if status:
        clauses.append("status = ?")
        params.append(status)
    where_sql = " AND ".join(clauses)
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT run_id, session_id, status, created_at, started_at, finished_at,
                   last_event_seq, error
            FROM chat_runs
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [dict(r) for r in rows]


def append_chat_run_event(
    run_id: str,
    payload: Dict[str, Any],
) -> int:
    """Append one event; returns monotonic seq for this run (>= 0)."""
    event_type = str(payload.get("type") or "unknown")
    payload_json = json.dumps(payload, ensure_ascii=False)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO chat_run_events (run_id, seq, event_type, payload_json)
            VALUES (
                ?,
                (SELECT COALESCE(MAX(seq), -1) + 1 FROM chat_run_events WHERE run_id = ?),
                ?,
                ?
            )
            """,
            (run_id, run_id, event_type, payload_json),
        )
        row = conn.execute(
            "SELECT MAX(seq) AS s FROM chat_run_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        seq = int(row["s"]) if row and row["s"] is not None else -1
        conn.execute(
            "UPDATE chat_runs SET last_event_seq = ? WHERE run_id = ?",
            (seq, run_id),
        )
        conn.commit()
    return seq


def batch_append_chat_run_events(
    run_id: str,
    payloads: List[Dict[str, Any]],
) -> List[int]:
    """Append multiple events in a single transaction; returns list of seq values.

    This is significantly faster than calling ``append_chat_run_event`` in a loop
    because it amortises the transaction overhead (commit + WAL sync) across all
    events in the batch.
    """
    if not payloads:
        return []

    seqs: List[int] = []
    with get_db() as conn:
        # Determine starting seq for the batch
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), -1) AS s FROM chat_run_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        next_seq = (int(row["s"]) if row and row["s"] is not None else -1) + 1

        for payload in payloads:
            event_type = str(payload.get("type") or "unknown")
            payload_json = json.dumps(payload, ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO chat_run_events (run_id, seq, event_type, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, next_seq, event_type, payload_json),
            )
            seqs.append(next_seq)
            next_seq += 1

        last_seq = seqs[-1] if seqs else -1
        conn.execute(
            "UPDATE chat_runs SET last_event_seq = ? WHERE run_id = ?",
            (last_seq, run_id),
        )
        conn.commit()
    return seqs


def fetch_events_after(run_id: str, after_seq: int) -> List[Tuple[int, Dict[str, Any]]]:
    """Return (seq, payload) rows with seq > after_seq, ordered by seq."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT seq, payload_json
            FROM chat_run_events
            WHERE run_id = ? AND seq > ?
            ORDER BY seq ASC
            """,
            (run_id, after_seq),
        ).fetchall()
    out: List[Tuple[int, Dict[str, Any]]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                payload = {"type": "unknown", "raw": row["payload_json"]}
        except json.JSONDecodeError:
            payload = {"type": "parse_error", "raw": row["payload_json"]}
        out.append((int(row["seq"]), payload))
    return out


def get_last_event_seq(run_id: str) -> int:
    row = get_chat_run(run_id)
    if not row:
        return -1
    return int(row.get("last_event_seq") or -1)
