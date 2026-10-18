"""Extensions API: catalog integrity, admin gating, persisted state, real mounts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import extensions_routes
from app.services.extensions import service, skills_view, state
from app.services.extensions.service import load_catalog
from app.services.request_principal import RequestPrincipal, make_legacy_principal
from tool_box.mcp_mount import MountManager, manager as manager_module, parse_entry

REPO = Path(__file__).resolve().parents[3]


# ------------------------------------------------------------------ catalog
def test_every_catalog_server_is_a_valid_pinned_container_mount():
    catalog = load_catalog()
    assert {"fetch", "uniprot"} <= set(catalog)
    for sid, server in catalog.items():
        spec = server.spec()  # parse_entry validates everything, including the pinned image
        assert spec.isolation == "container", sid
        assert spec.read_only_tools == tuple(t["name"] for t in server.raw["tools"] if t["read_only"])
        assert not spec.env.keys() & {"GAGENT_MCP_TOKEN", "GAGENT_MCP_ENDPOINT"}


def test_catalog_never_claims_a_human_reviewed_anything():
    for server in load_catalog().values():
        verification = server.raw["verification"]
        assert verification["human_signoff"] is False
        assert verification["status"] == "verified"
        assert all(check["detail"] for check in verification["checks"])


def test_catalog_images_match_the_dockerfiles_and_the_proxy_policy():
    from app.ops.docker_policy import load_catalog_images, load_policy_from_env, image_allowed

    for sid, server in load_catalog().items():
        image = server.raw["mount"]["container"]["image"]
        assert (REPO / "deploy" / "mcp" / sid / "Dockerfile").is_file()
        dockerfile = (REPO / "deploy" / "mcp" / sid / "Dockerfile").read_text()
        assert "@sha256:" in dockerfile.split("FROM", 1)[1].splitlines()[0], "base image must be digest-pinned"
        assert image in load_catalog_images(str(service.CATALOG_PATH))
    assert image_allowed("gagent-mcp-fetch:latest", load_policy_from_env()) is False


# -------------------------------------------------------------------- state
def test_state_round_trip_and_garbage_tolerance(tmp_path, monkeypatch):
    path = tmp_path / "s.json"
    monkeypatch.setenv(state.STATE_ENV, str(path))
    assert state.read_state() == {"mcp": {}, "skills": {}}
    state.write_state({"mcp": {"fetch": {"enabled": True}}, "skills": {"x": {"enabled": False}}})
    assert state.read_state()["mcp"]["fetch"]["enabled"] is True
    assert state.disabled_skill_names() == {"x"}
    path.write_text("{ garbage")
    assert state.read_state() == {"mcp": {}, "skills": {}}
    path.write_text(json.dumps([1, 2]))
    assert state.read_state() == {"mcp": {}, "skills": {}}
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".extensions-")]


# ------------------------------------------------------------------- routes
def _client(monkeypatch, tmp_path, principal):
    monkeypatch.setenv(state.STATE_ENV, str(tmp_path / "state.json"))
    app = FastAPI()
    app.include_router(extensions_routes.router)

    @app.middleware("http")
    async def inject(request, call_next):
        request.state.principal = principal
        return await call_next(request)

    return TestClient(app)


ADMIN = RequestPrincipal(user_id="u-admin", email="a@x", role="admin")
USER = RequestPrincipal(user_id="u-user", email="u@x", role="user")


def test_everyone_signed_in_can_read_but_only_admins_can_manage(monkeypatch, tmp_path):
    monkeypatch.delenv(service.ENABLE_ENV, raising=False)
    with _client(monkeypatch, tmp_path, USER) as c:
        body = c.get("/extensions/mcp").json()
        assert body["can_manage"] is False and body["configured"] is False
        assert {s["id"] for s in body["servers"]} >= {"fetch", "uniprot"}
        assert c.post("/extensions/mcp/fetch/enable").status_code == 403
        assert c.post("/extensions/skills/xlsx/disable").status_code == 403
        assert c.get("/extensions/skills").json()["can_manage"] is False
    with _client(monkeypatch, tmp_path, ADMIN) as c:
        assert c.get("/extensions/mcp").json()["can_manage"] is True


def test_anonymous_and_legacy_principals_can_read_but_never_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_MODE", "proxy")
    monkeypatch.setenv("PROXY_AUTH_REQUIRED", "0")
    from app.services.foundation.settings import get_settings

    get_settings.cache_clear()
    with _client(monkeypatch, tmp_path, make_legacy_principal()) as c:
        assert c.get("/extensions/mcp").status_code == 200
        assert c.get("/extensions/mcp").json()["can_manage"] is False
        assert c.post("/extensions/mcp/fetch/enable").status_code == 403
    get_settings.cache_clear()


def test_enable_is_refused_when_the_feature_is_off_or_the_id_is_unknown(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, ADMIN) as c:
        monkeypatch.delenv(service.ENABLE_ENV, raising=False)
        assert c.post("/extensions/mcp/fetch/enable").status_code == 409
        monkeypatch.setenv(service.ENABLE_ENV, "1")
        assert c.post("/extensions/mcp/does-not-exist/enable").status_code == 404
        assert c.post("/extensions/mcp/..%2Fetc/enable").status_code in {404, 422}


def test_api_cannot_be_used_to_mount_arbitrary_servers(monkeypatch, tmp_path):
    monkeypatch.setenv(service.ENABLE_ENV, "1")
    with _client(monkeypatch, tmp_path, ADMIN) as c:
        response = c.post("/extensions/mcp/fetch/enable", json={"command": "rm", "args": ["-rf", "/"], "container": None})
        assert response.status_code in {200, 409}  # body is ignored; only catalog entries exist
        assert c.post("/extensions/mcp", json={"command": "x"}).status_code in {404, 405}


def test_enable_reports_missing_image_instead_of_failing_opaquely(monkeypatch, tmp_path):
    monkeypatch.setenv(service.ENABLE_ENV, "1")

    async def no_image(_image):
        return False

    monkeypatch.setattr(service, "_image_present", no_image)
    monkeypatch.setattr(service.shutil, "which", lambda _n: "/usr/bin/docker")
    with _client(monkeypatch, tmp_path, ADMIN) as c:
        response = c.post("/extensions/mcp/fetch/enable")
        assert response.status_code == 409 and "镜像" in response.json()["detail"]
        listed = {s["id"]: s for s in c.get("/extensions/mcp").json()["servers"]}
        assert listed["fetch"]["state"] == "unavailable" and listed["fetch"]["unavailable_reason"]


# ------------------------------------------------------------- real mounting
_FAKE = '''
from mcp.server import MCPServer
mcp = MCPServer("fake")

@mcp.tool()
def echo(text: str) -> str:
    """Echo."""
    return text

if __name__ == "__main__":
    mcp.run()
'''


@pytest.fixture
def host_catalog(tmp_path, monkeypatch):
    """A catalog entry that runs a real stdio server on the host (no Docker needed)."""
    import sys

    script = tmp_path / "fake.py"
    script.write_text(_FAKE)
    catalog = {
        "schema": 1,
        "servers": {
            "fakesrv": {
                "title": "Fake", "description": "d", "category": "utility", "homepage": "https://x.example", "license": "MIT", "version": "1",
                "source": {"repo": "", "commit": "", "package": ""},
                "mount": {"transport": "stdio", "command": sys.executable, "args": [str(script)], "timeout_sec": 30},
                "tools": [{"name": "echo", "read_only": True}],
                "verification": {"status": "verified", "reviewed_at": "", "reviewer": "", "human_signoff": False, "checks": []},
            }
        },
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    monkeypatch.setattr(service, "CATALOG_PATH", path)
    original = service.load_catalog
    monkeypatch.setattr(service, "load_catalog", lambda p=path: original(p))
    monkeypatch.setenv(service.ENABLE_ENV, "1")
    monkeypatch.setenv(state.STATE_ENV, str(tmp_path / "state.json"))
    monkeypatch.setattr(manager_module, "_manager", MountManager())
    return path


async def test_enable_disable_mounts_and_unmounts_a_real_server_and_persists(host_catalog):
    from tool_box.tools import get_tool_registry

    shown = await service.set_mcp_enabled("fakesrv", True)
    assert shown["state"] == "enabled" and shown["connected"] and shown["live_tools"] == ["mcp__fakesrv__echo"]
    assert get_tool_registry().get_tool("mcp__fakesrv__echo") is not None
    assert state.read_state()["mcp"]["fakesrv"]["enabled"] is True

    # a restart: nothing mounted yet, then the saved choice is restored
    await manager_module.get_mount_manager().shutdown()
    assert get_tool_registry().get_tool("mcp__fakesrv__echo") is None
    await service.restore_enabled_servers()
    assert get_tool_registry().get_tool("mcp__fakesrv__echo") is not None

    shown = await service.set_mcp_enabled("fakesrv", False)
    assert shown["state"] == "disabled" and not shown["connected"]
    assert get_tool_registry().get_tool("mcp__fakesrv__echo") is None
    await manager_module.get_mount_manager().shutdown()


async def test_server_that_fails_to_start_is_rolled_back_with_a_reason(host_catalog, tmp_path):
    data = json.loads(host_catalog.read_text())
    data["servers"]["fakesrv"]["mount"]["command"] = "/nonexistent/binary"
    host_catalog.write_text(json.dumps(data))
    with pytest.raises(service.ExtensionError) as err:
        await service.set_mcp_enabled("fakesrv", True)
    assert err.value.status == 409
    assert "fakesrv" not in state.read_state()["mcp"]
    assert manager_module.get_mount_manager().tool_names() == []


async def test_hand_written_mounts_and_catalog_mounts_coexist_and_survive_reload(host_catalog, tmp_path):
    import sys

    script = tmp_path / "fake.py"
    mounts = tmp_path / "mounts.json"
    mounts.write_text(json.dumps({"servers": {"hand": {"transport": "stdio", "command": sys.executable, "args": [str(script)]}}}))
    manager = manager_module.get_mount_manager()
    await manager.load(str(mounts))
    await service.set_mcp_enabled("fakesrv", True)
    assert {"mcp__hand__echo", "mcp__fakesrv__echo"} <= set(manager.tool_names())
    await manager.load(str(mounts))  # a file reload must not drop the catalog server
    assert "mcp__fakesrv__echo" in manager.tool_names()
    listed = await service.list_mcp(can_manage=True)
    assert [c["name"] for c in listed["custom"]] == ["hand"]
    await manager.shutdown()


async def test_a_hand_written_entry_cannot_shadow_a_catalog_server(host_catalog, tmp_path):
    import sys

    mounts = tmp_path / "mounts.json"
    mounts.write_text(json.dumps({"servers": {"fakesrv": {"transport": "stdio", "command": sys.executable, "args": ["-c", "print(1)"]}}}))
    manager = manager_module.get_mount_manager()
    await manager.load(str(mounts))
    await service.set_mcp_enabled("fakesrv", True)
    status = {row["server"]: row for row in manager.status() if "managed" in row}
    assert status["fakesrv"]["managed"] is True and status["fakesrv"]["connected"] is True
    await manager.shutdown()


# ------------------------------------------------------------------- skills
def _skills_dir(tmp_path, names=("alpha", "beta")):
    root = tmp_path / "skills"
    for name in names:
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Skill {name}.\n---\n# {name}\n")
    (root / "CATALOG.json").write_text(json.dumps({"schema": 1, "skills": {"alpha": {"origin": {"kind": "third_party", "source": "acme/skills", "license": "MIT", "commit": "abc"}, "modified": True, "modifications": ["trimmed"], "review": {"scanner": "passed", "reviewed_by": "ai", "reviewed_at": "2026-10-18", "human_signoff": False, "notes": ""}}}}))
    return root


def test_skills_listing_merges_catalog_provenance_and_marks_unknown_ones(tmp_path, monkeypatch):
    monkeypatch.setenv(state.STATE_ENV, str(tmp_path / "state.json"))
    root = _skills_dir(tmp_path)
    rows = {r["name"]: r for r in skills_view.list_skills(can_manage=True, skills_dir=root)["skills"]}
    assert rows["alpha"]["origin"]["kind"] == "third_party" and rows["alpha"]["origin"]["modified"] is True
    assert rows["alpha"]["review"]["human_signoff"] is False
    assert rows["beta"]["review"]["scanner"] in {"n/a", "passed", "warnings"}
    assert rows["beta"]["origin"]["source"] == "unknown"
    assert all(r["enabled"] for r in rows.values())


def test_disabling_a_skill_hides_it_from_the_agent_but_not_from_the_page(tmp_path, monkeypatch):
    from app.services.skills.skills_loader import SkillsLoader

    monkeypatch.setenv(state.STATE_ENV, str(tmp_path / "state.json"))
    root = _skills_dir(tmp_path)
    row = skills_view.set_skill_enabled("alpha", False, skills_dir=root)
    assert row["enabled"] is False
    loader = SkillsLoader(skills_dir=str(tmp_path / "none"), project_skills_dir=str(root))
    assert {s["name"] for s in loader.list_skills()} == {"beta"}
    page = {r["name"]: r for r in skills_view.list_skills(can_manage=True, skills_dir=root)["skills"]}
    assert page["alpha"]["enabled"] is False and page["beta"]["enabled"] is True
    assert page["alpha"]["description"] == "Skill alpha."  # a disabled skill keeps its description on the page
    assert skills_view.set_skill_enabled("alpha", True, skills_dir=root)["enabled"] is True
    assert {s["name"] for s in SkillsLoader(skills_dir=str(tmp_path / "none"), project_skills_dir=str(root)).list_skills()} == {"alpha", "beta"}


@pytest.mark.parametrize("name", ["nope", "../etc", ".hidden", "a/b", ""])
def test_unknown_or_traversing_skill_names_are_404(tmp_path, monkeypatch, name):
    monkeypatch.setenv(state.STATE_ENV, str(tmp_path / "state.json"))
    with pytest.raises(service.ExtensionError) as err:
        skills_view.set_skill_enabled(name, False, skills_dir=_skills_dir(tmp_path))
    assert err.value.status == 404


def test_a_blocked_skill_cannot_be_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv(state.STATE_ENV, str(tmp_path / "state.json"))
    root = _skills_dir(tmp_path)
    monkeypatch.setattr(skills_view, "_scan", lambda _d: {"status": "blocked", "findings": [{"severity": "block", "rule": "x", "file": "SKILL.md", "line": 1, "detail": "d"}], "stats": {}})
    with pytest.raises(service.ExtensionError) as err:
        skills_view.set_skill_enabled("beta", True, skills_dir=root)
    assert err.value.status == 409
