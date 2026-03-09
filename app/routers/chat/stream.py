"""Streaming chat endpoint implementation."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, AsyncIterator, Dict
from uuid import uuid4

from fastapi import BackgroundTasks
from fastapi.responses import StreamingResponse

from app.repository.chat_action_runs import create_action_run
from app.repository.plan_storage import append_action_log_entry
from app.services.llm.structured_response import LLMStructuredResponse
from app.services.plans.decomposition_jobs import plan_decomposition_jobs
from app.services.plans.plan_session import PlanSession

from .action_execution import _execute_action_run
from .background import _classify_background_category, _sse_message
from .confirmation import (
    ACTIONS_REQUIRING_CONFIRMATION,
    _generate_confirmation_id,
    _requires_confirmation,
    _store_pending_confirmation,
)
from .models import ChatRequest, ChatResponse, StructuredReplyStreamParser
from .services import (
    get_structured_chat_agent_cls,
    plan_decomposer_service,
    plan_executor_service,
    plan_repository,
)
from .session_helpers import (
    _convert_history_to_agent_format,
    _derive_conversation_id,
    _get_session_current_task,
    _get_session_settings,
    _normalize_base_model,
    _normalize_llm_provider,
    _normalize_search_provider,
    _resolve_plan_binding,
    _save_assistant_response,
    _save_chat_message,
    _set_session_plan_id,
)

logger = logging.getLogger(__name__)


async def chat_stream(request: ChatRequest, background_tasks: BackgroundTasks):
    _ = background_tasks

    async def event_generator() -> AsyncIterator[str]:
        try:
            context = dict(request.context or {})
            incoming_plan_id = context.get("plan_id")
            if incoming_plan_id is not None and not isinstance(incoming_plan_id, int):
                try:
                    incoming_plan_id = int(str(incoming_plan_id).strip())
                except (TypeError, ValueError):
                    incoming_plan_id = None

            plan_id = _resolve_plan_binding(request.session_id, incoming_plan_id)
            plan_session = PlanSession(repo=plan_repository, plan_id=plan_id)
            try:
                plan_session.refresh()
            except ValueError as exc:
                logger.warning("Plan binding failed, detaching session: %s", exc)
                plan_session.detach()

            logger.info(
                "[CHAT][STREAM][REQ] session=%s plan=%s mode=%s message=%s",
                request.session_id or "<new>",
                plan_session.plan_id,
                request.mode or "assistant",
                request.message,
            )

            message_to_send = request.message
            attachments = context.get("attachments", [])
            if attachments and isinstance(attachments, list):
                attachment_info = "\n\n📎 User-uploaded attachments:\n"
                for att in attachments:
                    if isinstance(att, dict):
                        att_type = att.get("type", "file")
                        att_name = att.get("name", "Unknown file")
                        att_path = att.get("path", "")
                        att_extracted = att.get("extracted_path")
                        attachment_info += f"- {att_name} ({att_type}): {att_path}\n"
                        if att_extracted:
                            attachment_info += f"  extracted: {att_extracted}\n"
                # Provide tool usage hints based on attachment types.
                has_image = any(att.get("type") == "image" for att in attachments if isinstance(att, dict))
                has_document = any(att.get("type") in ["document", "application/pdf"] for att in attachments if isinstance(att, dict))
                if has_image and not has_document:
                    attachment_info += "\n💡 Hint: use vision_reader for image understanding and description."
                elif has_document and not has_image:
                    attachment_info += "\n💡 Hint: use document_reader to extract document content."
                elif has_image and has_document:
                    attachment_info += "\n💡 Hint: use vision_reader for images and document_reader for documents."
                message_to_send = request.message + attachment_info
                logger.info(
                    "[CHAT][STREAM][ATTACHMENTS] session=%s count=%d",
                    request.session_id,
                    len(attachments),
                )

            if plan_session.plan_id is not None:
                context["plan_id"] = plan_session.plan_id
            else:
                context.pop("plan_id", None)

            converted_history = _convert_history_to_agent_format(request.history)
            session_settings: Dict[str, Any] = {}

            if "task_id" in context and "current_task_id" not in context:
                context["current_task_id"] = context["task_id"]
                logger.info(
                    "[CHAT][STREAM][TASK_SYNC] Using task_id from context: %s",
                    context["current_task_id"],
                )

            if request.session_id:
                _save_chat_message(request.session_id, "user", request.message)
                session_settings = _get_session_settings(request.session_id)
                if "current_task_id" not in context:
                    current_task_id = _get_session_current_task(request.session_id)
                    if current_task_id is not None:
                        context["current_task_id"] = current_task_id
                        logger.info(
                            "[CHAT][STREAM][TASK_SYNC] Using current_task_id from session: %s",
                            current_task_id,
                        )

            explicit_provider = _normalize_search_provider(
                context.get("default_search_provider")
            )
            if explicit_provider:
                context["default_search_provider"] = explicit_provider
            else:
                session_provider = session_settings.get("default_search_provider")
                if session_provider:
                    context["default_search_provider"] = session_provider

            explicit_base_model = _normalize_base_model(
                context.get("default_base_model")
            )
            if explicit_base_model:
                context["default_base_model"] = explicit_base_model
            else:
                session_base_model = session_settings.get("default_base_model")
                if session_base_model:
                    context["default_base_model"] = session_base_model

            explicit_llm_provider = _normalize_llm_provider(
                context.get("default_llm_provider")
            )
            if explicit_llm_provider:
                context["default_llm_provider"] = explicit_llm_provider
            else:
                session_llm_provider = session_settings.get("default_llm_provider")
                if session_llm_provider:
                    context["default_llm_provider"] = session_llm_provider

            agent_cls = get_structured_chat_agent_cls()
            agent = agent_cls(
                mode=request.mode,
                plan_session=plan_session,
                plan_decomposer=plan_decomposer_service,
                plan_executor=plan_executor_service,
                session_id=request.session_id,
                conversation_id=_derive_conversation_id(request.session_id),
                history=converted_history,
                extra_context=context,
            )

            yield _sse_message({"type": "start"})

            agent._current_user_message = message_to_send
            prompt = agent._build_prompt(message_to_send)
            model_override = agent.extra_context.get("default_base_model")

            parser = StructuredReplyStreamParser()

            # 🚀 Deep Think Mode Check
            maybe_deep_think = agent._should_use_deep_think(message_to_send)
            use_deep_think = (
                await maybe_deep_think
                if inspect.isawaitable(maybe_deep_think)
                else bool(maybe_deep_think)
            )
            if use_deep_think:
                logger.info("[CHAT] Activating Deep Think Mode")
                async for chunk in agent.process_deep_think_stream(message_to_send):
                    yield chunk
                return

            # Typewriter effect aligned with DeepThink behavior.
            TYPEWRITER_DELAY = 0.01  # 10ms delay
            async for chunk in agent.llm_service.stream_chat_async(
                prompt, force_real=True, model=model_override
            ):
                for delta in parser.feed(chunk):
                    if delta:
                        # Stream one character at a time for smooth typing.
                        for char in delta:
                            yield _sse_message({"type": "delta", "content": char})
                            await asyncio.sleep(TYPEWRITER_DELAY)

            raw = parser.full_text()
            cleaned = agent._strip_code_fence(raw)
            try:
                structured = LLMStructuredResponse.model_validate_json(cleaned)
            except Exception as parse_exc:
                logger.warning(
                    "LLM response JSON parse failed, falling back to text reply: %s",
                    parse_exc,
                )
                fallback_response = ChatResponse(
                    response=raw,
                    suggestions=["The assistant's response could not be parsed as structured JSON. Displayed as plain text."],
                    actions=[],
                    metadata={
                        "status": "completed",
                        "unified_stream": True,
                        "parse_error": str(parse_exc),
                    },
                )
                saved = _save_assistant_response(request.session_id, fallback_response)
                yield _sse_message({"type": "final", "payload": saved.model_dump()})
                return
            structured = await agent._apply_experiment_fallback(structured)
            structured = agent._apply_plan_first_guardrail(structured)
            structured = agent._apply_phagescope_fallback(structured)
            structured = agent._apply_task_execution_followthrough_guardrail(structured)
            structured = agent._apply_completion_claim_guardrail(structured)
            agent._current_user_message = None

            # "Analyze" button from ExecutorPanel: force text-only response, no tool calls.
            if context.get("analysis_only"):
                structured.actions = []

            if not structured.actions:
                agent_result = await agent.execute_structured(structured)
                if request.session_id:
                    _set_session_plan_id(request.session_id, agent_result.bound_plan_id)
                tool_results = [
                    {
                        "name": step.action.name,
                        "summary": step.details.get("summary"),
                        "parameters": step.details.get("parameters"),
                        "result": step.details.get("result"),
                    }
                    for step in agent_result.steps
                    if step.action.kind == "tool_operation"
                ]

                actions_for_display = [
                    {
                        "kind": step.action.kind,
                        "name": step.action.name,
                        "parameters": step.action.parameters,
                        "order": step.action.order,
                        "blocking": step.action.blocking,
                        "status": "completed" if step.success else "failed",
                        "success": step.success,
                        "message": step.message,
                        "details": step.details,
                    }
                    for step in agent_result.steps
                ]

                metadata_payload: Dict[str, Any] = {
                    "intent": agent_result.primary_intent,
                    "success": agent_result.success,
                    "errors": agent_result.errors,
                    "plan_id": agent_result.bound_plan_id,
                    "plan_outline": agent_result.plan_outline,
                    "plan_persisted": agent_result.plan_persisted,
                    "status": "completed",
                    "unified_stream": True,
                    # In no-action scenarios, use the full reply as the primary analysis body.
                    "analysis_text": agent_result.reply or request.message,
                    "final_summary": None,
                    "actions": actions_for_display,
                    "raw_actions": [
                        step.action.model_dump() for step in agent_result.steps
                    ],
                }
                if tool_results:
                    metadata_payload["tool_results"] = [
                        entry
                        for entry in tool_results
                        if entry and isinstance(entry.get("result"), dict)
                    ]
                if agent_result.actions_summary:
                    metadata_payload["actions_summary"] = agent_result.actions_summary
                if agent_result.job_id:
                    metadata_payload["job_id"] = agent_result.job_id
                    metadata_payload["job_type"] = agent_result.job_type or "chat_action"
                    # Pull actual status from the job payload instead of agent_result.success.
                    job_snap = plan_decomposition_jobs.get_job_payload(agent_result.job_id)
                    if job_snap:
                        metadata_payload["job_status"] = job_snap.get("status", "queued")
                        metadata_payload["job"] = job_snap
                    else:
                        # For synchronously finished jobs, infer status from success.
                        metadata_payload["job_status"] = (
                                "succeeded" if agent_result.success else "failed"
                        )
                chat_response = ChatResponse(
                    response=agent_result.reply,
                    suggestions=agent_result.suggestions,
                    actions=actions_for_display,
                    metadata=metadata_payload,
                )
                saved = _save_assistant_response(request.session_id, chat_response)
                yield _sse_message({"type": "final", "payload": saved.model_dump()})
                return

            tracking_id = f"act_{uuid4().hex}"
            job_metadata = {
                "session_id": request.session_id,
                "mode": request.mode,
                "user_message": request.message,
            }
            job_params = {
                key: value
                for key, value in {
                    "mode": request.mode,
                    "session_id": request.session_id,
                    "plan_id": plan_session.plan_id,
                }.items()
                if value is not None
            }
            try:
                plan_decomposition_jobs.create_job(
                    plan_id=plan_session.plan_id,
                    task_id=None,
                    mode=request.mode or "assistant",
                    job_type="chat_action",
                    params=job_params,
                    metadata=job_metadata,
                    job_id=tracking_id,
                )
            except ValueError:
                pass

            structured_json = structured.model_dump_json()
            try:
                create_action_run(
                    run_id=tracking_id,
                    session_id=request.session_id,
                    user_message=request.message,
                    mode=request.mode,
                    plan_id=plan_session.plan_id,
                    context=context,
                    history=converted_history,
                    structured_json=structured_json,
                )
            except Exception as exc:
                logger.error("Failed to persist action run %s: %s", tracking_id, exc)
                raise

            pending_actions = [
                {
                    "kind": action.kind,
                    "name": action.name,
                    "parameters": action.parameters,
                    "order": action.order,
                    "blocking": action.blocking,
                    "status": "pending",
                    "success": None,
                    "message": None,
                    "details": None,
                }
                for action in structured.sorted_actions()
            ]
            for action in structured.sorted_actions():
                logger.info(
                    "[CHAT][STREAM][ASYNC] session=%s tracking=%s queued action=%s/%s order=%s params=%s",
                    request.session_id or "<new>",
                    tracking_id,
                    action.kind,
                    action.name,
                    action.order,
                    action.parameters,
                )
                try:
                    append_action_log_entry(
                        plan_id=plan_session.plan_id,
                        job_id=tracking_id,
                        job_type="chat_action",
                        sequence=action.order if isinstance(action.order, int) else None,
                        session_id=request.session_id,
                        user_message=request.message,
                        action_kind=action.kind,
                        action_name=action.name or "",
                        status="queued",
                        success=None,
                        message="Action queued for execution.",
                        parameters=action.parameters,
                        details=None,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("Failed to persist queued action log: %s", exc)

            suggestions = [
                "Actions have been generated; execution is running in the background.",
                "If it does not finish within two minutes, refresh the plan view or try again later.",
            ]
            plan_decomposition_jobs.append_log(
                tracking_id,
                "info",
                "Background action submitted and awaiting execution.",
                {
                    "session_id": request.session_id,
                    "plan_id": plan_session.plan_id,
                    "actions": [action.model_dump() for action in structured.actions],
                },
            )

            job_snapshot = plan_decomposition_jobs.get_job_payload(tracking_id)

            # Check whether user confirmation is required.
            requires_confirm = _requires_confirmation(structured.actions)

            if requires_confirm:
                # Generate a confirmation ID and store pending actions.
                confirmation_id = _generate_confirmation_id()
                _store_pending_confirmation(
                    confirmation_id=confirmation_id,
                    session_id=request.session_id or "",
                    actions=list(structured.actions),
                    structured=structured,
                    plan_id=plan_session.plan_id,
                    extra_context=agent.extra_context,
                )

                # Build a confirmation-required response.
                confirm_actions = [
                    {"kind": a.kind, "name": a.name}
                    for a in structured.actions
                    if (a.kind, a.name) in ACTIONS_REQUIRING_CONFIRMATION
                ]
                suggestions = [
                    "This action requires your confirmation before execution.",
                    "Please click the confirm button or call /chat/confirm to proceed.",
                ]
                chat_response = ChatResponse(
                    response=structured.llm_reply.message,
                    suggestions=suggestions,
                    actions=pending_actions,
                    metadata={
                        "status": "awaiting_confirmation",
                        "requires_confirmation": True,
                        "confirmation_id": confirmation_id,
                        "confirm_actions": confirm_actions,
                        "unified_stream": True,
                        "tracking_id": tracking_id,
                        "plan_id": plan_session.plan_id,
                        "raw_actions": [action.model_dump() for action in structured.actions],
                    },
                )
                saved = _save_assistant_response(request.session_id, chat_response)
                yield _sse_message({"type": "final", "payload": saved.model_dump()})
                logger.info(
                    "[CHAT][CONFIRMATION] Actions require confirmation: %s, confirmation_id=%s",
                    confirm_actions,
                    confirmation_id,
                )
                return

            # No confirmation needed; execute normally.
            bg_category = _classify_background_category(
                structured.actions, job_snapshot
            )
            chat_response = ChatResponse(
                response=structured.llm_reply.message,
                suggestions=suggestions,
                actions=pending_actions,
                metadata={
                    "status": "pending",
                    "unified_stream": True,
                    "tracking_id": tracking_id,
                    "plan_id": plan_session.plan_id,
                    "raw_actions": [action.model_dump() for action in structured.actions],
                    "type": "job_log",
                    "job_id": tracking_id,
                    "job_type": (job_snapshot or {}).get("job_type", "chat_action"),
                    "job_status": (job_snapshot or {}).get("status", "queued"),
                    "job": job_snapshot,
                    "job_logs": (job_snapshot or {}).get("logs"),
                    "background_category": bg_category,
                },
            )

            loop = asyncio.get_running_loop()
            queue = plan_decomposition_jobs.register_subscriber(tracking_id, loop)
            asyncio.create_task(_execute_action_run(tracking_id))

            saved = _save_assistant_response(request.session_id, chat_response)
            yield _sse_message({"type": "final", "payload": saved.model_dump()})

            if queue is None:
                return

            try:
                snapshot = plan_decomposition_jobs.get_job_payload(
                    tracking_id, include_logs=False
                )
                if snapshot is not None:
                    yield _sse_message(
                        {"type": "job_update", "payload": snapshot}
                    )
                while True:
                    try:
                        message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        heartbeat = plan_decomposition_jobs.get_job_payload(
                            tracking_id, include_logs=False
                        )
                        if heartbeat is None:
                            break
                        yield _sse_message(
                            {"type": "job_update", "payload": heartbeat}
                        )
                        continue
                    message.setdefault("job_id", tracking_id)
                    yield _sse_message({"type": "job_update", "payload": message})
                    if message.get("status") in {"succeeded", "failed"}:
                        break
            finally:
                plan_decomposition_jobs.unregister_subscriber(tracking_id, queue)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Chat streaming failed: %s", exc)
            yield _sse_message(
                {
                    "type": "error",
                    "message": "⚠️ Streaming request failed. Please try again.",
                    "error_type": type(exc).__name__,
                }
            )

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        event_generator(), media_type="text/event-stream", headers=headers
    )
