"""database. 

saveplan(plan, session), 
plantask plan SQLite filemedium. 
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config.database_config import get_database_config, get_main_database_path
from .database_pool import get_connection_pool, get_db, initialize_connection_pool
from .services.request_principal import LEGACY_LOCAL_OWNER_ID

logger = logging.getLogger(__name__)


def init_db() -> None:
    """database. """
    config = get_database_config()
    main_db_path = get_main_database_path()

    initialize_connection_pool(db_path=main_db_path)
    _ensure_plan_directory(config.get_plan_store_dir())

    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                owner TEXT,
                description TEXT,
                metadata TEXT,
                plan_db_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL DEFAULT 'legacy-local',
                name TEXT,
                name_source TEXT,
                is_user_named BOOLEAN DEFAULT 0,
                metadata TEXT,
                plan_id INTEGER,
                plan_title TEXT,
                current_task_id INTEGER,
                current_task_name TEXT,
                last_message_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE,
                FOREIGN KEY (plan_id) REFERENCES plans (id) ON DELETE SET NULL
            )
            """
        )
        _ensure_chat_session_columns(conn)
        _backfill_chat_session_owners(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES chat_sessions (id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plans_updated_at ON plans(updated_at DESC, id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_active ON chat_sessions(is_active)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner_updated "
            "ON chat_sessions(owner_id, updated_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_action_runs (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                owner_id TEXT NOT NULL DEFAULT 'legacy-local',
                user_message TEXT NOT NULL,
                mode TEXT,
                plan_id INTEGER,
                context_json TEXT,
                history_json TEXT,
                structured_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                result_json TEXT,
                errors_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES chat_sessions (id) ON DELETE SET NULL,
                FOREIGN KEY (plan_id) REFERENCES plans (id) ON DELETE SET NULL
            )
            """
        )
        _ensure_chat_action_run_columns(conn)
        _backfill_chat_action_run_owners(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_action_runs_status ON chat_action_runs(status, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_action_runs_session ON chat_action_runs(session_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_action_runs_owner_created "
            "ON chat_action_runs(owner_id, created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_decomposition_job_index (
                job_id TEXT PRIMARY KEY,
                plan_id INTEGER NOT NULL,
                job_type TEXT NOT NULL DEFAULT 'plan_decompose',
                owner_id TEXT NOT NULL DEFAULT 'legacy-local',
                session_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (plan_id) REFERENCES plans (id) ON DELETE CASCADE
            )
            """
        )
        _ensure_plan_decomposition_job_index_columns(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_index_plan ON plan_decomposition_job_index(plan_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_index_owner_created "
            "ON plan_decomposition_job_index(owner_id, created_at DESC)"
        )

        # PhageScope tracking recovery table — persists in-flight tracking jobs so
        # the polling thread can be restarted after a server restart.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS phagescope_tracking (
                job_id  TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                plan_id  INTEGER,
                remote_taskid TEXT NOT NULL,
                modulelist  TEXT,
                poll_interval REAL DEFAULT 30.0,
                poll_timeout  REAL DEFAULT 172800.0,
                status  TEXT NOT NULL DEFAULT 'running',
                created_at  TEXT NOT NULL,
                finished_at  TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_phagescope_tracking_status ON phagescope_tracking(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_phagescope_tracking_session ON phagescope_tracking(session_id)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                owner_id TEXT NOT NULL DEFAULT 'legacy-local',
                status TEXT NOT NULL DEFAULT 'queued',
                user_message_id INTEGER,
                assistant_message_id INTEGER,
                idempotency_key TEXT,
                error TEXT,
                request_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                last_event_seq INTEGER NOT NULL DEFAULT -1,
                FOREIGN KEY (session_id) REFERENCES chat_sessions (id) ON DELETE CASCADE
            )
            """
        )
        _ensure_chat_run_columns(conn)
        _backfill_chat_run_owners(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_runs_session_status "
            "ON chat_runs(session_id, status, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_runs_owner_created "
            "ON chat_runs(owner_id, created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_run_events (
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (run_id, seq),
                FOREIGN KEY (run_id) REFERENCES chat_runs (run_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_run_events_run_seq "
            "ON chat_run_events(run_id, seq)"
        )

    try:
        cleaned = config.cleanup_old_sessions(max_age_days=30)
        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} old session databases")
    except Exception as e:
        logger.warning(f"Failed to cleanup old sessions: {e}")

    logger.info("Main database initialised at %s", main_db_path)


def close_db_pool() -> None:
    """closeconnection, . """
    get_connection_pool().close_pool()


@contextmanager
def plan_db_connection(plan_path: Path) -> Iterator:
    """ plan fileconnection."""
    import sqlite3

    conn = sqlite3.connect(plan_path, isolation_level="DEFERRED")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_plan_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _ensure_chat_session_columns(conn) -> None:
    """Ensure newly required columns exist on chat_sessions table."""
    info_rows = conn.execute("PRAGMA table_info(chat_sessions)").fetchall()
    existing = {row["name"] for row in info_rows}

    if "owner_id" not in existing:
        conn.execute(
            "ALTER TABLE chat_sessions ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'legacy-local'"
        )
    if "plan_title" not in existing:
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN plan_title TEXT")
    if "current_task_id" not in existing:
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN current_task_id INTEGER")
    if "current_task_name" not in existing:
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN current_task_name TEXT")
    if "last_message_at" not in existing:
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN last_message_at TIMESTAMP")
    if "metadata" not in existing:
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN metadata TEXT")
    if "name_source" not in existing:
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN name_source TEXT")
    if "is_user_named" not in existing:
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN is_user_named BOOLEAN DEFAULT 0")


def _ensure_chat_action_run_columns(conn) -> None:
    info_rows = conn.execute("PRAGMA table_info(chat_action_runs)").fetchall()
    existing = {row["name"] for row in info_rows}
    if "owner_id" not in existing:
        conn.execute(
            "ALTER TABLE chat_action_runs ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'legacy-local'"
        )


def _ensure_chat_run_columns(conn) -> None:
    info_rows = conn.execute("PRAGMA table_info(chat_runs)").fetchall()
    existing = {row["name"] for row in info_rows}
    if "owner_id" not in existing:
        conn.execute(
            "ALTER TABLE chat_runs ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'legacy-local'"
        )


def _ensure_plan_decomposition_job_index_columns(conn) -> None:
    info_rows = conn.execute("PRAGMA table_info(plan_decomposition_job_index)").fetchall()
    existing = {row["name"] for row in info_rows}
    if "job_type" not in existing:
        conn.execute(
            "ALTER TABLE plan_decomposition_job_index ADD COLUMN job_type TEXT NOT NULL DEFAULT 'plan_decompose'"
        )
    if "owner_id" not in existing:
        conn.execute(
            "ALTER TABLE plan_decomposition_job_index ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'legacy-local'"
        )
    if "session_id" not in existing:
        conn.execute("ALTER TABLE plan_decomposition_job_index ADD COLUMN session_id TEXT")


def _backfill_chat_session_owners(conn) -> None:
    conn.execute(
        """
        UPDATE chat_sessions
        SET owner_id = ?
        WHERE owner_id IS NULL OR TRIM(owner_id) = ''
        """,
        (LEGACY_LOCAL_OWNER_ID,),
    )


def _backfill_chat_action_run_owners(conn) -> None:
    conn.execute(
        """
        UPDATE chat_action_runs
        SET owner_id = COALESCE(
            (
                SELECT NULLIF(TRIM(s.owner_id), '')
                FROM chat_sessions s
                WHERE s.id = chat_action_runs.session_id
            ),
            ?
        )
        WHERE owner_id IS NULL OR TRIM(owner_id) = ''
        """,
        (LEGACY_LOCAL_OWNER_ID,),
    )


def _backfill_chat_run_owners(conn) -> None:
    conn.execute(
        """
        UPDATE chat_runs
        SET owner_id = COALESCE(
            (
                SELECT NULLIF(TRIM(s.owner_id), '')
                FROM chat_sessions s
                WHERE s.id = chat_runs.session_id
            ),
            ?
        )
        WHERE owner_id IS NULL OR TRIM(owner_id) = ''
        """,
        (LEGACY_LOCAL_OWNER_ID,),
    )
