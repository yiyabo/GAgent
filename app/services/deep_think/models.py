"""Data models for the DeepThink agent (god-class split, behaviour zero-change).

The ThinkingStep / DeepThinkResult / TaskExecutionContext dataclasses and the
DeepThinkProtocolError exception, extracted verbatim from
app.services.deep_think_agent so the guard / synthesis / prompt / controller
modules can share them without importing the god class. deep_think_agent
re-exports all four names; existing importers are unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ThinkingStep:
    """Represents a single step in the thinking process."""
    iteration: int
    thought: str
    action: Optional[str]
    action_result: Optional[str]
    self_correction: Optional[str]
    display_text: Optional[str] = None
    kind: str = "reasoning"
    timestamp: datetime = field(default_factory=datetime.now)
    status: str = "thinking"  # thinking, calling_tool, analyzing, done, error
    evidence: List[Dict[str, str]] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None


@dataclass
class DeepThinkResult:
    """The final result of the deep thinking process."""
    final_answer: str
    thinking_steps: List[ThinkingStep]
    total_iterations: int
    tools_used: List[str]
    confidence: float  # 0.0 to 1.0
    thinking_summary: str  # A concise summary for the user
    tool_failures: List[Dict[str, Any]] = field(default_factory=list)
    search_verified: bool = True
    fallback_used: bool = False
    structured_plan_required: bool = False
    structured_plan_satisfied: bool = False
    structured_plan_state: Optional[str] = None
    structured_plan_message: Optional[str] = None
    structured_plan_plan_id: Optional[int] = None
    structured_plan_title: Optional[str] = None
    structured_plan_operation: Optional[str] = None


@dataclass
class TaskExecutionContext:
    task_id: Optional[int] = None
    task_name: Optional[str] = None
    task_instruction: Optional[str] = None
    dependency_outputs: List[Dict[str, Any]] = field(default_factory=list)
    plan_outline: Optional[str] = None
    constraints: List[str] = field(default_factory=list)
    skill_context: Optional[str] = None
    context_summary: Optional[str] = None
    context_sections: List[Dict[str, Any]] = field(default_factory=list)
    paper_context_paths: List[str] = field(default_factory=list)
    # Explicit task selection: set when the user names specific task IDs in the message.
    # When explicit_task_override=True the agent must only execute within the declared
    # set and must NOT fall back to prose status summaries or plan-optimise suggestions.
    explicit_task_ids: List[int] = field(default_factory=list)
    explicit_task_override: bool = False


class DeepThinkProtocolError(RuntimeError):
    """Raised when DeepThink output violates the required JSON protocol."""
