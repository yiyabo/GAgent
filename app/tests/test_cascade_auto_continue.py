"""Tests for the task cascade auto-continue loop in action_execution.

When a user executes a composite task (e.g. "执行任务8"), the system
expands it to leaf tasks [34, 35, 36, …] stored in ``pending_scope_task_ids``.
The cascade loop in ``_execute_action_run`` should execute them sequentially
without requiring additional user messages.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from app.routers.chat import action_execution
from app.routers.chat.models import AgentResult, AgentStep
from app.services.llm.structured_response import (
    LLMAction,
    LLMReply,
    LLMStructuredResponse,
)


# ── Stubs (same pattern as test_action_execution_auto_deep_think_retry) ──


class _PlanSessionStub:
    def __init__(self, repo: Any = None, plan_id: Optional[int] = None) -> None:
        self.repo = repo
        self.plan_id = plan_id

    def refresh(self) -> None:
        return

    def detach(self) -> None:
        return


class _JobStoreStub:
    def __init__(self) -> None:
        self._job: Optional[SimpleNamespace] = None
        self.logs: List[Dict[str, Any]] = []
        self.mark_success_calls: List[Dict[str, Any]] = []
        self.mark_failure_calls: List[Dict[str, Any]] = []

    def create_job(self, **kwargs):
        job_id = kwargs.get("job_id") or "job_stub"
        job_type = kwargs.get("job_type") or "chat_action"
        self._job = SimpleNamespace(job_id=job_id, job_type=job_type)
        return self._job

    def get_job(self, _job_id: str):
        return self._job

    def attach_plan(self, _job_id: str, _plan_id: int) -> None:
        return

    def append_log(self, _job_id: str, level: str, message: str, payload=None) -> None:
        self.logs.append({"level": level, "message": message, "payload": payload})

    def update_stats(self, _job_id: str, _stats: Dict[str, Any]) -> None:
        return

    def mark_running(self, _job_id: str) -> None:
        return

    def mark_failure(self, _job_id: str, error: str, **kwargs) -> None:
        p = dict(kwargs)
        p["error"] = error
        self.mark_failure_calls.append(p)

    def mark_success(self, _job_id: str, **kwargs) -> None:
        self.mark_success_calls.append(dict(kwargs))

    def get_job_payload(self, _job_id: str) -> Dict[str, Any]:
        return {"status": "running"}


# ── Helpers ──────────────────────────────────────────────────────


def _ok_step(task_id: int) -> AgentStep:
    return AgentStep(
        action=LLMAction(
            kind="task_operation",
            name="rerun_task",
            parameters={"task_id": task_id},
            blocking=True,
            order=1,
        ),
        success=True,
        message=f"Task [{task_id}] execution status: completed.",
        details={"task_id": task_id, "status": "completed"},
    )


def _fail_step(task_id: int) -> AgentStep:
    return AgentStep(
        action=LLMAction(
            kind="task_operation",
            name="rerun_task",
            parameters={"task_id": task_id},
            blocking=True,
            order=1,
        ),
        success=False,
        message=f"Task [{task_id}] failed.",
        details={"task_id": task_id, "status": "failed"},
    )


def _ok_result(task_id: int) -> AgentResult:
    return AgentResult(
        reply=f"Task {task_id} done",
        steps=[_ok_step(task_id)],
        suggestions=[],
        primary_intent=None,
        success=True,
    )


def _fail_result(task_id: int) -> AgentResult:
    return AgentResult(
        reply=f"Task {task_id} failed",
        steps=[_fail_step(task_id)],
        suggestions=[],
        primary_intent=None,
        success=False,
        errors=[f"Task {task_id} failed"],
    )


def _build_rerun_task_record(
    run_id: str,
    first_task_id: int,
    pending_task_ids: List[int],
) -> Dict[str, Any]:
    """Build an action run record for a single rerun_task action."""
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message=f"Execute task {first_task_id}"),
        actions=[
            LLMAction(
                kind="task_operation",
                name="rerun_task",
                parameters={"task_id": first_task_id},
                order=1,
                blocking=True,
            )
        ],
    )
    return {
        "id": run_id,
        "plan_id": 68,
        "session_id": None,
        "mode": "assistant",
        "structured_json": structured.model_dump_json(),
        "context": {
            "current_task_id": first_task_id,
            "task_id": first_task_id,
            "pending_scope_task_ids": list(pending_task_ids),
        },
        "history": [],
        "user_message": "执行任务8",
    }


def _patch_cascade(
    monkeypatch,
    *,
    run_id: str,
    first_task_id: int,
    pending_task_ids: List[int],
    results_by_task: Dict[int, AgentResult],
):
    """Wire up monkeypatches for cascade testing.

    ``results_by_task`` maps task_id → AgentResult that execute_structured
    should return for that task.
    """
    record = _build_rerun_task_record(run_id, first_task_id, pending_task_ids)
    updates: List[Dict[str, Any]] = []
    job_store = _JobStoreStub()

    call_log: List[int] = []  # track which tasks were executed

    class _StructuredAgentStub:
        def __init__(self, **kwargs) -> None:
            self.extra_context = dict(kwargs.get("extra_context") or {})
            self.plan_session = kwargs.get("plan_session") or _PlanSessionStub()
            self.llm_service = object()

        async def execute_structured(self, structured: LLMStructuredResponse):
            actions = structured.sorted_actions()
            task_id = None
            for a in actions:
                if a.name == "rerun_task":
                    task_id = a.parameters.get("task_id")
                    break
            if task_id is not None:
                call_log.append(task_id)
            return results_by_task.get(task_id, _ok_result(task_id or 0))

    async def _fake_analysis(*_args, **_kwargs):
        return "analysis-stub"

    async def _no_retry(*_args, **_kwargs):
        return {"attempted": False, "success": False, "error": "disabled in test"}

    monkeypatch.setattr(action_execution, "PlanSession", _PlanSessionStub)
    monkeypatch.setattr(
        action_execution,
        "get_structured_chat_agent_cls",
        lambda: _StructuredAgentStub,
    )
    monkeypatch.setattr(
        action_execution,
        "fetch_action_run",
        lambda _id: record if _id == run_id else None,
    )
    monkeypatch.setattr(
        action_execution,
        "update_action_run",
        lambda _id, **kwargs: updates.append(kwargs),
    )
    monkeypatch.setattr(action_execution, "plan_decomposition_jobs", job_store)
    monkeypatch.setattr(action_execution, "set_current_job", lambda _: None)
    monkeypatch.setattr(action_execution, "reset_current_job", lambda _: None)
    monkeypatch.setattr(
        action_execution,
        "_update_message_content_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution,
        "_update_message_metadata_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution, "_set_session_plan_id", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        action_execution, "_generate_action_analysis", _fake_analysis
    )
    monkeypatch.setattr(
        action_execution,
        "_run_blocking_failure_deep_think_retry_once",
        _no_retry,
    )
    monkeypatch.setattr(
        action_execution, "_generate_tool_analysis", _fake_analysis
    )

    return updates, job_store, call_log


# ── Tests ────────────────────────────────────────────────────────


def test_cascade_runs_all_pending_tasks(monkeypatch) -> None:
    """Cascade should execute all pending tasks when each succeeds."""
    run_id = "run_cascade_all"
    results = {
        34: _ok_result(34),
        35: _ok_result(35),
        36: _ok_result(36),
        37: _ok_result(37),
    }
    updates, job_store, call_log = _patch_cascade(
        monkeypatch,
        run_id=run_id,
        first_task_id=34,
        pending_task_ids=[35, 36, 37],
        results_by_task=results,
    )

    asyncio.run(action_execution._execute_action_run(run_id))

    # All 4 tasks should have been executed
    assert call_log == [34, 35, 36, 37]

    # Final update should be completed
    final = updates[-1]
    assert final["status"] == "completed"

    # Result should contain steps from all tasks
    result_payload = final["result"]
    assert result_payload["success"] is True
    assert len(result_payload["steps"]) == 4

    # Job store should record success
    assert job_store.mark_success_calls
    assert not job_store.mark_failure_calls

    # Cascade logs should be present
    cascade_logs = [
        log for log in job_store.logs if "[CASCADE]" in log["message"]
    ]
    assert len(cascade_logs) >= 3  # at least 3 auto-continue + 1 summary


def test_cascade_continues_after_failure(monkeypatch) -> None:
    """Cascade continues executing independent tasks after a failure.

    Without a plan tree for dependency checking, all remaining tasks
    are attempted. Failed tasks are tracked in cascade_failed_ids.
    """
    run_id = "run_cascade_fail"
    results = {
        34: _ok_result(34),
        35: _fail_result(35),  # This one fails
        36: _ok_result(36),  # Independent — should still execute
    }
    updates, job_store, call_log = _patch_cascade(
        monkeypatch,
        run_id=run_id,
        first_task_id=34,
        pending_task_ids=[35, 36],
        results_by_task=results,
    )

    asyncio.run(action_execution._execute_action_run(run_id))

    # All 3 tasks should be executed (35 fails, 36 still runs)
    assert call_log == [34, 35, 36]

    # Final status should be failed (task 35 failed)
    final = updates[-1]
    assert final["status"] == "failed"

    # Result should contain steps from all tasks
    result_payload = final["result"]
    assert result_payload["success"] is False

    # Cascade summary log should show 1 failed
    cascade_summary_logs = [
        log for log in job_store.logs
        if "[CASCADE] Completed" in log["message"]
    ]
    assert len(cascade_summary_logs) == 1
    summary_payload = cascade_summary_logs[0]["payload"]
    assert summary_payload["failed_ids"] == [35]
    assert summary_payload["succeeded"] >= 1


def test_cascade_skips_when_no_pending(monkeypatch) -> None:
    """No cascade when pending_scope_task_ids is empty."""
    run_id = "run_no_cascade"
    results = {34: _ok_result(34)}
    updates, job_store, call_log = _patch_cascade(
        monkeypatch,
        run_id=run_id,
        first_task_id=34,
        pending_task_ids=[],  # No pending
        results_by_task=results,
    )

    asyncio.run(action_execution._execute_action_run(run_id))

    # Only the first task executed
    assert call_log == [34]

    # No cascade logs
    cascade_logs = [
        log for log in job_store.logs if "[CASCADE]" in log["message"]
    ]
    assert len(cascade_logs) == 0


def test_cascade_skips_for_non_rerun_action(monkeypatch) -> None:
    """Cascade should not trigger for tool_operation actions."""
    run_id = "run_tool_no_cascade"
    tool_action = LLMAction(
        kind="tool_operation",
        name="code_executor",
        parameters={"code": "print('hello')"},
        blocking=True,
        order=1,
    )
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="run code"),
        actions=[tool_action],
    )
    record = {
        "id": run_id,
        "plan_id": 68,
        "session_id": None,
        "mode": "assistant",
        "structured_json": structured.model_dump_json(),
        "context": {
            "pending_scope_task_ids": [35, 36, 37],
        },
        "history": [],
        "user_message": "run code",
    }

    updates: List[Dict[str, Any]] = []
    job_store = _JobStoreStub()

    class _AgentStub:
        def __init__(self, **kwargs):
            self.extra_context = dict(kwargs.get("extra_context") or {})
            self.plan_session = kwargs.get("plan_session") or _PlanSessionStub()
            self.llm_service = object()

        async def execute_structured(self, _structured):
            return AgentResult(
                reply="code done",
                steps=[
                    AgentStep(
                        action=tool_action,
                        success=True,
                        message="ok",
                        details={},
                    )
                ],
                suggestions=[],
                primary_intent=None,
                success=True,
            )

    async def _fake_analysis(*_args, **_kwargs):
        return "analysis"

    monkeypatch.setattr(action_execution, "PlanSession", _PlanSessionStub)
    monkeypatch.setattr(
        action_execution, "get_structured_chat_agent_cls", lambda: _AgentStub
    )
    monkeypatch.setattr(
        action_execution,
        "fetch_action_run",
        lambda _id: record if _id == run_id else None,
    )
    monkeypatch.setattr(
        action_execution,
        "update_action_run",
        lambda _id, **kwargs: updates.append(kwargs),
    )
    monkeypatch.setattr(action_execution, "plan_decomposition_jobs", job_store)
    monkeypatch.setattr(action_execution, "set_current_job", lambda _: None)
    monkeypatch.setattr(action_execution, "reset_current_job", lambda _: None)
    monkeypatch.setattr(
        action_execution,
        "_update_message_content_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution,
        "_update_message_metadata_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution, "_set_session_plan_id", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        action_execution, "_generate_action_analysis", _fake_analysis
    )

    async def _no_retry(*_a, **_k):
        return {"attempted": False, "success": False}

    monkeypatch.setattr(
        action_execution,
        "_run_blocking_failure_deep_think_retry_once",
        _no_retry,
    )
    monkeypatch.setattr(
        action_execution, "_generate_tool_analysis", _fake_analysis
    )

    asyncio.run(action_execution._execute_action_run(run_id))

    # No cascade should have occurred
    cascade_logs = [
        log for log in job_store.logs if "[CASCADE]" in log["message"]
    ]
    assert len(cascade_logs) == 0


def test_cascade_respects_max_limit(monkeypatch) -> None:
    """Cascade stops after _CASCADE_MAX_TASKS even if pending tasks remain."""
    monkeypatch.setattr(action_execution, "_CASCADE_MAX_TASKS", 3)

    run_id = "run_cascade_limit"
    all_ids = list(range(100, 110))  # 10 tasks: 100..109
    results = {tid: _ok_result(tid) for tid in all_ids}
    updates, job_store, call_log = _patch_cascade(
        monkeypatch,
        run_id=run_id,
        first_task_id=100,
        pending_task_ids=list(all_ids[1:]),  # [101, 102, ..., 109]
        results_by_task=results,
    )

    asyncio.run(action_execution._execute_action_run(run_id))

    # First task + 3 cascade = 4 total
    assert call_log == [100, 101, 102, 103]

    # Remaining tasks [104..109] not executed
    assert 104 not in call_log


def test_cascade_updates_context(monkeypatch) -> None:
    """After cascade, agent extra_context reflects the last executed task."""
    run_id = "run_cascade_ctx"

    context_snapshots: List[Dict[str, Any]] = []

    results = {
        34: _ok_result(34),
        35: _ok_result(35),
        36: _ok_result(36),
    }

    record = _build_rerun_task_record(run_id, 34, [35, 36])
    updates: List[Dict[str, Any]] = []
    job_store = _JobStoreStub()

    class _ContextTrackingAgent:
        def __init__(self, **kwargs):
            self.extra_context = dict(kwargs.get("extra_context") or {})
            self.plan_session = kwargs.get("plan_session") or _PlanSessionStub()
            self.llm_service = object()

        async def execute_structured(self, structured: LLMStructuredResponse):
            actions = structured.sorted_actions()
            task_id = None
            for a in actions:
                if a.name == "rerun_task":
                    task_id = a.parameters.get("task_id")
                    break
            # Record context snapshot at execution time
            context_snapshots.append({
                "current_task_id": self.extra_context.get("current_task_id"),
                "task_id_param": task_id,
                "pending": list(self.extra_context.get("pending_scope_task_ids", [])),
            })
            return results.get(task_id, _ok_result(task_id or 0))

    async def _fake_analysis(*_args, **_kwargs):
        return "analysis"

    monkeypatch.setattr(action_execution, "PlanSession", _PlanSessionStub)
    monkeypatch.setattr(
        action_execution,
        "get_structured_chat_agent_cls",
        lambda: _ContextTrackingAgent,
    )
    monkeypatch.setattr(
        action_execution,
        "fetch_action_run",
        lambda _id: record if _id == run_id else None,
    )
    monkeypatch.setattr(
        action_execution,
        "update_action_run",
        lambda _id, **kwargs: updates.append(kwargs),
    )
    monkeypatch.setattr(action_execution, "plan_decomposition_jobs", job_store)
    monkeypatch.setattr(action_execution, "set_current_job", lambda _: None)
    monkeypatch.setattr(action_execution, "reset_current_job", lambda _: None)
    monkeypatch.setattr(
        action_execution,
        "_update_message_content_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution,
        "_update_message_metadata_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution, "_set_session_plan_id", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        action_execution, "_generate_action_analysis", _fake_analysis
    )

    async def _no_retry(*_a, **_k):
        return {"attempted": False, "success": False}

    monkeypatch.setattr(
        action_execution,
        "_run_blocking_failure_deep_think_retry_once",
        _no_retry,
    )
    monkeypatch.setattr(
        action_execution, "_generate_tool_analysis", _fake_analysis
    )

    asyncio.run(action_execution._execute_action_run(run_id))

    # 3 executions: task 34 (initial), task 35 (cascade), task 36 (cascade)
    assert len(context_snapshots) == 3

    # First call: context from the record
    assert context_snapshots[0]["current_task_id"] == 34
    assert context_snapshots[0]["task_id_param"] == 34
    assert context_snapshots[0]["pending"] == [35, 36]

    # Second call: context updated by cascade loop
    assert context_snapshots[1]["current_task_id"] == 35
    assert context_snapshots[1]["task_id_param"] == 35
    assert context_snapshots[1]["pending"] == [36]

    # Third call: last task
    assert context_snapshots[2]["current_task_id"] == 36
    assert context_snapshots[2]["task_id_param"] == 36
    assert context_snapshots[2]["pending"] == []


# ── New tests for cascade resilience features ────────────────────


class _PlanTreeStub:
    """Minimal plan tree stub for dependency-based skip tests."""

    def __init__(self, deps_map: Dict[int, List[int]]) -> None:
        # deps_map: {task_id: [dependency_ids]}
        self._deps = deps_map

    def get_node(self, task_id: int):
        return SimpleNamespace(dependencies=self._deps.get(task_id, []))


class _PlanSessionWithTree(_PlanSessionStub):
    """PlanSession stub that exposes a current_tree()."""

    def __init__(self, deps_map: Dict[int, List[int]], **kwargs) -> None:
        super().__init__(**kwargs)
        self._tree = _PlanTreeStub(deps_map)

    def current_tree(self):
        return self._tree


def test_cascade_retries_on_exception(monkeypatch) -> None:
    """Exception during execute_structured should trigger retry.

    First attempt raises, second succeeds → task marked as success.
    asyncio.sleep must be patched to avoid real delays.
    """
    monkeypatch.setattr(action_execution, "asyncio", _make_async_stub())

    run_id = "run_cascade_retry"
    attempt_counts: Dict[int, int] = {}

    class _RetryAgent:
        def __init__(self, **kwargs):
            self.extra_context = dict(kwargs.get("extra_context") or {})
            self.plan_session = kwargs.get("plan_session") or _PlanSessionStub()
            self.llm_service = object()

        async def execute_structured(self, structured: LLMStructuredResponse):
            actions = structured.sorted_actions()
            task_id = None
            for a in actions:
                if a.name == "rerun_task":
                    task_id = a.parameters.get("task_id")
                    break
            if task_id is None:
                return _ok_result(0)

            attempt_counts[task_id] = attempt_counts.get(task_id, 0) + 1

            if task_id == 35 and attempt_counts[task_id] == 1:
                raise ConnectionError("transient network error")

            return _ok_result(task_id)

    record = _build_rerun_task_record(run_id, 34, [35])
    updates: List[Dict[str, Any]] = []
    job_store = _JobStoreStub()

    async def _fake_analysis(*_args, **_kwargs):
        return "analysis"

    async def _no_retry(*_a, **_k):
        return {"attempted": False, "success": False}

    monkeypatch.setattr(action_execution, "PlanSession", _PlanSessionStub)
    monkeypatch.setattr(
        action_execution, "get_structured_chat_agent_cls", lambda: _RetryAgent
    )
    monkeypatch.setattr(
        action_execution, "fetch_action_run",
        lambda _id: record if _id == run_id else None,
    )
    monkeypatch.setattr(
        action_execution, "update_action_run",
        lambda _id, **kwargs: updates.append(kwargs),
    )
    monkeypatch.setattr(action_execution, "plan_decomposition_jobs", job_store)
    monkeypatch.setattr(action_execution, "set_current_job", lambda _: None)
    monkeypatch.setattr(action_execution, "reset_current_job", lambda _: None)
    monkeypatch.setattr(
        action_execution, "_update_message_content_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution, "_update_message_metadata_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution, "_set_session_plan_id", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        action_execution, "_generate_action_analysis", _fake_analysis
    )
    monkeypatch.setattr(
        action_execution, "_run_blocking_failure_deep_think_retry_once", _no_retry
    )
    monkeypatch.setattr(
        action_execution, "_generate_tool_analysis", _fake_analysis
    )

    asyncio.run(action_execution._execute_action_run(run_id))

    # Task 35 should have been called twice (1 fail + 1 retry success)
    assert attempt_counts[35] == 2

    # Final result should be success
    final = updates[-1]
    assert final["status"] == "completed"
    assert final["result"]["success"] is True

    # Retry log should be present
    retry_logs = [
        log for log in job_store.logs if "Retrying" in log["message"]
    ]
    assert len(retry_logs) >= 1


def test_cascade_dependency_skip(monkeypatch) -> None:
    """Task depending on a failed task should be skipped.

    Tasks: 34→OK, 35→FAIL, 36 depends on 35 → SKIP, 37 independent → OK.
    """
    monkeypatch.setattr(action_execution, "asyncio", _make_async_stub())

    run_id = "run_dep_skip"
    deps_map = {
        34: [],
        35: [34],
        36: [35],  # depends on 35 which will fail
        37: [34],  # depends only on 34 which succeeds
    }
    results = {
        34: _ok_result(34),
        35: _fail_result(35),
        36: _ok_result(36),  # wouldn't reach anyway
        37: _ok_result(37),
    }

    record = _build_rerun_task_record(run_id, 34, [35, 36, 37])
    updates: List[Dict[str, Any]] = []
    job_store = _JobStoreStub()
    call_log: List[int] = []

    plan_session = _PlanSessionWithTree(deps_map)

    class _DepAgent:
        def __init__(self, **kwargs):
            self.extra_context = dict(kwargs.get("extra_context") or {})
            self.plan_session = kwargs.get("plan_session") or plan_session
            self.llm_service = object()

        async def execute_structured(self, structured: LLMStructuredResponse):
            task_id = None
            for a in structured.sorted_actions():
                if a.name == "rerun_task":
                    task_id = a.parameters.get("task_id")
                    break
            if task_id is not None:
                call_log.append(task_id)
            return results.get(task_id, _ok_result(task_id or 0))

    # Override the agent class to inject the tree-aware plan session
    class _DepAgentFactory:
        @staticmethod
        def __call__(**kwargs):
            # Force plan_session to our stub with tree
            kwargs["plan_session"] = plan_session
            return _DepAgent(**kwargs)

    async def _fake_analysis(*_args, **_kwargs):
        return "analysis"

    async def _no_retry(*_a, **_k):
        return {"attempted": False, "success": False}

    monkeypatch.setattr(action_execution, "PlanSession", lambda **_k: plan_session)
    monkeypatch.setattr(
        action_execution, "get_structured_chat_agent_cls",
        lambda: _DepAgentFactory(),
    )
    monkeypatch.setattr(
        action_execution, "fetch_action_run",
        lambda _id: record if _id == run_id else None,
    )
    monkeypatch.setattr(
        action_execution, "update_action_run",
        lambda _id, **kwargs: updates.append(kwargs),
    )
    monkeypatch.setattr(action_execution, "plan_decomposition_jobs", job_store)
    monkeypatch.setattr(action_execution, "set_current_job", lambda _: None)
    monkeypatch.setattr(action_execution, "reset_current_job", lambda _: None)
    monkeypatch.setattr(
        action_execution, "_update_message_content_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution, "_update_message_metadata_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution, "_set_session_plan_id", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        action_execution, "_generate_action_analysis", _fake_analysis
    )
    monkeypatch.setattr(
        action_execution, "_run_blocking_failure_deep_think_retry_once", _no_retry
    )
    monkeypatch.setattr(
        action_execution, "_generate_tool_analysis", _fake_analysis
    )

    asyncio.run(action_execution._execute_action_run(run_id))

    # Task 36 should be SKIPPED (depends on failed 35), 37 should execute
    assert 34 in call_log
    assert 35 in call_log
    assert 36 not in call_log  # skipped
    assert 37 in call_log  # independent, still runs

    # Cascade summary should show skip
    summary_logs = [
        log for log in job_store.logs if "[CASCADE] Completed" in log["message"]
    ]
    assert len(summary_logs) == 1
    payload = summary_logs[0]["payload"]
    assert 36 in payload["skipped_ids"]
    assert 35 in payload["failed_ids"]


def test_cascade_transitive_skip(monkeypatch) -> None:
    """Transitive dependency skip: A fails → B skipped → C skipped (C depends on B).
    """
    monkeypatch.setattr(action_execution, "asyncio", _make_async_stub())

    run_id = "run_trans_skip"
    deps_map = {
        10: [],
        11: [10],
        12: [11],   # depends on 11 (which depends on failed 10→won't help)
        13: [12],   # depends on 12 (transitive skip)
    }
    results = {
        10: _ok_result(10),
        11: _fail_result(11),
        12: _ok_result(12),
        13: _ok_result(13),
    }

    record = _build_rerun_task_record(run_id, 10, [11, 12, 13])
    updates: List[Dict[str, Any]] = []
    job_store = _JobStoreStub()
    call_log: List[int] = []

    plan_session = _PlanSessionWithTree(deps_map)

    class _TransAgent:
        def __init__(self, **kwargs):
            self.extra_context = dict(kwargs.get("extra_context") or {})
            self.plan_session = kwargs.get("plan_session") or plan_session
            self.llm_service = object()

        async def execute_structured(self, structured: LLMStructuredResponse):
            task_id = None
            for a in structured.sorted_actions():
                if a.name == "rerun_task":
                    task_id = a.parameters.get("task_id")
                    break
            if task_id is not None:
                call_log.append(task_id)
            return results.get(task_id, _ok_result(task_id or 0))

    class _TransFactory:
        @staticmethod
        def __call__(**kwargs):
            kwargs["plan_session"] = plan_session
            return _TransAgent(**kwargs)

    async def _fake(*_a, **_k):
        return "analysis"

    async def _no_retry(*_a, **_k):
        return {"attempted": False, "success": False}

    monkeypatch.setattr(action_execution, "PlanSession", lambda **_k: plan_session)
    monkeypatch.setattr(
        action_execution, "get_structured_chat_agent_cls", lambda: _TransFactory()
    )
    monkeypatch.setattr(
        action_execution, "fetch_action_run",
        lambda _id: record if _id == run_id else None,
    )
    monkeypatch.setattr(
        action_execution, "update_action_run",
        lambda _id, **kwargs: updates.append(kwargs),
    )
    monkeypatch.setattr(action_execution, "plan_decomposition_jobs", job_store)
    monkeypatch.setattr(action_execution, "set_current_job", lambda _: None)
    monkeypatch.setattr(action_execution, "reset_current_job", lambda _: None)
    monkeypatch.setattr(
        action_execution, "_update_message_content_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution, "_update_message_metadata_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution, "_set_session_plan_id", lambda *_a, **_k: None
    )
    monkeypatch.setattr(action_execution, "_generate_action_analysis", _fake)
    monkeypatch.setattr(
        action_execution, "_run_blocking_failure_deep_think_retry_once", _no_retry
    )
    monkeypatch.setattr(action_execution, "_generate_tool_analysis", _fake)

    asyncio.run(action_execution._execute_action_run(run_id))

    # 11 fails, 12 depends on 11 → skipped, 13 depends on 12 → skipped
    assert 10 in call_log
    assert 11 in call_log
    assert 12 not in call_log  # dependency skip
    assert 13 not in call_log  # transitive skip

    summary = [
        log for log in job_store.logs if "[CASCADE] Completed" in log["message"]
    ]
    assert len(summary) == 1
    payload = summary[0]["payload"]
    assert sorted(payload["skipped_ids"]) == [12, 13]
    assert payload["failed_ids"] == [11]


def _make_async_stub():
    """Create a module-like stub for asyncio with a no-op sleep."""
    import types

    stub = types.ModuleType("asyncio_stub")

    async def _noop_sleep(_seconds):
        pass

    stub.sleep = _noop_sleep
    return stub


def test_cascade_phase_ordering(monkeypatch) -> None:
    """Cascade should reorder pending tasks by topological phases.

    DAG: 1→3, 2→3, 3→4  (tasks 1,2 are phase 0; 3 is phase 1; 4 is phase 2)
    If pending is given as [4, 3, 2] (wrong order), the TodoList integration
    should reorder to [2, 3, 4] (phase order).
    """
    from app.services.plans.plan_models import PlanNode, PlanTree

    monkeypatch.setattr(action_execution, "asyncio", _make_async_stub())

    run_id = "run_phase_order"
    nodes = {
        1: PlanNode(id=1, plan_id=1, name="Fetch", dependencies=[]),
        2: PlanNode(id=2, plan_id=1, name="Download", dependencies=[]),
        3: PlanNode(id=3, plan_id=1, name="Merge", dependencies=[1, 2]),
        4: PlanNode(id=4, plan_id=1, name="Analyze", dependencies=[3]),
    }
    tree = PlanTree(id=1, title="Test", nodes=nodes)
    tree.rebuild_adjacency()

    results = {tid: _ok_result(tid) for tid in [1, 2, 3, 4]}
    call_log: List[int] = []

    class _PhaseTreeSession(_PlanSessionStub):
        def current_tree(self):
            return tree

    plan_session = _PhaseTreeSession()

    class _PhaseAgent:
        def __init__(self, **kwargs):
            self.extra_context = dict(kwargs.get("extra_context") or {})
            self.plan_session = kwargs.get("plan_session") or plan_session
            self.llm_service = object()

        async def execute_structured(self, structured: LLMStructuredResponse):
            task_id = None
            for a in structured.sorted_actions():
                if a.name == "rerun_task":
                    task_id = a.parameters.get("task_id")
                    break
            if task_id is not None:
                call_log.append(task_id)
            return results.get(task_id, _ok_result(task_id or 0))

    class _PhaseFactory:
        @staticmethod
        def __call__(**kwargs):
            kwargs["plan_session"] = plan_session
            return _PhaseAgent(**kwargs)

    # Pending in WRONG order — should be reordered by phases
    record = _build_rerun_task_record(run_id, 1, [4, 3, 2])
    updates: List[Dict[str, Any]] = []
    job_store = _JobStoreStub()

    async def _fake(*_a, **_k):
        return "analysis"

    async def _no_retry(*_a, **_k):
        return {"attempted": False, "success": False}

    monkeypatch.setattr(action_execution, "PlanSession", lambda **_k: plan_session)
    monkeypatch.setattr(
        action_execution, "get_structured_chat_agent_cls", lambda: _PhaseFactory()
    )
    monkeypatch.setattr(
        action_execution, "fetch_action_run",
        lambda _id: record if _id == run_id else None,
    )
    monkeypatch.setattr(
        action_execution, "update_action_run",
        lambda _id, **kwargs: updates.append(kwargs),
    )
    monkeypatch.setattr(action_execution, "plan_decomposition_jobs", job_store)
    monkeypatch.setattr(action_execution, "set_current_job", lambda _: None)
    monkeypatch.setattr(action_execution, "reset_current_job", lambda _: None)
    monkeypatch.setattr(
        action_execution, "_update_message_content_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution, "_update_message_metadata_by_tracking",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        action_execution, "_set_session_plan_id", lambda *_a, **_k: None
    )
    monkeypatch.setattr(action_execution, "_generate_action_analysis", _fake)
    monkeypatch.setattr(
        action_execution, "_run_blocking_failure_deep_think_retry_once", _no_retry
    )
    monkeypatch.setattr(action_execution, "_generate_tool_analysis", _fake)

    asyncio.run(action_execution._execute_action_run(run_id))

    # Task 1 is the first (from record), then the pending should be
    # reordered: 2 (phase 0) before 3 (phase 1) before 4 (phase 2)
    assert call_log[0] == 1  # initial task from record
    remaining = call_log[1:]
    assert remaining.index(2) < remaining.index(3)
    assert remaining.index(3) < remaining.index(4)

    # Phase transition logs should be present
    phase_logs = [
        log for log in job_store.logs if "Entering Phase" in log["message"]
    ]
    assert len(phase_logs) >= 1  # at least one phase transition

    # Final result should be success
    final = updates[-1]
    assert final["status"] == "completed"
