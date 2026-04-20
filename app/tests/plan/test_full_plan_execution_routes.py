from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from starlette.requests import Request

from app.routers import plan_routes
from app.services.plans.artifact_contracts import canonical_artifact_path, save_artifact_manifest
from app.services.plans.artifact_preflight import ArtifactPreflightIssue, ArtifactPreflightResult
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.request_principal import RequestPrincipal


def _build_request(owner_id: str) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/plans/7/execute-full",
        "raw_path": b"/plans/7/execute-full",
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


def _tree() -> PlanTree:
    nodes = {
        1: PlanNode(id=1, plan_id=7, name="Step 1", status="pending"),
        2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending", dependencies=[1]),
    }
    tree = PlanTree(id=7, title="Plan 7", nodes=nodes)
    tree.rebuild_adjacency()
    return tree


class _JobStoreStub:
    def __init__(self) -> None:
        self.running_calls = 0
        self.success_calls: list[dict] = []
        self.failure_calls: list[dict] = []
        self.stats_calls: list[dict] = []

    def mark_running(self, _job_id: str) -> None:
        self.running_calls += 1

    def update_stats(self, _job_id: str, stats: dict) -> None:
        self.stats_calls.append(dict(stats))

    def mark_success(self, _job_id: str, **kwargs) -> None:
        self.success_calls.append(dict(kwargs))

    def mark_failure(self, _job_id: str, error: str, **kwargs) -> None:
        payload = dict(kwargs)
        payload["error"] = error
        self.failure_calls.append(payload)


def test_execute_full_plan_rejects_duplicate_run_before_creating_job(monkeypatch) -> None:
    tree = _tree()
    create_job_calls: list[dict] = []

    monkeypatch.setattr(
        plan_routes,
        "_load_authorized_plan_tree",
        lambda _plan_id, _request: tree,
    )
    monkeypatch.setattr(
        plan_routes.plan_decomposition_jobs,
        "create_job",
        lambda **kwargs: create_job_calls.append(kwargs),
    )

    lock = threading.Lock()
    assert lock.acquire(blocking=False) is True
    plan_routes._task_execution_locks.clear()
    plan_routes._task_execution_locks[(7, 0)] = lock
    try:
        response = plan_routes.execute_full_plan(
            7,
            _build_request("alice"),
            plan_routes.ExecuteFullPlanRequest(async_mode=True),
        )
    finally:
        try:
            lock.release()
        except RuntimeError:
            pass
        plan_routes._task_execution_locks.clear()

    assert response.success is False
    assert "already being executed" in response.message
    assert create_job_calls == []


def test_execute_full_plan_async_persists_overall_progress_counts(
    monkeypatch,
) -> None:
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(id=1, plan_id=7, name="Step 1", status="completed"),
            2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending", dependencies=[1]),
        },
    )
    tree.rebuild_adjacency()
    create_job_calls: list[dict] = []
    thread_calls: list[dict] = []

    class _Job:
        job_id = "job-async"
        status = "queued"
        job_type = "plan_execute"

        def to_payload(self):
            return {"job_id": self.job_id, "status": self.status, "job_type": self.job_type}

    class _Thread:
        def __init__(self, *, target=None, kwargs=None, daemon=None):
            thread_calls.append({"target": target, "kwargs": dict(kwargs or {}), "daemon": daemon})

        def start(self) -> None:
            return None

    monkeypatch.setattr(
        plan_routes,
        "_load_authorized_plan_tree",
        lambda _plan_id, _request: tree,
    )
    monkeypatch.setattr(plan_routes, "_acquire_plan_execution_lock", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(plan_routes, "_release_plan_execution_lock", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        plan_routes.plan_decomposition_jobs,
        "create_job",
        lambda **kwargs: create_job_calls.append(kwargs) or _Job(),
    )
    monkeypatch.setattr(plan_routes.plan_decomposition_jobs, "append_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(plan_routes.threading, "Thread", _Thread)

    response = plan_routes.execute_full_plan(
        7,
        _build_request("alice"),
        plan_routes.ExecuteFullPlanRequest(async_mode=True),
    )

    assert response.success is True
    assert len(create_job_calls) == 1
    assert len(thread_calls) == 1
    assert create_job_calls[0]["params"]["overall_total_steps"] == 2
    assert create_job_calls[0]["params"]["initial_completed_steps"] == 1
    assert create_job_calls[0]["metadata"]["todo_total_tasks"] == 2
    assert create_job_calls[0]["metadata"]["todo_completed_tasks"] == 1
    assert thread_calls[0]["kwargs"]["overall_total_steps"] == 2
    assert thread_calls[0]["kwargs"]["initial_completed_steps"] == 1


def test_execute_full_plan_returns_preflight_failure(monkeypatch) -> None:
    tree = _tree()

    monkeypatch.setattr(
        plan_routes,
        "_load_authorized_plan_tree",
        lambda _plan_id, _request: tree,
    )
    monkeypatch.setattr(
        plan_routes._artifact_preflight_service,
        "validate_plan",
        lambda *_args, **_kwargs: ArtifactPreflightResult(
            plan_id=7,
            ok=False,
            errors=[
                ArtifactPreflightIssue(
                    code="missing_producer",
                    severity="error",
                    task_id=2,
                    message="Task #2 requires missing artifact alias 'ai_dl.references_bib'.",
                )
            ],
        ),
    )

    response = plan_routes.execute_full_plan(
        7,
        _build_request("alice"),
        plan_routes.ExecuteFullPlanRequest(async_mode=True),
    )

    assert response.success is False
    assert "Artifact preflight failed" in response.message
    assert response.result["preflight"]["errors"][0]["code"] == "missing_producer"


def test_execute_task_with_dependencies_allows_external_canonical_producer(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    alias = "general.evidence_md"
    canonical = canonical_artifact_path(7, alias)
    assert canonical is not None
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("evidence", encoding="utf-8")
    save_artifact_manifest(
        7,
        {
            "plan_id": 7,
            "artifacts": {
                alias: {
                    "alias": alias,
                    "path": str(canonical.resolve()),
                    "producer_task_id": 1,
                    "source_path": str(canonical.resolve()),
                }
            },
        },
    )
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(
                id=1,
                plan_id=7,
                name="Prepare evidence",
                status="completed",
                metadata={"artifact_contract": {"publishes": [alias]}},
                execution_result=json.dumps({"status": "completed", "content": "ok"}),
            ),
            2: PlanNode(
                id=2,
                plan_id=7,
                name="Consume evidence",
                status="pending",
                metadata={"artifact_contract": {"requires": [alias]}},
            ),
        },
    )
    tree.rebuild_adjacency()

    monkeypatch.setattr(
        plan_routes,
        "_load_authorized_plan_tree",
        lambda _plan_id, _request: tree,
    )
    monkeypatch.setattr(
        plan_routes,
        "_build_plan_execution_snapshot",
        lambda _plan_id: {"active_task_ids": set(), "active_jobs": []},
    )
    monkeypatch.setattr(
        plan_routes._plan_executor,
        "execute_task",
        lambda _plan_id, task_id, **_kwargs: SimpleNamespace(
            status="completed",
            duration_sec=0.1,
            content=f"task {task_id} ok",
        ),
    )

    response = plan_routes.execute_task_with_dependencies(
        2,
        plan_id=7,
        raw_request=_build_request("alice"),
        request=plan_routes.ExecuteTaskRequest(
            async_mode=False,
            include_dependencies=False,
            include_subtasks=False,
        ),
    )

    assert response.success is True
    assert response.result["executed_task_ids"] == [2]


def test_run_full_plan_job_continues_after_exception_when_stop_on_failure_disabled(
    monkeypatch,
) -> None:
    store = _JobStoreStub()
    execution_order: list[int] = []
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(id=1, plan_id=7, name="Step 1", status="pending"),
            2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending"),
        },
    )
    tree.rebuild_adjacency()

    def _execute_task(_plan_id: int, task_id: int, **_kwargs):
        execution_order.append(task_id)
        if task_id == 1:
            raise RuntimeError("boom")
        return SimpleNamespace(status="completed", duration_sec=0.1, content="ok")

    monkeypatch.setattr(plan_routes, "plan_decomposition_jobs", store)
    monkeypatch.setattr(plan_routes, "log_job_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute_task)
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda _plan_id: tree)
    monkeypatch.setattr(
        plan_routes,
        "_resolve_effective_task_states",
        lambda _plan_id, _tree, **kwargs: {
            tid: {"effective_status": "pending"} for tid in tree.nodes
        },
    )

    plan_routes._run_full_plan_job(
        job_id="job-1",
        plan_id=7,
        task_order=[1, 2],
        stop_on_failure=False,
    )

    assert execution_order == [1, 2]
    assert store.success_calls == []
    assert len(store.failure_calls) == 1
    result = store.failure_calls[0]["result"]
    assert result["executed_task_ids"] == [2]
    assert result["failed_task_ids"] == [1]


def test_run_full_plan_job_skips_tasks_completed_after_queue_build(
    monkeypatch,
) -> None:
    store = _JobStoreStub()
    execution_order: list[int] = []
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(id=1, plan_id=7, name="Step 1", status="completed"),
            2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending"),
        },
    )
    tree.rebuild_adjacency()

    def _execute_task(_plan_id: int, task_id: int, **_kwargs):
        execution_order.append(task_id)
        return SimpleNamespace(status="completed", duration_sec=0.1, content="ok")

    monkeypatch.setattr(plan_routes, "plan_decomposition_jobs", store)
    monkeypatch.setattr(plan_routes, "log_job_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda _plan_id: tree)
    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute_task)

    plan_routes._run_full_plan_job(
        job_id="job-2",
        plan_id=7,
        task_order=[1, 2],
        stop_on_failure=False,
    )

    assert execution_order == [2]
    assert store.failure_calls == []
    assert len(store.success_calls) == 1
    result = store.success_calls[0]["result"]
    assert result["executed_task_ids"] == [1, 2]
    assert result["failed_task_ids"] == []
    assert result["skipped_task_ids"] == []
    assert result["steps"][0]["task_id"] == 1
    assert result["steps"][0]["status"] == "already_completed"


def test_execute_full_plan_excludes_running_tasks_and_blocked_dependents(
    monkeypatch,
) -> None:
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(id=1, plan_id=7, name="Step 1", status="running"),
            2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending", dependencies=[1]),
            3: PlanNode(id=3, plan_id=7, name="Step 3", status="pending"),
        },
    )
    tree.rebuild_adjacency()
    executed: list[int] = []

    def _execute_task(_plan_id: int, task_id: int, **_kwargs):
        executed.append(task_id)
        tree.nodes[task_id].status = "completed"
        tree.nodes[task_id].execution_result = json.dumps({"status": "completed", "content": "ok"})
        return SimpleNamespace(status="completed", duration_sec=0.1, content="ok")

    monkeypatch.setattr(
        plan_routes,
        "_load_authorized_plan_tree",
        lambda _plan_id, _request: tree,
    )
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda _plan_id: tree)
    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute_task)
    monkeypatch.setattr(
        plan_routes,
        "_build_plan_execution_snapshot",
        lambda _plan_id: {"active_task_ids": {1}, "active_jobs": []},
    )

    response = plan_routes.execute_full_plan(
        7,
        _build_request("alice"),
        plan_routes.ExecuteFullPlanRequest(async_mode=False),
    )

    assert response.success is True
    assert executed == [3]
    assert response.result["execution_order"] == [3]


def test_run_full_plan_job_skips_tasks_already_running_after_queue_build(
    monkeypatch,
) -> None:
    store = _JobStoreStub()
    execution_order: list[int] = []
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(id=1, plan_id=7, name="Step 1", status="running"),
            2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending"),
        },
    )
    tree.rebuild_adjacency()

    def _execute_task(_plan_id: int, task_id: int, **_kwargs):
        execution_order.append(task_id)
        return SimpleNamespace(status="completed", duration_sec=0.1, content="ok")

    monkeypatch.setattr(plan_routes, "plan_decomposition_jobs", store)
    monkeypatch.setattr(plan_routes, "log_job_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda _plan_id: tree)
    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute_task)
    monkeypatch.setattr(
        plan_routes,
        "_build_plan_execution_snapshot",
        lambda _plan_id, exclude_job_ids=None: {"active_task_ids": {1}, "active_jobs": [{"job_id": "job-active"}]},
    )

    plan_routes._run_full_plan_job(
        job_id="job-3",
        plan_id=7,
        task_order=[1, 2],
        stop_on_failure=False,
    )

    assert execution_order == [2]
    assert store.failure_calls == []
    assert len(store.success_calls) == 1
    result = store.success_calls[0]["result"]
    assert result["executed_task_ids"] == [1, 2]
    assert result["steps"][0]["task_id"] == 1
    assert result["steps"][0]["status"] == "already_running"


def test_run_full_plan_job_reruns_stale_running_tasks_without_active_job(
    monkeypatch,
) -> None:
    store = _JobStoreStub()
    execution_order: list[int] = []
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(
                id=1,
                plan_id=7,
                name="Step 1",
                status="running",
                execution_result=json.dumps({"status": "failed", "content": "Error: ConnectError"}),
            ),
            2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending"),
        },
    )
    tree.rebuild_adjacency()

    def _execute_task(_plan_id: int, task_id: int, **_kwargs):
        execution_order.append(task_id)
        return SimpleNamespace(status="completed", duration_sec=0.1, content="ok")

    monkeypatch.setattr(plan_routes, "plan_decomposition_jobs", store)
    monkeypatch.setattr(plan_routes, "log_job_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda _plan_id: tree)
    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute_task)
    monkeypatch.setattr(
        plan_routes,
        "_build_plan_execution_snapshot",
        lambda _plan_id, **kwargs: {"active_task_ids": set(), "active_jobs": []},
    )
    monkeypatch.setattr(
        plan_routes,
        "_resolve_effective_task_states",
        lambda _plan_id, _tree, **kwargs: {
            tid: {"effective_status": "pending"} for tid in tree.nodes
        },
    )

    plan_routes._run_full_plan_job(
        job_id="job-3b",
        plan_id=7,
        task_order=[1, 2],
        stop_on_failure=False,
    )

    assert execution_order == [1, 2]
    assert store.failure_calls == []
    assert len(store.success_calls) == 1
    result = store.success_calls[0]["result"]
    assert result["executed_task_ids"] == [1, 2]
    assert result["steps"][0]["status"] == "completed"


def test_run_full_plan_job_does_not_treat_current_job_as_already_running(
    monkeypatch,
) -> None:
    store = _JobStoreStub()
    execution_order: list[int] = []
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(id=1, plan_id=7, name="Step 1", status="pending"),
        },
    )
    tree.rebuild_adjacency()

    def _execute_task(_plan_id: int, task_id: int, **_kwargs):
        execution_order.append(task_id)
        return SimpleNamespace(status="completed", duration_sec=0.1, content="ok")

    def _get_job_payload(job_id: str, include_logs: bool = False):
        return {
            "job_id": job_id,
            "status": "running",
            "mode": "full_plan",
            "stats": {"current_task_id": 1},
        }

    monkeypatch.setattr(plan_routes, "plan_decomposition_jobs", store)
    monkeypatch.setattr(store, "get_job_payload", _get_job_payload, raising=False)
    monkeypatch.setattr(plan_routes, "_list_plan_execute_job_ids", lambda _plan_id, limit=64: ["job-self"])
    monkeypatch.setattr(plan_routes, "log_job_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda _plan_id: tree)
    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute_task)

    plan_routes._run_full_plan_job(
        job_id="job-self",
        plan_id=7,
        task_order=[1],
        stop_on_failure=False,
    )

    assert execution_order == [1]
    assert store.failure_calls == []
    assert len(store.success_calls) == 1
    result = store.success_calls[0]["result"]
    assert result["steps"][0]["status"] == "completed"


def test_run_full_plan_job_reports_overall_progress_with_completed_baseline(
    monkeypatch,
) -> None:
    store = _JobStoreStub()
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(id=1, plan_id=7, name="Step 1", status="pending"),
            2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending"),
        },
    )
    tree.rebuild_adjacency()

    def _execute_task(_plan_id: int, _task_id: int, **_kwargs):
        return SimpleNamespace(status="completed", duration_sec=0.1, content="ok")

    monkeypatch.setattr(plan_routes, "plan_decomposition_jobs", store)
    monkeypatch.setattr(plan_routes, "log_job_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute_task)
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda _plan_id: tree)
    monkeypatch.setattr(
        plan_routes,
        "_resolve_effective_task_states",
        lambda _plan_id, _tree, **kwargs: {
            tid: {"effective_status": "pending"} for tid in tree.nodes
        },
    )

    plan_routes._run_full_plan_job(
        job_id="job-4",
        plan_id=7,
        task_order=[1, 2],
        initial_completed_steps=3,
        overall_total_steps=5,
        stop_on_failure=False,
    )

    assert store.stats_calls[0]["overall_done_steps"] == 3
    assert store.stats_calls[0]["overall_total_steps"] == 5
    assert store.stats_calls[0]["progress_percent"] == 60

    final_stats = store.success_calls[0]["stats"]
    assert final_stats["overall_done_steps"] == 5
    assert final_stats["overall_total_steps"] == 5
    assert final_stats["progress_percent"] == 100


def test_run_full_plan_job_blocks_downstream_tasks_with_failed_dependencies(
    monkeypatch,
) -> None:
    store = _JobStoreStub()
    execution_order: list[int] = []
    persisted_updates: list[dict] = []
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(id=1, plan_id=7, name="Step 1", status="failed"),
            2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending", dependencies=[1]),
            3: PlanNode(id=3, plan_id=7, name="Step 3", status="pending"),
        },
    )
    tree.rebuild_adjacency()

    def _execute_task(_plan_id: int, task_id: int, **_kwargs):
        execution_order.append(task_id)
        return SimpleNamespace(status="completed", duration_sec=0.1, content="ok")

    monkeypatch.setattr(plan_routes, "plan_decomposition_jobs", store)
    monkeypatch.setattr(plan_routes, "log_job_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda _plan_id: tree)
    monkeypatch.setattr(
        plan_routes._plan_repo,
        "update_task",
        lambda plan_id, task_id, **kwargs: persisted_updates.append(
            {"plan_id": plan_id, "task_id": task_id, **kwargs}
        ),
    )
    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute_task)

    plan_routes._run_full_plan_job(
        job_id="job-5",
        plan_id=7,
        task_order=[2, 3],
        stop_on_failure=False,
    )

    assert execution_order == [3]
    assert store.success_calls == []
    assert len(store.failure_calls) == 1
    result = store.failure_calls[0]["result"]
    assert result["executed_task_ids"] == [3]
    assert result["failed_task_ids"] == []
    assert result["skipped_task_ids"] == [2]
    assert result["steps"][0]["task_id"] == 2
    assert result["steps"][0]["status"] == "blocked_by_dependencies"
    assert "#1(failed)" in result["steps"][0]["reason"]
    assert persisted_updates[0]["task_id"] == 2
    assert persisted_updates[0]["status"] == "skipped"
    assert "blocked_by_dependencies" in persisted_updates[0]["execution_result"]


def test_get_plan_tree_exposes_effective_status_and_dependency_block_reason(
    monkeypatch,
) -> None:
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(
                id=1,
                plan_id=7,
                name="Step 1",
                status="running",
                execution_result=json.dumps({"status": "failed", "content": "Error: ConnectError"}),
            ),
            2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending", dependencies=[1]),
        },
    )
    tree.rebuild_adjacency()

    monkeypatch.setattr(
        plan_routes,
        "_load_authorized_plan_tree",
        lambda _plan_id, _request: tree,
    )
    monkeypatch.setattr(
        plan_routes,
        "_build_plan_execution_snapshot",
        lambda _plan_id: {"active_task_ids": set(), "active_jobs": []},
    )

    payload = plan_routes.get_plan_tree(7, _build_request("alice"))

    assert payload["nodes"]["1"]["status"] == "failed"
    assert payload["nodes"]["1"]["effective_status"] == "failed"
    assert payload["nodes"]["2"]["status"] == "blocked"
    assert payload["nodes"]["2"]["blocked_by_dependencies"] is True
    assert payload["nodes"]["2"]["incomplete_dependencies"] == [1]


def test_get_plan_tree_keeps_alias_blocked_tasks_blocked_without_known_producer(
    monkeypatch,
) -> None:
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(
                id=1,
                plan_id=7,
                name="Step 1",
                status="skipped",
                execution_result=json.dumps(
                    {
                        "status": "skipped",
                        "content": "Missing required artifact alias.",
                        "metadata": {
                            "blocked_by_dependencies": True,
                            "missing_artifact_aliases": ["ai_dl.evidence_md"],
                            "incomplete_dependencies": [],
                        },
                    }
                ),
            ),
        },
    )
    tree.rebuild_adjacency()

    monkeypatch.setattr(
        plan_routes,
        "_load_authorized_plan_tree",
        lambda _plan_id, _request: tree,
    )
    monkeypatch.setattr(
        plan_routes,
        "_build_plan_execution_snapshot",
        lambda _plan_id: {"active_task_ids": set(), "active_jobs": []},
    )

    payload = plan_routes.get_plan_tree(7, _build_request("alice"))

    assert payload["nodes"]["1"]["status"] == "blocked"
    assert payload["nodes"]["1"]["effective_status"] == "blocked"
    assert payload["nodes"]["1"]["blocked_by_dependencies"] is True
    assert "ai_dl.evidence_md" in payload["nodes"]["1"]["status_reason"]


def test_get_plan_tree_marks_false_completed_producer_failed_when_canonical_publish_missing(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    # Create a manifest with at least one entry so artifact tracking is
    # considered active.  Without any manifest the resolver gracefully skips
    # publish-contract checks (plans executed via DeepThink may never
    # initialise a manifest).
    save_artifact_manifest(7, {
        "plan_id": 7,
        "artifacts": {
            "other.placeholder": {
                "alias": "other.placeholder",
                "path": "/tmp/placeholder",
                "producer_task_id": 999,
            }
        },
    })
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(
                id=1,
                plan_id=7,
                name="Step 1",
                status="completed",
                metadata={"artifact_contract": {"publishes": ["general.evidence_md"]}},
                execution_result=json.dumps({"status": "completed", "content": "ok"}),
            ),
        },
    )
    tree.rebuild_adjacency()

    monkeypatch.setattr(
        plan_routes,
        "_load_authorized_plan_tree",
        lambda _plan_id, _request: tree,
    )
    monkeypatch.setattr(
        plan_routes,
        "_build_plan_execution_snapshot",
        lambda _plan_id: {"active_task_ids": set(), "active_jobs": []},
    )

    payload = plan_routes.get_plan_tree(7, _build_request("alice"))

    assert payload["nodes"]["1"]["effective_status"] == "failed"
    assert payload["nodes"]["1"]["status_reason"].startswith("Completion contract unsatisfied")


def test_get_plan_tree_preserves_retryable_skipped_status_and_summary(
    monkeypatch,
) -> None:
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(
                id=1,
                plan_id=7,
                name="Step 1",
                status="skipped",
                execution_result=json.dumps(
                    {
                        "status": "skipped",
                        "content": "Upstream temporary issue.",
                        "metadata": {"blocked_by_dependencies": False},
                    }
                ),
            ),
        },
    )
    tree.rebuild_adjacency()

    monkeypatch.setattr(
        plan_routes,
        "_load_authorized_plan_tree",
        lambda _plan_id, _request: tree,
    )
    monkeypatch.setattr(
        plan_routes,
        "_build_plan_execution_snapshot",
        lambda _plan_id: {"active_task_ids": set(), "active_jobs": []},
    )

    payload = plan_routes.get_plan_tree(7, _build_request("alice"))
    summary = plan_routes.get_plan_execution_summary(7, _build_request("alice"))

    assert payload["nodes"]["1"]["status"] == "skipped"
    assert payload["nodes"]["1"]["effective_status"] == "skipped"
    assert summary.skipped == 1
    assert summary.pending == 0


def test_execute_full_plan_reruns_false_completed_tasks_with_retry_text(
    monkeypatch,
) -> None:
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(
                id=1,
                plan_id=7,
                name="Step 1",
                status="completed",
                execution_result=json.dumps(
                    {
                        "status": "completed",
                        "content": "Let me create the structured evidence file first, then retry with the proper context files",
                    }
                ),
            ),
            2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending", dependencies=[1]),
        },
    )
    tree.rebuild_adjacency()
    executed: list[int] = []

    def _execute_task(_plan_id: int, task_id: int, **_kwargs):
        executed.append(task_id)
        tree.nodes[task_id].status = "completed"
        tree.nodes[task_id].execution_result = json.dumps({"status": "completed", "content": "ok"})
        return SimpleNamespace(status="completed", duration_sec=0.1, content="ok")

    monkeypatch.setattr(
        plan_routes,
        "_load_authorized_plan_tree",
        lambda _plan_id, _request: tree,
    )
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda _plan_id: tree)
    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute_task)
    monkeypatch.setattr(
        plan_routes,
        "_build_plan_execution_snapshot",
        lambda _plan_id: {"active_task_ids": set(), "active_jobs": []},
    )

    response = plan_routes.execute_full_plan(
        7,
        _build_request("alice"),
        plan_routes.ExecuteFullPlanRequest(async_mode=False, stop_on_failure=False),
    )

    assert response.success is True
    assert executed == [1, 2]
    assert response.result["executed_task_ids"] == [1, 2]


def test_execute_full_plan_reruns_false_completed_tasks_with_missing_publish_contract(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    # Seed a manifest with a placeholder so artifact tracking is active and
    # the resolver can detect task 1 as "false completed" (publish contract
    # unsatisfied).
    save_artifact_manifest(7, {
        "plan_id": 7,
        "artifacts": {
            "other.placeholder": {
                "alias": "other.placeholder",
                "path": "/tmp/placeholder",
                "producer_task_id": 999,
            }
        },
    })
    tree = PlanTree(
        id=7,
        title="Plan 7",
        nodes={
            1: PlanNode(
                id=1,
                plan_id=7,
                name="Step 1",
                status="completed",
                metadata={"artifact_contract": {"publishes": ["general.evidence_md"]}},
                execution_result=json.dumps({"status": "completed", "content": "ok"}),
            ),
            2: PlanNode(id=2, plan_id=7, name="Step 2", status="pending", dependencies=[1]),
        },
    )
    tree.rebuild_adjacency()
    executed: list[int] = []

    def _execute_task(_plan_id: int, task_id: int, **_kwargs):
        executed.append(task_id)
        tree.nodes[task_id].status = "completed"
        tree.nodes[task_id].execution_result = json.dumps({"status": "completed", "content": "ok"})
        if task_id == 1:
            alias = "general.evidence_md"
            canonical = canonical_artifact_path(7, alias)
            assert canonical is not None
            canonical.parent.mkdir(parents=True, exist_ok=True)
            canonical.write_text("evidence", encoding="utf-8")
            save_artifact_manifest(
                7,
                {
                    "plan_id": 7,
                    "artifacts": {
                        alias: {
                            "alias": alias,
                            "path": str(canonical.resolve()),
                            "producer_task_id": 1,
                            "source_path": str(canonical.resolve()),
                        }
                    },
                },
            )
        return SimpleNamespace(status="completed", duration_sec=0.1, content="ok")

    monkeypatch.setattr(
        plan_routes,
        "_load_authorized_plan_tree",
        lambda _plan_id, _request: tree,
    )
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda _plan_id: tree)
    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute_task)
    monkeypatch.setattr(
        plan_routes,
        "_build_plan_execution_snapshot",
        lambda _plan_id: {"active_task_ids": set(), "active_jobs": []},
    )

    response = plan_routes.execute_full_plan(
        7,
        _build_request("alice"),
        plan_routes.ExecuteFullPlanRequest(async_mode=False, stop_on_failure=False),
    )

    assert response.success is True
    assert executed == [1, 2]
    assert response.result["executed_task_ids"] == [1, 2]
