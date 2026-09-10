from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..database import get_db

logger = logging.getLogger(__name__)


def init_llm_usage_table() -> None:
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_llm_usage_created_at
            ON llm_usage_log(created_at)
        """)
        # Migration: add session-scoped/cost columns if they don't exist
        _migrate_add_session_columns(conn)
        _migrate_add_cost_columns(conn)
        _migrate_add_attribution_columns(conn)
        conn.commit()


def _migrate_add_columns(conn: Any, columns: List[tuple[str, str]]) -> set[str]:
    cursor = conn.execute("PRAGMA table_info(llm_usage_log)")
    existing_columns = {row["name"] for row in cursor.fetchall()}
    for col_name, col_type in columns:
        if col_name not in existing_columns:
            try:
                conn.execute(
                    f"ALTER TABLE llm_usage_log ADD COLUMN {col_name} {col_type}"
                )
                logger.info("Migrated llm_usage_log: added column %s", col_name)
            except Exception as exc:
                logger.warning("Failed to add column %s: %s", col_name, exc)
    return existing_columns


def _migrate_add_session_columns(conn: Any) -> None:
    """Add session_id, plan_id, task_id, call_purpose columns if missing."""
    existing_columns = _migrate_add_columns(conn, [
        ("session_id", "TEXT"),
        ("plan_id", "INTEGER"),
        ("task_id", "INTEGER"),
        ("call_purpose", "TEXT"),
    ])

    if "session_id" not in existing_columns or "idx_llm_usage_session_id" not in existing_columns:
        try:
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_llm_usage_session_id
                ON llm_usage_log(session_id)
            """)
        except Exception:
            pass


def _migrate_add_cost_columns(conn: Any) -> None:
    """Add estimated cost columns if missing."""
    _migrate_add_columns(conn, [
        ("input_cost", "REAL"),
        ("output_cost", "REAL"),
        ("estimated_cost", "REAL"),
        ("cost_currency", "TEXT"),
    ])


def _migrate_add_attribution_columns(conn: Any) -> None:
    """Add run/phase/tool attribution and status columns if missing."""
    _migrate_add_columns(conn, [
        ("run_id", "TEXT"),
        ("phase", "TEXT"),
        ("tool_name", "TEXT"),
        ("call_status", "TEXT"),
        ("duration_ms", "REAL"),
    ])
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(llm_usage_log)").fetchall()}
    if "run_id" in existing_columns:
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_run_id ON llm_usage_log(run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_phase ON llm_usage_log(phase)")
        except Exception:
            pass



def _cost_env_key(provider: str, model: str, kind: str) -> str:
    token = f"{provider}_{model}_{kind}".upper()
    safe = "".join(ch if ch.isalnum() else "_" for ch in token)
    return f"LLM_COST_{safe}_PER_1K_CNY"


_DEFAULT_COST_CNY_PER_1K: Dict[tuple[str, str], tuple[float, float]] = {
    ("qwen", "qwen3.7-max"): (0.006, 0.018),
    ("qwen_code_cli", "qwen3.7-max"): (0.006, 0.018),
    ("qwen", "qwen-max"): (0.006, 0.018),
    ("qwen_code_cli", "qwen-max"): (0.006, 0.018),
}


def estimate_llm_cost(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> Dict[str, Any]:
    """Return estimated CNY cost from configurable per-1K token rates."""
    provider_key = str(provider or "").strip().lower()
    model_key = str(model or "").strip().lower()
    default_input, default_output = _DEFAULT_COST_CNY_PER_1K.get(
        (provider_key, model_key),
        (0.0, 0.0),
    )

    def _rate(kind: str, default: float) -> float:
        import os
        raw = os.getenv(_cost_env_key(provider_key, model_key, kind))
        if raw is None:
            raw = os.getenv(f"LLM_COST_{kind.upper()}_PER_1K_CNY")
        if raw is None:
            return default
        try:
            return max(0.0, float(str(raw).strip()))
        except (TypeError, ValueError):
            return default

    input_rate = _rate("input", default_input)
    output_rate = _rate("output", default_output)
    input_cost = max(0, int(prompt_tokens or 0)) * input_rate / 1000.0
    output_cost = max(0, int(completion_tokens or 0)) * output_rate / 1000.0
    return {
        "input_cost": input_cost,
        "output_cost": output_cost,
        "estimated_cost": input_cost + output_cost,
        "cost_currency": "CNY",
    }

def log_llm_usage(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    session_id: Optional[str] = None,
    plan_id: Optional[int] = None,
    task_id: Optional[int] = None,
    call_purpose: Optional[str] = None,
    run_id: Optional[str] = None,
    phase: Optional[str] = None,
    tool_name: Optional[str] = None,
    call_status: Optional[str] = None,
    duration_ms: Optional[float] = None,
    input_cost: Optional[float] = None,
    output_cost: Optional[float] = None,
    estimated_cost: Optional[float] = None,
    cost_currency: Optional[str] = None,
) -> None:
    if estimated_cost is None:
        estimated = estimate_llm_cost(
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        input_cost = estimated["input_cost"] if input_cost is None else input_cost
        output_cost = estimated["output_cost"] if output_cost is None else output_cost
        estimated_cost = estimated["estimated_cost"]
        cost_currency = cost_currency or estimated["cost_currency"]

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO llm_usage_log (
                provider, model, prompt_tokens, completion_tokens, total_tokens,
                created_at, session_id, plan_id, task_id, call_purpose,
                run_id, phase, tool_name, call_status, duration_ms,
                input_cost, output_cost, estimated_cost, cost_currency
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                datetime.now().isoformat(),
                session_id,
                plan_id,
                task_id,
                call_purpose,
                run_id,
                phase or "uncategorized",
                tool_name,
                call_status or "ok",
                duration_ms,
                input_cost,
                output_cost,
                estimated_cost,
                cost_currency,
            ),
        )
        conn.commit()


def get_usage_summary(hours: int = 24) -> Dict[str, Any]:
    cutoff = datetime.now().isoformat()[:10] + "T00:00:00"
    if hours < 24:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as call_count,
                COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(estimated_cost), 0.0) as estimated_cost
            FROM llm_usage_log
            WHERE created_at >= ?
            """,
            (cutoff,),
        ).fetchone()

        by_model_rows = conn.execute(
            """
            SELECT
                model,
                COUNT(*) as call_count,
                COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(estimated_cost), 0.0) as estimated_cost
            FROM llm_usage_log
            WHERE created_at >= ?
            GROUP BY model
            ORDER BY total_tokens DESC
            """,
            (cutoff,),
        ).fetchall()

    by_model = []
    for r in by_model_rows:
        by_model.append({
            "model": r["model"],
            "call_count": r["call_count"],
            "prompt_tokens": r["prompt_tokens"],
            "completion_tokens": r["completion_tokens"],
            "total_tokens": r["total_tokens"],
            "estimated_cost": r["estimated_cost"],
        })

    return {
        "period_hours": hours,
        "call_count": row["call_count"] if row else 0,
        "total_prompt_tokens": row["total_prompt_tokens"] if row else 0,
        "total_completion_tokens": row["total_completion_tokens"] if row else 0,
        "total_tokens": row["total_tokens"] if row else 0,
        "estimated_cost": row["estimated_cost"] if row else 0.0,
        "by_model": by_model,
    }


def get_session_usage_summary(session_id: str) -> Dict[str, Any]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as call_count,
                COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(estimated_cost), 0.0) as estimated_cost
            FROM llm_usage_log
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

        by_model_rows = conn.execute(
            """
            SELECT
                model,
                COUNT(*) as call_count,
                COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(estimated_cost), 0.0) as estimated_cost
            FROM llm_usage_log
            WHERE session_id = ?
            GROUP BY model
            ORDER BY total_tokens DESC
            """,
            (session_id,),
        ).fetchall()

        by_purpose_rows = conn.execute(
            """
            SELECT
                COALESCE(call_purpose, 'unknown') as purpose,
                COUNT(*) as call_count,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(estimated_cost), 0.0) as estimated_cost
            FROM llm_usage_log
            WHERE session_id = ?
            GROUP BY call_purpose
            ORDER BY total_tokens DESC
            """,
            (session_id,),
        ).fetchall()

    by_model = [
        {
            "model": r["model"],
            "call_count": r["call_count"],
            "prompt_tokens": r["prompt_tokens"],
            "completion_tokens": r["completion_tokens"],
            "total_tokens": r["total_tokens"],
            "estimated_cost": r["estimated_cost"],
        }
        for r in by_model_rows
    ]

    by_purpose = [
        {
            "purpose": r["purpose"],
            "call_count": r["call_count"],
            "total_tokens": r["total_tokens"],
            "estimated_cost": r["estimated_cost"],
        }
        for r in by_purpose_rows
    ]

    return {
        "session_id": session_id,
        "call_count": row["call_count"] if row else 0,
        "total_prompt_tokens": row["total_prompt_tokens"] if row else 0,
        "total_completion_tokens": row["total_completion_tokens"] if row else 0,
        "total_tokens": row["total_tokens"] if row else 0,
        "estimated_cost": row["estimated_cost"] if row else 0.0,
        "by_model": by_model,
        "by_purpose": by_purpose,
    }


def get_plan_tasks_usage_summary(plan_id: int) -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                task_id,
                COUNT(*) as call_count,
                COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(estimated_cost), 0.0) as estimated_cost
            FROM llm_usage_log
            WHERE plan_id = ? AND task_id IS NOT NULL
            GROUP BY task_id
            ORDER BY task_id
            """,
            (plan_id,),
        ).fetchall()

    return [
        {
            "task_id": r["task_id"],
            "call_count": r["call_count"],
            "prompt_tokens": r["prompt_tokens"],
            "completion_tokens": r["completion_tokens"],
            "total_tokens": r["total_tokens"],
            "estimated_cost": r["estimated_cost"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Cost attribution queries (phase / run / session drill-down)
# ---------------------------------------------------------------------------

def _usage_cutoff_iso(hours: Optional[int] = None) -> Optional[str]:
    if not hours or hours <= 0:
        return None
    return (datetime.now() - timedelta(hours=hours)).isoformat()


def _usage_where_clause(
    *,
    hours: Optional[int] = None,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    phase: Optional[str] = None,
    purpose: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    cutoff = _usage_cutoff_iso(hours)
    if cutoff:
        clauses.append("created_at >= ?")
        params.append(cutoff)
    if start:
        clauses.append("created_at >= ?")
        params.append(start)
    if end:
        clauses.append("created_at <= ?")
        params.append(end)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if run_id:
        clauses.append("run_id = ?")
        params.append(run_id)
    if phase:
        clauses.append("phase = ?")
        params.append(phase)
    if purpose:
        clauses.append("call_purpose = ?")
        params.append(purpose)
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


def get_usage_overview(
    *,
    hours: Optional[int] = None,
    session_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate token/cost totals with breakdowns by day, phase, purpose, model."""
    where_sql, params = _usage_where_clause(
        hours=hours, session_id=session_id, start=start, end=end,
    )
    with get_db() as conn:
        totals_row = dict(conn.execute(
            f"""
            SELECT
                COUNT(*) as call_count,
                COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(estimated_cost), 0) as estimated_cost,
                COALESCE(SUM(CASE WHEN call_status = 'error' THEN 1 ELSE 0 END), 0) as error_calls
            FROM llm_usage_log
            {where_sql}
            """,
            params,
        ).fetchone())

        def _breakdown(group_col: str) -> List[Dict[str, Any]]:
            rows = conn.execute(
                f"""
                SELECT {group_col} as key,
                    COUNT(*) as call_count,
                    COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                    COALESCE(SUM(total_tokens), 0) as total_tokens,
                    COALESCE(SUM(estimated_cost), 0) as estimated_cost
                FROM llm_usage_log
                {where_sql}
                GROUP BY {group_col}
                ORDER BY total_tokens DESC
                """,
                params,
            ).fetchall()
            return [
                {
                    "key": r["key"] or "uncategorized",
                    "call_count": r["call_count"],
                    "prompt_tokens": r["prompt_tokens"],
                    "completion_tokens": r["completion_tokens"],
                    "total_tokens": r["total_tokens"],
                    "estimated_cost": round(float(r["estimated_cost"] or 0.0), 6),
                }
                for r in rows
            ]

        return {
            "filter": {
                "hours": hours,
                "session_id": session_id,
                "start": start,
                "end": end,
            },
            "totals": {
                "call_count": totals_row["call_count"],
                "prompt_tokens": totals_row["prompt_tokens"],
                "completion_tokens": totals_row["completion_tokens"],
                "total_tokens": totals_row["total_tokens"],
                "estimated_cost": round(float(totals_row["estimated_cost"] or 0.0), 6),
                "error_calls": totals_row["error_calls"],
            },
            "by_day": _breakdown("substr(created_at, 1, 10)"),
            "by_phase": _breakdown("COALESCE(phase, 'uncategorized')"),
            "by_purpose": _breakdown("COALESCE(call_purpose, 'uncategorized')"),
            "by_model": _breakdown("provider || '/' || model"),
            "by_tool": _breakdown("COALESCE(tool_name, 'none')"),
        }


def get_top_sessions(
    *,
    limit: int = 10,
    hours: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Rank sessions by token consumption to spot the expensive ones."""
    where_sql, params = _usage_where_clause(hours=hours)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                l.session_id as session_id,
                COUNT(*) as call_count,
                COALESCE(SUM(l.prompt_tokens), 0) as prompt_tokens,
                COALESCE(SUM(l.completion_tokens), 0) as completion_tokens,
                COALESCE(SUM(l.total_tokens), 0) as total_tokens,
                COALESCE(SUM(l.estimated_cost), 0) as estimated_cost,
                MAX(l.created_at) as last_call_at,
                MIN(l.created_at) as first_call_at
            FROM llm_usage_log l
            {where_sql + ' AND l.session_id IS NOT NULL' if where_sql else 'WHERE l.session_id IS NOT NULL'}
            GROUP BY l.session_id
            ORDER BY total_tokens DESC
            LIMIT ?
            """,
            params + [max(1, int(limit))],
        ).fetchall()
        results: List[Dict[str, Any]] = []
        for r in rows:
            entry = {
                "session_id": r["session_id"],
                "call_count": r["call_count"],
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
                "total_tokens": r["total_tokens"],
                "estimated_cost": round(float(r["estimated_cost"] or 0.0), 6),
                "first_call_at": r["first_call_at"],
                "last_call_at": r["last_call_at"],
                "session_name": None,
            }
            try:
                name_row = conn.execute(
                    "SELECT name FROM chat_sessions WHERE id = ?", (r["session_id"],)
                ).fetchone()
                if name_row and name_row["name"]:
                    entry["session_name"] = name_row["name"]
            except Exception:
                pass
            results.append(entry)
        return results


def get_session_run_breakdown(
    session_id: str,
    *,
    limit: int = 20,
    hours: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Per-run (per conversation turn) token totals for one session."""
    where_sql, params = _usage_where_clause(session_id=session_id, hours=hours)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                run_id,
                COUNT(*) as call_count,
                COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(estimated_cost), 0) as estimated_cost,
                MAX(created_at) as last_call_at,
                GROUP_CONCAT(DISTINCT COALESCE(call_purpose, 'uncategorized')) as purposes
            FROM llm_usage_log
            {where_sql + ' AND run_id IS NOT NULL' if where_sql else 'WHERE session_id = ? AND run_id IS NOT NULL'}
            GROUP BY run_id
            ORDER BY total_tokens DESC
            LIMIT ?
            """,
            params + [max(1, int(limit))],
        ).fetchall()
        return [
            {
                "run_id": r["run_id"],
                "call_count": r["call_count"],
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
                "total_tokens": r["total_tokens"],
                "estimated_cost": round(float(r["estimated_cost"] or 0.0), 6),
                "last_call_at": r["last_call_at"],
                "purposes": sorted({p for p in (r["purposes"] or "").split(",") if p}),
            }
            for r in rows
        ]


def get_usage_calls(
    *,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    phase: Optional[str] = None,
    purpose: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Raw per-call rows for drill-down ('why did this session cost so much')."""
    where_sql, params = _usage_where_clause(
        session_id=session_id, run_id=run_id, phase=phase, purpose=purpose,
    )
    if status:
        where_sql = (where_sql + " AND call_status = ?") if where_sql else "WHERE call_status = ?"
        params.append(status)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                created_at, provider, model, prompt_tokens, completion_tokens,
                total_tokens, estimated_cost, cost_currency, session_id, plan_id,
                task_id, run_id, phase, tool_name, call_purpose, call_status, duration_ms
            FROM llm_usage_log
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params + [min(1000, max(1, int(limit)))],
        ).fetchall()
        return [dict(r) for r in rows]


def get_run_usage_summary(run_id: str) -> Optional[Dict[str, Any]]:
    """Aggregate one run's calls — stamped into the chat message metadata."""
    if not run_id:
        return None
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as call_count,
                COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(estimated_cost), 0) as estimated_cost
            FROM llm_usage_log
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if not row or not row["call_count"]:
            return None
        by_purpose = conn.execute(
            """
            SELECT COALESCE(call_purpose, 'uncategorized') as key,
                COUNT(*) as call_count,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(estimated_cost), 0) as estimated_cost
            FROM llm_usage_log
            WHERE run_id = ?
            GROUP BY key
            ORDER BY total_tokens DESC
            """,
            (run_id,),
        ).fetchall()
        return {
            "run_id": run_id,
            "call_count": row["call_count"],
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "total_tokens": row["total_tokens"],
            "estimated_cost": round(float(row["estimated_cost"] or 0.0), 6),
            "by_purpose": [
                {
                    "purpose": r["key"],
                    "call_count": r["call_count"],
                    "total_tokens": r["total_tokens"],
                    "estimated_cost": round(float(r["estimated_cost"] or 0.0), 6),
                }
                for r in by_purpose
            ],
        }
