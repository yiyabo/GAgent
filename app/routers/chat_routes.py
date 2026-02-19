"""Backward-compatible chat router shim.

The active chat implementation lives in ``app.routers.chat``. This module is
kept so legacy imports such as ``from app.routers.chat_routes import
StructuredChatAgent`` continue to work.
"""

from __future__ import annotations

from app.services.session_title_service import SessionNotFoundError

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
from .chat.routes import (
    _execute_confirmed_actions,
    autotitle_chat_session,
    bulk_autotitle_chat_sessions,
    chat_message,
    chat_status,
    chat_stream,
    confirm_pending_action,
    delete_chat_session,
    get_chat_history,
    get_pending_confirmation_status,
    head_chat_session,
    list_chat_sessions,
    router,
    update_chat_session,
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
    "SessionNotFoundError",
    "StructuredChatAgent",
    "StructuredReplyStreamParser",
    "_build_action_status_payloads",
    "_build_brief_action_summary",
    "_collect_created_tasks_from_steps",
    "_execute_action_run",
    "_execute_confirmed_actions",
    "_generate_action_analysis",
    "_generate_tool_analysis",
    "_generate_tool_summary",
    "autotitle_chat_session",
    "bulk_autotitle_chat_sessions",
    "chat_message",
    "chat_status",
    "chat_stream",
    "confirm_pending_action",
    "delete_chat_session",
    "get_action_status",
    "get_chat_history",
    "get_pending_confirmation_status",
    "head_chat_session",
    "list_chat_sessions",
    "retry_action_run",
    "router",
    "update_chat_session",
]
