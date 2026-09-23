"""Single source of truth for the chat-run SSE event vocabulary.

Every event persisted via ``append_chat_run_event`` / fanned out to SSE
clients uses one of the 13 registered types below. The discriminated union
is validated at the persistence choke point: production is fail-open
(unknown/invalid events are logged and still persisted so chat never
breaks), while ``CHAT_RUN_EVENT_SCHEMA_STRICT=1`` turns violations into
``ValueError`` for tests and development.

The JSON Schema export in ``scripts/export_chat_run_event_schema.py`` keeps
the frontend contract (``web-ui/src/types/chatRunEvents.*``) honest.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from typing_extensions import Annotated, Literal

logger = logging.getLogger(__name__)


class _EventBase(BaseModel):
    """Contract = what the frontend already tolerates: only core fields are
    required, everything else may evolve freely (extra keys allowed)."""

    model_config = ConfigDict(extra="allow")


class StartEvent(_EventBase):
    type: Literal["start"]


class DeltaEvent(_EventBase):
    type: Literal["delta"]
    content: str


class FinalEvent(_EventBase):
    type: Literal["final"]
    payload: Dict[str, Any]


class JobUpdateEvent(_EventBase):
    type: Literal["job_update"]
    payload: Dict[str, Any]


class ErrorEvent(_EventBase):
    type: Literal["error"]
    message: Optional[str] = None
    error_type: Optional[str] = None


class ThinkingStepEvent(_EventBase):
    type: Literal["thinking_step"]
    step: Dict[str, Any]


class ThinkingDeltaEvent(_EventBase):
    type: Literal["thinking_delta"]
    iteration: int
    delta: str


class ReasoningDeltaEvent(_EventBase):
    type: Literal["reasoning_delta"]
    iteration: int
    delta: str


class ProgressStatusEvent(_EventBase):
    type: Literal["progress_status"]
    phase: Optional[str] = None
    label: Optional[str] = None
    details: Optional[str] = None
    iteration: Optional[int] = None
    tool: Optional[str] = None
    status: Optional[str] = None


class ControlAckEvent(_EventBase):
    type: Literal["control_ack"]
    job_id: Optional[str] = None
    available: Optional[bool] = None
    paused: Optional[bool] = None
    action: Optional[str] = None


class ToolOutputEvent(_EventBase):
    type: Literal["tool_output"]
    tool: Optional[str] = None
    stream: Optional[str] = None
    content: Optional[str] = None
    iteration: Optional[int] = None


class ArtifactEvent(_EventBase):
    type: Literal["artifact"]
    path: Optional[str] = None
    extension: Optional[str] = None
    source_tool: Optional[str] = None
    iteration: Optional[int] = None
    display_name: Optional[str] = None
    mime_family: Optional[str] = None
    origin: Optional[str] = None
    tracking_id: Optional[str] = None


class SteerAckEvent(_EventBase):
    type: Literal["steer_ack"]
    message: Optional[str] = None
    iteration: Optional[int] = None


ChatRunEvent = Annotated[
    Union[
        StartEvent,
        DeltaEvent,
        FinalEvent,
        JobUpdateEvent,
        ErrorEvent,
        ThinkingStepEvent,
        ThinkingDeltaEvent,
        ReasoningDeltaEvent,
        ProgressStatusEvent,
        ControlAckEvent,
        ToolOutputEvent,
        ArtifactEvent,
        SteerAckEvent,
    ],
    Field(discriminator="type"),
]

CHAT_RUN_EVENT_TYPES = frozenset(
    {
        "start",
        "delta",
        "final",
        "job_update",
        "error",
        "thinking_step",
        "thinking_delta",
        "reasoning_delta",
        "progress_status",
        "control_ack",
        "tool_output",
        "artifact",
        "steer_ack",
    }
)

_EVENT_ADAPTER = TypeAdapter(ChatRunEvent)

# One canonical example per event type; consumed by the JSON export script
# and by contract tests on both ends (backend validation, frontend vitest).
CANONICAL_EVENT_EXAMPLES: Dict[str, Dict[str, Any]] = {
    "start": {"type": "start"},
    "delta": {"type": "delta", "content": "token"},
    "final": {"type": "final", "payload": {"response": "done", "metadata": {}}},
    "job_update": {"type": "job_update", "payload": {"job_id": "j1", "status": "running"}},
    "error": {"type": "error", "message": "boom", "error_type": "RuntimeError"},
    "thinking_step": {
        "type": "thinking_step",
        "step": {"iteration": 1, "thought": "...", "status": "thinking"},
    },
    "thinking_delta": {"type": "thinking_delta", "iteration": 1, "delta": "..."},
    "reasoning_delta": {"type": "reasoning_delta", "iteration": 1, "delta": "..."},
    "progress_status": {
        "type": "progress_status",
        "phase": "tool",
        "label": "code_executor",
        "details": None,
        "iteration": 2,
        "tool": "code_executor",
        "status": "running",
    },
    "control_ack": {
        "type": "control_ack",
        "job_id": "j1",
        "available": True,
        "paused": False,
        "action": "pause",
    },
    "tool_output": {
        "type": "tool_output",
        "tool": "code_executor",
        "stream": "stdout",
        "content": "...",
        "iteration": 2,
    },
    "artifact": {
        "type": "artifact",
        "path": "results/fig.png",
        "extension": ".png",
        "source_tool": "code_executor",
        "iteration": 3,
        "display_name": "fig.png",
        "mime_family": "image",
        "origin": "tool",
        "tracking_id": None,
    },
    "steer_ack": {"type": "steer_ack", "message": "noted", "iteration": 4},
}


def chat_run_event_schema() -> Dict[str, Any]:
    """JSON Schema for the full event union (used by the export script)."""
    return _EVENT_ADAPTER.json_schema()


def chat_run_event_schema_strict() -> bool:
    return os.getenv("CHAT_RUN_EVENT_SCHEMA_STRICT", "").strip().lower() in {"1", "true", "yes"}


def validate_chat_run_event(payload: Any) -> Optional[str]:
    """Validate one event payload against the union.

    Returns ``None`` when valid, otherwise a short human-readable reason.
    Never raises; callers decide whether to warn (production) or raise
    (strict mode).
    """
    if not isinstance(payload, dict):
        return f"event payload is {type(payload).__name__}, not an object"
    try:
        _EVENT_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ()))
        return f"{loc}: {first.get('msg', 'invalid')}" if loc else str(first.get("msg", "invalid"))
    return None


def check_chat_run_event(payload: Any, *, run_id: Optional[str] = None) -> None:
    """Choke-point hook: warn (or in strict mode raise) on contract drift."""
    reason = validate_chat_run_event(payload)
    if reason is None:
        return
    event_type = payload.get("type") if isinstance(payload, dict) else None
    message = f"chat_run event schema violation run={run_id or '-'} type={event_type!r}: {reason}"
    if chat_run_event_schema_strict():
        raise ValueError(message)
    logger.warning("[CHAT][EVENT-SCHEMA] %s", message)
