from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest

from app.repository import conversation_quality as repository


_SCHEMA = """
CREATE TABLE conversation_quality_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_run_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    evaluation_basis TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    observed_until TIMESTAMP NOT NULL,
    feedback_message_id INTEGER,
    feedback_received_at TIMESTAMP,
    snapshot_json TEXT NOT NULL,
    satisfaction_level TEXT,
    confidence REAL,
    label_source TEXT,
    evaluation_json TEXT,
    evaluator_provider TEXT,
    evaluator_model TEXT,
    prompt_version TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    evaluated_at TIMESTAMP,
    finalized_at TIMESTAMP
);
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture()
def quality_db(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    @contextmanager
    def fake_get_db() -> Iterator[sqlite3.Connection]:
        yield conn

    monkeypatch.setattr(repository, "get_db", fake_get_db)
    return conn


def _create_pending(quality_db: sqlite3.Connection, *, run_id: str = "run-1", owner_id: str = "owner-a") -> None:
    created = repository.create_pending_evaluation(
        target_run_id=run_id,
        session_id="session-1",
        owner_id=owner_id,
        snapshot={"user_goal": "analyze data", "routing": {"request_tier": "execute"}},
        observed_until=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert created is True


def test_create_pending_evaluation_is_idempotent(quality_db: sqlite3.Connection) -> None:
    _create_pending(quality_db)
    assert repository.create_pending_evaluation(
        target_run_id="run-1",
        session_id="session-1",
        owner_id="owner-a",
        snapshot={},
        observed_until=datetime.now(timezone.utc),
    ) is False
    assert quality_db.execute("SELECT COUNT(*) FROM conversation_quality_evaluations").fetchone()[0] == 1


def test_feedback_claim_and_completion_are_owner_scoped(quality_db: sqlite3.Connection) -> None:
    _create_pending(quality_db)
    quality_db.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES ('session-1', 'user', 'redo this')"
    )
    quality_db.commit()
    feedback_id = int(quality_db.execute("SELECT MAX(id) FROM chat_messages").fetchone()[0])

    evaluation_id = repository.attach_feedback_to_latest_pending(
        session_id="session-1", owner_id="owner-a", feedback_message_id=feedback_id
    )
    assert evaluation_id is not None
    assert repository.attach_feedback_to_latest_pending(
        session_id="session-1", owner_id="owner-b", feedback_message_id=feedback_id
    ) is None

    claimed = repository.claim_evaluation(
        evaluation_id, evaluation_basis="follow_up_message", max_attempts=3
    )
    assert claimed is not None
    assert claimed["feedback_message_id"] == feedback_id
    assert repository.get_evaluation(evaluation_id, owner_id="owner-b") is None

    repository.complete_evaluation(
        evaluation_id,
        status="final",
        result={
            "satisfaction_level": "negative",
            "confidence": 0.9,
            "feedback_relation": "unresolved_follow_up",
            "evidence": [],
            "failure_modes": ["tool_not_invoked"],
            "responsible_stages": ["tool_selection"],
        },
        evaluator_provider="qwen",
        evaluator_model="test",
        prompt_version="v1",
    )
    completed = repository.get_evaluation(evaluation_id, owner_id="owner-a")
    assert completed is not None
    assert completed["status"] == "final"
    assert completed["evaluation"]["failure_modes"] == ["tool_not_invoked"]


def test_due_and_stale_evaluations_recover(quality_db: sqlite3.Connection) -> None:
    _create_pending(quality_db, run_id="due")
    quality_db.execute(
        "UPDATE conversation_quality_evaluations SET observed_until='2000-01-01T00:00:00+00:00' WHERE target_run_id='due'"
    )
    quality_db.commit()
    due_ids = repository.list_due_evaluation_ids()
    assert len(due_ids) == 1
    assert repository.claim_evaluation(due_ids[0], evaluation_basis="observation_timeout", max_attempts=3)
    assert repository.recover_stale_evaluations() == 1
    assert repository.get_evaluation(due_ids[0])["status"] == "retry"


def test_next_user_message_resolves_overlapping_run_follow_up(quality_db: sqlite3.Connection) -> None:
    quality_db.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES ('session-1', 'user', 'first')"
    )
    quality_db.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES ('session-1', 'assistant', 'answer')"
    )
    quality_db.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES ('session-1', 'user', 'follow up')"
    )
    quality_db.commit()
    message = repository.get_next_user_message(session_id="session-1", after_message_id=1)
    assert message is not None
    assert message["content"] == "follow up"


def test_follow_up_rows_are_due_immediately_and_retry_is_delayed(quality_db: sqlite3.Connection) -> None:
    _create_pending(quality_db, run_id="follow-up")
    quality_db.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES ('session-1', 'user', 'redo')"
    )
    quality_db.commit()
    message_id = int(quality_db.execute("SELECT MAX(id) FROM chat_messages").fetchone()[0])
    evaluation_id = repository.attach_feedback_to_latest_pending(
        session_id="session-1", owner_id="owner-a", feedback_message_id=message_id
    )
    assert evaluation_id is not None
    assert evaluation_id in repository.list_due_evaluation_ids()
    claimed = repository.claim_evaluation(
        evaluation_id,
        evaluation_basis="observation_timeout",
        max_attempts=3,
    )
    assert claimed is not None
    assert claimed["evaluation_basis"] == "follow_up_message"
    repository.record_evaluation_failure(
        evaluation_id,
        error="temporary",
        max_attempts=3,
        retry_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert evaluation_id not in repository.list_due_evaluation_ids()


def test_provisional_evaluation_is_requeued_when_feedback_arrives(quality_db: sqlite3.Connection) -> None:
    _create_pending(quality_db, run_id="provisional")
    row = repository.get_evaluation_by_run("provisional")
    assert row is not None
    assert repository.claim_evaluation(row["id"], evaluation_basis="observation_timeout", max_attempts=3)
    repository.complete_evaluation(
        row["id"],
        status="provisional",
        result={
            "satisfaction_level": "acceptable",
            "confidence": 0.4,
            "feedback_relation": "observation_timeout",
            "evidence": [],
            "failure_modes": [],
            "responsible_stages": [],
        },
        evaluator_provider="qwen",
        evaluator_model="test",
        prompt_version="v1",
    )
    quality_db.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES ('session-1', 'user', 'this is wrong')"
    )
    quality_db.commit()
    message_id = int(quality_db.execute("SELECT MAX(id) FROM chat_messages").fetchone()[0])

    assert repository.attach_feedback_to_latest_pending(
        session_id="session-1", owner_id="owner-a", feedback_message_id=message_id
    ) == row["id"]
    requeued = repository.get_evaluation(row["id"])
    assert requeued is not None
    assert requeued["status"] == "pending"
    assert requeued["satisfaction_level"] is None
    assert requeued["finalized_at"] is None


def test_summary_aggregates_evidence_only_for_current_owner(quality_db: sqlite3.Connection) -> None:
    _create_pending(quality_db, run_id="owner-a", owner_id="owner-a")
    _create_pending(quality_db, run_id="owner-b", owner_id="owner-b")
    row = repository.get_evaluation_by_run("owner-a")
    assert row is not None
    assert repository.claim_evaluation(row["id"], evaluation_basis="observation_timeout", max_attempts=3)
    repository.complete_evaluation(
        row["id"],
        status="provisional",
        result={
            "satisfaction_level": "acceptable",
            "confidence": 0.4,
            "feedback_relation": "observation_timeout",
            "evidence": [],
            "failure_modes": ["context_lost"],
            "responsible_stages": ["context_building"],
        },
        evaluator_provider="qwen",
        evaluator_model="test",
        prompt_version="v1",
    )
    summary = repository.get_quality_summary(owner_id="owner-a")
    assert summary["total"] == 1
    assert summary["by_satisfaction_level"] == [{"name": "acceptable", "count": 1}]
    assert summary["failure_modes"] == [{"name": "context_lost", "count": 1}]


def test_summary_counts_all_evaluations_not_only_first_500(quality_db: sqlite3.Connection) -> None:
    for index in range(501):
        quality_db.execute(
            """
            INSERT INTO conversation_quality_evaluations (
                target_run_id, session_id, owner_id, status, observed_until,
                snapshot_json, evaluation_json, satisfaction_level, confidence
            ) VALUES (?, 'session-1', 'owner-a', 'final', '2026-01-01', ?, ?, 'negative', 0.8)
            """,
            (
                f"summary-{index}",
                '{"routing":{"request_tier":"execute"},"tools_used":["code_executor"]}',
                '{"failure_modes":["tool_not_invoked"],"responsible_stages":["tool_selection"]}',
            ),
        )
    quality_db.commit()

    summary = repository.get_quality_summary(owner_id="owner-a")
    assert summary["evaluated"] == 501
    assert summary["failure_modes"] == [{"name": "tool_not_invoked", "count": 501}]
    assert summary["tools"] == [{"name": "code_executor", "count": 501}]
