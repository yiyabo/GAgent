from __future__ import annotations

import pytest

from app.database_pool import get_db


@pytest.mark.integration
def test_local_auth_register_bootstrap_and_claim_legacy_resources(
    app_client_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("AUTH_OPEN_SIGNUP", "1")

    with app_client_factory() as client:
        with get_db() as conn:
            conn.execute("INSERT INTO plans (id, title, owner) VALUES (101, 'Legacy Plan', 'legacy-local')")
            conn.execute("INSERT INTO chat_sessions (id, owner_id, name) VALUES ('legacy-sess', 'legacy-local', 's')")
            conn.execute(
                """
                INSERT INTO chat_runs (run_id, session_id, owner_id, status)
                VALUES ('legacy-run', 'legacy-sess', 'legacy-local', 'queued')
                """
            )
            conn.execute(
                """
                INSERT INTO chat_action_runs (id, session_id, owner_id, user_message, structured_json, status)
                VALUES ('legacy-action', 'legacy-sess', 'legacy-local', 'hello', '{}', 'pending')
                """
            )
            conn.execute(
                """
                INSERT INTO plan_decomposition_job_index (job_id, plan_id, job_type, owner_id, session_id)
                VALUES ('legacy-job', 101, 'plan_decompose', 'legacy-local', 'legacy-sess')
                """
            )
            conn.commit()

        register_response = client.post(
            "/auth/register",
            json={"email": "Admin@Example.com", "password": "Password123"},
        )
        assert register_response.status_code == 200
        register_payload = register_response.json()
        assert register_payload["authenticated"] is True
        assert register_payload["user"]["email"] == "admin@example.com"
        assert register_payload["user"]["role"] == "admin"
        assert register_payload["user"]["auth_source"] == "session"
        user_id = register_payload["user"]["user_id"]

        me_response = client.get("/auth/me")
        assert me_response.status_code == 200
        me_payload = me_response.json()
        assert me_payload["authenticated"] is True
        assert me_payload["user"]["user_id"] == user_id
        assert me_payload["user"]["auth_source"] == "session"

        with get_db() as conn:
            row = conn.execute(
                """
                SELECT
                  (SELECT owner FROM plans WHERE id=101) AS plan_owner,
                  (SELECT owner_id FROM chat_sessions WHERE id='legacy-sess') AS session_owner,
                  (SELECT owner_id FROM chat_runs WHERE run_id='legacy-run') AS run_owner,
                  (SELECT owner_id FROM chat_action_runs WHERE id='legacy-action') AS action_owner,
                  (SELECT owner_id FROM plan_decomposition_job_index WHERE job_id='legacy-job') AS job_owner
                """
            ).fetchone()
        assert row is not None
        assert row["plan_owner"] == user_id
        assert row["session_owner"] == user_id
        assert row["run_owner"] == user_id
        assert row["action_owner"] == user_id
        assert row["job_owner"] == user_id

        second_response = client.post(
            "/auth/register",
            json={"email": "user@example.com", "password": "Password123"},
        )
        assert second_response.status_code == 200
        assert second_response.json()["user"]["role"] == "user"


@pytest.mark.integration
def test_local_auth_login_logout_change_password_and_protected_routes(
    app_client_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("AUTH_OPEN_SIGNUP", "1")

    with app_client_factory() as client:
        register_response = client.post(
            "/auth/register",
            json={"email": "alice@example.com", "password": "Password123"},
        )
        assert register_response.status_code == 200

        logout_response = client.post("/auth/logout")
        assert logout_response.status_code == 200

        unauthenticated_response = client.get("/chat/sessions")
        assert unauthenticated_response.status_code == 401

        bad_login = client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "wrong-password"},
        )
        assert bad_login.status_code == 401

        good_login = client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "Password123"},
        )
        assert good_login.status_code == 200
        assert good_login.json()["user"]["auth_source"] == "session"

        change_response = client.post(
            "/auth/change-password",
            json={"current_password": "Password123", "new_password": "Password456"},
        )
        assert change_response.status_code == 200

        client.post("/auth/logout")

        old_login = client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "Password123"},
        )
        assert old_login.status_code == 401

        new_login = client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "Password456"},
        )
        assert new_login.status_code == 200
        assert new_login.json()["user"]["auth_source"] == "session"


@pytest.mark.integration
def test_proxy_auth_me_exposes_proxy_auth_source(
    app_client_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_MODE", "proxy")

    with app_client_factory() as client:
        response = client.get(
            "/auth/me",
            headers={
                "X-Forwarded-User": "alice",
                "X-Forwarded-Email": "alice@example.com",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["authenticated"] is True
        assert payload["user"]["user_id"] == "alice"
        assert payload["user"]["auth_source"] == "proxy"
        assert payload["legacy_access_allowed"] is False


@pytest.mark.integration
def test_proxy_auth_me_exposes_legacy_access_when_proxy_headers_are_optional(
    app_client_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_MODE", "proxy")
    monkeypatch.delenv("PROXY_AUTH_REQUIRED", raising=False)

    with app_client_factory() as client:
        response = client.get("/auth/me")
        assert response.status_code == 200
        payload = response.json()
        assert payload["authenticated"] is False
        assert payload["user"] is None
        assert payload["legacy_access_allowed"] is True
