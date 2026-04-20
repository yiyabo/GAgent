import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.repository import plan_repository as plan_repository_module
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


def _make_plan_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE plan_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
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
            context_updated_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE task_dependencies (
            task_id INTEGER NOT NULL,
            depends_on INTEGER NOT NULL,
            PRIMARY KEY (task_id, depends_on)
        )
        """
    )
    return conn


def _insert_task(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    name: str,
    parent_id: int | None,
    position: int,
    path: str,
    depth: int,
    instruction: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO tasks (
            id, name, status, instruction, parent_id, position, path, depth,
            metadata, execution_result, context_combined, context_sections,
            context_meta, context_updated_at, created_at, updated_at
        )
        VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, '{}', NULL, NULL, '[]', '{}', '', '', '')
        """,
        (task_id, name, instruction, parent_id, position, path, depth),
    )


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
    row = conn.execute("SELECT status, execution_result FROM tasks WHERE id=?", (20,)).fetchone()
    assert row["status"] == "completed"
    payload = json.loads(row["execution_result"])
    assert payload["status"] == "completed"
    assert payload["metadata"]["auto_completed_from_children"] is True
    assert payload["metadata"]["source_task_id"] == 21


def test_reconcile_running_status_from_structured_execution_result():
    """Running tasks should NOT be reconciled from stale execution_result.

    When a task is actively running, its old execution_result is stale and
    must not override the current 'running' status.
    """
    conn = _make_tasks_conn()
    conn.execute(
        "INSERT INTO tasks (id, parent_id, status, execution_result, updated_at) VALUES (?, ?, ?, ?, ?)",
        (
            30,
            None,
            "running",
            json.dumps({"status": "failed", "content": "tool invocation failed"}, ensure_ascii=False),
            "",
        ),
    )

    repo = PlanRepository()
    updated = repo._reconcile_task_statuses_from_execution_results(conn, 1)

    assert updated == 0
    row = conn.execute("SELECT status FROM tasks WHERE id=?", (30,)).fetchone()
    assert row["status"] == "running"


def test_apply_changes_atomically_reorders_within_same_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.sqlite"
    conn = _make_plan_db(plan_path)
    _insert_task(conn, task_id=1, name="Root", parent_id=None, position=0, path="/1", depth=0)
    _insert_task(conn, task_id=2, name="A", parent_id=1, position=0, path="/1/2", depth=1)
    _insert_task(conn, task_id=3, name="B", parent_id=1, position=1, path="/1/3", depth=1)
    conn.commit()
    conn.close()

    repo = PlanRepository()
    monkeypatch.setattr(plan_repository_module, "get_plan_db_path", lambda _plan_id: plan_path)
    monkeypatch.setattr(repo, "_touch_plan", lambda _plan_id: None)

    applied = repo.apply_changes_atomically(
        7,
        [{"action": "reorder_task", "task_id": 3, "new_position": 0}],
    )

    assert applied == [
        {"action": "reorder_task", "task_id": 3, "new_position": 0, "parent_id": 1}
    ]

    check = sqlite3.connect(plan_path)
    check.row_factory = sqlite3.Row
    rows = check.execute(
        "SELECT id, position FROM tasks WHERE parent_id=? ORDER BY position ASC, id ASC",
        (1,),
    ).fetchall()
    check.close()

    assert [(row["id"], row["position"]) for row in rows] == [(3, 0), (2, 1)]


def test_apply_changes_atomically_skips_invalid_change_and_applies_valid_ones(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Invalid changes are skipped; valid changes in the same batch still apply."""
    plan_path = tmp_path / "plan.sqlite"
    conn = _make_plan_db(plan_path)
    _insert_task(conn, task_id=1, name="Root", parent_id=None, position=0, path="/1", depth=0)
    _insert_task(conn, task_id=2, name="A", parent_id=1, position=0, path="/1/2", depth=1)
    conn.commit()
    conn.close()

    repo = PlanRepository()
    monkeypatch.setattr(plan_repository_module, "get_plan_db_path", lambda _plan_id: plan_path)
    monkeypatch.setattr(repo, "_touch_plan", lambda _plan_id: None)

    applied = repo.apply_changes_atomically(
        7,
        [
            {"action": "add_task", "name": "New Task"},
            {"action": "update_task", "task_id": 999, "name": "Broken"},
        ],
    )

    # add_task succeeded, update_task was skipped
    assert len(applied) == 1
    assert applied[0]["action"] == "add_task"

    check = sqlite3.connect(plan_path)
    check.row_factory = sqlite3.Row
    rows = check.execute("SELECT id, name, parent_id FROM tasks ORDER BY id ASC").fetchall()
    check.close()

    assert len(rows) == 3  # Root + A + New Task
    assert rows[2]["name"] == "New Task"


def test_apply_changes_atomically_updates_plan_description_and_root_instruction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.sqlite"
    conn = _make_plan_db(plan_path)
    conn.execute(
        "INSERT INTO plan_meta (key, value) VALUES (?, ?)",
        ("description", "Original description"),
    )
    _insert_task(
        conn,
        task_id=1,
        name="Root",
        parent_id=None,
        position=0,
        path="/1",
        depth=0,
        instruction="Original description",
    )
    _insert_task(conn, task_id=2, name="A", parent_id=1, position=0, path="/1/2", depth=1)
    conn.commit()
    conn.close()

    main_db = sqlite3.connect(":memory:")
    main_db.row_factory = sqlite3.Row
    main_db.execute(
        """
        CREATE TABLE plans (
            id INTEGER PRIMARY KEY,
            description TEXT,
            updated_at TEXT
        )
        """
    )
    main_db.execute(
        "INSERT INTO plans (id, description, updated_at) VALUES (?, ?, ?)",
        (7, "Original description", ""),
    )
    main_db.commit()

    @contextmanager
    def _fake_get_db():
        try:
            yield main_db
            main_db.commit()
        except Exception:
            main_db.rollback()
            raise

    repo = PlanRepository()
    monkeypatch.setattr(plan_repository_module, "get_plan_db_path", lambda _plan_id: plan_path)
    monkeypatch.setattr(plan_repository_module, "get_db", _fake_get_db)
    monkeypatch.setattr(repo, "_touch_plan", lambda _plan_id: None)

    applied = repo.apply_changes_atomically(
        7,
        [{"action": "update_description", "description": "Revised plan rationale"}],
    )

    assert applied == [{"action": "update_description", "updated_fields": ["description"]}]

    check = sqlite3.connect(plan_path)
    check.row_factory = sqlite3.Row
    root = check.execute("SELECT instruction FROM tasks WHERE id=?", (1,)).fetchone()
    meta = check.execute("SELECT value FROM plan_meta WHERE key='description'").fetchone()
    check.close()

    plan_row = main_db.execute("SELECT description FROM plans WHERE id=?", (7,)).fetchone()
    main_db.close()

    assert root["instruction"] == "Revised plan rationale"
    assert meta["value"] == "Revised plan rationale"
    assert plan_row["description"] == "Revised plan rationale"
