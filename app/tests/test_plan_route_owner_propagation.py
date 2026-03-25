from __future__ import annotations

from types import SimpleNamespace

from fastapi import BackgroundTasks
from starlette.requests import Request

from app.routers import plan_routes
from app.services.request_principal import RequestPrincipal


def _build_request(owner_id: str) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/tasks/demo",
        "raw_path": b"/tasks/demo",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"x-forwarded-user", owner_id.encode("utf-8"))],
        "client": ("testclient", 50000),
        "server": ("test", 80),
        "state": {},
    }
    request = Request(scope)
    request.state.principal = RequestPrincipal(
        user_id=owner_id,
        email=f"{owner_id}@example.com",
        auth_source="test",
    )
    return request


class _Node:
    def __init__(self, task_id: int, name: str, status: str = "pending") -> None:
        self.id = task_id
        self.name = name
        self.status = status

    def display_name(self) -> str:
        return self.name


class _Tree:
    def __init__(self, plan_id: int, task_id: int) -> None:
        self.plan_id = plan_id
        self.title = f"Plan {plan_id}"
        self.nodes = {task_id: _Node(task_id, f"Task {task_id}")}

    def has_node(self, task_id: int) -> bool:
        return task_id in self.nodes


class _Job:
    def __init__(
        self,
        job_id: str = "job-1",
        status: str = "queued",
        *,
        job_type: str = "plan_decompose",
        mode: str = "single_node",
    ) -> None:
        self.job_id = job_id
        self.status = status
        self.job_type = job_type
        self.mode = mode

    def to_payload(self):
        return {"job_id": self.job_id, "status": self.status}


def test_async_decompose_job_records_request_owner(monkeypatch) -> None:
    seen: dict[str, object] = {}
    tree = _Tree(plan_id=7, task_id=3)

    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda plan_id: tree)
    monkeypatch.setattr(plan_routes, "_ensure_plan_access", lambda *args, **kwargs: None)

    def _create_job(**kwargs):
        seen["job"] = kwargs
        return _Job(job_id="decompose-1", job_type="plan_decompose", mode="single_node")

    monkeypatch.setattr(plan_routes.plan_decomposition_jobs, "create_job", _create_job)
    monkeypatch.setattr(plan_routes.plan_decomposition_jobs, "append_log", lambda *args, **kwargs: None)

    response = plan_routes.decompose_task(
        task_id=3,
        background_tasks=BackgroundTasks(),
        raw_request=_build_request("alice"),
        request=plan_routes.DecomposeTaskRequest(plan_id=7, async_mode=True),
    )

    assert response.success is True
    assert seen["job"]["owner_id"] == "alice"


def test_async_execute_job_records_request_owner_without_session(monkeypatch) -> None:
    seen: dict[str, object] = {}
    tree = _Tree(plan_id=9, task_id=5)
    dep_plan = SimpleNamespace(
        plan_id=9,
        target_task_id=5,
        satisfied_statuses=[],
        direct_dependencies=[],
        closure_dependencies=[],
        missing_dependencies=[],
        running_dependencies=[],
        execution_order=[5],
        cycle_detected=False,
        cycle_paths=[],
    )

    class _DummyThread:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

        def start(self) -> None:
            return None

    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda plan_id: tree)
    monkeypatch.setattr(plan_routes, "_ensure_plan_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(plan_routes, "_build_execution_dependency_plan", lambda *args, **kwargs: dep_plan)
    monkeypatch.setattr(plan_routes.threading, "Thread", _DummyThread)
    monkeypatch.setattr(plan_routes.plan_decomposition_jobs, "append_log", lambda *args, **kwargs: None)
    plan_routes._task_execution_locks.clear()

    def _create_job(**kwargs):
        seen["job"] = kwargs
        return _Job(job_id="execute-1", job_type="plan_execute", mode="task_chain")

    monkeypatch.setattr(plan_routes.plan_decomposition_jobs, "create_job", _create_job)

    response = plan_routes.execute_task_with_dependencies(
        task_id=5,
        plan_id=9,
        raw_request=_build_request("bob"),
        request=plan_routes.ExecuteTaskRequest(async_mode=True),
    )

    assert response.success is True
    assert seen["job"]["owner_id"] == "bob"
    assert seen["job"]["session_id"] is None
