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


def get_run_by_assistant_message(session_id: str, message_id: int) -> dict[str, Any] | None:
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
    return [(row["seq"], str(row["created_at"] or ""), _decode_payload(row["payload_json"])) for row in rows]


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


# ---------------------------------------------------------------------------
# Plan task inspection (read-only; plan shard + main usage ledger)
# ---------------------------------------------------------------------------


# Newest non-delta log rows loaded per task; older rows are dropped in SQL so a
# runaway task cannot load an unbounded result set (steps are capped later).
MAX_PLAN_TASK_LOG_ROWS = 5000


def _missing_schema(exc: sqlite3.OperationalError) -> bool:
    text = str(exc).lower()
    return "no such table" in text or "no such column" in text


def _job_dict(row: Any) -> dict[str, Any]:
    job = dict(row)
    for key in ("stats_json", "metadata_json"):
        raw = job.pop(key, None)
        try:
            decoded = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            decoded = None
        job[key[: -len("_json")]] = decoded if isinstance(decoded, dict) else {}
    return job


def _find_plan_task_job(conn: sqlite3.Connection, task_id: int, job_id: str | None) -> dict[str, Any] | None:
    if job_id:
        # A pinned job must belong to this task (exact ids); otherwise None -> 404.
        row = conn.execute(
            """
            SELECT job_id, job_type, mode, target_task_id, session_id, status, error,
               stats_json, metadata_json, created_at, started_at, finished_at
            FROM decomposition_jobs
            WHERE job_id = ? AND job_type = 'plan_execute'
              AND (
                target_task_id = ?
                OR (json_type(metadata_json, '$.task_id') IN ('integer', 'text')
                    AND CAST(json_extract(metadata_json, '$.task_id') AS TEXT) = CAST(? AS TEXT))
                OR (json_type(stats_json, '$.task_id') IN ('integer', 'text')
                    AND CAST(json_extract(stats_json, '$.task_id') AS TEXT) = CAST(? AS TEXT))
                OR EXISTS (
                  SELECT 1 FROM decomposition_job_logs
                  WHERE decomposition_job_logs.job_id = decomposition_jobs.job_id
                    AND (json_type(decomposition_job_logs.metadata_json, '$.task_id') IN ('integer', 'text')
                        AND CAST(json_extract(decomposition_job_logs.metadata_json, '$.task_id') AS TEXT) = CAST(? AS TEXT))
                )
              )
            """,
            (job_id, task_id, task_id, task_id, task_id),
        ).fetchone()
        return _job_dict(row) if row else None
    # Latest plan_execute job that logged this task, else one that targeted it,
    # else one whose stats/metadata reference it.
    row = conn.execute(
        """
        SELECT job_id, job_type, mode, target_task_id, session_id, status, error,
               stats_json, metadata_json, created_at, started_at, finished_at
        FROM decomposition_jobs
        WHERE job_type = 'plan_execute'
          AND (
            job_id IN (
              SELECT job_id FROM decomposition_job_logs
              WHERE (json_type(metadata_json, '$.task_id') IN ('integer', 'text')
                  AND CAST(json_extract(metadata_json, '$.task_id') AS TEXT) = CAST(? AS TEXT))
            )
            OR target_task_id = ?
            OR (json_type(metadata_json, '$.task_id') IN ('integer', 'text')
                AND CAST(json_extract(metadata_json, '$.task_id') AS TEXT) = CAST(? AS TEXT))
            OR (json_type(stats_json, '$.task_id') IN ('integer', 'text')
                AND CAST(json_extract(stats_json, '$.task_id') AS TEXT) = CAST(? AS TEXT))
          )
        ORDER BY COALESCE(started_at, created_at) DESC, created_at DESC
        LIMIT 1
        """,
        (task_id, task_id, task_id, task_id),
    ).fetchone()
    return _job_dict(row) if row else None


def _decode_meta(raw: Any) -> dict[str, Any] | None:
    try:
        value = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def load_plan_task_inspection_inputs(plan_id: int, task_id: int, job_id: str | None = None) -> dict[str, Any] | None:
    """Gather the rows ``build_plan_task_inspection`` needs, or None if no task.

    Returns ``{"task", "job", "logs", "log_counts", "usage_rows"}``. Streaming
    ``thinking_delta`` rows are counted in SQL and never loaded.
    """
    from app.database import plan_db_connection
    from app.repository.plan_storage import get_plan_db_path
    from app.services.run_inspector import plan_task_run_id

    path = get_plan_db_path(plan_id)
    if not path.exists():
        return None
    with plan_db_connection(path) as conn:
        task_row = conn.execute(
            """
            SELECT id, name, status, instruction, execution_result, created_at, updated_at
            FROM tasks WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        if task_row is None:
            return None
        task = dict(task_row)
        try:
            job = _find_plan_task_job(conn, task_id, job_id)
        except sqlite3.OperationalError as exc:
            if not _missing_schema(exc):
                raise
            job = None
        logs: list[tuple[int, str, dict[str, Any]]] = []
        log_counts: dict[str, int] = {}
        if job is not None:
            params = (job["job_id"], task_id)
            rows = conn.execute(
                """
                SELECT id, timestamp, level, message, metadata_json
                FROM decomposition_job_logs
                WHERE job_id = ?
                  AND (json_type(metadata_json, '$.task_id') IN ('integer', 'text')
                      AND CAST(json_extract(metadata_json, '$.task_id') AS TEXT) = CAST(? AS TEXT))
                  AND COALESCE(json_extract(metadata_json, '$.sub_type'), '') != 'thinking_delta'
                ORDER BY id DESC
                LIMIT ?
                """,
                (*params, MAX_PLAN_TASK_LOG_ROWS),
            ).fetchall()
            logs = [
                (
                    int(row["id"]),
                    str(row["timestamp"] or ""),
                    {"level": row["level"], "message": row["message"], "metadata": _decode_meta(row["metadata_json"])},
                )
                for row in reversed(rows)
            ]
            count_rows = conn.execute(
                """
                SELECT COALESCE(json_extract(metadata_json, '$.sub_type'), level) AS kind, COUNT(*) AS n
                FROM decomposition_job_logs
                WHERE job_id = ? AND (json_type(metadata_json, '$.task_id') IN ('integer', 'text')
                    AND CAST(json_extract(metadata_json, '$.task_id') AS TEXT) = CAST(? AS TEXT))
                GROUP BY kind
                """,
                params,
            ).fetchall()
            log_counts = {str(row["kind"]): int(row["n"]) for row in count_rows}

    run_id = plan_task_run_id(plan_id, task_id)
    usage_rows: list[dict[str, Any]] = []
    try:
        with get_db() as conn:
            usage_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT phase, call_purpose, tool_name, call_status, model,
                           prompt_tokens, completion_tokens, total_tokens,
                           cache_read_tokens, duration_ms, estimated_cost, cost_currency
                    FROM llm_usage_log
                    WHERE run_id = ? OR run_id GLOB ?
                    """,
                    (run_id, f"{run_id}:*"),
                ).fetchall()
            ]
    except sqlite3.OperationalError as exc:
        if not _missing_schema(exc):
            raise
    return {"task": task, "job": job, "logs": logs, "log_counts": log_counts, "usage_rows": usage_rows}
