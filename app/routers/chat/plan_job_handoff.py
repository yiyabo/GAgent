"""Hand explicit chat execution to the plan API's durable background jobs."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import Request

from app.services.request_principal import RequestPrincipal


def _enqueue(agent: Any, task_id: int | None):
    # The durable chat-run owner is injected by chat_run_worker from its DB row.
    owner_id = str((agent.extra_context or {}).get("owner_id") or "").strip()
    if not owner_id:
        raise ValueError("Cannot determine the owner of this chat run.")
    plan_id = agent.plan_session.plan_id
    if plan_id is None:
        raise ValueError("No plan is bound to this conversation.")
    principal = RequestPrincipal(user_id=owner_id, email=None)
    request = Request({"type": "http", "state": {"principal": principal}})
    paper_mode = bool((agent.extra_context or {}).get("paper_mode"))
    if task_id is None:
        from app.routers.plan_routes import execute_full_plan
        from app.routers.plan_routes.schemas import ExecuteFullPlanRequest

        return execute_full_plan(
            plan_id, request,
            ExecuteFullPlanRequest(session_id=agent.session_id, async_mode=True, paper_mode=paper_mode,
                                   skip_completed=True, stop_on_failure=True,
                                   ordering_mode="dependency_phase", dependency_block_mode="block"),
        )
    from app.routers.plan_routes import execute_task_with_dependencies
    from app.routers.plan_routes.schemas import ExecuteTaskRequest

    return execute_task_with_dependencies(
        task_id, plan_id=plan_id, raw_request=request,
        request=ExecuteTaskRequest(session_id=agent.session_id, async_mode=True, paper_mode=paper_mode,
                                   include_dependencies=True, include_subtasks=True),
    )


async def enqueue_chat_plan_job(agent: Any, task_id: int | None):
    """Return the existing plan route's response without waiting for execution."""
    return await asyncio.to_thread(_enqueue, agent, task_id)


async def stream_chat_plan_handoff(
    agent: Any, task_id: int | None, *, event_sink: Callable[[dict], Awaitable[None]] | None,
) -> AsyncIterator[str]:
    from .unified_stream import _stream_direct_image_response

    try:
        response = await enqueue_chat_plan_job(agent, task_id)
        job = response.job if isinstance(response.job, dict) else {}
        job_id = str(job.get("job_id") or "")
        if response.success and job_id:
            target = f"任务 #{task_id}" if task_id is not None else "整个计划"
            text = f"{target}已交给后台执行（作业 {job_id}）。你可以继续聊天，进度和结果在计划运行记录中查看。"
        else:
            text = f"未启动后台执行：{response.message}"
    except Exception as exc:
        job_id = ""
        detail = getattr(exc, "detail", None)
        text = f"未启动后台执行：{detail or exc}"
    metadata = {"plan_id": agent.plan_session.plan_id, "background_job_id": job_id,
                "job_type": "plan_execute" if job_id else None,
                "task_id": task_id}
    async for chunk in _stream_direct_image_response(
        agent, response_text=text, response_metadata=metadata, event_sink=event_sink,
    ):
        yield chunk
