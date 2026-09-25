"""Phase helpers for ``StructuredChatAgent.process_unified_stream`` (W5c).

The 1856-line unified stream is cut into phases by W5c.  This module holds the
pieces that are pure or parameter-light, so the method (and its closures) keep
only what really needs ``self`` and the stream's mutable local state.

Extracted so far:

* the progress/tool-callback helper family that the ``_emit_progress_status`` /
  ``on_thinking`` / ``on_tool_start`` closures call;
* ``_stream_deterministic_execute`` — the deterministic execute / rerun-task
  branch (its own queue, synthetic thinking step, rerun-job event relay and drain
  loop) as an async generator that yields the same SSE strings, in the same
  order, as the branch did inline.

Anything that closes over the main stream's mutable state (``run_agent``, the
tool wrapper, the deep-think callbacks, the drain loop) stays in ``agent.py``.

Deviations in this module (all registered in the commit messages):

* ``_progress_label_from_phase`` closed over the method's ``reasoning_language``
  local; it now takes ``language`` explicitly (the call sites pass
  ``language=reasoning_language``).  Mapping tables and returned strings are
  byte-identical.
* ``_stream_deterministic_execute`` took ``self`` implicitly as the branch's
  enclosing method scope; it now takes ``agent`` explicitly (``self.`` →
  ``agent.``), and its three patched facade bindings
  (``plan_decomposition_jobs``, ``_persist_runtime_context``,
  ``_save_chat_message``) are read through ``_ag()`` at call time so namespace
  monkeypatching keeps working.

No module-level name here was ever a module-level name of ``agent``, so nothing
is re-exported by the facade.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from app.services.deep_think_agent import DeepThinkResult, ThinkingStep, detect_reasoning_language
from app.services.llm.structured_response import LLMStructuredResponse

from .background import _sse_message
from .deterministic_execute import (
    _build_deterministic_execute_final_payload,
    _build_deterministic_execute_placeholder_step,
)
from .response_metadata import (
    _build_deep_think_response_metadata,
    _plan_runtime_metadata,
    _structured_plan_metadata_from_result,
)
from .subject_grounding import _apply_grounded_local_answer

logger = logging.getLogger(__name__)


def _ag() -> Any:
    """Late-bound agent facade module (monkeypatch-friendly lookups)."""
    from . import agent

    return agent


def _progress_label_from_phase(phase: str, *, language: str) -> str:
    if language == "zh":
        mapping = {
            "planning": "分析请求中",
            "gathering": "检索资料中",
            "analyzing": "整理候选方向中",
            "synthesizing": "汇总结论中",
            "finalizing": "生成最终答复中",
        }
    else:
        mapping = {
            "planning": "Planning the response",
            "gathering": "Gathering evidence",
            "analyzing": "Analyzing findings",
            "synthesizing": "Synthesizing conclusions",
            "finalizing": "Preparing the final answer",
        }
    return mapping.get(phase, mapping["analyzing"])


def _normalize_progress_text(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _truncate_progress_text(text: Optional[str], max_chars: int = 72) -> str:
    normalized = _normalize_progress_text(text)
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 1].rstrip()}…"


def _tool_progress_details(
    tool_name: str, params: Optional[Dict[str, Any]]
) -> Optional[str]:
    params = params if isinstance(params, dict) else {}
    lowered = (tool_name or "").strip().lower()
    if lowered == "web_search":
        query = _normalize_progress_text(params.get("query"))
        return query or None
    if lowered == "literature_pipeline":
        topic = _normalize_progress_text(
            params.get("topic") or params.get("query") or params.get("question")
        )
        return topic or None
    if lowered == "document_reader":
        path = _normalize_progress_text(
            params.get("path") or params.get("file_path")
        )
        return path or None
    if lowered == "file_operations":
        target = _normalize_progress_text(
            params.get("path")
            or params.get("target")
            or params.get("file_path")
        )
        return target or None
    return None


def _extract_tool_context(action_raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not action_raw:
        return None, None
    try:
        parsed = json.loads(action_raw)
    except Exception:
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    tool_name = str(parsed.get("tool") or "").strip() or None
    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    return tool_name, _tool_progress_details(tool_name or "", params)


def _progress_phase_from_step(step: ThinkingStep) -> str:
    if step.status == "calling_tool" or step.action:
        return "gathering"
    if step.status == "done":
        return "finalizing"
    if step.status == "analyzing":
        return "synthesizing"
    if step.iteration <= 1:
        return "planning"
    return "analyzing"


async def _stream_deterministic_execute(
    agent: Any,
    *,
    deterministic_execute: LLMStructuredResponse,
    effective_user_message: str,
    run_id: Optional[str],
    event_sink: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
) -> AsyncIterator[str]:
    """Stream the deterministic execute / rerun-task shortcut phase.

    Moved out of ``process_unified_stream`` (W5c phase: rerun 订阅 / 确定性执行
    快捷路径).  The branch keeps its own queue, synthetic pre-think step,
    rerun-job event relay and drain loop, so it extracts without touching the
    main stream's state; the caller keeps the ``is not None`` condition and the
    terminating ``return``.

    Patch surface: ``plan_decomposition_jobs`` (create_job / register_subscriber /
    unregister_subscriber), ``_persist_runtime_context`` and
    ``_save_chat_message`` are patched on the agent namespace by chat tests, so
    all five call sites read them through ``_ag()`` at call time.  Every event
    payload, progress label, error message and log literal is unchanged.
    """
    queue: asyncio.Queue[Any] = asyncio.Queue()
    rerun_task_job_id: Optional[str] = None
    rerun_task_job_queue: Optional[asyncio.Queue[Any]] = None
    reasoning_language = detect_reasoning_language(effective_user_message)
    synthetic_step_started_at: Optional[str] = None
    synthetic_step_finalized = False
    extra_context = agent.extra_context if isinstance(agent.extra_context, dict) else {}
    if not isinstance(agent.extra_context, dict):
        agent.extra_context = extra_context
    previous_rerun_job_id = extra_context.get("_rerun_task_execution_job_id")

    deterministic_task_id: Optional[int] = None
    deterministic_task_label = (
        "当前任务" if reasoning_language == "zh" else "the task"
    )
    if deterministic_execute.actions:
        try:
            deterministic_task_id = int(
                deterministic_execute.actions[0].parameters.get("task_id")
            )
        except (TypeError, ValueError, AttributeError):
            deterministic_task_id = None
    if deterministic_task_id is not None:
        deterministic_task_label = (
            f"任务 {deterministic_task_id}"
            if reasoning_language == "zh"
            else f"Task {deterministic_task_id}"
        )

    if deterministic_task_id is not None and agent.plan_session.plan_id is not None:
        rerun_task_job_id = f"plan_execute_{run_id or uuid4().hex}"
        task_name = f"Task {deterministic_task_id}"
        try:
            if agent.plan_tree and agent.plan_tree.has_node(deterministic_task_id):
                task_name = agent.plan_tree.get_node(deterministic_task_id).display_name()
        except Exception:
            task_name = f"Task {deterministic_task_id}"
        deterministic_task_label = task_name
        try:
            _ag().plan_decomposition_jobs.create_job(
                plan_id=agent.plan_session.plan_id,
                task_id=deterministic_task_id,
                mode="single_task",
                job_type="plan_execute",
                params={
                    "session_id": agent.session_id,
                    "task_id": deterministic_task_id,
                    "mode": "rerun_task",
                },
                metadata={
                    "session_id": agent.session_id,
                    "conversation_id": getattr(agent, "conversation_id", None),
                    "source": "deterministic_execute_shortcut",
                    "target_task_name": task_name,
                },
                session_id=agent.session_id,
                job_id=rerun_task_job_id,
            )
        except Exception:
            pass
        try:
            rerun_task_job_queue = _ag().plan_decomposition_jobs.register_subscriber(
                rerun_task_job_id,
                asyncio.get_running_loop(),
            )
        except Exception:
            rerun_task_job_queue = None

    if rerun_task_job_id:
        extra_context["_rerun_task_execution_job_id"] = rerun_task_job_id

    async def emit_synthetic_prethink_step(status: str) -> None:
        nonlocal synthetic_step_started_at, synthetic_step_finalized
        now_iso = datetime.now(timezone.utc).isoformat()
        if synthetic_step_started_at is None:
            synthetic_step_started_at = now_iso
        if status in {"done", "error"}:
            if synthetic_step_finalized:
                return
            synthetic_step_finalized = True
        await queue.put(
            {
                "type": "thinking_step",
                "step": _build_deterministic_execute_placeholder_step(
                    language=reasoning_language,
                    status=status,
                    started_at=synthetic_step_started_at,
                    finished_at=now_iso if status in {"done", "error"} else None,
                ),
            }
        )

    async def relay_rerun_task_job_events() -> None:
        nonlocal synthetic_step_finalized
        if rerun_task_job_queue is None:
            return
        while True:
            payload = await rerun_task_job_queue.get()
            if not isinstance(payload, dict):
                continue
            event_payload = payload.get("event")
            if not isinstance(event_payload, dict):
                continue
            metadata = (
                event_payload.get("metadata")
                if isinstance(event_payload.get("metadata"), dict)
                else {}
            )
            sub_type = str(metadata.get("sub_type") or "").strip().lower()
            if sub_type == "thinking_step":
                step_payload = metadata.get("step")
                if isinstance(step_payload, dict):
                    if not synthetic_step_finalized:
                        await emit_synthetic_prethink_step("done")
                    await queue.put({"type": "thinking_step", "step": step_payload})
                continue
            if sub_type == "thinking_delta":
                delta = str(metadata.get("delta") or "")
                if not delta:
                    continue
                if not synthetic_step_finalized:
                    await emit_synthetic_prethink_step("done")
                await queue.put(
                    {
                        "type": "thinking_delta",
                        "iteration": metadata.get("iteration"),
                        "delta": delta,
                    }
                )

    async def run_deterministic_execute() -> None:
        relay_task: Optional[asyncio.Task[Any]] = None
        try:
            await queue.put(
                {
                    "type": "progress_status",
                    "phase": "planning",
                    "label": (
                        f"准备执行 {deterministic_task_label}，正在加载任务上下文"
                        if reasoning_language == "zh"
                        else f"Preparing {deterministic_task_label}; loading task context"
                    ),
                    "status": "running",
                    "iteration": 0,
                }
            )
            await emit_synthetic_prethink_step("thinking")
            if rerun_task_job_queue is not None:
                relay_task = asyncio.create_task(relay_rerun_task_job_events())
            result = await agent.execute_structured(deterministic_execute)
            if relay_task is not None:
                await asyncio.sleep(0)
            if not synthetic_step_finalized:
                await emit_synthetic_prethink_step("done")
            await queue.put({"type": "result", "result": result})
        except Exception as exc:  # pragma: no cover - defensive
            if synthetic_step_started_at is not None and not synthetic_step_finalized:
                await emit_synthetic_prethink_step("error")
            await queue.put({"type": "error", "message": str(exc)})
        finally:
            if relay_task is not None:
                relay_task.cancel()
                await asyncio.gather(relay_task, return_exceptions=True)
            if rerun_task_job_id and rerun_task_job_queue is not None:
                _ag().plan_decomposition_jobs.unregister_subscriber(
                    rerun_task_job_id,
                    rerun_task_job_queue,
                )
            if previous_rerun_job_id is None:
                extra_context.pop("_rerun_task_execution_job_id", None)
            else:
                extra_context["_rerun_task_execution_job_id"] = previous_rerun_job_id
            await queue.put(None)

    asyncio.create_task(run_deterministic_execute())

    async def _through_sink(payload: Dict[str, Any]) -> str:
        if event_sink is not None:
            await event_sink(payload)
        return _sse_message(payload)

    while True:
        item = await queue.get()
        if item is None:
            break

        event_type = item.get("type")
        if event_type in {"thinking_step", "thinking_delta", "progress_status"}:
            out = await _through_sink(item)
            yield out
            continue
        if event_type == "error":
            err_payload = {"type": "error", "message": item.get("message") or "Unknown error"}
            out = await _through_sink(err_payload)
            yield out
            continue
        if event_type == "result":
            final_payload = _build_deterministic_execute_final_payload(
                item["result"],
                plan_id=agent.plan_session.plan_id,
                language=reasoning_language,
            )
            out = await _through_sink(final_payload)
            yield out
            if agent.session_id:
                try:
                    _ag()._persist_runtime_context(agent)
                    payload = final_payload.get("payload") if isinstance(final_payload, dict) else {}
                    payload = payload if isinstance(payload, dict) else {}
                    response_text = str(payload.get("response") or "")
                    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                    _ag()._save_chat_message(
                        agent.session_id,
                        "assistant",
                        response_text,
                        metadata=metadata,
                        model_provider=(agent.extra_context or {}).get("model_provider"),
                    )
                except Exception as save_err:
                    logger.warning(
                        "[CHAT][DETERMINISTIC_EXECUTE] Failed to save response: %s",
                        save_err,
                    )
    return


async def _stream_full_plan_delegate(
    agent: Any,
    *,
    routing_decision: Any,
    user_message: str,
    event_sink: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
    run_id: Optional[str],
    log_delegation: bool,
) -> AsyncIterator[str]:
    """Stream the PlanExecutor delegation phase (full-plan 委派).

    Moved out of ``process_unified_stream`` (W5c phase: full-plan).  Both
    delegating branches (``routing_decision.full_plan_execution`` with a bound
    plan, and the ``_full_plan_executor_delegate`` context flag) share this body;
    they differ only in the ``logger.info`` that the routing branch emits before
    the first delegated event, which is gated by ``log_delegation`` so the log
    timing stays exactly as it was in each branch.

    The delegate's own SSE events are forwarded verbatim, one per iteration.
    """
    if log_delegation:
        logger.info(
            "[CHAT][ROUTING][FULL_PLAN] Delegating to PlanExecutor plan_id=%s",
            agent.plan_session.plan_id,
        )
    async for event in agent._run_full_plan_via_executor(
        user_message=user_message,
        routing_decision=routing_decision,
        event_sink=event_sink,
        run_id=run_id,
    ):
        yield event


async def _stream_direct_image_response(
    agent: Any,
    *,
    response_text: str,
    response_metadata: Dict[str, Any],
    event_sink: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
) -> AsyncIterator[str]:
    """Stream the direct image-reuse response phase (图片直出).

    Moved out of ``process_unified_stream`` (W5c phase: 图片直出).  The caller
    keeps the ``_build_recent_image_display_response`` call and its ``is not
    None`` condition; this generator keeps the original order — persist (only
    when a session and text exist), build the single ``final`` payload, hand it
    to ``event_sink`` when present, then yield it as one SSE line.

    Patch surface: ``_persist_runtime_context`` and ``_save_chat_message`` are
    patched on the agent namespace by chat tests, so both are read through
    ``_ag()`` at call time.
    """
    if agent.session_id and response_text:
        try:
            _ag()._persist_runtime_context(agent)
            _ag()._save_chat_message(
                agent.session_id,
                "assistant",
                response_text,
                metadata=response_metadata,
                model_provider=(agent.extra_context or {}).get("model_provider"),
            )
        except Exception as save_err:  # pragma: no cover - defensive
            logger.warning(
                "[CHAT][IMAGE_REUSE] Failed to save direct response: %s",
                save_err,
            )
    payload = {
        "type": "final",
        "payload": {
            "response": response_text,
            "actions": [],
            "metadata": response_metadata,
        },
    }
    if event_sink is not None:
        await event_sink(payload)
    yield _sse_message(payload)


async def _drain_unified_stream_events(
    agent: Any,
    *,
    queue: asyncio.Queue[Any],
    routing_decision: Any,
    reasoning_language: str,
    thinking_visible: bool,
    progress_visible: bool,
    current_turn_tool_results: List[Dict[str, Any]],
    current_turn_artifact_gallery: List[Dict[str, Any]],
    event_sink: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
) -> AsyncIterator[str]:
    """Drain the unified-stream event queue and yield the SSE lines (finalize).

    Moved out of ``process_unified_stream`` (W5c phase: finalize).  The worker
    (``run_agent``) keeps producing into ``queue``; this generator consumes until
    the ``None`` sentinel and renders:

    * the passthrough set (``thinking_step`` / ``thinking_delta`` /
      ``reasoning_delta`` / ``progress_status`` / ``delta`` / ``control_ack`` /
      ``tool_output`` / ``artifact`` / ``steer_ack``) verbatim,
    * ``error`` events with the six optional diagnostic keys copied over,
    * ``result`` events into the ``final`` SSE payload, emitting it *before* the
      DB save, then persisting the assistant message.

    Parameter face: nine explicit references that all already existed as locals of
    the method.  ``current_turn_tool_results`` / ``current_turn_artifact_gallery``
    are never rebound (only appended to), so aliasing the same list objects keeps
    the concurrent-mutation semantics identical.

    Patch surface: ``_persist_runtime_context`` and ``_save_chat_message`` are
    patched on the agent namespace by chat tests and are read through ``_ag()``.
    """

    async def _through_sink(payload: Dict[str, Any]) -> str:
        if event_sink is not None:
            await event_sink(payload)
        return _sse_message(payload)

    # Consume queue
    while True:
        item = await queue.get()
        if item is None:
            break

        event_type = item.get("type")
        if event_type in {
            "thinking_step",
            "thinking_delta",
            "reasoning_delta",
            "progress_status",
            "delta",
            "control_ack",
            "tool_output",
            "artifact",
            "steer_ack",
        }:
            out = await _through_sink(item)
            yield out
        elif event_type == "error":
            err_payload = {"type": "error", "message": item["error"]}
            for key in (
                "error_type",
                "error_code",
                "category",
                "provider",
                "retryable",
                "status_code",
            ):
                if key in item:
                    err_payload[key] = item[key]
            out = await _through_sink(err_payload)
            yield out
        elif event_type == "result":
            # Final result, yield as standard chat message
            res: DeepThinkResult = item["result"]
            grounded_answer = _apply_grounded_local_answer(
                agent,
                res.final_answer,
                routing_decision,
            )
            if grounded_answer != str(res.final_answer or "").strip():
                res = replace(res, final_answer=grounded_answer)
            result_job_id = (
                str(item.get("job_id"))
                if isinstance(item.get("job_id"), str) and item.get("job_id")
                else None
            )

            # Construct final content for display and saving
            final_content_parts = []
            # Thinking Summary removed per user request
            if res.final_answer:
                final_content_parts.append(res.final_answer)

            full_response = "\n\n".join(final_content_parts)

            # Note: final_answer was already streamed via on_final_delta callback
            # No need to yield it again here to avoid duplication

            # Build metadata ONCE (shared by SSE final event and DB save)
            bg_category = item.get("bg_category")
            plan_tree = getattr(agent, "plan_tree", None)
            structured_plan_meta = _structured_plan_metadata_from_result(
                res,
                tool_results=current_turn_tool_results,
            )
            plan_runtime_meta = _plan_runtime_metadata(plan_tree)
            resolved_plan_id = res.structured_plan_plan_id or agent.plan_session.plan_id
            plan_title = res.structured_plan_title or (plan_tree.title if plan_tree else None)
            final_metadata = _build_deep_think_response_metadata(
                result=res,
                routing_metadata=routing_decision.metadata(),
                plan_id=resolved_plan_id,
                plan_title=plan_title,
                reasoning_language=reasoning_language,
                thinking_visible=thinking_visible,
                progress_visible=progress_visible,
                artifact_gallery=current_turn_artifact_gallery,
                tool_results=current_turn_tool_results,
                deep_think_job_id=result_job_id,
                background_category=bg_category,
                display_text=full_response,
                structured_plan_meta=structured_plan_meta,
                plan_runtime_meta=plan_runtime_meta,
            )

            # 🚀 Emit final event to client FIRST (before DB save)
            payload = {
                "llm_reply": {"message": res.final_answer},
                "response": full_response,
                "actions": [],
                "metadata": final_metadata,
            }
            final_payload = {"type": "final", "payload": payload}
            out = await _through_sink(final_payload)
            yield out

            # 💾 Save Deep Think response to database AFTER final event
            if agent.session_id and full_response:
                try:
                    _ag()._persist_runtime_context(agent)
                    _ag()._save_chat_message(
                        agent.session_id,
                        "assistant",
                        full_response,
                        metadata=final_metadata,
                        model_provider=(agent.extra_context or {}).get("model_provider"),
                    )
                    logger.info(
                        "[CHAT][DEEP_THINK] Response saved to database for session=%s",
                        agent.session_id,
                    )
                except Exception as save_err:
                    logger.warning(
                        "[CHAT][DEEP_THINK] Failed to save response: %s",
                        save_err,
                    )
