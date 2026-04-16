"""Integration tests for action run lifecycle.

Validates: action run creation → status query → DB persistence.
"""

from __future__ import annotations

import pytest

from app.database_pool import get_db
from app.repository.chat_action_runs import create_action_run


_OWNER = "tester"
_HEADERS = {"X-Forwarded-User": _OWNER}


@pytest.mark.integration
def test_action_run_status_query(app_client_factory) -> None:
    """Create an action run in DB, then query its status via API."""
    run_id = "act_integration_test_001"

    with app_client_factory() as client:
        # Ensure session exists
        client.patch(
            "/chat/sessions/action-sess-001",
            json={"name": "Action Test"},
            headers=_HEADERS,
        )

        create_action_run(
            run_id=run_id,
            session_id="action-sess-001",
            owner_id=_OWNER,
            user_message="test action",
            mode="assistant",
            plan_id=None,
            context={},
            history=[],
            structured_json='{"llm_reply":{"message":"ok"},"actions":[]}',
        )

        resp = client.get(f"/chat/actions/{run_id}", headers=_HEADERS)
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["tracking_id"] == run_id
        assert payload["status"] == "pending"


@pytest.mark.integration
def test_action_run_persists_to_db(app_client_factory) -> None:
    """Verify action run record is persisted correctly in SQLite."""
    run_id = "act_integration_test_002"

    with app_client_factory() as client:
        client.patch(
            "/chat/sessions/action-sess-002",
            json={"name": "Persist Test"},
            headers=_HEADERS,
        )

        create_action_run(
            run_id=run_id,
            session_id="action-sess-002",
            owner_id=_OWNER,
            user_message="persist action test",
            mode="assistant",
            plan_id=None,
            context={"key": "value"},
            history=[],
            structured_json='{"llm_reply":{"message":"done"},"actions":[]}',
        )

        with get_db() as conn:
            row = conn.execute(
                "SELECT id, session_id, owner_id, user_message, status FROM chat_action_runs WHERE id = ?",
                (run_id,),
            ).fetchone()

        assert row is not None
        assert row["session_id"] == "action-sess-002"
        assert row["owner_id"] == _OWNER
        assert row["user_message"] == "persist action test"
        assert row["status"] == "pending"
