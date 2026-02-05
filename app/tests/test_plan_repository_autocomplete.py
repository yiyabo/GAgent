import json
import sqlite3

from app.repository.plan_repository import PlanRepository


def _make_tasks_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            parent_id INTEGER,
            status TEXT,
            execution_result TEXT,
            updated_at TEXT
        )
        """
    )
    return conn


def test_autocomplete_skipped_blocked_by_deps_does_not_complete_parent():
    conn = _make_tasks_conn()
    conn.execute(
        "INSERT INTO tasks (id, parent_id, status, execution_result, updated_at) VALUES (?, ?, ?, ?, ?)",
        (10, None, "pending", None, ""),
    )
    blocked_payload = json.dumps(
        {"status": "skipped", "metadata": {"blocked_by_dependencies": True}},
        ensure_ascii=False,
    )
    conn.execute(
        "INSERT INTO tasks (id, parent_id, status, execution_result, updated_at) VALUES (?, ?, ?, ?, ?)",
        (11, 10, "skipped", blocked_payload, ""),
    )

    repo = PlanRepository()
    updated = repo._maybe_autocomplete_ancestors(conn, 11)

    assert updated == 0
    row = conn.execute("SELECT status FROM tasks WHERE id=?", (10,)).fetchone()
    assert row["status"] == "pending"


def test_autocomplete_skipped_non_blocked_completes_parent():
    conn = _make_tasks_conn()
    conn.execute(
        "INSERT INTO tasks (id, parent_id, status, execution_result, updated_at) VALUES (?, ?, ?, ?, ?)",
        (20, None, "pending", None, ""),
    )
    non_blocked_payload = json.dumps(
        {"status": "skipped", "metadata": {"blocked_by_dependencies": False}},
        ensure_ascii=False,
    )
    conn.execute(
        "INSERT INTO tasks (id, parent_id, status, execution_result, updated_at) VALUES (?, ?, ?, ?, ?)",
        (21, 20, "skipped", non_blocked_payload, ""),
    )

    repo = PlanRepository()
    updated = repo._maybe_autocomplete_ancestors(conn, 21)

    assert updated == 1
    row = conn.execute("SELECT status FROM tasks WHERE id=?", (20,)).fetchone()
    assert row["status"] == "completed"

