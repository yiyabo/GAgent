"""Session project re-bind: PATCH project_id validation + GET /chat/projects."""

from __future__ import annotations

import contextlib
import sqlite3
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

import pytest
from fastapi import HTTPException

import app.database as app_database
from app.routers.chat import routes as chat_routes
from app.routers.chat.models import ChatSessionUpdateRequest
from app.services.request_principal import RequestPrincipal


def _make_conn(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "main.sqlite"))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE chat_sessions (
            id TEXT PRIMARY KEY,
            owner_id TEXT,
            name TEXT,
            name_source TEXT,
            is_user_named INTEGER,
            metadata TEXT,
            plan_id INTEGER,
            plan_title TEXT,
            project_id INTEGER,
            current_task_id INTEGER,
            current_task_name TEXT,
            last_message_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            is_active INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO chat_sessions (
            id, owner_id, name, name_source, is_user_named, metadata,
            plan_id, plan_title, project_id, current_task_id, current_task_name,
            last_message_at, created_at, updated_at, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sess_move",
            "u1",
            "AML 数据分析",
            "default",
            0,
            None,
            None,
            None,
            103,
            None,
            None,
            "2026-09-29 05:22:01",
            "2026-09-29 04:34:18",
            "2026-09-29 05:22:02",
            1,
        ),
    )
    for row_values in (
        (
            "sess_in_88",
            "u1",
            "肺癌论文检索",
            "default",
            0,
            None,
            None,
            None,
            88,
            None,
            None,
            None,
            "2026-09-24 05:17:42",
            "2026-09-24 05:30:03",
            1,
        ),
        (
            "sess_other_owner",
            "u2",
            "别人的会话",
            "default",
            0,
            None,
            None,
            None,
            88,
            None,
            None,
            None,
            "2026-09-20 10:00:00",
            "2026-09-20 10:00:00",
            1,
        ),
    ):
        conn.execute(
            """
            INSERT INTO chat_sessions (
                id, owner_id, name, name_source, is_user_named, metadata,
                plan_id, plan_title, project_id, current_task_id, current_task_name,
                last_message_at, created_at, updated_at, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row_values,
        )
    conn.commit()
    return conn


def _platform_principal(project_id: int = 103, user_id: int = 30666) -> RequestPrincipal:
    return RequestPrincipal(
        user_id="u1",
        email="t@example.com",
        access_mode="platform",
        platform_user_id=user_id,
        platform_project_id=project_id,
    )


def _local_principal() -> RequestPrincipal:
    return RequestPrincipal(user_id="u1", email=None, access_mode="local")


class _StubPlatformClient:
    """get_project_context succeeds for allowed project ids, 403 otherwise."""

    def __init__(self, allowed: Dict[int, str]) -> None:
        self.allowed = allowed
        self.calls: List[Tuple[int, int]] = []

    async def get_project_context(self, platform_user_id: int, project_id: int):
        self.calls.append((platform_user_id, project_id))
        if project_id in self.allowed:
            return {"name": self.allowed[project_id], "data_roots": []}
        raise HTTPException(status_code=403, detail="Platform project lookup was denied")


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    conn = _make_conn(tmp_path)

    @contextlib.contextmanager
    def fake_get_db():
        yield conn

    monkeypatch.setattr(app_database, "get_db", fake_get_db)
    state: Dict[str, object] = {
        "principal": _platform_principal(),
        "platform_client": _StubPlatformClient({88: "肺癌数据", 103: "AML数据分析TEST-V2"}),
    }
    monkeypatch.setattr(
        chat_routes,
        "get_request_owner_id",
        lambda _req: state["principal"].owner_id,
    )
    monkeypatch.setattr(
        chat_routes,
        "get_request_principal",
        lambda _req: state["principal"],
    )
    monkeypatch.setattr(
        chat_routes,
        "get_platform_api_client",
        lambda: state["platform_client"],
    )
    yield state, conn
    conn.close()


def _project_of(conn: sqlite3.Connection, session_id: str) -> Optional[int]:
    row = conn.execute(
        "SELECT project_id FROM chat_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return row[0] if row else None


async def test_rebind_within_entry_project_needs_no_platform_call(env) -> None:
    state, conn = env
    payload = ChatSessionUpdateRequest(project_id=103)
    result = await chat_routes.update_chat_session("sess_move", payload, SimpleNamespace())
    assert _project_of(conn, "sess_move") == 103
    assert result.project_id == 103
    assert state["platform_client"].calls == []


async def test_rebind_to_other_project_validated_by_platform(env) -> None:
    state, conn = env
    payload = ChatSessionUpdateRequest(project_id=88)
    result = await chat_routes.update_chat_session("sess_move", payload, SimpleNamespace())
    assert _project_of(conn, "sess_move") == 88
    assert result.project_id == 88
    assert state["platform_client"].calls == [(30666, 88)]


async def test_rebind_rejected_when_platform_denies(env) -> None:
    _state, conn = env
    payload = ChatSessionUpdateRequest(project_id=999)
    with pytest.raises(HTTPException) as exc_info:
        await chat_routes.update_chat_session("sess_move", payload, SimpleNamespace())
    assert exc_info.value.status_code == 403
    assert _project_of(conn, "sess_move") == 103


async def test_unfile_sets_project_null(env) -> None:
    _state, conn = env
    payload = ChatSessionUpdateRequest(project_id=None)
    result = await chat_routes.update_chat_session("sess_move", payload, SimpleNamespace())
    assert _project_of(conn, "sess_move") is None
    assert result.project_id is None


async def test_local_mode_rebind_skips_platform_validation(env) -> None:
    state, conn = env
    state["principal"] = _local_principal()
    payload = ChatSessionUpdateRequest(project_id=42)
    result = await chat_routes.update_chat_session("sess_move", payload, SimpleNamespace())
    assert result.project_id == 42
    assert state["platform_client"].calls == []


async def test_rebind_other_owners_session_is_403(env) -> None:
    _state, _conn = env
    payload = ChatSessionUpdateRequest(project_id=88)
    with pytest.raises(HTTPException) as exc_info:
        await chat_routes.update_chat_session("sess_other_owner", payload, SimpleNamespace())
    assert exc_info.value.status_code == 403


async def test_list_projects_merges_entry_and_used_projects(env) -> None:
    _state, _conn = env
    result = await chat_routes.list_chat_projects(SimpleNamespace())
    by_id = {p.id: p for p in result.projects}
    assert set(by_id) == {88, 103}
    assert by_id[103].current is True
    assert by_id[88].current is False
    assert by_id[103].label == "AML数据分析TEST-V2"
    assert by_id[88].label == "肺癌数据"


async def test_list_projects_label_fallback_when_lookup_fails(env) -> None:
    state, _conn = env
    state["platform_client"] = _StubPlatformClient({})  # everything denied
    result = await chat_routes.list_chat_projects(SimpleNamespace())
    by_id = {p.id: p for p in result.projects}
    assert set(by_id) == {88, 103}
    assert by_id[88].label == "项目 88"
    assert by_id[103].label == "项目 103"
    assert by_id[103].current is True
