"""Background execution of a chat run (decoupled from HTTP)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from types import SimpleNamespace
from typing import Optional

from app.repository.chat_runs import (
    claim_chat_run_lease,
    get_chat_run,
    heartbeat_chat_run_lease,
    mark_chat_run_finished,
    mark_chat_run_started,
    release_chat_run_lease,
)
from app.routers.chat.models import ChatRequest
from app.routers.chat.stream_context import build_agent_for_chat_request
from app.routers.chat.session_helpers import _save_chat_message
from app.services import cancellation
from app.services.chat_run_emitter import ChatRunEmitter
from app.services.chat_run_state import ChatRunOutcome, chat_run_claim
from app.services import chat_run_hub as hub
from app.services.chat_run_signals import lease_ttl_seconds, run_signal_pump
from app.services.run_resume import prepare_run_resume
from app.services.foundation.logging_context import bind_log_context, clear_log_context
from app.services.foundation.otel import otel_span
from app.services.realtime_bus import get_worker_id, start_owner_lease, stop_owner_lease
from app.services.run_budget import (
    DEADLINE_REASON, RunDeadlineExceeded, bind_run_budget, configured_run_budget,
    reset_run_budget, run_stage, watch_run_owner,
)

logger = logging.getLogger(__name__)

_CASCADE_MAX_TASKS = 50


def _capture_quality_snapshot(run_id: str) -> None:
    try:
        from app.services.conversation_quality import get_conversation_quality_service

        get_conversation_quality_service().capture_completed_run(run_id)
    except Exception as exc:  # pragma: no cover - observability must not affect chat delivery
        logger.warning("[QUALITY] snapshot capture failed run=%s error=%s", run_id, type(exc).__name__)


def _build_explicit_execution_final_payload(
    *,
    summary: str,
    plan_id: int,
    completed_ids: list[int],
    failed_id: Optional[int],
    total_tasks: int,
    tools_used: Optional[list[str]] = None,
    tool_failures: Optional[list[str]] = None,
    status: Optional[str] = None,
) -> dict:
    status = status or ("failed" if failed_id is not None else "completed")
    metadata = {
        "plan_id": plan_id,
        "status": status,
        "unified_stream": True,
        "explicit_task_execution": True,
        "completed_task_ids": completed_ids,
        "failed_task_id": failed_id,
        "total_tasks": total_tasks,
        "analysis_text": summary,
        "final_summary": summary,
        "thinking_display_mode": "final_answer",
        "tools_used": list(tools_used or []),
        "tool_failures": list(tool_failures or []),
    }
    return {
        "type": "final",
        "payload": {
            "response": summary,
            "actions": [],
            "metadata": metadata,
        },
    }


async def _run_explicit_task_execution(
    agent,
    executor,
    plan_id: int,
    *,
    run_id: str,
    cancel_ev,
    emitter: ChatRunEmitter,
) -> ChatRunOutcome:
    """Execute all tasks in the explicit scope via plan_executor directly.

    This bypasses the chat DeepThink agent (process_unified_stream) which
    tends to probe instead of execute.  The plan_executor's internal
    DeepThink is more focused and reliably calls code_executor.
    """
    from app.services.plans.plan_executor import ExecutionConfig

    _ctx = getattr(agent, "extra_context", None) or {}
    first_task = _ctx.get("current_task_id")
    pending = list(_ctx.get("pending_scope_task_ids") or [])
    all_task_ids = [int(first_task)] + [int(t) for t in pending]

    logger.info(
        "[EXPLICIT_EXEC] run=%s plan=%d tasks=%s",
        run_id,
        plan_id,
        all_task_ids,
    )

    session_ctx = {
        "session_id": getattr(agent, "session_id", None),
        "chat_history": getattr(agent, "history", []),
        "paper_mode": False,
    }
    exec_config = ExecutionConfig(
        session_context=session_ctx,
        enable_skills=False,
        skill_trace_enabled=False,
    )
    completed_ids = []
    _ctx["completed_scope_task_ids"] = completed_ids
    failed_id = None
    skipped_ids: list[int] = []
    tools_used: list[str] = []
    tool_failures: list[str] = []

    for idx, task_id in enumerate(all_task_ids):
        if cancel_ev.is_set():
            break

        remaining = len(all_task_ids) - idx - 1
        logger.info(
            "[EXPLICIT_EXEC] run=%s task=%d (%d/%d) remaining=%d",
            run_id,
            task_id,
            idx + 1,
            len(all_task_ids),
            remaining,
        )

        try:
            await emitter.emit(
                {
                    "type": "progress_status",
                    "phase": "gathering",
                    "label": f"Executing task {task_id} "
                    f"({idx + 1}/{len(all_task_ids)})",
                    "status": "active",
                }
            )
        except Exception:
            pass

        # Check if task is already completed (skip it)
        try:
            repo = agent.plan_session.repo
            tree = repo.get_plan_tree(plan_id)
            if tree.has_node(task_id):
                node = tree.get_node(task_id)
                status = (node.status or "").strip().lower()
                if status in ("completed", "done"):
                    logger.info(
                        "[EXPLICIT_EXEC] Task %d already completed, skipping",
                        task_id,
                    )
                    completed_ids.append(task_id)
                    continue
        except Exception:
            pass

        try:
            exec_result = await run_stage(asyncio.to_thread(
                executor.execute_task,
                plan_id,
                task_id,
                config=exec_config,
            ), stage=f"plan-task:{task_id}")
        except RunDeadlineExceeded:
            raise
        except Exception as exc:
            logger.exception(
                "[EXPLICIT_EXEC] Task %d exception: %s", task_id, exc
            )
            tool_failures.append(str(exc))
            failed_id = task_id
            break

        result_metadata = getattr(exec_result, "metadata", {}) or {}
        if isinstance(result_metadata, dict):
            for tool_name in result_metadata.get("tools_used") or []:
                name = str(tool_name).strip()
                if name and name not in tools_used:
                    tools_used.append(name)
            for failure in result_metadata.get("tool_failures") or []:
                message = str(failure).strip()
                if message and message not in tool_failures:
                    tool_failures.append(message)

        task_status = (exec_result.status or "").strip().lower()
        logger.info(
            "[EXPLICIT_EXEC] Task %d finished status=%s",
            task_id,
            task_status,
        )

        if task_status in ("completed", "done", "success"):
            completed_ids.append(task_id)
        elif task_status == "skipped":
            skipped_ids.append(task_id)
            logger.info(
                "[EXPLICIT_EXEC] Task %d skipped (deps not met), continuing",
                task_id,
            )
        else:
            failed_id = task_id
            break

    # Emit summary
    summary = (
        f"Executed {len(completed_ids)}/{len(all_task_ids)} tasks. "
        f"Completed: {completed_ids}."
    )
    if failed_id:
        summary += f" Failed at task {failed_id}."
    if skipped_ids:
        summary += f" Skipped: {skipped_ids}."

    if cancel_ev.is_set():
        outcome = ChatRunOutcome("cancelled", "cancelled")
    elif failed_id is not None or skipped_ids:
        outcome = ChatRunOutcome("failed", summary)
    else:
        outcome = ChatRunOutcome("succeeded")

    logger.info("[EXPLICIT_EXEC] %s run=%s", summary, run_id)

    final_payload = _build_explicit_execution_final_payload(
        summary=summary,
        plan_id=plan_id,
        completed_ids=completed_ids,
        failed_id=failed_id,
        total_tasks=len(all_task_ids),
        tools_used=tools_used,
        tool_failures=tool_failures,
        status="completed" if outcome.status == "succeeded" else outcome.status,
    )

    await emitter.emit(final_payload)

    session_id = getattr(agent, "session_id", None)
    if session_id and summary:
        try:
            _save_chat_message(
                session_id,
                "assistant",
                summary,
                metadata=final_payload["payload"]["metadata"],
            )
        except Exception:
            pass
    return outcome


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
            exec_result = await run_stage(asyncio.to_thread(
                executor.execute_task,
                plan_id,
                next_task_id,
                config=exec_config,
            ), stage=f"plan-task:{next_task_id}")
        except RunDeadlineExceeded:
            raise
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


async def _run_lease_heartbeat(
    run_id: str, worker_id: str, stop_event: asyncio.Event,
    *, owner_task: Optional[asyncio.Task] = None,
    lease_lost: Optional[asyncio.Event] = None,
    terminal_committed: Optional[asyncio.Event] = None,
) -> None:
    """Renew the run's worker lease until stopped; never raises."""
    interval = max(2.0, lease_ttl_seconds() / 3.0)
    while not stop_event.is_set():
        try:
            ok = await asyncio.to_thread(
                heartbeat_chat_run_lease,
                run_id,
                worker_id,
                ttl_seconds=lease_ttl_seconds(),
            )
            if not ok:
                if terminal_committed is not None and terminal_committed.is_set():
                    return
                logger.warning(
                    "chat_run lease heartbeat lost run=%s (claimed elsewhere?)", run_id
                )
                if lease_lost is not None:
                    lease_lost.set()
                hub.request_cancel(run_id, "chat_run_lease_lost")
                if owner_task is not None:
                    owner_task.cancel()
                return
        except Exception as exc:  # pragma: no cover - heartbeat must not kill a run
            logger.warning(
                "chat_run lease heartbeat failed run=%s error=%s",
                run_id,
                type(exc).__name__,
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def execute_chat_run(run_id: str) -> None:
    # The process routes controls; this unique claim fences earlier attempts
    # even when a queued run is redispatched in the same process.
    worker_id = f"{get_worker_id()}:{uuid.uuid4().hex}"
    try:
        acquired = claim_chat_run_lease(run_id, worker_id, ttl_seconds=lease_ttl_seconds())
    except Exception as exc:
        logger.warning("chat_run lease claim failed run=%s error=%s", run_id, type(exc).__name__)
        return  # execution requires a durable claim; retries can heal queued runs
    if not acquired:
        logger.info("chat_run already owned or terminal; skipping run=%s", run_id)
        return
    cancel_ev = hub.ensure_cancel_event(run_id)
    # Thread-safe counterpart of `cancel_ev`: the delegations this run starts
    # (code_executor / delegate_task CLI subprocesses) are supervised from
    # worker threads, which can only poll a threading primitive.
    hub.ensure_cancel_token(run_id)
    hub.ensure_steer_queue(run_id)
    emitter = ChatRunEmitter(run_id)
    emitter.worker_id = worker_id
    start_owner_lease("run", run_id)
    lease_lost = asyncio.Event()
    terminal_committed = asyncio.Event()
    emitter.terminal_committed = terminal_committed
    outcome: Optional[ChatRunOutcome] = None
    observed_artifacts: list[dict] = []

    async def emit_run_event(payload: dict) -> bool:
        nonlocal outcome
        token = hub.ensure_cancel_token(run_id)
        if token.reason == DEADLINE_REASON and payload.get("type") == "final":
            metadata = (payload.get("payload") or {}).get("metadata") or {}
            if metadata.get("failure_kind") != "deadline_exceeded":
                raise RunDeadlineExceeded("Run deadline reached before final completion.")
        if payload.get("type") == "artifact":
            observed_artifacts.append(dict(payload))
        event_outcome = ChatRunOutcome.from_event(payload, cancelled=cancel_ev.is_set())
        accepted = await emitter.emit(payload)
        if accepted is not False and outcome is None and event_outcome is not None:
            outcome = event_outcome
            # Simple sinks used by embedding callers may not expose commit
            # hooks; a completed, accepted emit also signals completion.
            terminal_committed.set()
        return accepted is not False
    pump_stop = asyncio.Event()
    heartbeat_stop = asyncio.Event()
    pump_task = asyncio.create_task(run_signal_pump(run_id, pump_stop, worker_id=worker_id))
    heartbeat_task = asyncio.create_task(
        _run_lease_heartbeat(
            run_id, worker_id, heartbeat_stop, owner_task=asyncio.current_task(),
            lease_lost=lease_lost, terminal_committed=terminal_committed,
        )
    )
    from contextlib import ExitStack

    from app.llm import clear_usage_context

    _scope = ExitStack()
    # Handles of the run-scoped usage context, reset in the finally below so the
    # run's attribution (its run id) cannot outlive the run.
    usage_context_tokens: list = []
    cancel_handle = cancellation.set_cancel_token(hub.ensure_cancel_token(run_id))
    claim_handle = chat_run_claim.set((run_id, worker_id))
    budget = configured_run_budget(hub.ensure_cancel_token(run_id))
    budget_handle = bind_run_budget(budget)
    budget_stop = asyncio.Event()
    budget_watch = asyncio.create_task(watch_run_owner(asyncio.current_task(), budget, hub.ensure_cancel_token(run_id), budget_stop))
    request = None
    agent = None

    async def report_deadline() -> None:
        budget_stop.set()
        message = "The run reached its time limit. Execution stopped before completion."
        if observed_artifacts:
            message += " Already published outputs remain available."
        completed_ids = list((getattr(agent, "extra_context", None) or {}).get("completed_scope_task_ids") or [])
        if completed_ids:
            message += f" Completed tasks: {completed_ids}."
        metadata = {
            "status": "failed", "failure_kind": "deadline_exceeded",
            "completion_reason": "deadline_exceeded", "partial": bool(observed_artifacts or completed_ids),
            "completed_task_ids": completed_ids,
            "artifact_gallery": observed_artifacts, "analysis_text": message,
            "final_summary": message, "thinking_display_mode": "final_answer",
        }
        accepted = await run_stage(
            emit_run_event({"type": "final", "payload": {"response": message, "actions": [], "metadata": metadata}}),
            stage="deadline-closeout", closeout=True,
        )
        if accepted:
            mark_chat_run_finished(run_id, "failed", error=message, worker_id=worker_id)
        if accepted and request is not None and request.session_id:
            _save_chat_message(request.session_id, "assistant", message, metadata=metadata)
        _capture_quality_snapshot(run_id)
    try:
        row = get_chat_run(run_id)
        if not row:
            logger.warning("chat_run missing run_id=%s", run_id)
            return
        bind_log_context(run_id=run_id, session_id=str(row.get("session_id") or ""))
        _scope.enter_context(
            otel_span("chat_run", run_id=run_id, session_id=str(row.get("session_id") or ""))
        )
        raw = row.get("request_json")
        if not raw:
            await emit_run_event({"type": "error", "message": "missing request_json"})
            _capture_quality_snapshot(run_id)
            return
        data = json.loads(raw)
        request = ChatRequest.model_validate(data)
        await prepare_run_resume(run_id, request.context or {})

        if mark_chat_run_started(run_id, worker_id=worker_id) is False:
            return
        await emitter.emit({"type": "start", "run_id": run_id})

        agent, message_to_send = await build_agent_for_chat_request(
            request,
            save_user_message=False,
            run_id=run_id,
            usage_token_sink=usage_context_tokens.append,
        )
        agent._current_user_message = message_to_send
        resume_source = (request.context or {}).get("resume_from_run_id")
        if resume_source:
            from app.llm import update_usage_context
            update_usage_context(parent_run_id=str(resume_source))

        # ── Detect explicit task execution ──────────────────────
        # When user says "执行任务8", bypass the chat DeepThink agent and
        # use plan_executor.execute_task() directly.  The plan executor has
        # a more focused DeepThink that reliably calls code_executor.
        _ctx = getattr(agent, "extra_context", None) or {}
        _explicit = _ctx.get("explicit_task_override")
        _first_task = _ctx.get("current_task_id")
        _plan_id = getattr(
            getattr(agent, "plan_session", None), "plan_id", None
        )
        _executor = getattr(agent, "plan_executor", None)

        _pending = list(_ctx.get("pending_scope_task_ids") or [])

        if _explicit and _first_task and _plan_id and _executor and _pending:
            # Direct execution path: use plan_executor for ALL tasks
            outcome = await _run_explicit_task_execution(
                agent,
                _executor,
                _plan_id,
                run_id=run_id,
                cancel_ev=cancel_ev,
                emitter=SimpleNamespace(emit=emit_run_event),
            )
        else:
            if _explicit and _first_task and _plan_id and _executor:
                logger.info(
                    "[EXPLICIT_EXEC] run=%s single-task explicit execute -> using process_unified_stream",
                    run_id,
                )
            # Normal chat streaming path
            async for _chunk in agent.process_unified_stream(
                message_to_send,
                run_id=run_id,
                cancel_event=cancel_ev,
                event_sink=emit_run_event,
                steer_drain=lambda: hub.drain_steer_messages(run_id),
            ):
                pass

        if outcome is None:
            if hub.ensure_cancel_token(run_id).reason == DEADLINE_REASON:
                raise RunDeadlineExceeded("Run deadline reached before a final response.")
            await emit_run_event({
                "type": "error",
                "message": "Run cancelled." if cancel_ev.is_set() else "Chat execution ended without a final response.",
            })
        if outcome is not None:
            mark_chat_run_finished(run_id, outcome.status, error=outcome.error, worker_id=worker_id)
        _capture_quality_snapshot(run_id)
    except RunDeadlineExceeded:
        try:
            await report_deadline()
        except RunDeadlineExceeded:
            logger.warning("chat_run deadline closeout time exhausted run=%s", run_id)
    except asyncio.CancelledError:
        budget_stop.set()
        # A stale worker cannot commit a terminal outcome for the new owner.
        # The shared token also tears down delegated subprocesses in threads.
        if hub.ensure_cancel_token(run_id).reason == DEADLINE_REASON and not lease_lost.is_set():
            try:
                await report_deadline()
            except RunDeadlineExceeded:
                logger.warning("chat_run deadline closeout time exhausted run=%s", run_id)
            return
        if not lease_lost.is_set():
            hub.request_cancel(run_id)
            await run_stage(emit_run_event({"type": "error", "message": "Run cancelled."}), stage="cancel-closeout", closeout=True)
        raise
    except Exception as exc:
        budget_stop.set()
        logger.exception("chat_run worker failed run_id=%s", run_id)
        error_emitted = False
        try:
            error_emitted = await emit_run_event(
                {
                    "type": "error",
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
        except Exception:
            pass
        if error_emitted:
            mark_chat_run_finished(run_id, "failed", error=str(exc), worker_id=worker_id)
        _capture_quality_snapshot(run_id)
    finally:
        if budget is not None:
            budget.close()
        else:
            hub.ensure_cancel_token(run_id).close()
        budget_stop.set()
        budget_watch.cancel()
        await asyncio.gather(budget_watch, return_exceptions=True)
        pump_stop.set()
        heartbeat_stop.set()
        for task in (pump_task, heartbeat_task):
            task.cancel()
        for task in (pump_task, heartbeat_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            _scope.close()
        except Exception:  # pragma: no cover
            pass
        clear_log_context()
        try:
            release_chat_run_lease(run_id, worker_id)
        except Exception:  # pragma: no cover
            pass
        stop_owner_lease("run", run_id)
        hub.forget_worker_task(run_id)
        hub.cleanup_run_signals(run_id)
        cancellation.reset_cancel_token(cancel_handle)
        chat_run_claim.reset(claim_handle)
        reset_run_budget(budget_handle)
        for usage_token in usage_context_tokens:
            try:
                clear_usage_context(usage_token)
            except Exception:  # pragma: no cover - attribution must not mask the run result
                logger.warning(
                    "usage context reset failed run=%s", run_id, exc_info=True
                )
