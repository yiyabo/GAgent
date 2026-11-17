"""p72 memory governance: owner/session isolation, CRUD, extraction, dedupe,
hybrid recall, retrieval accounting and the ``memory_operation`` tool."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.api import memory_api
from app.config.database_config import DatabaseConfig
from app.models_memory import ImportanceLevel, MemoryType, QueryMemoryRequest, SaveMemoryRequest
from app.repository import context_recall as recall_repo
from app.services.deep_think.plan_mode import plan_mode_tool_allowed
from app.services.memory import chat_memory_middleware as cmm
from app.services.memory import context_recall as recall_service
from app.services.memory import memory_service as ms
from tool_box.context import ToolContext
from tool_box.tools_impl.memory_operation import memory_operation_handler

pytestmark = pytest.mark.asyncio


class _Embeddings:
    """Deterministic embeddings: vectors are looked up by exact text prefix."""

    def __init__(self) -> None:
        self.vectors: dict[str, list[float]] = {}

    def get_single_embedding(self, text: str) -> list[float] | None:
        for prefix, vector in self.vectors.items():
            if text.startswith(prefix):
                return vector
        return None

    @staticmethod
    def compute_similarity(a: list[float], b: list[float]) -> float:
        return recall_service._cosine(a, b)


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "main.sqlite"))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE chat_sessions (id TEXT PRIMARY KEY, owner_id TEXT, project_id INTEGER)")
    conn.executemany(
        "INSERT INTO chat_sessions VALUES (?, ?, NULL)",
        [("sa", "alice"), ("sb", "bob"), ("orphan", None)],
    )
    conn.commit()

    @contextlib.contextmanager
    def fake_get_db():
        yield conn

    monkeypatch.setattr(ms, "get_db", fake_get_db)
    monkeypatch.setattr(recall_repo, "get_db", fake_get_db)
    monkeypatch.setenv("DB_ROOT", str(tmp_path))

    svc = ms.IntegratedMemoryService.__new__(ms.IntegratedMemoryService)
    embeddings = _Embeddings()
    vars(svc).update(
        llm_client=None,
        embeddings_service=embeddings,
        evolution_threshold=10_000,
        evolution_count=0,
        db_config=DatabaseConfig(),
    )
    svc._ensure_memory_tables()
    monkeypatch.setattr(ms, "_memory_service", svc)
    yield SimpleNamespace(svc=svc, conn=conn, embeddings=embeddings)
    conn.close()


def _req(owner: str | None, content: str, **extra: Any) -> SaveMemoryRequest:
    payload: dict[str, Any] = {
        "content": content,
        "memory_type": MemoryType.KNOWLEDGE,
        "importance": ImportanceLevel.MEDIUM,
        "keywords": ["k"],
        "context": "ctx",
        "tags": ["t"],
        "owner_id": owner,
    }
    payload.update(extra)
    return SaveMemoryRequest(**payload)


def _row(conn: sqlite3.Connection, memory_id: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()


# --- 1. owner & session IDOR -------------------------------------------------


async def test_query_memory_session_idor_fails_closed(env) -> None:
    await env.svc.save_memory(_req("bob", "bob secret lysin plan", session_id="sb"))

    # alice cannot read bob's per-session store by passing his session id
    stolen = await env.svc.query_memory(
        QueryMemoryRequest(search_text="lysin", session_id="sb", owner_id="alice")
    )
    assert stolen.total == 0
    # an empty owner is fail-closed even for a valid session
    anon = await env.svc.query_memory(QueryMemoryRequest(search_text="lysin", session_id="sb"))
    assert anon.total == 0
    # an unowned (NULL-owner) session authorizes nobody
    nobody = await env.svc.query_memory(
        QueryMemoryRequest(search_text="lysin", session_id="orphan", owner_id="alice")
    )
    assert nobody.total == 0
    own = await env.svc.query_memory(
        QueryMemoryRequest(search_text="lysin", session_id="sb", owner_id="bob")
    )
    assert own.total == 1


def test_get_conn_require_owner_rejects_foreign_session(env) -> None:
    with pytest.raises(ms.MemoryAccessDenied), env.svc._get_conn("sb", owner_id="alice", require_owner=True):
        pass
    with pytest.raises(ms.MemoryAccessDenied), env.svc._get_conn(None, require_owner=True):
        pass
    with env.svc._get_conn("sb", owner_id="bob", require_owner=True) as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_session_message_resolves_owner_before_middleware() -> None:
    source = Path("app/routers/chat/session_helpers.py").read_text(encoding="utf-8")
    block = source[source.index("memory_owner_id = owner_id") : source.index("except Exception as mem_err")]
    assert "SELECT owner_id FROM chat_sessions WHERE id = ?" in block
    assert "owner_id=memory_owner_id" in block


# --- CRUD -------------------------------------------------------------------


async def test_crud_is_owner_scoped(env, monkeypatch: pytest.MonkeyPatch) -> None:
    a1 = await env.svc.save_memory(_req("alice", "alice prefers seaborn plots"))
    await env.svc.save_memory(_req("alice", "alice uses /data3/genomes", importance=ImportanceLevel.HIGH))
    b1 = await env.svc.save_memory(_req("bob", "bob prefers matplotlib"))
    env.conn.execute("INSERT INTO memories (id, content, memory_type, importance, owner_id) VALUES ('legacy', 'null owner', 'knowledge', 'low', NULL)")
    env.conn.commit()

    page = env.svc.list_memories("alice", limit=1)
    assert page["total"] == 2 and page["limit"] == 1 and page["offset"] == 0
    item = page["items"][0]
    assert set(item) == {
        "id", "content", "memory_type", "importance", "keywords", "tags", "context",
        "created_at", "last_accessed", "retrieval_count", "embedding_generated",
    }
    assert isinstance(item["keywords"], list) and isinstance(item["retrieval_count"], int)
    assert isinstance(item["embedding_generated"], bool)
    assert env.svc.list_memories("alice", importance="high")["total"] == 1
    assert env.svc.list_memories("alice", search="seaborn")["items"][0]["id"] == a1.memory_id
    assert env.svc.list_memories("")["total"] == 0
    assert env.svc.list_memories("alice", session_id="sb")["total"] == 0

    assert await env.svc.update_memory(b1.memory_id, "alice", content="hijack") is None
    assert _row(env.conn, b1.memory_id)["content"] == "bob prefers matplotlib"
    env.embeddings.vectors["alice prefers plotnine"] = [1.0, 0.0]
    updated = await env.svc.update_memory(
        a1.memory_id, "alice", content="alice prefers plotnine", importance="critical", tags=["pref"]
    )
    assert updated is not None
    assert updated["content"] == "alice prefers plotnine" and updated["importance"] == "critical"
    assert updated["tags"] == ["pref"] and updated["embedding_generated"] is True
    with pytest.raises(ValueError):
        await env.svc.update_memory(a1.memory_id, "alice", importance="bogus")

    assert env.svc.delete_memory(b1.memory_id, "alice") is False
    assert env.svc.delete_memory("legacy", "alice") is False
    assert env.svc.delete_memory(a1.memory_id, "alice") is True
    assert _row(env.conn, a1.memory_id) is None
    assert env.conn.execute(
        "SELECT COUNT(*) FROM memory_embeddings WHERE memory_id = ?", (a1.memory_id,)
    ).fetchone()[0] == 0

    stats = await env.svc.get_memory_stats(owner_id="bob")
    assert stats.total_memories == 1


async def test_entries_api_uses_request_owner(env, monkeypatch: pytest.MonkeyPatch) -> None:
    saved = await env.svc.save_memory(_req("alice", "alice api fact"))
    owner = {"id": "alice"}
    monkeypatch.setattr(memory_api, "get_request_owner_id", lambda _request: owner["id"])
    request: Any = SimpleNamespace()

    listed = await memory_api.list_memory_entries(request, None, None, None, None, 50, 0)
    assert listed["total"] == 1
    patched = await memory_api.update_memory_entry(saved.memory_id, request, {"importance": "high"})
    assert patched["success"] is True and patched["item"]["importance"] == "high"

    owner["id"] = "bob"
    with pytest.raises(HTTPException) as missing:
        await memory_api.update_memory_entry(saved.memory_id, request, {"content": "x"})
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as gone:
        await memory_api.delete_memory_entry(saved.memory_id, request)
    assert gone.value.status_code == 404
    with pytest.raises(HTTPException) as foreign:
        await memory_api.save_chat_message(request, {"content": "hello there", "session_id": "sa"})
    assert foreign.value.status_code == 404

    owner["id"] = "alice"
    deleted = await memory_api.delete_memory_entry(saved.memory_id, request)
    assert deleted == {"success": True, "deleted_id": saved.memory_id}


# --- 2. extraction & dedupe -------------------------------------------------


async def test_save_dedupes_normalized_content_per_owner(env) -> None:
    first = await env.svc.save_memory(_req("alice", "User prefers Chinese answers.", keywords=["lang"], tags=["pref"]))
    again = await env.svc.save_memory(
        _req("alice", "  user PREFERS chinese answers ", keywords=["zh"], tags=["style"], importance=ImportanceLevel.HIGH)
    )
    assert again.memory_id == first.memory_id
    row = _row(env.conn, first.memory_id)
    assert json.loads(row["keywords"]) == ["lang", "zh"]
    assert json.loads(row["tags"]) == ["pref", "style"]
    assert row["importance"] == "high"
    other_owner = await env.svc.save_memory(_req("bob", "User prefers Chinese answers."))
    assert other_owner.memory_id != first.memory_id
    assert env.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 2


async def test_save_dedupes_by_embedding_cosine(env) -> None:
    env.embeddings.vectors["Results live in /data3/out"] = [1.0, 0.0, 0.0]
    env.embeddings.vectors["Outputs are stored at /data3/out"] = [0.95, 0.05, 0.0]
    env.embeddings.vectors["Unrelated fact"] = [0.0, 1.0, 0.0]
    first = await env.svc.save_memory(_req("alice", "Results live in /data3/out"))
    assert _row(env.conn, first.memory_id)["embedding_generated"]
    near = await env.svc.save_memory(_req("alice", "Outputs are stored at /data3/out"))
    assert near.memory_id == first.memory_id
    far = await env.svc.save_memory(_req("alice", "Unrelated fact"))
    assert far.memory_id != first.memory_id


class _FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def chat(self, prompt: str, temperature: float = 0.0) -> str:
        self.calls += 1
        return self.response


def _middleware(response: str) -> tuple[cmm.ChatMemoryMiddleware, _FakeLLM]:
    middleware = cmm.ChatMemoryMiddleware.__new__(cmm.ChatMemoryMiddleware)
    llm = _FakeLLM(response)
    vars(middleware).update(hooks=None, llm_client=llm, enabled=True)
    return middleware, llm


async def test_middleware_skips_assistant_and_trivial_without_llm(env) -> None:
    middleware, llm = _middleware('{"should_save": true, "entries": [{"content": "x"}]}')
    for text in ("ok", "好的", "继续", "OK!", "  继续。 "):
        assert await middleware.process_message(text, role="user", owner_id="alice") is None
    assert await middleware.process_message("A long assistant answer " * 10, role="assistant", owner_id="alice") is None
    assert await middleware.process_message("Remember I use R 4.4 please", owner_id=None) is None
    assert llm.calls == 0


async def test_middleware_extracts_atomic_entries(env) -> None:
    payload = {
        "should_save": True,
        "entries": [
            {"content": "User prefers answers in Chinese.", "memory_type": "knowledge", "importance": "high", "keywords": ["language"], "tags": ["preference"]},
            {"content": "Project data lives in /data3/phage.", "memory_type": "context", "importance": "medium", "keywords": ["path"], "tags": ["path"]},
            {"content": "x2", "memory_type": "bogus"},
            {"content": "x3"},
            {"content": "never saved (over the cap)"},
        ],
    }
    middleware, llm = _middleware("```json\n" + json.dumps(payload) + "\n```")
    memory_id = await middleware.process_message(
        "以后都用中文回答，我的数据在 /data3/phage 下面", session_id="sa", owner_id="alice"
    )
    assert memory_id and llm.calls == 1
    rows = env.conn.execute("SELECT content, memory_type, tags, owner_id FROM memories ORDER BY content").fetchall()
    contents = [r["content"] for r in rows]
    assert len(rows) == 3 and "never saved (over the cap)" not in contents
    assert all(not c.startswith("[user]") for c in contents)
    assert {r["owner_id"] for r in rows} == {"alice"}
    chinese = next(r for r in rows if r["content"].startswith("User prefers"))
    assert "session:sa" in json.loads(chinese["tags"]) and "preference" in json.loads(chinese["tags"])
    assert next(r for r in rows if r["content"] == "x2")["memory_type"] == "knowledge"

    nothing, _ = _middleware('{"should_save": false, "entries": []}')
    assert await nothing.process_message("What is the weather like today?", owner_id="alice") is None


def test_middleware_has_no_fixed_sleep() -> None:
    source = Path("app/services/memory/chat_memory_middleware.py").read_text(encoding="utf-8")
    assert "asyncio.sleep" not in source


# --- 3. hybrid recall & retrieval_count ------------------------------------


async def test_query_memory_increments_retrieval_count(env) -> None:
    saved = await env.svc.save_memory(_req("alice", "lysin assay protocol"))
    for _ in range(2):
        result = await env.svc.query_memory(QueryMemoryRequest(search_text="lysin", owner_id="alice"))
        assert result.total == 1
    row = _row(env.conn, saved.memory_id)
    assert row["retrieval_count"] == 2 and row["last_accessed"] is not None


async def test_repository_mark_recalled_is_owner_scoped(env) -> None:
    mine = await env.svc.save_memory(_req("alice", "alice note"))
    theirs = await env.svc.save_memory(_req("bob", "bob note"))
    recall_repo.mark_recalled("alice", [mine.memory_id, theirs.memory_id])
    recall_repo.mark_recalled("alice", [mine.memory_id])
    assert _row(env.conn, mine.memory_id)["retrieval_count"] == 2
    assert _row(env.conn, theirs.memory_id)["retrieval_count"] == 0


def _candidate(identity: str, scope: str, content: str = "unrelated words") -> dict[str, Any]:
    return {
        "id": identity, "content": content, "memory_type": "knowledge", "importance": "medium",
        "keywords": "[]", "scope": scope, "source_store": "main",
        "source_session_id": "sa" if scope == "session" else None, "created_at": "2026-11-01",
    }


def test_recall_hybrid_vector_and_cross_session(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _candidate("same-session", "session", "lysin note"),
        _candidate("cross-session-pref", "owner", "user likes concise answers"),
        _candidate("cross-session-noise", "owner", "lysin"),
    ]
    marked: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(recall_repo, "session_scope", lambda sid: {"id": sid, "owner_id": "alice", "project_id": None})
    monkeypatch.setattr(recall_repo, "memory_candidates", lambda scope, terms: [dict(r) for r in rows])
    monkeypatch.setattr(recall_repo, "owner_embeddings", lambda owner, ids: {
        "cross-session-pref": "[1.0, 0.0]", "cross-session-noise": "[0.0, 1.0]",
    })
    monkeypatch.setattr(recall_repo, "mark_recalled", lambda owner, ids: marked.append((owner, ids)))
    monkeypatch.setattr(recall_service, "_query_embedding", lambda q: [1.0, 0.0])

    result = recall_service.recall("sa", "lysin brevity")
    ids = [m["id"] for m in result["memories"]]
    assert ids[0] == "same-session"
    assert ids.index("cross-session-pref") < ids.index("cross-session-noise")
    assert marked == [("alice", ids)]

    # Offline: embeddings unavailable -> clean keyword-only ranking, no errors.
    monkeypatch.setattr(recall_service, "_query_embedding", lambda q: None)
    offline = [m["id"] for m in recall_service.recall("sa", "lysin brevity")["memories"]]
    assert offline[0] == "same-session"
    assert offline.index("cross-session-noise") < offline.index("cross-session-pref")


def test_query_embedding_times_out_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    import app.services.embeddings as embeddings_module

    slow = SimpleNamespace(get_single_embedding=lambda _t: time.sleep(0.5) or [1.0])
    monkeypatch.setattr(embeddings_module, "get_embeddings_service", lambda: slow)
    monkeypatch.setattr(recall_service, "VECTOR_TIMEOUT_S", 0.05)
    assert recall_service._query_embedding("anything") is None


# --- 4. memory_operation tool ----------------------------------------------


async def test_memory_operation_tool_scoped_to_context_owner(env) -> None:
    alice = ToolContext(owner_id="alice", session_id="sa")
    bob = ToolContext(owner_id="bob")

    denied = await memory_operation_handler("list", tool_context=ToolContext())
    assert denied["success"] is False
    bad = await memory_operation_handler("drop_all", tool_context=alice)
    assert bad["success"] is False

    saved = await memory_operation_handler(
        "save", content="Alice wants figures as SVG", importance="high", tags=["preference"], tool_context=alice
    )
    assert saved["success"] is True
    row = _row(env.conn, saved["memory_id"])
    assert row["owner_id"] == "alice"
    assert {"agent_saved", "preference", "session:sa"} <= set(json.loads(row["tags"]))

    found = await memory_operation_handler("search", query="SVG", tool_context=alice)
    assert [i["id"] for i in found["items"]] == [saved["memory_id"]]
    assert (await memory_operation_handler("search", query="SVG", tool_context=bob))["items"] == []
    assert (await memory_operation_handler("list", tool_context=bob))["total"] == 0
    assert (await memory_operation_handler("list", tool_context=alice))["total"] == 1

    hijack = await memory_operation_handler("update", memory_id=saved["memory_id"], content="x", tool_context=bob)
    assert hijack["success"] is False
    assert (await memory_operation_handler("forget", memory_id=saved["memory_id"], tool_context=bob))["success"] is False
    assert (await memory_operation_handler("update", tool_context=alice))["success"] is False
    edited = await memory_operation_handler(
        "update", memory_id=saved["memory_id"], importance="critical", tool_context=alice
    )
    assert edited["item"]["importance"] == "critical"
    invalid = await memory_operation_handler(
        "update", memory_id=saved["memory_id"], memory_type="nope", tool_context=alice
    )
    assert invalid["success"] is False and "invalid argument" in invalid["error"]
    forgot = await memory_operation_handler("forget", memory_id=saved["memory_id"], tool_context=alice)
    assert forgot == {"success": True, "operation": "forget", "deleted_id": saved["memory_id"]}


def test_memory_operation_wiring_and_plan_mode() -> None:
    from app.routers.chat.request_routing import ALL_TOOLS
    from app.services.tool_schemas import EXECUTOR_AVAILABLE_TOOLS, build_tool_schemas
    from tool_box.native_tool_schemas import NATIVE_TOOL_CONTENT
    from tool_box.tool_registry import _STANDARD_TOOLS

    assert "memory_operation" in ALL_TOOLS
    assert "memory_operation" in EXECUTOR_AVAILABLE_TOOLS
    assert any(t["name"] == "memory_operation" for t in _STANDARD_TOOLS)
    schema = NATIVE_TOOL_CONTENT["memory_operation"]["parameters"]
    assert schema["properties"]["operation"]["enum"] == ["search", "save", "list", "update", "forget"]
    assert "memory_operation" in [s["function"]["name"] for s in build_tool_schemas(["memory_operation"])]

    assert plan_mode_tool_allowed("memory_operation", {"operation": "search"})
    assert plan_mode_tool_allowed("memory_operation", {"operation": "list"})
    for op in ("save", "update", "forget", ""):
        assert not plan_mode_tool_allowed("memory_operation", {"operation": op})
