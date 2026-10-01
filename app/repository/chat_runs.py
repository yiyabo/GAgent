"""Persistence for resilient chat runs (SSE replay + session resume)."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_db


@contextmanager
def _write_transaction(conn):
    """Keep lifecycle writes atomic, including on autocommit pool connections."""
    nested = conn.in_transaction
    conn.execute("SAVEPOINT chat_run_write" if nested else "BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if nested:
            conn.execute("ROLLBACK TO chat_run_write")
            conn.execute("RELEASE chat_run_write")
        else:
            conn.rollback()
        raise
    else:
        if nested:
            conn.execute("RELEASE chat_run_write")
        else:
            conn.commit()


def _owns_active_run(conn, run_id: str, worker_id: Optional[str], *, allow_terminal: bool = False) -> bool:
    if worker_id is None:
        return True
    ownership_clause = "status IN ('queued', 'running') AND lease_expires_at > datetime('now')"
    if allow_terminal:
        # A terminal row never heartbeats. Only the claim that atomically won
        # its terminal event may finish message persistence after TTL expiry.
        # Reaper errors deliberately carry no terminal_claim_id.
        ownership_clause = f"""({ownership_clause}) OR (
            status IN ('succeeded', 'failed', 'cancelled') AND EXISTS (
                SELECT 1 FROM chat_run_events e WHERE e.run_id = chat_runs.run_id
                  AND e.event_type IN ('final', 'error')
                  AND json_extract(e.payload_json, '$.terminal_claim_id') = chat_runs.worker_id
            ))"""
    return conn.execute(
        f"""SELECT 1 FROM chat_runs WHERE run_id = ? AND worker_id = ?
           AND ({ownership_clause})""",
        (run_id, worker_id),
    ).fetchone() is not None


@contextmanager
def guard_chat_run_assistant_save(conn, session_id: str, role: str):
    """Fence active saves; a recorded terminal winner retains save authority.

    Lease expiry/release after an owned final commit cannot drop its message.
    A reaper-interrupted claim has no winning event marker and stays fenced.
    """
    from app.services.chat_run_state import chat_run_claim
    from app.services.cancellation import current_cancel_token

    claim = chat_run_claim.get()
    if role != "assistant" or claim is None:
        yield None
        return
    token = current_cancel_token()
    run_id, worker_id = claim
    with _write_transaction(conn):
        if token is not None and token.closed:
            yield False
            return
        if not _owns_active_run(conn, run_id, worker_id, allow_terminal=True):
            yield False
            return
        row = conn.execute(
            """SELECT run_id, assistant_message_id FROM chat_runs
               WHERE run_id = ? AND session_id = ? AND worker_id = ?""",
            (run_id, session_id, worker_id),
        ).fetchone()
        yield row if row is not None else False


def create_chat_run(
    run_id: str,
    session_id: str,
    request_json: str,
    *,
    owner_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    user_message_id: Optional[int] = None,
) -> None:
    resolved_owner_id = str(owner_id or "").strip()
    if not resolved_owner_id:
        from app.routers.chat.session_helpers import lookup_session_owner

        try:
            resolved_owner_id = lookup_session_owner(session_id) or "legacy-local"
        except Exception:
            resolved_owner_id = "legacy-local"
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO chat_runs (
                run_id, session_id, owner_id, status, request_json,
                idempotency_key, user_message_id
            )
            VALUES (?, ?, ?, 'queued', ?, ?, ?)
            """,
            (
                run_id,
                session_id,
                resolved_owner_id,
                request_json,
                idempotency_key,
                user_message_id,
            ),
        )
        conn.commit()


def set_chat_run_user_message_id(run_id: str, user_message_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE chat_runs
            SET user_message_id = COALESCE(user_message_id, ?)
            WHERE run_id = ?
            """,
            (user_message_id, run_id),
        )
        conn.commit()


def mark_chat_run_started(run_id: str, *, worker_id: Optional[str] = None) -> bool:
    from app.services.chat_run_state import transition_chat_run_status

    with get_db() as conn:
        with _write_transaction(conn):
            if not _owns_active_run(conn, run_id, worker_id):
                return False
            return transition_chat_run_status(conn, run_id, "running", started=True)


def mark_chat_run_finished(
    run_id: str,
    status: str,
    *,
    error: Optional[str] = None,
    assistant_message_id: Optional[int] = None,
    worker_id: Optional[str] = None,
) -> bool:
    from app.services.chat_run_state import transition_chat_run_status

    with get_db() as conn:
        with _write_transaction(conn):
            if not _owns_active_run(conn, run_id, worker_id, allow_terminal=True):
                return False
            transitioned = transition_chat_run_status(
                conn, run_id, status, error=error,
                assistant_message_id=assistant_message_id,
            )
            if transitioned and assistant_message_id is not None:
                conn.execute(
                    "UPDATE chat_runs SET assistant_message_id = COALESCE(assistant_message_id, ?) WHERE run_id = ?",
                    (assistant_message_id, run_id),
                )
            return transitioned


def finish_chat_run_with_event(
    run_id: str,
    status: str,
    payload: Dict[str, Any],
    *,
    error: Optional[str] = None,
    assistant_message_id: Optional[int] = None,
    worker_id: Optional[str] = None,
) -> Optional[int]:
    """Commit the terminal transition, event, and winning claim marker together.

    The marker is added only for an acquired owner, never for reaper recovery,
    and authorizes that winner's later assistant save without a live lease.
    """
    from app.services.chat_run_events import check_chat_run_event
    from app.services.chat_run_state import TERMINAL_STATUSES, transition_chat_run_status

    if status not in TERMINAL_STATUSES or payload.get("type") not in {"final", "error"}:
        raise ValueError("a terminal status and final/error event are required")
    payload = dict(payload)
    payload.pop("terminal_claim_id", None)
    if worker_id is not None:
        payload["terminal_claim_id"] = worker_id
    check_chat_run_event(payload, run_id=run_id)
    with get_db() as conn:
        with _write_transaction(conn):
            if not _owns_active_run(conn, run_id, worker_id):
                return None
            row = conn.execute("SELECT status FROM chat_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None or row["status"] in TERMINAL_STATUSES:
                return None
            if not transition_chat_run_status(conn, run_id, status, error=error, assistant_message_id=assistant_message_id):
                return None
            return _append_event(conn, run_id, payload)


def get_chat_run(run_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT run_id, session_id, owner_id, status, user_message_id, assistant_message_id,
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


def get_chat_run_by_idempotency_key(
    session_id: str, idempotency_key: str
) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT run_id, session_id, owner_id, status, user_message_id, assistant_message_id,
                   idempotency_key, error, request_json, created_at, started_at,
                   finished_at, last_event_seq
            FROM chat_runs
            WHERE session_id = ? AND idempotency_key = ?
            """,
            (session_id, idempotency_key),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_session_runs(
    session_id: str,
    *,
    owner_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 50))
    clauses = ["session_id = ?"]
    params: List[Any] = [session_id]
    if owner_id:
        clauses.append("owner_id = ?")
        params.append(owner_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where_sql = " AND ".join(clauses)
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT run_id, session_id, owner_id, status, created_at, started_at, finished_at,
                   last_event_seq, error
            FROM chat_runs
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [dict(r) for r in rows]


def _append_event(conn, run_id: str, payload: Dict[str, Any]) -> int:
    """Append inside the caller's write transaction."""
    conn.execute(
        """INSERT INTO chat_run_events (run_id, seq, event_type, payload_json)
           VALUES (?, (SELECT COALESCE(MAX(seq), -1) + 1 FROM chat_run_events WHERE run_id = ?), ?, ?)""",
        (run_id, run_id, str(payload.get("type") or "unknown"), json.dumps(payload, ensure_ascii=False)),
    )
    row = conn.execute("SELECT MAX(seq) AS s FROM chat_run_events WHERE run_id = ?", (run_id,)).fetchone()
    seq = int(row["s"])
    conn.execute("UPDATE chat_runs SET last_event_seq = ? WHERE run_id = ?", (seq, run_id))
    return seq


def append_chat_run_event(
    run_id: str,
    payload: Dict[str, Any],
    *,
    worker_id: Optional[str] = None,
) -> Optional[int]:
    """Append one event; returns monotonic seq for this run (>= 0)."""
    from app.services.chat_run_events import check_chat_run_event

    check_chat_run_event(payload, run_id=run_id)
    with get_db() as conn:
        with _write_transaction(conn):
            if not _owns_active_run(conn, run_id, worker_id):
                return None
            return _append_event(conn, run_id, payload)


def batch_append_chat_run_events(
    run_id: str,
    payloads: List[Dict[str, Any]],
    *,
    worker_id: Optional[str] = None,
) -> List[int]:
    """Append multiple events in a single transaction; returns list of seq values.

    This is significantly faster than calling ``append_chat_run_event`` in a loop
    because it amortises the transaction overhead (commit + WAL sync) across all
    events in the batch.
    """
    if not payloads:
        return []

    from app.services.chat_run_events import check_chat_run_event

    for payload in payloads:
        check_chat_run_event(payload, run_id=run_id)

    seqs: List[int] = []
    with get_db() as conn:
        with _write_transaction(conn):
            if not _owns_active_run(conn, run_id, worker_id):
                return []
            row = conn.execute("SELECT COALESCE(MAX(seq), -1) AS s FROM chat_run_events WHERE run_id = ?", (run_id,)).fetchone()
            next_seq = int(row["s"]) + 1
            for payload in payloads:
                conn.execute(
                    "INSERT INTO chat_run_events (run_id, seq, event_type, payload_json) VALUES (?, ?, ?, ?)",
                    (run_id, next_seq, str(payload.get("type") or "unknown"), json.dumps(payload, ensure_ascii=False)),
                )
                seqs.append(next_seq)
                next_seq += 1
            conn.execute("UPDATE chat_runs SET last_event_seq = ? WHERE run_id = ?", (seqs[-1], run_id))
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


_STALE_RUN_ERROR = "server restarted; run interrupted"

DEFAULT_LEASE_TTL_SECONDS = 30


def insert_chat_run_signal(run_id: str, kind: str, payload: Optional[Dict[str, Any]] = None) -> int:
    """Persist a runtime control signal (durable fallback for cross-worker delivery)."""
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO chat_run_signals (run_id, kind, payload_json) VALUES (?, ?, ?)",
            (run_id, kind, payload_json),
        )
        conn.commit()
        return int(cursor.lastrowid)


def fetch_unconsumed_chat_run_signals(run_id: str) -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, kind, payload_json, created_at
            FROM chat_run_signals
            WHERE run_id = ? AND consumed_at IS NULL
            ORDER BY id ASC
            """,
            (run_id,),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                payload = {}
        except json.JSONDecodeError:
            payload = {}
        out.append(
            {
                "id": int(row["id"]),
                "kind": str(row["kind"]),
                "payload": payload,
                "created_at": row["created_at"],
            }
        )
    return out


def is_chat_run_owned(run_id: str, worker_id: str) -> bool:
    with get_db() as conn:
        return _owns_active_run(conn, run_id, worker_id)


def mark_chat_run_signals_consumed(signal_ids: List[int], *, run_id: Optional[str] = None, worker_id: Optional[str] = None) -> None:
    if not signal_ids:
        return
    placeholders = ",".join("?" for _ in signal_ids)
    with get_db() as conn:
        with _write_transaction(conn):
            if worker_id is not None and not _owns_active_run(conn, str(run_id or ""), worker_id):
                return
            conn.execute(
                f"UPDATE chat_run_signals SET consumed_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                tuple(signal_ids),
            )


def claim_chat_run_lease(run_id: str, worker_id: str, *, ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS) -> bool:
    """Acquire an unowned/expired active run; a live claim is never replaced."""
    ttl = max(5, int(ttl_seconds))
    with get_db() as conn:
        cursor = conn.execute(
            f"""
            UPDATE chat_runs
            SET worker_id = ?,
                heartbeat_at = CURRENT_TIMESTAMP,
                lease_expires_at = datetime('now', '+{ttl} seconds')
            WHERE run_id = ? AND status IN ('queued', 'running')
              AND (lease_expires_at IS NULL OR lease_expires_at <= datetime('now'))
            """,
            (worker_id, run_id),
        )
        conn.commit()
        return cursor.rowcount == 1


def heartbeat_chat_run_lease(run_id: str, worker_id: str, *, ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS) -> bool:
    """Renew the lease; returns False when the run is no longer claimed by us."""
    ttl = max(5, int(ttl_seconds))
    with get_db() as conn:
        cursor = conn.execute(
            f"""
            UPDATE chat_runs
            SET heartbeat_at = CURRENT_TIMESTAMP,
                lease_expires_at = datetime('now', '+{ttl} seconds')
            WHERE run_id = ? AND worker_id = ? AND status IN ('queued', 'running')
              AND lease_expires_at > datetime('now')
            """,
            (run_id, worker_id),
        )
        conn.commit()
        return cursor.rowcount == 1


def release_chat_run_lease(run_id: str, worker_id: str) -> None:
    """Drop the lease validity (worker_id stays for forensics)."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE chat_runs
            SET lease_expires_at = NULL
            WHERE run_id = ? AND worker_id = ?
            """,
            (run_id, worker_id),
        )
        conn.commit()


def is_chat_run_lease_live(run_id: str) -> bool:
    """True when some worker holds an unexpired lease on this run."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT 1 AS live
            FROM chat_runs
            WHERE run_id = ?
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at > datetime('now')
            """,
            (run_id,),
        ).fetchone()
    return row is not None


def reap_expired_chat_runs(*, ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS) -> int:
    """Fail runs whose worker lease expired (multi-instance-safe stale sweep).

    A row is reaped when its lease expired, or when it never got a lease and
    is older than the TTL grace window (crash between INSERT and claim, or
    pre-lease legacy rows). Fresh NULL-lease rows are left alone. Terminal
    runs' leftover signals are marked consumed as housekeeping.
    """
    from app.services.chat_run_state import transition_chat_run_status

    ttl = max(5, int(ttl_seconds))
    n = 0
    with get_db() as conn:
        # Acquire the write lock before selecting: a heartbeat/completion cannot
        # invalidate the candidate between selection and its terminal event.
        with _write_transaction(conn):
            rows = conn.execute(
                f"""
                SELECT run_id FROM chat_runs
                WHERE status IN ('queued', 'running')
                  AND (
                        (lease_expires_at IS NOT NULL AND lease_expires_at <= datetime('now'))
                     OR (lease_expires_at IS NULL AND created_at < datetime('now', '-{ttl} seconds'))
                  )
                """
            ).fetchall()
            for row in rows:
                rid = str(row["run_id"])
                if transition_chat_run_status(conn, rid, "failed", error=_STALE_RUN_ERROR):
                    _append_event(conn, rid, {
                        "type": "error",
                        "message": "Server restarted; this run was interrupted.",
                        "run_interrupted": True,
                    })
                    n += 1
            conn.execute(
                """
                UPDATE chat_run_signals SET consumed_at = CURRENT_TIMESTAMP
                WHERE consumed_at IS NULL
                  AND run_id IN (SELECT run_id FROM chat_runs WHERE status IN ('succeeded', 'failed', 'cancelled'))
                """
            )
    return n


def fix_stale_chat_runs_on_startup() -> int:
    """Startup wrapper around the lease-aware reaper (kept for main.py compat)."""
    return reap_expired_chat_runs()
