"""
Structured response schema and helpers for LLM conversations.

Defines Pydantic models describing the contract between the LLM and backend.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, PositiveInt


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
