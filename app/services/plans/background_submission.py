"""Internal adapter to the same async submission used by the task panel."""

from fastapi import Request
from app.services.request_principal import RequestPrincipal


def submit_plan_execution(plan_id, task_ids, *, owner_id, session_id=None, paper_mode=False):
    if not str(owner_id or "").strip():
        raise ValueError("Cannot determine the owner of this plan execution request.")
    if type(plan_id) is not int or plan_id <= 0:
        raise ValueError("plan_id must be a positive integer.")
    # Do not coerce bools, strings or floats to a different task. None is used
    # exclusively by the explicit execute_all operation, never by invalid IDs.
    if task_ids is not None:
        if not isinstance(task_ids, list) or not 1 <= len(task_ids) <= 64:
            raise ValueError("target_task_ids must contain 1 to 64 task IDs.")
        if any(type(tid) is not int or tid <= 0 for tid in task_ids):
            raise ValueError("Every target task ID must be a positive integer.")
    from app.routers.plan_routes import execute_full_plan, execute_task_with_dependencies
    from app.routers.plan_routes.schemas import ExecuteFullPlanRequest, ExecuteTaskRequest

    raw_request = Request({"type": "http", "state": {
        "principal": RequestPrincipal(user_id=str(owner_id), email=None),
    }})
    if task_ids is None:
        return execute_full_plan(plan_id, raw_request, ExecuteFullPlanRequest(
            session_id=session_id, async_mode=True, paper_mode=paper_mode,
            skip_completed=True, stop_on_failure=True,
            ordering_mode="dependency_phase", dependency_block_mode="block",
        ))
    ids = list(dict.fromkeys(task_ids))
    if not ids:
        raise ValueError("请指定任务编号，或在右侧计划中选择要执行的任务。")
    return execute_task_with_dependencies(ids[0], plan_id=plan_id, raw_request=raw_request,
        request=ExecuteTaskRequest(session_id=session_id, async_mode=True, paper_mode=paper_mode,
                                   include_dependencies=True, include_subtasks=True, selected_task_ids=ids))
