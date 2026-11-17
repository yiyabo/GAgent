"""p71: chat runs interrupted by a restart are continued by the server."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from app.repository import chat_runs as cr
from app.services import chat_restart_resume as crr
from app.services import chat_run_hub as hub
from app.tests.chat.test_chat_runs import _SCHEMA


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()

    @contextmanager
    def fake_get_db() -> Iterator[sqlite3.Connection]:
        yield conn

    monkeypatch.setattr(cr, "get_db", fake_get_db)
    return conn


def _run(conn, run_id: str, *, status: str, error: str | None, created: str = "-10 minutes",
         key: str | None = None, context: dict | None = None) -> None:
    request = {"message": "analyse my phages", "session_id": "sess_unit", "context": context or {}}
    conn.execute(
        """INSERT INTO chat_runs (run_id, session_id, owner_id, status, error, request_json,
                                  idempotency_key, created_at, finished_at)
           VALUES (?, 'sess_unit', 'owner-1', ?, ?, ?, ?, datetime('now', ?), datetime('now', ?))""",
        (run_id, status, error, json.dumps(request), key, created, created),
    )
    conn.commit()


def test_lists_only_recent_restart_interrupted_runs(db):
    _run(db, "r_lease", status="failed", error=cr._STALE_RUN_ERROR)
    _run(db, "r_restart", status="failed", error="server restarted; run interrupted")
    _run(db, "r_llm", status="failed", error="LLM 400")
    _run(db, "r_old", status="failed", error=cr._STALE_RUN_ERROR, created="-3 days")
    _run(db, "r_ok", status="succeeded", error=None)

    ids = {row["run_id"] for row in cr.list_restart_interrupted_runs(window_hours=12)}

    assert ids == {"r_lease", "r_restart"}


@pytest.fixture()
def started(monkeypatch: pytest.MonkeyPatch, db):
    calls: list[dict] = []

    def fake_start(request, *, session_id, owner_id):
        calls.append({"request": request, "session_id": session_id, "owner_id": owner_id})
        _run(db, f"child_{len(calls)}", status="running", error=None, created="+0 seconds",
             key=request.client_message_id, context=request.context)
        return f"child_{len(calls)}"

    from app.routers.chat import run_routes
    from app.services import run_resume

    monkeypatch.setattr(run_routes, "start_background_chat_run", fake_start)
    monkeypatch.setattr(run_resume, "resume_info",
                        lambda row: {"can_resume": True, "automatic_resume_allowed": True, "reason_code": "ready"})
    return calls


def test_interrupted_run_gets_exactly_one_continuation(db, started):
    _run(db, "r1", status="failed", error=cr._STALE_RUN_ERROR)

    first = crr.resume_interrupted_runs()
    second = crr.resume_interrupted_runs()

    assert [o["status"] for o in first] == ["resumed"]
    assert len(started) == 1
    request = started[0]["request"]
    assert request.client_message_id == "resume:r1"
    assert request.context["resume_from_run_id"] == "r1"
    assert request.context["auto_resumed_after_restart"] is True
    assert started[0]["owner_id"] == "owner-1"
    assert {o["status"] for o in second} == {"already_resumed"}


def test_run_the_user_already_moved_past_is_not_resumed(db, started):
    _run(db, "r1", status="failed", error=cr._STALE_RUN_ERROR, created="-20 minutes")
    _run(db, "r2", status="succeeded", error=None, created="-5 minutes")

    outcomes = crr.resume_interrupted_runs()

    assert outcomes[0]["status"] == "superseded" and started == []


def test_unsafe_runs_are_left_for_the_user(db, started, monkeypatch):
    from app.services import run_resume

    monkeypatch.setattr(run_resume, "resume_info", lambda row: {
        "can_resume": False, "automatic_resume_allowed": False, "reason_code": "reconciliation_required"})
    _run(db, "r1", status="failed", error=cr._STALE_RUN_ERROR)

    outcomes = crr.resume_interrupted_runs()

    assert outcomes[0] == {"run_id": "r1", "session_id": "sess_unit", "status": "not_resumable",
                           "reason_code": "reconciliation_required"}
    assert started == []


def test_disabled_switch(db, started, monkeypatch):
    monkeypatch.setenv("CHAT_RUN_AUTO_RESUME_ENABLED", "0")
    _run(db, "r1", status="failed", error=cr._STALE_RUN_ERROR)

    assert crr.resume_interrupted_runs() == [] and started == []


def test_shutdown_flag_round_trip():
    try:
        hub.mark_shutting_down()
        assert hub.is_shutting_down()
    finally:
        hub.mark_shutting_down(False)
    assert not hub.is_shutting_down()


def test_worker_keeps_run_resumable_when_process_shuts_down():
    import inspect

    from app.services import chat_run_worker

    source = inspect.getsource(chat_run_worker.execute_chat_run)
    shutdown = source.index("hub.is_shutting_down() and not cancel_ev.is_set()")
    closeout = source.index('"Run cancelled."}), stage="cancel-closeout"')
    assert shutdown < closeout
