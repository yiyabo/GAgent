import os
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app.database import init_db
from app.main import app  # Import app for the test client
from app.repository.tasks import SqliteTaskRepository
from app.scheduler import requires_dag_order


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Provides a clean, isolated database repository for each test."""
    db_path = tmp_path / "test_scheduler.db"
    monkeypatch.setattr("app.database.DB_PATH", str(db_path))
    init_db()
    return SqliteTaskRepository()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Provides a FastAPI TestClient with a clean, isolated database."""
    db_path = tmp_path / f"test_api_{os.urandom(4).hex()}.db"
    monkeypatch.setattr("app.database.DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_MOCK", "1")
    init_db()
    with TestClient(app) as c:
        yield c


def _short_names(rows: List[Dict[str, Any]]) -> List[str]:
    """Helper to get task names from a list of task dicts."""
    return [r.get("name", "") for r in rows]


def test_requires_dag_order_basic(repo):
    """Test basic DAG topological sort."""
    plan_id = repo.create_plan("DAG Plan")

    a = repo.create_task("A", status="pending", priority=5)
    repo.link_task_to_plan(plan_id, a)
    b = repo.create_task("B", status="pending", priority=1)
    repo.link_task_to_plan(plan_id, b)
    c = repo.create_task("C", status="pending", priority=10)
    repo.link_task_to_plan(plan_id, c)
    d = repo.create_task("D", status="pending", priority=10)
    repo.link_task_to_plan(plan_id, d)

    repo.create_link(a, c, "requires")
    repo.create_link(b, c, "requires")
    repo.create_link(c, d, "requires")

    order, cycle = requires_dag_order(plan_id)
    assert cycle is None
    assert _short_names(order) == ["B", "A", "C", "D"]


def test_requires_dag_cycle_detection_scoped(repo):
    """Test cycle detection within a plan."""
    plan_id = repo.create_plan("Cycle Plan")

    a = repo.create_task("A", status="pending", priority=1)
    repo.link_task_to_plan(plan_id, a)
    b = repo.create_task("B", status="pending", priority=2)
    repo.link_task_to_plan(plan_id, b)
    c = repo.create_task("C", status="pending", priority=3)
    repo.link_task_to_plan(plan_id, c)

    repo.create_link(a, b, "requires")
    repo.create_link(b, c, "requires")
    repo.create_link(c, a, "requires")

    order, cycle = requires_dag_order(plan_id)
    assert cycle is not None
    assert set(cycle.get("nodes", [])) == {a, b, c}
    assert len(order) == 0


def test_requires_dag_scoped_ignores_external_edges(repo):
    """Test that DAG is scoped to a single plan and ignores links from other plans."""
    plan_x_id = repo.create_plan("Plan X")
    plan_y_id = repo.create_plan("Plan Y")

    a = repo.create_task("A", status="pending", priority=1)
    repo.link_task_to_plan(plan_x_id, a)
    b = repo.create_task("B", status="pending", priority=2)
    repo.link_task_to_plan(plan_x_id, b)
    repo.create_link(a, b, "requires")

    c = repo.create_task("C", status="pending", priority=1)
    repo.link_task_to_plan(plan_y_id, c)
    # This link should be ignored when scheduling Plan X
    repo.create_link(c, b, "requires")

    order, cycle = requires_dag_order(plan_x_id)
    assert cycle is None
    assert _short_names(order) == ["A", "B"]


# Un-skipped and fixed test
def test_run_api_dag_cycle_returns_400(client):
    """Test that the /run API endpoint returns a 400 error for a DAG cycle."""
    # 1. Create a plan
    r_plan = client.post("/plans/propose", json={"goal": "Test Plan for Cycle"})
    assert r_plan.status_code == 200
    plan_id = r_plan.json()["plan_id"]

    # 2. Create tasks and link them to the plan
    task_a_payload = {"name": "A", "plan_id": plan_id, "task_type": "atomic"}
    r_task_a = client.post("/tasks", json=task_a_payload)
    assert r_task_a.status_code == 200
    task_a_id = r_task_a.json()["id"]

    task_b_payload = {"name": "B", "plan_id": plan_id, "task_type": "atomic"}
    r_task_b = client.post("/tasks", json=task_b_payload)
    assert r_task_b.status_code == 200
    task_b_id = r_task_b.json()["id"]

    # 3. Create a dependency cycle
    r_link1 = client.post(
        "/context/links",
        json={"from_id": task_a_id, "to_id": task_b_id, "kind": "requires"},
    )
    assert r_link1.status_code == 200
    r_link2 = client.post(
        "/context/links",
        json={"from_id": task_b_id, "to_id": task_a_id, "kind": "requires"},
    )
    assert r_link2.status_code == 200

    # 4. Run the plan with 'dag' schedule and expect a 400 error
    r_run = client.post("/run", json={"plan_id": plan_id, "schedule": "dag"})
    assert r_run.status_code == 400
    data = r_run.json()
    assert "Cycle detected" in data["detail"]["message"]
    assert sorted(data["detail"]["nodes"]) == sorted([task_a_id, task_b_id])
    assert "Cycle detected" in data["detail"]["message"]
    assert sorted(data["detail"]["nodes"]) == sorted([task_a_id, task_b_id])
