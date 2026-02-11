"""Chat APIs that orchestrate structured LLM responses and action dispatch."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Literal, Optional, Sequence, Tuple, Union
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_graph_rag_settings, get_search_settings
from app.config.tool_policy import get_tool_policy, is_tool_allowed
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
from app.llm import LLMClient
from app.services.llm.llm_service import LLMService, get_llm_service
from app.services.llm.structured_response import (
    LLMAction,
    LLMStructuredResponse,
    schema_as_json,
)
from app.services.llm.decomposer_service import PlanDecomposerLLMService
from app.services.plans.decomposition_jobs import (
    get_current_job,
    log_job_event,
    plan_decomposition_jobs,
    reset_current_job,
    set_current_job,
    start_decomposition_job_thread,
    start_phagescope_track_job_thread,
)
from app.services.plans.plan_decomposer import DecompositionResult, PlanDecomposer
from app.services.plans.plan_executor import PlanExecutor, PlanExecutorLLMService, ExecutionConfig
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.plan_session import PlanSession
from app.services.session_title_service import (
    SessionNotFoundError,
    SessionTitleService,
)
from app.services.deliverables import get_deliverable_publisher
from app.services.upload_storage import delete_session_storage
from app.services.tool_output_storage import store_tool_output
from app.prompts import prompt_manager
from tool_box import execute_tool
from app.services.deep_think_agent import DeepThinkAgent, ThinkingStep, DeepThinkResult

from . import register_router

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])
plan_repository = PlanRepository()
decomposer_settings = get_decomposer_settings()

# 需要用户确认才能执行的危险操作（仅删除类操作需要确认）
ACTIONS_REQUIRING_CONFIRMATION = {
    ("plan_operation", "delete_plan"),      # 删除计划
    ("task_operation", "delete_task"),      # 删除任务
    ("task_operation", "clear_tasks"),      # 清空任务
}

# 待确认操作存储: {confirmation_id: {session_id, actions, structured, created_at, ...}}
_pending_confirmations: Dict[str, Dict[str, Any]] = {}

def _generate_confirmation_id() -> str:
    """生成确认ID"""
    return f"confirm_{uuid4().hex[:12]}"

def _requires_confirmation(actions: List[Any]) -> bool:
    """检查操作列表是否包含需要确认的操作"""
    for action in actions:
        key = (getattr(action, 'kind', None), getattr(action, 'name', None))
        if key in ACTIONS_REQUIRING_CONFIRMATION:
            return True
    return False

def _store_pending_confirmation(
    confirmation_id: str,
    session_id: str,
    actions: List[Any],
    structured: Any,
    plan_id: Optional[int] = None,
    extra_context: Optional[Dict[str, Any]] = None,
) -> None:
    """存储待确认的操作"""
    _pending_confirmations[confirmation_id] = {
        "session_id": session_id,
        "actions": actions,
        "structured": structured,
        "plan_id": plan_id,
        "extra_context": extra_context or {},
        "created_at": datetime.now().isoformat(),
    }
    logger.info(f"[CONFIRMATION] Stored pending confirmation: {confirmation_id} for session {session_id}")

def _get_pending_confirmation(confirmation_id: str) -> Optional[Dict[str, Any]]:
    """获取待确认的操作"""
    return _pending_confirmations.get(confirmation_id)

def _remove_pending_confirmation(confirmation_id: str) -> Optional[Dict[str, Any]]:
    """移除并返回待确认的操作"""
    return _pending_confirmations.pop(confirmation_id, None)

def _cleanup_old_confirmations(max_age_seconds: int = 600) -> None:
    """清理过期的待确认操作（默认10分钟）"""
    now = datetime.now()
    expired = []
    for cid, data in _pending_confirmations.items():
        created = datetime.fromisoformat(data["created_at"])
        if (now - created).total_seconds() > max_age_seconds:
            expired.append(cid)
    for cid in expired:
        del _pending_confirmations[cid]
        logger.info(f"[CONFIRMATION] Cleaned up expired confirmation: {cid}")


# ---------------------------------------------------------------------------
# Background task classification
# ---------------------------------------------------------------------------

# Action names that correspond to long-running background tasks.
_BACKGROUND_TOOL_NAMES: Dict[str, str] = {
    "phagescope": "phagescope",
    "claude_code": "claude_code",
}

_BACKGROUND_PLAN_OPS = {"create_plan", "optimize_plan"}


def _classify_background_category(
    actions: List[Any],
    job_snapshot: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Classify an action run as a long-running background category.

    Returns ``"phagescope"``, ``"claude_code"``, or ``"task_creation"``
    when the actions indicate a long-running background task, otherwise
    ``None``.
    """
    # 1. Check job_type from snapshot (most reliable if available)
    job_type = str((job_snapshot or {}).get("job_type") or "").strip().lower()
    if job_type == "phagescope_track":
        return "phagescope"
    if job_type == "plan_decompose":
        return "task_creation"

    # 2. Scan individual actions
    for action in actions:
        kind = getattr(action, "kind", None) or ""
        name = str(getattr(action, "name", None) or "").strip().lower()
        if kind == "tool_operation" and name in _BACKGROUND_TOOL_NAMES:
            return _BACKGROUND_TOOL_NAMES[name]
        if kind == "plan_operation" and name in _BACKGROUND_PLAN_OPS:
            return "task_creation"

    return None

VALID_SEARCH_PROVIDERS = {"builtin", "perplexity", "tavily"}
VALID_BASE_MODELS = {"qwen3-max-2026-01-23", "qwen-turbo"}
VALID_LLM_PROVIDERS = {"qwen"}
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


def _sse_message(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class StructuredReplyStreamParser:
    def __init__(self) -> None:
        self._state = "search_llm_reply"
        self._scan_buffer = ""
        self._seen_colon = False
        self._escape = False
        self._unicode_escape: Optional[str] = None
        self._emit_buffer: List[str] = []
        self._full_parts: List[str] = []
        self._done = False

    def feed(self, text: str) -> List[str]:
        self._full_parts.append(text)
        deltas: List[str] = []
        for ch in text:
            if self._done:
                continue
            if self._state == "search_llm_reply":
                self._scan_buffer = (self._scan_buffer + ch)[-64:]
                if '"llm_reply"' in self._scan_buffer:
                    self._state = "search_message_key"
                    self._scan_buffer = ""
                continue
            if self._state == "search_message_key":
                self._scan_buffer = (self._scan_buffer + ch)[-64:]
                if '"message"' in self._scan_buffer:
                    self._state = "search_message_value"
                    self._scan_buffer = ""
                    self._seen_colon = False
                continue
            if self._state == "search_message_value":
                if ch == ":":
                    self._seen_colon = True
                elif self._seen_colon and ch == '"':
                    self._state = "in_message"
                    self._escape = False
                    self._unicode_escape = None
                continue

            if self._state == "in_message":
                self._consume_message_char(ch)
                if self._emit_buffer and len(self._emit_buffer) >= 16:
                    deltas.append("".join(self._emit_buffer))
                    self._emit_buffer = []
        if self._emit_buffer:
            deltas.append("".join(self._emit_buffer))
            self._emit_buffer = []
        return deltas

    def full_text(self) -> str:
        return "".join(self._full_parts)

    def _emit(self, value: str) -> None:
        if value:
            self._emit_buffer.append(value)

    def _consume_message_char(self, ch: str) -> None:
        if self._unicode_escape is not None:
            if ch.lower() in "0123456789abcdef":
                self._unicode_escape += ch
                if len(self._unicode_escape) == 4:
                    try:
                        self._emit(chr(int(self._unicode_escape, 16)))
                    except ValueError:
                        self._emit("\\u" + self._unicode_escape)
                    self._unicode_escape = None
                    self._escape = False
            else:
                self._emit("\\u" + self._unicode_escape + ch)
                self._unicode_escape = None
                self._escape = False
            return

        if self._escape:
            mapping = {
                '"': '"',
                "\\": "\\",
                "/": "/",
                "b": "\b",
                "f": "\f",
                "n": "\n",
                "r": "\r",
                "t": "\t",
            }
            if ch == "u":
                self._unicode_escape = ""
            else:
                self._emit(mapping.get(ch, ch))
                self._escape = False
            return

        if ch == "\\":
            self._escape = True
            return
        if ch == '"':
            self._done = True
            return
        self._emit(ch)


class ChatMessage(BaseModel):
    """Structure of an individual chat message."""

    id: Optional[int] = None
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

    default_search_provider: Optional[Literal["builtin", "perplexity", "tavily"]] = None
    default_base_model: Optional[
        Literal["qwen3-max-2026-01-23", "qwen-turbo"]
    ] = None
    default_llm_provider: Optional[
        Literal["qwen"]
    ] = None


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
                if "default_search_provider" in settings_update:
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
                if "default_base_model" in settings_update:
                    base_model = settings_update.get("default_base_model")
                    if base_model is not None:
                        normalized = _normalize_base_model(base_model)
                        if normalized is None:
                            raise HTTPException(
                                status_code=422,
                                detail="Invalid default_base_model value",
                            )
                        metadata_dict["default_base_model"] = normalized
                    else:
                        metadata_dict.pop("default_base_model", None)
                if "default_llm_provider" in settings_update:
                    llm_provider = settings_update.get("default_llm_provider")
                    if llm_provider is not None:
                        normalized = _normalize_llm_provider(llm_provider)
                        if normalized is None:
                            raise HTTPException(
                                status_code=422,
                                detail="Invalid default_llm_provider value",
                            )
                        metadata_dict["default_llm_provider"] = normalized
                    else:
                        metadata_dict.pop("default_llm_provider", None)
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


@router.head("/sessions/{session_id}")
async def head_chat_session(session_id: str) -> Response:
    """Check if a chat session exists (returns only headers, no body)."""
    from ..database import get_db  # lazy import

    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id FROM chat_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Session not found")
            return Response(status_code=200)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to check chat session %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail="Failed to check session") from exc


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
                try:
                    if delete_session_storage(session_id):
                        logger.info("Deleted session uploads for %s", session_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to delete session uploads for %s: %s",
                        session_id,
                        exc,
                    )
        return Response(status_code=204)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to delete chat session %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete session") from exc


# ============================================================================
# 操作确认 API
# ============================================================================

class ConfirmActionRequest(BaseModel):
    """确认操作请求"""
    confirmation_id: str
    confirmed: bool = True  # True=确认执行, False=取消


class ConfirmActionResponse(BaseModel):
    """确认操作响应"""
    success: bool
    message: str
    confirmation_id: str
    executed: bool = False
    result: Optional[Dict[str, Any]] = None


@router.post("/confirm", response_model=ConfirmActionResponse)
async def confirm_pending_action(
    request: ConfirmActionRequest,
    background_tasks: BackgroundTasks,
) -> ConfirmActionResponse:
    """
    确认或取消待执行的操作。

    当 LLM 生成的操作需要用户确认时（如 create_plan），
    系统会暂存操作并返回 confirmation_id，用户需调用此接口确认或取消。
    """
    _cleanup_old_confirmations()  # 清理过期确认

    pending = _remove_pending_confirmation(request.confirmation_id)
    if not pending:
        raise HTTPException(
            status_code=404,
            detail=f"Confirmation {request.confirmation_id} not found or expired"
        )

    if not request.confirmed:
        logger.info(f"[CONFIRMATION] User cancelled: {request.confirmation_id}")
        return ConfirmActionResponse(
            success=True,
            message="操作已取消",
            confirmation_id=request.confirmation_id,
            executed=False,
        )

    # 用户确认，执行操作
    logger.info(f"[CONFIRMATION] User confirmed: {request.confirmation_id}")

    try:
        session_id = pending["session_id"]
        actions = pending["actions"]
        plan_id = pending.get("plan_id")

        # 创建后台执行任务
        tracking_id = f"act_{uuid4().hex[:32]}"

        # 调度后台执行
        background_tasks.add_task(
            _execute_confirmed_actions,
            tracking_id=tracking_id,
            session_id=session_id,
            actions=actions,
            plan_id=plan_id,
            extra_context=pending.get("extra_context", {}),
        )

        return ConfirmActionResponse(
            success=True,
            message="操作已确认，正在执行...",
            confirmation_id=request.confirmation_id,
            executed=True,
            result={"tracking_id": tracking_id},
        )
    except Exception as e:
        logger.error(f"[CONFIRMATION] Execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/confirm/{confirmation_id}")
async def get_pending_confirmation_status(confirmation_id: str) -> Dict[str, Any]:
    """获取待确认操作的状态"""
    pending = _get_pending_confirmation(confirmation_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Confirmation not found or expired")

    return {
        "confirmation_id": confirmation_id,
        "session_id": pending["session_id"],
        "plan_id": pending.get("plan_id"),
        "created_at": pending["created_at"],
        "actions": [
            {"kind": getattr(a, 'kind', None), "name": getattr(a, 'name', None)}
            for a in pending.get("actions", [])
        ],
    }


async def _execute_confirmed_actions(
    tracking_id: str,
    session_id: str,
    actions: List[Any],
    plan_id: Optional[int],
    extra_context: Dict[str, Any],
) -> None:
    """后台执行已确认的操作"""
    logger.info(f"[CONFIRMATION] Executing confirmed actions: {tracking_id}")

    try:
        # 获取或创建 agent
        plan_session = PlanSessionManager(repo=plan_repository)
        if plan_id:
            plan_session.bind(plan_id)

        agent = PlanningAgent(
            llm_service=get_llm_service(),
            plan_session=plan_session,
            history=[],
            extra_context=extra_context,
        )

        # 执行每个 action
        for action in actions:
            try:
                step = await agent._dispatch_action(action)
                logger.info(
                    f"[CONFIRMATION] Action {action.name} completed: success={step.success}"
                )
            except Exception as e:
                logger.error(f"[CONFIRMATION] Action {action.name} failed: {e}")

        logger.info(f"[CONFIRMATION] All actions completed: {tracking_id}")

    except Exception as e:
        logger.error(f"[CONFIRMATION] Execution error: {e}")


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
async def get_chat_history(
    session_id: str,
    limit: int = 50,
    before_id: Optional[int] = Query(default=None, ge=1),
):
    """Fetch history for a specific session."""
    try:
        messages, has_more = _load_chat_history(session_id, limit, before_id)
        next_before_id = messages[0].id if messages else None
        return {
            "success": True,
            "session_id": session_id,
            "messages": [
                {
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "metadata": msg.metadata,
                }
                for msg in messages
            ],
            "total": len(messages),
            "has_more": has_more,
            "next_before_id": next_before_id,
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

        # 处理附件：如果context中有attachments，自动附加到消息中
        # 并尝试自动读取文本类附件内容
        message_to_send = request.message
        attachments = context.get("attachments", [])
        if attachments and isinstance(attachments, list):
            attachment_info = "\n\n📎 用户当前上传的附件：\n"
            # 定义可自动读取的文件类型
            AUTO_READ_TEXT_EXTS = {".txt", ".md", ".log", ".ini", ".cfg", ".yaml", ".yml"}
            AUTO_READ_PDF_EXT = ".pdf"
            MAX_AUTO_READ_SIZE = 200 * 1024  # 200KB
            
            for att in attachments:
                if isinstance(att, dict):
                    att_type = att.get("type", "file")
                    att_name = att.get("name", "未知文件")
                    att_path = att.get("path", "")
                    att_extracted = att.get("extracted_path")
                    attachment_info += f"- {att_name} ({att_type}): {att_path}\n"
                    if att_extracted:
                        attachment_info += f"  extracted: {att_extracted}\n"
                    
                    # 自动读取文本类文件内容
                    if att_path:
                        try:
                            from pathlib import Path
                            file_path = Path(att_path).expanduser().resolve()
                            if file_path.exists() and file_path.is_file():
                                file_size = file_path.stat().st_size
                                suffix = file_path.suffix.lower()
                                
                                if file_size <= MAX_AUTO_READ_SIZE:
                                    if suffix in AUTO_READ_TEXT_EXTS:
                                        # 直接读取文本文件
                                        try:
                                            content = file_path.read_text(encoding="utf-8", errors="replace")
                                            # 截断过长内容
                                            if len(content) > 10000:
                                                content = content[:10000] + f"\n... [内容已截断，共 {len(content)} 字符]"
                                            attachment_info += f"\n📄 文件内容 ({att_name}):\n```\n{content}\n```\n"
                                            logger.info("[CHAT][AUTO_READ] text file=%s size=%d", att_name, file_size)
                                        except Exception as read_err:
                                            logger.warning("[CHAT][AUTO_READ] Failed to read %s: %s", att_name, read_err)
                                    
                                    elif suffix == AUTO_READ_PDF_EXT:
                                        # 读取 PDF 文件
                                        try:
                                            import pypdf
                                            with file_path.open("rb") as f:
                                                reader = pypdf.PdfReader(f)
                                                text_parts = []
                                                for i, page in enumerate(reader.pages[:20]):  # 限制20页
                                                    try:
                                                        txt = page.extract_text() or ""
                                                        if txt.strip():
                                                            text_parts.append(f"--- Page {i+1} ---\n{txt}")
                                                    except Exception:
                                                        pass
                                                pdf_content = "\n\n".join(text_parts)
                                                if len(pdf_content) > 15000:
                                                    pdf_content = pdf_content[:15000] + f"\n... [PDF内容已截断，共 {len(pdf_content)} 字符]"
                                                if pdf_content.strip():
                                                    attachment_info += f"\n📄 PDF 内容 ({att_name}, {len(reader.pages)} 页):\n{pdf_content}\n"
                                                    logger.info("[CHAT][AUTO_READ] pdf file=%s pages=%d", att_name, len(reader.pages))
                                        except ImportError:
                                            logger.warning("[CHAT][AUTO_READ] pypdf not installed, skipping PDF auto-read")
                                        except Exception as pdf_err:
                                            logger.warning("[CHAT][AUTO_READ] Failed to read PDF %s: %s", att_name, pdf_err)
                        except Exception as e:
                            logger.warning("[CHAT][AUTO_READ] Error processing attachment %s: %s", att_name, e)
            
            # 根据附件类型给出工具使用建议
            has_image = any(att.get("type") == "image" for att in attachments if isinstance(att, dict))
            has_document = any(att.get("type") in ["document", "application/pdf"] for att in attachments if isinstance(att, dict))
            has_data = any(
                Path(att.get("path", "")).suffix.lower() in {".csv", ".tsv", ".json", ".xlsx", ".xls"} 
                for att in attachments if isinstance(att, dict) and att.get("path")
            )
            
            hints = []
            if has_image:
                hints.append("图片请使用 vision_reader 进行视觉理解")
            if has_data:
                hints.append("数据文件(.csv/.json/.xlsx)请使用 claude_code 进行分析")
            if hints:
                attachment_info += f"\n💡 提示：{'; '.join(hints)}。"
            
            message_to_send = request.message + attachment_info
            logger.info("[CHAT][ATTACHMENTS] session=%s count=%d", request.session_id, len(attachments))

        if plan_session.plan_id is not None:
            context["plan_id"] = plan_session.plan_id
        else:
            context.pop("plan_id", None)

        converted_history = _convert_history_to_agent_format(request.history)

        session_settings: Dict[str, Any] = {}
        
        # 🔄 任务状态同步：优先使用前端传入的 task_id
        # 这个逻辑必须在 session_id 检查之前执行，确保 task_id 总是被处理
        if "task_id" in context and "current_task_id" not in context:
            context["current_task_id"] = context["task_id"]
            logger.info(
                "[CHAT][TASK_SYNC] Using task_id from context: %s",
                context["current_task_id"],
            )
        
        if request.session_id:
            _save_chat_message(request.session_id, "user", request.message)
            session_settings = _get_session_settings(request.session_id)
            # 如果 context 中没有 current_task_id，尝试从 session 加载
            if "current_task_id" not in context:
                current_task_id = _get_session_current_task(request.session_id)
                if current_task_id is not None:
                    context["current_task_id"] = current_task_id
                    logger.info(
                        "[CHAT][TASK_SYNC] Using current_task_id from session: %s",
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

        structured = await agent.get_structured_response(message_to_send)

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
                # 从 job 获取真实状态，而不是使用 agent_result.success
                job_snap = plan_decomposition_jobs.get_job_payload(agent_result.job_id)
                if job_snap:
                    metadata_payload["job_status"] = job_snap.get("status", "queued")
                    metadata_payload["job"] = job_snap
                else:
                    # 如果是同步执行完成的 job，使用 success 判断
                    metadata_payload["job_status"] = (
                            "succeeded" if agent_result.success else "failed"
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


@router.post("/stream")
async def chat_stream(request: ChatRequest, background_tasks: BackgroundTasks):
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
                attachment_info = "\n\n📎 用户当前上传的附件：\n"
                for att in attachments:
                    if isinstance(att, dict):
                        att_type = att.get("type", "file")
                        att_name = att.get("name", "未知文件")
                        att_path = att.get("path", "")
                        att_extracted = att.get("extracted_path")
                        attachment_info += f"- {att_name} ({att_type}): {att_path}\n"
                        if att_extracted:
                            attachment_info += f"  extracted: {att_extracted}\n"
                # 根据附件类型给出工具使用建议
                has_image = any(att.get("type") == "image" for att in attachments if isinstance(att, dict))
                has_document = any(att.get("type") in ["document", "application/pdf"] for att in attachments if isinstance(att, dict))
                if has_image and not has_document:
                    attachment_info += "\n💡 提示：图片文件请使用 vision_reader 进行视觉理解和描述。"
                elif has_document and not has_image:
                    attachment_info += "\n💡 提示：文档文件请使用 document_reader 提取内容。"
                elif has_image and has_document:
                    attachment_info += "\n💡 提示：图片请使用 vision_reader，文档请使用 document_reader。"
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

            yield _sse_message({"type": "start"})

            agent._current_user_message = message_to_send
            prompt = agent._build_prompt(message_to_send)
            model_override = agent.extra_context.get("default_base_model")

            parser = StructuredReplyStreamParser()
            
            # 🚀 Deep Think Mode Check
            if agent._should_use_deep_think(message_to_send):
                logger.info("[CHAT] Activating Deep Think Mode")
                async for chunk in agent.process_deep_think_stream(message_to_send):
                    yield chunk
                return

            # 打字机效果：与 DeepThink 保持一致
            TYPEWRITER_DELAY = 0.01  # 10ms 延迟，与 DeepThink 一致
            BATCH_SIZE = 1  # 逐字符发送，与 DeepThink 一致

            async for chunk in agent.llm_service.stream_chat_async(
                prompt, force_real=True, model=model_override
            ):
                for delta in parser.feed(chunk):
                    if delta:
                        # 逐字符发送，实现平滑打字机效果
                        for char in delta:
                            yield _sse_message({"type": "delta", "content": char})
                            await asyncio.sleep(TYPEWRITER_DELAY)

            raw = parser.full_text()
            cleaned = agent._strip_code_fence(raw)
            structured = LLMStructuredResponse.model_validate_json(cleaned)
            structured = await agent._apply_experiment_fallback(structured)
            structured = agent._apply_plan_first_guardrail(structured)
            structured = agent._apply_phagescope_fallback(structured)
            structured = agent._apply_task_execution_followthrough_guardrail(structured)
            structured = agent._apply_completion_claim_guardrail(structured)
            agent._current_user_message = None

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
                    # 无动作场景直接使用完整回复作为正文分析
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
                    # 从 job 获取真实状态，而不是使用 agent_result.success
                    job_snap = plan_decomposition_jobs.get_job_payload(agent_result.job_id)
                    if job_snap:
                        metadata_payload["job_status"] = job_snap.get("status", "queued")
                        metadata_payload["job"] = job_snap
                    else:
                        # 如果是同步执行完成的 job，使用 success 判断
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

            # 检查是否需要用户确认
            requires_confirm = _requires_confirmation(structured.actions)

            if requires_confirm:
                # 生成确认ID并存储待确认操作
                confirmation_id = _generate_confirmation_id()
                _store_pending_confirmation(
                    confirmation_id=confirmation_id,
                    session_id=request.session_id or "",
                    actions=list(structured.actions),
                    structured=structured,
                    plan_id=plan_session.plan_id,
                    extra_context=agent.extra_context,
                )

                # 构建需要确认的响应
                confirm_actions = [
                    {"kind": a.kind, "name": a.name}
                    for a in structured.actions
                    if (a.kind, a.name) in ACTIONS_REQUIRING_CONFIRMATION
                ]
                suggestions = [
                    "此操作需要您的确认才能执行。",
                    "请点击确认按钮或调用 /chat/confirm 接口确认执行。",
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

            # 不需要确认，正常执行
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


def _normalize_base_model(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate in VALID_BASE_MODELS:
        return candidate
    return None


def _normalize_llm_provider(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if candidate in VALID_LLM_PROVIDERS:
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
    settings: Dict[str, Any] = {}
    provider = _normalize_search_provider(metadata.get("default_search_provider"))
    if provider:
        settings["default_search_provider"] = provider
    base_model = _normalize_base_model(metadata.get("default_base_model"))
    if base_model:
        settings["default_base_model"] = base_model
    llm_provider = _normalize_llm_provider(metadata.get("default_llm_provider"))
    if llm_provider:
        settings["default_llm_provider"] = llm_provider
    return settings or None


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


def _update_session_metadata(
    session_id: str, updater: Callable[[Dict[str, Any]], Dict[str, Any]]
) -> None:
    from ..database import get_db  # lazy import to avoid cycles

    with get_db() as conn:
        _ensure_session_exists(session_id, conn)
        metadata = _load_session_metadata_dict(conn, session_id)
        updated = updater(dict(metadata))
        conn.execute(
            """
            UPDATE chat_sessions
            SET metadata=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (json.dumps(updated, ensure_ascii=False), session_id),
        )
        conn.commit()


def _normalize_modulelist_value(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [str(key) for key in value.keys()]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("{") or raw.startswith("["):
            try:
                parsed = json.loads(raw.replace("'", '"'))
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return [str(key) for key in parsed.keys()]
            if isinstance(parsed, list):
                return [str(item) for item in parsed if item]
        if "," in raw:
            return [item.strip() for item in raw.split(",") if item.strip()]
        return [raw]
    return [str(value)]


def _find_key_recursive(value: Any, key: str) -> Optional[Any]:
    if isinstance(value, dict):
        if key in value:
            return value.get(key)
        for item in value.values():
            found = _find_key_recursive(item, key)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_key_recursive(item, key)
            if found is not None:
                return found
    return None


def _extract_taskid_from_result(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    for key in ("taskid", "task_id"):
        found = _find_key_recursive(result, key)
        if found is not None:
            try:
                return str(found)
            except Exception:
                return None
    return None


def _extract_phagescope_task_snapshot(detail_result: Any) -> Dict[str, Any]:
    """Extract a compact status snapshot from phagescope task_detail output."""
    if not isinstance(detail_result, dict):
        return {}
    payload = detail_result.get("data")
    if not isinstance(payload, dict):
        return {}

    snapshot: Dict[str, Any] = {}
    results = payload.get("results")
    if isinstance(results, dict):
        for key in ("status", "task_status", "state", "taskstatus"):
            value = results.get(key)
            if isinstance(value, str) and value.strip():
                snapshot["remote_status"] = value.strip()
                break

    task_detail = payload.get("parsed_task_detail")
    if not isinstance(task_detail, dict) and isinstance(results, dict):
        raw_task_detail = results.get("task_detail")
        if isinstance(raw_task_detail, dict):
            task_detail = raw_task_detail
        elif isinstance(raw_task_detail, str) and raw_task_detail.strip():
            try:
                parsed = json.loads(raw_task_detail)
                if isinstance(parsed, dict):
                    task_detail = parsed
            except Exception:
                task_detail = None

    if not isinstance(task_detail, dict):
        return snapshot

    task_status = task_detail.get("task_status")
    if isinstance(task_status, str) and task_status.strip():
        snapshot["task_status"] = task_status.strip()

    queue = task_detail.get("task_que")
    if not isinstance(queue, list):
        return snapshot

    done_states = {"COMPLETED", "SUCCESS", "SUCCEEDED", "DONE", "FINISHED"}
    failed_states = {"FAILED", "ERROR"}
    done = 0
    failed = 0
    waiting = 0
    running_modules: List[str] = []
    total = 0
    for item in queue:
        if not isinstance(item, dict):
            continue
        module_name = str(item.get("module") or "").strip()
        if not module_name:
            continue
        status_raw = item.get("module_satus") or item.get("module_status") or item.get("status")
        status_upper = str(status_raw or "").strip().upper()
        total += 1
        if status_upper in done_states:
            done += 1
            continue
        if status_upper in failed_states:
            failed += 1
            continue
        waiting += 1
        if len(running_modules) < 5:
            running_modules.append(module_name)

    snapshot["counts"] = {
        "done": done,
        "failed": failed,
        "waiting": waiting,
        "total": total,
    }
    if running_modules:
        snapshot["running_modules"] = running_modules
    return snapshot


def _build_phagescope_submit_background_summary(
    *,
    taskid: str,
    background_job_id: str,
    module_items: Optional[List[str]] = None,
    snapshot: Optional[Dict[str, Any]] = None,
) -> str:
    snapshot = snapshot or {}
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
    done = counts.get("done") if isinstance(counts.get("done"), int) else 0
    total_from_snapshot = counts.get("total") if isinstance(counts.get("total"), int) else 0
    total = total_from_snapshot or len(module_items or [])
    progress_text = f"{done}/{total}" if total > 0 else "0/?"

    remote_status = (
        str(snapshot.get("remote_status") or "").strip()
        or str(snapshot.get("task_status") or "").strip()
        or "submitted"
    )
    running_modules = snapshot.get("running_modules") if isinstance(snapshot.get("running_modules"), list) else []
    running_suffix = ""
    if running_modules:
        running_suffix = f"，进行中模块：{', '.join(str(x) for x in running_modules[:3])}"

    return (
        f"PhageScope 任务已提交（taskid={taskid}）。"
        f"已完成：submit。"
        f"后台运行中：后台任务ID={background_job_id}，状态={remote_status}，模块进度={progress_text}{running_suffix}。"
        "下一步：在「后台任务」刷新查看最新状态；任务完成后再执行 result/save_all/download。"
    )


def _is_empty_phagescope_param(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _backfill_phagescope_submit_params(
    submit_params: Dict[str, Any],
    *,
    sorted_actions: Sequence[LLMAction],
    submit_action: LLMAction,
    user_message: Optional[str],
) -> Tuple[Dict[str, Any], List[str]]:
    """Best-effort backfill for submit params from earlier PhageScope actions and message."""
    patched = dict(submit_params or {})
    backfilled_keys: List[str] = []
    candidates = (
        "userid",
        "phageid",
        "phageids",
        "modulelist",
        "analysistype",
        "inputtype",
        "sequence_ids",
        "sequence",
        "file_path",
    )

    previous_values: Dict[str, Any] = {}
    for action in sorted_actions:
        if action is submit_action:
            break
        if action.kind != "tool_operation" or action.name != "phagescope":
            continue
        if not isinstance(action.parameters, dict):
            continue
        for key in candidates:
            value = action.parameters.get(key)
            if not _is_empty_phagescope_param(value):
                previous_values[key] = value

    for key, value in previous_values.items():
        if _is_empty_phagescope_param(patched.get(key)):
            patched[key] = value
            backfilled_keys.append(key)

    # If userid is still missing, try extracting an email-like identifier from user message.
    if _is_empty_phagescope_param(patched.get("userid")) and isinstance(user_message, str):
        email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", user_message)
        if email_match:
            patched["userid"] = email_match.group(0)
            backfilled_keys.append("userid(from_message)")

    return patched, backfilled_keys


def _record_phagescope_task_memory(
    session_id: str, params: Dict[str, Any], result: Any
) -> Optional[str]:
    taskid = _extract_taskid_from_result(result)
    if not taskid:
        return None
    entry = {
        "taskid": taskid,
        "userid": params.get("userid"),
        "phageid": params.get("phageid"),
        "modulelist": _normalize_modulelist_value(params.get("modulelist")),
        "created_at": datetime.utcnow().isoformat(),
    }

    def _updater(metadata: Dict[str, Any]) -> Dict[str, Any]:
        tasks = metadata.get("phagescope_recent_tasks")
        if not isinstance(tasks, list):
            tasks = []
        tasks = [item for item in tasks if item.get("taskid") != taskid]
        tasks.insert(0, entry)
        metadata["phagescope_recent_tasks"] = tasks[:10]
        metadata["phagescope_last_taskid"] = taskid
        return metadata

    _update_session_metadata(session_id, _updater)
    return taskid


def _lookup_phagescope_task_memory(
    session_id: str,
    *,
    userid: Optional[str],
    phageid: Optional[str],
    modulelist: Optional[Any],
) -> Optional[str]:
    from ..database import get_db  # lazy import to avoid cycles

    module_items = _normalize_modulelist_value(modulelist)
    module_set = {item.lower() for item in module_items}
    phageid_value = phageid.strip() if isinstance(phageid, str) else None
    userid_value = userid.strip() if isinstance(userid, str) else None

    with get_db() as conn:
        metadata = _load_session_metadata_dict(conn, session_id)

    tasks = metadata.get("phagescope_recent_tasks")
    if not isinstance(tasks, list):
        return metadata.get("phagescope_last_taskid")

    for item in tasks:
        if not isinstance(item, dict):
            continue
        if userid_value and item.get("userid") and item.get("userid") != userid_value:
            continue
        if phageid_value and item.get("phageid") and item.get("phageid") != phageid_value:
            continue
        if module_set:
            stored = {str(val).lower() for val in item.get("modulelist", [])}
            if stored and not module_set.issubset(stored):
                continue
        taskid = item.get("taskid")
        if taskid:
            return str(taskid)
    return metadata.get("phagescope_last_taskid")


def _get_session_settings(session_id: str) -> Dict[str, Any]:
    from ..database import get_db  # lazy import to avoid cycles

    with get_db() as conn:
        metadata = _load_session_metadata_dict(conn, session_id)
    settings = _extract_session_settings(metadata)
    return settings or {}


def _get_session_current_task(session_id: str) -> Optional[int]:
    """Get the current_task_id from session if set."""
    from ..database import get_db  # lazy import to avoid cycles

    with get_db() as conn:
        row = conn.execute(
            "SELECT current_task_id FROM chat_sessions WHERE id=?", (session_id,)
        ).fetchone()
    if not row:
        return None
    task_id = row["current_task_id"]
    if task_id is not None:
        try:
            return int(task_id)
        except (TypeError, ValueError):
            return None
    return None


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


def _load_chat_history(
    session_id: str, limit: int = 50, before_id: Optional[int] = None
) -> Tuple[List[ChatMessage], bool]:
    """Load session history."""
    try:
        from ..database import get_db  # lazy import

        with get_db() as conn:
            cursor = conn.cursor()
            params: List[Any] = [session_id]
            before_clause = ""
            if before_id is not None:
                before_clause = "AND id < ?"
                params.append(before_id)
            params.append(limit + 1)
            cursor.execute(
                f"""
                SELECT id, role, content, metadata, created_at
                FROM chat_messages
                WHERE session_id = ?
                {before_clause}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            )
            rows = cursor.fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()

        messages = [
            ChatMessage(
                id=msg_id,
                role=role,
                content=content,
                timestamp=created_at,
                metadata=_loads_metadata(metadata_raw),
            )
            for msg_id, role, content, metadata_raw, created_at in rows
        ]
        return messages, has_more
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to load history: %s", exc)
        return [], False


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

def _update_message_content_by_tracking(
    session_id: Optional[str],
    tracking_id: Optional[str],
    content: str,
) -> None:
    if not session_id or not tracking_id:
        return
    from ..database import get_db  # lazy import

    pattern = f'%"tracking_id": "{tracking_id}"%'
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id FROM chat_messages WHERE session_id=? AND metadata LIKE ? ORDER BY id DESC LIMIT 1",
                (session_id, pattern),
            ).fetchone()
            if not row:
                return
            conn.execute(
                "UPDATE chat_messages SET content=? WHERE id=?",
                (content, row["id"]),
            )
            conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "Failed to update chat message content for %s: %s", tracking_id, exc
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
    final_summary: Optional[str] = None,
    analysis_text: Optional[str] = None,
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
    if final_summary:
        metadata["final_summary"] = final_summary
    if analysis_text:
        metadata["analysis_text"] = analysis_text

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

    latest_decomposition_job: Optional[Dict[str, Any]] = (
        metadata.get("decomposition_job")
        if isinstance(metadata.get("decomposition_job"), dict)
        else None
    )

    for action in actions or []:
        details = action.get("details") or {}
        embedded_job = details.get("decomposition_job")
        if isinstance(embedded_job, dict):
            embedded_job_id = embedded_job.get("job_id")
            if isinstance(embedded_job_id, str) and embedded_job_id.strip():
                job_summary: Dict[str, Any] = {
                    "job_id": embedded_job_id,
                    "job_type": embedded_job.get("job_type") or "plan_decompose",
                    "status": embedded_job.get("status"),
                    "plan_id": embedded_job.get("plan_id"),
                    "task_id": embedded_job.get("task_id"),
                    "mode": embedded_job.get("mode"),
                    "error": embedded_job.get("error"),
                    "created_at": embedded_job.get("created_at"),
                    "started_at": embedded_job.get("started_at"),
                    "finished_at": embedded_job.get("finished_at"),
                }
                if isinstance(embedded_job.get("stats"), dict):
                    job_summary["stats"] = embedded_job.get("stats")
                if isinstance(embedded_job.get("params"), dict):
                    job_summary["params"] = embedded_job.get("params")
                if isinstance(embedded_job.get("metadata"), dict):
                    job_summary["metadata"] = embedded_job.get("metadata")
                latest_decomposition_job = job_summary
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

    if latest_decomposition_job:
        metadata["decomposition_job"] = latest_decomposition_job
        metadata["decomposition_job_id"] = latest_decomposition_job.get("job_id")
        metadata["decomposition_job_status"] = latest_decomposition_job.get("status")

    return metadata


def _get_llm_service_for_provider(provider: Optional[str]) -> LLMService:
    normalized = _normalize_llm_provider(provider)
    if normalized:
        return LLMService(LLMClient(provider=normalized))
    return get_llm_service()


async def _generate_tool_analysis(
    user_message: str,
    tool_results: List[Dict[str, Any]],
    session_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
) -> Optional[str]:
    """
    让 LLM 基于工具执行结果生成详细分析。
    
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
        web_search_items: List[Dict[str, str]] = []
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
                        tool_desc += f"\n   {field}: {value}"
                if tool_name == "web_search":
                    results = result_data.get("results")
                    if isinstance(results, list) and results:
                        tool_desc += "\n   results:"
                        for item in results:
                            if not isinstance(item, dict):
                                continue
                            title = str(item.get("title") or "").strip()
                            url = str(item.get("url") or "").strip()
                            snippet = str(item.get("snippet") or "").strip()
                            tool_desc += f"\n   - title: {title}"
                            tool_desc += f"\n     url: {url}"
                            if snippet:
                                tool_desc += f"\n     snippet: {snippet}"
                            web_search_items.append(
                                {
                                    "title": title,
                                    "url": url,
                                }
                            )
                else:
                    details_payload = {}
                    for field in ("data", "results", "action", "status_code", "result_kind"):
                        if field in result_data and result_data[field] is not None:
                            details_payload[field] = result_data[field]
                    if details_payload:
                        details_text = json.dumps(details_payload, ensure_ascii=True)
                        if len(details_text) > 1200:
                            details_text = details_text[:1197] + "..."
                        tool_desc += f"\n   details: {details_text}"
            
            tools_description.append(tool_desc)
        
        tools_text = "\n\n".join(tools_description)
        
        analysis_requirements = [
            "1. 给出完整、深入的分析，不要重复用户问题",
            "2. 明确区分结论、依据、注意事项/风险",
            "3. 若涉及 web_search，请逐条列出所有结果并说明价值",
            "4. 若有错误或不确定性，说明原因并给出下一步建议",
            "5. 输出不少于 6 条要点或 3 个自然段",
            "6. Use only fields present in the tool outputs; do not invent paths, modules, or metrics.",
            "7. If a field is missing, explicitly say it was not provided by the tool.",
            "8. Prefer factual summaries over speculation.",
        ]

        base_prompt = (
            "你是资深分析助手。以下是用户问题与工具执行结果。\n"
            "请输出详细分析正文，务必可直接作为最终回答展示。\n\n"
            f"用户问题：{user_message}\n\n"
            "工具执行结果：\n"
            f"{tools_text}\n\n"
            "要求：\n"
            + "\n".join(analysis_requirements)
            + "\n\n输出分析："
        )
        llm_service = _get_llm_service_for_provider(llm_provider)

        analysis = await llm_service.chat_async(base_prompt)
        return analysis.strip() if analysis else None
        
    except Exception as exc:
        logger.error("[CHAT][SUMMARY] Failed to generate analysis for session=%s: %s", session_id, exc)
        return None


async def _generate_tool_summary(
    user_message: str,
    tool_results: List[Dict[str, Any]],
    session_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
) -> Optional[str]:
    """
    让 LLM 基于工具执行结果生成简短摘要（用于过程面板）。
    """
    try:
        tools_description = []
        for idx, tool_result in enumerate(tool_results, 1):
            tool_name = tool_result.get("name", "unknown")
            summary = tool_result.get("summary", "")
            tool_desc = f"{idx}. {tool_name}"
            if summary:
                tool_desc += f" - {summary}"
            tools_description.append(tool_desc)

        tools_text = "\n".join(tools_description)
        prompt = (
            "你是项目助理，请根据工具执行情况给出简短摘要（1-3 句话）。\n"
            f"用户问题：{user_message}\n"
            f"工具执行概览：\n{tools_text}\n"
            "输出摘要："
        )
        llm_service = _get_llm_service_for_provider(llm_provider)
        summary = await llm_service.chat_async(prompt)
        return summary.strip() if summary else None
    except Exception as exc:
        logger.error("[CHAT][SUMMARY] Failed to generate brief summary for session=%s: %s", session_id, exc)
        return None


def _collect_created_tasks_from_steps(steps: List["AgentStep"]) -> List[Dict[str, Any]]:
    created: List[Dict[str, Any]] = []
    for step in steps:
        details = step.details or {}
        created_nodes = details.get("created")
        if isinstance(created_nodes, list):
            for node in created_nodes:
                if isinstance(node, dict):
                    created.append(node)
        task_node = details.get("task")
        if isinstance(task_node, dict):
            created.append(task_node)
    return created


async def _generate_action_analysis(
    user_message: str,
    steps: List["AgentStep"],
    session_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
) -> Optional[str]:
    created_tasks = _collect_created_tasks_from_steps(steps)

    # Also collect structured results from plan/task operations
    step_summaries: List[str] = []
    for step in steps:
        if not step.success:
            continue
        details = step.details or {}
        kind = step.action.kind or ""
        name = step.action.name or ""
        msg = step.message or ""
        if kind in ("plan_operation", "task_operation"):
            detail_text = msg
            if isinstance(details, dict):
                for key in ("plans", "outline", "task", "plan_id", "task_count"):
                    val = details.get(key)
                    if val is not None:
                        detail_text += f"\n{key}: {json.dumps(val, ensure_ascii=False, default=str)[:2000]}"
            step_summaries.append(f"[{kind}/{name}] {detail_text}")

    if created_tasks:
        lines: List[str] = []
        for idx, task in enumerate(created_tasks, 1):
            task_name = str(task.get("name") or task.get("title") or "").strip()
            instruction = str(task.get("instruction") or "").strip()
            if task_name:
                lines.append(f"{idx}. {task_name}")
            if instruction:
                lines.append(f"   - 说明: {instruction}")
        tasks_text = "\n".join(lines)

        prompt = (
            "你是项目分析助手。用户要求对任务拆解结果进行详细分析。"
            "请基于拆解结果给出深入分析：覆盖范围是否充分、任务之间关系、"
            "是否有遗漏、可进一步细化的方向（如有）。不要复述\u201c生成了X个子任务\u201d这类总结，"
            "直接给出专业分析正文。\n"
            "要求：至少 6 条要点或 3 个自然段；要点清晰、具体可执行。\n\n"
            f"用户问题：{user_message}\n\n"
            "拆解结果：\n"
            f"{tasks_text}\n\n"
            "请输出分析："
        )
    elif step_summaries:
        steps_text = "\n\n".join(step_summaries)
        prompt = (
            "你是项目分析助手。用户请求分析后台任务的执行结果。"
            "请基于以下执行步骤的输出，给出结构化的分析：主要发现、关键数据、"
            "下一步建议。直接输出专业分析正文，不要说\u201c我来分析\u201d之类的开场白。\n"
            "要求：内容具体、数据驱动、可操作。\n\n"
            f"用户问题：{user_message}\n\n"
            "执行结果：\n"
            f"{steps_text}\n\n"
            "请输出分析："
        )
    else:
        return None
    try:
        llm_service = _get_llm_service_for_provider(llm_provider)
        analysis = await llm_service.chat_async(prompt)
        return analysis.strip() if analysis else None
    except Exception as exc:
        logger.error(
            "[CHAT][SUMMARY] Failed to generate action analysis for session=%s: %s",
            session_id,
            exc,
        )
        return None


def _build_brief_action_summary(steps: List["AgentStep"]) -> Optional[str]:
    if not steps:
        return None
    if len(steps) == 1:
        step = steps[0]
        if step.message:
            return step.message
        if step.action.name:
            return f"已完成动作：{step.action.name}"
        if step.action.kind:
            return f"已完成动作：{step.action.kind}"
        return None

    names: List[str] = []
    for step in steps:
        if step.action.name:
            names.append(step.action.name)
        elif step.action.kind:
            names.append(step.action.kind)
    if not names:
        return f"已完成 {len(steps)} 个动作。"
    unique = []
    for name in names:
        if name not in unique:
            unique.append(name)
    preview = "、".join(unique[:3])
    suffix = " 等" if len(unique) > 3 else ""
    return f"已完成 {len(steps)} 个动作：{preview}{suffix}。"


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

    # Parse structured payload early so we can decide job_type/mode.
    try:
        structured = LLMStructuredResponse.model_validate_json(record["structured_json"])
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Structured payload invalid for run %s: %s", run_id, exc)
        update_action_run(run_id, status="failed", errors=[str(exc)])
        return

    sorted_actions = structured.sorted_actions()
    primary_action = sorted_actions[0] if sorted_actions else None

    # PhageScope is treated as a special, long-running remote job:
    # submit -> return taskid immediately -> track via backend polling job.
    phagescope_submit_action: Optional[LLMAction] = None
    dropped_phagescope_actions: List[str] = []
    phagescope_only_actions = bool(sorted_actions) and all(
        action.kind == "tool_operation" and action.name == "phagescope"
        for action in sorted_actions
    )
    if phagescope_only_actions:
        for action in sorted_actions:
            if not isinstance(action.parameters, dict):
                continue
            if str(action.parameters.get("action") or "").strip().lower() == "submit":
                phagescope_submit_action = action
                break
        if phagescope_submit_action is not None:
            for action in sorted_actions:
                if action is phagescope_submit_action:
                    continue
                action_name = ""
                if isinstance(action.parameters, dict):
                    action_name = str(action.parameters.get("action") or "").strip().lower()
                dropped_phagescope_actions.append(action_name or f"{action.kind}:{action.name}")

    job_type_to_use = "phagescope_track" if phagescope_submit_action else "chat_action"
    mode_to_use = "phagescope_track" if phagescope_submit_action else (record.get("mode") or "assistant")

    job_plan_id = plan_session.plan_id
    job_metadata = {
        "session_id": record.get("session_id"),
        "mode": mode_to_use,
        "user_message": record.get("user_message"),
    }
    job_params = {
        key: value
        for key, value in {
            "mode": mode_to_use,
            "session_id": record.get("session_id"),
            "plan_id": job_plan_id,
        }.items()
        if value is not None
    }

    try:
        job = plan_decomposition_jobs.create_job(
            plan_id=job_plan_id,
            task_id=None,
            mode=mode_to_use,
            job_type=job_type_to_use,
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
                mode=mode_to_use,
                job_type=job_type_to_use,
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
                "mode": mode_to_use,
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
        base_model_in_context = _normalize_base_model(
            context.get("default_base_model")
        )
        if base_model_in_context:
            context["default_base_model"] = base_model_in_context
        elif record.get("session_id"):
            session_defaults = _get_session_settings(record["session_id"])
            fallback_base_model = session_defaults.get("default_base_model")
            if fallback_base_model:
                context["default_base_model"] = fallback_base_model
        llm_provider_in_context = _normalize_llm_provider(
            context.get("default_llm_provider")
        )
        if llm_provider_in_context:
            context["default_llm_provider"] = llm_provider_in_context
        elif record.get("session_id"):
            session_defaults = _get_session_settings(record["session_id"])
            fallback_llm_provider = session_defaults.get("default_llm_provider")
            if fallback_llm_provider:
                context["default_llm_provider"] = fallback_llm_provider

        # 🔄 任务状态同步：优先使用 context 中的 task_id
        if "task_id" in context and "current_task_id" not in context:
            context["current_task_id"] = context["task_id"]
            logger.info(
                "[CHAT][ASYNC][TASK_SYNC] Using task_id from context: %s",
                context["current_task_id"],
            )
        # 如果 context 中没有，尝试从 session 加载
        if "current_task_id" not in context and record.get("session_id"):
            current_task_id = _get_session_current_task(record["session_id"])
            if current_task_id is not None:
                context["current_task_id"] = current_task_id
                logger.info(
                    "[CHAT][ASYNC][TASK_SYNC] Using current_task_id from session: %s",
                    current_task_id,
                )

        # Special path: PhageScope submit-only tasks run as a background tracker job.
        if phagescope_submit_action is not None:
            plan_decomposition_jobs.mark_running(job.job_id)
            submit_params = dict(phagescope_submit_action.parameters or {})
            submit_params, backfilled_keys = _backfill_phagescope_submit_params(
                submit_params,
                sorted_actions=sorted_actions,
                submit_action=phagescope_submit_action,
                user_message=record.get("user_message"),
            )
            submit_params.setdefault("timeout", 120.0)
            submit_params.setdefault("poll_interval", 30.0)
            submit_params.setdefault("poll_timeout", 172800.0)

            plan_decomposition_jobs.append_log(
                job.job_id,
                "info",
                "Submitting PhageScope remote task.",
                {"parameters": {k: v for k, v in submit_params.items() if k != "token"}},
            )
            if backfilled_keys:
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "info",
                    "Backfilled missing PhageScope submit parameters from context.",
                    {"keys": backfilled_keys},
                )
            if dropped_phagescope_actions:
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "info",
                    "PhageScope submit-only mode enabled; skipped follow-up actions in this turn.",
                    {"skipped_actions": dropped_phagescope_actions},
                )
            missing_fields: List[str] = []
            if _is_empty_phagescope_param(submit_params.get("userid")):
                missing_fields.append("userid")
            if _is_empty_phagescope_param(submit_params.get("modulelist")):
                missing_fields.append("modulelist")
            has_input_source = any(
                not _is_empty_phagescope_param(submit_params.get(key))
                for key in ("phageid", "phageids", "sequence", "file_path", "sequence_ids")
            )
            if not has_input_source:
                missing_fields.append("phageid/phageids/sequence/file_path")
            if missing_fields:
                msg = "PhageScope submit missing required params: " + ", ".join(missing_fields)
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "error",
                    msg,
                    {"missing_fields": missing_fields},
                )
                plan_decomposition_jobs.mark_failure(job.job_id, msg)
                update_action_run(run_id, status="failed", errors=[msg])
                return
            try:
                submit_result = await execute_tool("phagescope", **submit_params)
            except Exception as exc:  # pragma: no cover - defensive
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "error",
                    "PhageScope submit failed.",
                    {"error": str(exc)},
                )
                plan_decomposition_jobs.mark_failure(job.job_id, str(exc))
                update_action_run(run_id, status="failed", errors=[str(exc)])
                return

            taskid = _extract_taskid_from_result(submit_result)
            if not taskid:
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "error",
                    "PhageScope submit returned no taskid.",
                    {"result": submit_result},
                )
                plan_decomposition_jobs.mark_failure(job.job_id, "phagescope submit returned no taskid")
                update_action_run(run_id, status="failed", errors=["phagescope submit returned no taskid"])
                return

            module_items = _normalize_modulelist_value(submit_params.get("modulelist"))
            task_snapshot: Dict[str, Any] = {}
            try:
                detail_result = await execute_tool(
                    "phagescope",
                    action="task_detail",
                    taskid=str(taskid),
                    base_url=submit_params.get("base_url"),
                    timeout=min(float(submit_params.get("timeout") or 60.0), 40.0),
                )
                task_snapshot = _extract_phagescope_task_snapshot(detail_result)
            except Exception as exc:  # pragma: no cover - best-effort
                plan_decomposition_jobs.append_log(
                    job.job_id,
                    "warning",
                    "Initial PhageScope task_detail probe failed; tracker will continue polling.",
                    {"remote_taskid": str(taskid), "error": str(exc)},
                )

            counts_from_snapshot = (
                task_snapshot.get("counts")
                if isinstance(task_snapshot.get("counts"), dict)
                else {}
            )
            done_count = (
                counts_from_snapshot.get("done")
                if isinstance(counts_from_snapshot.get("done"), int)
                else 0
            )
            total_count_raw = (
                counts_from_snapshot.get("total")
                if isinstance(counts_from_snapshot.get("total"), int)
                else None
            )
            total_count = total_count_raw if total_count_raw and total_count_raw > 0 else (
                len(module_items) if module_items else None
            )
            percent = 0
            if isinstance(total_count, int) and total_count > 0:
                percent = int(round((done_count / max(1, total_count)) * 100))
                percent = max(0, min(100, percent))
            remote_status = (
                str(task_snapshot.get("remote_status") or "").strip()
                or str(task_snapshot.get("task_status") or "").strip()
                or "submitted"
            )

            progress_payload: Dict[str, Any] = {
                "tool": "phagescope",
                "taskid": str(taskid),
                "percent": percent,
                "status": remote_status,
                "phase": "submitted",
            }
            if isinstance(total_count, int) and total_count > 0:
                progress_payload["counts"] = {
                    "done": done_count,
                    "total": total_count,
                }

            plan_decomposition_jobs.update_stats(
                job.job_id,
                {
                    "tool_progress": progress_payload
                },
            )

            summary_text = _build_phagescope_submit_background_summary(
                taskid=str(taskid),
                background_job_id=job.job_id,
                module_items=module_items,
                snapshot=task_snapshot,
            )

            plan_decomposition_jobs.append_log(
                job.job_id,
                "info",
                "PhageScope task submitted; tracking in background.",
                {
                    "remote_taskid": str(taskid),
                    "modulelist": module_items,
                    "status_snapshot": task_snapshot or None,
                },
            )

            update_action_run(
                run_id,
                result={
                    "tracking_id": run_id,
                    "execution_mode": "phagescope_track",
                    "final_summary": summary_text,
                    "completed_now": [
                        {"tool": "phagescope", "action": "submit", "taskid": str(taskid)}
                    ],
                    "background_running": [
                        {
                            "tool": "phagescope",
                            "taskid": str(taskid),
                            "backend_job_id": job.job_id,
                            "status": remote_status,
                            "modulelist": module_items,
                            "counts": progress_payload.get("counts"),
                        }
                    ],
                    "phagescope": {
                        "taskid": str(taskid),
                        "backend_job_id": job.job_id,
                        "status": remote_status,
                        "modulelist": module_items,
                        "counts": progress_payload.get("counts"),
                    },
                },
                errors=[],
            )

            # Persist taskid into session metadata for later lookup.
            if record.get("session_id"):
                try:
                    _record_phagescope_task_memory(record["session_id"], submit_params, submit_result)
                except Exception:
                    pass

            # Update the assistant message so the user immediately sees taskid.
            if record.get("session_id"):
                try:
                    _update_message_content_by_tracking(
                        record.get("session_id"),
                        run_id,
                        summary_text,
                    )
                    _update_message_metadata_by_tracking(
                        record.get("session_id"),
                        run_id,
                        lambda existing: {
                            **(existing or {}),
                            "phagescope_taskid": str(taskid),
                            "phagescope_modulelist": module_items,
                            "phagescope_remote_status": remote_status,
                            "phagescope_counts": progress_payload.get("counts"),
                            "phagescope_submit_only": True,
                            "job_type": "phagescope_track",
                        },
                    )
                except Exception:
                    pass

            # Start polling tracker (no auto save_all; user will request later).
            start_phagescope_track_job_thread(
                job_id=job.job_id,
                remote_taskid=str(taskid),
                modulelist=module_items,
                base_url=submit_params.get("base_url"),
                token=None,  # avoid persisting secrets; usually unused for PhageScope
                poll_interval=float(submit_params.get("poll_interval") or 30.0),
                poll_timeout=float(submit_params.get("poll_timeout") or 172800.0),
                request_timeout=40.0,
            )
            # Do not finalize the job here; the tracker thread will mark_success/mark_failure.
            return

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
            
            if step.action.kind not in ("tool_operation", "plan_operation", "task_operation"):
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
        
        # Agent Loop: 生成正文分析 + 过程摘要
        analysis_text: Optional[str] = None
        summary_text: Optional[str] = None
        llm_provider = _normalize_llm_provider(
            (context or {}).get("default_llm_provider")
        )
        if result.success and tool_results_payload:
            logger.info(
                "[CHAT][SUMMARY] session=%s tracking=%s Starting analysis generation...",
                record.get("session_id"),
                run_id,
            )
            try:
                analysis_text = await _generate_tool_analysis(
                    user_message=record.get("user_message", ""),
                    tool_results=tool_results_payload,
                    session_id=record.get("session_id"),
                    llm_provider=llm_provider,
                )
            except Exception as exc:
                logger.error(
                    "[CHAT][SUMMARY] session=%s tracking=%s Failed to generate analysis: %s",
                    record.get("session_id"),
                    run_id,
                    exc,
                    exc_info=True,
                )
        if not analysis_text:
            analysis_text = await _generate_action_analysis(
                record.get("user_message", ""),
                result.steps,
                record.get("session_id"),
                llm_provider,
            )
        if not analysis_text:
            analysis_text = result.reply or result.summarize_steps()

        summary_text = _build_brief_action_summary(result.steps) or result.summarize_steps()

        if analysis_text:
            result_dict["analysis_text"] = analysis_text
        if summary_text:
            result_dict["final_summary"] = summary_text
            try:
                result.final_summary = summary_text
            except Exception:
                pass
        content_for_message = analysis_text or summary_text
        if content_for_message:
            _update_message_content_by_tracking(
                record.get("session_id"),
                run_id,
                content_for_message,
            )
            logger.info(
                "[CHAT][SUMMARY] session=%s tracking=%s Analysis saved: %s",
                record.get("session_id"),
                run_id,
                content_for_message[:100] if len(content_for_message) > 100 else content_for_message,
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
                final_summary=summary_text,
                analysis_text=analysis_text,
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
                result=result_dict,
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
                result=result_dict,
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

    result_data = record.get("result") or {}
    if not isinstance(result_data, dict):
        result_data = {}

    job_snapshot = plan_decomposition_jobs.get_job_payload(tracking_id, include_logs=False)
    if isinstance(job_snapshot, dict):
        stats_payload = job_snapshot.get("stats")
        if isinstance(stats_payload, dict):
            progress = stats_payload.get("tool_progress")
            if isinstance(progress, dict):
                result_data = dict(result_data)
                result_data["tool_progress"] = progress
                if str(progress.get("tool") or "").strip().lower() == "phagescope":
                    phage_result = (
                        dict(result_data.get("phagescope"))
                        if isinstance(result_data.get("phagescope"), dict)
                        else {}
                    )
                    taskid = progress.get("taskid")
                    if taskid is not None:
                        phage_result["taskid"] = str(taskid)
                    status_text = progress.get("status")
                    if isinstance(status_text, str) and status_text.strip():
                        phage_result["status"] = status_text.strip()
                    counts = progress.get("counts")
                    if isinstance(counts, dict):
                        phage_result["counts"] = counts
                    if phage_result:
                        result_data["phagescope"] = phage_result
                    if "background_running" not in result_data:
                        result_data["background_running"] = [
                            {
                                "tool": "phagescope",
                                "taskid": phage_result.get("taskid"),
                                "backend_job_id": tracking_id,
                                "status": phage_result.get("status"),
                                "counts": phage_result.get("counts"),
                            }
                        ]

    # 提取 final_summary 以便前端显示
    final_summary = result_data.get("final_summary")
    if not final_summary and isinstance(job_snapshot, dict):
        stats_payload = job_snapshot.get("stats")
        if isinstance(stats_payload, dict):
            progress = stats_payload.get("tool_progress")
            if (
                isinstance(progress, dict)
                and str(progress.get("tool") or "").strip().lower() == "phagescope"
                and progress.get("taskid") is not None
            ):
                snapshot_payload: Dict[str, Any] = {
                    "remote_status": progress.get("status"),
                }
                if isinstance(progress.get("counts"), dict):
                    snapshot_payload["counts"] = progress.get("counts")
                final_summary = _build_phagescope_submit_background_summary(
                    taskid=str(progress.get("taskid")),
                    background_job_id=tracking_id,
                    module_items=None,
                    snapshot=snapshot_payload,
                )
                result_data["final_summary"] = final_summary

    metadata = {}
    if tool_results:
        metadata["tool_results"] = tool_results
    if final_summary:
        metadata["final_summary"] = final_summary
    if isinstance(job_snapshot, dict):
        metadata["job"] = job_snapshot
        stats_payload = job_snapshot.get("stats")
        if isinstance(stats_payload, dict) and isinstance(stats_payload.get("tool_progress"), dict):
            metadata["tool_progress"] = stats_payload.get("tool_progress")
    
    return ActionStatusResponse(
        tracking_id=tracking_id,
        status=record["status"],
        plan_id=record.get("plan_id"),
        actions=actions,
        result=result_data or record.get("result"),
        errors=record.get("errors"),
        created_at=record.get("created_at"),
        started_at=record.get("started_at"),
        finished_at=record.get("finished_at"),
        metadata=metadata if metadata else None,
    )


@router.post("/actions/{tracking_id}/retry", response_model=ActionStatusResponse)
async def retry_action_run(tracking_id: str, background_tasks: BackgroundTasks):
    """Retry a previous action run by cloning its structured actions."""
    original = fetch_action_run(tracking_id)
    if not original:
        raise HTTPException(status_code=404, detail="Action run not found")

    try:
        structured = LLMStructuredResponse.model_validate_json(original["structured_json"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid structured payload: {exc}")

    # reuse session/plan/context/history/user_message
    new_tracking = f"act_{uuid4().hex}"

    plan_session = PlanSession(repo=plan_repository, plan_id=original.get("plan_id"))
    try:
        plan_session.refresh()
    except ValueError:
        plan_session.detach()

    context = original.get("context") or {}
    history = original.get("history") or []

    # Persist new action run
    create_action_run(
        run_id=new_tracking,
        session_id=original.get("session_id"),
        user_message=original.get("user_message", ""),
        mode=original.get("mode"),
        plan_id=plan_session.plan_id,
        context=context,
        history=history,
        structured_json=original["structured_json"],
    )

    # Plan jobs for visibility
    job_metadata = {
        "session_id": original.get("session_id"),
        "mode": original.get("mode"),
        "user_message": original.get("user_message"),
    }
    job_params = {
        key: value
        for key, value in {
            "mode": original.get("mode"),
            "session_id": original.get("session_id"),
            "plan_id": plan_session.plan_id,
        }.items()
        if value is not None
    }

    try:
        plan_decomposition_jobs.create_job(
            plan_id=plan_session.plan_id,
            task_id=None,
            mode=original.get("mode") or "assistant",
            job_type="chat_action",
            params=job_params,
            metadata=job_metadata,
            job_id=new_tracking,
        )
    except ValueError:
        pass

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

    # 记录动作日志为 queued
    for action in structured.sorted_actions():
        try:
            append_action_log_entry(
                plan_id=plan_session.plan_id,
                job_id=new_tracking,
                job_type="chat_action",
                sequence=action.order if isinstance(action.order, int) else None,
                session_id=original.get("session_id"),
                user_message=original.get("user_message", ""),
                action_kind=action.kind,
                action_name=action.name or "",
                status="queued",
                success=None,
                message="Action queued for execution (retry).",
                parameters=action.parameters,
                details=None,
            )
        except Exception:
            logger.debug("Failed to persist queued action log on retry", exc_info=True)

    background_tasks.add_task(_execute_action_run, new_tracking)

    return ActionStatusResponse(
        tracking_id=new_tracking,
        status="pending",
        plan_id=plan_session.plan_id,
        actions=pending_actions,
        result=None,
        errors=None,
        created_at=None,
        started_at=None,
        finished_at=None,
        metadata={"retry_of": tracking_id},
    )


def _build_action_status_payloads(
    record: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
    """Build action payload list based on stored structured/result data."""
    result = record.get("result") or {}
    if isinstance(result, dict):
        completed_now = result.get("completed_now")
        background_running = result.get("background_running")
        payloads: List[Dict[str, Any]] = []
        if isinstance(completed_now, list):
            for item in completed_now:
                if not isinstance(item, dict):
                    continue
                payloads.append(
                    {
                        "kind": "tool_operation",
                        "name": item.get("tool"),
                        "parameters": {
                            "action": item.get("action"),
                            "taskid": item.get("taskid"),
                        },
                        "order": None,
                        "blocking": False,
                        "status": "completed",
                        "success": True,
                        "message": "completed_now",
                        "details": item,
                    }
                )
        if isinstance(background_running, list):
            for item in background_running:
                if not isinstance(item, dict):
                    continue
                payloads.append(
                    {
                        "kind": "tool_operation",
                        "name": item.get("tool"),
                        "parameters": {
                            "action": "task_detail",
                            "taskid": item.get("taskid"),
                        },
                        "order": None,
                        "blocking": False,
                        "status": "running",
                        "success": None,
                        "message": "background_running",
                        "details": item,
                    }
                )
        if payloads:
            return payloads, None

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
    final_summary: Optional[str] = None
    def summarize_steps(self) -> Optional[str]:
        lines: List[str] = []
        for idx, step in enumerate(self.steps):
            action = step.action
            label_parts = []
            if action.kind:
                label_parts.append(action.kind)
            if action.name:
                label_parts.append(action.name)
            header = "/".join(label_parts) if label_parts else f"步骤 {idx + 1}"
            params = action.parameters or {}
            detail = (
                params.get("instruction")
                or params.get("name")
                or params.get("title")
                or step.message
            )
            lines.append(f"- {header}: {detail or '已完成'}")
            subtasks = params.get("subtasks")
            if isinstance(subtasks, list):
                for st in subtasks:
                    st_name = (
                        (isinstance(st, dict) and (st.get("name") or st.get("title")))
                        or None
                    )
                    st_instr = (
                        (isinstance(st, dict) and st.get("instruction"))
                        or None
                    )
                    if st_name:
                        lines.append(f"  - 子任务: {st_name}")
                    if st_instr:
                        lines.append(f"    · 说明: {st_instr}")
        return "\n".join(lines) if lines else None


class StructuredChatAgent:
    """Plan conversation agent using a structured schema."""

    MAX_HISTORY = 30
    PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*previous\.([^\}]+)\s*\}\}")

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
        base_model = _normalize_base_model(
            self.extra_context.get("default_base_model")
        )
        if base_model:
            self.extra_context["default_base_model"] = base_model
        elif "default_base_model" in self.extra_context:
            self.extra_context.pop("default_base_model", None)
        llm_provider = _normalize_llm_provider(
            self.extra_context.get("default_llm_provider")
        )
        if llm_provider:
            self.extra_context["default_llm_provider"] = llm_provider
        elif "default_llm_provider" in self.extra_context:
            self.extra_context.pop("default_llm_provider", None)

        override_llm_service: Optional[LLMService] = None
        if llm_provider:
            override_llm_service = LLMService(LLMClient(provider=llm_provider))

        self.plan_session = plan_session or PlanSession(repo=plan_repository)
        self.plan_tree = self.plan_session.current_tree()
        self.schema_json = schema_as_json()
        self.llm_service = override_llm_service or get_llm_service()

        if override_llm_service:
            override_decomposer_settings = decomposer_settings
            if base_model:
                override_decomposer_settings = replace(
                    override_decomposer_settings, model=base_model
                )
            override_executor_settings = get_executor_settings()
            if base_model:
                override_executor_settings = replace(
                    override_executor_settings, model=base_model
                )
            decomposer_llm = PlanDecomposerLLMService(
                llm=override_llm_service, settings=override_decomposer_settings
            )
            self.plan_decomposer = PlanDecomposer(
                repo=self.plan_session.repo,
                llm_service=decomposer_llm,
                settings=override_decomposer_settings,
            )
            executor_llm = PlanExecutorLLMService(
                llm=override_llm_service, settings=override_executor_settings
            )
            self.plan_executor = PlanExecutor(
                repo=self.plan_session.repo,
                llm_service=executor_llm,
                settings=override_executor_settings,
            )
        else:
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
        structured = await self._apply_experiment_fallback(structured)
        structured = self._apply_plan_first_guardrail(structured)
        structured = self._apply_phagescope_fallback(structured)
        structured = self._apply_task_execution_followthrough_guardrail(structured)
        structured = self._apply_completion_claim_guardrail(structured)
        return await self.execute_structured(structured)

    async def get_structured_response(self, user_message: str) -> LLMStructuredResponse:
        """Return the raw structured response without executing actions."""
        structured = await self._invoke_llm(user_message)
        structured = await self._apply_experiment_fallback(structured)
        structured = self._apply_plan_first_guardrail(structured)
        structured = self._apply_phagescope_fallback(structured)
        structured = self._apply_task_execution_followthrough_guardrail(structured)
        return self._apply_completion_claim_guardrail(structured)

    async def _apply_experiment_fallback(
        self, structured: LLMStructuredResponse
    ) -> LLMStructuredResponse:
        """Guardrail: only allow manuscript_writer when the user explicitly asks to write a paper."""
        user_message = self._current_user_message or ""
        if not user_message.strip():
            return structured

        manuscript_actions = [
            action
            for action in structured.actions
            if action.kind == "tool_operation" and action.name == "manuscript_writer"
        ]
        if not manuscript_actions:
            return structured

        if not self._explicit_manuscript_request(user_message):
            structured.actions = [
                action
                for action in structured.actions
                if not (action.kind == "tool_operation" and action.name == "manuscript_writer")
            ]
        return structured

    @staticmethod
    def _explicit_manuscript_request(user_message: str) -> bool:
        text = user_message.strip()
        if not text:
            return False
        lowered = text.lower()

        if re.search(r"\b(manuscript|paper)\b", lowered):
            if re.search(r"\b(write|draft|revise|edit|polish|prepare)\b", lowered):
                return True

        if re.search(r"(写|撰写|生成|润色|修改|改写|完善).*(论文|稿件)", text):
            return True
        if re.search(r"(论文|稿件).*(写|撰写|生成|润色|修改|改写|完善)", text):
            return True

        return False

    def _apply_phagescope_fallback(
        self, structured: LLMStructuredResponse
    ) -> LLMStructuredResponse:
        user_message = self._current_user_message or ""
        if not user_message.strip():
            return structured

        def _wants_results(text: str) -> bool:
            text_lower = text.lower()
            triggers = [
                "结果",
                "分析",
                "展示",
                "report",
                "result",
                "results",
                "quality",
                "评估",
                "指标",
            ]
            avoid = [
                "列表",
                "list",
                "任务列表",
                "task list",
            ]
            return any(token in text_lower for token in triggers) and not any(
                token in text_lower for token in avoid
            )

        def _infer_result_kind(text: str) -> Optional[str]:
            text_lower = text.lower()
            if any(token in text_lower for token in ("quality", "质量", "评估", "checkv")):
                return "quality"
            if any(token in text_lower for token in ("protein", "proteins", "蛋白")):
                return "proteins"
            if any(token in text_lower for token in ("fasta", "序列")):
                return "phagefasta"
            if any(token in text_lower for token in ("tree", "系统树")):
                return "tree"
            if any(token in text_lower for token in ("detail", "详情")):
                return "phage_detail"
            if any(token in text_lower for token in ("phage", "噬菌体")):
                return "phage"
            return None

        def _wants_download(text: str) -> bool:
            tl = text.lower()
            triggers = [
                "下载",
                "保存",
                "落盘",
                "导出",
                "save_all",
                "saveall",
            ]
            return any(token in tl for token in triggers)

        def _wants_analysis(text: str) -> bool:
            tl = text.lower()
            triggers = [
                "分析",
                "解读",
                "总结",
                "interpret",
                "analyze",
                "analyse",
            ]
            return any(token in tl for token in triggers)

        def _extract_taskid_from_text(text: str) -> Optional[str]:
            # Support patterns like taskid=36322 / 任务 36322
            m = re.search(r"(?:taskid\s*=?\s*|任务\s*)(\d{4,})", text, flags=re.IGNORECASE)
            if m:
                return m.group(1)
            m = re.search(r"\b(\d{4,})\b", text)
            if m:
                return m.group(1)
            return None

        # Guardrail: for long-running PhageScope workflows, prefer submit-only in this turn.
        # If the model emits submit + result/save_all/download in one response, keep submit only.
        phagescope_actions = [
            action
            for action in structured.actions
            if action.kind == "tool_operation" and action.name == "phagescope"
        ]
        submit_actions = [
            action
            for action in phagescope_actions
            if isinstance(action.parameters, dict)
            and str(action.parameters.get("action") or "").strip().lower() == "submit"
        ]
        if submit_actions and len(phagescope_actions) > 1:
            explicit_taskid = _extract_taskid_from_text(user_message)
            if not (explicit_taskid and (_wants_results(user_message) or _wants_download(user_message))):
                submit_action = sorted(
                    submit_actions,
                    key=lambda item: (item.order if isinstance(item.order, int) else 10**9),
                )[0]
                normalized_submit = LLMAction.model_validate(submit_action.model_dump())
                normalized_submit.order = 1
                normalized_submit.blocking = True
                structured.actions = [normalized_submit]
                if structured.llm_reply and structured.llm_reply.message:
                    structured.llm_reply.message = (
                        "我会先把 PhageScope 任务提交到后台，不在本轮等待远端完成；"
                        "提交后会返回 taskid 与后台运行状态。"
                    )
                return structured

        # One-shot UX: when the user asks to download+analyze, inject a deterministic action chain:
        # 1) phagescope save_all (partial 207 is acceptable)
        # 2) read key files from the saved output directory
        if _wants_download(user_message) and _wants_analysis(user_message):
            taskid_text = _extract_taskid_from_text(user_message)
            taskid_from_history = _extract_taskid_from_text(
                " ".join(str(item.get("content") or "") for item in self.history[-6:])
            )
            taskid_value = taskid_text or taskid_from_history

            try:
                actions: List[LLMAction] = []
                save_params: Dict[str, Any] = {"action": "save_all"}
                if taskid_value:
                    save_params["taskid"] = taskid_value
                actions.append(
                    LLMAction(
                        kind="tool_operation",
                        name="phagescope",
                        parameters=save_params,
                        blocking=True,
                        order=1,
                    )
                )

                # Read files (best-effort). Use placeholders from previous save_all result.
                read_targets = [
                    # Use *_rel paths so file_operations can resolve them safely as relative paths.
                    ("summary", "{{ previous.summary_file_rel }}"),
                    ("quality", "{{ previous.output_directory_rel }}/metadata/quality.json"),
                    ("phage_info", "{{ previous.output_directory_rel }}/metadata/phage_info.json"),
                    ("proteins_tsv", "{{ previous.output_directory_rel }}/annotation/proteins.tsv"),
                    ("proteins_json", "{{ previous.output_directory_rel }}/annotation/proteins.json"),
                ]
                for idx, (label, path_tpl) in enumerate(read_targets, start=2):
                    actions.append(
                        LLMAction(
                            kind="tool_operation",
                            name="file_operations",
                            parameters={"operation": "read", "path": path_tpl},
                            blocking=True,
                            order=idx,
                            metadata={
                                "label": label,
                                "optional": True,
                                "use_anchor": True,
                                "preserve_previous": True,
                            },
                        )
                    )

                structured.actions = actions
                if structured.llm_reply and structured.llm_reply.message:
                    # Keep LLM wording but ensure it doesn't confuse users with tool details.
                    structured.llm_reply.message = (
                        "我会先把 PhageScope 结果下载到本地（即便部分结果缺失也会继续），"
                        "然后读取关键文件并给出结构化解读。"
                    )
                return structured
            except Exception:
                # Fall back to the model's original actions.
                return structured

        if not _wants_results(user_message):
            return structured

        history_text = " ".join(
            str(item.get("content") or "") for item in self.history[-6:]
        )
        inferred_kind = _infer_result_kind(user_message) or _infer_result_kind(history_text)

        for action in structured.actions:
            if action.kind != "tool_operation" or action.name != "phagescope":
                continue
            params = action.parameters or {}
            action_value = params.get("action")
            # Important: do NOT rewrite explicit task_detail/task_list into result.
            # Users often ask for completion status, and converting to result_kind=phage_detail
            # can hit remote endpoints that are not ready / error-prone.
            if action_value == "result" and not params.get("result_kind"):
                params = dict(params)
                params["result_kind"] = inferred_kind or "quality"
                action.parameters = params

        return structured

    @staticmethod
    def _extract_task_id_from_text(text: str) -> Optional[int]:
        if not text:
            return None
        patterns = [
            r"(?:task[_\s-]?id|task)\s*[#:=]?\s*(\d+)",
            r"任务\s*[#:=]?\s*(\d+)",
            r"#(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _is_status_query_only(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        status_tokens = (
            "完成了",
            "完成吗",
            "完成没",
            "done",
            "status",
            "进度",
            "状态",
            "更新了吗",
            "好了没",
            "结果呢",
        )
        execute_tokens = (
            "执行",
            "运行",
            "开始",
            "继续",
            "重跑",
            "重试",
            "run",
            "execute",
            "start",
            "rerun",
            "resume",
        )
        has_status = any(token in lowered for token in status_tokens)
        has_execute = any(token in lowered for token in execute_tokens)
        return has_status and not has_execute

    @staticmethod
    def _user_explicitly_requests_execution(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        execute_tokens = (
            "执行",
            "运行",
            "开始",
            "继续",
            "重跑",
            "重试",
            "去做",
            "撰写",
            "写",
            "run ",
            "execute",
            "start",
            "rerun",
            "resume",
            "do task",
        )
        return any(token in lowered for token in execute_tokens)

    @staticmethod
    def _reply_promises_execution(reply_text: str) -> bool:
        lowered = str(reply_text or "").strip().lower()
        if not lowered:
            return False
        promise_tokens = (
            "我将",
            "我会",
            "马上",
            "立即",
            "接下来",
            "i will",
            "i'll",
            "starting now",
            "immediately",
        )
        action_tokens = (
            "执行",
            "运行",
            "开始",
            "撰写",
            "写入",
            "run",
            "execute",
            "start",
            "draft",
            "write",
        )
        return any(token in lowered for token in promise_tokens) and any(
            token in lowered for token in action_tokens
        )

    def _apply_task_execution_followthrough_guardrail(
        self,
        structured: LLMStructuredResponse,
    ) -> LLMStructuredResponse:
        """Ensure task execution intent always emits a concrete rerun_task action."""
        # region agent log
        try:
            with open("/Users/apple/LLM/agent/.cursor/debug.log", "a", encoding="utf-8") as _dbg:
                _dbg.write(
                    json.dumps(
                        {
                            "id": f"followthrough_enter_{int(__import__('time').time()*1000)}",
                            "timestamp": int(__import__("time").time() * 1000),
                            "runId": "pre-fix-2",
                            "hypothesisId": "N1,N2,N3",
                            "location": "app/routers/chat_routes.py:_apply_task_execution_followthrough_guardrail",
                            "message": "followthrough guardrail entered",
                            "data": {
                                "session_id": self.session_id,
                                "plan_id": self.plan_session.plan_id,
                                "has_actions": bool(structured.actions),
                                "user_message": str(self._current_user_message or "")[:200],
                                "reply_message": (
                                    str(structured.llm_reply.message)[:200]
                                    if structured.llm_reply and isinstance(structured.llm_reply.message, str)
                                    else ""
                                ),
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # endregion
        if structured.actions:
            return structured

        plan_id = self.plan_session.plan_id
        if plan_id is None:
            return structured

        user_message = str(self._current_user_message or "").strip()

        reply_text = (
            structured.llm_reply.message
            if structured.llm_reply and isinstance(structured.llm_reply.message, str)
            else ""
        )

        explicit_execute = self._user_explicitly_requests_execution(user_message)
        promise_execute = self._reply_promises_execution(reply_text)
        # region agent log
        try:
            with open("/Users/apple/LLM/agent/.cursor/debug.log", "a", encoding="utf-8") as _dbg:
                _dbg.write(
                    json.dumps(
                        {
                            "id": f"followthrough_decision_{int(__import__('time').time()*1000)}",
                            "timestamp": int(__import__("time").time() * 1000),
                            "runId": "pre-fix-2",
                            "hypothesisId": "N1,N2,N3",
                            "location": "app/routers/chat_routes.py:_apply_task_execution_followthrough_guardrail",
                            "message": "followthrough decision signals",
                            "data": {
                                "explicit_execute": explicit_execute,
                                "promise_execute": promise_execute,
                                "status_query_only": self._is_status_query_only(user_message),
                                "user_message": user_message[:200],
                                "reply_text": reply_text[:200],
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # endregion
        if not explicit_execute and not promise_execute:
            return structured
        if self._is_status_query_only(user_message) and not promise_execute:
            return structured

        try:
            tree = self.plan_session.repo.get_plan_tree(plan_id)
        except Exception:
            return structured
        target_task_id = self._resolve_followthrough_target_task_id(
            tree=tree,
            user_message=user_message,
            reply_text=reply_text,
        )
        if target_task_id is None:
            return structured
        if not tree.has_node(target_task_id):
            return structured

        node = tree.get_node(target_task_id)
        node_status = str(node.status or "pending").strip().lower()
        if node_status in {"running", "completed", "done"} and self._is_status_query_only(user_message):
            return structured

        structured.actions = [
            LLMAction(
                kind="task_operation",
                name="rerun_task",
                parameters={"task_id": target_task_id},
                blocking=True,
                order=1,
                metadata={
                    "guardrail": "execution_followthrough",
                    "target_task_name": node.display_name(),
                },
            )
        ]
        # region agent log
        try:
            with open("/Users/apple/LLM/agent/.cursor/debug.log", "a", encoding="utf-8") as _dbg:
                _dbg.write(
                    json.dumps(
                        {
                            "id": f"followthrough_injected_{int(__import__('time').time()*1000)}",
                            "timestamp": int(__import__("time").time() * 1000),
                            "runId": "pre-fix-2",
                            "hypothesisId": "N1,N2",
                            "location": "app/routers/chat_routes.py:_apply_task_execution_followthrough_guardrail",
                            "message": "guardrail injected rerun_task",
                            "data": {
                                "target_task_id": target_task_id,
                                "target_task_name": node.display_name(),
                                "node_status": node_status,
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # endregion
        if structured.llm_reply:
            structured.llm_reply.message = (
                f"收到，我现在开始执行任务 #{target_task_id}（{node.display_name()}），"
                "执行结果会同步返回。"
            )
        return structured

    def _resolve_followthrough_target_task_id(
        self,
        *,
        tree: PlanTree,
        user_message: str,
        reply_text: str,
    ) -> Optional[int]:
        explicit_task_id = self._extract_task_id_from_text(user_message)
        if explicit_task_id is not None and tree.has_node(explicit_task_id):
            if not tree.children_ids(explicit_task_id):
                return explicit_task_id
            descendant = self._first_executable_atomic_descendant(tree, explicit_task_id)
            if descendant is not None:
                return descendant

        raw_current_task_id = self.extra_context.get("current_task_id")
        try:
            current_task_id = int(raw_current_task_id) if raw_current_task_id is not None else None
        except (TypeError, ValueError):
            current_task_id = None
        if current_task_id is not None and tree.has_node(current_task_id):
            if not tree.children_ids(current_task_id):
                node = tree.get_node(current_task_id)
                if self._is_task_executable_status(node.status):
                    return node.id
            descendant = self._first_executable_atomic_descendant(tree, current_task_id)
            if descendant is not None:
                return descendant

        keyword_text = "\n".join(
            part.strip()
            for part in (user_message, reply_text)
            if isinstance(part, str) and part.strip()
        )
        keyword_match = self._match_atomic_task_by_keywords(tree, keyword_text)
        if keyword_match is not None:
            return keyword_match

        for node in tree.nodes.values():
            if tree.children_ids(node.id):
                continue
            if self._is_task_executable_status(node.status):
                return node.id
        return None

    @staticmethod
    def _looks_like_completion_claim(reply_text: str) -> bool:
        lowered = str(reply_text or "").strip().lower()
        if not lowered:
            return False
        claim_tokens = (
            "已完成",
            "完成了",
            "全部完成",
            "已创建",
            "已生成",
            "completed",
            "all required files",
            "files have been created",
            "generated successfully",
        )
        return any(token in lowered for token in claim_tokens)

    @staticmethod
    def _extract_declared_absolute_paths(reply_text: str) -> List[str]:
        if not reply_text:
            return []
        pattern = re.compile(r"(/(?:[^\s`\"'<>|])+)")
        paths: List[str] = []
        seen: set[str] = set()
        # CJK and other non-filesystem characters indicate the "/" is part of
        # natural language (e.g. "创建/拆分"), not a real file path.
        _NON_PATH_RE = re.compile(r"[\u2E80-\u9FFF\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFFEF\u3000-\u303F\u3040-\u309F\u30A0-\u30FF\u{20000}-\u{2FA1F}]|[^\x00-\x7F]{2}")
        for match in pattern.findall(reply_text):
            cleaned = match.rstrip(".,;:!?)]}")
            if not cleaned.startswith("/"):
                continue
            # Skip matches that contain CJK or other clearly non-path characters
            if _NON_PATH_RE.search(cleaned):
                continue
            # Must have at least one path separator depth (e.g. /foo/bar)
            if cleaned.count("/") < 2:
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            paths.append(cleaned)
        return paths

    def _apply_completion_claim_guardrail(
        self,
        structured: LLMStructuredResponse,
    ) -> LLMStructuredResponse:
        if not structured.llm_reply or not isinstance(structured.llm_reply.message, str):
            return structured
        reply_text = structured.llm_reply.message
        if not self._looks_like_completion_claim(reply_text):
            return structured

        declared_paths = self._extract_declared_absolute_paths(reply_text)
        if not declared_paths:
            return structured

        missing: List[str] = []
        for path_text in declared_paths[:40]:
            try:
                if not Path(path_text).exists():
                    missing.append(path_text)
            except Exception:
                missing.append(path_text)

        if not missing:
            return structured

        # region agent log
        try:
            with open("/Users/apple/LLM/agent/.cursor/debug.log", "a", encoding="utf-8") as _dbg:
                _dbg.write(
                    json.dumps(
                        {
                            "id": f"completion_guardrail_rewrite_{int(__import__('time').time()*1000)}",
                            "timestamp": int(__import__("time").time() * 1000),
                            "runId": "pre-fix-2",
                            "hypothesisId": "N4",
                            "location": "app/routers/chat_routes.py:_apply_completion_claim_guardrail",
                            "message": "completion claim guardrail rewrote reply",
                            "data": {
                                "declared_paths": declared_paths[:8],
                                "missing_paths": missing[:8],
                                "original_reply": reply_text[:240],
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # endregion
        preview = "\n".join(f"- {item}" for item in missing[:8])
        structured.llm_reply.message = (
            "自动核验发现以下声明文件当前不存在，结果不能判定为“已完成”：\n"
            f"{preview}\n"
            "请先执行并实际落盘，再返回完成结论。"
        )
        return structured

    @staticmethod
    def _is_task_executable_status(status: Optional[str]) -> bool:
        normalized = str(status or "pending").strip().lower()
        return normalized in {"pending", "failed", "skipped"}

    def _first_executable_atomic_descendant(
        self,
        tree: PlanTree,
        parent_task_id: int,
    ) -> Optional[int]:
        queue = list(tree.children_ids(parent_task_id))
        while queue:
            node_id = queue.pop(0)
            if not tree.has_node(node_id):
                continue
            children = tree.children_ids(node_id)
            if children:
                queue.extend(children)
                continue
            node = tree.get_node(node_id)
            if self._is_task_executable_status(node.status):
                return node.id
        return None

    def _match_atomic_task_by_keywords(
        self,
        tree: PlanTree,
        text: str,
    ) -> Optional[int]:
        merged = str(text or "").strip().lower()
        if not merged:
            return None

        keyword_groups: Dict[str, Tuple[str, ...]] = {
            "abstract": ("abstract", "摘要"),
            "introduction": ("introduction", "intro", "引言"),
            "methods": ("method", "methods", "方法"),
            "experiment": ("experiment", "evaluation", "实验", "评估"),
            "result": ("result", "results", "结果"),
            "conclusion": ("conclusion", "总结", "结论"),
            "reference": ("reference", "references", "bib", "参考文献"),
        }
        requested_sections = [
            key
            for key, aliases in keyword_groups.items()
            if any(alias in merged for alias in aliases)
        ]
        if not requested_sections:
            return None

        candidates: List[Tuple[int, int]] = []
        for node in tree.nodes.values():
            if tree.children_ids(node.id):
                continue
            if not self._is_task_executable_status(node.status):
                continue
            node_text = f"{node.display_name()} {node.instruction or ''}".lower()
            score = 0
            for section in requested_sections:
                aliases = keyword_groups.get(section, ())
                if any(alias in node_text for alias in aliases):
                    score += 1
            if score > 0:
                candidates.append((score, node.id))

        if not candidates:
            return None
        candidates.sort(key=lambda row: (-row[0], row[1]))
        return candidates[0][1]

    @staticmethod
    def _is_generic_plan_confirmation(text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        normalized = re.sub(r"[\s，。,.!！?？]+", "", raw).lower()
        generic_phrases = {
            "ok",
            "okay",
            "yes",
            "yep",
            "sure",
            "好的",
            "好",
            "可以",
            "可以的",
            "行",
            "行的",
            "创建吧",
            "开始吧",
            "执行吧",
            "继续吧",
            "可以创建吧",
            "可以开始吧",
            "可以执行吧",
            "可以的创建吧",
            "可以的开始吧",
            "可以的执行吧",
        }
        return normalized in generic_phrases

    def _infer_plan_seed_message(self, current_message: str) -> Optional[str]:
        current = str(current_message or "").strip()
        history = self.history or []
        for item in reversed(history):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            if role != "user":
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            if current and content == current:
                continue
            if self._is_generic_plan_confirmation(content):
                continue
            return content
        return None

    def _apply_plan_first_guardrail(
        self,
        structured: LLMStructuredResponse,
    ) -> LLMStructuredResponse:
        """Guardrail: enforce plan-first for broad project requests."""
        user_message = (self._current_user_message or "").strip()
        if not user_message:
            return structured
        if self.plan_session.plan_id is not None:
            return structured
        if re.search(
            r"(不要|无需|不用|don't|do not)\s*(创建|生成|make|create).*(计划|plan)",
            user_message,
            flags=re.IGNORECASE,
        ):
            return structured

        actions = list(structured.actions or [])
        if not actions:
            return structured

        plan_actions = [action for action in actions if action.kind == "plan_operation"]
        tool_actions = [action for action in actions if action.kind == "tool_operation"]
        if not tool_actions:
            return structured
        if not self._should_force_plan_first(user_message, tool_actions):
            return structured

        existing_create_action = next(
            (
                action
                for action in plan_actions
                if action.name == "create_plan"
            ),
            None,
        )

        request_seed = user_message
        if self._is_generic_plan_confirmation(user_message):
            inferred = self._infer_plan_seed_message(user_message)
            if inferred:
                request_seed = inferred

        compact_title = re.sub(r"\s+", " ", request_seed).strip()
        if len(compact_title) > 80:
            compact_title = compact_title[:80].rstrip()
        if not compact_title:
            compact_title = "Research Project Plan"

        create_params: Dict[str, Any] = {}
        if existing_create_action and isinstance(existing_create_action.parameters, dict):
            create_params.update(existing_create_action.parameters)
        create_params.setdefault("title", compact_title)
        create_params.setdefault("goal", request_seed)
        create_params.setdefault("description", request_seed)

        structured.actions = [
            LLMAction(
                kind="plan_operation",
                name="create_plan",
                parameters=create_params,
                blocking=True,
                order=1,
                metadata={
                    "guardrail": "plan_first",
                    "reason": "prevent_one_shot_tool_execution",
                },
            )
        ]
        if structured.llm_reply and structured.llm_reply.message:
            structured.llm_reply.message = "我会先创建并分解任务图谱，确认计划结构后再按任务执行。"
        return structured

    @staticmethod
    def _should_force_plan_first(
        user_message: str,
        tool_actions: Optional[List[LLMAction]] = None,
    ) -> bool:
        text = (user_message or "").strip()
        lowered = text.lower()
        if not lowered:
            return False

        project_keywords = (
            "从0到1",
            "完整",
            "全流程",
            "整个任务",
            "综述",
            "论文",
            "项目",
            "task graph",
            "任务图谱",
            "project",
            "end-to-end",
            "end to end",
            "roadmap",
            "research plan",
            "manuscript",
            "paper draft",
            "review paper",
        )
        action_keywords = (
            "完成",
            "实现",
            "产出",
            "交付",
            "构建",
            "build",
            "deliver",
            "complete",
            "implement",
            "finish",
        )
        broad_execution_keywords = (
            "一键",
            "全部",
            "全都",
            "一次性",
            "完整项目",
            "whole project",
            "full project",
            "entire workflow",
        )

        has_project_signal = any(token in lowered for token in project_keywords)
        has_action_signal = any(token in lowered for token in action_keywords)
        long_request = len(text) >= 80

        actions = list(tool_actions or [])
        has_claude_action = any(action.name == "claude_code" for action in actions)
        has_heavy_tool_mix = len(actions) >= 2

        claude_task_texts: List[str] = []
        for action in actions:
            if action.name != "claude_code":
                continue
            params = action.parameters or {}
            task_text = str(params.get("task") or "").strip().lower()
            if task_text:
                claude_task_texts.append(task_text)

        claude_task_is_broad = any(
            len(task_text) >= 120
            or any(token in task_text for token in project_keywords)
            or any(token in task_text for token in broad_execution_keywords)
            for task_text in claude_task_texts
        )
        user_message_requests_broad_execution = any(
            token in lowered for token in broad_execution_keywords
        )

        if has_project_signal and (has_action_signal or long_request):
            return True
        if has_claude_action and (
            has_project_signal
            or user_message_requests_broad_execution
            or claude_task_is_broad
        ):
            return True
        if has_claude_action and has_heavy_tool_mix and long_request:
            return True
        return False

    def _resolve_claude_code_task_context(self) -> Tuple[Optional[PlanNode], Optional[str]]:
        plan_id = self.plan_session.plan_id
        if plan_id is None:
            return None, "missing_plan_binding"

        raw_task_id = self.extra_context.get("current_task_id")
        if raw_task_id is None:
            return None, "missing_target_task"

        try:
            task_id = int(raw_task_id)
        except (TypeError, ValueError):
            return None, "invalid_target_task"

        try:
            tree = self.plan_session.repo.get_plan_tree(plan_id)
        except Exception:
            return None, "plan_tree_unavailable"

        if not tree.has_node(task_id):
            return None, "target_task_not_found"

        node = tree.get_node(task_id)
        if tree.children_ids(task_id):
            atomic_task_id = self._first_executable_atomic_descendant(tree, task_id)
            if atomic_task_id is None:
                return None, "target_task_not_atomic"
            try:
                self.extra_context["current_task_id"] = int(atomic_task_id)
            except (TypeError, ValueError):
                pass
            node = tree.get_node(atomic_task_id)
            logger.info(
                "[CLAUDE_CODE] Redirected composite task %s to atomic descendant %s",
                task_id,
                atomic_task_id,
            )

        return node, None

    @staticmethod
    def _normalize_csv_arg(value: Any) -> Optional[str]:
        tokens: List[str] = []
        if value is None:
            return None
        if isinstance(value, str):
            raw_tokens = value.split(",")
            tokens = [token.strip() for token in raw_tokens if token.strip()]
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                if item is None:
                    continue
                item_text = str(item).strip()
                if not item_text:
                    continue
                if "," in item_text:
                    tokens.extend(part.strip() for part in item_text.split(",") if part.strip())
                else:
                    tokens.append(item_text)
        else:
            item_text = str(value).strip()
            if item_text:
                tokens.append(item_text)

        if not tokens:
            return None

        deduped: List[str] = []
        seen = set()
        for token in tokens:
            normalized = token.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(token)
        return ",".join(deduped) if deduped else None

    @staticmethod
    def _summarize_amem_experiences_for_cc(
        experiences: List[Dict[str, Any]],
        *,
        max_items: int = 3,
    ) -> str:
        if not experiences:
            return ""

        lines: List[str] = []
        for exp in experiences[:max_items]:
            content = str(exp.get("content") or "")
            score = exp.get("score")
            score_text = ""
            try:
                if score is not None:
                    score_text = f"{float(score):.2f}"
            except (TypeError, ValueError):
                score_text = ""

            status_match = re.search(r"状态:\s*([^\n]+)", content)
            key_match = re.search(r"##\s*关键发现\s*([\s\S]+)", content)
            key_text = ""
            if key_match:
                key_text = key_match.group(1).splitlines()[0].strip()
            if not key_text:
                for raw_line in content.splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    key_text = line
                    break
            if not key_text:
                key_text = "No concise finding extracted."
            key_text = re.sub(r"\s+", " ", key_text)[:220]

            segments: List[str] = []
            if score_text:
                segments.append(f"score={score_text}")
            if status_match:
                segments.append(status_match.group(1).strip())
            header = f"[{' | '.join(segments)}] " if segments else ""
            lines.append(f"- {header}{key_text}")

        return "\n".join(lines)

    @staticmethod
    def _compose_claude_code_atomic_task_prompt(
        *,
        task_node: PlanNode,
        original_task: str,
        amem_hints: str = "",
    ) -> str:
        task_instruction = (task_node.instruction or "").strip() or original_task.strip()
        user_task_context = original_task.strip()
        if len(user_task_context) > 1200:
            user_task_context = user_task_context[:1200].rstrip() + "..."

        lines: List[str] = [
            "[OUTER AGENT EXECUTION CONTRACT]",
            "You are a code execution worker for ONE atomic task. Planning is forbidden.",
            f"Plan ID: {task_node.plan_id}",
            f"Task ID: {task_node.id}",
            f"Task Name: {task_node.display_name()}",
            "",
            "Atomic task objective:",
            task_instruction or "No instruction provided.",
            "",
            "Mandatory rules:",
            "- Execute ONLY this atomic task.",
            "- Do NOT create roadmap, decomposition, or extra tasks.",
            "- Do NOT execute sibling or downstream tasks.",
            "- Keep outputs scoped to the current task deliverables.",
            "- If this task still needs decomposition or broader planning, STOP and output exactly:",
            "  STATUS: BLOCKED_SCOPE",
            "  REASON: NEED_ATOMIC_TASK",
            "  DETAIL: <one sentence>",
        ]

        if user_task_context and user_task_context != task_instruction:
            lines.extend(
                [
                    "",
                    "User-provided context (reference only, do not expand scope):",
                    user_task_context,
                ]
            )

        if amem_hints:
            lines.extend(
                [
                    "",
                    "Historical execution hints (reference only, never expand scope):",
                    amem_hints,
                ]
            )

        return "\n".join(lines)

    def _resolve_previous_path(
        self, previous_result: Dict[str, Any], path: str
    ) -> Optional[Any]:
        if not path:
            return None
        if path in {"taskid", "task_id"}:
            return _find_key_recursive(previous_result, "taskid") or _find_key_recursive(
                previous_result, "task_id"
            )
        current: Any = previous_result
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
                continue
            if isinstance(current, list):
                try:
                    index = int(part)
                except ValueError:
                    return None
                if 0 <= index < len(current):
                    current = current[index]
                    continue
            return None
        if current is not None:
            return current
        fallback_key = path.split(".")[-1]
        return _find_key_recursive(previous_result, fallback_key)

    def _resolve_placeholders_in_value(
        self, value: Any, previous_result: Dict[str, Any]
    ) -> Any:
        if isinstance(value, str):
            def _replace(match: re.Match) -> str:
                token = match.group(1).strip()
                resolved = self._resolve_previous_path(previous_result, token)
                if resolved is None:
                    return match.group(0)
                return str(resolved)

            return self.PLACEHOLDER_PATTERN.sub(_replace, value)
        if isinstance(value, dict):
            return {
                key: self._resolve_placeholders_in_value(item, previous_result)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                self._resolve_placeholders_in_value(item, previous_result)
                for item in value
            ]
        return value

    def _resolve_action_placeholders(
        self, action: LLMAction, previous_result: Optional[Dict[str, Any]]
    ) -> LLMAction:
        if not previous_result:
            return action
        if not isinstance(action.parameters, dict):
            return action
        resolved = self._resolve_placeholders_in_value(
            action.parameters, previous_result
        )
        action.parameters = resolved
        return action

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

        previous_result: Optional[Dict[str, Any]] = None
        anchor_result: Optional[Dict[str, Any]] = None
        for action in structured.sorted_actions():
            placeholder_source = previous_result
            if isinstance(action.metadata, dict) and action.metadata.get("use_anchor") and anchor_result:
                placeholder_source = anchor_result
            action = self._resolve_action_placeholders(action, placeholder_source)
            if (
                action.kind == "tool_operation"
                and action.name == "phagescope"
                and isinstance(action.parameters, dict)
                and steps
            ):
                last_step = steps[-1]
                last_params = (
                    last_step.details.get("parameters")
                    if isinstance(last_step.details, dict)
                    else None
                )
                if (
                    last_step.action.kind == "tool_operation"
                    and last_step.action.name == "phagescope"
                    and last_step.success
                    and isinstance(last_params, dict)
                    and last_params.get("action") == "submit"
                ):
                    current_action = action.parameters.get("action")
                    if current_action in {"result", "quality", "save_all", "download"}:
                        patched = dict(action.parameters)
                        taskid_value = patched.get("taskid")
                        if taskid_value is not None:
                            try:
                                int(str(taskid_value).strip())
                            except (TypeError, ValueError):
                                patched.pop("taskid", None)
                        if not patched.get("taskid") and previous_result:
                            extracted_taskid = _extract_taskid_from_result(previous_result)
                            if extracted_taskid:
                                patched["taskid"] = extracted_taskid
                        # Do not block on immediate result retrieval after submit.
                        # Convert follow-up actions to a lightweight status query.
                        patched["action"] = "task_detail"
                        patched.pop("result_kind", None)
                        patched.pop("download_path", None)
                        patched.pop("save_path", None)
                        patched.pop("wait", None)
                        patched.pop("poll_interval", None)
                        patched.pop("poll_timeout", None)
                        action.parameters = patched
            retry_limit = 0
            backoff_sec = 0.0
            if action.retry_policy is not None:
                retry_limit = max(0, int(action.retry_policy.max_retries))
                backoff_sec = max(0.0, float(action.retry_policy.backoff_sec))

            attempt = 0
            step: Optional[AgentStep] = None
            while attempt <= retry_limit:
                attempt += 1
                try:
                    step = await self._execute_action(action)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception("Action execution failed: %s", exc)
                    step = AgentStep(
                        action=action,
                        success=False,
                        message=f"Action execution failed: {exc}",
                        details={"exception": type(exc).__name__},
                    )

                if step.success or attempt > retry_limit:
                    break

                retry_message = (
                    f"Action {action.kind}/{action.name} failed on attempt "
                    f"{attempt}/{retry_limit + 1}; retrying."
                )
                errors.append(retry_message)
                logger.warning(retry_message)
                if backoff_sec > 0:
                    await asyncio.sleep(backoff_sec)

            if step is None:  # pragma: no cover - defensive
                step = AgentStep(
                    action=action,
                    success=False,
                    message="Action execution failed with an unknown error.",
                    details={"exception": "UnknownError"},
                )

            step.details = dict(step.details or {})
            step.details.setdefault("attempt", attempt)
            step.details.setdefault("max_attempts", retry_limit + 1)
            if action.retry_policy is not None:
                step.details.setdefault(
                    "retry_policy",
                    {"max_retries": retry_limit, "backoff_sec": backoff_sec},
                )

            steps.append(step)
            details = step.details or {}
            result_payload = details.get("result")
            if isinstance(result_payload, dict):
                if (
                    anchor_result is None
                    and step.action.kind == "tool_operation"
                    and step.action.name == "phagescope"
                    and isinstance(details.get("parameters"), dict)
                    and (details["parameters"].get("action") == "save_all")
                ):
                    anchor_result = result_payload

            if not (isinstance(action.metadata, dict) and action.metadata.get("preserve_previous")):
                previous_result = result_payload if isinstance(result_payload, dict) else None

            if not step.success:
                errors.append(step.message)
                if action.blocking:
                    block_message = (
                        f"Stopping execution because blocking action "
                        f"{action.kind}/{action.name} failed."
                    )
                    errors.append(block_message)
                    logger.warning(block_message)
                    break

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

        # Special case: one-shot "download + analyze" chain for PhageScope.
        # We must synthesize the analysis here (there is no post-tool LLM pass in this mode).
        try:
            synthesized = self._maybe_synthesize_phagescope_saveall_analysis(steps)
            if synthesized:
                reply_text = synthesized
        except Exception as exc:  # pragma: no cover - best-effort
            logger.debug("Failed to synthesize phagescope save_all analysis: %s", exc)
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

        return result

    def _maybe_synthesize_phagescope_saveall_analysis(self, steps: List[AgentStep]) -> Optional[str]:
        """If the action sequence matches save_all + local reads, return a structured analysis string."""
        if not steps:
            return None

        save_step: Optional[AgentStep] = None
        for step in steps:
            if step.action.kind == "tool_operation" and step.action.name == "phagescope":
                params = (
                    step.details.get("parameters")
                    if isinstance(step.details, dict)
                    else None
                )
                if isinstance(params, dict) and params.get("action") == "save_all":
                    save_step = step
                    break
        if not save_step or not isinstance(save_step.details, dict):
            return None

        save_result = save_step.details.get("result")
        if not isinstance(save_result, dict):
            return None

        # Detect the injected chain by presence of file_operations reads with metadata labels.
        reads: Dict[str, str] = {}
        for step in steps:
            if step.action.kind != "tool_operation" or step.action.name != "file_operations":
                continue
            label = step.action.metadata.get("label") if isinstance(step.action.metadata, dict) else None
            if not isinstance(label, str) or not label:
                continue
            result = step.details.get("result") if isinstance(step.details, dict) else None
            if isinstance(result, dict) and isinstance(result.get("content"), str):
                reads[label] = result["content"]

        if not reads:
            return None

        output_dir = save_result.get("output_directory") or save_result.get("output_directory_rel")
        status_code = save_result.get("status_code")
        missing = save_result.get("missing_artifacts") or []
        missing_text = ""
        if isinstance(missing, list) and missing:
            missing_text = f"（部分缺失：{', '.join(str(x) for x in missing)}）"

        # Parse key jsons (best-effort)
        phage_info = None
        quality = None
        try:
            if "phage_info" in reads:
                phage_info = json.loads(reads["phage_info"]).get("results")
        except Exception:
            phage_info = None
        try:
            if "quality" in reads:
                quality = json.loads(reads["quality"]).get("results")
        except Exception:
            quality = None

        # Extract host/lifestyle/taxonomy
        host = lifestyle = taxonomy = gc_content = length = genes = None
        if isinstance(phage_info, list) and phage_info:
            row = phage_info[0] if isinstance(phage_info[0], dict) else None
            if isinstance(row, dict):
                host = row.get("host")
                lifestyle = row.get("lifestyle")
                taxonomy = row.get("taxonomy")
                gc_content = row.get("gc_content")
                length = row.get("length")
                genes = row.get("genes")

        # Extract quality summary
        qsum = None
        if isinstance(quality, dict):
            q = quality.get("quality_summary")
            if isinstance(q, list) and q and isinstance(q[0], dict):
                qsum = q[0]

        # Proteins: count + top5 annotations
        protein_count = None
        top5 = []
        proteins_tsv = reads.get("proteins_tsv")
        if isinstance(proteins_tsv, str) and proteins_tsv.strip():
            lines = [ln for ln in proteins_tsv.splitlines() if ln.strip()]
            if len(lines) >= 2:
                protein_count = max(0, len(lines) - 1)
                header = lines[0].split("\t")
                idx = None
                for i, col in enumerate(header):
                    if col.strip() in {"Protein_function_classification", "function", "annotation"}:
                        idx = i
                        break
                for ln in lines[1:6]:
                    cols = ln.split("\t")
                    if idx is not None and idx < len(cols):
                        top5.append(cols[idx].strip())
        # Fallback: parse proteins.json when TSV missing/empty
        if (protein_count is None or not top5) and isinstance(reads.get("proteins_json"), str):
            try:
                payload = json.loads(reads["proteins_json"])
                results = payload.get("results") if isinstance(payload, dict) else None
                if isinstance(results, list):
                    if protein_count is None:
                        protein_count = len(results)
                    if not top5:
                        for item in results[:5]:
                            if not isinstance(item, dict):
                                continue
                            val = (
                                item.get("Protein_function_classification")
                                or item.get("function")
                                or item.get("annotation")
                            )
                            if val is not None:
                                top5.append(str(val).strip())
            except Exception:
                pass
        # Fallback: if no tsv, try summary/task_detail or phage_info genes
        if protein_count is None:
            try:
                if isinstance(genes, str) and genes.isdigit():
                    protein_count = int(genes)
            except Exception:
                protein_count = None

        lines: List[str] = []
        lines.append(f"已下载到：{output_dir}{missing_text}")
        if status_code == 207:
            lines.append("提示：本次为 207（部分成功），核心结果可用，我已按可用结果继续解读。")

        lines.append("")
        lines.append("## 结构化解读")
        if isinstance(qsum, dict):
            lines.append("- **质量指标**：")
            lines.append(
                "  - contig_id={cid}, length={clen}, gene_count={gc}, checkv_quality={cq}, miuvig_quality={mq}, completeness={comp}, contamination={cont}".format(
                    cid=qsum.get("contig_id"),
                    clen=qsum.get("contig_length"),
                    gc=qsum.get("gene_count"),
                    cq=qsum.get("checkv_quality"),
                    mq=qsum.get("miuvig_quality"),
                    comp=qsum.get("completeness"),
                    cont=qsum.get("contamination"),
                )
            )
        else:
            lines.append("- **质量指标**：未能读取 `metadata/quality.json`，请检查该文件是否存在或是否被安全策略拦截。")

        lines.append("- **宿主/生活方式**：")
        if host or lifestyle or taxonomy:
            lines.append(f"  - host={host}, lifestyle={lifestyle}, taxonomy={taxonomy}")
            lines.append(f"  - length={length}, genes={genes}, gc_content={gc_content}")
        else:
            lines.append("  - 未能读取 `metadata/phage_info.json` 或内容为空。")

        lines.append("- **蛋白注释**：")
        if protein_count is not None:
            lines.append(f"  - 蛋白数量：{protein_count}")
        else:
            lines.append("  - 蛋白数量：未能统计（可稍后补充读取 proteins.json/tsv）")
        if top5:
            lines.append("  - 前 5 条注释：")
            for i, item in enumerate(top5, 1):
                lines.append(f"    {i}. {item}")
        else:
            lines.append("  - 前 5 条注释：未能从 `annotation/proteins.tsv` 提取（可能缺失或读取失败）。")

        return "\n".join(lines).strip()

    def _should_use_deep_think(self, message: str) -> bool:
        """Check if deep think mode should be activated."""
        # Check explicit trigger
        if message.startswith("/think ") or message.startswith("/深度") or message.startswith("/deep"):
            return True
        # Check context flag
        if self.extra_context.get("deep_think_enabled", False):
            return True
        # Default: force DeepThink for plan creation / research planning.
        # This prevents shallow plans and enables rubric-based self-optimization.
        msg = (message or "").strip().lower()
        if not msg:
            return False
        plan_keywords = (
            "create plan",
            "make a plan",
            "plan for",
            "research plan",
            "project plan",
            "roadmap",
            "计划",
            "研究计划",
            "项目计划",
            "任务计划",
            "规划",
            "拆解",
            "分解",
            "任务树",
        )
        if any(k in msg for k in plan_keywords):
            return True
        return False

    async def process_deep_think_stream(self, user_message: str) -> AsyncIterator[str]:
        """
        Execute deep thinking process and yield SSE events with streaming support.
        """
        # Create a queue for events
        queue = asyncio.Queue()
        
        async def on_thinking(step: ThinkingStep):
            await queue.put({
                "type": "thinking_step",
                "step": {
                    "iteration": step.iteration,
                    "thought": step.thought,
                    "action": step.action,
                    "status": step.status,
                    "action_result": step.action_result
                }
            })

        async def on_thinking_delta(iteration: int, delta: str):
            """Send token-level updates for thinking process."""
            logger.debug(f"[DEEP_THINK_DELTA] iteration={iteration} delta_len={len(delta)}")
            await queue.put({
                "type": "thinking_delta",
                "iteration": iteration,
                "delta": delta
            })

        async def on_final_delta(delta: str):
            """Send token-level updates for final answer."""
            await queue.put({
                "type": "delta",
                "content": delta
            })

        async def run_agent():
            try:
                deep_think_tool_order = 0
                deep_think_bg_category: Optional[str] = None

                # Wrapper for tool execution with plan_operation binding
                async def tool_wrapper(name: str, params: Dict[str, Any]) -> Any:
                    nonlocal deep_think_tool_order, deep_think_bg_category
                    safe_params = params if isinstance(params, dict) else {}

                    if name != "plan_operation":
                        deep_think_tool_order += 1
                        synthetic_action = LLMAction(
                            kind="tool_operation",
                            name=name,
                            parameters=safe_params,
                            order=max(1, deep_think_tool_order),
                            blocking=True,
                            metadata={"origin": "deep_think"},
                        )
                        step = await self._handle_tool_action(synthetic_action)

                        details = step.details if isinstance(step.details, dict) else {}
                        result_payload = details.get("result")
                        if isinstance(result_payload, dict):
                            result = dict(result_payload)
                            if isinstance(step.message, str) and step.message.strip():
                                result.setdefault("summary", step.message.strip())
                            storage_payload = details.get("storage")
                            if storage_payload is not None:
                                result.setdefault("storage", storage_payload)
                            deliverables_payload = details.get("deliverables")
                            if deliverables_payload is not None:
                                result.setdefault("deliverables", deliverables_payload)
                            return result

                        fallback_result: Dict[str, Any] = {
                            "success": bool(step.success),
                            "tool": name,
                            "message": step.message,
                        }
                        if isinstance(details, dict) and details:
                            fallback_result["details"] = details
                        return fallback_result

                    result = await execute_tool(name, **safe_params)
                    
                    # Special handling: bind Plan to session after successful creation
                    if name == "plan_operation" and isinstance(result, dict):
                        if result.get("success") and result.get("operation") == "create":
                            plan_id = result.get("plan_id")
                            if plan_id:
                                try:
                                    # Bind the newly created plan to the current session
                                    self.plan_session.bind(plan_id)
                                    self._refresh_plan_tree(force_reload=True)
                                    self.extra_context["plan_id"] = plan_id
                                    self._dirty = True
                                    
                                    # CRITICAL: Also update the database session record
                                    # so that frontend can fetch the new plan_id
                                    if self.session_id:
                                        _set_session_plan_id(self.session_id, plan_id)
                                        logger.info(f"[DeepThink] Updated database session {self.session_id} with plan_id={plan_id}")
                                    
                                    # CRITICAL: Trigger automatic task decomposition
                                    # This ensures DeepThink-created plans get the same
                                    # multi-level decomposition as regular plans
                                    session_ctx = {
                                        "user_message": user_message,
                                        "chat_history": self.history,
                                        "recent_tool_results": self.extra_context.get(
                                            "recent_tool_results", []
                                        ),
                                    }
                                    decompose_result = await asyncio.to_thread(
                                        self._auto_decompose_plan,
                                        plan_id,
                                        wait_for_completion=False,
                                        session_context=session_ctx,
                                    )
                                    if decompose_result:
                                        if decompose_result.get("result") is not None:
                                            summary = decompose_result["result"]
                                            logger.info(
                                                "[DeepThink] Auto-decomposition completed for plan %s",
                                                plan_id,
                                            )
                                            result["decomposition_completed"] = True
                                            result["decomposition_created"] = len(
                                                summary.created_tasks
                                            )
                                            result["decomposition_stats"] = summary.stats
                                            result["decomposition_note"] = (
                                                "Automatic task decomposition completed before review."
                                            )
                                        elif decompose_result.get("job") is not None:
                                            logger.info(
                                                "[DeepThink] Auto-decomposition submitted for plan %s",
                                                plan_id,
                                            )
                                            result["decomposition_triggered"] = True
                                            result["decomposition_note"] = (
                                                "Automatic task decomposition has been submitted for background execution."
                                            )

                                    # Mark that a background decomposition was started
                                    # so the final SSE event can include background_category.
                                    deep_think_bg_category = "task_creation"

                                    # NOTE: Auto optimization loop is skipped when
                                    # decomposition runs in the background because it
                                    # depends on the completed task tree.  The user can
                                    # trigger plan review/optimize manually after
                                    # decomposition finishes.
                                    logger.info(
                                        "[DeepThink] Auto-bound plan %s to session "
                                        "(decomposition dispatched to background, "
                                        "auto-optimize skipped)",
                                        plan_id,
                                    )
                                except Exception as bind_err:
                                    logger.warning(f"[DeepThink] Failed to bind plan {plan_id}: {bind_err}")
                    
                    return result
                
                # Instantiate DeepThinkAgent with streaming callbacks
                dt_agent = DeepThinkAgent(
                    llm_client=self.llm_service,
                    available_tools=["web_search", "graph_rag", "claude_code", "file_operations", "document_reader", "vision_reader", "bio_tools", "phagescope", "result_interpreter", "plan_operation"],
                    tool_executor=tool_wrapper,
                    max_iterations=100,
                    tool_timeout=120,  # 2分钟工具超时
                    on_thinking=on_thinking,
                    on_thinking_delta=on_thinking_delta,
                    on_final_delta=on_final_delta
                )
                
                # 构建上下文，包含对话历史
                think_context = {
                    **self.extra_context,
                    "chat_history": self.history,
                    "session_id": self.session_id,
                }
                
                # Run think
                result = await dt_agent.think(user_message, think_context)
                await queue.put({
                    "type": "result",
                    "result": result,
                    "bg_category": deep_think_bg_category,
                })
            except Exception as e:
                logger.exception("Deep think execution failed")
                await queue.put({"type": "error", "error": str(e)})
            finally:
                await queue.put(None) # Signal end

        # Start agent in background
        asyncio.create_task(run_agent())
        
        # Consume queue
        while True:
            item = await queue.get()
            if item is None:
                break
            
            if item.get("type") == "thinking_step":
                # Yield thinking event
                yield _sse_message(item)
            elif item.get("type") == "thinking_delta":
                # Yield thinking delta for streaming display
                yield _sse_message(item)
            elif item.get("type") == "delta":
                # Yield final answer delta for streaming display
                yield _sse_message(item)
            elif item.get("type") == "error":
                yield _sse_message({"type": "error", "message": item["error"]})
            elif item.get("type") == "result":
                # Final result, yield as standard chat message
                res: DeepThinkResult = item["result"]
                
                # Construct final content for display and saving
                final_content_parts = []
                # Thinking Summary removed per user request
                if res.final_answer:
                    final_content_parts.append(res.final_answer)
                
                full_response = "\n\n".join(final_content_parts)
                
                # 💾 Save Deep Think response to database
                if self.session_id and full_response:
                    try:
                        _save_chat_message(
                            self.session_id,
                            "assistant",
                            full_response,
                            metadata={
                                "deep_think": True,
                                "iterations": res.total_iterations,
                                "tools_used": res.tools_used,
                                "confidence": res.confidence,
                                "thinking_process": {
                                    "status": "completed",
                                    "total_iterations": res.total_iterations,
                                    "steps": [
                                        {
                                            "iteration": s.iteration,
                                            "thought": s.thought,
                                            "action": s.action,
                                            "action_result": s.action_result,
                                            "status": "done" if s.status == "done" else "completed", # Normalize status for history
                                            "timestamp": s.timestamp.isoformat() if s.timestamp else None
                                        }
                                        for s in res.thinking_steps
                                    ]
                                }
                            }
                        )
                        logger.info("[CHAT][DEEP_THINK] Response saved to database for session=%s", self.session_id)
                    except Exception as save_err:
                        logger.warning("[CHAT][DEEP_THINK] Failed to save response: %s", save_err)
                
                # Thinking Summary removed per user request
                # if res.thinking_summary:
                #    yield _sse_message({"type": "delta", "content": f"**Thinking Summary:** {res.thinking_summary}"})
                pass
                
                # Note: final_answer was already streamed via on_final_delta callback
                # No need to yield it again here to avoid duplication
                
                # Mock a final structure event to satisfy frontend if needed, 
                # or just let the stream end (frontend usually handles text)
                bg_category = item.get("bg_category")
                final_metadata: Dict[str, Any] = {
                    "plan_id": self.plan_session.plan_id,  # Include plan_id so frontend can update
                    "deep_think": True,
                }
                if bg_category:
                    final_metadata["background_category"] = bg_category
                payload = {
                    "llm_reply": {"message": res.final_answer},
                    "actions": [],
                    "metadata": final_metadata,
                }
                yield _sse_message({"type": "final", "payload": payload})

    async def _invoke_llm(self, user_message: str) -> LLMStructuredResponse:
        self._current_user_message = user_message
        prompt = self._build_prompt(user_message)
        model_override = self.extra_context.get("default_base_model")
        raw = await self.llm_service.chat_async(
            prompt, force_real=True, model=model_override
        )
        cleaned = self._strip_code_fence(raw)
        return LLMStructuredResponse.model_validate_json(cleaned)

    def _build_prompt(self, user_message: str) -> str:
        plan_bound = self.plan_session.plan_id is not None
        history_text = self._format_history()
        
        # 从 extra_context 中提取记忆，单独格式化
        memories = self.extra_context.pop("memories", None)
        memory_section = self._format_memories(memories) if memories else ""
        
        context_text = json.dumps(self.extra_context, ensure_ascii=False, indent=2)
        plan_outline = self.plan_session.outline(max_depth=4, max_nodes=100)
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
        ]
        
        # 如果有相关记忆，添加到 prompt 中
        if memory_section:
            prompt_parts.append(memory_section)
        
        prompt_parts.extend([
            f"History (latest {self.MAX_HISTORY} messages):\n{history_text}",
            "\n=== Plan Overview ===",
            plan_outline,
        ])
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

    def _format_memories(self, memories: List[Dict[str, Any]]) -> str:
        """格式化记忆列表为 prompt 中的文本"""
        if not memories:
            return ""
        
        lines = ["\n=== Relevant Memories (from previous conversations) ==="]
        for mem in memories:
            content = mem.get("content", "")
            similarity = mem.get("similarity", 0)
            mem_type = mem.get("memory_type", "unknown")
            similarity_pct = int(similarity * 100) if similarity else 0
            lines.append(f"- [{similarity_pct}% match, type: {mem_type}] {content}")
        
        lines.append("(Use these memories as context to provide more relevant responses)")
        return "\n".join(lines)

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
        prompts = self._get_structured_agent_prompts()
        base_actions = list(prompts["action_catalog"]["base_actions"])
        plan_actions = prompts["action_catalog"]["plan_actions"]
        selected_plan_actions = plan_actions["bound" if plan_bound else "unbound"]
        policy = get_tool_policy()
        filtered_base_actions: List[str] = []
        for line in base_actions:
            tool_name = self._extract_tool_name(line)
            if tool_name and not is_tool_allowed(tool_name, policy):
                continue
            filtered_base_actions.append(line)
        return "\n".join(filtered_base_actions + list(selected_plan_actions))

    def _compose_guidelines(self, plan_bound: bool) -> str:
        prompts = self._get_structured_agent_prompts()
        common_rules = list(prompts["guidelines"]["common_rules"])
        scenario_rules = prompts["guidelines"]["scenario_rules"]
        selected_rules = scenario_rules["bound" if plan_bound else "unbound"]
        all_rules = common_rules + list(selected_rules)
        return "\n".join(
            f"{idx}. {rule}" for idx, rule in enumerate(all_rules, start=1)
        )

    @staticmethod
    def _get_structured_agent_prompts() -> Dict[str, Any]:
        prompts = prompt_manager.get_category("structured_agent")
        if not isinstance(prompts, dict):
            raise ValueError("structured_agent prompts must be a dictionary.")
        return prompts

    @staticmethod
    def _extract_tool_name(action_line: str) -> Optional[str]:
        match = re.search(r"-\s*tool_operation:\s*([^\s(]+)", action_line)
        if match:
            return match.group(1).strip()
        return None

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
                sequence=action.order if isinstance(action.order, int) else None,
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
    def _truncate_summary_text(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return str(value)

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
        # 不再在回复末尾追加 Action summary，因为前端已有状态标签和「查看过程」按钮
        # 保留方法签名以保持兼容性
            return reply

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
        # Fix incomplete Unicode escapes (e.g. "\u" not followed by 4 hex digits)
        # that some LLMs emit, which cause json.loads to fail with
        # "incomplete escape \u at position N".
        cleaned = re.sub(
            r'\\u(?![0-9a-fA-F]{4})',
            r'\\\\u',
            cleaned,
        )
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
        policy = get_tool_policy()
        if not is_tool_allowed(tool_name, policy):
            return AgentStep(
                action=action,
                success=False,
                message=f"Tool '{tool_name}' is not allowed by policy.",
                details={"error": "tool_not_allowed", "tool": tool_name},
            )

        params = dict(action.parameters or {})

        # 🔄 如果 LLM 指定了 target_task_id，优先使用它来更新任务状态
        target_task_id = params.pop("target_task_id", None)
        if target_task_id is not None:
            try:
                self.extra_context["current_task_id"] = int(target_task_id)
            except (TypeError, ValueError):
                pass

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

        elif tool_name == "file_operations":
            operation = params.get("operation")
            if not isinstance(operation, str) or not operation.strip():
                return AgentStep(
                    action=action,
                    success=False,
                    message="file_operations requires a non-empty `operation` string.",
                    details={"error": "invalid_operation", "tool": tool_name},
                )
            operation = operation.strip()
            # Minimal validation for common operations.
            if operation in {"read", "list", "delete", "exists", "info"}:
                path = params.get("path")
                if not isinstance(path, str) or not path.strip():
                    return AgentStep(
                        action=action,
                        success=False,
                        message=f"file_operations {operation} requires a non-empty `path` string.",
                        details={"error": "missing_params", "tool": tool_name},
                    )
                clean_params = {"operation": operation, "path": path}
                if operation == "list":
                    pattern = params.get("pattern")
                    if isinstance(pattern, str) and pattern.strip():
                        clean_params["pattern"] = pattern
                params = clean_params
            elif operation in {"write"}:
                path = params.get("path")
                content = params.get("content")
                if not isinstance(path, str) or not path.strip():
                    return AgentStep(
                        action=action,
                        success=False,
                        message="file_operations write requires a non-empty `path` string.",
                        details={"error": "missing_params", "tool": tool_name},
                    )
                if content is None:
                    content = ""
                if not isinstance(content, str):
                    content = str(content)
                params = {"operation": operation, "path": path, "content": content}
            elif operation in {"copy", "move"}:
                path = params.get("path")
                dest = params.get("destination")
                if not isinstance(path, str) or not path.strip() or not isinstance(dest, str) or not dest.strip():
                    return AgentStep(
                        action=action,
                        success=False,
                        message=f"file_operations {operation} requires `path` and `destination`.",
                        details={"error": "missing_params", "tool": tool_name},
                    )
                params = {"operation": operation, "path": path, "destination": dest}
            else:
                return AgentStep(
                    action=action,
                    success=False,
                    message=f"file_operations does not support operation={operation!r}.",
                    details={"error": "invalid_operation", "tool": tool_name},
                )

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

        elif tool_name == "literature_pipeline":
            query = params.get("query")
            if not isinstance(query, str) or not query.strip():
                return AgentStep(
                    action=action,
                    success=False,
                    message="literature_pipeline requires a non-empty `query` string.",
                    details={"error": "missing_query", "tool": tool_name},
                )
            clean_params: Dict[str, Any] = {"query": query.strip()}
            max_results = params.get("max_results")
            if max_results is not None:
                try:
                    clean_params["max_results"] = int(max_results)
                except (TypeError, ValueError):
                    pass
            out_dir = params.get("out_dir")
            if isinstance(out_dir, str) and out_dir.strip():
                clean_params["out_dir"] = out_dir.strip()
            download_pdfs = params.get("download_pdfs")
            if isinstance(download_pdfs, bool):
                clean_params["download_pdfs"] = download_pdfs
            max_pdfs = params.get("max_pdfs")
            if max_pdfs is not None:
                try:
                    clean_params["max_pdfs"] = int(max_pdfs)
                except (TypeError, ValueError):
                    pass
            user_agent = params.get("user_agent")
            if isinstance(user_agent, str) and user_agent.strip():
                clean_params["user_agent"] = user_agent.strip()
            proxy = params.get("proxy")
            if isinstance(proxy, str) and proxy.strip():
                clean_params["proxy"] = proxy.strip()
            params = clean_params

        elif tool_name == "review_pack_writer":
            topic = params.get("topic")
            if not isinstance(topic, str) or not topic.strip():
                return AgentStep(
                    action=action,
                    success=False,
                    message="review_pack_writer requires a non-empty `topic` string.",
                    details={"error": "missing_topic", "tool": tool_name},
                )
            clean_params: Dict[str, Any] = {"topic": topic.strip()}
            query = params.get("query")
            if isinstance(query, str) and query.strip():
                clean_params["query"] = query.strip()
            out_dir = params.get("out_dir")
            if isinstance(out_dir, str) and out_dir.strip():
                clean_params["out_dir"] = out_dir.strip()
            for int_key in ("max_results", "max_pdfs", "max_revisions"):
                if int_key in params and params[int_key] is not None:
                    try:
                        clean_params[int_key] = int(params[int_key])
                    except (TypeError, ValueError):
                        pass
            if "evaluation_threshold" in params and params["evaluation_threshold"] is not None:
                try:
                    clean_params["evaluation_threshold"] = float(params["evaluation_threshold"])
                except (TypeError, ValueError):
                    pass
            for bool_key in ("download_pdfs", "keep_workspace"):
                if isinstance(params.get(bool_key), bool):
                    clean_params[bool_key] = params[bool_key]
            output_path = params.get("output_path")
            if isinstance(output_path, str) and output_path.strip():
                clean_params["output_path"] = output_path.strip()
            sections = params.get("sections")
            if isinstance(sections, list):
                clean_sections: List[str] = []
                for item in sections:
                    if isinstance(item, str) and item.strip():
                        clean_sections.append(item.strip())
                if clean_sections:
                    clean_params["sections"] = clean_sections
            task_value = params.get("task")
            if isinstance(task_value, str) and task_value.strip():
                clean_params["task"] = task_value.strip()
            for key in (
                "generation_model",
                "evaluation_model",
                "merge_model",
                "generation_provider",
                "evaluation_provider",
                "merge_provider",
                "user_agent",
                "proxy",
            ):
                val = params.get(key)
                if isinstance(val, str) and val.strip():
                    clean_params[key] = val.strip()
            params = clean_params

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

            task_node, context_error = self._resolve_claude_code_task_context()
            if context_error or task_node is None:
                context_messages = {
                    "missing_plan_binding": "claude_code execution requires a bound plan. Please create/bind a plan first.",
                    "missing_target_task": "claude_code execution requires a target atomic task context. Please select or run a task first.",
                    "invalid_target_task": "claude_code execution requires a valid numeric task id.",
                    "plan_tree_unavailable": "Unable to load the current plan tree. Please retry after refreshing plan state.",
                    "target_task_not_found": "The selected task was not found in the current plan.",
                    "target_task_not_atomic": "claude_code can only execute atomic tasks. Please decompose this task and execute a leaf task.",
                }
                return AgentStep(
                    action=action,
                    success=False,
                    message=context_messages.get(
                        context_error or "",
                        "claude_code execution requires a bound atomic task context.",
                    ),
                    details={
                        "error": context_error or "missing_task_context",
                        "tool": tool_name,
                        "requires_plan_binding": True,
                        "requires_atomic_task": True,
                    },
                )

            # 🔍 A-mem集成：查询历史执行经验
            original_task = task_value.strip()
            amem_hints = ""
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
                        amem_hints = self._summarize_amem_experiences_for_cc(
                            amem_experiences
                        )
                        logger.info(
                            f"[AMEM] Injected compact hints from {len(amem_experiences)} historical experiences"
                        )
            except Exception as amem_err:
                logger.warning(f"[AMEM] Failed to query experiences: {amem_err}")
                # 继续执行，不影响主流程

            # Optional: allowed_tools parameter
            allowed_tools = self._normalize_csv_arg(params.get("allowed_tools"))

            # Optional: add_dirs parameter
            add_dirs = self._normalize_csv_arg(params.get("add_dirs"))

            constrained_task = self._compose_claude_code_atomic_task_prompt(
                task_node=task_node,
                original_task=original_task,
                amem_hints=amem_hints,
            )

            # Build final params (严格原子任务执行约束)
            params = {
                "task": constrained_task,
            }
            if allowed_tools:
                params["allowed_tools"] = allowed_tools
            if add_dirs:
                params["add_dirs"] = add_dirs

            # 会话/任务上下文信息，便于 runtime 归档与溯源
            if self.session_id:
                params["session_id"] = self.session_id
            params["plan_id"] = task_node.plan_id
            params["task_id"] = task_node.id

            # 🚀 实时日志回调注入
            # 获取当前上下文中的 job_id (在 _execute_action 中已经 set_current_job)
            current_job_id = get_current_job()
            # 如果没有找到 (例如同步调用)，尝试使用 sync_job_id
            if not current_job_id:
                current_job_id, _ = self._resolve_job_meta()
            
            if current_job_id:
                async def log_stdout(line: str):
                    """将 stdout 实时写入 job logs"""
                    plan_decomposition_jobs.append_log(
                        current_job_id,
                        "stdout",
                        line,
                        {},
                    )

                async def log_stderr(line: str):
                    """将 stderr 实时写入 job logs"""
                    plan_decomposition_jobs.append_log(
                        current_job_id,
                        "stderr",
                        line,
                        {},
                    )

                params["on_stdout"] = log_stdout
                params["on_stderr"] = log_stderr

        elif tool_name == "document_reader":
            operation = params.get("operation")
            file_path = params.get("file_path")
            
            if not operation or not file_path:
                return AgentStep(
                    action=action,
                    success=False,
                    message="document_reader requires `operation` and `file_path`.",
                    details={"error": "missing_params", "tool": tool_name},
                )
            
            # 验证操作类型
            if operation not in [
                "read_pdf",
                "read_image",
                "read_text",
                "read_any",
                "read_file",
                "auto",
            ]:
                return AgentStep(
                    action=action,
                    success=False,
                    message=f"Unsupported operation: {operation}",
                    details={"error": "invalid_operation", "tool": tool_name},
                )

            params = {
                "operation": operation,
                "file_path": file_path,
                "use_ocr": params.get("use_ocr", False),
            }

        elif tool_name == "vision_reader":
            operation = params.get("operation")
            image_path = params.get("image_path")

            if not operation or not image_path:
                return AgentStep(
                    action=action,
                    success=False,
                    message="vision_reader requires `operation` and `image_path`.",
                    details={"error": "missing_params", "tool": tool_name},
                )

            page_number = params.get("page_number")
            region = params.get("region")
            question = params.get("question")
            language = params.get("language")

            clean_params: Dict[str, Any] = {
                "operation": operation,
                "image_path": image_path,
            }
            if isinstance(page_number, int):
                clean_params["page_number"] = page_number
            if isinstance(region, dict):
                clean_params["region"] = region
            if isinstance(question, str):
                clean_params["question"] = question
            if isinstance(language, str):
                clean_params["language"] = language

            params = clean_params

        elif tool_name == "paper_replication":
            # Paper replication ExperimentCard loader
            exp_id = params.get("experiment_id")
            if exp_id is None:
                exp_id = "experiment_1"
            elif not isinstance(exp_id, str):
                try:
                    exp_id = str(exp_id)
                except Exception:
                    exp_id = "experiment_1"

            params = {"experiment_id": exp_id}

        elif tool_name == "generate_experiment_card":
            exp_id = params.get("experiment_id")
            if exp_id is not None and not isinstance(exp_id, str):
                exp_id = str(exp_id)
            pdf_path = params.get("pdf_path")
            if pdf_path is not None and not isinstance(pdf_path, str):
                pdf_path = str(pdf_path)
            code_root = params.get("code_root")
            if code_root is not None and not isinstance(code_root, str):
                code_root = str(code_root)
            notes_val = params.get("notes")
            if notes_val is not None and not isinstance(notes_val, str):
                notes_val = str(notes_val)
            overwrite_val = params.get("overwrite")
            overwrite = False
            if isinstance(overwrite_val, bool):
                overwrite = overwrite_val
            elif isinstance(overwrite_val, str):
                overwrite = overwrite_val.strip().lower() in {"1", "true", "yes", "y"}

            params = {
                "experiment_id": exp_id,
                "pdf_path": pdf_path,
                "code_root": code_root,
                "notes": notes_val,
                "overwrite": overwrite,
            }

        elif tool_name == "phagescope":
            if "result_kind" not in params:
                for alias in ("resultkind", "resultKind", "result_type", "resultType"):
                    if alias in params and params[alias] is not None:
                        params["result_kind"] = params[alias]
                        break
            if "taskid" not in params:
                for alias in ("task_id", "taskId"):
                    if alias in params and params[alias] is not None:
                        params["taskid"] = params[alias]
                        break
            if "phageid" not in params:
                for alias in ("phage_id", "phageId"):
                    if alias in params and params[alias] is not None:
                        params["phageid"] = params[alias]
                        break
            if "phageids" not in params:
                for alias in ("phage_ids", "phageIds"):
                    if alias in params and params[alias] is not None:
                        params["phageids"] = params[alias]
                        break

            # Compat aliases used by some prompts/tool wrappers.
            sequence_ids_value = None
            for alias in ("sequence_ids", "sequenceIds", "sequence_id", "sequenceId", "idlist"):
                if alias in params and params[alias] is not None:
                    sequence_ids_value = params[alias]
                    break
            if sequence_ids_value is not None and not params.get("phageid") and not params.get("phageids"):
                seq_items: List[str] = []
                if isinstance(sequence_ids_value, (list, tuple, set)):
                    seq_items = [str(v).strip() for v in sequence_ids_value if str(v).strip()]
                elif isinstance(sequence_ids_value, str):
                    raw = sequence_ids_value.strip()
                    if raw:
                        parsed = None
                        if raw.startswith("["):
                            try:
                                parsed = json.loads(raw.replace("'", '"'))
                            except Exception:
                                parsed = None
                        if isinstance(parsed, list):
                            seq_items = [str(v).strip() for v in parsed if str(v).strip()]
                        else:
                            normalized = raw.replace(",", ";").replace("\n", ";")
                            seq_items = [chunk.strip() for chunk in normalized.split(";") if chunk.strip()]
                else:
                    text = str(sequence_ids_value).strip()
                    if text:
                        seq_items = [text]
                if seq_items:
                    params["phageid"] = seq_items[0] if len(seq_items) == 1 else json.dumps(seq_items, ensure_ascii=False)
                    params["phageids"] = ";".join(seq_items)

            action_value = params.get("action")
            if not isinstance(action_value, str) or not action_value.strip():
                return AgentStep(
                    action=action,
                    success=False,
                    message="phagescope requires a non-empty `action` string.",
                    details={"error": "missing_action", "tool": tool_name},
                )

            clean_params: Dict[str, Any] = {
                "action": action_value.strip(),
            }
            for key in (
                "base_url",
                "token",
                "timeout",
                "phageid",
                "phageids",
                "sequence_ids",
                "inputtype",
                "analysistype",
                "userid",
                "modulelist",
                "rundemo",
                "taskid",
                "modulename",
                "result_kind",
                "module",
                "page",
                "pagesize",
                "seq_type",
                "download_path",
                "save_path",
                "preview_bytes",
                "wait",
                "poll_interval",
                "poll_timeout",
                "sequence",
                "file_path",
            ):
                if key in params and params[key] is not None:
                    clean_params[key] = params[key]

            for int_key in ("page", "pagesize", "preview_bytes"):
                if int_key in clean_params:
                    try:
                        clean_params[int_key] = int(clean_params[int_key])
                    except (TypeError, ValueError):
                        clean_params.pop(int_key, None)

            if "timeout" in clean_params:
                try:
                    clean_params["timeout"] = float(clean_params["timeout"])
                except (TypeError, ValueError):
                    clean_params.pop("timeout", None)

            for float_key in ("poll_interval", "poll_timeout"):
                if float_key in clean_params:
                    try:
                        clean_params[float_key] = float(clean_params[float_key])
                    except (TypeError, ValueError):
                        clean_params.pop(float_key, None)

            if "wait" in clean_params and not isinstance(clean_params.get("wait"), bool):
                wait_value = str(clean_params.get("wait", "")).strip().lower()
                clean_params["wait"] = wait_value in {"1", "true", "yes", "y", "on"}

            if isinstance(clean_params.get("rundemo"), bool):
                clean_params["rundemo"] = "true" if clean_params["rundemo"] else "false"

            action_value = clean_params.get("action")
            if action_value in {"result", "quality", "task_detail", "save_all"} and "taskid" in clean_params:
                try:
                    int(str(clean_params.get("taskid")).strip())
                except (TypeError, ValueError):
                    clean_params.pop("taskid", None)
            if (
                action_value in {"result", "quality", "task_detail", "save_all"}
                and not clean_params.get("taskid")
                and self.session_id
            ):
                cached_taskid = _lookup_phagescope_task_memory(
                    self.session_id,
                    userid=clean_params.get("userid"),
                    phageid=clean_params.get("phageid"),
                    modulelist=clean_params.get("modulelist"),
                )
                if cached_taskid:
                    clean_params["taskid"] = cached_taskid

            if action_value == "quality" or (
                action_value == "result"
                and str(clean_params.get("result_kind") or "").strip().lower()
                == "quality"
            ):
                clean_params.setdefault("wait", True)
                clean_params.setdefault("poll_interval", 2.0)
                clean_params.setdefault("poll_timeout", 120.0)

            params = clean_params

        elif tool_name == "manuscript_writer":
            task_value = params.get("task")
            output_path = params.get("output_path")
            if not isinstance(task_value, str) or not task_value.strip():
                return AgentStep(
                    action=action,
                    success=False,
                    message="manuscript_writer requires a non-empty `task` string.",
                    details={"error": "invalid_task", "tool": tool_name},
                )
            if not isinstance(output_path, str) or not output_path.strip():
                return AgentStep(
                    action=action,
                    success=False,
                    message="manuscript_writer requires a non-empty `output_path` string.",
                    details={"error": "missing_output_path", "tool": tool_name},
                )

            context_paths = params.get("context_paths") or []
            if isinstance(context_paths, str):
                context_paths = [context_paths]
            if not isinstance(context_paths, list):
                context_paths = []

            analysis_path = params.get("analysis_path")
            if analysis_path is not None and not isinstance(analysis_path, str):
                analysis_path = str(analysis_path)

            max_context_bytes = params.get("max_context_bytes")
            if max_context_bytes is not None:
                try:
                    max_context_bytes = int(max_context_bytes)
                except (TypeError, ValueError):
                    max_context_bytes = None

            params = {
                "task": task_value,
                "output_path": output_path,
                "context_paths": context_paths,
            }
            if analysis_path:
                params["analysis_path"] = analysis_path
            if max_context_bytes:
                params["max_context_bytes"] = max_context_bytes
            if params.get("context_paths") is None:
                params["context_paths"] = []

            sections = params.get("sections")
            if sections is None:
                sections = action.parameters.get("sections")
            if isinstance(sections, str):
                sections = [sections]
            if isinstance(sections, list):
                params["sections"] = sections

            max_revisions = action.parameters.get("max_revisions")
            if max_revisions is not None:
                params["max_revisions"] = max_revisions

            evaluation_threshold = action.parameters.get("evaluation_threshold")
            if evaluation_threshold is not None:
                params["evaluation_threshold"] = evaluation_threshold

            generation_model = action.parameters.get("generation_model")
            if generation_model is not None:
                params["generation_model"] = generation_model
            evaluation_model = action.parameters.get("evaluation_model")
            if evaluation_model is not None:
                params["evaluation_model"] = evaluation_model
            merge_model = action.parameters.get("merge_model")
            if merge_model is not None:
                params["merge_model"] = merge_model

            generation_provider = action.parameters.get("generation_provider")
            if generation_provider is not None:
                params["generation_provider"] = generation_provider
            evaluation_provider = action.parameters.get("evaluation_provider")
            if evaluation_provider is not None:
                params["evaluation_provider"] = evaluation_provider
            merge_provider = action.parameters.get("merge_provider")
            if merge_provider is not None:
                params["merge_provider"] = merge_provider

            if self.session_id:
                params["session_id"] = self.session_id

        else:
            return AgentStep(
                action=action,
                success=False,
                message=f"Tool {tool_name} is not supported yet.",
                details={"error": "unsupported_tool", "tool": tool_name},
            )

        try:
            # PhageScope: provide elegant progress during wait/poll (job_update -> stats.tool_progress)
            if tool_name == "phagescope":
                action_value = str(params.get("action") or "").strip().lower()
                wait_value = params.get("wait") is True
                taskid_value = params.get("taskid")
                if wait_value and action_value in {"result", "quality"} and taskid_value:
                    import time as _time
                    import json as _json

                    def _extract_task_status(detail_result: Any) -> str:
                        if not isinstance(detail_result, dict):
                            return "unknown"
                        payload = detail_result.get("data")
                        if isinstance(payload, dict):
                            results = payload.get("results")
                            if isinstance(results, dict):
                                for k in ("status", "task_status", "state", "taskstatus"):
                                    v = results.get(k)
                                    if isinstance(v, str) and v.strip():
                                        return v.strip()
                        return "unknown"

                    def _extract_task_detail_dict(detail_result: Any) -> Optional[Dict[str, Any]]:
                        if not isinstance(detail_result, dict):
                            return None
                        payload = detail_result.get("data")
                        if not isinstance(payload, dict):
                            return None
                        # phagescope_handler attaches parsed_task_detail when possible
                        parsed = payload.get("parsed_task_detail")
                        if isinstance(parsed, dict):
                            return parsed
                        # sometimes nested under results.task_detail
                        results = payload.get("results")
                        if isinstance(results, dict):
                            td = results.get("task_detail")
                            if isinstance(td, dict):
                                return td
                            if isinstance(td, str) and td.strip():
                                try:
                                    parsed_td = _json.loads(td)
                                    if isinstance(parsed_td, dict):
                                        return parsed_td
                                except Exception:
                                    return None
                        return None

                    def _module_status_upper(value: Any) -> Optional[str]:
                        if not isinstance(value, str):
                            return None
                        v = value.strip()
                        return v.upper() if v else None

                    poll_timeout = float(params.get("poll_timeout") or 120.0)
                    poll_interval = float(params.get("poll_interval") or 2.0)
                    start = _time.monotonic()

                    # Avoid the tool's internal polling; we do it here so we can stream progress
                    attempt_params = dict(params)
                    attempt_params["wait"] = False

                    raw_result = None
                    last_status = "queued"
                    while True:
                        elapsed = _time.monotonic() - start
                        denom = poll_timeout if poll_timeout > 0 else 1.0
                        time_percent = int(max(0.0, min(1.0, elapsed / denom)) * 100)

                        # best-effort task status
                        modules_payload: Optional[List[Dict[str, Any]]] = None
                        counts_payload: Optional[Dict[str, int]] = None
                        try:
                            detail = await execute_tool(
                                "phagescope",
                                action="task_detail",
                                taskid=str(taskid_value),
                                base_url=params.get("base_url"),
                                token=params.get("token"),
                                timeout=min(float(params.get("timeout") or 60.0), 40.0),
                            )
                            last_status = _extract_task_status(detail)
                            task_detail = _extract_task_detail_dict(detail)
                            if isinstance(task_detail, dict):
                                queue = task_detail.get("task_que")
                                if isinstance(queue, list) and queue:
                                    modules: List[Dict[str, Any]] = []
                                    done = 0
                                    total = 0
                                    for item in queue:
                                        if not isinstance(item, dict):
                                            continue
                                        name = item.get("module")
                                        if not isinstance(name, str) or not name.strip():
                                            continue
                                        status_raw = (
                                            item.get("module_satus")
                                            or item.get("module_status")
                                            or item.get("status")
                                        )
                                        status_upper = _module_status_upper(status_raw) or "UNKNOWN"
                                        is_done: Optional[bool] = None
                                        if status_upper in {"COMPLETED", "SUCCESS", "SUCCEEDED", "DONE", "FINISHED"}:
                                            is_done = True
                                        elif status_upper in {"FAILED", "ERROR"}:
                                            is_done = False
                                        modules.append(
                                            {
                                                "name": name.strip(),
                                                "status": str(status_raw) if status_raw is not None else status_upper,
                                                "done": is_done,
                                            }
                                        )
                                        total += 1
                                        if is_done is True:
                                            done += 1
                                    if total > 0:
                                        modules_payload = modules
                                        counts_payload = {"done": done, "total": total}
                        except Exception:
                            # keep last_status
                            pass

                        # Prefer module-based percent when available; otherwise fallback to time-based percent.
                        percent = time_percent
                        if counts_payload and counts_payload.get("total"):
                            percent = int(round((counts_payload["done"] / max(1, counts_payload["total"])) * 100))
                            percent = max(0, min(100, percent))

                        plan_decomposition_jobs.update_stats_from_context(
                            {
                                "tool_progress": {
                                    "tool": "phagescope",
                                    "taskid": str(taskid_value),
                                    "percent": percent,
                                    "status": last_status,
                                    "phase": "poll",
                                    **({"modules": modules_payload} if modules_payload is not None else {}),
                                    **({"counts": counts_payload} if counts_payload is not None else {}),
                                }
                            }
                        )

                        # try fetch result
                        raw_result = await execute_tool(tool_name, **attempt_params)
                        if isinstance(raw_result, dict) and raw_result.get("success") is True:
                            plan_decomposition_jobs.update_stats_from_context(
                                {
                                    "tool_progress": {
                                        "tool": "phagescope",
                                        "taskid": str(taskid_value),
                                        "percent": 100,
                                        "status": last_status or "Success",
                                        "phase": "done",
                                    }
                                }
                            )
                            break

                        upper = str(last_status or "").strip().upper()
                        if upper in {"FAILED", "ERROR"}:
                            break
                        if elapsed >= poll_timeout:
                            raw_result = {
                                "success": False,
                                "status_code": 408,
                                "action": action_value,
                                "taskid": str(taskid_value),
                                "error": f"Result not ready within {poll_timeout:.0f}s. Retry later with taskid={taskid_value}.",
                                "polling": {
                                    "waited": True,
                                    "poll_timeout": poll_timeout,
                                    "poll_interval": poll_interval,
                                },
                            }
                            break
                        await asyncio.sleep(max(0.2, poll_interval))
                else:
                    raw_result = await execute_tool(tool_name, **params)
            else:
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
        # For optional local file reads (e.g., one-shot phagescope download+analyze),
        # treat read failures as non-fatal and continue the chain.
        try:
            is_optional = (
                isinstance(action.metadata, dict) and bool(action.metadata.get("optional"))
            )
            if (
                tool_name == "file_operations"
                and is_optional
                and isinstance(params, dict)
                and params.get("operation") == "read"
                and isinstance(sanitized, dict)
                and sanitized.get("success") is False
            ):
                patched = dict(sanitized)
                patched["optional"] = True
                patched["optional_error"] = patched.get("error") or "read_failed"
                patched["success"] = True
                sanitized = patched
        except Exception:
            pass
        summary = self._summarize_tool_result(tool_name, sanitized)
        self._append_recent_tool_result(tool_name, summary, sanitized)

        storage_info = None
        if self.session_id:
            action_payload = {
                "kind": action.kind,
                "name": action.name,
                "order": action.order,
                "blocking": action.blocking,
                "parameters": self._drop_callables(params),
            }
            try:
                storage_info = store_tool_output(
                    session_id=self.session_id,
                    job_id=get_current_job(),
                    action=action_payload,
                    tool_name=tool_name,
                    raw_result=raw_result,
                    summary=summary,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Failed to store tool output for %s in session %s: %s",
                    tool_name,
                    self.session_id,
                    exc,
                )

        # Attach stored output paths back to agent/tool result (no manual copy needed)
        if storage_info is not None and self.session_id:
            try:
                from app.services.upload_storage import ensure_session_dir
                from pathlib import Path

                session_root = ensure_session_dir(self.session_id)

                def _abs(rel: Optional[str]) -> Optional[str]:
                    if not rel:
                        return None
                    try:
                        return str((session_root / Path(rel)).resolve())
                    except Exception:
                        return str(session_root / Path(rel))

                storage_payload: Dict[str, Any] = {
                    "session_id": self.session_id,
                    "job_id": get_current_job(),
                    "tool": tool_name,
                    "step_order": action.order,
                    "output_dir": _abs(getattr(storage_info, "output_dir", None)),
                    "result_path": _abs(getattr(storage_info, "result_path", None)),
                    "manifest_path": _abs(getattr(storage_info, "manifest_path", None)),
                    "preview_path": _abs(getattr(storage_info, "preview_path", None)),
                }
                # Also keep relative paths for portability (optional)
                storage_payload_rel: Dict[str, Any] = {
                    "output_dir": getattr(storage_info, "output_dir", None),
                    "result_path": getattr(storage_info, "result_path", None),
                    "manifest_path": getattr(storage_info, "manifest_path", None),
                    "preview_path": getattr(storage_info, "preview_path", None),
                }
                storage_payload["relative"] = storage_payload_rel

                if isinstance(raw_result, dict):
                    raw_result.setdefault("storage", storage_payload)
                if isinstance(sanitized, dict):
                    sanitized.setdefault("storage", storage_payload)

                # Persist latest output location for later retrieval
                def _updater(metadata: Dict[str, Any]) -> Dict[str, Any]:
                    metadata["phagescope_last_output"] = storage_payload
                    items = metadata.get("phagescope_recent_outputs")
                    if not isinstance(items, list):
                        items = []
                    # de-dup by result_path
                    rp = storage_payload.get("result_path")
                    items = [it for it in items if not (isinstance(it, dict) and it.get("result_path") == rp)]
                    items.insert(0, storage_payload)
                    metadata["phagescope_recent_outputs"] = items[:10]
                    return metadata

                if tool_name == "phagescope":
                    _update_session_metadata(self.session_id, _updater)
                    # Make it available to the current agent loop immediately
                    self.extra_context["phagescope_last_output"] = storage_payload
            except Exception as exc:  # pragma: no cover - best-effort
                logger.debug("Failed to attach phagescope storage paths: %s", exc)

        if tool_name == "phagescope" and self.session_id:
            action_value = params.get("action")
            if action_value == "submit" and sanitized.get("success"):
                try:
                    _record_phagescope_task_memory(self.session_id, params, sanitized)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug(
                        "Failed to record phagescope task memory for %s: %s",
                        self.session_id,
                        exc,
                    )

        success = sanitized.get("success", True)
        deliverable_report = None
        if self.session_id:
            publish_task_id: Optional[int] = None
            publish_task_name: Optional[str] = None
            publish_task_instruction: Optional[str] = None
            try:
                current_task_id = self.extra_context.get("current_task_id")
                if current_task_id is not None:
                    publish_task_id = int(current_task_id)
            except (TypeError, ValueError):
                publish_task_id = None

            if publish_task_id is not None and self.plan_session.plan_id is not None:
                try:
                    tree = self.plan_session.repo.get_plan_tree(self.plan_session.plan_id)
                    if tree.has_node(publish_task_id):
                        task_node = tree.get_node(publish_task_id)
                        publish_task_name = task_node.display_name()
                        publish_task_instruction = task_node.instruction
                except Exception as exc:  # pragma: no cover - best-effort
                    logger.debug(
                        "Unable to resolve task context for deliverable publish in session %s: %s",
                        self.session_id,
                        exc,
                    )

            try:
                publish_payload = self._drop_callables(raw_result)
                deliverable_report = get_deliverable_publisher().publish_from_tool_result(
                    session_id=self.session_id,
                    tool_name=tool_name,
                    raw_result=publish_payload,
                    summary=summary,
                    source={
                        "channel": "chat",
                        "action_kind": action.kind,
                        "action_name": action.name,
                        "step_order": action.order,
                    },
                    job_id=get_current_job(),
                    plan_id=self.plan_session.plan_id,
                    task_id=publish_task_id,
                    task_name=publish_task_name,
                    task_instruction=publish_task_instruction,
                    publish_status="final" if success is not False else "draft",
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Failed to publish deliverables for session %s tool %s: %s",
                    self.session_id,
                    tool_name,
                    exc,
                )

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

        # 🔄 任务状态同步：如果有关联的 current_task_id，更新任务状态
        current_task_id = self.extra_context.get("current_task_id")
        if current_task_id is not None and self.plan_session.plan_id is not None:
            try:
                new_status = "completed" if success else "failed"
                task_id_int = int(current_task_id)
                
                # 更新当前任务状态
                self.plan_session.repo.update_task(
                    self.plan_session.plan_id,
                    task_id_int,
                    status=new_status,
                    execution_result=summary or message,
                )
                logger.info(
                    "[TASK_SYNC] Updated task %s status to %s after tool %s execution",
                    current_task_id,
                    new_status,
                    tool_name,
                )
                
                # 🔄 级联更新：如果是 root 任务完成，也更新所有子任务
                if new_status == "completed":
                    cascade_result = f"Completed as part of parent task #{task_id_int}"
                    descendants_updated = self.plan_session.repo.cascade_update_descendants_status(
                        self.plan_session.plan_id,
                        task_id_int,
                        status=new_status,
                        execution_result=cascade_result,
                    )
                    if descendants_updated > 0:
                        logger.info(
                            "[TASK_SYNC] Cascade updated %d descendant tasks to %s",
                            descendants_updated,
                            new_status,
                        )
                
                self._dirty = True
            except Exception as sync_err:
                logger.warning(
                    "[TASK_SYNC] Failed to update task %s status: %s",
                    current_task_id,
                    sync_err,
                )

        return AgentStep(
            action=action,
            success=bool(success),
            message=message,
            details={
                "tool": tool_name,
                "parameters": self._drop_callables(params),
                "result": sanitized,
                "summary": summary,
                "storage": storage_info.__dict__ if storage_info else None,
                "deliverables": deliverable_report.to_dict() if deliverable_report else None,
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
            if metadata is None:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            # Ensure plan origin is recorded for later comparison (standard vs deepthink).
            metadata.setdefault("plan_origin", "standard")
            metadata.setdefault("created_by", "structured_agent")
            # First create an empty plan record
            new_tree = self.plan_session.repo.create_plan(
                title=title,
                owner=owner,
                description=description,
                metadata=metadata,
            )
            
            # Create a ROOT task first - enforce a single root node for the plan
            raw_tasks = params.get("tasks")
            root_node = self.plan_session.repo.create_task(
                new_tree.id,
                name=title,
                status="pending",
                instruction=description or f"Root task for plan: {title}",
                parent_id=None,  # ROOT has no parent
                metadata={"is_root": True, "task_type": "root"},
            )
            root_task_id: Optional[int] = root_node.id
            logger.info("Created ROOT task %s for plan %s", root_task_id, new_tree.id)
            
            # If the caller provided an explicit task list, materialize it immediately
            created_seed_tasks: List[Any] = []
            if isinstance(raw_tasks, list) and raw_tasks:
                for idx, t in enumerate(raw_tasks):
                    if not isinstance(t, dict):
                        continue
                    instr_raw = t.get("instruction") or t.get("description") or ""
                    try:
                        instruction = str(instr_raw).strip()
                    except Exception:
                        instruction = ""
                    name_raw = t.get("name") or t.get("title")
                    name: str
                    if isinstance(name_raw, str) and name_raw.strip():
                        name = name_raw.strip()
                    else:
                        # Derive a short step name from the instruction when no explicit name is given
                        base = instruction.strip()
                        if "。" in base:
                            base = base.split("。", 1)[0]
                        elif "." in base:
                            base = base.split(".", 1)[0]
                        elif "\n" in base:
                            base = base.split("\n", 1)[0]
                        base = base[:40] if base else f"Step {idx + 1}"
                        name = f"Step {idx + 1}: {base}" if base else f"Step {idx + 1}"

                    status_raw = t.get("status") or "pending"
                    if not isinstance(status_raw, str):
                        status = str(status_raw).strip() or "pending"
                    else:
                        status = status_raw.strip() or "pending"

                    parent_id_raw = t.get("parent_id")
                    # Default to ROOT task if no parent_id is specified
                    parent_id = root_task_id
                    if parent_id_raw is not None:
                        try:
                            parent_id = int(parent_id_raw)
                        except (TypeError, ValueError):
                            parent_id = root_task_id  # Fall back to ROOT if invalid
                    # Enforce single root: if resolved parent_id is None, force to root_task_id
                    if parent_id is None:
                        parent_id = root_task_id

                    deps_raw = t.get("dependencies")
                    deps: Optional[List[int]] = None
                    if isinstance(deps_raw, list):
                        tmp: List[int] = []
                        for d in deps_raw:
                            try:
                                tmp.append(int(d))
                            except (TypeError, ValueError):
                                continue
                        deps = tmp or None

                    meta_raw = t.get("metadata")
                    meta = meta_raw if isinstance(meta_raw, dict) else None

                    try:
                        node = self.plan_session.repo.create_task(
                            new_tree.id,
                            name=name,
                            status=status,
                            instruction=instruction or None,
                            parent_id=parent_id,
                            metadata=meta,
                            dependencies=deps,
                        )
                        created_seed_tasks.append(node)
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.warning(
                            "Failed to create seed task for plan %s: %s", new_tree.id, exc
                        )

            # Bind session to the new plan and refresh the in-memory tree so that
            # any seed tasks are immediately visible to the caller and UI.
            self.plan_session.bind(new_tree.id)
            self._refresh_plan_tree(force_reload=True)
            effective_tree = self.plan_tree or new_tree
            self.plan_tree = effective_tree
            self.extra_context["plan_id"] = effective_tree.id
            message = f'Created and bound new plan #{effective_tree.id} "{effective_tree.title}".'
            if created_seed_tasks:
                message += f" Seeded with {len(created_seed_tasks)} top-level task(s) from the proposed plan."
            details = {
                "plan_id": effective_tree.id,
                "title": effective_tree.title,
                "task_count": effective_tree.node_count(),
            }
            if created_seed_tasks:
                details["seed_tasks"] = [node.model_dump() for node in created_seed_tasks]
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
            # 构建会话上下文，传递给 Plan 执行器
            session_ctx = {
                "session_id": self.session_id,  # 用于工具调用
                "user_message": self._current_user_message if hasattr(self, "_current_user_message") else None,
                "chat_history": self.history,
                "recent_tool_results": self.extra_context.get("recent_tool_results", []),
            }
            exec_config = ExecutionConfig(session_context=session_ctx)
            summary = await asyncio.to_thread(self.plan_executor.execute_plan, tree.id, config=exec_config)
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
            success = failed_count == 0 and skipped_count == 0
            self._refresh_plan_tree(force_reload=True)
            return AgentStep(
                action=action, success=success, message=message, details=details
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
                                # Support shorthand "before"/"after":
                                # - If anchor_task_id is provided separately, treat as relative to it.
                                # - Otherwise, map to inserting as first/last child.
                                derived_position = keyword
                                if anchor_task_id is None:
                                    derived_position = (
                                        "first_child" if keyword == "before" else "last_child"
                                    )
                                if anchor_position is not None and anchor_position != derived_position:
                                    raise ValueError(
                                        "anchor_position does not match the pattern specified in position."
                                    )
                                anchor_position = derived_position
                            else:
                                candidate_id = self._coerce_int(parts[1].strip(), f"position {keyword}")
                                if anchor_task_id is not None and anchor_task_id != candidate_id:
                                    raise ValueError(
                                        "anchor_task_id does not match the task referenced in position."
                                    )
                                if anchor_position is not None and anchor_position != keyword:
                                    raise ValueError(
                                        "anchor_position does not match the pattern specified in position."
                                    )
                                anchor_task_id = candidate_id
                                anchor_position = keyword
                        elif keyword in {"first_child", "last_child"}:
                            if anchor_position is not None and anchor_position != keyword:
                                raise ValueError(
                                    "anchor_position does not match the pattern specified in position."
                                )
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
            # region agent log
            try:
                with open("/Users/apple/LLM/agent/.cursor/debug.log", "a", encoding="utf-8") as _dbg:
                    _dbg.write(
                        json.dumps(
                            {
                                "id": f"rerun_task_execute_{int(__import__('time').time()*1000)}",
                                "timestamp": int(__import__("time").time() * 1000),
                                "runId": "pre-fix-2",
                                "hypothesisId": "N1,N2,N5",
                                "location": "app/routers/chat_routes.py:_execute_action",
                                "message": "executing rerun_task action",
                                "data": {
                                    "task_id": task_id,
                                    "session_id": self.session_id,
                                    "action_metadata": action.metadata if isinstance(action.metadata, dict) else None,
                                    "current_user_message": str(self._current_user_message or "")[:200],
                                },
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # endregion
            # 构建会话上下文，传递给任务执行器
            session_ctx = {
                "session_id": self.session_id,  # 用于工具调用
                "user_message": self._current_user_message if hasattr(self, "_current_user_message") else None,
                "chat_history": self.history,
                "recent_tool_results": self.extra_context.get("recent_tool_results", []),
            }
            exec_config = ExecutionConfig(session_context=session_ctx)
            result = self.plan_executor.execute_task(tree.id, task_id, config=exec_config)
            status = (result.status or "").strip().lower()
            success = status in {"completed", "done", "success"}
            message = f"Task [{task_id}] execution status: {result.status}."
            if status == "skipped":
                message = f"Task [{task_id}] was skipped."
            elif status in {"failed", "error"}:
                message = f"Task [{task_id}] failed."
            details = result.to_dict()
            self._refresh_plan_tree(force_reload=True)
            return AgentStep(
                action=action, success=success, message=message, details=details
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
            # 构建会话上下文，传递给 Plan 分解器
            session_ctx = {
                "user_message": self._current_user_message if hasattr(self, "_current_user_message") else None,
                "chat_history": self.history,
                "recent_tool_results": self.extra_context.get("recent_tool_results", []),
            }
            if task_id_raw is None:
                result = self.plan_decomposer.run_plan(
                    tree.id,
                    max_depth=expand_depth,
                    node_budget=node_budget,
                    session_context=session_ctx,
                )
            else:
                task_id = self._coerce_int(task_id_raw, "task_id")
                result = self.plan_decomposer.decompose_node(
                    tree.id,
                    task_id,
                    expand_depth=expand_depth,
                    node_budget=node_budget,
                    allow_existing_children=allow_existing_children,
                    session_context=session_ctx,
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

    def _auto_decompose_plan(
        self,
        plan_id: int,
        *,
        wait_for_completion: bool = False,
        session_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
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
        if wait_for_completion:
            try:
                result = self.plan_decomposer.run_plan(
                    plan_id,
                    max_depth=settings.max_depth,
                    node_budget=settings.total_node_budget,
                    session_context=session_context,
                )
            except Exception as exc:  # pragma: no cover - defensive
                message = f"Automatic decomposition failed: {exc}"
                logger.exception(
                    "Auto decomposition failed for plan %s: %s", plan_id, exc
                )
                self._decomposition_errors.append(message)
                return None
            self._last_decomposition = result
            if result.created_tasks:
                self._dirty = True
            try:
                self._refresh_plan_tree(force_reload=True)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Failed to refresh plan tree after synchronous decomposition: %s",
                    exc,
                )
                self._decomposition_errors.append(
                    f"Failed to refresh plan after decomposition: {exc}"
                )
            note = "Automatic decomposition completed synchronously."
            if note not in self._decomposition_notes:
                self._decomposition_notes.append(note)
            return {"result": result}
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
        if len(raw) == 0:
            # Explicit empty list means "clear dependencies".
            return []
        deps: List[int] = []
        for item in raw:
            try:
                deps.append(int(item))
            except (TypeError, ValueError):
                continue
        # If user/LLM provided a non-empty list but all items were invalid,
        # treat as "no change" rather than clearing.
        if not deps:
            return None
        return deps

    def _sanitize_tool_result(self, tool_name: str, raw_result: Any) -> Dict[str, Any]:
        if tool_name == "phagescope" and isinstance(raw_result, dict):
            sanitized: Dict[str, Any] = {
                "tool": tool_name,
                "action": raw_result.get("action"),
                "status_code": raw_result.get("status_code"),
                "success": raw_result.get("success", False),
            }
            if "error" in raw_result:
                sanitized["error"] = raw_result.get("error")
            # Keep key local artifact paths for save_all so follow-up file reads can work.
            if str(raw_result.get("action") or "").strip().lower() == "save_all":
                for key in (
                    "taskid",
                    "output_directory",
                    "output_directory_rel",
                    "summary_file",
                    "summary_file_rel",
                    "files_saved",
                    "errors",
                    "missing_artifacts",
                    "warnings",
                    "partial",
                ):
                    if key in raw_result:
                        sanitized[key] = raw_result.get(key)
            payload = raw_result.get("data")
            if isinstance(payload, dict):
                trimmed: Dict[str, Any] = {}
                for key in ("status", "message", "code", "results", "data", "error"):
                    if key in payload:
                        trimmed[key] = payload[key]
                if "results" in trimmed and isinstance(trimmed["results"], list):
                    trimmed["results"] = trimmed["results"][:3]
                sanitized["data"] = trimmed
            return sanitized

        if tool_name == "file_operations" and isinstance(raw_result, dict):
            # Some operations (e.g. exists) historically didn't include "success".
            inferred_success = raw_result.get("success")
            if inferred_success is None:
                inferred_success = False if raw_result.get("error") else True
            sanitized: Dict[str, Any] = {
                "tool": tool_name,
                "operation": raw_result.get("operation"),
                "path": raw_result.get("path"),
                "success": bool(inferred_success),
            }
            if "error" in raw_result:
                sanitized["error"] = raw_result.get("error")
            # Keep read content for downstream synthesis (already bounded by tool limits).
            content = raw_result.get("content")
            if isinstance(content, str):
                # Extra guardrail: cap to 80k chars to avoid bloating chat logs.
                sanitized["content"] = content[:80_000]
            for key in ("size", "file_size", "lines_read", "encoding", "truncated", "truncated_message", "count", "items", "exists", "type"):
                if key in raw_result:
                    sanitized[key] = raw_result.get(key)
            return sanitized

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
                "text",
                "page_count",
                "file_path",
                "file_type",
                "base64",
                "width",
                "height",
                "operation",
                "image_path",
                "page_number",
                "language",
                "experiment_id",
                "card",
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
            items = list(raw_result)
            return {"tool": tool_name, "items": items, "success": True}

        text = str(raw_result)
        return {"tool": tool_name, "text": text, "success": True}

    @staticmethod
    def _drop_callables(value: Any) -> Any:
        if callable(value):
            return None
        if isinstance(value, dict):
            cleaned: Dict[str, Any] = {}
            for key, item in value.items():
                if callable(item):
                    continue
                cleaned[key] = StructuredChatAgent._drop_callables(item)
            return cleaned
        if isinstance(value, list):
            cleaned_list: List[Any] = []
            for item in value:
                if callable(item):
                    continue
                cleaned_list.append(StructuredChatAgent._drop_callables(item))
            return cleaned_list
        if isinstance(value, tuple):
            cleaned_tuple: List[Any] = []
            for item in value:
                if callable(item):
                    continue
                cleaned_tuple.append(StructuredChatAgent._drop_callables(item))
            return cleaned_tuple
        return value

    @staticmethod
    def _summarize_tool_result(tool_name: str, result: Dict[str, Any]) -> str:
        if tool_name == "phagescope":
            action = result.get("action") or "phagescope"
            # Special handling: save_all may return 207 (partial) but still be usable.
            if str(action).strip().lower() == "save_all":
                status_code = result.get("status_code")
                out_dir = result.get("output_directory") or result.get("output_directory_rel")
                missing = result.get("missing_artifacts") or []
                errors = result.get("errors") or []
                if result.get("success") is True:
                    if status_code == 207:
                        miss_text = ""
                        if isinstance(missing, list) and missing:
                            miss_text = f"; missing: {', '.join(str(x) for x in missing[:6])}{'...' if len(missing) > 6 else ''}"
                        elif isinstance(errors, list) and errors:
                            miss_text = f"; partial errors: {', '.join(str(x) for x in errors[:2])}{'...' if len(errors) > 2 else ''}"
                        return f"PhageScope save_all completed (partial): saved to {out_dir}{miss_text}"
                    return f"PhageScope save_all completed: saved to {out_dir}"
                error = result.get("error") or "Execution failed"
                # If partial output exists, surface it even on failure.
                if status_code == 207 and out_dir:
                    return f"PhageScope save_all completed (partial): saved to {out_dir}; but marked failed: {error}"
                return f"PhageScope save_all failed: {error}"

            if result.get("success") is False:
                error = result.get("error") or "Execution failed"
                return f"PhageScope {action} failed: {error}"

            action_lower = str(action).strip().lower()
            if action_lower == "submit":
                taskid = _extract_taskid_from_result(result)
                if taskid:
                    return f"PhageScope submit succeeded: taskid={taskid}; running in background."
                return "PhageScope submit succeeded; task is running in background."

            if action_lower == "task_detail":
                snapshot = _extract_phagescope_task_snapshot(result)
                status = (
                    str(snapshot.get("remote_status") or "").strip()
                    or str(snapshot.get("task_status") or "").strip()
                    or "unknown"
                )
                counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
                done = counts.get("done") if isinstance(counts.get("done"), int) else None
                total = counts.get("total") if isinstance(counts.get("total"), int) else None
                if isinstance(done, int) and isinstance(total, int) and total > 0:
                    return f"PhageScope task_detail succeeded: status={status}, progress={done}/{total}."
                return f"PhageScope task_detail succeeded: status={status}."

            payload = result.get("data")
            message = None
            if isinstance(payload, dict):
                message = payload.get("message") or payload.get("status")
            if message:
                return f"PhageScope {action} succeeded: {message}"
            return f"PhageScope {action} succeeded."

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
                return f"Claude Code execution{file_info} succeeded. Output: {snippet}"
            
            return f"Claude Code execution{file_info} succeeded."

        if tool_name == "manuscript_writer":
            if result.get("success") is False:
                error = result.get("error") or "Manuscript writing failed"
                return f"Manuscript writer failed: {error}"
            output_path = result.get("output_path") or ""
            analysis_path = result.get("analysis_path") or ""
            if output_path and analysis_path:
                return (
                    "Manuscript writer succeeded. Draft: "
                    f"{output_path}; analysis memo: {analysis_path}."
                )
            if output_path:
                return f"Manuscript writer succeeded. Draft: {output_path}."
            return "Manuscript writer succeeded."
        
        if tool_name == "paper_replication":
            if result.get("success") is False:
                error = result.get("error") or "Paper replication tool failed"
                return f"Paper replication tool failed: {error}"

            exp_id = result.get("experiment_id") or "unknown_experiment"
            card = result.get("card") or {}
            paper = {}
            if isinstance(card, dict):
                paper = card.get("paper") or {}
            title = ""
            if isinstance(paper, dict):
                title = paper.get("title") or ""
            if title:
                return f"Loaded replication spec for {exp_id} (paper: {title})."
            return f"Loaded replication spec for {exp_id}."

        if tool_name == "vision_reader":
            if result.get("success") is False:
                error = result.get("error") or "Vision reader execution failed"
                return f"Vision reader failed: {error}"

            op = result.get("operation") or "vision task"
            text = result.get("text") or ""
            if isinstance(text, str) and text.strip():
                snippet = text.strip()
                return f"Vision reader ({op}) succeeded. Content preview: {snippet}"

            return "Vision reader succeeded, but no textual content was extracted."

        if tool_name == "document_reader":
            if result.get("success") is False:
                error = result.get("error") or "Document reading failed"
                return f"Document reader failed: {error}"
            
            text = result.get("text") or ""
            page_count = result.get("page_count")
            
            if text.strip():
                page_info = f" ({page_count} pages)" if page_count else ""
                return f"Document reader{page_info} succeeded. Content preview: {text.strip()}"
            
            return "Document reader succeeded, but no text content was extracted."
        
        return f"{tool_name} finished execution."

    def _append_recent_tool_result(
        self, tool_name: str, summary: str, sanitized: Dict[str, Any]
    ) -> None:
        """Append tool result to context with tiered compression based on size."""
        history = self.extra_context.setdefault("recent_tool_results", [])
        if not isinstance(history, list):
            history = []
            self.extra_context["recent_tool_results"] = history
        
        # 分级压缩策略
        # 将结果序列化为字符串来计算大小
        try:
            result_str = json.dumps(sanitized, ensure_ascii=False, default=str)
        except Exception:
            result_str = str(sanitized)
        
        result_size = len(result_str)
        
        # 定义阈值
        SMALL_THRESHOLD = 2000    # 2000字符以下：完整保留
        MEDIUM_THRESHOLD = 8000   # 8000字符以下：截断保留
        # 超过8000：只保留摘要
        
        if result_size <= SMALL_THRESHOLD:
            # 小结果：完整保留
            compressed_result = sanitized
            compression_level = "full"
        elif result_size <= MEDIUM_THRESHOLD:
            # 中等结果：保留结构但截断长文本字段
            compressed_result = self._truncate_large_fields(sanitized, max_field_length=1000)
            compression_level = "truncated"
        else:
            # 大结果：只保留摘要和关键元数据
            compressed_result = {
                "_compressed": True,
                "_original_size": result_size,
                "success": sanitized.get("success"),
                "summary": sanitized.get("summary") or summary,
                "error": sanitized.get("error"),
            }
            # 保留一些常用的小字段
            for key in ["file_path", "file_name", "total", "count", "status"]:
                if key in sanitized and sanitized[key] is not None:
                    val = sanitized[key]
                    if isinstance(val, str) and len(val) < 200:
                        compressed_result[key] = val
                    elif isinstance(val, (int, float, bool)):
                        compressed_result[key] = val
            compression_level = "summary_only"
        
        entry = {
            "tool": tool_name,
            "summary": summary,
            "result": compressed_result,
            "_compression": compression_level,
            "_original_size": result_size,
        }
        history.append(entry)
        
        # 增加保留数量到10个
        max_items = 10
        if len(history) > max_items:
            del history[:-max_items]
    
    def _truncate_large_fields(
        self, data: Any, max_field_length: int = 1000, current_depth: int = 0
    ) -> Any:
        """递归截断大文本字段，保留结构"""
        if current_depth > 5:  # 防止过深递归
            return "...[nested data truncated]"
        
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                result[key] = self._truncate_large_fields(value, max_field_length, current_depth + 1)
            return result
        elif isinstance(data, list):
            if len(data) > 10:
                # 列表过长，只保留前5个和后2个
                truncated = data[:5] + [f"...[{len(data) - 7} items omitted]"] + data[-2:]
                return [self._truncate_large_fields(item, max_field_length, current_depth + 1) for item in truncated]
            return [self._truncate_large_fields(item, max_field_length, current_depth + 1) for item in data]
        elif isinstance(data, str):
            if len(data) > max_field_length:
                return data[:max_field_length] + f"...[truncated, {len(data)} chars total]"
            return data
        else:
            return data
