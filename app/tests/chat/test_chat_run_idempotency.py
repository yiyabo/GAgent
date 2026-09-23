"""Idempotency guarantees for chat run creation and message persistence.

Covers the retry-safety contract: a retried POST carrying the same
``client_message_id`` must return the existing run instead of re-running the
agent, and the user message must be persisted exactly once.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from typing import Iterator, List

import pytest

import app.database as app_database
from app.repository import chat_runs as cr
from app.routers.chat import run_routes
from app.routers.chat.background import _sse_with_keepalive
from app.routers.chat.models import ChatRequest
from app.routers.chat.session_helpers import _save_chat_message
from app.services import chat_run_hub as hub


_SCHEMA = """
CREATE TABLE chat_sessions (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'legacy-local',
    name TEXT,
    name_source TEXT,
    is_user_named INTEGER DEFAULT 0,
    metadata TEXT,
    plan_id INTEGER,
    plan_title TEXT,
    project_id INTEGER,
    last_message_at TEXT,
    created_at TEXT,
    updated_at TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    metadata TEXT
);
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
CREATE UNIQUE INDEX idx_chat_runs_idempotency
ON chat_runs(session_id, idempotency_key)
WHERE idempotency_key IS NOT NULL;
"""


@pytest.fixture()
def idem_db(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()

    @contextmanager
    def fake_get_db() -> Iterator[sqlite3.Connection]:
        yield conn

    monkeypatch.setattr(cr, "get_db", fake_get_db)
    monkeypatch.setattr(run_routes, "get_db", fake_get_db)
    monkeypatch.setattr(app_database, "get_db", fake_get_db)
    return conn


@pytest.fixture(autouse=True)
def _stub_memory_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.memory.chat_memory_middleware as mw

    class _Stub:
        async def process_message(self, **kwargs) -> None:
            return None

    monkeypatch.setattr(mw, "get_chat_memory_middleware", lambda: _Stub())


@pytest.fixture()
async def worker_gate(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """Fake worker that stays alive until teardown; records spawn calls."""
    gate = asyncio.Event()
    spawns: List[str] = []

    async def _fake_worker(run_id: str) -> None:
        await gate.wait()

    def _counting_register(run_id: str, task: asyncio.Task) -> None:
        spawns.append(run_id)
        _real_register(run_id, task)

    _real_register = hub.register_worker_task
    monkeypatch.setattr(run_routes, "execute_chat_run", _fake_worker)
    monkeypatch.setattr(run_routes, "_schedule_quality_follow_up", lambda **kwargs: None)
    monkeypatch.setattr(hub, "register_worker_task", _counting_register)
    yield spawns
    gate.set()
    await asyncio.sleep(0)


def _req(msg: str = "分析一下这个文件", cmid: str | None = "ck_a") -> ChatRequest:
    return ChatRequest(message=msg, session_id="sess_unit", client_message_id=cmid)


def _user_message_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM chat_messages WHERE session_id = ? AND role = 'user'",
        ("sess_unit",),
    ).fetchone()
    return int(row["c"])


# --- repository level -------------------------------------------------------


def test_create_chat_run_stores_idempotency_key(idem_db: sqlite3.Connection) -> None:
    cr.create_chat_run("run_k1", "sess_unit", "{}", idempotency_key="ck_1")
    row = cr.get_chat_run_by_idempotency_key("sess_unit", "ck_1")
    assert row is not None
    assert row["run_id"] == "run_k1"
    assert cr.get_chat_run_by_idempotency_key("sess_unit", "missing") is None


def test_idempotency_key_unique_index(idem_db: sqlite3.Connection) -> None:
    cr.create_chat_run("run_k2", "sess_unit", "{}", idempotency_key="ck_dup")
    with pytest.raises(sqlite3.IntegrityError):
        cr.create_chat_run("run_k3", "sess_unit", "{}", idempotency_key="ck_dup")
    # NULL keys never collide
    cr.create_chat_run("run_k4", "sess_unit", "{}")
    cr.create_chat_run("run_k5", "sess_unit", "{}")


# --- start_background_chat_run ----------------------------------------------


async def test_start_run_retry_returns_existing_run(
    idem_db: sqlite3.Connection, worker_gate: List[str]
) -> None:
    r1 = run_routes.start_background_chat_run(_req(), session_id="sess_unit", owner_id="u1")
    r2 = run_routes.start_background_chat_run(_req(), session_id="sess_unit", owner_id="u1")
    assert r1 == r2
    assert _user_message_count(idem_db) == 1
    assert len(worker_gate) == 1  # live worker not re-spawned
    row = cr.get_chat_run(r1)
    assert row is not None
    assert row["idempotency_key"] == "ck_a"
    assert row["user_message_id"] is not None


async def test_start_run_distinct_keys_create_distinct_runs(
    idem_db: sqlite3.Connection, worker_gate: List[str]
) -> None:
    r1 = run_routes.start_background_chat_run(_req(cmid="ck_1"), session_id="sess_unit", owner_id="u1")
    r2 = run_routes.start_background_chat_run(_req(cmid="ck_2"), session_id="sess_unit", owner_id="u1")
    assert r1 != r2
    assert _user_message_count(idem_db) == 2
    assert len(worker_gate) == 2


async def test_start_run_without_key_always_creates(
    idem_db: sqlite3.Connection, worker_gate: List[str]
) -> None:
    r1 = run_routes.start_background_chat_run(_req(cmid=None), session_id="sess_unit", owner_id="u1")
    r2 = run_routes.start_background_chat_run(_req(cmid=None), session_id="sess_unit", owner_id="u1")
    assert r1 != r2
    assert _user_message_count(idem_db) == 2


async def test_reentry_queued_without_worker_respawns(
    idem_db: sqlite3.Connection, worker_gate: List[str]
) -> None:
    # Simulates a crash between INSERT and create_task: row exists, no worker.
    cr.create_chat_run("run_crash", "sess_unit", "{}", idempotency_key="ck_crash")
    run_id = run_routes.start_background_chat_run(
        _req(cmid="ck_crash"), session_id="sess_unit", owner_id="u1"
    )
    assert run_id == "run_crash"
    assert worker_gate == ["run_crash"]
    row = cr.get_chat_run("run_crash")
    assert row is not None
    assert row["user_message_id"] is not None  # crash window healed
    assert _user_message_count(idem_db) == 1


async def test_reentry_terminal_run_not_respawned(
    idem_db: sqlite3.Connection, worker_gate: List[str]
) -> None:
    cr.create_chat_run("run_done", "sess_unit", "{}", idempotency_key="ck_done")
    cr.mark_chat_run_finished("run_done", "succeeded")
    run_id = run_routes.start_background_chat_run(
        _req(cmid="ck_done"), session_id="sess_unit", owner_id="u1"
    )
    assert run_id == "run_done"
    assert worker_gate == []


# --- _save_chat_message ------------------------------------------------------


async def test_save_chat_message_dedupes_client_message_id(
    idem_db: sqlite3.Connection,
) -> None:
    m1 = _save_chat_message("sess_unit", "user", "hello", {"client_message_id": "ck_m"}, owner_id="u1")
    m2 = _save_chat_message("sess_unit", "user", "hello", {"client_message_id": "ck_m"}, owner_id="u1")
    assert isinstance(m1, int) and m1 == m2
    assert _user_message_count(idem_db) == 1
    # Messages without a key are never deduped
    m3 = _save_chat_message("sess_unit", "user", "hello", None, owner_id="u1")
    m4 = _save_chat_message("sess_unit", "user", "hello", None, owner_id="u1")
    assert isinstance(m3, int) and isinstance(m4, int) and m3 != m4


# --- SSE keepalive -----------------------------------------------------------


async def test_sse_with_keepalive_emits_comment_on_idle() -> None:
    async def slow_source():
        await asyncio.sleep(0.25)
        yield "data: done\n\n"

    lines: List[str] = []
    async for line in _sse_with_keepalive(slow_source(), idle_seconds=0.05):
        lines.append(line)
    assert lines[-1] == "data: done\n\n"
    assert any(line == ": keepalive\n\n" for line in lines)
