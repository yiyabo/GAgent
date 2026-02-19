"""Backward-compatible chat router shim.

The active chat implementation lives in ``app.routers.chat``. This module keeps
legacy imports stable (including monkeypatch points used by older tests/tools).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from app.config.tool_policy import get_tool_policy, is_tool_allowed
from app.repository.chat_action_runs import fetch_action_run
from app.services.deep_think_agent import DeepThinkAgent
from app.services.plans.decomposition_jobs import get_current_job
from app.services.session_title_service import SessionNotFoundError
from tool_box import execute_tool

from .chat import routes as _routes_module
from .chat import stream as _stream_module
from .chat.action_execution import (
    _build_action_status_payloads,
    _build_brief_action_summary,
    _collect_created_tasks_from_steps,
    _execute_action_run,
    _generate_action_analysis,
    _generate_tool_analysis,
    _generate_tool_summary,
    get_action_status,
    retry_action_run,
)
from .chat.agent import StructuredChatAgent
from .chat.confirmation import ACTIONS_REQUIRING_CONFIRMATION
from .chat.models import (
    ActionStatusResponse,
    AgentResult,
    AgentStep,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatSessionAutoTitleBulkRequest,
    ChatSessionAutoTitleBulkResponse,
    ChatSessionAutoTitleRequest,
    ChatSessionAutoTitleResult,
    ChatSessionSettings,
    ChatSessionSummary,
    ChatSessionsResponse,
    ChatSessionUpdateRequest,
    ChatStatusResponse,
    ConfirmActionRequest,
    ConfirmActionResponse,
    StructuredReplyStreamParser,
)
from .chat.session_helpers import _resolve_plan_binding as _resolve_plan_binding_impl

_ACTION_RUN_ID_PATTERN = re.compile(r"\bact_[a-zA-Z0-9]+\b")

# Expose mutable compatibility hooks at module level.
_resolve_plan_binding = _resolve_plan_binding_impl

_execute_confirmed_actions = _routes_module._execute_confirmed_actions
autotitle_chat_session = _routes_module.autotitle_chat_session
bulk_autotitle_chat_sessions = _routes_module.bulk_autotitle_chat_sessions
chat_status = _routes_module.chat_status
confirm_pending_action = _routes_module.confirm_pending_action
delete_chat_session = _routes_module.delete_chat_session
get_chat_history = _routes_module.get_chat_history
get_pending_confirmation_status = _routes_module.get_pending_confirmation_status
head_chat_session = _routes_module.head_chat_session
list_chat_sessions = _routes_module.list_chat_sessions
router = _routes_module.router
update_chat_session = _routes_module.update_chat_session


def _coerce_plan_id(raw_plan_id: Any) -> Optional[int]:
    if raw_plan_id is None:
        return None
    if isinstance(raw_plan_id, int):
        return raw_plan_id
    try:
        return int(str(raw_plan_id).strip())
    except (TypeError, ValueError):
        return None


def _sync_binding_hook() -> None:
    # Keep monkeypatched compatibility hook in sync with active route modules.
    _routes_module._resolve_plan_binding = _resolve_plan_binding
    _stream_module._resolve_plan_binding = _resolve_plan_binding


async def chat_message(request: ChatRequest, background_tasks: BackgroundTasks):
    context = dict(getattr(request, "context", None) or {})
    incoming_plan_id = _coerce_plan_id(context.get("plan_id"))
    try:
        _resolve_plan_binding(getattr(request, "session_id", None), incoming_plan_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Chat request failed in strict mode: {exc}",
        ) from exc

    _sync_binding_hook()
    return await _routes_module.chat_message(request, background_tasks)


async def chat_stream(request: ChatRequest, background_tasks: BackgroundTasks):
    context = dict(getattr(request, "context", None) or {})
    incoming_plan_id = _coerce_plan_id(context.get("plan_id"))
    try:
        _resolve_plan_binding(getattr(request, "session_id", None), incoming_plan_id)
    except Exception as exc:
        async def _error_generator(err: Exception = exc):
            payload = {
                "type": "error",
                "error_type": type(err).__name__,
                "strict_mode": True,
                "message": f"Streaming request failed in strict mode: {err}",
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(_error_generator(), media_type="text/event-stream")

    _sync_binding_hook()
    return await _stream_module.chat_stream(request, background_tasks)


def _extract_source_action_run_id(
    context: Optional[Dict[str, Any]],
    user_message: str,
) -> Optional[str]:
    source_id = None
    if isinstance(context, dict):
        raw_source = context.get("source_job_id")
        if isinstance(raw_source, str) and raw_source.strip():
            source_id = raw_source.strip()
        elif raw_source is not None:
            source_id = str(raw_source).strip()
    if source_id and _ACTION_RUN_ID_PATTERN.fullmatch(source_id):
        return source_id

    message_match = _ACTION_RUN_ID_PATTERN.search(user_message or "")
    if message_match:
        return message_match.group(0)
    return None


def _collect_tool_results_for_analysis(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    result_payload = record.get("result")
    if not isinstance(result_payload, dict):
        return []

    collected: List[Dict[str, Any]] = []
    raw_tool_results = result_payload.get("tool_results")
    if isinstance(raw_tool_results, list):
        for item in raw_tool_results:
            if not isinstance(item, dict):
                continue
            result_data = item.get("result")
            if not isinstance(result_data, dict):
                continue
            collected.append(
                {
                    "name": item.get("name"),
                    "summary": item.get("summary"),
                    "parameters": item.get("parameters"),
                    "result": result_data,
                }
)
    if collected:
        return collected

    steps = result_payload.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            details = step.get("details")
            if not isinstance(details, dict):
                continue
            result_data = details.get("result")
            if not isinstance(result_data, dict):
                continue
            action_payload = step.get("action")
            action_name = None
            action_params = None
            if isinstance(action_payload, dict):
                action_name = action_payload.get("name")
                action_params = action_payload.get("parameters")
            collected.append(
                {
                    "name": action_name,
                    "summary": details.get("summary"),
                    "parameters": details.get("parameters") or action_params,
                    "result": result_data,
                }
            )
    return collected


async def _build_analysis_only_chat_response(
    *,
    user_message: str,
    context: Dict[str, Any],
    session_id: Optional[str],
    llm_provider: Optional[str] = None,
) -> Optional[ChatResponse]:
    if not context.get("analysis_only"):
        return None

    source_job_id = _extract_source_action_run_id(context, user_message)
    if not source_job_id:
        return ChatResponse(
            response=(
                "Analysis-only mode requires a valid `source_job_id` "
                "(for example `act_xxx`)."
            ),
            suggestions=[],
            actions=[],
            metadata={
                "analysis_only": True,
                "status": "completed",
            },
        )

    record = fetch_action_run(source_job_id)
    if not record:
        return ChatResponse(
            response=f"Background job `{source_job_id}` was not found.",
            suggestions=[],
            actions=[],
            metadata={
                "analysis_only": True,
                "status": "completed",
                "source_job_id": source_job_id,
                "source_job_status": "not_found",
            },
        )

    tool_results = _collect_tool_results_for_analysis(record)
    analysis_text: Optional[str] = None
    if tool_results:
        analysis_text = await _generate_tool_analysis(
            user_message=user_message,
            tool_results=tool_results,
            session_id=session_id,
            llm_provider=llm_provider,
        )

    result_payload = record.get("result")
    if not isinstance(result_payload, dict):
        result_payload = {}

    if not analysis_text:
        for key in ("analysis_text", "reply", "final_summary"):
            candidate = result_payload.get(key)
            if isinstance(candidate, str) and candidate.strip():
                analysis_text = candidate.strip()
                break

    if not analysis_text:
        status_text = str(record.get("status") or "unknown")
        errors = record.get("errors")
        if isinstance(errors, list) and errors:
            error_text = "; ".join(str(item) for item in errors)
        else:
            error_text = "None"
        analysis_text = (
            f"Background job `{source_job_id}` status: {status_text}.\n"
            "No structured tool output was available for deeper analysis.\n"
            f"Reported errors: {error_text}."
        )

    metadata: Dict[str, Any] = {
        "analysis_only": True,
        "status": "completed",
        "source_job_id": source_job_id,
        "source_job_status": record.get("status"),
        "source_job_created_at": record.get("created_at"),
        "source_job_started_at": record.get("started_at"),
        "source_job_finished_at": record.get("finished_at"),
    }
    source_plan_id = record.get("plan_id")
    if source_plan_id is not None:
        metadata["source_plan_id"] = source_plan_id
    if isinstance(record.get("errors"), list):
        metadata["source_errors"] = record.get("errors") or []
    if tool_results:
        metadata["tool_results"] = tool_results
    final_summary = result_payload.get("final_summary")
    if isinstance(final_summary, str) and final_summary.strip():
        metadata["final_summary"] = final_summary.strip()

    return ChatResponse(
        response=analysis_text,
        suggestions=[],
        actions=[],
        metadata=metadata,
    )


__all__ = [
    "ACTIONS_REQUIRING_CONFIRMATION",
    "ActionStatusResponse",
    "AgentResult",
    "AgentStep",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ChatSessionAutoTitleBulkRequest",
    "ChatSessionAutoTitleBulkResponse",
    "ChatSessionAutoTitleRequest",
    "ChatSessionAutoTitleResult",
    "ChatSessionSettings",
    "ChatSessionSummary",
    "ChatSessionsResponse",
    "ChatSessionUpdateRequest",
    "ChatStatusResponse",
    "ConfirmActionRequest",
    "ConfirmActionResponse",
    "DeepThinkAgent",
    "SessionNotFoundError",
    "StructuredChatAgent",
    "StructuredReplyStreamParser",
    "_build_action_status_payloads",
    "_build_analysis_only_chat_response",
    "_build_brief_action_summary",
    "_collect_created_tasks_from_steps",
    "_collect_tool_results_for_analysis",
    "_execute_action_run",
    "_execute_confirmed_actions",
    "_extract_source_action_run_id",
    "_generate_action_analysis",
    "_generate_tool_analysis",
    "_generate_tool_summary",
    "_resolve_plan_binding",
    "autotitle_chat_session",
    "bulk_autotitle_chat_sessions",
    "chat_message",
    "chat_status",
    "chat_stream",
    "confirm_pending_action",
    "delete_chat_session",
    "execute_tool",
    "fetch_action_run",
    "get_action_status",
    "get_chat_history",
    "get_current_job",
    "get_pending_confirmation_status",
    "get_tool_policy",
    "head_chat_session",
    "is_tool_allowed",
    "list_chat_sessions",
    "retry_action_run",
    "router",
    "update_chat_session",
]
