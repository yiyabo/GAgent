"""Chat APIs that orchestrate structured LLM responses and action dispatch."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple, Union
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_graph_rag_settings, get_search_settings
from app.config.decomposer_config import get_decomposer_settings
from app.config.executor_config import get_executor_settings
from app.repository.chat_action_runs import (
    create_action_run,
    fetch_action_run,
    update_action_run,
)
from app.repository.plan_repository import PlanRepository
from app.repository.plan_storage import (
    append_action_log_entry,
    record_decomposition_job,
    update_decomposition_job_status,
)
from app.services.foundation.settings import get_settings
from app.services.llm.llm_service import get_llm_service
from app.services.llm.structured_response import (
    LLMAction,
    LLMStructuredResponse,
    schema_as_json,
)
from app.services.plans.decomposition_jobs import (
    get_current_job,
    log_job_event,
    plan_decomposition_jobs,
    reset_current_job,
    set_current_job,
    start_decomposition_job_thread,
)
from app.services.plans.plan_decomposer import DecompositionResult, PlanDecomposer
from app.services.plans.plan_executor import PlanExecutor
from app.services.plans.plan_models import PlanTree
from app.services.plans.plan_session import PlanSession
from app.services.session_title_service import (
    SessionNotFoundError,
    SessionTitleService,
)
from tool_box import execute_tool

from . import register_router

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])
plan_repository = PlanRepository()
decomposer_settings = get_decomposer_settings()

VALID_SEARCH_PROVIDERS = {"builtin", "perplexity"}
plan_decomposer_service = PlanDecomposer(
    repo=plan_repository,
    settings=decomposer_settings,
)
plan_executor_service = PlanExecutor(repo=plan_repository)
session_title_service = SessionTitleService()
app_settings = get_settings()

register_router(
    namespace="chat",
    version="v1",
    path="/chat",
    router=router,
    tags=["chat"],
    description="Primary entry point for chat and plan management (structured LLM dialog)",
)


class ChatMessage(BaseModel):
    """Structure of an individual chat message."""

    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ChatRequest(BaseModel):
    """Chat request payload from the frontend."""

    message: str
    history: Optional[List[ChatMessage]] = None
    context: Optional[Dict[str, Any]] = None
    mode: Optional[str] = "assistant"
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    """Chat response returned to the frontend."""

    response: str
    suggestions: Optional[List[str]] = None
    actions: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None


class ActionStatusResponse(BaseModel):
    """Status envelope for background action execution."""

    tracking_id: str
    status: str
    plan_id: Optional[int] = None
    actions: Optional[List[Dict[str, Any]]] = None
    result: Optional[Dict[str, Any]] = None
    errors: Optional[List[str]] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ChatSessionSettings(BaseModel):
    """Session-level customization settings."""

    default_search_provider: Optional[Literal["builtin", "perplexity"]] = None


class ChatSessionSummary(BaseModel):
    """Summary row for a chat session list."""

    id: str
    name: Optional[str] = None
    name_source: Optional[str] = None
    is_user_named: Optional[bool] = None
    plan_id: Optional[int] = None
    plan_title: Optional[str] = None
    current_task_id: Optional[int] = None
    current_task_name: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_message_at: Optional[str] = None
    is_active: bool
    settings: Optional[ChatSessionSettings] = None


class ChatSessionsResponse(BaseModel):
    """Response wrapper for chat session listing."""

    sessions: List[ChatSessionSummary]
    total: int
    limit: int
    offset: int


class ChatSessionUpdateRequest(BaseModel):
    """Request to update core chat session attributes."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    is_active: Optional[bool] = None
    plan_id: Optional[int] = None
    plan_title: Optional[str] = None
    current_task_id: Optional[int] = None
    current_task_name: Optional[str] = None
    settings: Optional[ChatSessionSettings] = None


class ChatSessionAutoTitleRequest(BaseModel):
    """Request payload for automatic session titling."""

    force: bool = False
    strategy: Optional[str] = Field(default=None, description="Generation strategy (auto/heuristic/plan/llm/etc.)")


class ChatSessionAutoTitleResult(BaseModel):
    """Result returned after auto-titling a session."""

    session_id: str
    title: str
    source: str
    updated: bool = True
    previous_title: Optional[str] = None
    skipped_reason: Optional[str] = None


class ChatSessionAutoTitleBulkRequest(ChatSessionAutoTitleRequest):
    """Bulk auto-title request."""

    session_ids: Optional[List[str]] = None
    limit: Optional[int] = Field(default=20, ge=1, le=200)


class ChatSessionAutoTitleBulkResponse(BaseModel):
    """Response for bulk auto-title operations."""

    results: List[ChatSessionAutoTitleResult]
    processed: int


class ChatStatusResponse(BaseModel):
    """Status payload describing the chat service state."""

    status: str
    llm: Dict[str, Any]
    decomposer: Dict[str, Any]
    executor: Dict[str, Any]
    features: Dict[str, Any]
    warnings: List[str] = Field(default_factory=list)


@router.get("/sessions", response_model=ChatSessionsResponse)
async def list_chat_sessions(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    active: Optional[bool] = None,
):
    """List existing chat sessions."""
    from ..database import get_db  # lazy import

    try:
        with get_db() as conn:
            where_clauses: List[str] = []
            params: List[Any] = []
            if active is not None:
                where_clauses.append("s.is_active = ?")
                params.append(1 if active else 0)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            total_row = conn.execute(
                f"SELECT COUNT(1) AS total FROM chat_sessions s {where_sql}",
                params,
            ).fetchone()
            total = int(total_row["total"]) if total_row else 0

            session_rows = conn.execute(
                f"""
                WITH session_with_last AS (
                    SELECT
                        s.id,
                        s.name,
                        s.name_source,
                        s.is_user_named,
                        s.metadata,
                        s.plan_id,
                        s.plan_title,
                        s.current_task_id,
                        s.current_task_name,
                        s.created_at,
                        s.updated_at,
                        s.is_active,
                        COALESCE(
                            s.last_message_at,
                            (
                                SELECT MAX(m.created_at)
                                FROM chat_messages m
                                WHERE m.session_id = s.id
                            )
                        ) AS last_message_at
                    FROM chat_sessions s
                    {where_sql}
                )
                SELECT *
                FROM session_with_last
                ORDER BY COALESCE(last_message_at, updated_at, created_at) DESC, id ASC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()

        sessions = [_row_to_session_info(row) for row in session_rows]
        return ChatSessionsResponse(
            sessions=[ChatSessionSummary(**session) for session in sessions],
            total=total,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to list chat sessions: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load sessions") from exc


@router.patch("/sessions/{session_id}", response_model=ChatSessionSummary)
async def update_chat_session(
    session_id: str, payload: ChatSessionUpdateRequest
) -> ChatSessionSummary:
    """Update the core attributes of a chat session."""
    from ..database import get_db  # lazy import

    updates = payload.model_dump(exclude_unset=True)
    settings_update = updates.pop("settings", None)

    if not updates and settings_update is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        with get_db() as conn:
            # 确保session存在，如果不存在则自动创建
            _ensure_session_exists(session_id, conn)
            
            row = conn.execute(
                "SELECT id FROM chat_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Session not found")

            set_clauses: List[str] = []
            params: List[Any] = []
            if settings_update is not None:
                metadata_dict = _load_session_metadata_dict(conn, session_id)
                provider = settings_update.get("default_search_provider")
                if provider is not None:
                    normalized = _normalize_search_provider(provider)
                    if normalized is None:
                        raise HTTPException(
                            status_code=422,
                            detail="Invalid default_search_provider value",
                        )
                    metadata_dict["default_search_provider"] = normalized
                else:
                    metadata_dict.pop("default_search_provider", None)
                set_clauses.append("metadata=?")
                params.append(_dump_metadata(metadata_dict))

            if "name" in updates:
                set_clauses.append("name=?")
                params.append(updates["name"])
                set_clauses.append("name_source=?")
                params.append("user" if updates["name"] else "default")
                set_clauses.append("is_user_named=?")
                params.append(1 if updates["name"] else 0)

            if "is_active" in updates:
                set_clauses.append("is_active=?")
                params.append(1 if updates["is_active"] else 0)

            plan_title_sentinel = object()
            plan_title_override = updates.get("plan_title", plan_title_sentinel)
            if "plan_id" in updates:
                plan_id_value = updates["plan_id"]
                set_clauses.append("plan_id=?")
                params.append(plan_id_value)

                if plan_title_override is plan_title_sentinel:
                    plan_title_override = _lookup_plan_title(conn, plan_id_value)

            if plan_title_override is not plan_title_sentinel:
                set_clauses.append("plan_title=?")
                params.append(plan_title_override)

            if "current_task_id" in updates:
                set_clauses.append("current_task_id=?")
                params.append(updates["current_task_id"])

            if "current_task_name" in updates:
                set_clauses.append("current_task_name=?")
                params.append(updates["current_task_name"])

            if not set_clauses:
                raise HTTPException(status_code=400, detail="No valid fields to update")

            set_clauses.append("updated_at=CURRENT_TIMESTAMP")
            sql = f"UPDATE chat_sessions SET {', '.join(set_clauses)} WHERE id=?"
            params.append(session_id)
            conn.execute(sql, params)
            conn.commit()

            session_info = _fetch_session_info(conn, session_id)
            if not session_info:
                raise HTTPException(status_code=404, detail="Session not found")
            return ChatSessionSummary(**session_info)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to update chat session %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update session") from exc


@router.post(
    "/sessions/{session_id}/autotitle",
    response_model=ChatSessionAutoTitleResult,
)
async def autotitle_chat_session(
    session_id: str,
    payload: ChatSessionAutoTitleRequest,
) -> ChatSessionAutoTitleResult:
    """Auto-generate a session title from context."""
    try:
        result = session_title_service.generate_for_session(
            session_id,
            force=payload.force,
            strategy=payload.strategy,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to auto-title session %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail="Failed to generate title") from exc

    return ChatSessionAutoTitleResult(
        session_id=result.session_id,
        title=result.title,
        source=result.source,
        updated=result.updated,
        previous_title=result.previous_title,
        skipped_reason=result.skipped_reason,
    )


@router.post(
    "/sessions/autotitle/bulk",
    response_model=ChatSessionAutoTitleBulkResponse,
)
async def bulk_autotitle_chat_sessions(
    payload: ChatSessionAutoTitleBulkRequest,
) -> ChatSessionAutoTitleBulkResponse:
    """Bulk-generate session titles."""
    try:
        results = session_title_service.bulk_generate(
            session_ids=payload.session_ids,
            force=payload.force,
            strategy=payload.strategy,
            limit=payload.limit,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to bulk auto-title chat sessions: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to bulk auto-title sessions") from exc

    response_items = [
        ChatSessionAutoTitleResult(
            session_id=item.session_id,
            title=item.title,
            source=item.source,
            updated=item.updated,
            previous_title=item.previous_title,
            skipped_reason=item.skipped_reason,
        )
        for item in results
    ]
    return ChatSessionAutoTitleBulkResponse(
        results=response_items,
        processed=len(response_items),
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_chat_session(
    session_id: str, archive: bool = Query(False)
) -> Response:
    """Delete or archive a chat session."""
    from ..database import get_db  # lazy import

    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, is_active FROM chat_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Session not found")

            if archive:
                conn.execute(
                    """
                    UPDATE chat_sessions
                    SET is_active=0,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (session_id,),
                )
                logger.info("Archived chat session %s", session_id)
            else:
                conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
                logger.info("Deleted chat session %s", session_id)
        return Response(status_code=204)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to delete chat session %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete session") from exc


@router.get("/status", response_model=ChatStatusResponse)
async def chat_status() -> ChatStatusResponse:
    """Return the chat system and LLM status."""
    warnings: List[str] = []

    llm_payload: Dict[str, Any] = {
        "provider": None,
        "model": None,
        "api_url": None,
        "has_api_key": False,
        "mock_mode": False,
    }

    try:
        llm_service = get_llm_service()
        client = getattr(llm_service, "client", None)
        if client is None:
            warnings.append("LLM client unavailable")
        else:
            llm_payload.update({
                "provider": getattr(client, "provider", None),
                "model": getattr(client, "model", None),
                "api_url": getattr(client, "url", None),
                "has_api_key": bool(getattr(client, "api_key", None)),
                "mock_mode": bool(getattr(client, "mock", False)),
            })
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append(f"LLM initialisation failed: {exc}")

    decomposer_info = {
        "provider": decomposer_settings.provider,
        "model": decomposer_settings.model,
        "auto_on_create": decomposer_settings.auto_on_create,
        "max_depth": decomposer_settings.max_depth,
        "total_node_budget": decomposer_settings.total_node_budget,
    }

    executor_settings = get_executor_settings()
    executor_info = {
        "provider": executor_settings.provider,
        "model": executor_settings.model,
        "serial": executor_settings.serial,
        "use_context": executor_settings.use_context,
        "max_tasks": executor_settings.max_tasks,
    }

    features = {
        "auto_decompose": bool(decomposer_settings.auto_on_create),
        "plan_executor": bool(executor_settings.model or executor_settings.provider),
        "structured_actions": True,
    }

    status_value = "ready" if not warnings else "degraded"

    return ChatStatusResponse(
        status=status_value,
        llm=llm_payload,
        decomposer=decomposer_info,
        executor=executor_info,
        features=features,
        warnings=warnings,
    )


@router.get("/history/{session_id}")
async def get_chat_history(session_id: str, limit: int = 50):
    """Fetch history for a specific session."""
    try:
        messages = _load_chat_history(session_id, limit)
        return {
            "success": True,
            "session_id": session_id,
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "metadata": msg.metadata,
                }
                for msg in messages
            ],
            "total": len(messages),
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to get chat history: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/message", response_model=ChatResponse)
async def chat_message(request: ChatRequest, background_tasks: BackgroundTasks):
    """Main chat entry: respond with LLM actions first, then execute in the background."""
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
            "[CHAT][REQ] session=%s plan=%s mode=%s message=%s",
            request.session_id or "<new>",
            plan_session.plan_id,
            request.mode or "assistant",
            request.message,
        )

        if plan_session.plan_id is not None:
            context["plan_id"] = plan_session.plan_id
        else:
            context.pop("plan_id", None)

        converted_history = _convert_history_to_agent_format(request.history)

        session_settings: Dict[str, Any] = {}
        if request.session_id:
            _save_chat_message(request.session_id, "user", request.message)
            session_settings = _get_session_settings(request.session_id)

        explicit_provider = _normalize_search_provider(
            context.get("default_search_provider")
        )
        if explicit_provider:
            context["default_search_provider"] = explicit_provider
        else:
            session_provider = session_settings.get("default_search_provider")
            if session_provider:
                context["default_search_provider"] = session_provider

        agent = StructuredChatAgent(
            mode=request.mode,
            plan_session=plan_session,
            plan_decomposer=plan_decomposer_service,
            plan_executor=plan_executor_service,
            session_id=request.session_id,
            conversation_id=_derive_conversation_id(request.session_id),
            history=converted_history,
            extra_context=context,
        )

        structured = await agent.get_structured_response(request.message)

        if not structured.actions:
            agent_result = await agent.execute_structured(structured)
            if request.session_id:
                _set_session_plan_id(request.session_id, agent_result.bound_plan_id)
            if agent_result.steps:
                for step in agent_result.steps:
                    logger.info(
                        "[CHAT][SYNC] session=%s action=%s/%s success=%s message=%s",
                        request.session_id or "<new>",
                        step.action.kind,
                        step.action.name,
                        step.success,
                        step.message,
                    )
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
            metadata_payload: Dict[str, Any] = {
                "intent": agent_result.primary_intent,
                "success": agent_result.success,
                "errors": agent_result.errors,
                "plan_id": agent_result.bound_plan_id,
                "plan_outline": agent_result.plan_outline,
                "plan_persisted": agent_result.plan_persisted,
                "status": "completed",
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
                metadata_payload["job_status"] = (
                    "completed" if agent_result.success else "failed"
                )
            chat_response = ChatResponse(
                response=agent_result.reply,
                suggestions=agent_result.suggestions,
                actions=[step.action_payload for step in agent_result.steps],
                metadata=metadata_payload,
            )
            return _save_assistant_response(request.session_id, chat_response)

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
                "[CHAT][ASYNC] session=%s tracking=%s queued action=%s/%s order=%s params=%s",
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
        chat_response = ChatResponse(
            response=structured.llm_reply.message,
            suggestions=suggestions,
            actions=pending_actions,
            metadata={
                "status": "pending",
                "tracking_id": tracking_id,
                "plan_id": plan_session.plan_id,
                "raw_actions": [action.model_dump() for action in structured.actions],
                "type": "job_log",
                "job_id": tracking_id,
                "job_type": (job_snapshot or {}).get("job_type", "chat_action"),
                "job_status": (job_snapshot or {}).get("status", "queued"),
                "job": job_snapshot,
                "job_logs": (job_snapshot or {}).get("logs"),
            },
        )

        background_tasks.add_task(_execute_action_run, tracking_id)

        return _save_assistant_response(request.session_id, chat_response)

    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Chat processing failed: %s", exc)
        error_message = "⚠️ Something went wrong while processing the request. Try again later or rephrase."
        fallback = ChatResponse(
            response=error_message,
            suggestions=["Retry", "Try another phrasing", "Contact the administrator"],
            actions=[],
            metadata={"error": True, "error_type": type(exc).__name__},
        )
        return _save_assistant_response(request.session_id, fallback)


# ---------------------------------------------------------------------------
# Data persistence and helper utilities
# ---------------------------------------------------------------------------


def _derive_conversation_id(session_id: Optional[str]) -> Optional[int]:
    """Map session_id to a stable integer ID."""
    if not session_id:
        return None
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    return int(digest, 16)


def _convert_history_to_agent_format(
    history: Optional[List[ChatMessage]],
) -> List[Dict[str, Any]]:
    """Transform frontend history messages into agent-ready format."""
    if not history:
        return []
    return [{"role": msg.role, "content": msg.content} for msg in history]


def _loads_metadata(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:  # pragma: no cover - best effort parsing
        return None


def _normalize_search_provider(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if candidate in VALID_SEARCH_PROVIDERS:
        return candidate
    return None


def _dump_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    if not metadata:
        return None
    if not isinstance(metadata, dict):
        return None
    if not metadata:
        return None
    return json.dumps(metadata, ensure_ascii=False)


def _extract_session_settings(
    metadata: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not metadata:
        return None
    provider = _normalize_search_provider(metadata.get("default_search_provider"))
    if not provider:
        return None
    return {"default_search_provider": provider}


def _lookup_plan_title(conn, plan_id: Optional[int]) -> Optional[str]:
    if plan_id is None:
        return None
    row = conn.execute("SELECT title FROM plans WHERE id=?", (plan_id,)).fetchone()
    if not row:
        return None
    return row["title"]


def _row_to_session_info(row) -> Dict[str, Any]:
    """Convert a SQLite row into a session info dictionary."""
    metadata = None
    if isinstance(row, dict) or hasattr(row, "keys"):
        try:
            metadata = _loads_metadata(row["metadata"])
        except Exception:
            metadata = None
    info = {
        "id": row["id"],
        "name": row["name"],
        "name_source": row["name_source"] if "name_source" in row.keys() else None,
        "is_user_named": (
            bool(row["is_user_named"])
            if "is_user_named" in row.keys() and row["is_user_named"] is not None
            else None
        ),
        "plan_id": row["plan_id"],
        "plan_title": row["plan_title"],
        "current_task_id": row["current_task_id"],
        "current_task_name": row["current_task_name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_message_at": row["last_message_at"],
        "is_active": bool(row["is_active"]) if row["is_active"] is not None else True,
    }
    settings = _extract_session_settings(metadata)
    if settings:
        info["settings"] = settings
    else:
        info["settings"] = None
    return info


def _fetch_session_info(conn, session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve information for a specific session."""
    row = conn.execute(
        """
        SELECT
            s.id,
            s.name,
            s.name_source,
            s.is_user_named,
            s.metadata,
            s.plan_id,
            s.plan_title,
            s.current_task_id,
            s.current_task_name,
            s.created_at,
            s.updated_at,
            s.is_active,
            COALESCE(
                s.last_message_at,
                (
                    SELECT MAX(m.created_at)
                    FROM chat_messages m
                    WHERE m.session_id = s.id
                )
            ) AS last_message_at
        FROM chat_sessions s
        WHERE s.id = ?
        """,
        (session_id,),
    ).fetchone()
    if not row:
        return None
    return _row_to_session_info(row)


def _load_session_metadata_dict(conn, session_id: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT metadata FROM chat_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not row:
        return {}
    data = _loads_metadata(row["metadata"])
    return data or {}


def _get_session_settings(session_id: str) -> Dict[str, Any]:
    from ..database import get_db  # lazy import to avoid cycles

    with get_db() as conn:
        metadata = _load_session_metadata_dict(conn, session_id)
    settings = _extract_session_settings(metadata)
    return settings or {}


def _ensure_session_exists(
    session_id: str, conn, plan_id: Optional[int] = None
) -> Optional[int]:
    """Ensure the chat_sessions table contains this session."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, plan_id FROM chat_sessions WHERE id = ?", (session_id,))
    row = cursor.fetchone()
    if not row:
        plan_title = _lookup_plan_title(conn, plan_id)
        cursor.execute(
            """
            INSERT INTO chat_sessions (
                id,
                name,
                name_source,
                is_user_named,
                metadata,
                plan_id,
                plan_title,
                last_message_at,
                created_at,
                updated_at,
                is_active
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1
            )
            """,
            (
                session_id,
                f"Session {session_id[:8]}",
                "default",
                0,
                None,
                plan_id,
                plan_title,
            ),
        )
        logger.info("Created new chat session: %s (plan_id=%s)", session_id, plan_id)
        return plan_id

    current_plan_id = row["plan_id"]
    if plan_id is not None and current_plan_id != plan_id:
        plan_title = _lookup_plan_title(conn, plan_id)
        cursor.execute(
            """
            UPDATE chat_sessions
            SET plan_id=?,
                plan_title=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (plan_id, plan_title, session_id),
        )
        logger.info(
            "Updated chat session %s binding to plan %s (was %s)",
            session_id,
            plan_id,
            current_plan_id,
        )
        return plan_id
    return current_plan_id


def _resolve_plan_binding(
    session_id: Optional[str], requested_plan_id: Optional[int]
) -> Optional[int]:
    """Determine the final bound plan ID based on session state and request parameters."""
    if not session_id:
        return requested_plan_id

    from ..database import get_db  # lazy import

    with get_db() as conn:
        current_plan_id = _ensure_session_exists(session_id, conn, requested_plan_id)
        if current_plan_id is not None:
            return current_plan_id
    return requested_plan_id


def _set_session_plan_id(session_id: str, plan_id: Optional[int]) -> None:
    """Update the plan binding for the session."""
    from ..database import get_db  # lazy import

    with get_db() as conn:
        _ensure_session_exists(session_id, conn)
        plan_title = _lookup_plan_title(conn, plan_id)
        conn.execute(
            """
            UPDATE chat_sessions
            SET plan_id=?,
                plan_title=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (plan_id, plan_title, session_id),
        )
        conn.commit()


def _save_chat_message(
    session_id: str, role: str, content: str, metadata: Optional[Dict[str, Any]] = None
) -> None:
    """Persist chat message."""
    try:
        from ..database import get_db  # lazy import to avoid circular deps

        with get_db() as conn:
            _ensure_session_exists(session_id, conn)
            cursor = conn.cursor()
            metadata_json = (
                json.dumps(metadata, ensure_ascii=False) if metadata else None
            )
            logger.info(
                "[CHAT][SAVE] session=%s role=%s content=%s metadata=%s",
                session_id,
                role,
                content,
                metadata,
            )
            cursor.execute(
                """
                INSERT INTO chat_messages (session_id, role, content, metadata)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, role, content, metadata_json),
            )
            
            # Process message through chat memory middleware
            try:
                from ..services.memory.chat_memory_middleware import get_chat_memory_middleware
                
                middleware = get_chat_memory_middleware()
                # Run async middleware in background (fire and forget)
                asyncio.create_task(
                    middleware.process_message(
                        content=content,
                        role=role,
                        session_id=session_id
                    )
                )
            except Exception as mem_err:
                logger.warning(f"Failed to process chat memory: {mem_err}")
            cursor.execute(
                """
                UPDATE chat_sessions
                SET last_message_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (session_id,),
            )
            conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to save chat message: %s", exc)


def _load_chat_history(session_id: str, limit: int = 50) -> List[ChatMessage]:
    """Load session history."""
    try:
        from ..database import get_db  # lazy import

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT role, content, metadata, created_at
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (session_id, limit),
            )
            rows = cursor.fetchall()

        return [
            ChatMessage(
                role=role,
                content=content,
                timestamp=created_at,
                metadata=_loads_metadata(metadata_raw),
            )
            for role, content, metadata_raw, created_at in rows
        ]
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to load history: %s", exc)
        return []


def _save_assistant_response(
    session_id: Optional[str], response: ChatResponse
) -> ChatResponse:
    """Persist assistant response."""
    if session_id and response.response:
        _save_chat_message(
            session_id,
            "assistant",
            response.response,
            metadata=response.metadata,
        )
    return response


def _update_message_metadata_by_tracking(
    session_id: Optional[str],
    tracking_id: Optional[str],
    updater: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> None:
    if not session_id or not tracking_id:
        return
    from ..database import get_db  # lazy import

    pattern = f'%"tracking_id": "{tracking_id}"%'
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, metadata FROM chat_messages WHERE session_id=? AND metadata LIKE ? ORDER BY id DESC LIMIT 1",
                (session_id, pattern),
            ).fetchone()
            if not row:
                return
            current = _loads_metadata(row["metadata"]) or {}
            updated = updater(dict(current))
            conn.execute(
                "UPDATE chat_messages SET metadata=? WHERE id=?",
                (json.dumps(updated, ensure_ascii=False), row["id"]),
            )
            conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "Failed to update chat message metadata for %s: %s", tracking_id, exc
        )


def _merge_async_metadata(
    existing: Optional[Dict[str, Any]],
    *,
    status: str,
    tracking_id: str,
    plan_id: Optional[int],
    actions: List[Dict[str, Any]],
    actions_summary: Optional[List[Dict[str, Any]]],
    tool_results: List[Dict[str, Any]],
    errors: List[str],
    job_id: Optional[str] = None,
    job_payload: Optional[Dict[str, Any]] = None,
    job_type: Optional[str] = None,
) -> Dict[str, Any]:
    metadata = dict(existing or {})
    metadata["status"] = status
    metadata["tracking_id"] = tracking_id
    if plan_id is not None:
        metadata["plan_id"] = plan_id
    metadata["actions"] = actions
    metadata["action_list"] = actions
    if actions_summary:
        metadata["actions_summary"] = actions_summary
    elif "actions_summary" in metadata:
        metadata.pop("actions_summary")
    if tool_results:
        metadata["tool_results"] = tool_results
    elif "tool_results" in metadata:
        metadata.pop("tool_results")
    metadata["errors"] = errors or []
    if "raw_actions" not in metadata and actions:
        metadata["raw_actions"] = actions

    if job_id:
        metadata["type"] = "job_log"
        metadata["job_id"] = job_id
        metadata["job_type"] = job_type or metadata.get("job_type") or "chat_action"
        if job_payload:
            metadata["job"] = job_payload
            metadata["job_status"] = job_payload.get("status")
            metadata.setdefault("plan_id", job_payload.get("plan_id"))
            if "logs" in job_payload:
                metadata["job_logs"] = job_payload.get("logs")

    for action in actions or []:
        details = action.get("details") or {}
        embedded_job = details.get("decomposition_job")
        if embedded_job and "job_id" not in metadata:
            metadata["type"] = "job_log"
            metadata["job"] = embedded_job
            metadata["job_id"] = embedded_job.get("job_id")
            metadata["job_status"] = embedded_job.get("status")
            if embedded_job.get("job_type"):
                metadata["job_type"] = embedded_job.get("job_type")
            metadata.setdefault("plan_id", embedded_job.get("plan_id"))
            metadata["job_logs"] = embedded_job.get("logs")
        if "target_task_name" not in metadata:
            if "target_task_name" in details:
                metadata["target_task_name"] = details["target_task_name"]
            elif "title" in details:
                metadata["target_task_name"] = details["title"]

    return metadata


async def _generate_tool_summary(
    user_message: str,
    tool_results: List[Dict[str, Any]],
    session_id: Optional[str] = None,
) -> Optional[str]:
    """
    让 LLM 基于工具执行结果生成自然语言总结。
    
    Args:
        user_message: 用户的原始问题
        tool_results: 工具执行结果列表
        session_id: 会话 ID（用于日志）
        
    Returns:
        LLM 生成的总结文本，如果失败则返回 None
    """
    try:
        # 构建工具结果的描述
        tools_description = []
        for idx, tool_result in enumerate(tool_results, 1):
            tool_name = tool_result.get("name", "unknown")
            summary = tool_result.get("summary", "")
            result_data = tool_result.get("result", {})
            
            # 提取关键信息
            tool_desc = f"{idx}. 工具: {tool_name}"
            if summary:
                tool_desc += f"\n   执行摘要: {summary}"
            
            # 添加结果详情
            if isinstance(result_data, dict):
                # 提取有用的字段
                useful_fields = ["output", "stdout", "stderr", "success", "error"]
                for field in useful_fields:
                    if field in result_data and result_data[field]:
                        value = result_data[field]
                        if isinstance(value, str) and len(value) > 500:
                            value = value[:500] + "..."
                        tool_desc += f"\n   {field}: {value}"
            
            tools_description.append(tool_desc)
        
        tools_text = "\n\n".join(tools_description)
        
        # Use prompt manager for internationalized prompts
        from app.prompts import prompt_manager
        
        intro = prompt_manager.get("chat.tool_summary.intro")
        user_q_label = prompt_manager.get("chat.tool_summary.user_question")
        tools_label = prompt_manager.get("chat.tool_summary.tools_executed")
        instruction = prompt_manager.get("chat.tool_summary.instruction")
        requirements = prompt_manager.get_category("chat")["tool_summary"]["requirements"]
        response_label = prompt_manager.get("chat.tool_summary.response_prompt")
        
        requirements_text = "\n".join(requirements)
        
        prompt = f"""{intro}

{user_q_label}
{user_message}

{tools_label}
{tools_text}

{instruction}
{requirements_text}

{response_label}"""

        # 调用 LLM
        llm_service = get_llm_service()
        summary = await llm_service.chat_async(prompt)
        
        return summary.strip() if summary else None
        
    except Exception as exc:
        logger.error(
            "[CHAT][SUMMARY] Failed to generate summary for session=%s: %s",
            session_id,
            exc,
        )
        return None


async def _execute_action_run(run_id: str) -> None:
    record = fetch_action_run(run_id)
    if not record:
        logger.warning("Action run %s not found when executing", run_id)
        return

    try:
        update_action_run(run_id, status="running")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to mark action run %s as running: %s", run_id, exc)

    logger.info(
        "[CHAT][ASYNC][START] tracking=%s session=%s plan=%s",
        run_id,
        record.get("session_id"),
        record.get("plan_id"),
    )

    plan_session = PlanSession(repo=plan_repository, plan_id=record.get("plan_id"))
    try:
        plan_session.refresh()
    except ValueError:
        plan_session.detach()

    job_plan_id = plan_session.plan_id
    job_metadata = {
        "session_id": record.get("session_id"),
        "mode": record.get("mode"),
        "user_message": record.get("user_message"),
    }
    job_params = {
        key: value
        for key, value in {
            "mode": record.get("mode"),
            "session_id": record.get("session_id"),
            "plan_id": job_plan_id,
        }.items()
        if value is not None
    }

    try:
        job = plan_decomposition_jobs.create_job(
            plan_id=job_plan_id,
            task_id=None,
            mode=record.get("mode") or "assistant",
            job_type="chat_action",
            params=job_params,
            metadata=job_metadata,
            job_id=run_id,
        )
    except ValueError:
        job = plan_decomposition_jobs.get_job(run_id)
        if job is None:
            job = plan_decomposition_jobs.create_job(
                plan_id=job_plan_id,
                task_id=None,
                mode=record.get("mode") or "assistant",
                job_type="chat_action",
                params=job_params,
                metadata=job_metadata,
            )

    job_token = set_current_job(job.job_id)
    try:
        if job_plan_id is not None:
            plan_decomposition_jobs.attach_plan(job.job_id, job_plan_id)

        plan_decomposition_jobs.append_log(
            job.job_id,
            "info",
            "Background action enqueued and awaiting execution.",
            {
                "session_id": record.get("session_id"),
                "plan_id": job_plan_id,
                "mode": record.get("mode"),
            },
        )

        context = dict(record.get("context") or {})
        history = record.get("history") or []
        provider_in_context = _normalize_search_provider(
            context.get("default_search_provider")
        )
        if provider_in_context:
            context["default_search_provider"] = provider_in_context
        elif record.get("session_id"):
            session_defaults = _get_session_settings(record["session_id"])
            fallback_provider = session_defaults.get("default_search_provider")
            if fallback_provider:
                context["default_search_provider"] = fallback_provider

        agent = StructuredChatAgent(
            mode=record.get("mode"),
            plan_session=plan_session,
            plan_decomposer=plan_decomposer_service,
            plan_executor=plan_executor_service,
            session_id=record.get("session_id"),
            conversation_id=_derive_conversation_id(record.get("session_id")),
            history=history,
            extra_context=context,
        )

        try:
            structured = LLMStructuredResponse.model_validate_json(
                record["structured_json"]
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Structured payload invalid for run %s: %s", run_id, exc)
            plan_decomposition_jobs.append_log(
                job.job_id,
                "error",
                "Failed to parse structured actions.",
                {"error": str(exc)},
            )
            plan_decomposition_jobs.mark_failure(job.job_id, str(exc))
            update_action_run(run_id, status="failed", errors=[str(exc)])
            logger.info(
                "[CHAT][ASYNC][DONE] tracking=%s status=failed errors=%s",
                run_id,
                exc,
            )
            return

        sorted_actions = structured.sorted_actions()
        primary_action = sorted_actions[0] if sorted_actions else None

        plan_decomposition_jobs.mark_running(job.job_id)
        plan_decomposition_jobs.append_log(
            job.job_id,
            "info",
            "Starting structured action execution.",
            {
                "action_total": len(sorted_actions),
                "first_action": primary_action.name if primary_action else None,
            },
        )

        try:
            result = await agent.execute_structured(structured)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Action run %s failed during execution: %s", run_id, exc)
            plan_decomposition_jobs.append_log(
                job.job_id,
                "error",
                "An exception occurred during execution.",
                {"error": str(exc)},
            )
            current_plan_id = plan_session.plan_id
            if current_plan_id is not None:
                plan_decomposition_jobs.attach_plan(job.job_id, current_plan_id)
            plan_decomposition_jobs.mark_failure(job.job_id, str(exc))
            update_action_run(run_id, status="failed", errors=[str(exc)])
            logger.info(
                "[CHAT][ASYNC][DONE] tracking=%s status=failed errors=%s",
                run_id,
                exc,
            )
            return

        status = "completed" if result.success else "failed"
        result_dict = result.model_dump()
        tool_results_payload: List[Dict[str, Any]] = []
        
        # 诊断日志：记录所有步骤
        logger.info(
            "[CHAT][TOOL_RESULTS] session=%s tracking=%s total_steps=%d success=%s",
            record.get("session_id"),
            run_id,
            len(result.steps),
            result.success,
        )
        
        for step in result.steps:
            logger.info(
                "[CHAT][TOOL_RESULTS] session=%s tracking=%s step_kind=%s step_name=%s step_success=%s",
                record.get("session_id"),
                run_id,
                step.action.kind,
                step.action.name,
                step.success,
            )
            
            if step.action.kind != "tool_operation":
                continue
            details = step.details or {}
            result_payload = details.get("result")
            
            # 诊断日志：记录result类型
            logger.info(
                "[CHAT][TOOL_RESULTS] session=%s tracking=%s tool=%s result_type=%s has_result=%s",
                record.get("session_id"),
                run_id,
                step.action.name,
                type(result_payload).__name__,
                result_payload is not None,
            )
            
            if isinstance(result_payload, dict):
                tool_results_payload.append({
                    "name": step.action.name,
                    "summary": details.get("summary"),
                    "parameters": details.get("parameters"),
                    "result": result_payload,
                })
            else:
                # 如果不是dict，尝试包装一下
                logger.warning(
                    "[CHAT][TOOL_RESULTS] session=%s tracking=%s tool=%s result is not dict, wrapping it",
                    record.get("session_id"),
                    run_id,
                    step.action.name,
                )
                tool_results_payload.append({
                    "name": step.action.name,
                    "summary": details.get("summary"),
                    "parameters": details.get("parameters"),
                    "result": {"output": str(result_payload)} if result_payload is not None else {},
                })
        
        if tool_results_payload:
            result_dict["tool_results"] = tool_results_payload
            logger.info(
                "[CHAT][TOOL_RESULTS] session=%s tracking=%s collected %d tool results",
                record.get("session_id"),
                run_id,
                len(tool_results_payload),
            )
        
        # Agent Loop: 让 LLM 基于工具结果生成最终总结
        # 关键：必须在 update_action_run 之前完成，否则前端会停止轮询
        final_summary = None
        if result.success and tool_results_payload:
            logger.info(
                "[CHAT][SUMMARY] session=%s tracking=%s Starting summary generation...",
                record.get("session_id"),
                run_id,
            )
            try:
                final_summary = await _generate_tool_summary(
                    user_message=record.get("user_message", ""),
                    tool_results=tool_results_payload,
                    session_id=record.get("session_id"),
                )
                if final_summary:
                    # 保存 LLM 的总结作为新的 assistant 消息
                    _save_chat_message(
                        session_id=record.get("session_id"),
                        role="assistant",
                        content=final_summary,
                        metadata={
                            "type": "tool_summary",
                            "tracking_id": run_id,
                            "tool_count": len(tool_results_payload),
                        },
                    )
                    # 将总结添加到 result_dict 中，前端可以直接显示
                    result_dict["final_summary"] = final_summary
                    logger.info(
                        "[CHAT][SUMMARY] session=%s tracking=%s Summary saved: %s",
                        record.get("session_id"),
                        run_id,
                        final_summary[:100] if len(final_summary) > 100 else final_summary,
                    )
                else:
                    logger.warning(
                        "[CHAT][SUMMARY] session=%s tracking=%s Summary generation returned empty",
                        record.get("session_id"),
                        run_id,
                    )
            except Exception as exc:
                logger.error(
                    "[CHAT][SUMMARY] session=%s tracking=%s Failed to generate summary: %s",
                    record.get("session_id"),
                    run_id,
                    exc,
                    exc_info=True,
                )
        
        # 现在才更新状态为 completed，前端会在下次轮询时看到总结消息
        update_kwargs: Dict[str, Any] = {
            "status": status,
            "result": result_dict,
            "errors": result.errors,
        }
        if result.bound_plan_id is not None:
            update_kwargs["plan_id"] = result.bound_plan_id
        
        logger.info(
            "[CHAT][SUMMARY] session=%s tracking=%s Updating action status to %s",
            record.get("session_id"),
            run_id,
            status,
        )
        update_action_run(run_id, **update_kwargs)

        job_snapshot = plan_decomposition_jobs.get_job_payload(job.job_id)

        _update_message_metadata_by_tracking(
            record.get("session_id"),
            run_id,
            lambda existing: _merge_async_metadata(
                existing,
                status=status,
                tracking_id=run_id,
                plan_id=result.bound_plan_id,
                actions=[step.action_payload for step in result.steps],
                actions_summary=result.actions_summary,
                tool_results=tool_results_payload,
                errors=result.errors,
                job_id=job.job_id,
                job_payload=job_snapshot,
                job_type=getattr(job, "job_type", None),
            ),
        )

        if record.get("session_id"):
            _set_session_plan_id(record["session_id"], result.bound_plan_id)

        final_plan_id = result.bound_plan_id or plan_session.plan_id
        if final_plan_id is not None:
            plan_decomposition_jobs.attach_plan(job.job_id, final_plan_id)

        stats_payload = {
            "step_count": len(result.steps),
            "success": result.success,
            "error_count": len(result.errors),
        }

        if result.success:
            plan_decomposition_jobs.append_log(
                job.job_id,
                "info",
                "Structured action execution completed.",
                stats_payload,
            )
            plan_decomposition_jobs.mark_success(
                job.job_id,
                result=result,
                stats=stats_payload,
            )
        else:
            error_message = result.errors[0] if result.errors else "Some actions failed"
            plan_decomposition_jobs.append_log(
                job.job_id,
                "error",
                "Structured actions finished with failures in some steps.",
                {**stats_payload, "errors": result.errors},
            )
            plan_decomposition_jobs.mark_failure(
                job.job_id,
                error_message,
                result=result,
                stats=stats_payload,
            )

        logger.info(
            "[CHAT][ASYNC][DONE] tracking=%s status=%s plan=%s errors=%s",
            run_id,
            status,
            result.bound_plan_id,
            result.errors,
        )
    finally:
        reset_current_job(job_token)


@router.get("/actions/{tracking_id}", response_model=ActionStatusResponse)
async def get_action_status(tracking_id: str):
    """Query background action execution status."""
    record = fetch_action_run(tracking_id)
    if not record:
        raise HTTPException(status_code=404, detail="Action run not found")

    actions, tool_results = _build_action_status_payloads(record)

    # 提取 final_summary 以便前端显示
    result_data = record.get("result") or {}
    final_summary = result_data.get("final_summary")
    
    metadata = {}
    if tool_results:
        metadata["tool_results"] = tool_results
    if final_summary:
        metadata["final_summary"] = final_summary
    
    return ActionStatusResponse(
        tracking_id=tracking_id,
        status=record["status"],
        plan_id=record.get("plan_id"),
        actions=actions,
        result=record.get("result"),
        errors=record.get("errors"),
        created_at=record.get("created_at"),
        started_at=record.get("started_at"),
        finished_at=record.get("finished_at"),
        metadata=metadata if metadata else None,
    )


def _build_action_status_payloads(
    record: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
    """Build action payload list based on stored structured/result data."""
    result = record.get("result") or {}
    steps = result.get("steps") or []
    tool_results: List[Dict[str, Any]] = []
    if steps:
        payloads: List[Dict[str, Any]] = []
        for step in steps:
            action = step.get("action") or {}
            details = step.get("details") or {}
            if isinstance(details, dict) and isinstance(details.get("result"), dict):
                tool_results.append(details["result"])
            payloads.append({
                "kind": action.get("kind"),
                "name": action.get("name"),
                "parameters": action.get("parameters"),
                "order": action.get("order"),
                "blocking": action.get("blocking"),
                "status": "completed" if step.get("success") else "failed",
                "success": step.get("success"),
                "message": step.get("message"),
                "details": details,
            })
        return payloads, (tool_results or None)

    try:
        structured = LLMStructuredResponse.model_validate_json(
            record["structured_json"]
        )
    except Exception:  # pragma: no cover - defensive
        return [], None

    payloads = [
        {
            "kind": action.kind,
            "name": action.name,
            "parameters": action.parameters,
            "order": action.order,
            "blocking": action.blocking,
            "status": record.get("status", "pending"),
            "success": None,
        }
        for action in structured.sorted_actions()
    ]
    return payloads, None


class AgentStep(BaseModel):
    """Single action record executed by the agent."""

    action: LLMAction
    success: bool
    message: str
    details: Dict[str, Any]

    @property
    def action_payload(self) -> Dict[str, Any]:
        return {
            "kind": self.action.kind,
            "name": self.action.name,
            "parameters": self.action.parameters,
            "order": self.action.order,
            "blocking": self.action.blocking,
            "success": self.success,
            "message": self.message,
            "details": self.details,
        }


class AgentResult(BaseModel):
    """Unified output from StructuredChatAgent."""

    reply: str
    steps: List[AgentStep]
    suggestions: List[str]
    primary_intent: Optional[str]
    success: bool
    bound_plan_id: Optional[int] = None
    plan_outline: Optional[str] = None
    plan_persisted: bool = False
    job_id: Optional[str] = None
    job_type: Optional[str] = None
    actions_summary: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class StructuredChatAgent:
    """Plan conversation agent using a structured schema."""

    MAX_HISTORY = 10

    def __init__(
        self,
        *,
        mode: Optional[str] = "assistant",
        plan_session: Optional[PlanSession] = None,
        plan_decomposer: Optional[PlanDecomposer] = None,
        plan_executor: Optional[PlanExecutor] = None,
        session_id: Optional[str] = None,
        conversation_id: Optional[int] = None,
        history: Optional[List[Dict[str, str]]] = None,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.mode = mode or "assistant"
        self.session_id = session_id
        self.conversation_id = conversation_id
        self.history = history or []
        self.extra_context = extra_context or {}
        provider = _normalize_search_provider(
            self.extra_context.get("default_search_provider")
        )
        if provider:
            self.extra_context["default_search_provider"] = provider
        elif "default_search_provider" in self.extra_context:
            self.extra_context.pop("default_search_provider", None)
        self.plan_session = plan_session or PlanSession(repo=plan_repository)
        self.plan_tree = self.plan_session.current_tree()
        self.schema_json = schema_as_json()
        self.llm_service = get_llm_service()
        self.plan_decomposer = plan_decomposer
        self.plan_executor = plan_executor
        self.decomposer_settings = decomposer_settings
        self._last_decomposition: Optional[DecompositionResult] = None
        self._decomposition_errors: List[str] = []
        self._decomposition_notes: List[str] = []
        self._dirty = False
        self._sync_job_id: Optional[str] = None
        self._current_user_message: Optional[str] = None
        self._include_action_summary = getattr(
            app_settings, "chat_include_action_summary", True
        )

    async def handle(self, user_message: str) -> AgentResult:
        structured = await self._invoke_llm(user_message)
        return await self.execute_structured(structured)

    async def get_structured_response(self, user_message: str) -> LLMStructuredResponse:
        """Return the raw structured response without executing actions."""
        return await self._invoke_llm(user_message)

    async def execute_structured(
        self, structured: LLMStructuredResponse
    ) -> AgentResult:
        steps: List[AgentStep] = []
        errors: List[str] = []
        try:
            job_id, job_type = self._resolve_job_meta()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to resolve job metadata: %s", exc)
            job_id = None
            job_type = "chat_action"

        for action in structured.sorted_actions():
            try:
                step = await self._execute_action(action)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Action execution failed: %s", exc)
                errors.append(str(exc))
                step = AgentStep(
                    action=action,
                    success=False,
                    message=f"Action execution failed: {exc}",
                    details={"exception": type(exc).__name__},
                )
            steps.append(step)

        suggestions = self._build_suggestions(structured, steps)
        success = all(step.success for step in steps) if steps else True
        primary_intent = steps[-1].action.name if steps else None
        plan_persisted = False
        if self.plan_session.plan_id is not None:
            try:
                plan_persisted = self._persist_if_dirty()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Failed to persist plan state: %s", exc)
                errors.append(f"Failed to save plan updates: {exc}")
        outline = None
        if self.plan_session.plan_id is not None:
            try:
                outline = self.plan_session.outline(max_depth=4, max_nodes=80)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Failed to build plan outline: %s", exc)

        if self._decomposition_errors:
            errors.extend(self._decomposition_errors)

        actions_summary = self._build_actions_summary(steps)
        reply_text = structured.llm_reply.message or ""
        if self._include_action_summary and actions_summary:
            reply_text = self._append_summary_to_reply(reply_text, actions_summary)

        result = AgentResult(
            reply=reply_text,
            steps=steps,
            suggestions=suggestions,
            primary_intent=primary_intent,
            success=success,
            bound_plan_id=self.plan_session.plan_id,
            plan_outline=outline,
            plan_persisted=plan_persisted,
            job_id=job_id,
            job_type=job_type,
            actions_summary=actions_summary,
            errors=errors,
        )

        if get_current_job() is None:
            self._sync_job_id = None
            if job_id:
                try:
                    update_decomposition_job_status(
                        self.plan_session.plan_id,
                        job_id=job_id,
                        status="succeeded" if success else "failed",
                        finished_at=datetime.utcnow(),
                        stats={
                            "step_count": len(steps),
                            "success": success,
                            "error_count": len(errors),
                        },
                        result=result.model_dump(),
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("Failed to update sync job status: %s", exc)
        self._current_user_message = None

        return result

    async def _invoke_llm(self, user_message: str) -> LLMStructuredResponse:
        self._current_user_message = user_message
        prompt = self._build_prompt(user_message)
        raw = await self.llm_service.chat_async(prompt, force_real=True)
        cleaned = self._strip_code_fence(raw)
        return LLMStructuredResponse.model_validate_json(cleaned)

    def _build_prompt(self, user_message: str) -> str:
        plan_bound = self.plan_session.plan_id is not None
        history_text = self._format_history()
        context_text = json.dumps(self.extra_context, ensure_ascii=False, indent=2)
        plan_outline = self.plan_session.outline(max_depth=4, max_nodes=60)
        plan_status = self._compose_plan_status(plan_bound)
        plan_catalog = self._compose_plan_catalog(plan_bound)
        actions_catalog = self._compose_action_catalog(plan_bound)
        guidelines = self._compose_guidelines(plan_bound)

        prompt_parts = [
            "You are an AI assistant that manages research plans represented as task trees.",
            f"Current mode: {self.mode}",
            f"Conversation ID: {self.conversation_id or 'N/A'}",
            f"Session binding: {plan_status}",
            f"Extra context:\n{context_text}",
            f"History (latest {self.MAX_HISTORY} messages):\n{history_text}",
            "\n=== Plan Overview ===",
            plan_outline,
        ]
        if plan_catalog:
            prompt_parts.append(plan_catalog)
        prompt_parts.extend([
            "\nReturn a JSON object that matches the following schema exactly:",
            self.schema_json,
            "\nAction catalog:",
            actions_catalog,
            "\nGuidelines:",
            guidelines,
            f"\nUser message: {user_message}",
            "Respond with the JSON object now.",
        ])
        return "\n".join(prompt_parts)

    def _compose_plan_status(self, plan_bound: bool) -> str:
        if plan_bound:
            assert self.plan_session.plan_id is not None
            return f"Currently bound Plan ID: {self.plan_session.plan_id}"
        return (
            "This session is not bound to any plan. Continue clarifying requirements, "
            "sharing suggestions, or using tools to assist the discussion. Only trigger "
            "plan-related actions when the user explicitly requests a new plan or wants "
            "to take over an existing one."
        )

    def _compose_plan_catalog(self, plan_bound: bool) -> str:
        if plan_bound:
            return ""
        summaries = self.plan_session.summaries_for_prompt(limit=10)
        return (
            "Available plans (up to 10, for reference):\n"
            f"{summaries}\n"
            "If the user wants to work with one of them, ask for the specific plan ID; otherwise keep clarifying needs."
        )

    def _compose_action_catalog(self, plan_bound: bool) -> str:
        base_actions = [
            "- system_operation: help",
            "- tool_operation: web_search (use for live web information; requires `query`, optional provider/max_results)",
            "- tool_operation: graph_rag (query the phage-host knowledge graph; requires `query`, optional top_k/hops/return_subgraph/focus_entities)",
            "- tool_operation: claude_code (execute complex coding tasks using Claude AI with full local file access; requires `task`, optional allowed_tools/add_dirs)",
        ]
        if plan_bound:
            plan_actions = [
                "- plan_operation: create_plan, list_plans, execute_plan, delete_plan",
                "- task_operation: create_task, update_task, update_task_instruction, move_task, delete_task, decompose_task, show_tasks, query_status, rerun_task",
                "- context_request: request_subgraph (request additional task context; this response must not include other actions)",
            ]
        else:
            plan_actions = [
                "- plan_operation: create_plan  # only when the user explicitly asks to create a plan",
                "- plan_operation: list_plans  # list candidates; do not execute or mutate tasks while unbound",
            ]
        return "\n".join(base_actions + plan_actions)

    def _compose_guidelines(self, plan_bound: bool) -> str:
        common_rules = [
            "Return only a JSON object that matches the schema above—no code fences or additional commentary.",
            "`llm_reply.message` must be natural language directed to the user.",
            "Fill `actions` in execution order (`order` starts at 1); use an empty array if no actions are required.",
            "Use the `kind`/`name` pairs from the action catalog without inventing new values.",
            "A `request_subgraph` reply may contain only that action.",
            "Plan nodes do not provide a `priority` field; avoid fabricating it. `status` reflects progress and may be referenced when helpful.",
            "When the user explicitly asks to execute, run, or rerun a task or the plan, include the matching action or explain why it cannot proceed.",
        ]
        if plan_bound:
            scenario_rules = [
                "Verify that dependencies and prerequisite tasks are satisfied before executing a plan or task.",
                "When the user wants to run the entire plan, call `plan_operation.execute_plan` and provide a summary if appropriate.",
                "When the user targets a specific task (for example, \"run the first task\" or \"rerun task 42\"), call `task_operation.show_tasks` first if the ID is unclear, then `task_operation.rerun_task` with a concrete `task_id`.",
                "Use `web_search` or `graph_rag` only when the user explicitly asks for web data or knowledge-graph lookup; otherwise rely on available context or ask clarifying questions.",
                "When `web_search` is used, craft a clear query and summarize results with sources. When `graph_rag` is used, describe phage-related insights and cite triples when helpful.",
                "After gathering supporting information, continue scheduling or executing the requested plan or tasks—do not stop at preparation only.",
            ]
        else:
            scenario_rules = [
                "Do not create, modify, or execute tasks while the session is unbound; instead clarify needs via dialogue or tools.",
                "Feel free to ask follow-up questions, summarize, or retrieve information that helps the user decide whether a plan is needed.",
                "Invoke `plan_operation` only when the user explicitly requests a plan or provides an existing plan ID.",
                "Use `web_search` or `graph_rag` only when the user clearly asks for live search or knowledge-graph access; otherwise respond or confirm intent first.",
            ]
        all_rules = common_rules + scenario_rules
        return "\n".join(
            f"{idx}. {rule}" for idx, rule in enumerate(all_rules, start=1)
        )

    def _resolve_job_meta(self) -> Tuple[str, str]:
        job_id = get_current_job()
        job_type = "chat_action"
        if job_id:
            job = plan_decomposition_jobs.get_job(job_id)
            if job is not None and getattr(job, "job_type", None):
                job_type = job.job_type
            return job_id, job_type
        if self._sync_job_id is None:
            prefix = (self.session_id or "session").replace(":", "_")
            self._sync_job_id = f"sync_{prefix}_{uuid4().hex}"
            try:
                record_decomposition_job(
                    self.plan_session.plan_id,
                    job_id=self._sync_job_id,
                    job_type="chat_action",
                    mode=self.mode or "assistant",
                    target_task_id=None,
                    status="running",
                    params={
                        "session_id": self.session_id,
                        "mode": self.mode,
                    },
                    metadata={
                        "session_id": self.session_id,
                        "conversation_id": self.conversation_id,
                    },
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Failed to record sync job metadata: %s", exc)
        return self._sync_job_id, job_type

    def _log_action_event(
        self,
        action: LLMAction,
        *,
        status: str,
        success: Optional[bool],
        message: Optional[str],
        parameters: Optional[Dict[str, Any]],
        details: Optional[Dict[str, Any]],
    ) -> None:
        try:
            job_id, job_type = self._resolve_job_meta()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to resolve job metadata for logging: %s", exc)
            return
        try:
            append_action_log_entry(
                plan_id=self.plan_session.plan_id,
                job_id=job_id,
                job_type=job_type,
                session_id=self.session_id,
                user_message=self._current_user_message,
                action_kind=action.kind or "",
                action_name=action.name or "",
                status=status,
                success=success,
                message=message,
                parameters=parameters,
                details=details,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to persist action log entry: %s", exc)

    @staticmethod
    def _truncate_summary_text(
        value: Optional[str], *, limit: int = 160
    ) -> Optional[str]:
        if value is None:
            return None
        text = str(value)
        if len(text) > limit:
            return text[: limit - 3] + "..."
        return text

    def _build_actions_summary(self, steps: List[AgentStep]) -> List[Dict[str, Any]]:
        summary: List[Dict[str, Any]] = []
        for step in steps:
            action = step.action
            summary.append({
                "order": action.order,
                "kind": action.kind,
                "name": action.name,
                "success": step.success,
                "message": self._truncate_summary_text(step.message),
            })
        return summary

    def _append_summary_to_reply(
        self, reply: str, summary: List[Dict[str, Any]]
    ) -> str:
        if not summary:
            return reply
        lines = ["Action summary:"]
        for item in summary:
            status_icon = "⏳"
            if item["success"] is True:
                status_icon = "✅"
            elif item["success"] is False:
                status_icon = "⚠️"
            descriptor = (
                f"{item['kind']}/{item['name']}" if item["name"] else item["kind"]
            )
            line = f"{status_icon} Step {item['order']}: {descriptor}"
            if item.get("message"):
                line += f" - {item['message']}"
            lines.append(line)
        reply = reply.rstrip()
        return reply + "\n\n" + "\n".join(lines)

    def _format_history(self) -> str:
        if not self.history:
            return "<empty>"
        truncated = self.history[-self.MAX_HISTORY :]
        return "\n".join(
            f"{item.get('role', 'user')}: {item.get('content', '')}"
            for item in truncated
        )

    @staticmethod
    def _strip_code_fence(raw: str) -> str:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        return cleaned

    async def _execute_action(self, action: LLMAction) -> AgentStep:
        logger.info(
            "[CHAT][ACTION] session=%s plan=%s executing %s/%s params=%s",
            self.session_id,
            self.plan_session.plan_id,
            action.kind,
            action.name,
            action.parameters,
        )
        self._log_action_event(
            action,
            status="running",
            success=None,
            message="Action execution started.",
            parameters=action.parameters,
            details=None,
        )
        log_job_event(
            "info",
            "Preparing to execute the action.",
            {
                "kind": action.kind,
                "name": action.name,
                "order": action.order,
                "blocking": action.blocking,
                "parameters": action.parameters,
            },
        )
        handler = {
            "plan_operation": self._handle_plan_action,
            "task_operation": self._handle_task_action,
            "context_request": self._handle_context_request,
            "system_operation": self._handle_system_action,
            "tool_operation": self._handle_tool_action,
        }.get(action.kind, self._handle_unknown_action)
        try:
            result = handler(action)
            step = await result if inspect.isawaitable(result) else result
        except Exception as exc:
            log_job_event(
                "error",
                "An exception occurred while executing the action.",
                {
                    "kind": action.kind,
                    "name": action.name,
                    "error": str(exc),
                },
            )
            self._log_action_event(
                action,
                status="failed",
                success=False,
                message=str(exc),
                parameters=action.parameters,
                details={"error": str(exc), "exception": type(exc).__name__},
            )
            raise

        self._log_action_event(
            action,
            status="completed" if step.success else "failed",
            success=step.success,
            message=step.message,
            parameters=action.parameters,
            details=step.details,
        )
        log_job_event(
            "success" if step.success else "error",
            "Action execution completed.",
            {
                "kind": action.kind,
                "name": action.name,
                "success": step.success,
                "message": step.message,
                "details": step.details,
            },
        )
        logger.info(
            "[CHAT][ACTION] session=%s plan=%s finished %s/%s success=%s message=%s",
            self.session_id,
            self.plan_session.plan_id,
            action.kind,
            action.name,
            step.success,
            step.message,
        )
        return step

    async def _handle_tool_action(self, action: LLMAction) -> AgentStep:
        tool_name = (action.name or "").strip()
        if not tool_name:
            return AgentStep(
                action=action,
                success=False,
                message="Tool action is missing a name.",
                details={"error": "missing_tool_name"},
            )

        params = dict(action.parameters or {})

        if tool_name == "web_search":
            query = params.get("query")
            if not isinstance(query, str) or not query.strip():
                return AgentStep(
                    action=action,
                    success=False,
                    message="web_search requires a non-empty query.",
                    details={"error": "missing_query", "tool": tool_name},
                )

            provider_value = params.get("provider")
            normalized_provider = _normalize_search_provider(provider_value)
            if not normalized_provider:
                session_provider = _normalize_search_provider(
                    self.extra_context.get("default_search_provider")
                )
                if session_provider:
                    normalized_provider = session_provider
                else:
                    settings_provider = _normalize_search_provider(
                        get_search_settings().default_provider
                    )
                    normalized_provider = settings_provider or "builtin"
            params["provider"] = normalized_provider

        elif tool_name == "graph_rag":
            query = params.get("query")
            if not isinstance(query, str) or not query.strip():
                return AgentStep(
                    action=action,
                    success=False,
                    message="graph_rag requires a non-empty query.",
                    details={"error": "missing_query", "tool": tool_name},
                )

            rag_settings = get_graph_rag_settings()

            def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    parsed = default
                return max(minimum, min(parsed, maximum))

            default_top_k = min(12, rag_settings.max_top_k)
            default_hops = min(1, rag_settings.max_hops)

            top_k = _safe_int(
                params.get("top_k"),
                default=default_top_k,
                minimum=1,
                maximum=rag_settings.max_top_k,
            )
            hops = _safe_int(
                params.get("hops"),
                default=default_hops,
                minimum=0,
                maximum=rag_settings.max_hops,
            )
            return_subgraph = params.get("return_subgraph")
            if return_subgraph is None:
                return_subgraph = True
            else:
                return_subgraph = bool(return_subgraph)

            focus_raw = params.get("focus_entities")
            focus_entities: List[str] = []
            if isinstance(focus_raw, list):
                for item in focus_raw:
                    if isinstance(item, str) and item.strip():
                        focus_entities.append(item.strip())

            params = {
                "query": query.strip(),
                "top_k": top_k,
                "hops": hops,
                "return_subgraph": return_subgraph,
                "focus_entities": focus_entities,
            }

        elif tool_name == "claude_code":
            # Claude Code CLI - requires task parameter
            task_value = params.get("task")
            if not isinstance(task_value, str) or not task_value.strip():
                return AgentStep(
                    action=action,
                    success=False,
                    message="claude_code requires a non-empty `task` string.",
                    details={"error": "invalid_task", "tool": tool_name},
                )

            # 🔍 A-mem集成：查询历史执行经验
            original_task = task_value.strip()
            enhanced_task = original_task
            amem_experiences = []
            
            try:
                from ..services.amem_client import get_amem_client
                amem_client = get_amem_client()
                
                if amem_client.enabled:
                    # 查询相似的历史执行经验
                    amem_experiences = await amem_client.query_experiences(
                        query=original_task,
                        top_k=3
                    )
                    
                    if amem_experiences:
                        # 格式化经验供LLM参考
                        experience_context = amem_client.format_experiences_for_llm(amem_experiences)
                        enhanced_task = f"{original_task}\n\n{experience_context}"
                        logger.info(
                            f"[AMEM] Enhanced task with {len(amem_experiences)} historical experiences"
                        )
            except Exception as amem_err:
                logger.warning(f"[AMEM] Failed to query experiences: {amem_err}")
                # 继续执行，不影响主流程

            # Optional: allowed_tools parameter
            allowed_tools = params.get("allowed_tools")
            if allowed_tools and not isinstance(allowed_tools, str):
                allowed_tools = str(allowed_tools)

            # Optional: add_dirs parameter
            add_dirs_param = params.get("add_dirs")
            add_dirs: Optional[str] = None
            if add_dirs_param is not None:
                if isinstance(add_dirs_param, list):
                    add_dirs = ",".join(str(d) for d in add_dirs_param if d)
                elif isinstance(add_dirs_param, str):
                    add_dirs = add_dirs_param

            # Build final params (使用增强后的任务描述)
            params = {
                "task": enhanced_task,
            }
            if allowed_tools:
                params["allowed_tools"] = allowed_tools
            if add_dirs:
                params["add_dirs"] = add_dirs

        else:
            return AgentStep(
                action=action,
                success=False,
                message=f"Tool {tool_name} is not supported yet.",
                details={"error": "unsupported_tool", "tool": tool_name},
            )

        try:
            raw_result = await execute_tool(tool_name, **params)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception(
                "Tool %s execution failed for session %s: %s",
                tool_name,
                self.session_id,
                exc,
            )
            return AgentStep(
                action=action,
                success=False,
                message=f"{tool_name} failed: {exc}",
                details={"error": str(exc), "tool": tool_name},
            )

        sanitized = self._sanitize_tool_result(tool_name, raw_result)
        summary = self._summarize_tool_result(tool_name, sanitized)
        self._append_recent_tool_result(tool_name, summary, sanitized)

        success = sanitized.get("success", True)
        if success is False:
            message = summary or f"{tool_name} failed to execute."
        else:
            message = summary or f"{tool_name} finished execution."

        # 💾 A-mem集成：保存执行结果（异步，不阻塞主流程）
        if tool_name == "claude_code":
            try:
                from ..services.amem_client import get_amem_client
                amem_client = get_amem_client()
                
                if amem_client.enabled:
                    # 异步保存到A-mem
                    asyncio.create_task(
                        amem_client.save_execution(
                            task=original_task,  # 使用原始任务描述
                            result=sanitized,
                            session_id=self.session_id,
                            plan_id=self.plan_session.plan_id,
                            key_findings=summary  # 将总结作为关键发现
                        )
                    )
                    logger.info("[AMEM] Scheduled execution result save")
            except Exception as amem_err:
                logger.warning(f"[AMEM] Failed to schedule save: {amem_err}")
                # 不影响主流程

        return AgentStep(
            action=action,
            success=bool(success),
            message=message,
            details={
                "tool": tool_name,
                "parameters": params,
                "result": sanitized,
                "summary": summary,
            },
        )

    async def _handle_plan_action(self, action: LLMAction) -> AgentStep:
        params = action.parameters or {}
        if action.name == "create_plan":
            title = params.get("title")
            goal = params.get("goal")
            if not title:
                if isinstance(goal, str) and goal.strip():
                    title = goal.strip()[:80]
                else:
                    title = f"Plan-{self.conversation_id or 'new'}"
            description = params.get("description")
            owner = params.get("owner")
            metadata = params.get("metadata")
            if metadata is not None and not isinstance(metadata, dict):
                metadata = {}
            new_tree = self.plan_session.repo.create_plan(
                title=title,
                owner=owner,
                description=description,
                metadata=metadata,
            )
            self.plan_session.bind(new_tree.id)
            self.plan_tree = new_tree
            self.extra_context["plan_id"] = new_tree.id
            message = f'Created and bound new plan #{new_tree.id} "{new_tree.title}".'
            details = {
                "plan_id": new_tree.id,
                "title": new_tree.title,
                "task_count": new_tree.node_count(),
            }
            self._dirty = True

            decomposition_info = self._auto_decompose_plan(new_tree.id)
            if decomposition_info:
                job = decomposition_info.get("job")
                summary = decomposition_info.get("result")
                if job is not None:
                    job_payload = job.to_payload()
                    details["decomposition_job"] = job_payload
                    details["target_task_name"] = new_tree.title
                    message += " Automatic decomposition has been submitted for background execution."
                elif summary is not None:
                    created_count = len(summary.created_tasks)
                    details["decomposition"] = {
                        "created": [
                            node.model_dump() for node in summary.created_tasks
                        ],
                        "failed_nodes": summary.failed_nodes,
                        "stopped_reason": summary.stopped_reason,
                        "stats": summary.stats,
                    }
                    if created_count:
                        message += f" Automatic decomposition produced {created_count} tasks."
                    else:
                        message += " Automatic decomposition finished without creating new tasks."
            elif self._decomposition_notes:
                details["decomposition_notes"] = list(self._decomposition_notes)

            return AgentStep(
                action=action, success=True, message=message, details=details
            )

        if action.name == "list_plans":
            plans = self.plan_session.list_plans()
            details = {"plans": [plan.model_dump() for plan in plans]}
            message = "Available plans have been listed." if plans else "No plans are currently available."
            return AgentStep(
                action=action, success=True, message=message, details=details
            )

        if action.name == "execute_plan":
            tree = self._require_plan_bound()
            if self.plan_executor is None:
                raise ValueError("Plan executor is not enabled in this environment.")
            summary = await asyncio.to_thread(self.plan_executor.execute_plan, tree.id)
            executed_count = len(summary.executed_task_ids)
            failed_count = len(summary.failed_task_ids)
            skipped_count = len(summary.skipped_task_ids)
            parts = [f"Plan #{tree.id} finished execution"]
            parts.append(f"Succeeded tasks: {executed_count}")
            if failed_count:
                parts.append(f"Failed tasks: {failed_count}")
            if skipped_count:
                parts.append(f"Skipped tasks: {skipped_count}")
            message = "，".join(parts) + "。"
            details = summary.to_dict()
            self._refresh_plan_tree(force_reload=True)
            return AgentStep(
                action=action, success=True, message=message, details=details
            )

        if action.name == "delete_plan":
            plan_id_param = params.get("plan_id") or self.plan_session.plan_id
            plan_id = self._coerce_int(plan_id_param, "plan_id")
            self.plan_session.repo.delete_plan(plan_id)
            detached = False
            if self.plan_session.plan_id == plan_id:
                self.plan_session.detach()
                self.plan_tree = None
                self.extra_context.pop("plan_id", None)
                detached = True
            self._dirty = False
            message = f"Plan #{plan_id} has been deleted."
            details = {"plan_id": plan_id, "detached": detached}
            return AgentStep(
                action=action, success=True, message=message, details=details
            )

        return self._handle_unknown_action(action)

    def _handle_task_action(self, action: LLMAction) -> AgentStep:
        params = action.parameters or {}
        tree = self._require_plan_bound()

        if action.name == "create_task":
            name = params.get("task_name") or params.get("name") or params.get("title")
            if not name:
                raise ValueError("create_task requires a task_name.")
            instruction = params.get("instruction")
            parent_id = params.get("parent_id")
            if parent_id is not None:
                parent_id = self._coerce_int(parent_id, "parent_id")
            metadata = (
                params.get("metadata")
                if isinstance(params.get("metadata"), dict)
                else None
            )
            dependencies = self._normalize_dependencies(params.get("dependencies"))

            raw_anchor_task_id = params.get("anchor_task_id")
            anchor_task_id = None
            if raw_anchor_task_id is not None:
                anchor_task_id = self._coerce_int(raw_anchor_task_id, "anchor_task_id")

            anchor_position = params.get("anchor_position")
            if anchor_position is not None and not isinstance(anchor_position, str):
                raise ValueError("anchor_position must be a string.")
            if isinstance(anchor_position, str):
                anchor_position = anchor_position.strip()
                anchor_position = anchor_position.lower() if anchor_position else None

            position_param = params.get("position")
            position: Optional[int] = None
            if position_param is not None:
                if isinstance(position_param, str):
                    position_str = position_param.strip()
                    if position_str:
                        parts = position_str.split(":", 1)
                        keyword = parts[0].strip().lower()
                        if keyword in {"before", "after"}:
                            if len(parts) < 2 or not parts[1].strip():
                                raise ValueError("position must follow the format 'before:<task_id>' or 'after:<task_id>'.")
                            candidate_id = self._coerce_int(parts[1].strip(), f"position {keyword}")
                            if anchor_task_id is not None and anchor_task_id != candidate_id:
                                raise ValueError("anchor_task_id does not match the task referenced in position.")
                            if anchor_position is not None and anchor_position != keyword:
                                raise ValueError("anchor_position does not match the pattern specified in position.")
                            anchor_task_id = candidate_id
                            anchor_position = keyword
                        elif keyword in {"first_child", "last_child"}:
                            if anchor_position is not None and anchor_position != keyword:
                                raise ValueError("anchor_position does not match the pattern specified in position.")
                            anchor_position = keyword
                        else:
                            position = self._coerce_int(position_param, "position")
                    else:
                        position = None
                else:
                    position = self._coerce_int(position_param, "position")

            if position is not None and position < 0:
                raise ValueError("position cannot be negative.")

            insert_before_val = params.get("insert_before")
            insert_after_val = params.get("insert_after")
            insert_before_id = (
                self._coerce_int(insert_before_val, "insert_before")
                if insert_before_val is not None
                else None
            )
            insert_after_id = (
                self._coerce_int(insert_after_val, "insert_after")
                if insert_after_val is not None
                else None
            )

            siblings_parent_key = parent_id if parent_id is not None else None
            siblings = tree.children_ids(siblings_parent_key)

            if insert_before_id is not None and insert_after_id is not None:
                if insert_before_id == insert_after_id:
                    raise ValueError("insert_before and insert_after cannot point to the same task.")
                if insert_after_id not in siblings or insert_before_id not in siblings:
                    raise ValueError("insert_before / The task referenced by insert_after does not belong to the target parent node.")
                after_idx = siblings.index(insert_after_id)
                before_idx = siblings.index(insert_before_id)
                if after_idx > before_idx:
                    raise ValueError("insert_after must appear before insert_before.")
                if anchor_task_id is not None and anchor_task_id not in {
                    insert_after_id,
                    insert_before_id,
                }:
                    raise ValueError("anchor_task_id is inconsistent with insert_before/insert_after.")
                anchor_task_id = insert_after_id
                anchor_position = "after"
            else:
                if insert_before_id is not None:
                    if anchor_task_id is not None and anchor_task_id != insert_before_id:
                        raise ValueError("anchor_task_id points to a different task than insert_before.")
                    if insert_before_id not in siblings:
                        raise ValueError("The task referenced by insert_before does not belong to the target parent node.")
                    anchor_task_id = insert_before_id
                    anchor_position = "before"
                if insert_after_id is not None:
                    if anchor_task_id is not None and anchor_task_id != insert_after_id:
                        raise ValueError("anchor_task_id points to a different task than insert_after.")
                    if insert_after_id not in siblings:
                        raise ValueError("The task referenced by insert_after does not belong to the target parent node.")
                    anchor_task_id = insert_after_id
                    anchor_position = "after"
            if anchor_position is not None:
                valid_anchor_positions = {
                    "before",
                    "after",
                    "first_child",
                    "last_child",
                }
                if anchor_position not in valid_anchor_positions:
                    raise ValueError(
                        f"Invalid anchor_position; only {', '.join(sorted(valid_anchor_positions))} are supported."
                    )
            node = self.plan_session.repo.create_task(
                tree.id,
                name=name,
                instruction=instruction,
                parent_id=parent_id,
                metadata=metadata,
                dependencies=dependencies,
                position=position,
                anchor_task_id=anchor_task_id,
                anchor_position=anchor_position,
            )
            self._refresh_plan_tree()
            message = f"Created task [{node.id}] {node.name}."
            details = {"task": node.model_dump()}
            self._dirty = True
            return AgentStep(
                action=action, success=True, message=message, details=details
            )

        if action.name == "update_task":
            task_id = self._coerce_int(params.get("task_id"), "task_id")
            name = params.get("name")
            instruction = params.get("instruction")
            metadata = (
                params.get("metadata")
                if isinstance(params.get("metadata"), dict)
                else None
            )
            dependencies = self._normalize_dependencies(params.get("dependencies"))
            node = self.plan_session.repo.update_task(
                tree.id,
                task_id,
                name=name,
                instruction=instruction,
                metadata=metadata,
                dependencies=dependencies,
            )
            self._refresh_plan_tree()
            message = f"Task [{node.id}] information has been updated."
            details = {"task": node.model_dump()}
            self._dirty = True
            return AgentStep(
                action=action, success=True, message=message, details=details
            )

        if action.name == "update_task_instruction":
            task_id = self._coerce_int(params.get("task_id"), "task_id")
            instruction = params.get("instruction")
            if not instruction:
                raise ValueError("update_task_instruction requires an instruction.")
            node = self.plan_session.repo.update_task(
                tree.id,
                task_id,
                instruction=instruction,
            )
            self._refresh_plan_tree()
            message = f"Task [{node.id}] instructions have been updated."
            details = {"task": node.model_dump()}
            self._dirty = True
            return AgentStep(
                action=action, success=True, message=message, details=details
            )

        if action.name == "move_task":
            task_id = self._coerce_int(params.get("task_id"), "task_id")
            new_parent_id = params.get("new_parent_id")
            if new_parent_id is not None:
                new_parent_id = self._coerce_int(new_parent_id, "new_parent_id")
            new_position = params.get("new_position")
            if new_position is not None:
                new_position = self._coerce_int(new_position, "new_position")
            node = self.plan_session.repo.move_task(
                tree.id,
                task_id,
                new_parent_id=new_parent_id,
                new_position=new_position,
            )
            self._refresh_plan_tree()
            message = f"Task [{node.id}] has been moved to a new position."
            details = {"task": node.model_dump()}
            self._dirty = True
            return AgentStep(
                action=action, success=True, message=message, details=details
            )

        if action.name == "delete_task":
            task_id = self._coerce_int(params.get("task_id"), "task_id")
            self.plan_session.repo.delete_task(tree.id, task_id)
            self._refresh_plan_tree()
            message = f"Task [{task_id}] and its subtasks have been deleted."
            details = {"task_id": task_id}
            self._dirty = True
            return AgentStep(
                action=action, success=True, message=message, details=details
            )

        if action.name == "show_tasks":
            self._refresh_plan_tree(force_reload=False)
            outline = self.plan_session.outline(max_depth=6, max_nodes=120)
            message = f"Here is the task overview for plan #{tree.id}."
            details = {"plan_id": tree.id, "outline": outline}
            return AgentStep(
                action=action, success=True, message=message, details=details
            )

        if action.name == "query_status":
            self._refresh_plan_tree(force_reload=False)
            node_count = self.plan_tree.node_count() if self.plan_tree else 0
            root_count = len(self.plan_tree.root_node_ids()) if self.plan_tree else 0
            message = f"Plan #{tree.id} currently has {node_count} task nodes ({root_count} roots)."
            details = {
                "plan_id": tree.id,
                "task_count": node_count,
                "root_tasks": root_count,
            }
            return AgentStep(
                action=action, success=True, message=message, details=details
            )

        if action.name == "rerun_task":
            task_id_raw = params.get("task_id")
            task_id = self._coerce_int(task_id_raw, "task_id")
            if self.plan_executor is None:
                raise ValueError("Plan executor is not enabled in this environment.")
            result = self.plan_executor.execute_task(tree.id, task_id)
            message = f"Task [{task_id}] execution status: {result.status}."
            details = result.to_dict()
            self._refresh_plan_tree(force_reload=True)
            return AgentStep(
                action=action, success=True, message=message, details=details
            )

        if action.name == "decompose_task":
            if self.plan_decomposer is None:
                raise ValueError("Task decomposition service is not enabled in this environment.")
            if self.decomposer_settings.model is None:
                raise ValueError("No decomposition model configured; cannot proceed.")

            expand_depth_raw = params.get("expand_depth")
            node_budget_raw = params.get("node_budget")
            allow_existing_raw = params.get("allow_existing_children")

            expand_depth = (
                self._coerce_int(expand_depth_raw, "expand_depth")
                if expand_depth_raw is not None
                else None
            )
            node_budget = (
                self._coerce_int(node_budget_raw, "node_budget")
                if node_budget_raw is not None
                else None
            )
            allow_existing_children = None
            if allow_existing_raw is not None:
                if isinstance(allow_existing_raw, bool):
                    allow_existing_children = allow_existing_raw
                else:
                    allow_existing_children = str(
                        allow_existing_raw
                    ).strip().lower() in {
                        "1",
                        "true",
                        "yes",
                        "y",
                    }

            task_id_raw = params.get("task_id")
            if task_id_raw is None:
                result = self.plan_decomposer.run_plan(
                    tree.id,
                    max_depth=expand_depth,
                    node_budget=node_budget,
                )
            else:
                task_id = self._coerce_int(task_id_raw, "task_id")
                result = self.plan_decomposer.decompose_node(
                    tree.id,
                    task_id,
                    expand_depth=expand_depth,
                    node_budget=node_budget,
                    allow_existing_children=allow_existing_children,
                )

            self._last_decomposition = result
            if result.created_tasks:
                self._dirty = True
            try:
                self._refresh_plan_tree(force_reload=True)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Failed to refresh plan tree after decomposition: %s", exc
                )
                self._decomposition_errors.append(f"Failed to refresh plan after decomposition: {exc}")

            created_count = len(result.created_tasks)
            message = (
                f"Generated {created_count} subtasks."
                if created_count
                else "No new subtasks were generated."
            )
            if result.stopped_reason:
                message += f" Stop reason: {result.stopped_reason}."
            details = {
                "plan_id": tree.id,
                "mode": result.mode,
                "processed_nodes": result.processed_nodes,
                "created": [node.model_dump() for node in result.created_tasks],
                "failed_nodes": result.failed_nodes,
                "stopped_reason": result.stopped_reason,
                "stats": result.stats,
            }
            return AgentStep(
                action=action, success=True, message=message, details=details
            )

        return self._handle_unknown_action(action)

    def _handle_context_request(self, action: LLMAction) -> AgentStep:
        if action.name != "request_subgraph":
            return self._handle_unknown_action(action)
        params = action.parameters or {}
        tree = self._require_plan_bound()
        node_id_value = params.get("logical_id") or params.get("task_id")
        node_id = self._coerce_int(node_id_value, "task_id")
        max_depth_raw = params.get("max_depth")
        max_depth = (
            self._coerce_int(max_depth_raw, "max_depth")
            if max_depth_raw is not None
            else 2
        )
        self._refresh_plan_tree(force_reload=False)
        graph_tree = self.plan_tree or self.plan_session.ensure()
        try:
            nodes = graph_tree.subgraph_nodes(node_id, max_depth=max_depth)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        outline = graph_tree.subgraph_outline(node_id, max_depth=max_depth)
        details = {
            "plan_id": tree.id,
            "root_node": node_id,
            "max_depth": max_depth,
            "outline": outline,
            "nodes": [node.model_dump() for node in nodes],
        }
        message = f"Returned a subgraph preview for node {node_id}."
        return AgentStep(action=action, success=True, message=message, details=details)

    def _handle_system_action(self, action: LLMAction) -> AgentStep:
        if action.name == "help":
            message = (
                "System help: you can create/list/delete plans or perform CRUD and restructuring actions on the current plan. "
                "For subgraph queries and similar operations, bind a plan first by calling create_plan or list_plans."
            )
            return AgentStep(action=action, success=True, message=message, details={})
        return self._handle_unknown_action(action)

    def _handle_unknown_action(self, action: LLMAction) -> AgentStep:
        message = f"Unrecognized action kind or name: {action.kind}/{action.name}."
        return AgentStep(action=action, success=False, message=message, details={})

    def _build_suggestions(
        self, structured: LLMStructuredResponse, steps: List[AgentStep]
    ) -> List[str]:
        base_suggestions: List[str] = []
        failures = [step for step in steps if not step.success]
        if failures:
            base_suggestions.append(
                "Some actions failed; provide more specific parameters or try again later."
            )
        if not structured.actions:
            base_suggestions.append("Continue describing the tasks or plans you want to handle.")
            if self.plan_session.plan_id is None:
                base_suggestions.append("I can create new plans or list existing ones.")
        else:
            base_suggestions.append("If you need to execute those actions, supply the required details and confirm.")
        if structured.actions and structured.actions[0].kind == "context_request":
            base_suggestions.append("After reviewing the returned subgraph, you may provide the next instruction.")
        return base_suggestions

    def _require_plan_bound(self) -> PlanTree:
        if self.plan_session.plan_id is None:
            raise ValueError("The session is not bound to any plan, so tasks or context actions cannot be executed.")
        try:
            return self.plan_session.ensure()
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    def _refresh_plan_tree(self, force_reload: bool = True) -> None:
        if self.plan_session.plan_id is None:
            self.plan_tree = None
            return
        if force_reload:
            try:
                self.plan_tree = self.plan_session.refresh()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to refresh plan tree: %s", exc)
                self.plan_tree = None
        else:
            self.plan_tree = self.plan_session.current_tree()

    @staticmethod
    def _coerce_int(value: Any, field: str) -> int:
        if value is None:
            raise ValueError(f"{field} is missing or empty.")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError(f"{field} must be an integer; received {value!r}") from exc

    def _auto_decompose_plan(self, plan_id: int) -> Optional[Dict[str, Any]]:
        settings = self.decomposer_settings
        if not settings.auto_on_create:
            note = "Automatic decomposition is disabled."
            if note not in self._decomposition_notes:
                self._decomposition_notes.append(note)
            return None
        if self.plan_decomposer is None:
            note = "The automatic decomposer is not initialised."
            if note not in self._decomposition_notes:
                self._decomposition_notes.append(note)
            return None
        if settings.model is None:
            note = "Automatic decomposition was skipped: no decomposition model configured."
            if note not in self._decomposition_notes:
                self._decomposition_notes.append(note)
            return None
        try:
            job = start_decomposition_job_thread(
                self.plan_decomposer,
                plan_id=plan_id,
                mode="plan_bfs",
                max_depth=settings.max_depth,
                node_budget=settings.total_node_budget,
            )
        except Exception as exc:  # pragma: no cover - defensive
            message = f"Failed to submit automatic task decomposition: {exc}"
            logger.exception(
                "Auto decomposition enqueue failed for plan %s: %s", plan_id, exc
            )
            self._decomposition_errors.append(message)
            return None

        self._last_decomposition = None
        note = "Automatic decomposition has been submitted for background execution."
        if note not in self._decomposition_notes:
            self._decomposition_notes.append(note)
        return {"job": job}

    def _persist_if_dirty(self) -> bool:
        if not self._dirty or self.plan_session.plan_id is None:
            return False
        note = f"session:{self.session_id}" if self.session_id else None
        self._refresh_plan_tree(force_reload=True)
        self.plan_session.persist_current_tree(note=note)
        self._dirty = False
        return True

    @staticmethod
    def _normalize_dependencies(raw: Any) -> Optional[List[int]]:
        if raw is None:
            return None
        if not isinstance(raw, list):
            return None
        deps: List[int] = []
        for item in raw:
            try:
                deps.append(int(item))
            except (TypeError, ValueError):
                continue
        return deps or None

    def _sanitize_tool_result(self, tool_name: str, raw_result: Any) -> Dict[str, Any]:
        if tool_name == "claude_code" and isinstance(raw_result, dict):
            def _trim(text: str, limit: int = 800) -> str:
                text = text.strip()
                if len(text) > limit:
                    return text[: limit - 3] + "..."
                return text

            sanitized: Dict[str, Any] = {
                "tool": tool_name,
                "code": raw_result.get("code"),
                "owner": raw_result.get("owner"),
                "language": raw_result.get("language", "python"),
                "uploaded_files": raw_result.get("uploaded_files") or [],
                "success": raw_result.get("success", False),
            }

            stdout_value = raw_result.get("stdout")
            if isinstance(stdout_value, str) and stdout_value.strip():
                sanitized["stdout"] = _trim(stdout_value)

            stderr_value = raw_result.get("stderr")
            if isinstance(stderr_value, str) and stderr_value.strip():
                sanitized["stderr"] = _trim(stderr_value, limit=400)

            output_value = raw_result.get("output")
            if isinstance(output_value, str) and output_value.strip():
                sanitized["output"] = _trim(output_value)

            if "error" in raw_result:
                sanitized["error"] = str(raw_result["error"])

            tool_calls = raw_result.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                sanitized["tool_calls"] = tool_calls

            return sanitized

        if isinstance(raw_result, dict):
            sanitized: Dict[str, Any] = {"tool": tool_name}
            for key in (
                "query",
                "provider",
                "success",
                "response",
                "answer",
                "total_results",
                "fallback_from",
                "code",
                "cache_hit",
            ):
                if key in raw_result:
                    sanitized[key] = raw_result[key]
            if "error" in raw_result:
                sanitized["error"] = raw_result["error"]
            results = raw_result.get("results")
            if isinstance(results, list):
                trimmed: List[Dict[str, Any]] = []
                for item in results[:3]:
                    if isinstance(item, dict):
                        trimmed.append({
                            "title": item.get("title"),
                            "url": item.get("url"),
                            "snippet": item.get("snippet"),
                            "source": item.get("source"),
                        })
                if trimmed:
                    sanitized["results"] = trimmed
            result_block = raw_result.get("result")
            if isinstance(result_block, dict):
                if "prompt" in result_block and isinstance(result_block["prompt"], str):
                    sanitized["prompt"] = result_block["prompt"]
                triples = result_block.get("triples")
                if isinstance(triples, list):
                    sanitized["triples"] = triples
                if "metadata" in result_block and isinstance(
                    result_block["metadata"], dict
                ):
                    sanitized["metadata"] = result_block["metadata"]
                if "subgraph" in result_block:
                    sanitized["subgraph"] = result_block["subgraph"]
                if "query" in result_block and "query" not in sanitized:
                    sanitized["query"] = result_block["query"]
            if "success" not in sanitized:
                if "error" in sanitized:
                    sanitized["success"] = False
                else:
                    sanitized["success"] = True
            if tool_name == "graph_rag":
                if not sanitized.get("success"):
                    sanitized["empty_result"] = False
                else:
                    triples = sanitized.get("triples")
                    sanitized["empty_result"] = not bool(triples)
            return sanitized

        if raw_result is None:
            return {"tool": tool_name, "success": False, "error": "empty_result"}

        if isinstance(raw_result, (list, tuple)):
            preview = list(raw_result[:3])
            return {"tool": tool_name, "items": preview, "success": True}

        text = str(raw_result)
        return {"tool": tool_name, "text": text, "success": True}

    @staticmethod
    def _summarize_tool_result(tool_name: str, result: Dict[str, Any]) -> str:
        if tool_name == "web_search":
            query = result.get("query") or ""
            prefix = f"Web search“{query}”" if query else "Web search"
            provider = result.get("provider")
            if isinstance(provider, str) and provider:
                provider_map = {
                    "builtin": "builtin",
                    "perplexity": "Perplexity",
                }
                label = provider_map.get(provider, provider)
                prefix = f"{prefix}（{label}）"
            if result.get("success") is False:
                error = result.get("error") or "Execution failed"
                return f"{prefix} failed: {error}"
            results = result.get("results") or []
            if isinstance(results, list) and results:
                first = results[0]
                source = first.get("source") or first.get("url") or "Unknown source"
                title = first.get("title") or ""
                if title:
                    return f'{prefix} finished; the first result came from {source}: "{title}".'
                return f"{prefix} finished; the first result came from {source}."
            response = result.get("response") or result.get("answer")
            if isinstance(response, str) and response.strip():
                snippet = response.strip()
                if len(snippet) > 120:
                    snippet = snippet[:117] + "..."
                return f"{prefix} finished. Summary: {snippet}"
            total = result.get("total_results")
            if isinstance(total, int) and total > 0:
                return f"{prefix} finished with {total} results."
            return f"{prefix} finished."
        if tool_name == "graph_rag":
            query = result.get("query") or ""
            prefix = f"Knowledge-graph search“{query}”" if query else "Knowledge-graph search"
            if result.get("success") is False:
                error = result.get("error") or "Execution failed"
                return f"{prefix} failed: {error}"
            triples = result.get("triples") or []
            count = len(triples) if isinstance(triples, list) else 0
            if count:
                return f"{prefix} finished, returning {count} triples."
            if result.get("empty_result"):
                return f"{prefix} finished, but no relevant results were found."
            prompt = result.get("prompt")
            if isinstance(prompt, str) and prompt.strip():
                snippet = prompt.strip()
                if len(snippet) > 120:
                    snippet = snippet[:117] + "..."
                return f"{prefix} finished. Prompt summary: {snippet}"
            return f"{prefix} finished."
        if tool_name == "claude_code":
            if result.get("success") is False:
                error = result.get("error") or "Code execution failed"
                return f"Claude Code execution failed: {error}"
            
            uploaded = result.get("uploaded_files") or []
            file_info = f" (with {len(uploaded)} file(s))" if uploaded else ""
            
            stdout_text = result.get("stdout") or result.get("output") or ""
            if stdout_text.strip():
                snippet = stdout_text.strip()
                if len(snippet) > 150:
                    snippet = snippet[:147] + "..."
                return f"Claude Code execution{file_info} succeeded. Output: {snippet}"
            
            return f"Claude Code execution{file_info} succeeded."
        
        return f"{tool_name} finished execution."

    def _append_recent_tool_result(
        self, tool_name: str, summary: str, sanitized: Dict[str, Any]
    ) -> None:
        history = self.extra_context.setdefault("recent_tool_results", [])
        if not isinstance(history, list):
            history = []
            self.extra_context["recent_tool_results"] = history
        entry = {
            "tool": tool_name,
            "summary": summary,
            "result": sanitized,
        }
        history.append(entry)
        max_items = 5
        if len(history) > max_items:
            del history[:-max_items]
