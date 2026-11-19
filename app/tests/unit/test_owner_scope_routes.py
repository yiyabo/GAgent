import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.database import get_db
from app.database_pool import initialize_connection_pool
from app.routers import artifact_routes, job_routes, plan_routes, project_routes, upload_routes
from app.routers.chat import confirmation, routes
from app.services.request_principal import RequestPrincipal


@pytest.fixture
def scoped_client(monkeypatch, tmp_path, isolated_app_env):
    initialize_connection_pool(db_path=str(tmp_path / "scope.db"), pool_size=1, max_overflow=1)
    with get_db() as conn:
        conn.execute("""CREATE TABLE chat_sessions (
            id TEXT PRIMARY KEY, owner_id TEXT, name TEXT, name_source TEXT,
            is_user_named INTEGER, metadata TEXT, last_message_at TEXT,
            created_at TEXT, updated_at TEXT, is_active INTEGER)""")
        conn.executemany("INSERT INTO chat_sessions(id,owner_id) VALUES (?,?)", [
            ("session_victim", "alice"), ("bob-session", "bob"), ("unknown-owner", None)
        ])
        conn.execute("CREATE TABLE plans(id INTEGER PRIMARY KEY, owner TEXT)")
        conn.execute("INSERT INTO plans VALUES (42,'alice')")
        conn.commit()
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("APP_INFO_SESSIONS_ROOT", str(tmp_path / "legacy"))
    monkeypatch.setattr(artifact_routes, "_workspace_root", lambda: tmp_path)
    victim = tmp_path / "runtime" / "session_victim"
    (victim / "uploads").mkdir(parents=True)
    (victim / "uploads" / "secret.txt").write_text("alice secret")
    (victim / "private.png").write_bytes(b"alice image")
    (tmp_path / "source.txt").write_text("project file")

    async def project_context(request, project_id):
        return {"id": project_id, "data_roots": [{"path": str(tmp_path)}]}

    monkeypatch.setattr(project_routes, "_project_context_for_request", project_context)
    monkeypatch.setattr(job_routes, "_CLAUDE_LOG_DIR", tmp_path)
    (tmp_path / "alice-job.log").write_text("alice log")
    monkeypatch.setattr(job_routes.plan_decomposition_jobs, "get_job_payload",
                        lambda job_id, **kwargs: {"job_id": job_id, "owner_id": "alice", "status": "running"}
                        if job_id == "alice-job" else None)
    monkeypatch.setattr(plan_routes, "_list_plan_execute_job_ids", lambda *args, **kwargs: ["alice-job"])
    confirmation._pending_confirmations.clear()
    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):
        request.state.principal = RequestPrincipal(user_id=request.headers.get("x-user", "bob"), email=None)
        return await call_next(request)

    for router in (artifact_routes.router, upload_routes.router, project_routes.router,
                   routes.router, job_routes.job_router, plan_routes.plan_router):
        app.include_router(router)
    with TestClient(app) as client:
        yield client, tmp_path
    confirmation._pending_confirmations.clear()


@pytest.mark.parametrize("alias", ["victim", "session-session_victim", "victim!"])
def test_session_alias_cannot_claim_another_owners_storage(scoped_client, alias):
    client, root = scoped_client
    assert client.get("/upload/list", params={"session_id": alias}).status_code == 403
    with get_db() as conn:
        assert conn.execute("SELECT id FROM chat_sessions WHERE id=?", (alias,)).fetchone() is None
    assert client.get("/upload/list", params={"session_id": "session_victim"}, headers={"x-user": "alice"}).status_code == 200
    assert client.get("/artifacts/sessions/session_victim/file", params={"path": "uploads/secret.txt"}).status_code == 403
    assert client.get("/artifacts/sessions/session_victim/file", params={"path": "uploads/secret.txt"}, headers={"x-user": "alice"}).status_code == 200
    assert (root / "runtime/session_victim/uploads/secret.txt").read_text() == "alice secret"


def test_existing_alias_cannot_read_or_delete_victim_storage(scoped_client):
    client, root = scoped_client
    with get_db() as conn:
        conn.execute("INSERT INTO chat_sessions(id,owner_id) VALUES ('victim','bob')")
        conn.commit()
    assert client.get("/artifacts/sessions/victim/file", params={"path": "uploads/secret.txt"}).status_code == 403
    assert client.post("/upload/clear", data={"session_id": "victim"}).status_code == 403
    assert client.delete("/chat/sessions/victim").status_code == 403
    assert (root / "runtime/session_victim/uploads/secret.txt").exists()


def test_workspace_image_is_session_scoped(scoped_client):
    client, root = scoped_client
    params = {"path": "runtime/session_victim/private.png"}
    assert client.get("/artifacts/sessions/bob-session/workspace-file", params=params).status_code == 403
    response = client.get("/artifacts/sessions/session_victim/workspace-file", params=params, headers={"x-user": "alice"})
    assert response.status_code == 200 and response.content == b"alice image"
    (root / "shared.png").write_bytes(b"shared image")
    assert client.get("/artifacts/sessions/bob-session/workspace-file", params={"path": "shared.png"}).status_code == 200
    assert client.get("/artifacts/sessions/missing/workspace-file", params=params).status_code == 404


def test_project_selection_checks_destination_owner_and_alias(scoped_client):
    client, root = scoped_client
    payload = {"project_id": 9, "selected_paths": ["source.txt"], "session_id": "session_victim"}
    assert client.post("/project/9/select-files", json=payload).status_code == 403
    payload["session_id"] = "victim"
    assert client.post("/project/9/select-files", json=payload).status_code == 403
    payload["session_id"] = "session_victim"
    assert client.post("/project/9/select-files", json=payload, headers={"x-user": "alice"}).status_code == 200
    assert (root / "runtime/session_victim/uploads/source.txt").read_text() == "project file"
    payload["session_id"] = "new-session"
    assert client.post("/project/9/select-files", json=payload).status_code == 200


@pytest.mark.parametrize("confirmed", [False, True])
def test_confirmations_check_owner_before_consuming(scoped_client, monkeypatch, confirmed):
    client, _ = scoped_client
    confirmation._store_pending_confirmation("confirm_test", "session_victim", [], None)
    monkeypatch.setattr(routes, "_execute_confirmed_actions", lambda **kwargs: None)
    assert client.get("/chat/confirm/confirm_test").status_code == 403
    payload = {"confirmation_id": "confirm_test", "confirmed": confirmed}
    assert client.post("/chat/confirm", json=payload).status_code == 403
    assert confirmation._get_pending_confirmation("confirm_test") is not None
    assert client.get("/chat/confirm/confirm_test", headers={"x-user": "alice"}).status_code == 200
    assert client.post("/chat/confirm", json=payload, headers={"x-user": "alice"}).status_code == 200
    assert confirmation._get_pending_confirmation("confirm_test") is None


@pytest.mark.parametrize("session_id", ["missing", "unknown-owner"])
def test_confirmation_requires_known_session_owner(scoped_client, session_id):
    client, _ = scoped_client
    confirmation._store_pending_confirmation("confirm_unknown", session_id, [], None)
    assert client.get("/chat/confirm/confirm_unknown").status_code == 404
    assert client.post("/chat/confirm", json={"confirmation_id": "confirm_unknown", "confirmed": False}).status_code == 404
    assert client.get("/chat/confirm/missing").status_code == 404


def test_job_logs_require_job_owner(scoped_client):
    client, _ = scoped_client
    assert client.get("/jobs/alice-job/logs").status_code == 403
    response = client.get("/jobs/alice-job/logs", headers={"x-user": "alice"})
    assert response.status_code == 200 and response.json()["lines"] == ["alice log"]
    assert client.get("/jobs/missing/logs").status_code == 404


def test_active_job_requires_plan_owner(scoped_client):
    client, _ = scoped_client
    assert client.get("/plans/42/active-job").status_code == 403
    response = client.get("/plans/42/active-job", headers={"x-user": "alice"})
    assert response.status_code == 200 and response.json()["job_id"] == "alice-job"
    assert client.get("/plans/999/active-job").status_code == 404
