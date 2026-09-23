"""Contract tests for the chat-run SSE event registry and choke-point validation."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

import pytest

from app.repository import chat_runs as cr
from app.services.chat_run_events import (
    CANONICAL_EVENT_EXAMPLES,
    CHAT_RUN_EVENT_TYPES,
    chat_run_event_schema,
    check_chat_run_event,
    validate_chat_run_event,
)


_SCHEMA = """
CREATE TABLE chat_runs (
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
    last_event_seq INTEGER NOT NULL DEFAULT -1
);
CREATE TABLE chat_run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, seq)
);
INSERT INTO chat_runs (run_id, session_id) VALUES ('run_schema', 'sess_unit');
"""


@pytest.fixture()
def schema_db(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()

    @contextmanager
    def fake_get_db() -> Iterator[sqlite3.Connection]:
        yield conn

    monkeypatch.setattr(cr, "get_db", fake_get_db)
    return conn


def test_canonical_examples_all_validate() -> None:
    assert set(CANONICAL_EVENT_EXAMPLES) == set(CHAT_RUN_EVENT_TYPES)
    bad = {
        event_type: validate_chat_run_event(payload)
        for event_type, payload in CANONICAL_EVENT_EXAMPLES.items()
        if validate_chat_run_event(payload)
    }
    assert bad == {}


def test_unknown_type_rejected() -> None:
    reason = validate_chat_run_event({"type": "totally_new"})
    assert reason is not None and "totally_new" in reason


def test_missing_required_core_field_rejected() -> None:
    assert validate_chat_run_event({"type": "delta"}) is not None
    assert validate_chat_run_event({"type": "thinking_delta", "iteration": 1}) is not None
    assert validate_chat_run_event(
        {"type": "thinking_delta", "iteration": "x", "delta": "a"}
    ) is not None
    assert validate_chat_run_event({"type": "final"}) is not None
    assert validate_chat_run_event({"type": "thinking_step"}) is not None


def test_extra_keys_allowed() -> None:
    assert validate_chat_run_event({"type": "delta", "content": "x", "future": 1}) is None


def test_non_object_rejected() -> None:
    assert validate_chat_run_event("nope") is not None
    assert validate_chat_run_event(None) is not None


def test_schema_export_covers_union() -> None:
    schema = chat_run_event_schema()
    assert isinstance(schema, dict) and schema


def test_check_raises_in_strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_RUN_EVENT_SCHEMA_STRICT", "1")
    with pytest.raises(ValueError):
        check_chat_run_event({"type": "unknown"}, run_id="run_schema")
    check_chat_run_event({"type": "start"}, run_id="run_schema")  # valid: no raise


def test_check_warns_and_passes_by_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("CHAT_RUN_EVENT_SCHEMA_STRICT", raising=False)
    with caplog.at_level("WARNING"):
        check_chat_run_event({"type": "unknown"}, run_id="run_schema")
    assert any("EVENT-SCHEMA" in rec.message for rec in caplog.records)


def test_append_event_validates_at_choke(
    schema_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHAT_RUN_EVENT_SCHEMA_STRICT", "1")
    seq = cr.append_chat_run_event("run_schema", {"type": "delta", "content": "tok"})
    assert seq == 0
    with pytest.raises(ValueError):
        cr.append_chat_run_event("run_schema", {"type": "bogus"})
    with pytest.raises(ValueError):
        cr.batch_append_chat_run_events("run_schema", [{"type": "delta"}])
    seqs = cr.batch_append_chat_run_events(
        "run_schema",
        [
            {"type": "thinking_delta", "iteration": 1, "delta": "a"},
            {"type": "reasoning_delta", "iteration": 1, "delta": "b"},
        ],
    )
    assert seqs == [1, 2]
