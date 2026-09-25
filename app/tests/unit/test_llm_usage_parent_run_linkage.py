"""Parent/child linkage for delegated sub-agent rows in ``llm_usage_log``.

Covers the S3a attribution contract: a delegated run keeps its own ``run_id``
and additionally carries ``parent_run_id`` (the conversation turn / plan task
that delegated), so delegation cost can be grouped by parent.  Rows without a
delegation link keep everything they had before, with ``parent_run_id`` NULL.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.repository.llm_usage import (
    init_llm_usage_table,
    log_llm_usage,
)

_LEGACY_TABLE_SQL = """
CREATE TABLE llm_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
)
"""


@pytest.fixture
def ledger_db(tmp_path: Path):
    """Point the shared SQLite pool at a throwaway ledger instead of the repo DB."""
    from app.database_pool import close_connection_pool, initialize_connection_pool

    db_path = tmp_path / "llm_usage_ledger.db"
    initialize_connection_pool(db_path=str(db_path))
    try:
        yield db_path
    finally:
        close_connection_pool()


def _columns(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(llm_usage_log)")}


def _index_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute("PRAGMA index_list(llm_usage_log)")}


def _rows(db_path: Path, where: str = "1=1") -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM llm_usage_log WHERE {where}")]


def test_fresh_table_has_parent_run_column_and_index(ledger_db: Path) -> None:
    init_llm_usage_table()

    assert "parent_run_id" in _columns(ledger_db)
    assert "idx_llm_usage_parent_run_id" in _index_names(ledger_db)


def test_migration_adds_parent_run_column_to_legacy_table(ledger_db: Path) -> None:
    with sqlite3.connect(ledger_db) as raw:
        raw.execute(_LEGACY_TABLE_SQL)
        raw.execute(
            "INSERT INTO llm_usage_log (provider, model, prompt_tokens, completion_tokens, total_tokens, created_at)"
            " VALUES ('qwen', 'qwen-legacy', 10, 5, 15, '2026-09-25T00:00:00')"
        )

    init_llm_usage_table()

    assert "parent_run_id" in _columns(ledger_db)
    assert "idx_llm_usage_parent_run_id" in _index_names(ledger_db)
    legacy = _rows(ledger_db, "model = 'qwen-legacy'")
    assert len(legacy) == 1
    assert legacy[0]["parent_run_id"] is None

    # The widened INSERT must match the migrated legacy schema.
    log_llm_usage(
        provider="qwen_code_cli",
        model="qwen3.7-max",
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        run_id="child_run",
        parent_run_id="parent_run",
    )
    inserted = _rows(ledger_db, "run_id = 'child_run'")
    assert len(inserted) == 1
    assert inserted[0]["parent_run_id"] == "parent_run"


def test_migration_is_idempotent(ledger_db: Path) -> None:
    with sqlite3.connect(ledger_db) as raw:
        raw.execute(_LEGACY_TABLE_SQL)

    init_llm_usage_table()
    init_llm_usage_table()
    init_llm_usage_table()

    with sqlite3.connect(ledger_db) as conn:
        parent_columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(llm_usage_log)")
            if row[1] == "parent_run_id"
        ]
    assert parent_columns == ["parent_run_id"]


def test_log_llm_usage_writes_parent_run_without_touching_child_run(ledger_db: Path) -> None:
    init_llm_usage_table()

    log_llm_usage(
        provider="qwen_code_cli",
        model="qwen3.7-max",
        prompt_tokens=1000,
        completion_tokens=200,
        total_tokens=1200,
        session_id="session-1",
        plan_id=7,
        task_id=3,
        call_purpose="qwen_code_cli_execution",
        run_id="20260925_120000_000000_deadbeef",
        parent_run_id="plan_7_task_3",
        tool_name="code_executor",
        duration_ms=4321.0,
        call_status="ok",
    )

    rows = _rows(ledger_db)
    assert len(rows) == 1
    row = rows[0]
    # The child keeps its own run identity; the parent is an added link.
    assert row["run_id"] == "20260925_120000_000000_deadbeef"
    assert row["parent_run_id"] == "plan_7_task_3"
    assert row["tool_name"] == "code_executor"
    assert row["duration_ms"] == 4321.0


def test_log_llm_usage_leaves_parent_null_by_default(ledger_db: Path) -> None:
    init_llm_usage_table()

    log_llm_usage(
        provider="qwen",
        model="qwen-test",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        run_id="chat_run_1",
        call_purpose="chat_main",
    )

    rows = _rows(ledger_db)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "chat_run_1"
    assert rows[0]["parent_run_id"] is None


def _seed_parent_and_children() -> None:
    """One parent chat turn with its own calls plus two delegated child runs."""
    log_llm_usage(
        provider="qwen",
        model="qwen-max",
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        session_id="session-1",
        call_purpose="chat_main",
        run_id="chat_run_42",
        duration_ms=2000.0,
    )
    log_llm_usage(
        provider="qwen",
        model="qwen-max",
        prompt_tokens=200,
        completion_tokens=100,
        total_tokens=300,
        session_id="session-1",
        call_purpose="chat_main",
        run_id="chat_run_42",
        duration_ms=1000.0,
    )
    log_llm_usage(
        provider="qwen_code_cli",
        model="qwen3.7-max",
        prompt_tokens=4000,
        completion_tokens=1000,
        total_tokens=5000,
        session_id="session-1",
        call_purpose="qwen_code_cli_execution",
        run_id="child_run_a",
        parent_run_id="chat_run_42",
        tool_name="code_executor",
        duration_ms=60000.0,
        call_status="ok",
    )
    log_llm_usage(
        provider="qwen_code_cli",
        model="qwen3.7-max",
        prompt_tokens=200,
        completion_tokens=100,
        total_tokens=300,
        session_id="session-1",
        call_purpose="qwen_code_cli_execution",
        run_id="child_run_b",
        parent_run_id="chat_run_42",
        tool_name="code_executor",
        duration_ms=5000.0,
        call_status="error",
    )


def test_child_run_usage_summary_aggregates_delegations(ledger_db: Path) -> None:
    from app.repository.llm_usage import get_child_run_usage_summary

    init_llm_usage_table()
    _seed_parent_and_children()

    summary = get_child_run_usage_summary("chat_run_42")

    assert summary is not None
    assert summary["parent_run_id"] == "chat_run_42"
    assert summary["child_run_count"] == 2

    # Children are ordered by wall clock, which is where delegation cost lands.
    first, second = summary["children"]
    assert first["child_run_id"] == "child_run_a"
    assert first["total_tokens"] == 5000
    assert first["prompt_tokens"] == 4000
    assert first["completion_tokens"] == 1000
    assert first["duration_ms"] == 60000.0
    assert first["providers"] == ["qwen_code_cli"]
    assert first["models"] == ["qwen3.7-max"]
    assert first["tool_names"] == ["code_executor"]
    assert first["call_statuses"] == ["ok"]
    assert first["ok_calls"] == 1
    assert first["error_calls"] == 0

    assert second["child_run_id"] == "child_run_b"
    assert second["total_tokens"] == 300
    assert second["call_statuses"] == ["error"]
    assert second["ok_calls"] == 0
    assert second["error_calls"] == 1

    assert summary["children_total"]["total_tokens"] == 5300
    assert summary["children_total"]["duration_ms"] == 65000.0
    assert summary["children_total"]["error_calls"] == 1
    assert summary["children_total"]["ok_calls"] == 1

    parent_own = summary["parent_own"]
    assert parent_own is not None
    assert parent_own["run_id"] == "chat_run_42"
    assert parent_own["call_count"] == 2
    assert parent_own["total_tokens"] == 1800
    assert parent_own["duration_ms"] == 3000.0
    assert parent_own["error_calls"] == 0

    assert summary["combined_total"]["total_tokens"] == 7100
    assert summary["combined_total"]["duration_ms"] == 68000.0
    assert summary["combined_total"]["call_count"] == 4


def test_child_run_usage_summary_without_links(ledger_db: Path) -> None:
    from app.repository.llm_usage import get_child_run_usage_summary

    init_llm_usage_table()
    log_llm_usage(
        provider="qwen",
        model="qwen-max",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        call_purpose="chat_main",
        run_id="solo_run",
        duration_ms=500.0,
    )

    summary = get_child_run_usage_summary("solo_run")

    assert summary is not None
    assert summary["child_run_count"] == 0
    assert summary["children"] == []
    assert summary["children_total"]["total_tokens"] == 0
    assert summary["children_total"]["duration_ms"] == 0.0
    assert summary["parent_own"]["total_tokens"] == 15
    assert summary["combined_total"]["total_tokens"] == 15


def test_child_run_usage_summary_handles_unknown_parent(ledger_db: Path) -> None:
    from app.repository.llm_usage import get_child_run_usage_summary

    init_llm_usage_table()
    _seed_parent_and_children()

    summary = get_child_run_usage_summary("no_such_run")

    assert summary is not None
    assert summary["child_run_count"] == 0
    assert summary["parent_own"] is None
    assert summary["combined_total"]["total_tokens"] == 0


def test_child_run_usage_summary_requires_parent_run_id(ledger_db: Path) -> None:
    from app.repository.llm_usage import get_child_run_usage_summary

    init_llm_usage_table()
    assert get_child_run_usage_summary("") is None


def test_run_usage_summary_keeps_reporting_the_child_run(ledger_db: Path) -> None:
    """A delegated run's own summary must stay keyed on its own run id."""
    from app.repository.llm_usage import get_run_usage_summary

    init_llm_usage_table()
    _seed_parent_and_children()

    child = get_run_usage_summary("child_run_a")

    assert child is not None
    assert child["run_id"] == "child_run_a"
    assert child["call_count"] == 1
    assert child["total_tokens"] == 5000

    parent = get_run_usage_summary("chat_run_42")
    assert parent is not None
    # Parent's own total excludes delegated spend — the link is opt-in.
    assert parent["total_tokens"] == 1800
