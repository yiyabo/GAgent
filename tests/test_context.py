import os
from typing import Dict, Any

from fastapi.testclient import TestClient

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.services.context import gather_context


def test_repository_links_and_dependencies(tmp_path, monkeypatch):
    # Isolate DB
    test_db = tmp_path / "repo_links.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # Create tasks A -> B (requires), C -> B (refers)
    a = repo.create_task("[D] A", status="pending", priority=5)
    b = repo.create_task("[D] B", status="pending", priority=10)
    c = repo.create_task("[D] C", status="pending", priority=15)

    repo.upsert_task_input(a, "Prompt A")
    repo.upsert_task_input(b, "Prompt B")
    repo.upsert_task_input(c, "Prompt C")

    repo.create_link(a, b, "requires")
    repo.create_link(c, b, "refers")

    inbound = repo.list_links(to_id=b)
    assert any(x["from_id"] == a and x["to_id"] == b and x["kind"] == "requires" for x in inbound)
    assert any(x["from_id"] == c and x["to_id"] == b and x["kind"] == "refers" for x in inbound)

    outbound_a = repo.list_links(from_id=a)
    assert len(outbound_a) == 1 and outbound_a[0]["to_id"] == b

    deps = repo.list_dependencies(b)
    # requires should come before refers
    assert deps[0]["id"] == a and deps[0]["kind"] == "requires"
    assert any(d["id"] == c and d["kind"] == "refers" for d in deps)


def test_gather_context_minimal(tmp_path, monkeypatch):
    test_db = tmp_path / "ctx.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # Plan: [P] A -> [P] B (requires)
    a = repo.create_task("[P] A", status="pending", priority=1)
    b = repo.create_task("[P] B", status="pending", priority=2)
    repo.upsert_task_input(a, "Input A")
    repo.upsert_task_input(b, "Input B")
    repo.create_link(a, b, "requires")

    bundle = gather_context(b, repo=repo, include_deps=True, include_plan=False)
    assert bundle["task_id"] == b
    secs = bundle["sections"]
    assert len(secs) >= 1
    assert secs[0]["task_id"] == a and secs[0]["kind"].startswith("dep:")
    assert isinstance(bundle["combined"], str) and len(bundle["combined"]) > 0


def test_api_context_endpoints(tmp_path, monkeypatch):
    os.environ["LLM_MOCK"] = "1"
    test_db = tmp_path / "api_ctx.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    from app.main import app

    with TestClient(app) as client:
        # Approve a simple plan with two tasks
        plan = {
            "title": "CTX",
            "tasks": [
                {"name": "One", "prompt": "Do one"},
                {"name": "Two", "prompt": "Do two"},
            ],
        }
        r = client.post("/plans/approve", json=plan)
        assert r.status_code == 200

        # Get task IDs
        r = client.get(f"/plans/{plan['title']}/tasks")
        assert r.status_code == 200
        tasks = r.json()
        one: Dict[str, Any] = next(t for t in tasks if t["short_name"] == "One")
        two: Dict[str, Any] = next(t for t in tasks if t["short_name"] == "Two")

        # Create a requires link: One -> Two
        r = client.post("/context/links", json={"from_id": one["id"], "to_id": two["id"], "kind": "requires"})
        assert r.status_code == 200 and r.json().get("ok")

        # Verify inbound/outbound
        r = client.get(f"/context/links/{two['id']}")
        data = r.json()
        assert any(l["from_id"] == one["id"] and l["kind"] == "requires" for l in data.get("inbound", []))

        # Preview context for Two
        r = client.post(f"/tasks/{two['id']}/context/preview", json={})
        assert r.status_code == 200
        preview = r.json()
        assert preview["task_id"] == two["id"]
        assert any(sec["task_id"] == one["id"] for sec in preview.get("sections", []))

        # Run tasks with context enabled to ensure no error path
        r = client.post("/run", json={"title": plan["title"], "use_context": True})
        assert r.status_code == 200
        assert isinstance(r.json(), list)
