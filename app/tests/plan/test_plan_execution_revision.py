"""A running Plan task may revise its unfinished Plan in a bounded, audited way."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.plans import edit_policy
from app.services.plans.plan_models import PlanNode, PlanTree
from tool_box.context import ToolContext
from tool_box.tools_impl import plan_tools

_REVISION = {"plan_tree_edit_allowed": edit_policy.EXECUTION_REVISION}
_REASON = "Tried bio_tools hmmscan and a looser panel; the step needs a GH25 family search first."


def _ctx(task_id: int = 6) -> ToolContext:
    return ToolContext(plan_id=9, task_id=task_id, extra=dict(_REVISION))


def test_plan_tasks_get_revision_mode_by_default(monkeypatch):
    from app.services.execution.tool_executor import (
        ToolExecutionContext,
        _task_edit_mode,
    )
    from app.services.plans.execution_budget import request_profile

    monkeypatch.delenv("PLAN_EXECUTION_REVISION_ENABLED", raising=False)
    assert _task_edit_mode(ToolExecutionContext(plan_id=9, task_id=6, mode="task_execution")) == "execution_revision"
    assert request_profile(9, 6, {})["plan_tree_edit_allowed"] == "execution_revision"
    assert _task_edit_mode(ToolExecutionContext(plan_id=9, task_id=None)) is None
    monkeypatch.setenv("PLAN_EXECUTION_REVISION_ENABLED", "0")
    assert request_profile(9, 6, {})["plan_tree_edit_allowed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "bind", "update_task"])
async def test_revision_mode_still_blocks_other_plan_edits(monkeypatch, operation):
    called = AsyncMock()
    names = {"create": "_create_plan", "bind": "_bind_plan", "update_task": "_update_task"}
    monkeypatch.setattr(plan_tools, names[operation], called)
    result = await plan_tools.plan_operation_handler(operation, plan_id=9, tool_context=_ctx())
    assert result["error_code"] == "plan_revision_not_allowed" and result["executed"] is False
    called.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes, reason", [([], _REASON), (None, _REASON),
                                             ([{"action": "add_task", "name": "x", "instruction": "y"}], "")])
async def test_revision_needs_explicit_changes_and_reason(monkeypatch, changes, reason):
    called = AsyncMock()
    monkeypatch.setattr(plan_tools, "_optimize_plan", called)
    result = await plan_tools.plan_operation_handler("optimize", plan_id=9, changes=changes, reason=reason,
                                                     tool_context=_ctx())
    assert result["error_code"] == "plan_revision_not_allowed"
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_revision_cannot_delete_the_running_task_or_its_parent(monkeypatch):
    tree = PlanTree(id=9, title="P", nodes={1: PlanNode(id=1, plan_id=9, name="root"),
                                         6: PlanNode(id=6, plan_id=9, name="P1b", parent_id=1)})
    monkeypatch.setattr("app.repository.plan_repository.PlanRepository.get_plan_tree", lambda self, pid: tree)
    called = AsyncMock()
    monkeypatch.setattr(plan_tools, "_optimize_plan", called)
    for target in (6, 1):
        result = await plan_tools.plan_operation_handler(
            "optimize", plan_id=9, reason=_REASON, tool_context=_ctx(),
            changes=[{"action": "delete_task", "task_id": target}])
        assert result["error_code"] == "plan_revision_not_allowed", target
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_revision_is_applied_and_recorded(monkeypatch):
    recorded: list[dict[str, Any]] = []
    tree = PlanTree(id=9, title="P", metadata={}, nodes={1: PlanNode(id=1, plan_id=9, name="root"),
                                                       6: PlanNode(id=6, plan_id=9, name="P1b", parent_id=1)})
    monkeypatch.setattr("app.repository.plan_repository.PlanRepository.get_plan_tree", lambda self, pid: tree)
    monkeypatch.setattr("app.repository.plan_repository.PlanRepository.update_plan_metadata",
                        lambda self, pid, meta: recorded.append(meta))
    changes = [{"action": "add_task", "name": "P1a+ GH25 search", "instruction": "Add GH25 lysozyme family."}]
    optimize = AsyncMock(return_value={"success": True, "applied_changes": 1, "generated_changes": changes})
    monkeypatch.setattr(plan_tools, "_optimize_plan", optimize)
    result = await plan_tools.plan_operation_handler("optimize", plan_id=9, changes=changes, reason=_REASON,
                                                     tool_context=_ctx())
    assert result["success"] is True
    optimize.assert_awaited_once()
    entry = recorded[-1]["execution_revisions"][-1]
    assert entry["task_id"] == 6 and entry["reason"] == _REASON and entry["changes"] == changes


def test_revision_prompt_keeps_the_bar_and_requires_attempts():
    text = edit_policy.EXECUTION_REVISION_GUIDANCE
    assert "try hard to complete this task as written" in text
    assert "never the bar" in text and "never relabel" in text


def test_prompt_uses_revision_guidance_for_plan_tasks():
    from app.services.deep_think_agent import DeepThinkAgent

    agent = DeepThinkAgent(llm_client=SimpleNamespace(), available_tools=["plan_operation"],
                           tool_executor=AsyncMock(), max_iterations=2,
                           request_profile={"plan_tree_edit_allowed": "execution_revision", "current_plan_id": 9,
                                            "current_task_id": 6, "intent_type": "execute_task",
                                            "execution_lane": "plan"})
    prompt = agent._build_native_system_prompt()
    assert "PLAN REVISION DURING EXECUTION" in prompt
    assert "The persisted Plan task tree is fixed for this turn" not in prompt


# --- the running job picks up revisions ---------------------------------------------------------

@dataclass
class _Result:
    status: str
    duration_sec: float = 0.1
    content: str = "ok"
    metadata: dict = field(default_factory=dict)


def _install_chain(monkeypatch, tree: PlanTree, on_execute):
    from app.routers import plan_routes

    calls: list[int] = []
    store: dict[str, Any] = {}
    monkeypatch.setattr("app.services.plans.executor_reuse.completed_result", lambda *a, **k: None)
    monkeypatch.setattr(plan_routes, "log_job_event", lambda *a, **k: None)
    jobs = plan_routes.plan_decomposition_jobs
    monkeypatch.setattr(jobs, "mark_running", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "update_stats", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "mark_success", lambda job_id, result=None, **k: store.update(ok=result))
    monkeypatch.setattr(jobs, "mark_failure", lambda job_id, error, **k: store.update(error=error))
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda pid: tree)

    def _execute(plan_id, task_id, config=None):
        calls.append(task_id)
        on_execute(task_id)
        return _Result("completed")

    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", _execute)
    monkeypatch.setattr(plan_routes._plan_executor, "_repo", SimpleNamespace(get_plan_tree=lambda pid: tree))
    return calls, store


def _tree() -> PlanTree:
    nodes = {1: PlanNode(id=1, plan_id=9, name="root"),
             2: PlanNode(id=2, plan_id=9, name="P1a", parent_id=1),
             3: PlanNode(id=3, plan_id=9, name="P1b", parent_id=1, dependencies=[2]),
             4: PlanNode(id=4, plan_id=9, name="P2", parent_id=1, dependencies=[3])}
    tree = PlanTree(id=9, title="P", nodes=nodes)
    tree.rebuild_adjacency()
    return tree


def _run_chain(task_order):
    from app.routers import plan_routes

    asyncio.run(asyncio.to_thread(plan_routes._run_task_chain_job, job_id="job", plan_id=9, target_task_id=4,
                                  task_order=task_order, session_id="s", deep_think=True))


def test_chain_runs_a_task_the_agent_added_before_its_dependents(monkeypatch):
    tree = _tree()

    def _on_execute(task_id):
        if task_id == 2 and 5 not in tree.nodes:
            tree.nodes[5] = PlanNode(id=5, plan_id=9, name="P1a+ GH25", parent_id=1, dependencies=[2])
            tree.nodes[3].dependencies = [2, 5]
            tree.rebuild_adjacency()

    calls, store = _install_chain(monkeypatch, tree, _on_execute)
    _run_chain([2, 3, 4])
    assert calls == [2, 5, 3, 4]
    assert "error" not in store


def test_chain_drops_a_pending_task_the_agent_deleted(monkeypatch):
    tree = _tree()

    def _on_execute(task_id):
        if task_id == 2 and 3 in tree.nodes:
            del tree.nodes[3]
            tree.nodes[4].dependencies = [2]
            tree.rebuild_adjacency()

    calls, store = _install_chain(monkeypatch, tree, _on_execute)
    _run_chain([2, 3, 4])
    assert calls == [2, 4]
    assert "error" not in store


# --- no injected wall-clock wrap-up for Plan tasks --------------------------------------------

def test_plan_task_runs_have_no_wall_clock_wrap_up(monkeypatch):
    from app.services.deep_think import guards

    monkeypatch.delenv("PLAN_TASK_WALL_CLOCK_LIMIT", raising=False)
    plan_agent = SimpleNamespace(request_profile={"execution_lane": "plan", "intent_type": "execute_task"})
    chat_agent = SimpleNamespace(request_profile={"intent_type": "execute_task"})
    assert guards._plan_lane_unbounded(plan_agent) is True
    assert guards._plan_lane_unbounded(chat_agent) is False
    monkeypatch.setenv("PLAN_TASK_WALL_CLOCK_LIMIT", "1")
    assert guards._plan_lane_unbounded(plan_agent) is False
