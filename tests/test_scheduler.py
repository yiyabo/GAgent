import os
from typing import List, Dict, Any

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.scheduler import requires_dag_order
from app.utils import split_prefix
from fastapi.testclient import TestClient


def _short_names(rows: List[Dict[str, Any]]) -> List[str]:
    return [split_prefix(r.get("name", ""))[1] for r in rows]


def test_requires_dag_order_basic(tmp_path, monkeypatch):
    test_db = tmp_path / "dag_basic.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # Plan: [DAG] B(1) & A(5) -> C(10) -> D(10)
    a = repo.create_task("[DAG] A", status="pending", priority=5)
    b = repo.create_task("[DAG] B", status="pending", priority=1)
    c = repo.create_task("[DAG] C", status="pending", priority=10)
    d = repo.create_task("[DAG] D", status="pending", priority=10)

    repo.create_link(a, c, "requires")
    repo.create_link(b, c, "requires")
    repo.create_link(c, d, "requires")

    order, cycle = requires_dag_order("DAG")
    assert cycle is None
    assert _short_names(order) == ["B", "A", "C", "D"]


def test_requires_dag_cycle_detection_scoped(tmp_path, monkeypatch):
    test_db = tmp_path / "dag_cycle.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # [CYC] A -> B -> C -> A (cycle)
    a = repo.create_task("[CYC] A", status="pending", priority=1)
    b = repo.create_task("[CYC] B", status="pending", priority=2)
    c = repo.create_task("[CYC] C", status="pending", priority=3)

    repo.create_link(a, b, "requires")
    repo.create_link(b, c, "requires")
    repo.create_link(c, a, "requires")

    order, cycle = requires_dag_order("CYC")
    assert cycle is not None
    names = set((cycle.get("names") or {}).values())
    assert names == {"A", "B", "C"}
    assert len(order) < 3


def test_requires_dag_scoped_ignores_external_edges(tmp_path, monkeypatch):
    test_db = tmp_path / "dag_scope.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    init_db()
    repo = SqliteTaskRepository()

    # [X] A -> B ; external [Y] C -> [X] B (ignored when scoping to X)
    a = repo.create_task("[X] A", status="pending", priority=1)
    b = repo.create_task("[X] B", status="pending", priority=2)
    c = repo.create_task("[Y] C", status="pending", priority=1)

    repo.create_link(a, b, "requires")
    repo.create_link(c, b, "requires")  # cross-plan; should be ignored for X scope

    order, cycle = requires_dag_order("X")
    assert cycle is None
    assert _short_names(order) == ["A", "B"]


def test_run_api_dag_cycle_returns_400(tmp_path, monkeypatch):
    os.environ["LLM_MOCK"] = "1"
    test_db = tmp_path / "dag_api.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)

    from app.main import app

    with TestClient(app) as client:
        # Approve small plan
        plan = {
            "title": "DAPI",
            "tasks": [
                {"name": "A", "prompt": "Do A"},
                {"name": "B", "prompt": "Do B"},
            ],
        }
        r = client.post("/plans/approve", json=plan)
        assert r.status_code == 200

        # Get task IDs
        r = client.get(f"/plans/{plan['title']}/tasks")
        assert r.status_code == 200
        tasks = r.json()
        ta = next(t for t in tasks if t["short_name"] == "A")
        tb = next(t for t in tasks if t["short_name"] == "B")

        # Create cycle: A<->B
        r = client.post("/context/links", json={"from_id": ta["id"], "to_id": tb["id"], "kind": "requires"})
        assert r.status_code == 200 and r.json().get("ok")
        r = client.post("/context/links", json={"from_id": tb["id"], "to_id": ta["id"], "kind": "requires"})
        assert r.status_code == 200 and r.json().get("ok")

        # Run with DAG schedule should 400
        r = client.post("/run", json={"title": plan["title"], "schedule": "dag"})
        assert r.status_code == 400
        data = r.json()
        assert data.get("detail", {}).get("error") == "cycle_detected"
