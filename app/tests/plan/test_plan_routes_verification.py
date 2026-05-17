from __future__ import annotations

import json

from starlette.requests import Request

from app.routers import plan_routes
from app.services.plans.plan_models import PlanNode, PlanTree


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": []})


def _tree(*nodes: PlanNode) -> PlanTree:
    tree = PlanTree(id=501, title="Verification route plan")
    for node in nodes:
        tree.nodes[node.id] = node
    tree.rebuild_adjacency()
    return tree


class _RepoStub:
    def __init__(self, tree: PlanTree) -> None:
        self._tree = tree
        self.update_calls: list[tuple[int, int, dict]] = []

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        assert plan_id == self._tree.id
        return self._tree

    def update_task(self, plan_id: int, task_id: int, **kwargs):
        self.update_calls.append((plan_id, task_id, dict(kwargs)))
        raise AssertionError("dry-run route must not persist task updates")


def test_verify_task_without_execution_result_returns_structured_response(monkeypatch) -> None:
    tree = _tree(PlanNode(id=1, plan_id=501, name="Leaf", status="pending"))
    monkeypatch.setattr(plan_routes, "_load_authorized_plan_tree", lambda _plan_id, _request: tree)
    monkeypatch.setattr(plan_routes, "_resolve_effective_task_states", lambda _plan_id, _tree: {})

    response = plan_routes.verify_task_result(
        1,
        _request(),
        plan_id=501,
    )

    assert response.success is False
    assert response.message == "Task 1 has not produced an execution result yet; run it before verification."
    assert response.result.metadata["verification_status"] == "not_run"


def test_verify_composite_task_without_execution_result_points_to_child(monkeypatch) -> None:
    tree = _tree(
        PlanNode(id=1, plan_id=501, name="Parent", status="pending"),
        PlanNode(id=2, plan_id=501, name="Leaf", parent_id=1, status="pending"),
    )
    monkeypatch.setattr(plan_routes, "_load_authorized_plan_tree", lambda _plan_id, _request: tree)
    monkeypatch.setattr(plan_routes, "_resolve_effective_task_states", lambda _plan_id, _tree: {})

    response = plan_routes.verify_task_result(
        1,
        _request(),
        plan_id=501,
    )

    assert response.success is False
    assert "composite parent" in response.message
    assert response.result.metadata["child_task_ids"] == [2]
    assert response.result.metadata["verifiable_task_ids"] == [2]


def test_dry_run_reverify_plan_route_does_not_update_repo(tmp_path, monkeypatch) -> None:
    report = tmp_path / "report.txt"
    report.write_text("ok\n", encoding="utf-8")
    payload = {
        "status": "failed",
        "metadata": {"execution_status": "completed", "artifact_paths": [str(report)]},
    }
    tree = _tree(
        PlanNode(
            id=1,
            plan_id=501,
            name="Leaf",
            status="failed",
            metadata={
                "acceptance_criteria": {
                    "blocking": True,
                    "checks": [{"type": "file_nonempty", "path": str(report)}],
                }
            },
            execution_result=json.dumps(payload, ensure_ascii=False),
        )
    )
    repo = _RepoStub(tree)
    monkeypatch.setattr(plan_routes, "_plan_repo", repo)
    monkeypatch.setattr(plan_routes, "_load_authorized_plan_tree", lambda _plan_id, _request: tree)

    response = plan_routes.dry_run_reverify_plan(501, _request())

    assert response.success is True
    assert response.dry_run is True
    assert response.summary["would_change_status"] == 1
    assert response.items[0]["dry_run_status"] == "completed"
    assert response.items[0]["diagnostics"]["chosen_base_dir"] == str(tmp_path)
    assert repo.update_calls == []
