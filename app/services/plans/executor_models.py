"""Pure data models for the plan executor (god-class split, behaviour zero-change).

``ToolCallRequest`` / ``ExecutionResponse`` / ``ExecutionResult`` /
``ExecutionSummary`` / ``ExecutionConfig`` were moved verbatim out of
``plan_executor.py`` per ``design/2026-09-24-backend-godfiles-refactor-plan.md``
§4.6 (cluster ①, zero-risk pure data).  ``plan_executor.py`` re-exports every
name, so all ``from app.services.plans.plan_executor import ExecutionConfig``
call sites (routers, scripts, tests) are unchanged.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from ...config.executor_config import ExecutorSettings


# ---------------------------------------------------------------------------
# Pydantic models for structured LLM responses
# ---------------------------------------------------------------------------


class ToolCallRequest(BaseModel):
    """Tool call request from executor LLM."""
    name: str = Field(description="Tool name: code_executor, web_search, document_reader, etc.")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Tool parameters")


class ExecutionResponse(BaseModel):
    """Structured payload returned by the execution LLM."""

    status: str = Field(pattern="^(success|failed|skipped|needs_tool)$")
    content: str
    tool_call: Optional[ToolCallRequest] = Field(default=None, description="Tool call request when status is needs_tool")
    notes: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: Optional[bool] = None,
        context: Optional[Dict[str, Any]] = None,
        by_alias: Optional[bool] = None,
        by_name: Optional[bool] = None,
        extra: Optional[str] = None,
    ) -> "ExecutionResponse":
        _ = strict, context, by_alias, by_name, extra
        try:
            payload = json.loads(json_data)
        except json.JSONDecodeError as exc:
            raise ValidationError([exc], cls) from exc
        return super().model_validate(payload)


@dataclass
class ExecutionResult:
    """Execution outcome for a single task."""

    plan_id: int
    task_id: int
    status: str
    content: str
    notes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw_response: Optional[str] = None
    attempts: int = 1
    duration_sec: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "status": self.status,
            "content": self.content,
            "notes": list(self.notes),
            "metadata": dict(self.metadata),
            "raw_response": self.raw_response,
            "attempts": self.attempts,
            "duration_sec": self.duration_sec,
        }


@dataclass
class ExecutionSummary:
    """Aggregate results for execute_plan."""

    plan_id: int
    executed_task_ids: List[int] = field(default_factory=list)
    failed_task_ids: List[int] = field(default_factory=list)
    skipped_task_ids: List[int] = field(default_factory=list)
    results: List[ExecutionResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def duration_sec(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return self.finished_at - self.started_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "executed_task_ids": list(self.executed_task_ids),
            "failed_task_ids": list(self.failed_task_ids),
            "skipped_task_ids": list(self.skipped_task_ids),
            "results": [result.to_dict() for result in self.results],
            "duration_sec": self.duration_sec,
        }


@dataclass
class ExecutionConfig:
    """Per-run execution options."""

    model: Optional[str] = None
    max_retries: int = 2
    timeout: Optional[float] = None
    use_context: bool = True
    include_plan_outline: bool = True
    dependency_throttle: bool = True
    max_tasks: Optional[int] = None
    session_context: Optional[Dict[str, Any]] = None
    enforce_dependencies: bool = True
    paper_mode: bool = False
    force_rerun: bool = False
    auto_recovery: bool = False
    max_recovery_attempts: int = 2
    contract_repair_attempts: int = 1
    autonomous: bool = False
    on_task_complete: Optional[Callable[["ExecutionResult", int, int], None]] = None
    enable_skills: bool = True
    skill_budget_chars: int = 6000
    skill_selection_mode: str = "hybrid"
    skill_max_per_task: int = 3
    skill_trace_enabled: bool = True
    skip_preflight: bool = False

    def __post_init__(self) -> None:
        if self.autonomous:
            self.auto_recovery = True
            self.dependency_throttle = False

    @classmethod
    def from_settings(cls, settings: ExecutorSettings) -> "ExecutionConfig":
        return cls(
            model=settings.model,
            max_retries=max(1, settings.max_retries),
            timeout=settings.timeout,
            use_context=settings.use_context,
            include_plan_outline=settings.include_plan_outline,
            dependency_throttle=settings.dependency_throttle,
            max_tasks=settings.max_tasks,
            enforce_dependencies=getattr(settings, "enforce_dependencies", True),
            paper_mode=bool(getattr(settings, "paper_mode", False)),
            force_rerun=getattr(settings, "force_rerun", False),
            auto_recovery=getattr(settings, "auto_recovery", False),
            max_recovery_attempts=max(1, getattr(settings, "max_recovery_attempts", 2)),
            contract_repair_attempts=max(0, getattr(settings, "contract_repair_attempts", 1)),
            autonomous=getattr(settings, "autonomous", False),
            enable_skills=settings.enable_skills,
            skill_budget_chars=settings.skill_budget_chars,
            skill_selection_mode=settings.skill_selection_mode,
            skill_max_per_task=settings.skill_max_per_task,
            skill_trace_enabled=settings.skill_trace_enabled,
        )
