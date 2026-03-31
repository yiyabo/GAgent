"""Chat router registration and endpoint mounting.

This module is the active router entrypoint for chat APIs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response

from app.config.executor_config import get_executor_settings
from app.repository.chat_action_runs import create_action_run
from app.repository.plan_storage import append_action_log_entry
from app.routers import register_router
from app.services.llm.llm_service import get_llm_service
from app.services.plans.decomposition_jobs import plan_decomposition_jobs
from app.services.plans.plan_session import PlanSession
from app.services.request_principal import get_request_owner_id
from app.services.session_title_service import SessionNotFoundError
from app.services.upload_storage import delete_session_storage

from .action_execution import _execute_action_run, get_action_status, retry_action_run
from .confirmation import (
    _cleanup_old_confirmations,
    _get_pending_confirmation,
    _remove_pending_confirmation,
)
from .models import (
    ActionStatusResponse,
    ChatRequest,
    ChatResponse,
    ChatSessionAutoTitleBulkRequest,
    ChatSessionAutoTitleBulkResponse,
    ChatSessionAutoTitleRequest,
    ChatSessionAutoTitleResult,
    ChatSessionSummary,
    ChatSessionsResponse,
    ChatSessionUpdateRequest,
    ChatStatusResponse,
    ConfirmActionRequest,
    ConfirmActionResponse,
)
from .services import (
    decomposer_settings,
    get_structured_chat_agent_cls,
    plan_decomposer_service,
    plan_executor_service,
    plan_repository,
    session_title_service,
)
from .session_helpers import (
    _convert_history_to_agent_format,
    _derive_conversation_id,
    _dump_metadata,
    _ensure_session_exists,
    _fetch_session_info,
    _get_session_current_task,
    _get_session_settings,
    _load_chat_history,
    _load_session_metadata_dict,
    _load_session_runtime_context,
    _lookup_plan_title,
    _normalize_base_model,
    _normalize_llm_provider,
    _normalize_search_provider,
    _resolve_plan_binding,
    _row_to_session_info,
    _save_assistant_response,
    _save_chat_message,
    _set_session_plan_id,
)
from .run_routes import mount_run_routes
from .stream import chat_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

register_router(
    namespace="chat",
    version="v1",
    path="/chat",
    router=router,
    tags=["chat"],
    description="Primary entry point for chat and plan management (structured LLM dialog)",
)

# Route mounting is declared at the bottom of this file after handler definitions.


def _select_owned_session_ids(
    conn,
    owner_id: str,
    *,
    session_ids: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> List[str]:
    if session_ids:
        normalized_ids = [str(session_id).strip() for session_id in session_ids if str(session_id or "").strip()]
        if not normalized_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_ids)
        rows = conn.execute(
            f"""
            SELECT id
            FROM chat_sessions
            WHERE owner_id = ? AND id IN ({placeholders})
            """,
            (owner_id, *normalized_ids),
        ).fetchall()
        allowed_ids = {str(row["id"]) for row in rows}
        return [session_id for session_id in normalized_ids if session_id in allowed_ids]

    max_items = limit if limit and limit > 0 else session_title_service.DEFAULT_LIMIT
    rows = conn.execute(
        """
        SELECT id
        FROM chat_sessions
        WHERE
            owner_id = ?
            AND (name IS NULL OR name = '' OR name_source IS NULL OR name_source = 'default')
            AND (is_user_named IS NULL OR is_user_named = 0)
        ORDER BY COALESCE(last_message_at, updated_at, created_at) DESC, id ASC
        LIMIT ?
        """,
        (owner_id, max_items),
    ).fetchall()
    return [str(row["id"]) for row in rows]


async def chat_message(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    raw_request: Request,
):
    """Main chat entry: respond with LLM actions first, then execute in the background."""
    owner_id = get_request_owner_id(raw_request)
    try:
        context = dict(request.context or {})
        incoming_plan_id = context.get("plan_id")
        if incoming_plan_id is not None and not isinstance(incoming_plan_id, int):
            try:
                incoming_plan_id = int(str(incoming_plan_id).strip())
            except (TypeError, ValueError):
                incoming_plan_id = None

        plan_id = _resolve_plan_binding(
            request.session_id,
            incoming_plan_id,
            owner_id=owner_id,
        )
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

        # Handle attachments: if context has attachments, append metadata to the message
        # and try auto-reading text-like content.
        message_to_send = request.message
        attachments = context.get("attachments", [])
        if attachments and isinstance(attachments, list):
            attachment_info = "\n\n📎 User-uploaded attachments:\n"
            # Define file types eligible for auto-read.
            AUTO_READ_TEXT_EXTS = {".txt", ".md", ".log", ".ini", ".cfg", ".yaml", ".yml"}
            AUTO_READ_PDF_EXT = ".pdf"
            MAX_AUTO_READ_SIZE = 200 * 1024  # 200KB

            for att in attachments:
                if isinstance(att, dict):
                    att_type = att.get("type", "file")
                    att_name = att.get("name", "Unknown file")
                    att_path = att.get("path", "")
                    att_extracted = att.get("extracted_path")
                    attachment_info += f"- {att_name} ({att_type}): {att_path}\n"
                    if att_extracted:
                        attachment_info += f"  extracted: {att_extracted}\n"

                    # Auto-read text-like content where possible.
                    if att_path:
                        try:
                            file_path = Path(att_path).expanduser().resolve()
                            if file_path.exists() and file_path.is_file():
                                file_size = file_path.stat().st_size
                                suffix = file_path.suffix.lower()

                                if file_size <= MAX_AUTO_READ_SIZE:
                                    if suffix in AUTO_READ_TEXT_EXTS:
                                        # Read text file directly.
                                        try:
                                            content = file_path.read_text(
                                                encoding="utf-8", errors="replace"
                                            )
                                            # Truncate overly long content.
                                            if len(content) > 10000:
                                                content = content[:10000] + f"\n... [content truncated, total {len(content)} characters]"
                                            attachment_info += f"\n📄 File content ({att_name}):\n```\n{content}\n```\n"
                                            logger.info("[CHAT][AUTO_READ] text file=%s size=%d", att_name, file_size)
                                        except Exception as read_err:
                                            logger.warning("[CHAT][AUTO_READ] Failed to read %s: %s", att_name, read_err)

                                    elif suffix == AUTO_READ_PDF_EXT:
                                        # Read PDF file.
                                        try:
                                            import pypdf
                                            with file_path.open("rb") as f:
                                                reader = pypdf.PdfReader(f)
                                                text_parts = []
                                                for i, page in enumerate(reader.pages[:20]):  # Cap at 20 pages.
                                                    try:
                                                        txt = page.extract_text() or ""
                                                        if txt.strip():
                                                            text_parts.append(f"--- Page {i+1} ---\n{txt}")
                                                    except Exception:
                                                        pass
                                                pdf_content = "\n\n".join(text_parts)
                                                if len(pdf_content) > 15000:
                                                    pdf_content = pdf_content[:15000] + f"\n... [PDF content truncated, total {len(pdf_content)} characters]"
                                                if pdf_content.strip():
                                                    attachment_info += f"\n📄 PDF content ({att_name}, {len(reader.pages)} pages):\n{pdf_content}\n"
                                                    logger.info("[CHAT][AUTO_READ] pdf file=%s pages=%d", att_name, len(reader.pages))
                                        except ImportError:
                                            logger.warning("[CHAT][AUTO_READ] pypdf not installed, skipping PDF auto-read")
                                        except Exception as pdf_err:
                                            logger.warning("[CHAT][AUTO_READ] Failed to read PDF %s: %s", att_name, pdf_err)
                        except Exception as e:
                            logger.warning("[CHAT][AUTO_READ] Error processing attachment %s: %s", att_name, e)

            # Add tool-usage hints based on attachment types.
            has_image = any(att.get("type") == "image" for att in attachments if isinstance(att, dict))
            has_document = any(att.get("type") in ["document", "application/pdf"] for att in attachments if isinstance(att, dict))
            has_data = any(
                Path(att.get("path", "")).suffix.lower() in {".csv", ".tsv", ".json", ".xlsx", ".xls"}
                for att in attachments if isinstance(att, dict) and att.get("path")
            )

            hints = []
            if has_image:
                hints.append("Use vision_reader for image understanding")
            if has_data:
                hints.append("Use code_executor for data-file analysis (.csv/.json/.xlsx)")
            if hints:
                attachment_info += f"\n💡 Hint: {'; '.join(hints)}."

            message_to_send = request.message + attachment_info
            logger.info("[CHAT][ATTACHMENTS] session=%s count=%d", request.session_id, len(attachments))

        if plan_session.plan_id is not None:
            context["plan_id"] = plan_session.plan_id
        else:
            context.pop("plan_id", None)

        converted_history = _convert_history_to_agent_format(request.history)

        session_settings: Dict[str, Any] = {}

        # 🔄 Task-state sync: prioritize task_id from frontend context.
        # This logic must run before session checks to ensure task_id is always handled.
        if "task_id" in context and "current_task_id" not in context:
            context["current_task_id"] = context["task_id"]
            logger.info(
                "[CHAT][TASK_SYNC] Using task_id from context: %s",
                context["current_task_id"],
            )

        if request.session_id:
            _save_chat_message(
                request.session_id,
                "user",
                request.message,
                owner_id=owner_id,
            )
            session_settings = _get_session_settings(
                request.session_id,
                owner_id=owner_id,
            )
            runtime_context = _load_session_runtime_context(
                request.session_id,
                owner_id=owner_id,
            )
            for key, value in runtime_context.items():
                context.setdefault(key, value)
            # If current_task_id is missing in context, try loading from session.
            if "current_task_id" not in context:
                current_task_id = _get_session_current_task(
                    request.session_id,
                    owner_id=owner_id,
                )
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
        context.setdefault("owner_id", owner_id)

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

        structured = await agent.get_structured_response(message_to_send)

        if not structured.actions:
            agent_result = await agent.execute_structured(structured)
            if request.session_id:
                _set_session_plan_id(
                    request.session_id,
                    agent_result.bound_plan_id,
                    owner_id=owner_id,
                )
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
                # Pull actual status from job payload instead of agent_result.success.
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
                actions=[step.action_payload for step in agent_result.steps],
                metadata=metadata_payload,
            )
            return _save_assistant_response(
                request.session_id,
                chat_response,
                owner_id=owner_id,
            )

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
                owner_id=owner_id,
                session_id=request.session_id,
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
                owner_id=owner_id,
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

        return _save_assistant_response(
            request.session_id,
            chat_response,
            owner_id=owner_id,
        )

    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Chat processing failed: %s", exc)
        error_message = "⚠️ Something went wrong while processing the request. Try again later or rephrase."
        fallback = ChatResponse(
            response=error_message,
            suggestions=["Retry", "Try another phrasing", "Contact the administrator"],
            actions=[],
            metadata={"error": True, "error_type": type(exc).__name__},
        )
        return _save_assistant_response(
            request.session_id,
            fallback,
            owner_id=owner_id,
        )




async def list_chat_sessions(
    raw_request: Request,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    active: Optional[bool] = None,
):
    """List existing chat sessions."""
    from ...database import get_db  # lazy import

    owner_id = get_request_owner_id(raw_request)
    try:
        with get_db() as conn:
            where_clauses: List[str] = ["s.owner_id = ?"]
            params: List[Any] = [owner_id]
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

async def update_chat_session(
    session_id: str,
    payload: ChatSessionUpdateRequest,
    raw_request: Request,
) -> ChatSessionSummary:
    """Update the core attributes of a chat session."""
    from ...database import get_db  # lazy import

    updates = payload.model_dump(exclude_unset=True)
    settings_update = updates.pop("settings", None)
    owner_id = get_request_owner_id(raw_request)

    if not updates and settings_update is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        with get_db() as conn:
            # Ensure session exists; auto-create if missing.
            _ensure_session_exists(session_id, conn, owner_id=owner_id)

            row = conn.execute(
                "SELECT id FROM chat_sessions WHERE id=? AND owner_id=?",
                (session_id, owner_id),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Session not found")

            set_clauses: List[str] = []
            params: List[Any] = []
            if settings_update is not None:
                metadata_dict = _load_session_metadata_dict(
                    conn,
                    session_id,
                    owner_id=owner_id,
                )
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
            sql = (
                f"UPDATE chat_sessions SET {', '.join(set_clauses)} "
                "WHERE id=? AND owner_id=?"
            )
            params.extend([session_id, owner_id])
            conn.execute(sql, params)
            conn.commit()

            session_info = _fetch_session_info(
                conn,
                session_id,
                owner_id=owner_id,
            )
            if not session_info:
                raise HTTPException(status_code=404, detail="Session not found")
            return ChatSessionSummary(**session_info)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to update chat session %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update session") from exc

async def autotitle_chat_session(
    session_id: str,
    payload: ChatSessionAutoTitleRequest,
    raw_request: Request,
) -> ChatSessionAutoTitleResult:
    """Auto-generate a session title from context."""
    from ...database import get_db  # lazy import

    owner_id = get_request_owner_id(raw_request)
    with get_db() as conn:
        session_info = _fetch_session_info(conn, session_id, owner_id=owner_id)
    if not session_info:
        raise HTTPException(status_code=404, detail="Session not found")

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

async def bulk_autotitle_chat_sessions(
    payload: ChatSessionAutoTitleBulkRequest,
    raw_request: Request,
) -> ChatSessionAutoTitleBulkResponse:
    """Bulk-generate session titles."""
    from ...database import get_db  # lazy import

    owner_id = get_request_owner_id(raw_request)
    try:
        with get_db() as conn:
            target_ids = _select_owned_session_ids(
                conn,
                owner_id,
                session_ids=payload.session_ids,
                limit=payload.limit,
            )
        results = session_title_service.bulk_generate(
            session_ids=target_ids,
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

async def head_chat_session(session_id: str, raw_request: Request) -> Response:
    """Check if a chat session exists (returns only headers, no body)."""
    from ...database import get_db  # lazy import

    owner_id = get_request_owner_id(raw_request)

    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id FROM chat_sessions WHERE id=? AND owner_id=?",
                (session_id, owner_id),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Session not found")
            return Response(status_code=200)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to check chat session %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail="Failed to check session") from exc

async def delete_chat_session(
    session_id: str,
    raw_request: Request,
    archive: bool = Query(False),
) -> Response:
    """Delete or archive a chat session."""
    from ...database import get_db  # lazy import

    owner_id = get_request_owner_id(raw_request)
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, is_active FROM chat_sessions WHERE id=? AND owner_id=?",
                (session_id, owner_id),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Session not found")

            if archive:
                conn.execute(
                    """
                    UPDATE chat_sessions
                    SET is_active=0,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND owner_id=?
                    """,
                    (session_id, owner_id),
                )
                logger.info("Archived chat session %s", session_id)
            else:
                conn.execute(
                    "DELETE FROM chat_sessions WHERE id=? AND owner_id=?",
                    (session_id, owner_id),
                )
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

async def confirm_pending_action(
    request: ConfirmActionRequest,
    background_tasks: BackgroundTasks,
) -> ConfirmActionResponse:
    """
    Confirm or cancel a pending action.

    When LLM-generated actions require user confirmation (e.g., create_plan),
    the system stores them and returns a confirmation_id. The client then uses
    this endpoint to confirm or cancel.
    """
    _cleanup_old_confirmations()  # Clean up expired confirmations.

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
            message="Operation cancelled.",
            confirmation_id=request.confirmation_id,
            executed=False,
        )

    # User confirmed; execute actions.
    logger.info(f"[CONFIRMATION] User confirmed: {request.confirmation_id}")

    try:
        session_id = pending["session_id"]
        actions = pending["actions"]
        plan_id = pending.get("plan_id")

        # Create background execution task.
        tracking_id = f"act_{uuid4().hex[:32]}"

        # Schedule background execution.
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
            message="Operation confirmed and now executing...",
            confirmation_id=request.confirmation_id,
            executed=True,
            result={"tracking_id": tracking_id},
        )
    except Exception as e:
        logger.error(f"[CONFIRMATION] Execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def get_pending_confirmation_status(confirmation_id: str) -> Dict[str, Any]:
    """Get status of a pending confirmation action."""
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
    """Execute confirmed actions in the background."""
    logger.info(f"[CONFIRMATION] Executing confirmed actions: {tracking_id}")

    try:
        plan_session = PlanSession(repo=plan_repository, plan_id=plan_id)
        try:
            plan_session.refresh()
        except ValueError:
            plan_session.detach()

        agent_cls = get_structured_chat_agent_cls()
        agent = agent_cls(
            mode="assistant",
            plan_session=plan_session,
            plan_decomposer=plan_decomposer_service,
            plan_executor=plan_executor_service,
            session_id=session_id,
            conversation_id=_derive_conversation_id(session_id),
            history=[],
            extra_context=extra_context or {},
        )

        # Execute each action.
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

async def get_chat_history(
    session_id: str,
    raw_request: Request,
    limit: int = 50,
    before_id: Optional[int] = Query(default=None, ge=1),
):
    """Fetch history for a specific session."""
    from ...database import get_db  # lazy import

    owner_id = get_request_owner_id(raw_request)
    try:
        with get_db() as conn:
            session_info = _fetch_session_info(conn, session_id, owner_id=owner_id)
        if not session_info:
            raise HTTPException(status_code=404, detail="Session not found")
        messages, has_more = _load_chat_history(
            session_id,
            limit,
            before_id,
            owner_id=owner_id,
        )
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
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to get chat history: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

# Session / message routes
router.add_api_route(
    "/sessions",
    list_chat_sessions,
    methods=["GET"],
    response_model=ChatSessionsResponse,
)
router.add_api_route(
    "/sessions/{session_id}",
    update_chat_session,
    methods=["PATCH"],
    response_model=ChatSessionSummary,
)
router.add_api_route(
    "/sessions/{session_id}/autotitle",
    autotitle_chat_session,
    methods=["POST"],
    response_model=ChatSessionAutoTitleResult,
)
router.add_api_route(
    "/sessions/autotitle/bulk",
    bulk_autotitle_chat_sessions,
    methods=["POST"],
    response_model=ChatSessionAutoTitleBulkResponse,
)
router.add_api_route(
    "/sessions/{session_id}",
    head_chat_session,
    methods=["HEAD"],
)
router.add_api_route(
    "/sessions/{session_id}",
    delete_chat_session,
    methods=["DELETE"],
    status_code=204,
)
router.add_api_route(
    "/confirm",
    confirm_pending_action,
    methods=["POST"],
    response_model=ConfirmActionResponse,
)
router.add_api_route(
    "/confirm/{confirmation_id}",
    get_pending_confirmation_status,
    methods=["GET"],
)
router.add_api_route(
    "/status",
    chat_status,
    methods=["GET"],
    response_model=ChatStatusResponse,
)
router.add_api_route(
    "/history/{session_id}",
    get_chat_history,
    methods=["GET"],
)
router.add_api_route(
    "/message",
    chat_message,
    methods=["POST"],
    response_model=ChatResponse,
)

# Stream route
router.add_api_route(
    "/stream",
    chat_stream,
    methods=["POST"],
)

mount_run_routes(router)

# Action routes
router.add_api_route(
    "/actions/{tracking_id}",
    get_action_status,
    methods=["GET"],
    response_model=ActionStatusResponse,
)
router.add_api_route(
    "/actions/{tracking_id}/retry",
    retry_action_run,
    methods=["POST"],
    response_model=ActionStatusResponse,
)
