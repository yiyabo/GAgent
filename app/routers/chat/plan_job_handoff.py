"""Hand explicit chat execution to the plan API's durable background jobs."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Awaitable, Callable

from app.services.request_principal import get_current_principal


def _enqueue(agent: Any, target: int | list[int] | None, *, paper_mode: bool | None = None):
    # The durable chat-run owner is injected by chat_run_worker from its DB row.
    owner_id = str((agent.extra_context or {}).get("owner_id") or "").strip()
    current = get_current_principal()
    if not owner_id and current is not None:
        owner_id = current.owner_id
    if not owner_id:
        raise ValueError("Cannot determine the owner of this chat run.")
    plan_id = agent.plan_session.plan_id
    if plan_id is None:
        raise ValueError("No plan is bound to this conversation.")
    from app.services.plans.background_submission import submit_plan_execution
    paper_mode = bool((agent.extra_context or {}).get("paper_mode")) if paper_mode is None else paper_mode
    task_ids = target if isinstance(target, list) or target is None else [target]
    return submit_plan_execution(plan_id, task_ids, owner_id=owner_id,
                                 session_id=agent.session_id, paper_mode=paper_mode)


async def enqueue_chat_plan_job(agent: Any, task_id: int | list[int] | None):
    """Return the existing plan route's response without waiting for execution."""
    return await asyncio.to_thread(_enqueue, agent, task_id)


def submission_text(response: Any, target: int | list[int] | None) -> str:
    from app.services.plans.execution_receipt import execution_receipt
    ids = target if isinstance(target, list) or target is None else [target]
    payload = {"success": response.success, "message": response.message,
               "job": response.job, "plan_id": getattr(response, "plan_id", None)}
    return execution_receipt(payload, ids)


def submit_plan_action(agent: Any, action: Any, target: int | None):
    """Structured chat actions use the same submission as the UI button."""
    from .models import AgentStep

    raw = (action.parameters or {}).get("paper_mode")
    paper_mode = None if raw is None else str(raw).lower() in {"true", "1", "yes", "on"}
    response = _enqueue(agent, target, paper_mode=paper_mode)
    details = response.model_dump()
    details["background_submitted"] = bool(response.success and response.job)
    return AgentStep(action=action, success=response.success,
                     message=submission_text(response, target), details=details)


async def stream_chat_plan_handoff(
    agent: Any, task_id: int | list[int] | None, *, event_sink: Callable[[dict], Awaitable[None]] | None,
) -> AsyncIterator[str]:
    from .unified_stream import _stream_direct_image_response

    try:
        response = await enqueue_chat_plan_job(agent, task_id)
        job = response.job if isinstance(response.job, dict) else {}
        job_id = str(job.get("job_id") or "")
        text = submission_text(response, task_id)
    except Exception as exc:
        job_id = ""
        detail = getattr(exc, "detail", None)
        text = f"未启动后台执行：{detail or exc}"
    metadata = {"plan_id": agent.plan_session.plan_id, "background_job_id": job_id,
                "job_type": "plan_execute" if job_id else None,
                "task_id": task_id if isinstance(task_id, int) else None,
                "submitted_task_ids": task_id if isinstance(task_id, list) else [task_id] if task_id else []}
    async for chunk in _stream_direct_image_response(
        agent, response_text=text, response_metadata=metadata, event_sink=event_sink,
    ):
        yield chunk
