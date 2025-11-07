"""Per-plan SQLite 文件管理."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config.database_config import get_database_config
from app.database import get_db, plan_db_connection

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "2"

SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "headers",
    "cookie",
    "token",
    "secret",
    "signature",
    "password",
}


def _json_dump(data: Any) -> Optional[str]:
    if data is None:
        return None
    try:
        return json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def _json_load(data: Optional[str]) -> Any:
    if not data:
        return None
    try:
        return json.loads(data)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def get_plan_db_path(plan_id: int) -> Path:
    """返回指定 plan 的数据库文件路径."""
    base_dir = get_database_config().get_plan_store_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"plan_{plan_id}.sqlite"


def get_system_job_db_path() -> Path:
    """返回未绑定 Plan 的系统级 Job 日志数据库."""
    path = get_database_config().get_system_jobs_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_job_db_path(plan_id: Optional[int]) -> Path:
    if plan_id is None:
        return get_system_job_db_path()
    return get_plan_db_path(plan_id)


def initialize_plan_database(
    plan_id: int,
    *,
    title: str,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Path:
    """创建并初始化 plan 专属数据库文件."""
    db_path = get_plan_db_path(plan_id)
    metadata = metadata or {}

    with plan_db_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                instruction TEXT,
                parent_id INTEGER,
                position INTEGER DEFAULT 0,
                path TEXT,
                depth INTEGER DEFAULT 0,
                metadata TEXT,
                execution_result TEXT,
                context_combined TEXT,
                context_sections TEXT,
                context_meta TEXT,
                context_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (parent_id) REFERENCES tasks (id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_dependencies (
                task_id INTEGER NOT NULL,
                depends_on INTEGER NOT NULL,
                PRIMARY KEY (task_id, depends_on),
                FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE,
                FOREIGN KEY (depends_on) REFERENCES tasks (id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot TEXT NOT NULL,
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_path ON tasks(path)")

        _upsert_meta(conn, "schema_version", SCHEMA_VERSION)
        _upsert_meta(conn, "title", title)
        _upsert_meta(conn, "description", description or "")
        _upsert_meta(conn, "metadata", json.dumps(metadata, ensure_ascii=False))

        _ensure_decomposition_tables(conn)
        _ensure_action_log_tables(conn, plan_id=plan_id)

    logger.info("Initialized plan database %s at %s", plan_id, db_path)
    return db_path


def update_plan_metadata(
    plan_id: int,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """更新 plan_meta 表中的基础信息."""
    db_path = get_plan_db_path(plan_id)
    with plan_db_connection(db_path) as conn:
        if title is not None:
            _upsert_meta(conn, "title", title)
        if description is not None:
            _upsert_meta(conn, "description", description)
        if metadata is not None:
            _upsert_meta(conn, "metadata", json.dumps(metadata, ensure_ascii=False))


def remove_plan_database(plan_id: int) -> None:
    """删除 plan 的数据库文件."""
    db_path = get_plan_db_path(plan_id)
    try:
        if db_path.exists():
            db_path.unlink()
            logger.info("Removed plan database at %s", db_path)
    except OSError as exc:  # pragma: no cover - best effort cleanup
        logger.warning("Failed to remove plan database %s: %s", db_path, exc)


def _upsert_meta(conn, key: str, value: Optional[str]) -> None:
    conn.execute(
        """
        INSERT INTO plan_meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value),
    )


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    info_rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {row["name"] for row in info_rows}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def register_decomposition_job_index(job_id: str, plan_id: Optional[int], *, job_type: str = "plan_decompose") -> None:
    if plan_id is None:
        return
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO plan_decomposition_job_index (job_id, plan_id, job_type, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(job_id) DO UPDATE SET
                plan_id=excluded.plan_id,
                job_type=excluded.job_type
            """,
            (job_id, plan_id, job_type),
        )


def lookup_decomposition_job_plan(job_id: str) -> Optional[int]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT plan_id FROM plan_decomposition_job_index WHERE job_id=?",
            (job_id,),
        ).fetchone()
    if not row:
        return None
    return row["plan_id"]


def lookup_decomposition_job_entry(job_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT plan_id, job_type FROM plan_decomposition_job_index WHERE job_id=?",
            (job_id,),
        ).fetchone()
    if not row:
        return None
    return {"plan_id": row["plan_id"], "job_type": row["job_type"]}


def remove_decomposition_job_index(job_id: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM plan_decomposition_job_index WHERE job_id=?", (job_id,))


def record_decomposition_job(
    plan_id: Optional[int],
    *,
    job_id: str,
    job_type: str,
    mode: str,
    target_task_id: Optional[int],
    status: str,
    params: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    db_path = _resolve_job_db_path(plan_id)
    params_json = _json_dump(params)
    metadata_json = _json_dump(metadata)
    with plan_db_connection(db_path) as conn:
        _ensure_decomposition_tables(conn)
        conn.execute(
            """
            INSERT INTO decomposition_jobs (
                job_id, job_type, mode, target_task_id, status, params_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                job_type=excluded.job_type,
                mode=excluded.mode,
                target_task_id=excluded.target_task_id,
                status=excluded.status,
                params_json=excluded.params_json,
                metadata_json=excluded.metadata_json
            """,
            (job_id, job_type, mode, target_task_id, status, params_json, metadata_json),
        )


def update_decomposition_job_status(
    plan_id: Optional[int],
    *,
    job_id: str,
    status: Optional[str] = None,
    error: Optional[str] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    stats: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
) -> None:
    db_path = _resolve_job_db_path(plan_id)
    sets: List[str] = []
    params: List[Any] = []
    if status is not None:
        sets.append("status=?")
        params.append(status)
    if error is not None:
        sets.append("error=?")
        params.append(error)
    if started_at is not None:
        sets.append("started_at=?")
        params.append(started_at.isoformat())
    if finished_at is not None:
        sets.append("finished_at=?")
        params.append(finished_at.isoformat())
    if stats is not None:
        sets.append("stats_json=?")
        params.append(_json_dump(stats))
    if result is not None:
        sets.append("result_json=?")
        params.append(_json_dump(result))
    if not sets:
        return
    params.append(job_id)
    with plan_db_connection(db_path) as conn:
        _ensure_decomposition_tables(conn)
        conn.execute(
            f"UPDATE decomposition_jobs SET {', '.join(sets)} WHERE job_id=?",
            params,
        )


def append_decomposition_job_log(
    plan_id: Optional[int],
    *,
    job_id: str,
    timestamp: datetime,
    level: str,
    message: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    db_path = _resolve_job_db_path(plan_id)
    with plan_db_connection(db_path) as conn:
        _ensure_decomposition_tables(conn)
        conn.execute(
            """
            INSERT INTO decomposition_job_logs (job_id, timestamp, level, message, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                job_id,
                timestamp.isoformat(),
                level,
                message,
                _json_dump(metadata) or None,
            ),
        )


def load_decomposition_job(plan_id: Optional[int], job_id: str) -> Optional[Dict[str, Any]]:
    db_path = _resolve_job_db_path(plan_id)
    with plan_db_connection(db_path) as conn:
        _ensure_decomposition_tables(conn)
        row = conn.execute(
            """
            SELECT job_id, job_type, mode, target_task_id, status, error, params_json,
                   stats_json, result_json, metadata_json, created_at, started_at, finished_at
            FROM decomposition_jobs
            WHERE job_id=?
            """,
            (job_id,),
        ).fetchone()
        if not row:
            return None
        log_rows = conn.execute(
            "SELECT timestamp, level, message, metadata_json FROM decomposition_job_logs WHERE job_id=? ORDER BY timestamp, id",
            (job_id,),
        ).fetchall()

    logs = [
        {
            "timestamp": log_row["timestamp"],
            "level": log_row["level"],
            "message": log_row["message"],
            "metadata": _json_load(log_row["metadata_json"]) or {},
        }
        for log_row in log_rows
    ]

    return {
        "job_id": row["job_id"],
        "plan_id": plan_id,
        "job_type": row["job_type"] or "plan_decompose",
        "mode": row["mode"],
        "target_task_id": row["target_task_id"],
        "status": row["status"],
        "error": row["error"],
        "params": _json_load(row["params_json"]) or {},
        "stats": _json_load(row["stats_json"]) or {},
        "result": _json_load(row["result_json"]) or None,
        "metadata": _json_load(row["metadata_json"]) or {},
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "logs": logs,
    }


def _ensure_decomposition_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decomposition_jobs (
            job_id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL DEFAULT 'plan_decompose',
            mode TEXT NOT NULL,
            target_task_id INTEGER,
            status TEXT NOT NULL,
            error TEXT,
            params_json TEXT,
            stats_json TEXT,
            result_json TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            finished_at TIMESTAMP
        )
        """
    )
    _ensure_column(
        conn,
        "decomposition_jobs",
        "job_type",
        "job_type TEXT NOT NULL DEFAULT 'plan_decompose'",
    )
    _ensure_column(
        conn,
        "decomposition_jobs",
        "metadata_json",
        "metadata_json TEXT",
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decomposition_job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            metadata_json TEXT,
            FOREIGN KEY (job_id) REFERENCES decomposition_jobs(job_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_logs_job ON decomposition_job_logs(job_id, timestamp)"
    )


def _ensure_action_log_tables(conn, *, plan_id: Optional[int] = None) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_action_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER,
            job_id TEXT NOT NULL,
            job_type TEXT,
            sequence INTEGER NOT NULL,
            session_id TEXT,
            user_message TEXT,
            action_kind TEXT NOT NULL,
            action_name TEXT NOT NULL,
            status TEXT NOT NULL,
            success INTEGER,
            message TEXT,
            details_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_action_logs_job_seq ON plan_action_logs(job_id, sequence)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_action_logs_plan_created ON plan_action_logs(plan_id, created_at)"
    )
    # 保持 schema_version 更新
    if plan_id is not None:
        conn.execute(
            """
            INSERT INTO plan_meta (key, value)
            VALUES ('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (SCHEMA_VERSION,),
        )


def _trim_text(value: Optional[str], *, limit: int = 1024) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _redact_log_payload(data: Any, *, depth: int = 0) -> Any:
    if data is None:
        return None
    if depth >= 3:
        return "[truncated]"
    if isinstance(data, dict):
        sanitized: Dict[str, Any] = {}
        for key, value in data.items():
            key_str = str(key)
            if key_str.lower() in SENSITIVE_KEYS:
                sanitized[key_str] = "[redacted]"
                continue
            sanitized[key_str] = _redact_log_payload(value, depth=depth + 1)
        return sanitized
    if isinstance(data, list):
        limited = [*data[:20]]
        sanitized_list: List[Any] = [
            _redact_log_payload(item, depth=depth + 1) for item in limited
        ]
        remaining = len(data) - len(limited)
        if remaining > 0:
            sanitized_list.append(f"... ({remaining} more items)")
        return sanitized_list
    if isinstance(data, (str, bytes)):
        text = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else data
        return _trim_text(text, limit=2048)
    if isinstance(data, (int, float, bool)):
        return data
    return _trim_text(repr(data), limit=2048)


def append_action_log_entry(
    *,
    plan_id: Optional[int],
    job_id: str,
    job_type: Optional[str],
    session_id: Optional[str],
    user_message: Optional[str],
    action_kind: str,
    action_name: str,
    status: str,
    success: Optional[bool],
    message: Optional[str] = None,
    parameters: Optional[Dict[str, Any]] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not job_id:
        raise ValueError("job_id is required for action log entry.")

    db_path = _resolve_job_db_path(plan_id)
    sanitized_message = _trim_text(message, limit=1024)
    sanitized_session_id = _trim_text(session_id, limit=128)
    sanitized_user_message = _trim_text(user_message, limit=1024)

    payload: Dict[str, Any] = {}
    if parameters:
        payload["parameters"] = parameters
    if details:
        payload["details"] = details

    redacted_details = _redact_log_payload(payload) if payload else None
    success_int: Optional[int]
    if success is True:
        success_int = 1
    elif success is False:
        success_int = 0
    else:
        success_int = None

    with plan_db_connection(db_path) as conn:
        _ensure_action_log_tables(conn, plan_id=plan_id)
        current_max = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS seq FROM plan_action_logs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        next_sequence = int(current_max["seq"]) + 1 if current_max else 1
        conn.execute(
            """
            INSERT INTO plan_action_logs (
                plan_id, job_id, job_type, sequence, session_id, user_message,
                action_kind, action_name, status, success, message, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                job_id,
                job_type,
                next_sequence,
                sanitized_session_id,
                sanitized_user_message,
                action_kind,
                action_name,
                status,
                success_int,
                sanitized_message,
                _json_dump(redacted_details),
            ),
        )
        row = conn.execute(
            """
            SELECT id, plan_id, job_id, job_type, sequence, session_id, user_message,
                   action_kind, action_name, status, success, message, details_json,
                   created_at, updated_at
            FROM plan_action_logs
            WHERE job_id=? AND sequence=?
            """,
            (job_id, next_sequence),
        ).fetchone()

    return {
        "id": row["id"],
        "plan_id": row["plan_id"],
        "job_id": row["job_id"],
        "job_type": row["job_type"],
        "sequence": row["sequence"],
        "session_id": row["session_id"],
        "user_message": row["user_message"],
        "action_kind": row["action_kind"],
        "action_name": row["action_name"],
        "status": row["status"],
        "success": None if row["success"] is None else bool(row["success"]),
        "message": row["message"],
        "details": _json_load(row["details_json"]) or {},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_action_logs(
    plan_id: Optional[int],
    *,
    job_id: Optional[str] = None,
    limit: int = 100,
    cursor: Optional[str] = None,
    reverse: bool = False,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    db_path = _resolve_job_db_path(plan_id)
    order = "ASC" if reverse else "DESC"
    limit = max(1, min(limit, 500))

    cursor_sequence: Optional[int] = None
    cursor_created: Optional[str] = None
    if cursor:
        try:
            sequence_part, created_part = cursor.split(":", 1)
            cursor_sequence = int(sequence_part)
            cursor_created = created_part
        except Exception:
            cursor_sequence = None
            cursor_created = None

    query = [
        "SELECT id, plan_id, job_id, job_type, sequence, session_id, user_message,",
        "       action_kind, action_name, status, success, message, details_json,",
        "       created_at, updated_at",
        "FROM plan_action_logs",
        "WHERE 1=1",
    ]
    params: List[Any] = []
    if job_id:
        query.append("AND job_id=?")
        params.append(job_id)
    if plan_id is not None:
        query.append("AND plan_id=?")
        params.append(plan_id)
    if cursor_sequence is not None and cursor_created is not None:
        if reverse:
            query.append(
                "AND (sequence > ? OR (sequence = ? AND created_at > ?))"
            )
        else:
            query.append(
                "AND (sequence < ? OR (sequence = ? AND created_at < ?))"
            )
        params.extend([cursor_sequence, cursor_sequence, cursor_created])

    query.append(f"ORDER BY sequence {order}")
    query.append("LIMIT ?")
    params.append(limit + 1)

    with plan_db_connection(db_path) as conn:
        _ensure_action_log_tables(conn, plan_id=plan_id)
        rows = conn.execute("\n".join(query), params).fetchall()

    next_cursor: Optional[str] = None
    if len(rows) > limit:
        tail = rows[-1]
        next_cursor = f"{tail['sequence']}:{tail['created_at']}"
        rows = rows[:-1]

    logs: List[Dict[str, Any]] = []
    for row in rows:
        logs.append(
            {
                "id": row["id"],
                "plan_id": row["plan_id"],
                "job_id": row["job_id"],
                "job_type": row["job_type"],
                "sequence": row["sequence"],
                "session_id": row["session_id"],
                "user_message": row["user_message"],
                "action_kind": row["action_kind"],
                "action_name": row["action_name"],
                "status": row["status"],
                "success": None if row["success"] is None else bool(row["success"]),
                "message": row["message"],
                "details": _json_load(row["details_json"]) or {},
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    return logs, next_cursor


def cleanup_action_logs(
    plan_id: Optional[int],
    *,
    older_than_days: Optional[int] = None,
    max_rows: Optional[int] = None,
) -> None:
    db_path = _resolve_job_db_path(plan_id)
    with plan_db_connection(db_path) as conn:
        _ensure_action_log_tables(conn, plan_id=plan_id)
        if older_than_days is not None and older_than_days > 0:
            cutoff = datetime.utcnow() - timedelta(days=older_than_days)
            conn.execute(
                "DELETE FROM plan_action_logs WHERE created_at < ?",
                (cutoff.isoformat(),),
            )
        if max_rows is not None and max_rows > 0:
            rows = conn.execute(
                "SELECT id FROM plan_action_logs ORDER BY created_at DESC LIMIT -1 OFFSET ?",
                (max_rows,),
            ).fetchall()
            if rows:
                ids_to_delete = [row["id"] for row in rows]
                placeholders = ",".join(["?"] * len(ids_to_delete))
                conn.execute(
                    f"DELETE FROM plan_action_logs WHERE id IN ({placeholders})",
                    ids_to_delete,
                )
