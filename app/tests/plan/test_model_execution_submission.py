"""The real submission adapter validates IDs before any work or scope widening."""

from types import SimpleNamespace

import pytest

from app.services.plans.background_submission import submit_plan_execution
from app.services.plans.plan_models import PlanNode, PlanTree
from tool_box.tools_impl.plan_tools import plan_operation_handler


@pytest.mark.parametrize("ids", [[], [True], [0], [-1], ["5"], [5.0], [5, None], "5", [5] * 65])
def test_invalid_id_scope_never_falls_back_to_whole_plan(monkeypatch, ids):
    from app.routers import plan_routes
    for name in ("execute_task_with_dependencies", "execute_full_plan"):
        monkeypatch.setattr(plan_routes, name, lambda *a, **kw: pytest.fail("invalid scope dispatched"))
    with pytest.raises(ValueError):
        submit_plan_execution(34, ids, owner_id="alice")


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{}, {"target_task_ids": []},
                                    {"target_task_ids": [5], "target_task_id": 6}])
async def test_missing_or_conflicting_tool_targets_do_not_dispatch(monkeypatch, params):
    monkeypatch.setattr("app.services.plans.background_submission.submit_plan_execution",
                        lambda *a, **kw: pytest.fail("invalid scope dispatched"))
    result = await plan_operation_handler("execute_task", plan_id=34, **params)
    assert result["success"] is False and result["executed"] is False


@pytest.mark.asyncio
async def test_explicit_whole_plan_operation_cannot_silently_discard_targets(monkeypatch):
    monkeypatch.setattr("app.services.plans.background_submission.submit_plan_execution",
                        lambda *a, **kw: pytest.fail("scope widened"))
    result = await plan_operation_handler("execute_all", plan_id=34, target_task_ids=[5])
    assert not result["success"] and result["executed"] is False


@pytest.mark.asyncio
async def test_cross_plan_and_nonexistent_ids_create_no_job(monkeypatch):
    from app.routers import plan_routes
    ctx = SimpleNamespace(plan_id=34, owner_id="alice", session_id="isolated")
    monkeypatch.setattr(plan_routes.plan_decomposition_jobs, "create_job",
                        lambda *a, **kw: pytest.fail("invalid scope created job"))
    crossed = await plan_operation_handler("execute_task", plan_id=35, tool_context=ctx,
                                           target_task_ids=[5])
    assert not crossed["success"] and crossed["bound_plan_id"] == 34
    tree = PlanTree(id=34, title="isolated", nodes={5: PlanNode(id=5, plan_id=34, name="图表")})
    monkeypatch.setattr(plan_routes, "_load_authorized_plan_tree", lambda *a: tree)
    missing = await plan_operation_handler("execute_task", plan_id=34, tool_context=ctx,
                                           target_task_ids=[5, 999])
    assert not missing["success"] and missing["executed"] is False
    assert "999" in missing["error"]


@pytest.mark.asyncio
async def test_composite_scope_is_preserved_by_tool_and_adapter(monkeypatch):
    from app.routers import plan_routes
    seen = []

    def execute(tid, *, plan_id, raw_request, request):
        seen.append((tid, request.selected_task_ids, request.include_subtasks, request.async_mode))
        return SimpleNamespace(model_dump=lambda: {"success": True, "plan_id": 34,
            "job": {"job_id": "fixture", "status": "queued"}}, success=True, message="Queued")

    monkeypatch.setattr(plan_routes, "execute_task_with_dependencies", execute)
    monkeypatch.setattr(plan_routes, "execute_full_plan", lambda *a, **kw: pytest.fail("scope widened"))
    result = await plan_operation_handler("execute_task", plan_id=34,
        tool_context=SimpleNamespace(plan_id=34, owner_id="alice", session_id="isolated"), target_task_ids=[3])
    assert seen == [(3, [3], True, True)]
    assert result["background_submitted"] and result["submitted_task_ids"] == [3]
