"""Streaming chat endpoint implementation."""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict

from fastapi import BackgroundTasks
from fastapi.responses import StreamingResponse

from app.services.plans.plan_session import PlanSession

from .background import _sse_message
from .models import ChatRequest
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
    _save_chat_message,
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

            # Unified stream runs DeepThink + real tool execution (web_search, etc.).
            # Plan binding adds plan_operation / task context; without a plan, tools still run.
            # The lightweight `stream_simple_chat` path does not execute tools and is not used here.
            log_ctx = "plan-bound" if agent.plan_session.plan_id is not None else "no-plan"
            logger.info("[CHAT] Unified agent stream (%s)", log_ctx)
            async for chunk in agent.process_unified_stream(message_to_send):
                yield chunk
            return
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
