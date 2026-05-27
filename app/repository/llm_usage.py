from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..database import get_db


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
        conn.commit()


def log_llm_usage(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO llm_usage_log (
                provider, model, prompt_tokens, completion_tokens, total_tokens, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                datetime.now().isoformat(),
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
                COALESCE(SUM(total_tokens), 0) as total_tokens
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
                COALESCE(SUM(total_tokens), 0) as total_tokens
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
        })

    return {
        "period_hours": hours,
        "call_count": row["call_count"] if row else 0,
        "total_prompt_tokens": row["total_prompt_tokens"] if row else 0,
        "total_completion_tokens": row["total_completion_tokens"] if row else 0,
        "total_tokens": row["total_tokens"] if row else 0,
        "by_model": by_model,
    }
