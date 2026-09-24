"""Targeted tests for the rerun_task cluster and task sub-dispatch (W0 gap).

Ahead of the planned `action_plan_ops.py` / `action_task_ops.py` split, the
rerun cluster (`_prepare_rerun_task_execution`, `_execute_rerun_task_with_job`,
`_finalize_rerun_task_execution`) and the create_task position/anchor
normalization in `handle_task_action` had only incidental coverage via the
guardrail suite.  These tests pin the branch contracts directly with fake
agents and a fake decomposition-jobs facade.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

import app.routers.chat.action_handlers as action_handlers
from app.routers.chat.action_handlers import (
    _execute_rerun_task_with_job,
    _finalize_rerun_task_execution,
    _prepare_rerun_task_execution,
    handle_task_action,
)
from app.services.llm.structured_response import LLMAction
from app.services.plans.plan_models import PlanNode, PlanTree

TASK_ID = 23
PLAN_ID = 34


def _tree() -> PlanTree:
    return PlanTree(
        id=PLAN_ID,
        title="t",
        nodes={TASK_ID: PlanNode(id=TASK_ID, plan_id=PLAN_ID, name="Collect evidence")},
        adjacency={None: [TASK_ID]},
    )


def _fake_agent(tree: PlanTree, **overrides: Any) -> SimpleNamespace:
    agent = SimpleNamespace(
        session_id="sess-rerun",
        conversation_id="conv-1",
        history=[{"role": "user", "content": "hi"}],
        max_history_messages=42,
        extra_context={"recent_tool_results": [{"tool": "web_search"}]},
        plan_executor=object(),
        _current_user_message="请重跑任务",
        _require_plan_bound=lambda: tree,
        _coerce_int=lambda value, name: int(value),
    )
    for key, value in overrides.items():
        setattr(agent, key, value)
    return agent


def _rerun_action(**param_overrides: Any) -> LLMAction:
    parameters = {"task_id": TASK_ID}
    parameters.update(param_overrides)
    return LLMAction(kind="task_operation", name="rerun_task", parameters=parameters, order=1)


# --------------------------------------------------------------------------- _prepare


def test_prepare_rerun_paper_mode_parsing_and_defaults() -> None:
    agent = _fake_agent(_tree())

    for raw, expected in ((True, True), ("yes", True), ("1", True), ("no", False), (None, False)):
        _, _, config = _prepare_rerun_task_execution(agent, _rerun_action(paper_mode=raw))
        assert config.paper_mode is expected
        assert config.session_context["paper_mode"] is expected

    # Default (non-shortcut) origin keeps skills enabled.
    _, task_id, config = _prepare_rerun_task_execution(agent, _rerun_action())
    assert task_id == TASK_ID
    assert config.enable_skills is True
    assert config.skill_trace_enabled is True
    assert config.session_context == {
        "session_id": "sess-rerun",
        "user_message": "请重跑任务",
        "chat_history": [{"role": "user", "content": "hi"}],
        "chat_history_max_messages": 42,
        "recent_tool_results": [{"tool": "web_search"}],
        "paper_mode": False,
        "explicit_execute_shortcut": False,
    }


def test_prepare_rerun_requires_plan_executor() -> None:
    agent = _fake_agent(_tree(), plan_executor=None)
    with pytest.raises(ValueError, match="Plan executor is not enabled"):
        _prepare_rerun_task_execution(agent, _rerun_action())


# --------------------------------------------------------------------------- _execute


class _FakeJobs:
    def __init__(self) -> None:
        self.created: List[Dict[str, Any]] = []
        self.marks: List[Tuple[Any, ...]] = []

    def get_job(self, job_id: str) -> None:
        return None

    def create_job(self, **kwargs: Any) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(job_id=kwargs["job_id"])

    def mark_running(self, job_id: str) -> None:
        self.marks.append(("running", job_id))

    def mark_success(self, job_id: str, **kwargs: Any) -> None:
        self.marks.append(("success", job_id, kwargs))

    def mark_failure(self, job_id: str, error: str, **kwargs: Any) -> None:
        self.marks.append(("failure", job_id, error, kwargs))


def _install_fake_jobs(monkeypatch: pytest.MonkeyPatch) -> _FakeJobs:
    jobs = _FakeJobs()
    monkeypatch.setattr(action_handlers, "plan_decomposition_jobs", jobs)
    return jobs


def test_execute_rerun_task_with_job_status_routing(monkeypatch) -> None:
    jobs = _install_fake_jobs(monkeypatch)
    tree = _tree()
    marks_results: Dict[str, Any] = {}

    def _execute(plan_id: int, task_id: int, config=None) -> Any:
        status = marks_results["status"]
        return SimpleNamespace(
            status=status,
            content="boom" if status == "failed" else "ok",
            to_dict=lambda: {"status": status, "task_id": task_id},
        )

    agent = _fake_agent(tree, plan_executor=SimpleNamespace(execute_task=_execute))
    _, _, config = _prepare_rerun_task_execution(agent, _rerun_action())

    marks_results["status"] = "completed"
    result, job_id = _execute_rerun_task_with_job(agent, tree, TASK_ID, config)
    assert result.status == "completed"
    assert job_id and job_id.startswith("plan_execute_")

    marks_results["status"] = "failed"
    _, job_id_2 = _execute_rerun_task_with_job(agent, tree, TASK_ID, config)

    # Job creation contract: single_task plan_execute job tagged as rerun_task.
    create = jobs.created[0]
    assert create["plan_id"] == PLAN_ID
    assert create["task_id"] == TASK_ID
    assert create["mode"] == "single_task"
    assert create["job_type"] == "plan_execute"
    assert create["params"]["mode"] == "rerun_task"
    assert create["metadata"]["source"] == "rerun_task"
    assert create["metadata"]["target_task_name"] == "Collect evidence"

    success_mark = next(m for m in jobs.marks if m[0] == "success")
    assert success_mark[1] == job_id
    assert success_mark[2]["result"] == {"status": "completed", "task_id": TASK_ID}
    assert success_mark[2]["stats"] == {"plan_id": PLAN_ID, "task_id": TASK_ID, "execution_status": "completed"}

    failure_mark = next(m for m in jobs.marks if m[0] == "failure")
    assert failure_mark[1] == job_id_2
    assert failure_mark[2] == "boom"
    assert failure_mark[3]["stats"]["execution_status"] == "failed"

    running_marks = [m for m in jobs.marks if m[0] == "running"]
    assert [m[1] for m in running_marks] == [job_id, job_id_2]


def test_execute_rerun_task_with_job_exception_marks_failure(monkeypatch) -> None:
    jobs = _install_fake_jobs(monkeypatch)
    tree = _tree()

    def _explode(plan_id: int, task_id: int, config=None) -> Any:
        raise RuntimeError("executor blew up")

    agent = _fake_agent(tree, plan_executor=SimpleNamespace(execute_task=_explode))
    _, _, config = _prepare_rerun_task_execution(agent, _rerun_action())

    with pytest.raises(RuntimeError, match="executor blew up"):
        _execute_rerun_task_with_job(agent, tree, TASK_ID, config)

    failure_mark = next(m for m in jobs.marks if m[0] == "failure")
    assert failure_mark[2] == "executor blew up"
    assert failure_mark[3]["result"]["status"] == "failed"
    assert failure_mark[3]["stats"]["execution_status"] == "failed"
    assert not any(m[0] == "success" for m in jobs.marks)


# --------------------------------------------------------------------------- _finalize


def _finalize_agent(tree: PlanTree) -> Tuple[SimpleNamespace, List[bool]]:
    refreshes: List[bool] = []
    agent = _fake_agent(tree)
    agent._refresh_plan_tree = lambda force_reload=False: refreshes.append(force_reload)
    return agent, refreshes


def test_finalize_rerun_task_execution_messages_and_job_block() -> None:
    tree = _tree()
    action = _rerun_action()

    cases = [
        ("completed", True, "Task [23] execution status: completed."),
        ("done", True, "Task [23] execution status: done."),
        ("skipped", False, "Task [23] was skipped."),
        ("failed", False, "Task [23] failed."),
        ("error", False, "Task [23] failed."),
    ]
    for status, expected_success, expected_message in cases:
        agent, refreshes = _finalize_agent(tree)
        result = SimpleNamespace(status=status, to_dict=lambda: {"status": status})
        step = _finalize_rerun_task_execution(agent, action, tree, TASK_ID, result, job_id="job-9")
        assert step.success is expected_success
        assert step.message == expected_message
        assert step.details["result"] == {"status": status}
        assert step.details["job"] == {
            "job_id": "job-9",
            "job_type": "plan_execute",
            "task_id": TASK_ID,
            "plan_id": PLAN_ID,
        }
        assert refreshes == [True]

    agent, _ = _finalize_agent(tree)
    result = SimpleNamespace(status="completed", to_dict=lambda: {"status": "completed"})
    step = _finalize_rerun_task_execution(agent, action, tree, TASK_ID, result)
    assert "job" not in step.details


# --------------------------------------------------------------------------- create_task sub-dispatch


def _create_action(parameters: Dict[str, Any]) -> LLMAction:
    base = {"task_name": "New task"}
    base.update(parameters)
    return LLMAction(kind="task_operation", name="create_task", parameters=base, order=1)


def test_handle_task_action_create_task_anchor_validation() -> None:
    agent = _fake_agent(_tree())

    # position pattern references a different task than anchor_task_id.
    with pytest.raises(ValueError, match="anchor_task_id does not match"):
        handle_task_action(agent, _create_action({"position": "before:23", "anchor_task_id": 99}))

    # position pattern conflicts with an explicit anchor_position.
    with pytest.raises(ValueError, match="anchor_position does not match"):
        handle_task_action(
            agent,
            _create_action({"position": "after:23", "anchor_task_id": TASK_ID, "anchor_position": "before"}),
        )

    # anchor_position must be a string.
    with pytest.raises(ValueError, match="anchor_position must be a string"):
        handle_task_action(agent, _create_action({"anchor_position": 3}))

    # negative absolute position is rejected.
    with pytest.raises(ValueError, match="position cannot be negative"):
        handle_task_action(agent, _create_action({"position": -1}))

    # insert_before / insert_after must not point at the same task.
    with pytest.raises(ValueError, match="cannot point to the same task"):
        handle_task_action(agent, _create_action({"insert_before": TASK_ID, "insert_after": TASK_ID}))
