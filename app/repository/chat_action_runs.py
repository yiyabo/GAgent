from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..database import get_db


def create_action_run(
    *,
    run_id: str,
    session_id: Optional[str],
    owner_id: Optional[str] = None,
    user_message: str,
    mode: Optional[str],
    plan_id: Optional[int],
    context: Optional[Dict[str, Any]],
    history: Optional[list[Dict[str, Any]]],
    structured_json: str,
) -> None:
    """Insert a new chat action run record."""
    resolved_owner_id = str(owner_id or "").strip()
    if not resolved_owner_id:
        from app.routers.chat.session_helpers import lookup_session_owner

        try:
            resolved_owner_id = lookup_session_owner(session_id) or "legacy-local"
        except Exception:
            resolved_owner_id = "legacy-local"
    context_json = json.dumps(context or {}, ensure_ascii=False)
    history_json = json.dumps(history or [], ensure_ascii=False)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO chat_action_runs (
                id, session_id, owner_id, user_message, mode, plan_id,
                context_json, history_json, structured_json, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                run_id,
                session_id,
                resolved_owner_id,
                user_message,
                mode,
                plan_id,
                context_json,
                history_json,
                structured_json,
            ),
        )
        conn.commit()


def update_action_run(
    run_id: str,
    *,
    status: Optional[str] = None,
    plan_id: Optional[int] = None,
    result: Optional[Dict[str, Any]] = None,
    errors: Optional[list[str]] = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    """Update an action run with new status/result information."""
    sets = []
    params: list[Any] = []
    if status is not None:
        sets.append("status=?")
        params.append(status)
        if status == "running" and not started:
            started = True
        if status in {"completed", "failed"} and not finished:
            finished = True
    if plan_id is not None:
        sets.append("plan_id=?")
        params.append(plan_id)
    if result is not None:
        sets.append("result_json=?")
        params.append(json.dumps(result, ensure_ascii=False))
    if errors is not None:
        sets.append("errors_json=?")
        params.append(json.dumps(errors, ensure_ascii=False))
    if started:
        sets.append("started_at=CURRENT_TIMESTAMP")
    if finished:
        sets.append("finished_at=CURRENT_TIMESTAMP")
    if not sets:
        return

    sets.append("updated_at=CURRENT_TIMESTAMP")

    with get_db() as conn:
        params.append(run_id)
        conn.execute(
            f"UPDATE chat_action_runs SET {', '.join(sets)} WHERE id=?",
            params,
        )
        conn.commit()


def fetch_action_run(
    run_id: str,
    *,
    owner_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return stored action run metadata."""
    params: list[Any] = [run_id]
    where_sql = "WHERE id=?"
    if owner_id:
        where_sql += " AND owner_id=?"
        params.append(owner_id)
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                id, session_id, owner_id, user_message, mode, plan_id,
                context_json, history_json, structured_json,
                status, result_json, errors_json,
                created_at, started_at, finished_at
            FROM chat_action_runs
            """
            + where_sql,
            tuple(params),
        ).fetchone()

    if not row:
        return None

    context = json.loads(row["context_json"]) if row["context_json"] else {}
    history = json.loads(row["history_json"]) if row["history_json"] else []
    result = json.loads(row["result_json"]) if row["result_json"] else None
    errors = json.loads(row["errors_json"]) if row["errors_json"] else None

    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "owner_id": row["owner_id"],
        "user_message": row["user_message"],
        "mode": row["mode"],
        "plan_id": row["plan_id"],
        "context": context,
        "history": history,
        "structured_json": row["structured_json"],
        "status": row["status"],
        "result": result,
        "errors": errors,
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }
