from __future__ import annotations

import json

import pytest

from app.database_pool import get_db
from app.routers.chat.session_helpers import _ensure_session_exists, _set_session_plan_id


def _seed_plans(conn, *plan_ids: int) -> None:
    """chat_sessions.plan_id is a real FK (foreign_keys=ON): parents must exist."""
    for plan_id in plan_ids:
        conn.execute(
            "INSERT OR IGNORE INTO plans (id, title, owner) VALUES (?, ?, ?)",
            (plan_id, f"Plan {plan_id}", "plan-owner"),
        )


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
                    "default_base_model": "qwen3.7-max",
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
        assert payload["settings"]["default_base_model"] == "qwen3.7-max"
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


@pytest.mark.integration
def test_existing_unbound_session_becomes_visible_in_its_project(
    app_client_factory,
) -> None:
    session_id = "integration-session-project-backfill-001"
    owner_headers = {"X-Forwarded-User": "project-owner"}

    with app_client_factory() as client:
        client.patch(
            f"/chat/sessions/{session_id}",
            json={"name": "Unbound Project Session"},
            headers=owner_headers,
        ).raise_for_status()

        with get_db() as conn:
            _ensure_session_exists(
                session_id,
                conn,
                owner_id="project-owner",
                project_id=15,
            )
            conn.commit()
            row = conn.execute(
                "SELECT project_id FROM chat_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
        assert row is not None
        assert row["project_id"] == 15

        visible_sessions = client.get(
            "/chat/sessions",
            params={"project_id": 15},
            headers=owner_headers,
        )
        assert visible_sessions.status_code == 200
        assert session_id in {
            item["id"] for item in visible_sessions.json()["sessions"]
        }


@pytest.mark.integration
def test_existing_session_project_binding_is_not_overwritten(
    app_client_factory,
) -> None:
    session_id = "integration-session-project-isolation-001"

    with app_client_factory():
        with get_db() as conn:
            _ensure_session_exists(
                session_id,
                conn,
                owner_id="project-owner",
                project_id=15,
            )
            _ensure_session_exists(
                session_id,
                conn,
                owner_id="project-owner",
                project_id=16,
            )
            conn.commit()
            row = conn.execute(
                "SELECT project_id FROM chat_sessions WHERE id=?",
                (session_id,),
            ).fetchone()

    assert row is not None
    assert row["project_id"] == 15


@pytest.mark.integration
def test_existing_session_plan_binding_is_not_overwritten_by_request(
    app_client_factory,
) -> None:
    """One session, one plan: a request carrying another plan_id is ignored (§113)."""
    session_id = "integration-session-plan-sticky-001"

    with app_client_factory():
        with get_db() as conn:
            _seed_plans(conn, 15, 16)
            first = _ensure_session_exists(session_id, conn, 15, owner_id="plan-owner")
            second = _ensure_session_exists(session_id, conn, 16, owner_id="plan-owner")
            conn.commit()
            row = conn.execute(
                "SELECT plan_id FROM chat_sessions WHERE id=?",
                (session_id,),
            ).fetchone()

    assert first == 15
    assert second == 15
    assert row is not None
    assert row["plan_id"] == 15


@pytest.mark.integration
def test_unbound_session_binds_plan_on_first_request(app_client_factory) -> None:
    session_id = "integration-session-plan-firstbind-001"

    with app_client_factory():
        with get_db() as conn:
            _seed_plans(conn, 21)
            unbound = _ensure_session_exists(session_id, conn, owner_id="plan-owner")
            bound = _ensure_session_exists(session_id, conn, 21, owner_id="plan-owner")
            conn.commit()
            row = conn.execute(
                "SELECT plan_id FROM chat_sessions WHERE id=?",
                (session_id,),
            ).fetchone()

    assert unbound is None
    assert bound == 21
    assert row is not None
    assert row["plan_id"] == 21


@pytest.mark.integration
def test_explicit_lifecycle_rebind_still_replaces_plan(app_client_factory) -> None:
    """``_set_session_plan_id`` (plan created/replaced in-conversation) still rebinds."""
    session_id = "integration-session-plan-lifecycle-001"

    with app_client_factory():
        with get_db() as conn:
            _seed_plans(conn, 31, 32)
            _ensure_session_exists(session_id, conn, 31, owner_id="plan-owner")
            conn.commit()
        _set_session_plan_id(session_id, 32, owner_id="plan-owner")
        with get_db() as conn:
            row = conn.execute(
                "SELECT plan_id FROM chat_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            kept = _ensure_session_exists(session_id, conn, 31, owner_id="plan-owner")

    assert row is not None
    assert row["plan_id"] == 32
    assert kept == 32


@pytest.mark.integration
def test_real_app_chat_session_routes_are_owner_scoped(app_client_factory) -> None:
    session_id = "integration-session-owner-001"
    alice_headers = {"X-Forwarded-User": "alice"}
    bob_headers = {"X-Forwarded-User": "bob"}

    with app_client_factory() as client:
        create_response = client.patch(
            f"/chat/sessions/{session_id}",
            json={"name": "Alice Only Session"},
            headers=alice_headers,
        )
        assert create_response.status_code == 200

        with get_db() as conn:
            row = conn.execute(
                "SELECT owner_id FROM chat_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            conn.execute(
                "INSERT INTO chat_messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, "user", "owner-scoped message"),
            )
            conn.commit()
        assert row is not None
        assert row["owner_id"] == "alice"

        alice_list = client.get("/chat/sessions", headers=alice_headers)
        assert alice_list.status_code == 200
        alice_ids = {item["id"] for item in alice_list.json()["sessions"]}
        assert session_id in alice_ids

        bob_list = client.get("/chat/sessions", headers=bob_headers)
        assert bob_list.status_code == 200
        bob_ids = {item["id"] for item in bob_list.json()["sessions"]}
        assert session_id not in bob_ids

        alice_history = client.get(f"/chat/history/{session_id}", headers=alice_headers)
        assert alice_history.status_code == 200
        assert alice_history.json()["total"] == 1

        bob_history = client.get(f"/chat/history/{session_id}", headers=bob_headers)
        assert bob_history.status_code == 404

        bob_head = client.head(f"/chat/sessions/{session_id}", headers=bob_headers)
        assert bob_head.status_code == 404

        bob_update = client.patch(
            f"/chat/sessions/{session_id}",
            json={"name": "Bob Cannot Rename"},
            headers=bob_headers,
        )
        assert bob_update.status_code == 403

        bob_delete = client.delete(
            f"/chat/sessions/{session_id}",
            headers=bob_headers,
        )
        assert bob_delete.status_code == 404

        alice_delete = client.delete(
            f"/chat/sessions/{session_id}",
            headers=alice_headers,
        )
        assert alice_delete.status_code == 204


@pytest.mark.integration
def test_cancelled_chat_history_exposes_continuation_and_original_run_stays_terminal(app_client_factory, monkeypatch):
    from app.repository import chat_runs
    from app.routers.chat import run_routes
    from app.routers.chat.models import ChatRequest
    from app.services.execution.step_ledger import ControllerCheckpoint, StepLedger

    session_id = 'continuation-history'
    headers = {'X-Forwarded-User':'alice'}
    spawned = []
    monkeypatch.setattr(run_routes,'_spawn_chat_run_worker',spawned.append)
    monkeypatch.setattr(run_routes,'_save_run_user_message',lambda *args,**kwargs:None)
    with app_client_factory() as client:
        assert client.patch(f'/chat/sessions/{session_id}',json={'name':'Continue'},headers=headers).status_code == 200
        with get_db() as conn:
            user_id = conn.execute('INSERT INTO chat_messages(session_id,role,content) VALUES(?,?,?)',(session_id,'user','Write a report')).lastrowid
            conn.commit()
        request = ChatRequest(message='Write a report',session_id=session_id,client_message_id='original-report')
        chat_runs.create_chat_run('source-history',session_id,request.model_dump_json(),owner_id='alice',idempotency_key='original-report')
        assert chat_runs.claim_chat_run_lease('source-history','source-worker',ttl_seconds=300)
        assert chat_runs.mark_chat_run_started('source-history',worker_id='source-worker')
        chat_runs.set_chat_run_user_message_id('source-history',user_id)
        StepLedger('source-history',worker_id='source-worker').save_checkpoint(ControllerCheckpoint(run_id='source-history'))
        assert chat_runs.mark_chat_run_finished('source-history','cancelled',worker_id='source-worker')
        history = client.get(f'/chat/history/{session_id}',headers=headers)
        assert history.status_code == 200
        assert history.json()['messages'][0]['metadata']['resume_run_id'] == 'source-history'
        info = client.get('/chat/runs/source-history/resume',params={'session_id':session_id},headers=headers)
        assert info.status_code == 200 and info.json()['can_resume'] is True
        assert info.json()['message'] == 'Write a report' and 'request_json' not in info.json()
        child = client.post('/chat/runs/source-history/resume',json={'session_id':session_id,'client_message_id':'new-continuation','memory_enabled':False},headers=headers)
        assert child.status_code == 200
        child_id = child.json()['run_id']
        assert spawned == [child_id]
        assert chat_runs.get_chat_run('source-history')['status'] == 'cancelled'
        assert child.json()['resume_from_run_id'] == 'source-history'
