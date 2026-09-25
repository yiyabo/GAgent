"""Pydantic DTOs for the plan and task routes (pure data)."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _default_plan_paper_mode() -> bool:
    raw = os.getenv("PLAN_PAPER_MODE_DEFAULT")
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


class SubgraphResponse(BaseModel):
    plan_id: int
    root_node: int
    max_depth: int
    outline: str
    nodes: list[dict[str, Any]]


class TodoItemResponse(BaseModel):
    task_id: int
    name: str
    instruction: Optional[str] = None
    status: str
    effective_status: str
    status_reason: Optional[str] = None
    blocked_by_dependencies: bool = False
    incomplete_dependencies: List[int] = Field(default_factory=list)
    is_active_execution: bool = False
    dependencies: List[int] = Field(default_factory=list)
    phase: int


class TodoPhaseResponse(BaseModel):
    phase_id: int
    label: str
    status: str
    total: int
    completed: int
    items: List[TodoItemResponse]


class TodoWorkflowSectionResponse(BaseModel):
    section_id: str
    label: str
    status: str
    total: int
    completed: int
    items: List[TodoItemResponse]


class TodoListResponse(BaseModel):
    plan_id: int
    target_task_id: int
    ordering_mode: str = "dependency_phase"
    total_tasks: int
    completed_tasks: int
    phases: List[TodoPhaseResponse]
    workflow_sections: List[TodoWorkflowSectionResponse] = Field(default_factory=list)
    execution_order: List[int]
    pending_order: List[int]
    summary: str


class DecomposeTaskRequest(BaseModel):
    plan_id: int = Field(..., description="Plan ID")
    expand_depth: Optional[int] = Field(None, ge=1, description="Maximum decomposition depth (defaults to service config)")
    node_budget: Optional[int] = Field(None, ge=1, description="Node budget for decomposition")
    allow_existing_children: Optional[bool] = Field(
        None, description="Allow decomposition even when child tasks already exist"
    )
    async_mode: bool = Field(
        False,
        description="Run decomposition asynchronously and return a background job id",
    )


class DecomposeTaskResponse(BaseModel):
    success: bool
    message: str
    result: Dict[str, Any]
    job: Optional[Dict[str, Any]] = Field(
        default=None, description="Background decomposition job status payload"
    )


class DecompositionJobStatusResponse(BaseModel):
    job_id: str
    job_type: str = "plan_decompose"
    status: str
    plan_id: Optional[int] = None
    task_id: Optional[int] = None
    mode: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    stats: Dict[str, Any] = Field(default_factory=dict)
    params: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    logs: List[Dict[str, Any]] = Field(default_factory=list)


class TaskResultItem(BaseModel):
    task_id: int
    name: Optional[str] = None
    status: Optional[str] = None
    effective_status: Optional[str] = None
    status_reason: Optional[str] = None
    blocked_by_dependencies: bool = False
    incomplete_dependencies: List[int] = Field(default_factory=list)
    is_active_execution: bool = False
    content: Optional[str] = None
    notes: List[str] = []
    metadata: Dict[str, Any] = {}
    raw: Optional[Dict[str, Any]] = None


class PlanResultsResponse(BaseModel):
    plan_id: int
    total: int
    items: List[TaskResultItem]


class VerifyTaskResponse(BaseModel):
    success: bool
    message: str
    plan_id: int
    task_id: int
    result: TaskResultItem


class ReverifyPlanDryRunResponse(BaseModel):
    success: bool
    message: str
    plan_id: int
    dry_run: bool = True
    summary: Dict[str, Any]
    items: List[Dict[str, Any]]


class AcceptTaskRequest(BaseModel):
    reason: str = Field(..., min_length=3, description="Why this failed task is acceptable")
    name: Optional[str] = Field(default=None, description="Optional updated task name")
    instruction: Optional[str] = Field(default=None, description="Optional updated task instruction")


class AcceptTaskResponse(BaseModel):
    success: bool
    message: str
    plan_id: int
    task_id: int
    updated_fields: List[str] = Field(default_factory=list)
    result: TaskResultItem


class PlanExecutionSummary(BaseModel):
    plan_id: int
    total_tasks: int
    completed: int
    failed: int
    skipped: int
    blocked: int = 0
    running: int
    pending: int


class DependencyNodeSummary(BaseModel):
    id: int
    name: str
    status: str
    effective_status: Optional[str] = None
    status_reason: Optional[str] = None
    blocked_by_dependencies: bool = False
    incomplete_dependencies: List[int] = Field(default_factory=list)
    is_active_execution: bool = False


class ExecutionChecklistItem(BaseModel):
    step_index: int
    task_id: int
    name: str
    status: str
    effective_status: Optional[str] = None
    status_reason: Optional[str] = None
    blocked_by_dependencies: bool = False
    incomplete_dependencies: List[int] = Field(default_factory=list)
    is_active_execution: bool = False
    execution_state: str
    instruction: Optional[str] = None
    depends_on: List[int] = Field(default_factory=list)
    unmet_dependencies: List[int] = Field(default_factory=list)
    expected_deliverables: List[str] = Field(default_factory=list)
    is_target: bool = False


class DependencyPlanResponse(BaseModel):
    plan_id: int
    target_task_id: int
    satisfied_statuses: List[str] = Field(default_factory=list)
    direct_dependencies: List[int] = Field(default_factory=list)
    closure_dependencies: List[int] = Field(default_factory=list)
    missing_dependencies: List[DependencyNodeSummary] = Field(default_factory=list)
    running_dependencies: List[DependencyNodeSummary] = Field(default_factory=list)
    execution_order: List[int] = Field(default_factory=list)
    execution_items: List[ExecutionChecklistItem] = Field(default_factory=list)
    cycle_detected: bool = False
    cycle_paths: List[List[int]] = Field(default_factory=list)


class ExecuteTaskRequest(BaseModel):
    include_dependencies: bool = True
    include_subtasks: bool = True
    deep_think: bool = True
    async_mode: bool = True
    session_id: Optional[str] = None
    paper_mode: bool = Field(default_factory=_default_plan_paper_mode)


class ExecuteTaskResponse(BaseModel):
    success: bool
    message: str
    plan_id: int
    task_id: int
    dependency_plan: DependencyPlanResponse
    job: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None


class ExecuteFullPlanRequest(BaseModel):
    deep_think: bool = True
    async_mode: bool = True
    session_id: Optional[str] = None
    paper_mode: bool = Field(default_factory=_default_plan_paper_mode)
    skip_completed: bool = Field(
        True, description="Skip tasks already marked completed"
    )
    stop_on_failure: bool = Field(
        True, description="Stop the chain when a task fails"
    )
    ordering_mode: str = Field(
        "structure",
        description="Full-plan order strategy: structure or dependency_phase",
    )
    dependency_block_mode: str = Field(
        "block",
        description="How execute-full handles incomplete dependencies: warn or block",
    )


class ExecuteFullPlanResponse(BaseModel):
    success: bool
    message: str
    plan_id: int
    todo_list: Optional[Dict[str, Any]] = None
    job: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
