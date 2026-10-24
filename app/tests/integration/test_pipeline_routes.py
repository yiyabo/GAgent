"""Pipeline routes end to end: feature switch, ownership, uploads, saving, running and reading outputs."""

from __future__ import annotations

import asyncio
import threading
import uuid
from pathlib import Path

import pytest

PASSWORD = "pipeline-route-test-pw"
FASTA = ">seq1\nATGCATGC\n"


class FakeSeqkit:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = []

    async def __call__(self, tool_name, operation, input_file=None, params=None, **_):
        self.calls.append((tool_name, operation, input_file))
        work = self.root / "bio_tools" / "_native" / "runs" / uuid.uuid4().hex[:12] / "work"
        work.mkdir(parents=True)
        return {"success": True, "work_dir": str(work), "stdout": "file\tnum_seqs\nx\t1\n"}


@pytest.fixture
def client_env(app_client_factory, isolated_app_env, monkeypatch):
    import tool_box.bio_tools as bio_tools_pkg
    from app.services.auth import rate_limiter
    from app.services.pipelines import engine, service

    # The sign-up limiter is process-wide; earlier tests may have used up this client's window.
    rate_limiter._buckets.clear()

    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("AUTH_OPEN_SIGNUP", "1")
    monkeypatch.setenv("PIPELINES_ENABLED", "1")
    fake = FakeSeqkit(isolated_app_env["runtime_root"])
    monkeypatch.setattr(bio_tools_pkg, "bio_tools_handler", fake)

    def run_now(run_id: str) -> None:
        thread = threading.Thread(target=lambda: asyncio.run(engine.execute_run(run_id)))
        thread.start()
        thread.join(timeout=30)

    monkeypatch.setattr(service, "launch", run_now)
    return app_client_factory, fake


def register(client, email):
    client.cookies.clear()
    response = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["user"]["user_id"]


def login(client, email):
    client.cookies.clear()
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text


@pytest.mark.integration
def test_pipeline_routes_save_run_and_serve_outputs_to_their_owner_only(client_env):
    from app.services.pipelines import service

    factory, fake = client_env
    with factory() as client:
        register(client, "owner@example.com")
        assert client.get("/pipelines/status").json() == {"enabled": True}
        catalog = client.get("/pipelines/node-types").json()
        assert any(node["type"] == "prodigal.predict" for node in catalog["node_types"])

        created = client.post("/pipelines/items", json={"name": "Phage basics", "description": "test"})
        assert created.status_code == 200, created.text
        pipeline_id = created.json()["id"]
        assert [item["id"] for item in client.get("/pipelines/items").json()["pipelines"]] == [pipeline_id]

        refused = client.post(f"/pipelines/{pipeline_id}/files", files={"file": ("notes.docx", b"x")})
        assert refused.status_code == 415
        upload = client.post(f"/pipelines/{pipeline_id}/files", files={"file": ("lambda.fasta", FASTA.encode())})
        assert upload.status_code == 200, upload.text
        path = upload.json()["path"]
        assert path.startswith("uploads/") and path.endswith("_lambda.fasta"), path  # no server path leaves the API
        assert [item["path"] for item in client.get(f"/pipelines/{pipeline_id}/files").json()["files"]] == [path]

        graph = {
            "schema": 1,
            "nodes": {
                "g": {"type": "input.genome", "params": {"path": path}},
                "s": {"type": "seqkit.stats", "inputs": {"sequences": {"node": "g", "port": "file"}}},
            },
            "layout": {"g": {"x": 10, "y": 20}},
        }
        saved = client.put(f"/pipelines/{pipeline_id}", json={"graph": graph})
        assert saved.status_code == 200 and saved.json()["validation"]["ok"] is True
        assert saved.json()["draft"]["layout"] == {"g": {"x": 10, "y": 20}}

        bad = client.post("/pipelines/validate", json={"graph": {"schema": 1, "nodes": {"s": {"type": "seqkit.stats"}}}})
        assert bad.json()["ok"] is False and bad.json()["errors"][0]["code"] == "missing_input"

        started = client.post(f"/pipelines/{pipeline_id}/runs", json={})
        assert started.status_code == 200, started.text
        run_id = started.json()["id"]
        run = client.get(f"/pipelines/runs/{run_id}").json()
        assert run["status"] == "succeeded", run
        assert run["version"] == 1 and run["nodes"]["s"]["status"] == "succeeded"
        assert fake.calls == [("seqkit", "stats", str((service.session_dir(pipeline_id) / path).resolve()))]
        preview = client.get(f"/pipelines/runs/{run_id}/nodes/s/outputs/stats/preview").json()
        assert "num_seqs" in preview["text"]
        download = client.get(f"/pipelines/runs/{run_id}/nodes/s/outputs/stats/download")
        assert download.status_code == 200 and b"num_seqs" in download.content
        assert client.get(f"/pipelines/runs/{run_id}/nodes/s/outputs/nope/preview").status_code == 404
        assert client.post(f"/pipelines/runs/{run_id}/cancel").status_code == 409
        assert [item["id"] for item in client.get(f"/pipelines/{pipeline_id}/runs").json()["runs"]] == [run_id]

        register(client, "other@example.com")
        assert client.get("/pipelines/items").json()["pipelines"] == []
        assert client.get(f"/pipelines/{pipeline_id}").status_code == 403
        assert client.get(f"/pipelines/runs/{run_id}").status_code == 403
        assert client.get(f"/pipelines/runs/{run_id}/nodes/s/outputs/stats/download").status_code == 403
        assert client.post(f"/pipelines/{pipeline_id}/runs", json={}).status_code == 403

        login(client, "owner@example.com")
        assert [item["id"] for item in catalog["templates"]] == ["phage_lambda_t7"]
        from_template = client.post("/pipelines/items", json={"name": "Lambda vs T7", "template": "phage_lambda_t7"})
        assert from_template.status_code == 200 and from_template.json()["validation"]["ok"] is True
        assert len(from_template.json()["draft"]["nodes"]) == catalog["templates"][0]["node_count"]
        assert client.post("/pipelines/items", json={"name": "x", "template": "nope"}).status_code == 422
        assert client.delete(f"/pipelines/{pipeline_id}").status_code == 200
        assert client.get(f"/pipelines/{pipeline_id}").status_code == 404


@pytest.mark.integration
def test_pipeline_routes_are_hidden_while_the_feature_is_off(client_env, monkeypatch):
    factory, _ = client_env
    monkeypatch.setenv("PIPELINES_ENABLED", "0")
    with factory() as client:
        register(client, "owner@example.com")
        assert client.get("/pipelines/status").json() == {"enabled": False}
        assert client.get("/pipelines/items").status_code == 404
        assert client.get("/pipelines/node-types").status_code == 404
        assert client.post("/pipelines/items", json={"name": "x"}).status_code == 404
