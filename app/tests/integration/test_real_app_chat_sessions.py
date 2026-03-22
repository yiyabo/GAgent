from __future__ import annotations

import json

import pytest

from app.database_pool import get_db


@pytest.mark.integration
def test_real_app_chat_session_crud_persists_state(app_client_factory) -> None:
    session_id = "integration-session-001"

    with app_client_factory() as client:
        update_response = client.patch(
            f"/chat/sessions/{session_id}",
            json={
                "name": "Production Readiness Review",
                "current_task_id": 7,
                "current_task_name": "Validate deployment safety rails",
                "settings": {
                    "default_search_provider": "builtin",
                    "default_base_model": "qwen3.5-plus",
                    "default_llm_provider": "qwen",
                },
            },
        )
        assert update_response.status_code == 200
        payload = update_response.json()
        assert payload["id"] == session_id
        assert payload["name"] == "Production Readiness Review"
        assert payload["current_task_id"] == 7
        assert payload["current_task_name"] == "Validate deployment safety rails"
        assert payload["settings"]["default_search_provider"] == "builtin"
        assert payload["settings"]["default_base_model"] == "qwen3.5-plus"
        assert payload["settings"]["default_llm_provider"] == "qwen"

        with get_db() as conn:
            row = conn.execute(
                "SELECT name, is_active, metadata FROM chat_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
        assert row is not None
        assert row["name"] == "Production Readiness Review"
        assert bool(row["is_active"]) is True
        metadata = json.loads(row["metadata"])
        assert metadata["default_search_provider"] == "builtin"
        assert metadata["default_llm_provider"] == "qwen"

        head_response = client.head(f"/chat/sessions/{session_id}")
        assert head_response.status_code == 200

        list_response = client.get("/chat/sessions", params={"active": "true"})
        assert list_response.status_code == 200
        listed_ids = {item["id"] for item in list_response.json()["sessions"]}
        assert session_id in listed_ids

        archive_response = client.delete(
            f"/chat/sessions/{session_id}",
            params={"archive": "true"},
        )
        assert archive_response.status_code == 204

        archived_list = client.get("/chat/sessions", params={"active": "false"})
        assert archived_list.status_code == 200
        archived_items = {
            item["id"]: item for item in archived_list.json()["sessions"]
        }
        assert archived_items[session_id]["is_active"] is False

        delete_response = client.delete(f"/chat/sessions/{session_id}")
        assert delete_response.status_code == 204

        head_after_delete = client.head(f"/chat/sessions/{session_id}")
        assert head_after_delete.status_code == 404

        with get_db() as conn:
            deleted_row = conn.execute(
                "SELECT id FROM chat_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
        assert deleted_row is None
