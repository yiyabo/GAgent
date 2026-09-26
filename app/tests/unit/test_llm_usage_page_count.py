"""``llm_usage_log.page_count``: the basis of a per-page (non-token) charge.

The PDF file-extract reader bills a flat rate per document page. No token column
can reconstruct that charge, so the page count travels with the usage row and
``estimated_cost`` is expected to already include the page component. ``NULL``
means the call was not page-billed, which is every row recorded before this
column existed.
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


def _rows(db_path: Path) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM llm_usage_log")]


def test_fresh_table_has_page_count_column(ledger_db: Path) -> None:
    init_llm_usage_table()

    assert "page_count" in _columns(ledger_db)


def test_migration_adds_page_count_to_a_legacy_table(ledger_db: Path) -> None:
    with sqlite3.connect(ledger_db) as conn:
        conn.execute(_LEGACY_TABLE_SQL)
        conn.commit()

    init_llm_usage_table()

    assert "page_count" in _columns(ledger_db)


def test_log_llm_usage_round_trips_page_count(ledger_db: Path) -> None:
    init_llm_usage_table()

    log_llm_usage(
        provider="qwen",
        model="qwen-long",
        prompt_tokens=10,
        completion_tokens=2,
        total_tokens=12,
        call_purpose="pdf_parse",
        tool_name="vision_reader",
        page_count=42,
        estimated_cost=0.84,
        cost_currency="CNY",
    )
    log_llm_usage(
        provider="qwen",
        model="qwen-max",
        prompt_tokens=10,
        completion_tokens=2,
        total_tokens=12,
        call_purpose="chat_main",
    )

    rows = _rows(ledger_db)
    by_purpose = {row["call_purpose"]: row for row in rows}
    assert by_purpose["pdf_parse"]["page_count"] == 42
    assert by_purpose["pdf_parse"]["estimated_cost"] == pytest.approx(0.84)
    assert by_purpose["chat_main"]["page_count"] is None
