import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.database import get_db, init_db
from app.repository.plan_repository import PlanRepository
from app.services.request_principal import RequestPrincipal
from app.services.session_paths import get_runtime_session_dir


@pytest.fixture
def world(isolated_app_env, monkeypatch):
    import tool_box
    from app.routers.system_health_routes import router as system_router
    from app.routers.tool_routes import router as tools_router

    init_db()
    from app.repository.llm_usage import init_llm_usage_table

    init_llm_usage_table()
    with get_db() as conn:
        conn.executemany(
            "INSERT INTO chat_sessions (id, owner_id, name) VALUES (?, ?, ?)",
            [("session_alice", "alice", "Alice"), ("bob", "bob", "Bob")],
        )
        conn.execute(
            "INSERT INTO llm_usage_log (provider, model, total_tokens, session_id, created_at) "
            "VALUES ('synthetic', 'synthetic', 142, 'session_alice', CURRENT_TIMESTAMP)"
        )
    plan = PlanRepository().create_plan("Alice plan", owner="alice")
    own = get_runtime_session_dir("bob", create=True) / "uploads/own.fa"
    private = get_runtime_session_dir("session_alice", create=True) / "uploads/private.fa"
    own.write_text(">bob\nACGT\n")
    private.write_text(">alice\nALICE_PRIVATE\n")
    calls = []

    async def execute(name, **kwargs):
        calls.append((name, kwargs))
        return {"success": True, "session_id": kwargs.get("session_id")}

    monkeypatch.setattr(tool_box, "execute_tool", execute)
    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):
        user = request.headers.get("X-Test-User", "bob")
        request.state.principal = RequestPrincipal(
            user_id=user, email=None, role="admin" if user == "admin" else "user",
            is_authenticated=user != "anonymous",
        )
        return await call_next(request)

    app.include_router(tools_router)
    app.include_router(system_router)
    with TestClient(app) as client:
        yield client, calls, own, private, plan.id


@pytest.mark.parametrize("path, body", [
    ("/tools/url-fetch", {"url": "https://example.test/file"}),
    ("/tools/sequence-fetch", {"accession": "synthetic"}),
    ("/tools/bio-tools", {"tool_name": "seqkit", "operation": "stats"}),
])
def test_tool_sessions_are_owned_alias_safe_and_required(world, path, body):
    client, calls, *_ = world
    assert client.post(path, json={**body, "session_id": "session_alice"}).status_code == 403
    assert client.post(path, json={**body, "session_id": "alice"}).status_code == 403
    assert not calls
    assert client.post(path, json=body).status_code == 400
    assert client.post(path, json={**body, "session_id": "---"}).status_code == 400
    assert client.post(path, json={**body, "session_id": "bob"}).status_code == 200
    assert calls[-1][1]["session_id"] == "bob"
    assert client.post(path, json={**body, "session_id": "new-bob"}).status_code == 200
    with get_db() as conn:
        assert conn.execute("SELECT owner_id FROM chat_sessions WHERE id='new-bob'").fetchone()[0] == "bob"
        assert conn.execute("SELECT id FROM chat_sessions WHERE id='alice'").fetchone() is None
    assert client.post(path, headers={"X-Test-User": "anonymous"}, json={**body, "session_id": "bob"}).status_code == 401


def test_bio_input_output_and_parameter_paths_are_scoped(world):
    client, calls, own, private, _ = world
    body = {"session_id": "bob", "tool_name": "seqkit", "operation": "stats"}
    for extra in (
        {"input_file": str(private)},
        {"output_file": str(private)},
        {"params": {"reference": str(private)}},
        {"params": {"r1": [str(own), str(private)]}},
        {"params": {"r1": "../../session_alice/uploads/private.fa"}},
        {"input_file": "/etc/passwd"},
        {"input_file": str(own.parent.parent)},
    ):
        assert client.post("/tools/bio-tools", json={**body, **extra}).status_code == 403
    link = own.parent / "escape.fa"
    link.symlink_to(private)
    assert client.post("/tools/bio-tools", json={**body, "input_file": str(link)}).status_code == 403
    assert not calls
    assert client.post("/tools/bio-tools", json={**body, "input_file": str(own)}).status_code == 200
    assert calls[-1][1]["input_file"] == str(own)
    assert client.post("/tools/bio-tools", json={**body, "input_file": "uploads/own.fa"}).status_code == 200
    assert calls[-1][1]["input_file"] == str(own)


def test_bio_shared_reference_data_is_readable_but_not_an_output(world, isolated_app_env):
    client, calls, *_ = world
    reference = isolated_app_env["db_root"] / "bio_tools/reference.fa"
    reference.parent.mkdir()
    reference.write_text(">reference\nACGT\n")
    body = {"session_id": "bob", "tool_name": "seqkit", "operation": "stats"}
    assert client.post("/tools/bio-tools", json={**body, "input_file": str(reference)}).status_code == 200
    assert client.post("/tools/bio-tools", json={**body, "output_file": str(reference)}).status_code == 403
    assert client.post("/tools/bio-tools", json={**body, "params": {"output": str(reference)}}).status_code == 403


@pytest.mark.parametrize("tool, operation, params, output_name, expected_command", [
    ("blast", "makeblastdb", {"type": "nucl", "db": "mydb"}, "mydb", "makeblastdb -in"),
    ("seqkit", "grep", {"pattern": "/read1", "output": "hits.fa"}, "hits.fa", "-p /read1"),
])
def test_operation_parameter_roles_survive_native_rendering(
    world, monkeypatch, tool, operation, params, output_name, expected_command,
):
    import tool_box
    from tool_box.bio_tools.bio_tools_handler import get_tools_config
    from tool_box.bio_tools.native_exec import build_job

    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "native")
    client, _, own, _, _ = world
    work_dir = own.parent.parent / "native-run"
    work_dir.mkdir()

    async def render_native(name, **kwargs):
        assert name == "bio_tools"
        assert kwargs["params"] == params
        job = build_job(
            get_tools_config(), tool=kwargs["tool_name"], operation=kwargs["operation"],
            input_file=kwargs.get("input_file"), output_file=kwargs.get("output_file"),
            params=kwargs.get("params"), timeout=None, work_dir=str(work_dir),
            read_roots=[str(own.parent.parent)],
        )
        return {"success": True, "job": job}

    monkeypatch.setattr(tool_box, "execute_tool", render_native)
    response = client.post("/tools/bio-tools", json={
        "session_id": "bob", "tool_name": tool, "operation": operation,
        "input_file": str(own), "params": params,
    })
    assert response.status_code == 200, response.text
    job = response.json()["result"]["job"]
    assert expected_command in job["command"]
    assert str(own) in job["command"]
    assert job["output_path"] == str(work_dir / output_name)


@pytest.mark.parametrize("operation, params", [
    ("makeblastdb", {"type": "nucl", "db": "../escape"}),
    ("makeblastdb", {"type": "nucl", "db": "/etc/private"}),
    ("blastn", {"db": "/etc/private"}),
])
def test_operation_parameter_roles_preserve_path_boundaries(world, operation, params):
    client, calls, own, *_ = world
    response = client.post("/tools/bio-tools", json={
        "session_id": "bob", "tool_name": "blast", "operation": operation,
        "input_file": str(own), "params": params,
    })
    assert response.status_code == 403
    assert not calls


def test_all_backend_dependent_input_roles_are_covered():
    from tool_box.bio_tools.bio_tools_handler import get_tools_config
    from tool_box.bio_tools import native_exec

    differences = set()
    for tool, info in get_tools_config().items():
        for operation, op in info.get("operations", {}).items():
            docker = set(native_exec._WORK_PLACEHOLDER_RE.findall(op["command"]))
            native = set(native_exec._WORK_PLACEHOLDER_RE.findall(op.get("native_command") or op["command"]))
            for key in (docker ^ native) & native_exec.INPUT_PATH_PARAM_KEYS:
                differences.add((tool, operation, key))
    assert differences == {
        ("hmmer", "hmmpress", "db"), ("concoct", "bin", "coverage"), ("checkm", "qa", "tree"),
    }


@pytest.mark.parametrize("mode", ["native", "native_first"])
@pytest.mark.parametrize("tool, operation, path_key, other_params", [
    ("hmmer", "hmmpress", "db", {}),
    ("concoct", "bin", "coverage", {"output": "bins"}),
    ("checkm", "qa", "tree", {"output": "qa.tsv"}),
])
def test_backend_specific_inputs_reach_native_rendering(
    world, monkeypatch, mode, tool, operation, path_key, other_params,
):
    import tool_box
    from tool_box.bio_tools.bio_tools_handler import get_tools_config
    from tool_box.bio_tools.native_exec import build_job

    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", mode)
    client, _, own, private, _ = world
    work_dir = own.parent.parent / "native-run"
    work_dir.mkdir()
    params = {**other_params, path_key: "uploads/own.fa"}
    if tool == "concoct":
        params["contigs"] = "uploads/own.fa"
    if tool == "checkm":
        params["input"] = "uploads/own.fa"

    async def render_native(name, **kwargs):
        assert kwargs["params"][path_key] == str(own)
        job = build_job(
            get_tools_config(), tool=tool, operation=operation,
            input_file=kwargs.get("input_file"), output_file=None, params=kwargs["params"],
            timeout=None, work_dir=str(work_dir), read_roots=[str(own.parent.parent)],
            database_path=str(own.parent),
        )
        return {"success": True, "job": job}

    monkeypatch.setattr(tool_box, "execute_tool", render_native)
    body = {"session_id": "bob", "tool_name": tool, "operation": operation, "input_file": str(own)}
    response = client.post("/tools/bio-tools", json={**body, "params": params})
    assert response.status_code == 200, response.text
    assert str(own) in response.json()["result"]["job"]["command"]
    for forbidden in (str(private), "../session_alice/uploads/private.fa"):
        response = client.post("/tools/bio-tools", json={**body, "params": {**params, path_key: forbidden}})
        assert response.status_code == 403


def test_docker_work_relative_parameter_reaches_docker_rendering(world, monkeypatch):
    import tool_box
    from tool_box.bio_tools.bio_tools_handler import _build_docker_command_args

    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "local")
    client, _, own, private, _ = world
    work_dir = own.parent.parent / "docker-run"
    work_dir.mkdir()

    async def render_docker(name, **kwargs):
        assert kwargs["params"]["db"] == "uploads/own.fa"
        command = _build_docker_command_args(
            "hmmer", "hmmpress", extra_params=kwargs["params"], work_dir_override=str(work_dir),
            validate_database=False,
        )
        return {"success": True, "command": command}

    monkeypatch.setattr(tool_box, "execute_tool", render_docker)
    body = {"session_id": "bob", "tool_name": "hmmer", "operation": "hmmpress"}
    response = client.post("/tools/bio-tools", json={**body, "params": {"db": "uploads/own.fa"}})
    assert response.status_code == 200, response.text
    assert "/work/uploads/own.fa" in response.json()["result"]["command"]
    assert client.post("/tools/bio-tools", json={**body, "params": {"db": str(private)}}).status_code == 403


@pytest.mark.parametrize("owner, session_id, allowed", [
    ("alice", "session_alice", False), ("bob", "bob", True),
    ("legacy-local", "bob", True), ("legacy-local", None, False),
])
def test_bio_jobs_are_scoped_on_both_http_surfaces(world, owner, session_id, allowed):
    from app.services.plans.decomposition_jobs import plan_decomposition_jobs

    client, calls, *_ = world
    job = plan_decomposition_jobs.create_job(
        plan_id=None, task_id=None, mode="bio_tools_background", job_type="bio_tools_run",
        owner_id=owner, session_id=session_id,
    )
    expected = 200 if allowed else 403
    assert client.get(f"/tools/bio-tools/jobs/{job.job_id}").status_code == expected
    assert client.post("/tools/bio-tools", json={
        "session_id": "bob", "tool_name": "job", "operation": "job_status",
        "params": {"job_id": job.job_id},
    }).status_code == expected
    assert bool(calls) == allowed
    assert client.get("/tools/bio-tools/jobs/missing").status_code == 404


def test_http_background_jobs_persist_owner_binding(world, monkeypatch):
    import tool_box
    from app.repository.plan_storage import load_decomposition_job
    from app.services.plans.decomposition_jobs import plan_decomposition_jobs

    client, *_ = world
    job = plan_decomposition_jobs.create_job(
        plan_id=None, task_id=None, mode="bio_tools_background", job_type="bio_tools_run",
    )

    async def execute(name, **kwargs):
        return {"success": True, "background": True, "job_id": job.job_id}

    monkeypatch.setattr(tool_box, "execute_tool", execute)
    assert client.post("/tools/bio-tools", json={
        "session_id": "bob", "tool_name": "seqkit", "operation": "stats", "background": True,
    }).status_code == 200
    assert job.owner_id == "bob"
    assert job.session_id == "bob"
    persisted = load_decomposition_job(None, job.job_id)
    assert persisted["owner_id"] == "bob"
    assert persisted["session_id"] == "bob"


def test_system_usage_and_maintenance_authorization(world):
    client, _, _, _, plan_id = world
    paths = ["/system/stats/token-usage/session/session_alice", f"/system/stats/token-usage/plan/{plan_id}"]
    for path in paths:
        assert client.get(path).status_code == 403
        assert client.get(path, headers={"X-Test-User": "alice"}).status_code == 200
        assert client.get(path, headers={"X-Test-User": "admin"}).status_code == 200
    assert client.get("/system/stats/token-usage/session/missing").status_code == 404
    assert client.get("/system/stats/token-usage/plan/999999").status_code == 404
    assert client.get("/system/stats/token-usage").status_code == 403
    assert client.get("/system/stats/token-usage", headers={"X-Test-User": "admin"}).status_code == 200
    assert client.post("/system/maintenance/optimize").status_code == 403
    assert client.post("/system/maintenance/optimize", headers={"X-Test-User": "admin"}).status_code == 200
