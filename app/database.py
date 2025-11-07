"""主数据库初始化逻辑。

主库仅保存跨计划的元信息（计划索引、会话与聊天记录），
实际的计划任务数据存放在独立的 plan SQLite 文件中。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config.database_config import get_database_config, get_main_database_path
from .database_pool import get_connection_pool, get_db, initialize_connection_pool

logger = logging.getLogger(__name__)


def init_db() -> None:
    """初始化主数据库及目录结构。"""
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
            """
            CREATE TABLE IF NOT EXISTS chat_action_runs (
                id TEXT PRIMARY KEY,
                session_id TEXT,
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_action_runs_status ON chat_action_runs(status, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_action_runs_session ON chat_action_runs(session_id, created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_decomposition_job_index (
                job_id TEXT PRIMARY KEY,
                plan_id INTEGER NOT NULL,
                job_type TEXT NOT NULL DEFAULT 'plan_decompose',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (plan_id) REFERENCES plans (id) ON DELETE CASCADE
            )
            """
        )
        info_rows = conn.execute(
            "PRAGMA table_info(plan_decomposition_job_index)"
        ).fetchall()
        existing_columns = {row["name"] for row in info_rows}
        if "job_type" not in existing_columns:
            conn.execute(
                "ALTER TABLE plan_decomposition_job_index ADD COLUMN job_type TEXT NOT NULL DEFAULT 'plan_decompose'"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_index_plan ON plan_decomposition_job_index(plan_id)"
        )

    logger.info("Main database initialised at %s", main_db_path)


def close_db_pool() -> None:
    """关闭连接池，释放资源。"""
    get_connection_pool().close_pool()


@contextmanager
def plan_db_connection(plan_path: Path) -> Iterator:
    """针对单个 plan 文件建立连接的便捷方法."""
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
