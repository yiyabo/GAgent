"""
Structured response schema and helpers for LLM conversations.

Defines Pydantic models describing the contract between the LLM and backend.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, PositiveInt

logger = logging.getLogger(__name__)


ActionKind = Literal[
    "plan_operation",
    "task_operation",
    "context_request",
    "system_operation",
    "tool_operation",
]


class RetryPolicy(BaseModel):
    """Retry/backoff configuration for an action."""

    max_retries: int = Field(default=0, ge=0)
    backoff_sec: float = Field(default=0.0, ge=0.0)


class LLMAction(BaseModel):
    """Single action description returned by the LLM."""

    kind: ActionKind
    name: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    blocking: bool = True
    order: PositiveInt = 1
    retry_policy: Optional[RetryPolicy] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LLMReply(BaseModel):
    """Assistant reply payload."""

    message: str = Field(..., min_length=1)


class LLMStructuredResponse(BaseModel):
    """Complete structured response returned by the LLM."""

    llm_reply: LLMReply
    actions: List[LLMAction] = Field(default_factory=list)

    def sorted_actions(self) -> List[LLMAction]:
        """Return actions ordered by their 'order' field."""
        return sorted(self.actions, key=lambda action: action.order)


def schema_as_json(indent: int = 2) -> str:
    """Return the JSON schema definition for LLMStructuredResponse."""
    schema_dict = LLMStructuredResponse.model_json_schema()
    return json.dumps(schema_dict, ensure_ascii=False, indent=indent)


def parse_structured_response(raw: str) -> Optional[LLMStructuredResponse]:
    """Parse leniently: direct JSON, then fence-stripped, then outermost brace slice.

    Returns None when no candidate validates. Lazy-imports strip_code_fence to
    avoid a circular import with the chat prompt builder.
    """
    from app.routers.chat.prompt_builder import strip_code_fence

    text = (raw or "").strip()
    if not text:
        return None
    candidates = [text]
    fenced = strip_code_fence(text)
    if fenced != text:
        candidates.append(fenced)
    start = fenced.find("{")
    end = fenced.rfind("}")
    if start != -1 and end > start:
        candidates.append(fenced[start : end + 1])
    for candidate in candidates:
        try:
            return LLMStructuredResponse.model_validate_json(candidate)
        except Exception:
            continue
    return None


def build_repair_prompt(raw: str, *, max_chars: int = 8000) -> str:
    """Prompt asking the model to convert its own non-JSON output to the schema."""
    excerpt = (raw or "")[:max_chars]
    return (
        "The assistant output below was required to be a single JSON object matching this JSON Schema:\n"
        f"{schema_as_json()}\n\n"
        "Convert it into that JSON object. Output ONLY the JSON object: no markdown fences, no "
        "commentary. Put the natural-language answer into llm_reply.message; use an empty actions "
        "list when no actions are needed.\n\n"
        f'Assistant output to convert:\n"""\n{excerpt}\n"""\n'
    )


def fallback_reply_response(raw: str) -> LLMStructuredResponse:
    """Wrap unparseable model output as a plain reply instead of failing the turn."""
    text = (raw or "").strip()
    if text.startswith("```"):
        try:
            from app.routers.chat.prompt_builder import strip_code_fence

            text = strip_code_fence(text)
        except Exception:
            pass
    if len(text) > 20000:
        text = text[:20000] + "…"
    if not text:
        text = "(The model returned no usable content.)"
    return LLMStructuredResponse(llm_reply=LLMReply(message=text), actions=[])
