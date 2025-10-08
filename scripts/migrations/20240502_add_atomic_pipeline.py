#!/usr/bin/env python3
"""Database migration: add context_refs/artifacts columns and task execution logs."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Iterable, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "databases" / "main" / "tasks.db"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def add_column(conn: sqlite3.Connection, table: str, column: Tuple[str, str]) -> None:
    name, ddl = column
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    except sqlite3.OperationalError:
        # Column already exists
        pass


def ensure_columns(conn: sqlite3.Connection) -> None:
    additions: Iterable[Tuple[str, str]] = (
        ("context_refs", "TEXT"),
        ("artifacts", "TEXT"),
    )
    for col in additions:
        add_column(conn, "tasks", col)


def ensure_execution_logs_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_execution_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            workflow_id TEXT,
            step_type TEXT,
            content TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_execution_logs_task ON task_execution_logs(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_execution_logs_workflow ON task_execution_logs(workflow_id)")


def ensure_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_root_status ON tasks(root_id, status)")


def run_migration(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = connect(db_path)
    try:
        conn.execute("BEGIN")
        ensure_columns(conn)
        ensure_execution_logs_table(conn)
        ensure_indexes(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Add atomic execution pipeline metadata")
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to tasks SQLite database",
    )
    args = parser.parse_args()
    run_migration(args.database)
    print(f"Migration completed successfully for {args.database}")


if __name__ == "__main__":
    main()
