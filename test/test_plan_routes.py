from __future__ import annotations

import json

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.repository.plan_repository import PlanRepository
from app.routers import plan_routes
from app.services.plans.plan_decomposer import DecompositionResult
from app.services.plans.plan_models import PlanNode


@pytest.fixture()
def test_client() -> TestClient:
    return TestClient(app)


def test_get_plan_tree_endpoint(plan_repo: PlanRepository, test_client: TestClient):
    plan = plan_repo.create_plan("Tree Endpoint Plan")
    root = plan_repo.create_task(plan.id, name="Root")
    plan_repo.create_task(plan.id, name="Child", parent_id=root.id)

    response = test_client.get(f"/plans/{plan.id}/tree")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == plan.id
    assert str(root.id) in {str(node_id) for node_id in payload["nodes"].keys()}


def test_get_plan_subgraph_endpoint(plan_repo: PlanRepository, test_client: TestClient):
    plan = plan_repo.create_plan("Subgraph Plan")
    root = plan_repo.create_task(plan.id, name="Root")
    child = plan_repo.create_task(plan.id, name="Child", parent_id=root.id)

    response = test_client.get(f"/plans/{plan.id}/subgraph", params={"node_id": root.id, "max_depth": 2})
    assert response.status_code == 200
    payload = response.json()
    assert payload["plan_id"] == plan.id
    node_ids = {node["id"] for node in payload["nodes"]}
    assert {root.id, child.id}.issubset(node_ids)
    assert "outline" in payload


def test_decompose_task_endpoint_returns_stubbed_result(
    plan_repo: PlanRepository,
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    plan = plan_repo.create_plan("Decompose Plan")
    root = plan_repo.create_task(plan.id, name="Root")

    class StubDecomposer:
        def decompose_node(self, plan_id: int, task_id: int, **kwargs) -> DecompositionResult:
            return DecompositionResult(
                plan_id=plan_id,
                mode="single_node",
                root_node_id=task_id,
                processed_nodes=[task_id],
                created_tasks=[
                    PlanNode(
                        id=999,
                        plan_id=plan_id,
                        name="Stub Task",
                        instruction=None,
                        parent_id=task_id,
                        position=0,
                        depth=1,
                        path=f"/{task_id}/999",
                        metadata={},
                        dependencies=[],
                        context_combined=None,
                        context_sections=[],
                        context_meta={},
                        context_updated_at=None,
                        execution_result=None,
                    )
                ],
                failed_nodes=[],
                stopped_reason=None,
                stats={},
            )

    monkeypatch.setattr(plan_routes, "_plan_decomposer", StubDecomposer())

    response = test_client.post(
        f"/tasks/{root.id}/decompose",
        json={"plan_id": plan.id, "expand_depth": 1},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["result"]["plan_id"] == plan.id
    assert payload["result"]["created_tasks"]


def test_plan_results_endpoint(plan_repo: PlanRepository, test_client: TestClient):
    plan = plan_repo.create_plan("Results Plan")
    root = plan_repo.create_task(plan.id, name="Root")
    child1 = plan_repo.create_task(plan.id, name="Child 1", parent_id=root.id)
    child2 = plan_repo.create_task(plan.id, name="Child 2", parent_id=root.id)
    child3 = plan_repo.create_task(plan.id, name="Child 3", parent_id=root.id)

    plan_repo.update_task(
        plan.id,
        child1.id,
        status="completed",
        execution_result=json.dumps(
            {
                "status": "success",
                "content": "Trained models",
                "notes": ["took 3 mins"],
                "metadata": {"duration_sec": 180},
            }
        ),
    )
    plan_repo.update_task(
        plan.id,
        child3.id,
        status="failed",
        execution_result="Something went wrong",
    )

    response = test_client.get(f"/plans/{plan.id}/results")
    assert response.status_code == 200
    payload = response.json()
    assert payload["plan_id"] == plan.id
    assert payload["total"] == 2  # only tasks with outputs
    items = {item["task_id"]: item for item in payload["items"]}
    assert child1.id in items and child3.id in items
    assert items[child1.id]["content"] == "Trained models"
    assert items[child1.id]["notes"] == ["took 3 mins"]
    assert items[child1.id]["metadata"] == {"duration_sec": 180}
    assert items[child3.id]["content"] == "Something went wrong"
    assert items[child3.id]["notes"] == []
    assert items[child3.id]["metadata"] == {}

    response_all = test_client.get(f"/plans/{plan.id}/results", params={"only_with_output": False})
    assert response_all.status_code == 200
    payload_all = response_all.json()
    assert payload_all["total"] == 4  # root + three children


def test_task_result_endpoint(plan_repo: PlanRepository, test_client: TestClient):
    plan = plan_repo.create_plan("Task Result Plan")
    root = plan_repo.create_task(plan.id, name="Root")
    plan_repo.update_task(
        plan.id,
        root.id,
        status="completed",
        execution_result=json.dumps({"content": "Root output"}),
    )

    response = test_client.get(f"/tasks/{root.id}/result", params={"plan_id": plan.id})
    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == root.id
    assert payload["content"] == "Root output"
    assert payload["status"] == "completed"
    assert payload["raw"]["content"] == "Root output"


def test_plan_execution_summary_endpoint(plan_repo: PlanRepository, test_client: TestClient):
    plan = plan_repo.create_plan("Summary Plan")
    root = plan_repo.create_task(plan.id, name="Root")
    child_a = plan_repo.create_task(plan.id, name="A", parent_id=root.id)
    child_b = plan_repo.create_task(plan.id, name="B", parent_id=root.id)
    child_c = plan_repo.create_task(plan.id, name="C", parent_id=root.id)

    plan_repo.update_task(plan.id, root.id, status="running")
    plan_repo.update_task(plan.id, child_a.id, status="completed")
    plan_repo.update_task(plan.id, child_b.id, status="failed")
    plan_repo.update_task(plan.id, child_c.id, status="skipped")

    response = test_client.get(f"/plans/{plan.id}/execution/summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["plan_id"] == plan.id
    assert payload["total_tasks"] == 4
    assert payload["completed"] == 1
    assert payload["failed"] == 1
    assert payload["skipped"] == 1
    assert payload["running"] == 1
    assert payload["pending"] == 0
