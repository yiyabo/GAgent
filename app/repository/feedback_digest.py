"""Read-only queries behind the weekly dissatisfaction digest."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

_EXCERPT = 400


def _json(raw: Any, fallback: Any) -> Any:
    if not isinstance(raw, str) or not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _excerpt(text: Any) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= _EXCERPT else value[: _EXCERPT - 1] + "…"


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _message(conn: sqlite3.Connection, message_id: Optional[int]) -> str:
    if not message_id:
        return ""
    row = conn.execute("SELECT content FROM chat_messages WHERE id = ?", (message_id,)).fetchone()
    return row[0] if row else ""


def _turn(conn: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT r.session_id, r.owner_id, r.status, r.user_message_id, r.assistant_message_id,
               r.request_json, r.created_at, u.email
        FROM chat_runs r LEFT JOIN users u ON u.id = r.owner_id
        WHERE r.run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return {"run_id": run_id}
    question = _message(conn, row["user_message_id"]) or _json(row["request_json"], {}).get("message", "")
    return {
        "run_id": run_id,
        "session_id": row["session_id"],
        "owner_id": row["owner_id"],
        "owner_email": row["email"],
        "run_status": row["status"],
        "asked_at": row["created_at"],
        "question": _excerpt(question),
        "answer": _excerpt(_message(conn, row["assistant_message_id"])),
    }


def _tables(conn: sqlite3.Connection) -> set:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}


def collect(conn: sqlite3.Connection, *, since: str, until: str, limit: int = 50) -> Dict[str, Any]:
    """Thumbs ratings and auto-evaluated dissatisfaction between *since* and *until* (UTC text).

    *conn* must use ``sqlite3.Row`` rows.
    """
    tables = _tables(conn)
    ratings = {"useful": 0, "needs_work": 0}
    downvotes: List[Dict[str, Any]] = []
    if "skill_run_feedback" in tables:
        for row in conn.execute(
            "SELECT rating, COUNT(*) FROM skill_run_feedback WHERE updated_at >= ? AND updated_at <= ? GROUP BY rating",
            (since, until),
        ).fetchall():
            ratings[row[0]] = row[1]
        reasons_column = "reasons_json" if "reasons_json" in _columns(conn, "skill_run_feedback") else "'[]'"
        for row in conn.execute(
            f"""
            SELECT run_id, comment, updated_at, {reasons_column} AS reasons_json FROM skill_run_feedback
            WHERE rating = 'needs_work' AND updated_at >= ? AND updated_at <= ?
            ORDER BY updated_at DESC LIMIT ?
            """,
            (since, until, limit),
        ).fetchall():
            downvotes.append({
                **_turn(conn, row["run_id"]),
                "feedback_at": row["updated_at"],
                "reasons": _json(row["reasons_json"], []),
                "comment": row["comment"] or "",
            })

    levels: Dict[str, int] = {}
    auto_cases: List[Dict[str, Any]] = []
    if "conversation_quality_evaluations" in tables:
        stamp = "COALESCE(finalized_at, evaluated_at, updated_at)"
        for row in conn.execute(
            f"""
            SELECT satisfaction_level, COUNT(*) FROM conversation_quality_evaluations
            WHERE satisfaction_level IS NOT NULL AND {stamp} >= ? AND {stamp} <= ?
            GROUP BY satisfaction_level
            """,
            (since, until),
        ).fetchall():
            levels[row[0]] = row[1]
        downvoted = {case["run_id"] for case in downvotes}
        for row in conn.execute(
            f"""
            SELECT target_run_id, satisfaction_level, confidence, evaluation_json, {stamp} AS evaluated_at
            FROM conversation_quality_evaluations
            WHERE satisfaction_level IN ('negative', 'angry') AND {stamp} >= ? AND {stamp} <= ?
            ORDER BY {stamp} DESC LIMIT ?
            """,
            (since, until, limit),
        ).fetchall():
            if row["target_run_id"] in downvoted:
                continue
            evaluation = _json(row["evaluation_json"], {}) or {}
            evidence = evaluation.get("evidence") or []
            auto_cases.append({
                **_turn(conn, row["target_run_id"]),
                "evaluated_at": row["evaluated_at"],
                "satisfaction_level": row["satisfaction_level"],
                "confidence": row["confidence"],
                "failure_modes": evaluation.get("failure_modes") or [],
                "evidence": _excerpt(evidence[0].get("quote")) if evidence and isinstance(evidence[0], dict) else "",
            })
    return {
        "ratings": ratings,
        "downvotes": downvotes,
        "auto_levels": levels,
        "auto_cases": auto_cases,
    }
