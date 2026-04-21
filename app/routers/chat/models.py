"""Pydantic models and helpers shared across the chat router package."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.llm.structured_response import LLMAction


# ---------------------------------------------------------------------------
# Stream parser
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Chat models
# ---------------------------------------------------------------------------

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
    client_message_id: Optional[str] = None


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
        Literal["qwen3.6-plus", "qwen3.5-plus", "qwen3-max-2026-01-23", "qwen-turbo"]
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


# ---------------------------------------------------------------------------
# Confirmation models
# ---------------------------------------------------------------------------

class ConfirmActionRequest(BaseModel):
    """Confirmation action request."""
    confirmation_id: str
    confirmed: bool = True  # True = confirm execution, False = cancel.


class ConfirmActionResponse(BaseModel):
    """Confirmation action response."""
    success: bool
    message: str
    confirmation_id: str
    executed: bool = False
    result: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Agent models
# ---------------------------------------------------------------------------

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
            header = "/".join(label_parts) if label_parts else f"Step {idx + 1}"
            params = action.parameters or {}
            detail = (
                params.get("instruction")
                or params.get("name")
                or params.get("title")
                or step.message
            )
            lines.append(f"- {header}: {detail or 'completed'}")
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
                        lines.append(f"  - Subtask: {st_name}")
                    if st_instr:
                        lines.append(f"    · Note: {st_instr}")
        return "\n".join(lines) if lines else None
