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



def _seed_plan(conn: sqlite3.Connection, plan_id: int, owner: str | None, task_ids: list[int]) -> None:
    """Create a main-db plan row plus its shard holding plan-local task ids."""
    conn.execute("CREATE TABLE IF NOT EXISTS plans (id INTEGER PRIMARY KEY, owner TEXT)")
    conn.execute("INSERT OR REPLACE INTO plans (id, owner) VALUES (?, ?)", (plan_id, owner))
    conn.commit()

    from app.repository.plan_storage import get_plan_db_path

    shard = sqlite3.connect(str(get_plan_db_path(plan_id)))
    shard.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, name TEXT)")
    for tid in task_ids:
        shard.execute("INSERT OR REPLACE INTO tasks (id, name) VALUES (?, ?)", (tid, f"plan{plan_id}-t{tid}"))
    shard.commit()
    shard.close()


def _insert_raw_memory(conn: sqlite3.Connection, memory_id: str, owner_id: str | None, tags_raw: str | None) -> None:
    conn.execute(
        "INSERT INTO memories (id, content, memory_type, importance, tags, owner_id, created_at, last_accessed) "
        "VALUES (?, ?, 'conversation', 'medium', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (memory_id, f"content {memory_id}", tags_raw, owner_id),
    )
    conn.commit()


def _memory_ids(conn: sqlite3.Connection) -> set:
    return {row[0] for row in conn.execute("SELECT id FROM memories").fetchall()}


def _make_hooks(memory_service: Any) -> MemoryHooks:
    hooks = MemoryHooks.__new__(MemoryHooks)
    hooks.memory_service = memory_service
    hooks.enabled = True
    hooks.stats = {"total_saved": 0, "by_type": {}, "last_save_time": None}
    return hooks


# ---------------------------------------------------------------------------
# Session-delete cascade: authorized owner + exact session tag
# ---------------------------------------------------------------------------


async def test_delete_memories_for_session_cascades(service) -> None:
    svc, conn = service
    doomed = await svc.save_memory(_save_request("user_a", tags=["conversation", "user", "session:sess_del"]))
    kept = await svc.save_memory(
        _save_request(
            "user_a",
            content="ERCP 另一条保留的会话记忆",
            tags=["conversation", "user", "session:sess_keep"],
        )
    )
    conn.execute(
        "INSERT INTO memory_embeddings (memory_id, embedding_vector) VALUES (?, ?)",
        (doomed.memory_id, "[0.1, 0.2]"),
    )
    conn.commit()

    deleted = svc.delete_memories_for_session("sess_del", "user_a")
    assert deleted == 1

    remaining = conn.execute("SELECT id, tags FROM memories").fetchall()
    assert len(remaining) == 1
    assert "sess_keep" in remaining[0][1]
    assert conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 0

    result = await svc.query_memory(QueryMemoryRequest(search_text="ERCP", owner_id="user_a"))
    assert result.total == 1
    assert result.memories[0].memory_id == kept.memory_id


def test_delete_memories_for_session_no_match(service) -> None:
    svc, conn = service
    _insert_raw_memory(conn, "m_a", "user_a", '["session:sess_x"]')
    assert svc.delete_memories_for_session("sess_nope", "user_a") == 0
    assert svc.delete_memories_for_session("", "user_a") == 0
    # Missing owner is a no-op: the cascade is never unscoped.
    assert svc.delete_memories_for_session("sess_x", "") == 0
    assert _memory_ids(conn) == {"m_a"}


def test_session_cleanup_isolated_by_owner(service) -> None:
    svc, conn = service
    _insert_raw_memory(conn, "a_shared", "user_a", '["conversation", "session:sess_shared"]')
    _insert_raw_memory(conn, "b_shared", "user_b", '["conversation", "session:sess_shared"]')
    conn.execute(
        "INSERT INTO memory_embeddings (memory_id, embedding_vector) VALUES ('a_shared', '[0.1]'), ('b_shared', '[0.2]')"
    )
    conn.commit()

    assert svc.delete_memories_for_session("sess_shared", "user_a") == 1

    assert _memory_ids(conn) == {"b_shared"}
    embedding_ids = {row[0] for row in conn.execute("SELECT memory_id FROM memory_embeddings").fetchall()}
    assert embedding_ids == {"b_shared"}

    # A different owner cannot cascade-delete user_b's row via the same tag.
    assert svc.delete_memories_for_session("sess_shared", "user_c") == 0
    assert _memory_ids(conn) == {"b_shared"}


def test_session_cleanup_matches_exact_tag_only(service) -> None:
    svc, conn = service
    _insert_raw_memory(conn, "exact", "user_a", '["session:sess_a_1"]')
    # LIKE wildcards ("_" / "%") and prefix/suffix lookalikes must not match.
    _insert_raw_memory(conn, "underscore_lookalike", "user_a", '["session:sessXaY1"]')
    _insert_raw_memory(conn, "longer_id", "user_a", '["session:sess_a_10"]')
    _insert_raw_memory(conn, "prefixed_tag", "user_a", '["xsession:sess_a_1"]')
    _insert_raw_memory(conn, "object_tags", "user_a", '{"k": "session:sess_a_1"}')
    _insert_raw_memory(conn, "non_json_tags", "user_a", 'session:sess_a_1 "session:sess_a_1"')
    _insert_raw_memory(conn, "null_tags", "user_a", None)

    assert svc.delete_memories_for_session("sess_a_1", "user_a") == 1
    assert "exact" not in _memory_ids(conn)

    # Wildcard-shaped session ids delete nothing else.
    assert svc.delete_memories_for_session("sess%", "user_a") == 0
    assert svc.delete_memories_for_session("sess_a__", "user_a") == 0
    assert _memory_ids(conn) == {
        "underscore_lookalike",
        "longer_id",
        "prefixed_tag",
        "object_tags",
        "non_json_tags",
        "null_tags",
    }


def test_session_cleanup_preserves_legacy_ownerless_rows(service) -> None:
    svc, conn = service
    _insert_raw_memory(conn, "legacy_null_owner", None, '["session:sess_old"]')
    _insert_raw_memory(conn, "owned", "user_a", '["session:sess_old"]')

    assert svc.delete_memories_for_session("sess_old", "user_a") == 1
    row = conn.execute("SELECT owner_id, tags FROM memories WHERE id = 'legacy_null_owner'").fetchone()
    assert row is not None
    assert row[0] is None
    assert row[1] == '["session:sess_old"]'


# ---------------------------------------------------------------------------
# Plan task ownership: plan-local task ids never resolve without plan context
# ---------------------------------------------------------------------------


def test_owner_resolvers(service) -> None:
    _svc, conn = service
    conn.execute("INSERT INTO chat_sessions (id, owner_id) VALUES ('s1', 'u1')")
    conn.commit()
    _seed_plan(conn, 42, "u1", [5])
    _seed_plan(conn, 43, None, [5])

    assert ms.resolve_owner_id_for_session("s1") == "u1"
    assert ms.resolve_owner_id_for_session("missing") is None
    assert ms.resolve_owner_id_for_session(None) is None
    assert ms.resolve_owner_id_for_plan_task(42, 5) == "u1"
    assert ms.resolve_owner_id_for_plan_task(42) == "u1"
    assert ms.resolve_owner_id_for_plan_task(42, 999) is None
    assert ms.resolve_owner_id_for_plan_task(43, 5) is None  # ownerless plan
    assert ms.resolve_owner_id_for_plan_task(None, 5) is None  # bare task id
    assert ms.resolve_owner_id_for_plan_task(None) is None
    assert not hasattr(ms, "resolve_owner_id_for_task")


def test_plan_local_task_ids_resolve_per_plan(service) -> None:
    _svc, conn = service
    # Both plans number their first task 1; only plan 102 has task 2.
    _seed_plan(conn, 101, "alice", [1])
    _seed_plan(conn, 102, "bob", [1, 2])

    assert ms.resolve_owner_id_for_plan_task(101, 1) == "alice"
    assert ms.resolve_owner_id_for_plan_task(102, 1) == "bob"
    assert ms.resolve_owner_id_for_plan_task("102", "1") == "bob"
    assert ms.resolve_owner_id_for_plan_task(102, 2) == "bob"
    # Task 2 is not part of plan 101 -> unknown, never borrowed from plan 102.
    assert ms.resolve_owner_id_for_plan_task(101, 2) is None
    assert ms.resolve_owner_id_for_plan_task(999, 1) is None  # unknown plan
    for bad in ("x", 0, -1, True, 1.5j):
        assert ms.resolve_owner_id_for_plan_task(bad, 1) is None
        assert ms.resolve_owner_id_for_plan_task(101, bad) is None

    # Ownership is read fresh (no per-task cache that could go stale or collide).
    conn.execute("UPDATE plans SET owner = 'carol' WHERE id = 101")
    conn.commit()
    assert ms.resolve_owner_id_for_plan_task(101, 1) == "carol"


def test_plan_task_owner_missing_shard_is_unknown(service) -> None:
    _svc, conn = service
    conn.execute("CREATE TABLE IF NOT EXISTS plans (id INTEGER PRIMARY KEY, owner TEXT)")
    conn.execute("INSERT INTO plans (id, owner) VALUES (77, 'alice')")
    conn.commit()
    assert ms.resolve_owner_id_for_plan_task(77, 1) is None
    assert ms.resolve_owner_id_for_plan_task(77) == "alice"


class _StubMemoryService:
    def __init__(self) -> None:
        self.requests: List[SaveMemoryRequest] = []

    async def save_memory(self, request: SaveMemoryRequest):
        self.requests.append(request)
        return SimpleNamespace(memory_id=f"mem_stub_{len(self.requests)}")


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
    assert memory_id == "mem_stub_1"
    assert stub.requests[0].owner_id == "user_a"


async def test_hooks_task_complete_resolves_owner_from_plan(service) -> None:
    _svc, conn = service
    _seed_plan(conn, 42, "u1", [7])

    stub = _StubMemoryService()
    hooks = _make_hooks(stub)

    memory_id = await hooks.on_task_complete(
        task_id=7,
        task_name="质控",
        task_content="对 ERCP 数据做质控",
        task_result="完成",
        success=True,
        plan_id=42,
    )
    assert memory_id == "mem_stub_1"
    assert stub.requests[0].owner_id == "u1"
    assert stub.requests[0].related_task_id == 7
    assert "plan:42" in (stub.requests[0].tags or [])


async def test_hooks_skip_when_plan_context_missing_or_unknown(service) -> None:
    _svc, conn = service
    _seed_plan(conn, 101, "alice", [1])
    _seed_plan(conn, 102, "bob", [1])

    stub = _StubMemoryService()
    hooks = _make_hooks(stub)

    # Bare plan-local task id: ambiguous between plans 101 and 102 -> skipped.
    assert await hooks.on_task_complete(task_id=1, task_name="t", task_content="c", task_result="r") is None
    assert await hooks.on_error_occurred(error_message="boom", error_type="RuntimeError", task_id=1) is None
    assert await hooks.on_error_occurred(error_message="boom", error_type="RuntimeError") is None
    assert await hooks.on_evaluation_complete(task_id=1, evaluation_result={"ok": True}, score=0.9) is None
    # Unknown plan / task not in plan -> skipped.
    assert await hooks.on_task_complete(task_id=1, task_name="t", task_content="c", plan_id=999) is None
    assert await hooks.on_task_complete(task_id=5, task_name="t", task_content="c", plan_id=101) is None

    assert stub.requests == []
    assert hooks.stats["total_saved"] == 0


async def test_hooks_error_and_evaluation_use_plan_owner(service) -> None:
    _svc, conn = service
    _seed_plan(conn, 101, "alice", [1])
    _seed_plan(conn, 102, "bob", [1])

    stub = _StubMemoryService()
    hooks = _make_hooks(stub)

    assert await hooks.on_error_occurred(
        error_message="boom", error_type="RuntimeError", task_id=1, plan_id=102
    )
    assert await hooks.on_evaluation_complete(
        task_id=1, evaluation_result={"ok": True}, score=0.9, plan_id=101
    )
    # Plan-level error without a task id still resolves via the plan.
    assert await hooks.on_error_occurred(error_message="plan boom", error_type="PlanError", plan_id=101)

    owners = [(r.owner_id, r.related_task_id, [t for t in (r.tags or []) if t.startswith("plan:")]) for r in stub.requests]
    assert owners == [
        ("bob", 1, ["plan:102"]),
        ("alice", 1, ["plan:101"]),
        ("alice", None, ["plan:101"]),
    ]


async def test_two_plan_local_task_ids_saved_and_recalled_per_owner(service) -> None:
    """End-to-end offline: same task id 1 in two plans lands with each plan's owner."""
    svc, conn = service
    _seed_plan(conn, 101, "alice", [1])
    _seed_plan(conn, 102, "bob", [1])
    hooks = _make_hooks(svc)

    alice_mem = await hooks.on_task_complete(
        task_id=1,
        task_name="ERCP 质控 alice",
        task_content="alice 的 ERCP 数据质控",
        task_result="alice 完成",
        plan_id=101,
    )
    bob_mem = await hooks.on_task_complete(
        task_id=1,
        task_name="ERCP 质控 bob",
        task_content="bob 的 ERCP 数据质控",
        task_result="bob 完成",
        plan_id=102,
    )
    skipped = await hooks.on_task_complete(
        task_id=1,
        task_name="ERCP 质控 unknown",
        task_content="无计划上下文",
        task_result="完成",
    )
    assert alice_mem and bob_mem and alice_mem != bob_mem
    assert skipped is None

    rows = {
        row[0]: (row[1], row[2], row[3])
        for row in conn.execute("SELECT id, owner_id, related_task_id, tags FROM memories").fetchall()
    }
    assert set(rows) == {alice_mem, bob_mem}
    assert rows[alice_mem][:2] == ("alice", 1)
    assert rows[bob_mem][:2] == ("bob", 1)
    assert '"plan:101"' in rows[alice_mem][2]
    assert '"plan:102"' in rows[bob_mem][2]

    alice_view = await svc.query_memory(QueryMemoryRequest(search_text="ERCP", owner_id="alice"))
    bob_view = await svc.query_memory(QueryMemoryRequest(search_text="ERCP", owner_id="bob"))
    assert [m.memory_id for m in alice_view.memories] == [alice_mem]
    assert [m.memory_id for m in bob_view.memories] == [bob_mem]
