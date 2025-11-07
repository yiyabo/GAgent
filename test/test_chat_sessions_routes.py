from __future__ import annotations

import json
import uuid
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.repository.plan_repository import PlanRepository


@pytest.fixture()
def chat_client() -> TestClient:
    return TestClient(app)


def _clear_chat_tables() -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM chat_action_runs")
        conn.execute("DELETE FROM chat_sessions")
        conn.commit()


def _create_session(record: Dict[str, object]) -> None:
    with get_db() as conn:
        metadata = record.get("metadata")
        if isinstance(metadata, dict):
            metadata_json = json.dumps(metadata, ensure_ascii=False)
        else:
            metadata_json = metadata
        conn.execute(
            """
            INSERT INTO chat_sessions (
                id,
                name,
                metadata,
                plan_id,
                plan_title,
                current_task_id,
                current_task_name,
                last_message_at,
                created_at,
                updated_at,
                is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("id"),
                record.get("name"),
                metadata_json,
                record.get("plan_id"),
                record.get("plan_title"),
                record.get("current_task_id"),
                record.get("current_task_name"),
                record.get("last_message_at"),
                record.get("created_at"),
                record.get("updated_at"),
                record.get("is_active", 1),
            ),
        )
        conn.execute(
            """
            INSERT INTO chat_messages (session_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                record.get("id"),
                record.get("message_role", "assistant"),
                record.get("message_content", "hello"),
                record.get("last_message_at"),
            ),
        )
        conn.commit()


def test_list_sessions_empty(chat_client: TestClient):
    _clear_chat_tables()

    resp = chat_client.get("/chat/sessions")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["sessions"] == []
    assert payload["total"] == 0
    assert payload["offset"] == 0


def test_list_sessions_returns_plan_metadata(chat_client: TestClient, plan_repo: PlanRepository):
    _clear_chat_tables()

    plan = plan_repo.create_plan("Chat Session Plan")
    timestamp = "2024-05-01 12:00:00"
    session_id = uuid.uuid4().hex

    _create_session(
        {
            "id": session_id,
            "name": "Initial Session",
            "plan_id": plan.id,
            "plan_title": plan.title,
            "current_task_id": None,
            "current_task_name": None,
            "last_message_at": timestamp,
            "created_at": timestamp,
            "updated_at": timestamp,
            "message_role": "user",
            "message_content": "hi",
            "is_active": 1,
        }
    )

    resp = chat_client.get("/chat/sessions")
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["total"] >= 1
    sessions = payload["sessions"]
    assert any(s["id"] == session_id for s in sessions)

    target = next(s for s in sessions if s["id"] == session_id)
    assert target["plan_id"] == plan.id
    assert target["plan_title"] == plan.title
    assert target["last_message_at"] == timestamp
    assert target["is_active"] is True


def test_patch_session_updates_fields(chat_client: TestClient, plan_repo: PlanRepository):
    _clear_chat_tables()

    plan_a = plan_repo.create_plan("Plan Alpha")
    plan_b = plan_repo.create_plan("Plan Beta")
    session_id = uuid.uuid4().hex
    timestamp = "2024-06-10 08:30:00"

    _create_session(
        {
            "id": session_id,
            "name": "Session Alpha",
            "plan_id": plan_a.id,
            "plan_title": plan_a.title,
            "current_task_id": None,
            "current_task_name": None,
            "last_message_at": timestamp,
            "created_at": timestamp,
            "updated_at": timestamp,
            "is_active": 1,
        }
    )

    resp = chat_client.patch(
        f"/chat/sessions/{session_id}",
        json={
            "name": "Renamed Session",
            "is_active": False,
            "plan_id": plan_b.id,
            "current_task_id": 42,
            "current_task_name": "Prepare report",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed Session"
    assert body["is_active"] is False
    assert body["plan_id"] == plan_b.id
    assert body["plan_title"] == plan_b.title
    assert body["current_task_id"] == 42
    assert body["current_task_name"] == "Prepare report"

    # follow-up GET should reflect persisted changes
    verify = chat_client.get("/chat/sessions").json()
    target = next(s for s in verify["sessions"] if s["id"] == session_id)
    assert target["plan_id"] == plan_b.id
    assert target["plan_title"] == plan_b.title
    assert target["is_active"] is False


def test_delete_session_removes_records(chat_client: TestClient):
    _clear_chat_tables()

    session_id = uuid.uuid4().hex
    timestamp = "2024-07-01 10:00:00"
    _create_session(
        {
            "id": session_id,
            "name": "To Delete",
            "plan_id": None,
            "plan_title": None,
            "current_task_id": None,
            "current_task_name": None,
            "last_message_at": timestamp,
            "created_at": timestamp,
            "updated_at": timestamp,
            "message_role": "assistant",
            "message_content": "bye",
            "is_active": 1,
        }
    )

    resp = chat_client.delete(f"/chat/sessions/{session_id}")
    assert resp.status_code == 204

    with get_db() as conn:
        remaining_session = conn.execute(
            "SELECT COUNT(1) AS cnt FROM chat_sessions WHERE id=?", (session_id,)
        ).fetchone()
        remaining_messages = conn.execute(
            "SELECT COUNT(1) AS cnt FROM chat_messages WHERE session_id=?", (session_id,)
        ).fetchone()

    assert remaining_session["cnt"] == 0
    assert remaining_messages["cnt"] == 0


def test_delete_session_archive_flag(chat_client: TestClient):
    _clear_chat_tables()

    session_id = uuid.uuid4().hex
    timestamp = "2024-07-02 09:15:00"
    _create_session(
        {
            "id": session_id,
            "name": "Archive Me",
            "plan_id": None,
            "plan_title": None,
            "current_task_id": None,
            "current_task_name": None,
            "last_message_at": timestamp,
            "created_at": timestamp,
            "updated_at": timestamp,
            "message_role": "assistant",
            "message_content": "archive",
            "is_active": 1,
        }
    )

    resp = chat_client.delete(f"/chat/sessions/{session_id}?archive=true")
    assert resp.status_code == 204

    with get_db() as conn:
        row = conn.execute(
            "SELECT is_active FROM chat_sessions WHERE id=?", (session_id,)
        ).fetchone()
        message_row = conn.execute(
            "SELECT COUNT(1) AS cnt FROM chat_messages WHERE session_id=?", (session_id,)
        ).fetchone()

    assert row is not None
    assert row["is_active"] == 0
    assert message_row["cnt"] == 1


def test_delete_session_not_found(chat_client: TestClient):
    _clear_chat_tables()

    resp = chat_client.delete(f"/chat/sessions/{uuid.uuid4().hex}")
    assert resp.status_code == 404


def test_patch_session_settings_updates_default_provider(chat_client: TestClient):
    _clear_chat_tables()

    session_id = uuid.uuid4().hex
    timestamp = "2024-08-01 09:15:00"
    _create_session(
        {
            "id": session_id,
            "name": "Session Settings",
            "plan_id": None,
            "plan_title": None,
            "current_task_id": None,
            "current_task_name": None,
            "last_message_at": timestamp,
            "created_at": timestamp,
            "updated_at": timestamp,
            "is_active": 1,
            "metadata": {},
        }
    )

    resp = chat_client.patch(
        f"/chat/sessions/{session_id}",
        json={"settings": {"default_search_provider": "perplexity"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["default_search_provider"] == "perplexity"

    overview = chat_client.get("/chat/sessions").json()
    target = next(s for s in overview["sessions"] if s["id"] == session_id)
    assert target["settings"]["default_search_provider"] == "perplexity"

    resp_clear = chat_client.patch(
        f"/chat/sessions/{session_id}",
        json={"settings": {"default_search_provider": None}},
    )
    assert resp_clear.status_code == 200
    cleared = resp_clear.json()
    assert cleared["settings"] is None

    overview_after = chat_client.get("/chat/sessions").json()
    target_after = next(s for s in overview_after["sessions"] if s["id"] == session_id)
    assert target_after["settings"] is None


def test_patch_session_settings_rejects_invalid_provider(chat_client: TestClient):
    _clear_chat_tables()

    session_id = uuid.uuid4().hex
    timestamp = "2024-09-15 12:00:00"
    _create_session(
        {
            "id": session_id,
            "name": "Invalid Provider",
            "plan_id": None,
            "plan_title": None,
            "current_task_id": None,
            "current_task_name": None,
            "last_message_at": timestamp,
            "created_at": timestamp,
            "updated_at": timestamp,
            "is_active": 1,
        }
    )

    resp = chat_client.patch(
        f"/chat/sessions/{session_id}",
        json={"settings": {"default_search_provider": "bing"}},
    )
    assert resp.status_code == 422


def test_chat_status_endpoint(chat_client: TestClient):
    resp = chat_client.get("/chat/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in {"ready", "degraded"}
    assert "llm" in data and "provider" in data["llm"]
    assert "features" in data and "structured_actions" in data["features"]
