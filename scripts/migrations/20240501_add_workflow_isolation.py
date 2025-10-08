#!/usr/bin/env python3
"""Database migration: add workflow isolation fields and table.

This migration performs the following steps:
1. Ensure the `workflows` registry table exists.
2. Add `root_id`, `workflow_id`, and `metadata` columns to the `tasks` table.
3. Backfill existing records so every task is associated with a workflow/root.
4. Normalise session IDs so descendants inherit their root's session when missing.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "databases" / "main" / "tasks.db"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def ensure_workflows_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL UNIQUE,
            session_id TEXT,
            root_task_id INTEGER UNIQUE,
            title TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (root_task_id) REFERENCES tasks (id) ON DELETE SET NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_session ON workflows(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_root ON workflows(root_task_id)")
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_workflows_updated_at
        AFTER UPDATE ON workflows
        FOR EACH ROW
        BEGIN
            UPDATE workflows SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
        END;
        """
    )


def add_missing_task_columns(conn: sqlite3.Connection) -> None:
    alterations = {
        "root_id": "INTEGER",
        "workflow_id": "TEXT",
        "metadata": "TEXT",
    }
    for column, ddl in alterations.items():
        if not column_exists(conn, "tasks", column):
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {ddl}")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_root_id ON tasks(root_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_workflow_id ON tasks(workflow_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_workflow_status ON tasks(workflow_id, status)")


def extract_root_id(path_value: Optional[str]) -> Optional[int]:
    if not path_value:
        return None
    first = str(path_value).strip("/").split("/")[0]
    return int(first) if first.isdigit() else None


def backfill_workflows(conn: sqlite3.Connection) -> None:
    roots = conn.execute(
        "SELECT id, name, session_id, workflow_id FROM tasks WHERE parent_id IS NULL"
    ).fetchall()

    root_sessions: Dict[int, Optional[str]] = {}

    for row in roots:
        root_id = row["id"]
        session_id = row["session_id"] or "default"
        workflow_id = row["workflow_id"] or f"wf_{root_id}"
        title = row["name"] or f"Root {root_id}"

        conn.execute(
            """
            INSERT INTO workflows (workflow_id, session_id, root_task_id, title)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(workflow_id) DO UPDATE SET
                session_id=excluded.session_id,
                root_task_id=excluded.root_task_id,
                title=excluded.title
            """,
            (workflow_id, session_id, root_id, title),
        )
        conn.execute(
            "UPDATE tasks SET root_id=?, workflow_id=?, session_id=? WHERE id=?",
            (root_id, workflow_id, session_id, root_id),
        )
        root_sessions[root_id] = session_id

    # Propagate root/workflow to descendants
    rows = conn.execute(
        "SELECT id, parent_id, path, root_id, workflow_id, session_id FROM tasks WHERE parent_id IS NOT NULL"
    ).fetchall()
    for row in rows:
        task_id = row["id"]
        path_value = row["path"]
        parent_root = row["root_id"]
        parent_workflow = row["workflow_id"]
        session_id = row["session_id"]

        derived_root = parent_root or extract_root_id(path_value)
        workflow_id = parent_workflow or (f"wf_{derived_root}" if derived_root else None)
        session_fallback = session_id
        if derived_root in root_sessions and not session_fallback:
            session_fallback = root_sessions[derived_root]

        conn.execute(
            "UPDATE tasks SET root_id=?, workflow_id=?, session_id=COALESCE(session_id, ?) WHERE id=?",
            (derived_root, workflow_id, session_fallback, task_id),
        )


def run_migration(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = connect(db_path)
    try:
        conn.execute("BEGIN")
        ensure_workflows_table(conn)
        add_missing_task_columns(conn)
        backfill_workflows(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Add workflow isolation metadata to tasks database")
    parser.add_argument(
        "--database",
        type=Path,
        default=DB_PATH,
        help="Path to tasks SQLite database (defaults to repository data/databases/main/tasks.db)",
    )
    args = parser.parse_args()
    run_migration(args.database)
    print(f"Migration completed successfully for {args.database}")


if __name__ == "__main__":
    main()
