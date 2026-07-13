from __future__ import annotations

from app.database import init_db
from app.database_pool import get_db
from app.services.auth import (
    consume_sso_handoff,
    create_auth_session,
    create_sso_handoff,
    session_principal_from_session_id,
)


def _insert_user(user_id: str = "sso-user") -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO users (id, email, password_hash, role, is_active)
            VALUES (?, ?, 'unused', 'user', 1)
            """,
            (user_id, f"{user_id}@example.com"),
        )


def test_platform_handoff_is_single_use_and_restores_platform_principal(isolated_app_env) -> None:
    _ = isolated_app_env
    init_db()
    _insert_user()
    session = create_auth_session(
        "sso-user",
        access_mode="platform",
        platform_user_id=31,
        platform_project_id=12,
        platform_project_label="Trusted project",
    )

    handoff = create_sso_handoff(session["id"], ttl_seconds=120)

    assert consume_sso_handoff(handoff) == session["id"]
    assert consume_sso_handoff(handoff) is None
    resolved = session_principal_from_session_id(session["id"])
    assert resolved is not None
    principal, _ = resolved
    assert principal.auth_source == "sso"
    assert principal.access_mode == "platform"
    assert principal.platform_user_id == 31
    assert principal.platform_project_id == 12
    assert principal.platform_project_label == "Trusted project"


def test_legacy_session_defaults_to_local_access_mode(isolated_app_env) -> None:
    _ = isolated_app_env
    init_db()
    _insert_user("legacy-user")
    session = create_auth_session("legacy-user")

    resolved = session_principal_from_session_id(session["id"])

    assert resolved is not None
    principal, _ = resolved
    assert principal.access_mode == "local"
    assert principal.auth_source == "session"
    assert principal.platform_user_id is None
