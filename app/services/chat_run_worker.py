"""Background execution of a chat run (decoupled from HTTP)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from app.repository.chat_runs import get_chat_run, mark_chat_run_finished, mark_chat_run_started
from app.routers.chat.models import ChatRequest
from app.routers.chat.stream_context import build_agent_for_chat_request
from app.services.chat_run_emitter import ChatRunEmitter
from app.services import chat_run_hub as hub
from app.services.realtime_bus import start_owner_lease, stop_owner_lease

logger = logging.getLogger(__name__)

_CASCADE_MAX_TASKS = 50


async def _run_cascade(
    agent,
    executor,
    plan_id: int,
    pending: list,
    *,
    run_id: str,
    cancel_ev,
    emitter: ChatRunEmitter,
) -> None:
    """Execute remaining pending tasks sequentially via plan_executor."""
    from app.services.plans.plan_executor import ExecutionConfig

    _ctx = getattr(agent, "extra_context", None) or {}

    # Before cascading, verify the first task actually completed.
    prev_task_id = _ctx.get("current_task_id")
    if prev_task_id is not None:
        try:
            repo = agent.plan_session.repo
            tree = repo.get_plan_tree(plan_id)
            prev_node = tree.get_node(int(prev_task_id))
            if (prev_node.status or "").strip().lower() not in (
                "completed",
                "done",
            ):
                logger.info(
                    "[CASCADE] First task %s status=%s, skipping cascade",
                    prev_task_id,
                    prev_node.status,
                )
                return
        except Exception as exc:
            logger.warning("[CASCADE] Cannot verify first task status: %s", exc)
            return

    cascade_count = 0
    while (
        cascade_count < _CASCADE_MAX_TASKS
        and pending
        and not cancel_ev.is_set()
    ):
        cascade_count += 1
        next_task_id = pending.pop(0)

        _ctx["current_task_id"] = next_task_id
        _ctx["task_id"] = next_task_id
        _ctx["pending_scope_task_ids"] = pending

        remaining = len(pending)
        logger.info(
            "[CASCADE] run=%s iter=%d task=%d remaining=%d",
            run_id,
            cascade_count,
            next_task_id,
            remaining,
        )

        try:
            await emitter.emit(
                {
                    "type": "progress_status",
                    "phase": "gathering",
                    "label": f"[CASCADE] Executing task {next_task_id} "
                    f"({remaining} remaining)",
                    "status": "active",
                }
            )
        except Exception:
            pass

        session_ctx = {
            "session_id": getattr(agent, "session_id", None),
            "chat_history": getattr(agent, "history", []),
            "paper_mode": False,
        }
        exec_config = ExecutionConfig(session_context=session_ctx)

        try:
            exec_result = await asyncio.to_thread(
                executor.execute_task,
                plan_id,
                next_task_id,
                config=exec_config,
            )
        except Exception as exc:
            logger.exception(
                "[CASCADE] Task %d raised exception: %s", next_task_id, exc
            )
            break

        task_status = (exec_result.status or "").strip().lower()
        logger.info(
            "[CASCADE] Task %d finished status=%s", next_task_id, task_status
        )

        if task_status not in ("completed", "done", "success"):
            logger.warning(
                "[CASCADE] Task %d not completed (status=%s), stopping",
                next_task_id,
                task_status,
            )
            break

    logger.info(
        "[CASCADE] Finished. Executed %d additional tasks for run=%s",
        cascade_count,
        run_id,
    )


async def execute_chat_run(run_id: str) -> None:
    cancel_ev = hub.ensure_cancel_event(run_id)
    hub.ensure_steer_queue(run_id)
    emitter = ChatRunEmitter(run_id)
    start_owner_lease("run", run_id)
    try:
        row = get_chat_run(run_id)
        if not row:
            logger.warning("chat_run missing run_id=%s", run_id)
            return
        raw = row.get("request_json")
        if not raw:
            mark_chat_run_finished(run_id, "failed", error="missing request_json")
            return
        data = json.loads(raw)
        request = ChatRequest.model_validate(data)

        mark_chat_run_started(run_id)
        await emitter.emit({"type": "start", "run_id": run_id})

        agent, message_to_send = build_agent_for_chat_request(
            request, save_user_message=False
        )
        agent._current_user_message = message_to_send

        async for _chunk in agent.process_unified_stream(
            message_to_send,
            run_id=run_id,
            cancel_event=cancel_ev,
            event_sink=emitter.emit,
            steer_drain=lambda: hub.drain_steer_messages(run_id),
        ):
            pass

        # ── Task Cascade Auto-Continue ──────────────────────────
        # When a composite task ("执行任务8") expanded to multiple leaf
        # tasks, execute them sequentially after the first one finishes.
        _ctx = getattr(agent, "extra_context", None) or {}
        pending = _ctx.get("pending_scope_task_ids")
        plan_id = getattr(
            getattr(agent, "plan_session", None), "plan_id", None
        )
        executor = getattr(agent, "plan_executor", None)

        if (
            executor is not None
            and plan_id is not None
            and not cancel_ev.is_set()
            and isinstance(pending, list)
            and len(pending) > 0
        ):
            await _run_cascade(
                agent, executor, plan_id, pending,
                run_id=run_id,
                cancel_ev=cancel_ev,
                emitter=emitter,
            )

        if cancel_ev.is_set():
            mark_chat_run_finished(run_id, "cancelled", error="cancelled")
        else:
            mark_chat_run_finished(run_id, "succeeded")
    except Exception as exc:
        logger.exception("chat_run worker failed run_id=%s", run_id)
        try:
            await emitter.emit(
                {
                    "type": "error",
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
        except Exception:
            pass
        mark_chat_run_finished(run_id, "failed", error=str(exc))
    finally:
        stop_owner_lease("run", run_id)
        hub.forget_worker_task(run_id)
        hub.cleanup_run_signals(run_id)
