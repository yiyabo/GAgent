"""SQLite persistence for asynchronous conversation quality evaluations."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.database import get_db

PENDING_STATUSES = ("pending", "retry")
TERMINAL_STATUSES = ("provisional", "final", "failed")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _from_json(raw: Any, fallback: Any) -> Any:
    if not isinstance(raw, str) or not raw:
        return fallback
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return fallback
    return value


def _row_to_evaluation(row: Any) -> Dict[str, Any]:
    item = dict(row)
    item["snapshot"] = _from_json(item.pop("snapshot_json", None), {})
    item["evaluation"] = _from_json(item.pop("evaluation_json", None), None)
    return item


def create_pending_evaluation(
    *,
    target_run_id: str,
    session_id: str,
    owner_id: str,
    snapshot: Dict[str, Any],
    observed_until: datetime,
    feedback_message_id: Optional[int] = None,
    feedback_received_at: Optional[datetime] = None,
) -> bool:
    """Create a pending evaluation once; returns whether a row was inserted."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO conversation_quality_evaluations (
                target_run_id, session_id, owner_id, status, observed_until, snapshot_json,
                feedback_message_id, feedback_received_at
            )
            VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
            ON CONFLICT(target_run_id) DO NOTHING
            """,
            (
                target_run_id,
                session_id,
                owner_id,
                observed_until.astimezone(timezone.utc).isoformat(),
                _as_json(snapshot),
                feedback_message_id,
                (
                    feedback_received_at.astimezone(timezone.utc).isoformat()
                    if feedback_received_at is not None
                    else None
                ),
            ),
        )
        conn.commit()
    return cursor.rowcount == 1


def get_evaluation(
    evaluation_id: int,
    *,
    owner_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    clauses = ["id = ?"]
    params: List[Any] = [evaluation_id]
    if owner_id is not None:
        clauses.append("owner_id = ?")
        params.append(owner_id)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM conversation_quality_evaluations WHERE " + " AND ".join(clauses),
            tuple(params),
        ).fetchone()
    return _row_to_evaluation(row) if row else None


def get_evaluation_by_run(target_run_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM conversation_quality_evaluations WHERE target_run_id = ?",
            (target_run_id,),
        ).fetchone()
    return _row_to_evaluation(row) if row else None


def attach_feedback_to_latest_pending(
    *,
    session_id: str,
    owner_id: str,
    feedback_message_id: int,
    feedback_received_at: Optional[datetime] = None,
) -> Optional[int]:
    """Attach one new user message to the newest unobserved reply in its session."""
    received_at = (feedback_received_at or _utc_now()).astimezone(timezone.utc).isoformat()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM conversation_quality_evaluations
            WHERE session_id = ?
              AND owner_id = ?
              AND status IN ('pending', 'retry', 'provisional')
              AND feedback_message_id IS NULL
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (session_id, owner_id),
        ).fetchone()
        if not row:
            return None
        evaluation_id = int(row["id"])
        cursor = conn.execute(
            """
            UPDATE conversation_quality_evaluations
            SET status = 'pending',
                attempt_count = 0,
                last_error = NULL,
                feedback_message_id = ?,
                feedback_received_at = ?,
                observed_until = ?,
                satisfaction_level = NULL,
                confidence = NULL,
                label_source = NULL,
                evaluation_json = NULL,
                finalized_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND feedback_message_id IS NULL
              AND status IN ('pending', 'retry', 'provisional')
            """,
            (feedback_message_id, received_at, received_at, evaluation_id),
        )
        conn.commit()
    return evaluation_id if cursor.rowcount == 1 else None


def claim_evaluation(
    evaluation_id: int,
    *,
    evaluation_basis: str,
    max_attempts: int,
) -> Optional[Dict[str, Any]]:
    """Atomically claim a pending/retry evaluation for one worker."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE conversation_quality_evaluations
            SET status = 'evaluating',
                evaluation_basis = CASE
                    WHEN feedback_message_id IS NOT NULL THEN 'follow_up_message'
                    ELSE ?
                END,
                attempt_count = attempt_count + 1,
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status IN ('pending', 'retry')
              AND attempt_count < ?
            """,
            (evaluation_basis, evaluation_id, max(1, max_attempts)),
        )
        if cursor.rowcount != 1:
            conn.commit()
            return None
        row = conn.execute(
            "SELECT * FROM conversation_quality_evaluations WHERE id = ?",
            (evaluation_id,),
        ).fetchone()
        conn.commit()
    return _row_to_evaluation(row) if row else None


def list_due_evaluation_ids(
    *,
    now: Optional[datetime] = None,
    limit: int = 20,
) -> List[int]:
    cutoff = (now or _utc_now()).astimezone(timezone.utc).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM conversation_quality_evaluations
            WHERE status IN ('pending', 'retry')
              AND observed_until <= ?
            ORDER BY observed_until ASC, id ASC
            LIMIT ?
            """,
            (cutoff, max(1, min(limit, 100))),
        ).fetchall()
    return [int(row["id"]) for row in rows]


def get_next_user_message(
    *,
    session_id: str,
    after_message_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    if after_message_id is None:
        return None
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, role, content, created_at
            FROM chat_messages
            WHERE session_id = ?
              AND role = 'user'
              AND id > ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (session_id, after_message_id),
        ).fetchone()
    return dict(row) if row else None


def get_feedback_message(evaluation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    feedback_message_id = evaluation.get("feedback_message_id")
    if feedback_message_id is None:
        return None
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, role, content, created_at
            FROM chat_messages
            WHERE id = ? AND session_id = ?
            """,
            (feedback_message_id, evaluation["session_id"]),
        ).fetchone()
    return dict(row) if row else None


def complete_evaluation(
    evaluation_id: int,
    *,
    status: str,
    result: Dict[str, Any],
    evaluator_provider: str,
    evaluator_model: str,
    prompt_version: str,
) -> None:
    if status not in {"provisional", "final"}:
        raise ValueError(f"Unsupported quality evaluation status: {status}")
    level = str(result.get("satisfaction_level") or "").strip()
    confidence = float(result.get("confidence") or 0.0)
    with get_db() as conn:
        conn.execute(
            """
            UPDATE conversation_quality_evaluations
            SET status = ?,
                satisfaction_level = ?,
                confidence = ?,
                label_source = 'llm',
                evaluation_json = ?,
                evaluator_provider = ?,
                evaluator_model = ?,
                prompt_version = ?,
                evaluated_at = CURRENT_TIMESTAMP,
                finalized_at = CASE WHEN ? = 'final' THEN CURRENT_TIMESTAMP ELSE NULL END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'evaluating'
            """,
            (
                status,
                level,
                max(0.0, min(1.0, confidence)),
                _as_json(result),
                evaluator_provider,
                evaluator_model,
                prompt_version,
                status,
                evaluation_id,
            ),
        )
        conn.commit()


def record_evaluation_failure(
    evaluation_id: int,
    *,
    error: str,
    max_attempts: int,
    retry_at: Optional[datetime] = None,
) -> None:
    safe_error = str(error or "evaluation failed")[:1000]
    with get_db() as conn:
        conn.execute(
            """
            UPDATE conversation_quality_evaluations
            SET status = CASE WHEN attempt_count >= ? THEN 'failed' ELSE 'retry' END,
                last_error = ?,
                observed_until = CASE
                    WHEN attempt_count >= ? THEN observed_until
                    ELSE ?
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'evaluating'
            """,
            (
                max(1, max_attempts),
                safe_error,
                max(1, max_attempts),
                (retry_at or _utc_now()).astimezone(timezone.utc).isoformat(),
                evaluation_id,
            ),
        )
        conn.commit()


def recover_stale_evaluations() -> int:
    """Return interrupted model calls to retry state after a process restart."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE conversation_quality_evaluations
            SET status = 'retry',
                last_error = COALESCE(last_error, 'server restarted during evaluation'),
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'evaluating'
            """
        )
        conn.commit()
    return cursor.rowcount


def list_evaluations(
    *,
    owner_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    satisfaction_level: Optional[str] = None,
    failure_mode: Optional[str] = None,
    since: Optional[str] = None,
) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if owner_id is not None:
        clauses.append("owner_id = ?")
        params.append(owner_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if satisfaction_level:
        clauses.append("satisfaction_level = ?")
        params.append(satisfaction_level)
    if failure_mode:
        clauses.append("evaluation_json LIKE ?")
        params.append(f'%"{failure_mode}"%')
    if since:
        clauses.append("created_at >= ?")
        params.append(since)
    params.extend([max(1, min(limit, 100)), max(0, offset)])
    with get_db() as conn:
        where = " AND ".join(clauses) if clauses else "1 = 1"
        rows = conn.execute(
            """
            SELECT *
            FROM conversation_quality_evaluations
            WHERE """ + where + """
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params),
        ).fetchall()
    return [_row_to_evaluation(row) for row in rows]


def get_quality_summary(
    *,
    owner_id: Optional[str] = None,
    since: Optional[str] = None,
) -> Dict[str, Any]:
    clauses: List[str] = []
    params: List[Any] = []
    if owner_id is not None:
        clauses.append("owner_id = ?")
        params.append(owner_id)
    if since:
        clauses.append("created_at >= ?")
        params.append(since)
    where = " AND ".join(clauses) if clauses else "1 = 1"
    with get_db() as conn:
        totals = conn.execute(
            f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status IN ('pending', 'retry', 'evaluating') THEN 1 ELSE 0 END) AS pending,
                   SUM(CASE WHEN status IN ('provisional', 'final') THEN 1 ELSE 0 END) AS evaluated,
                   AVG(CASE WHEN confidence IS NOT NULL THEN confidence END) AS average_confidence
            FROM conversation_quality_evaluations
            WHERE {where}
            """,
            tuple(params),
        ).fetchone()
        levels = conn.execute(
            f"""
            SELECT satisfaction_level, COUNT(*) AS count
            FROM conversation_quality_evaluations
            WHERE {where} AND satisfaction_level IS NOT NULL
            GROUP BY satisfaction_level
            ORDER BY count DESC, satisfaction_level ASC
            """,
            tuple(params),
        ).fetchall()
        samples = conn.execute(
            f"""
            SELECT evaluation_json, snapshot_json
            FROM conversation_quality_evaluations
            WHERE {where} AND evaluation_json IS NOT NULL
            """,
            tuple(params),
        ).fetchall()

    failure_modes: Dict[str, int] = {}
    responsible_stages: Dict[str, int] = {}
    request_tiers: Dict[str, int] = {}
    tools: Dict[str, int] = {}
    for row in samples:
        result = _from_json(row["evaluation_json"], {})
        snapshot = _from_json(row["snapshot_json"], {})
        for value in result.get("failure_modes", []) if isinstance(result, dict) else []:
            if isinstance(value, str):
                failure_modes[value] = failure_modes.get(value, 0) + 1
        for value in result.get("responsible_stages", []) if isinstance(result, dict) else []:
            if isinstance(value, str):
                responsible_stages[value] = responsible_stages.get(value, 0) + 1
        routing = snapshot.get("routing", {}) if isinstance(snapshot, dict) else {}
        tier = routing.get("request_tier") if isinstance(routing, dict) else None
        if isinstance(tier, str) and tier:
            request_tiers[tier] = request_tiers.get(tier, 0) + 1
        for tool in snapshot.get("tools_used", []) if isinstance(snapshot, dict) else []:
            if isinstance(tool, str):
                tools[tool] = tools.get(tool, 0) + 1

    def _ordered(values: Dict[str, int]) -> List[Dict[str, Any]]:
        return [
            {"name": name, "count": count}
            for name, count in sorted(values.items(), key=lambda item: (-item[1], item[0]))
        ]

    return {
        "total": int(totals["total"] or 0) if totals else 0,
        "pending": int(totals["pending"] or 0) if totals else 0,
        "evaluated": int(totals["evaluated"] or 0) if totals else 0,
        "average_confidence": float(totals["average_confidence"] or 0.0) if totals else 0.0,
        "by_satisfaction_level": [
            {"name": str(row["satisfaction_level"]), "count": int(row["count"])}
            for row in levels
        ],
        "failure_modes": _ordered(failure_modes),
        "responsible_stages": _ordered(responsible_stages),
        "request_tiers": _ordered(request_tiers),
        "tools": _ordered(tools),
    }
