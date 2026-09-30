"""Memory owner isolation: save stamping, fail-closed recall, session-delete cascade."""

from __future__ import annotations

import contextlib
import sqlite3
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from app.config.database_config import DatabaseConfig
from app.models_memory import (
    ImportanceLevel,
    MemoryType,
    QueryMemoryRequest,
    SaveMemoryRequest,
)
from app.services.memory import memory_service as ms
from app.services.memory.chat_memory_middleware import ChatMemoryMiddleware
from app.services.memory.memory_hooks import MemoryHooks


def _make_conn(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "main.sqlite"))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE chat_sessions (id TEXT PRIMARY KEY, owner_id TEXT)")
    conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, session_id TEXT)")
    conn.commit()
    return conn


@pytest.fixture()
def service(monkeypatch: pytest.MonkeyPatch, tmp_path):
    conn = _make_conn(tmp_path)

    @contextlib.contextmanager
    def fake_get_db():
        yield conn

    monkeypatch.setattr(ms, "get_db", fake_get_db)
    monkeypatch.setenv("DB_ROOT", str(tmp_path))

    svc = ms.IntegratedMemoryService.__new__(ms.IntegratedMemoryService)
    svc.llm_client = None
    svc.embeddings_service = SimpleNamespace(
        get_single_embedding=lambda *_a, **_k: None,
        compute_similarity=lambda *_a, **_k: 0.0,
    )
    svc.evolution_threshold = 10
    svc.evolution_count = 0
    svc.db_config = DatabaseConfig()
    svc._ensure_memory_tables()
    yield svc, conn
    conn.close()


def _save_request(owner_id: Optional[str], **overrides: Any) -> SaveMemoryRequest:
    payload: Dict[str, Any] = dict(
        content="ERCP 术后胆道感染危险因素分析记录",
        memory_type=MemoryType.CONVERSATION,
        importance=ImportanceLevel.MEDIUM,
        keywords=["ERCP"],
        context="medical",
        tags=["conversation", "user"],
        owner_id=owner_id,
    )
    payload.update(overrides)
    return SaveMemoryRequest(**payload)


async def test_save_stamps_owner_and_recall_isolated(service) -> None:
    svc, conn = service
    await svc.save_memory(_save_request("user_a"))
    row = conn.execute("SELECT owner_id FROM memories").fetchone()
    assert row[0] == "user_a"

    mine = await svc.query_memory(QueryMemoryRequest(search_text="ERCP", owner_id="user_a"))
    assert mine.total == 1

    other = await svc.query_memory(QueryMemoryRequest(search_text="ERCP", owner_id="user_b"))
    assert other.total == 0


async def test_global_recall_without_owner_fails_closed(service) -> None:
    svc, _conn = service
    await svc.save_memory(_save_request("user_a"))
    result = await svc.query_memory(QueryMemoryRequest(search_text="ERCP"))
    assert result.total == 0
    assert result.memories == []


def test_owner_column_migration_idempotent(tmp_path) -> None:
    conn = sqlite3.connect(str(tmp_path / "legacy.sqlite"))
    conn.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT)")
    conn.execute("INSERT INTO memories VALUES ('m1', 'legacy row')")
    conn.commit()

    ms._ensure_owner_column(conn)
    ms._ensure_owner_column(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
    assert "owner_id" in cols
    assert conn.execute("SELECT content FROM memories WHERE id = 'm1'").fetchone()[0] == "legacy row"
    conn.close()


async def test_delete_memories_for_session_cascades(service) -> None:
    svc, conn = service
    doomed = await svc.save_memory(
        _save_request("user_a", tags=["conversation", "user", "session:sess_del"])
    )
    kept = await svc.save_memory(
        _save_request("user_a", tags=["conversation", "user", "session:sess_keep"])
    )
    conn.execute(
        "INSERT INTO memory_embeddings (memory_id, embedding_vector) VALUES (?, ?)",
        (doomed.memory_id, "[0.1, 0.2]"),
    )
    conn.commit()

    deleted = svc.delete_memories_for_session("sess_del")
    assert deleted == 1

    remaining = conn.execute("SELECT id, tags FROM memories").fetchall()
    assert len(remaining) == 1
    assert "sess_keep" in remaining[0][1]
    assert (
        conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 0
    )

    result = await svc.query_memory(QueryMemoryRequest(search_text="ERCP", owner_id="user_a"))
    assert result.total == 1
    assert result.memories[0].memory_id == kept.memory_id


def test_delete_memories_for_session_no_match(service) -> None:
    svc, _conn = service
    assert svc.delete_memories_for_session("sess_nope") == 0
    assert svc.delete_memories_for_session("") == 0


def test_owner_resolvers(service) -> None:
    _svc, conn = service
    ms._task_owner_cache.clear()
    conn.execute("INSERT INTO chat_sessions (id, owner_id) VALUES ('s1', 'u1')")
    conn.execute("CREATE TABLE plans (id INTEGER PRIMARY KEY, owner TEXT)")
    conn.execute("INSERT INTO plans (id, owner) VALUES (42, 'u1')")
    conn.execute("INSERT INTO plans (id, owner) VALUES (43, NULL)")
    conn.commit()

    from app.repository.plan_storage import get_plan_db_path

    shard_path = get_plan_db_path(42)
    shard = sqlite3.connect(str(shard_path))
    shard.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT)")
    shard.execute("INSERT INTO tasks (id, name) VALUES (5, 't5')")
    shard.commit()
    shard.close()
    empty_shard_path = get_plan_db_path(43)
    empty_shard = sqlite3.connect(str(empty_shard_path))
    empty_shard.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT)")
    empty_shard.commit()
    empty_shard.close()

    assert ms.resolve_owner_id_for_session("s1") == "u1"
    assert ms.resolve_owner_id_for_session("missing") is None
    assert ms.resolve_owner_id_for_session(None) is None
    assert ms.resolve_owner_id_for_task(5) == "u1"
    assert ms.resolve_owner_id_for_task(999) is None
    assert ms.resolve_owner_id_for_task(None) is None


class _StubMemoryService:
    def __init__(self) -> None:
        self.requests: List[SaveMemoryRequest] = []

    async def save_memory(self, request: SaveMemoryRequest):
        self.requests.append(request)
        return SimpleNamespace(memory_id="mem_stub")


async def test_middleware_stamps_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubMemoryService()
    monkeypatch.setattr(ms, "get_memory_service", lambda: stub)

    middleware = ChatMemoryMiddleware.__new__(ChatMemoryMiddleware)
    middleware.hooks = None
    middleware.llm_client = None
    middleware.enabled = True

    memory_id = await middleware.process_message(
        content="记住：ERCP 数据集的随访截止日期是 2026-03-01",
        role="user",
        session_id="sess_x",
        force_save=True,
        owner_id="user_a",
    )
    assert memory_id == "mem_stub"
    assert stub.requests[0].owner_id == "user_a"


async def test_hooks_task_complete_resolves_owner(
    monkeypatch: pytest.MonkeyPatch, service
) -> None:
    _svc, conn = service
    ms._task_owner_cache.clear()
    conn.execute("CREATE TABLE IF NOT EXISTS plans (id INTEGER PRIMARY KEY, owner TEXT)")
    conn.execute("INSERT INTO plans (id, owner) VALUES (42, 'u1')")
    conn.commit()

    from app.repository.plan_storage import get_plan_db_path

    shard = sqlite3.connect(str(get_plan_db_path(42)))
    shard.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, name TEXT)")
    shard.execute("INSERT OR REPLACE INTO tasks (id, name) VALUES (7, 't7')")
    shard.commit()
    shard.close()

    stub = _StubMemoryService()
    hooks = MemoryHooks.__new__(MemoryHooks)
    hooks.memory_service = stub
    hooks.enabled = True
    hooks.stats = {"total_saved": 0, "by_type": {}, "last_save_time": None}

    await hooks.on_task_complete(
        task_id=7,
        task_name="质控",
        task_content="对 ERCP 数据做质控",
        task_result="完成",
        success=True,
    )
    assert stub.requests[0].owner_id == "u1"
