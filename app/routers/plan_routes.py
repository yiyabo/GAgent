from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import heapq
import os
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.database import get_db
from app.repository.plan_repository import PlanRepository
from app.services.realtime_bus import EventSubscription, get_realtime_bus
from app.services.request_principal import ensure_owner_access, get_request_owner_id
from app.services.plans.artifact_contracts import producer_candidates_for_alias
from app.services.plans.acceptance_criteria import (
    derive_acceptance_criteria_from_text,
    derive_expected_deliverables,
)
from app.services.plans.dependency_planner import DependencyPlan, compute_dependency_plan
from app.services.plans.plan_decomposer import PlanDecomposer, DecompositionResult
from app.services.plans.plan_executor import ExecutionConfig, PlanExecutor
from app.services.plans.artifact_preflight import ArtifactPreflightResult, ArtifactPreflightService
from app.services.plans.status_resolver import PlanStatusResolver
from app.services.plans.task_verification import TaskVerificationService
from app.services.plans.dependency_enrichment import (
    enrich_plan_dependencies,
    validate_plan_dag,
    check_artifact_readiness,
)
from app.services.plans.todo_list import (
    build_todo_list as _build_todo_list,
    build_full_plan_todo_list as _build_full_plan_todo_list,
    _collect_leaf_ids,
)
from app.services.plans.decomposition_jobs import (
    execute_decomposition_job,
    plan_decomposition_jobs,
    log_job_event,
    reset_current_job,
    set_current_job,
)
from . import register_router

plan_router = APIRouter(prefix="/plans", tags=["plans"])
task_router = APIRouter(prefix="/tasks", tags=["tasks"])

_plan_repo = PlanRepository()
_plan_decomposer = PlanDecomposer(repo=_plan_repo)
_plan_executor = PlanExecutor(repo=_plan_repo)
_task_verifier = TaskVerificationService()
_artifact_preflight_service = ArtifactPreflightService()
_plan_status_resolver = PlanStatusResolver()
logger = logging.getLogger(__name__)

# Guard against duplicate concurrent execution of the same plan+task pair.
_task_execution_locks: Dict[Tuple[int, int], threading.Lock] = {}
_task_execution_locks_guard = threading.Lock()


def _default_plan_paper_mode() -> bool:
    raw = os.getenv("PLAN_PAPER_MODE_DEFAULT")
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def _ensure_plan_access(plan_id: int, request: Request) -> None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT owner FROM plans WHERE id=?",
            (plan_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")
    ensure_owner_access(request, row["owner"], detail="plan owner mismatch")


def _load_authorized_plan_tree(plan_id: int, request: Request):
    _ensure_plan_access(plan_id, request)
    try:
        return _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _sse_message(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _artifact_preflight_failure_payload(result: ArtifactPreflightResult) -> Dict[str, Any]:
    return {
        "preflight": result.model_dump(),
        "summary": result.summary(),
    }


def _is_terminal_job_status(raw_status: Any) -> bool:
    return str(raw_status or "").strip().lower() in {
        "succeeded",
        "failed",
        "completed",
        "success",
        "done",
        "error",
    }


def _run_decomposition_job(
    job_id: str,
    plan_id: int,
    task_id: Optional[int],
    expand_depth: Optional[int],
    node_budget: Optional[int],
    allow_existing_children: Optional[bool],
) -> None:
    """Background execution wrapper for async decomposition jobs."""
    execute_decomposition_job(
        plan_decomposer=_plan_decomposer,
        job_id=job_id,
        plan_id=plan_id,
        mode="single_node",
        task_id=task_id,
        expand_depth=expand_depth,
        node_budget=node_budget,
        allow_existing_children=allow_existing_children,
    )


@plan_router.get("", summary="List plans")
def list_plans(request: Request):
    """Return plan summaries."""
    summaries = _plan_repo.list_plans(owner=get_request_owner_id(request))
    return [summary.model_dump() for summary in summaries]


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


class TodoListResponse(BaseModel):
    plan_id: int
    target_task_id: int
    total_tasks: int
    completed_tasks: int
    phases: List[TodoPhaseResponse]
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


def _parse_execution_result(raw_value: Any) -> Tuple[Optional[str], List[str], Dict[str, Any], Optional[Dict[str, Any]]]:
    """Normalize execution result payloads into structured components."""

    if raw_value in (None, ""):
        return None, [], {}, None

    payload: Any = raw_value
    if isinstance(raw_value, (bytes, bytearray)):
        try:
            payload = raw_value.decode("utf-8")
        except Exception:  # pragma: no cover - defensive
            payload = raw_value

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            # legacy plain-text payload
            return payload, [], {}, None

    if isinstance(payload, dict):
        content = payload.get("content")
        notes_data = payload.get("notes") or []
        if isinstance(notes_data, list):
            notes = [str(item) for item in notes_data if item is not None]
        else:
            notes = [str(notes_data)]
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        return content, notes, metadata, payload

    # Fallback for unexpected payload types
    return str(payload), [], {}, None


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        text = str(value).strip()
        if not text:
            return None
        if "." in text:
            return int(float(text))
        return int(text)
    except Exception:
        return None


def _truncate_reason(value: Optional[str], max_chars: int = 220) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3].rstrip()}..."


def _looks_like_failure_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    tokens = (
        "traceback",
        "exception",
        "failed",
        "error",
        "unable to",
        "timed out",
        "interrupted",
    )
    return any(token in text for token in tokens)


def _looks_like_dependency_blocked_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    tokens = (
        "blocked by dependencies",
        "dependency outputs are missing",
        "incomplete dependencies",
        "unmet dependencies",
    )
    return any(token in text for token in tokens)


def _looks_like_retry_or_blocked_failure_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    tokens = (
        "retry",
        "blocked",
        "did not pass",
        "quality gate",
        "release_state: blocked",
        "release state: blocked",
        "unable to",
        "error:",
        "exception",
        "failed",
        "阻断",
        "重试",
        "未通过",
    )
    return any(token in text for token in tokens)


def _looks_like_success_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    tokens = ("completed", "completion", "succeeded", "success", "done")
    return any(token in text for token in tokens)


def _list_plan_execute_job_ids(plan_id: int, *, limit: int = 64) -> List[str]:
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT job_id
                FROM plan_decomposition_job_index
                WHERE job_type='plan_execute' AND plan_id=?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (plan_id, limit),
            ).fetchall()
    except Exception:
        return []
    return [str(row["job_id"]) for row in rows if row and row["job_id"]]


def _build_plan_execution_snapshot(
    plan_id: int,
    *,
    exclude_job_ids: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    exclude_job_ids = {str(job_id) for job_id in (exclude_job_ids or set()) if str(job_id).strip()}
    active_task_ids: Set[int] = set()
    active_jobs: List[Dict[str, Any]] = []
    for job_id in _list_plan_execute_job_ids(plan_id):
        if job_id in exclude_job_ids:
            continue
        payload = plan_decomposition_jobs.get_job_payload(job_id, include_logs=False)
        if not isinstance(payload, dict):
            continue
        status = _normalize_task_status(payload.get("status"))
        if status != "running":
            continue
        stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
        current_task_id = _to_int(stats.get("current_task_id"))
        if current_task_id is None:
            current_task_id = _to_int(payload.get("task_id"))
        if current_task_id is not None and current_task_id > 0:
            active_task_ids.add(current_task_id)
        active_jobs.append(payload)
    return {
        "active_task_ids": active_task_ids,
        "active_jobs": active_jobs,
    }


def _build_dependency_block_reason(
    task_id: int,
    *,
    tree: "PlanTree",
    incomplete_dependencies: List[int],
    state_by_task: Dict[int, Dict[str, Any]],
) -> str:
    parts: List[str] = []
    for dep_id in incomplete_dependencies:
        node = tree.nodes.get(dep_id)
        dep_state = state_by_task.get(dep_id) or {}
        dep_status = str(dep_state.get("effective_status") or "pending").strip().lower() or "pending"
        if node is None:
            parts.append(f"#{dep_id}({dep_status})")
        else:
            parts.append(f"#{dep_id}({dep_status})")
    incomplete_display = ", ".join(parts)
    return (
        f"Blocked by dependencies: task #{task_id} requires completed outputs from "
        f"{len(incomplete_dependencies)} dependency task(s): {incomplete_display}."
    )


def _resolve_effective_task_states(
    plan_id: int,
    tree: "PlanTree",
    *,
    snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[int, Dict[str, Any]]:
    snapshot = snapshot or _build_plan_execution_snapshot(plan_id)
    return _plan_status_resolver.resolve_plan_states(
        plan_id,
        tree,
        snapshot=snapshot,
    )


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


def _expected_deliverables_for_node(node: Any) -> List[str]:
    metadata = node.metadata if isinstance(getattr(node, "metadata", None), dict) else {}
    criteria = metadata.get("acceptance_criteria")
    if not isinstance(criteria, dict):
        raw_execution_result = getattr(node, "execution_result", None)
        if isinstance(raw_execution_result, str):
            try:
                raw_execution_result = json.loads(raw_execution_result)
            except Exception:
                raw_execution_result = None
        if isinstance(raw_execution_result, dict):
            payload_meta = raw_execution_result.get("metadata")
            if isinstance(payload_meta, dict):
                criteria = payload_meta.get("acceptance_criteria")
    if not isinstance(criteria, dict):
        criteria = derive_acceptance_criteria_from_text(getattr(node, "instruction", None))
    if not isinstance(criteria, dict):
        return []
    return derive_expected_deliverables(criteria)


def _build_execution_checklist_items(
    tree: "PlanTree",
    plan: DependencyPlan,
    *,
    state_by_task: Dict[int, Dict[str, Any]],
) -> List[ExecutionChecklistItem]:
    satisfied = set(plan.satisfied_statuses or ("completed", "done"))
    selected = set(plan.execution_order)
    items: List[ExecutionChecklistItem] = []

    for index, task_id in enumerate(plan.execution_order, start=1):
        if task_id not in tree.nodes:
            continue
        node = tree.nodes[task_id]
        ordering_dependencies: Set[int] = {
            dep_id
            for dep_id in list(getattr(node, "dependencies", []) or [])
            if dep_id in selected and dep_id in tree.nodes
        }
        for child_id in tree.children_ids(task_id):
            if child_id in selected and child_id in tree.nodes:
                ordering_dependencies.add(child_id)
        unmet = [
            dep_id
            for dep_id in sorted(ordering_dependencies)
            if str((state_by_task.get(dep_id) or {}).get("effective_status") or "pending") not in satisfied
        ]
        state = state_by_task.get(task_id) or {}
        effective_status = str(state.get("effective_status") or _normalize_task_status(getattr(node, "status", None)))
        if effective_status in satisfied:
            execution_state = "completed"
        elif effective_status in {"failed", "error"}:
            execution_state = "failed"
        elif effective_status == "running":
            execution_state = "running"
        elif effective_status == "blocked":
            execution_state = "blocked"
        elif unmet:
            execution_state = "blocked"
        else:
            execution_state = "ready"
        items.append(
            ExecutionChecklistItem(
                step_index=index,
                task_id=task_id,
                name=node.display_name(),
                status=effective_status,
                **_effective_response_fields(state),
                execution_state=execution_state,
                instruction=str(getattr(node, "instruction", "") or "").strip() or None,
                depends_on=sorted(ordering_dependencies),
                unmet_dependencies=unmet,
                expected_deliverables=_expected_deliverables_for_node(node),
                is_target=task_id == plan.target_task_id,
            )
        )
    return items


def _to_dependency_plan_response(
    tree: "PlanTree",
    plan: DependencyPlan,
    *,
    state_by_task: Optional[Dict[int, Dict[str, Any]]] = None,
) -> DependencyPlanResponse:
    if state_by_task is None:
        state_by_task = _resolve_effective_task_states(plan.plan_id, tree)

    def _node_summary(task_id: int) -> DependencyNodeSummary:
        node = tree.nodes[task_id]
        state = state_by_task.get(task_id)
        return DependencyNodeSummary(
            id=node.id,
            name=node.display_name(),
            status=str((state or {}).get("effective_status") or node.status),
            **_effective_response_fields(state),
        )

    missing_dependencies = [
        tid
        for tid in plan.closure_dependencies
        if str((state_by_task.get(tid) or {}).get("effective_status") or "pending")
        not in set(plan.satisfied_statuses or ("completed", "done"))
    ]
    running_dependencies = [
        tid
        for tid in plan.closure_dependencies
        if str((state_by_task.get(tid) or {}).get("effective_status") or "") == "running"
    ]

    return DependencyPlanResponse(
        plan_id=plan.plan_id,
        target_task_id=plan.target_task_id,
        satisfied_statuses=list(plan.satisfied_statuses),
        direct_dependencies=list(plan.direct_dependencies),
        closure_dependencies=list(plan.closure_dependencies),
        missing_dependencies=[_node_summary(tid) for tid in missing_dependencies],
        running_dependencies=[_node_summary(tid) for tid in running_dependencies],
        execution_order=list(plan.execution_order),
        execution_items=_build_execution_checklist_items(
            tree,
            plan,
            state_by_task=state_by_task,
        ),
        cycle_detected=plan.cycle_detected,
        cycle_paths=[list(path) for path in plan.cycle_paths],
    )


def _normalize_task_status(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _build_dependency_block_details(
    tree: Any,
    task_id: int,
    *,
    state_by_task: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    node = tree.nodes.get(task_id) if tree is not None else None
    if node is None:
        return None

    incomplete_deps: List[Tuple[Any, str]] = []
    for dep_id in list(node.dependencies or []):
        dep = tree.nodes.get(dep_id)
        if dep is None:
            continue
        dep_state = (state_by_task or {}).get(dep.id) or {}
        dep_status = str(dep_state.get("effective_status") or _normalize_task_status(dep.status) or "pending")
        if dep_status not in ("completed", "done"):
            incomplete_deps.append((dep, dep_status))

    if not incomplete_deps:
        return None

    incomplete_ids = [dep.id for dep, _ in incomplete_deps]
    incomplete_display = ", ".join(
        f"#{dep.id}({dep_status or 'pending'})"
        for dep, dep_status in incomplete_deps
    )
    reason = (
        f"Blocked by dependencies: task #{task_id} requires completed outputs from "
        f"{len(incomplete_deps)} dependency task(s): {incomplete_display}."
    )
    notes = [
        "This task was not executed because dependency outputs are missing.",
        f"Unmet dependencies: {incomplete_display}",
    ]
    payload = {
        "status": "skipped",
        "content": reason,
        "notes": notes,
        "metadata": {
            "blocked_by_dependencies": True,
            "incomplete_dependencies": incomplete_ids,
            "incomplete_dependency_info": [
                {
                    "id": dep.id,
                    "name": dep.display_name(),
                    "status": dep_status,
                }
                for dep, dep_status in incomplete_deps
            ],
        },
    }
    return {
        "reason": reason,
        "notes": notes,
        "metadata": dict(payload["metadata"]),
        "payload": payload,
    }


def _collect_subtree_node_ids(tree: "PlanTree", root_task_id: int) -> List[int]:
    if root_task_id not in tree.nodes:
        return []
    ordered: List[int] = []
    stack: List[int] = [root_task_id]
    visited: Set[int] = set()
    while stack:
        current = stack.pop()
        if current in visited or current not in tree.nodes:
            continue
        visited.add(current)
        ordered.append(current)
        children = list(tree.children_ids(current))
        for child_id in reversed(children):
            if child_id not in visited:
                stack.append(child_id)
    return ordered


def _expand_artifact_preflight_scope(
    tree: "PlanTree",
    task_ids: Set[int],
    preflight: ArtifactPreflightResult,
) -> Set[int]:
    expanded = {task_id for task_id in task_ids if task_id in tree.nodes}
    if not expanded or not preflight.has_errors():
        return expanded

    all_nodes = list(tree.nodes.values())
    for issue in preflight.errors:
        if issue.code != "missing_producer" or not issue.alias:
            continue
        for producer_id in producer_candidates_for_alias(issue.alias, all_nodes):
            if producer_id in tree.nodes:
                expanded.add(producer_id)
    return expanded


def _persist_dependency_block(plan_id: int, task_id: int, dependency_block: Dict[str, Any]) -> None:
    try:
        _plan_repo.update_task(
            plan_id,
            task_id,
            status="skipped",
            execution_result=json.dumps(dependency_block["payload"], ensure_ascii=False),
        )
    except Exception as exc:
        logger.warning(
            "Failed to persist dependency-blocked status for task %s in plan %s: %s",
            task_id,
            plan_id,
            exc,
        )


def _effective_response_fields(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    state = state or {}
    return {
        "effective_status": str(state.get("effective_status") or "pending"),
        "status_reason": state.get("status_reason"),
        "blocked_by_dependencies": bool(state.get("blocked_by_dependencies")),
        "incomplete_dependencies": list(state.get("incomplete_dependencies") or []),
        "is_active_execution": bool(state.get("is_active_execution")),
    }


def _serialize_plan_tree_with_effective_status(
    plan_id: int,
    tree: "PlanTree",
    *,
    state_by_task: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    state_by_task = state_by_task or _resolve_effective_task_states(plan_id, tree)
    nodes_payload: Dict[str, Any] = {}
    for task_id, node in tree.nodes.items():
        payload = node.model_dump()
        payload.update(_effective_response_fields(state_by_task.get(task_id)))
        payload["status"] = payload["effective_status"]
        nodes_payload[str(task_id)] = payload

    adjacency_payload: Dict[str, List[int]] = {}
    for parent_id, children in tree.adjacency.items():
        adjacency_payload["null" if parent_id is None else str(parent_id)] = list(children)

    return {
        "id": tree.id,
        "title": tree.title,
        "description": tree.description,
        "metadata": tree.metadata,
        "nodes": nodes_payload,
        "adjacency": adjacency_payload,
    }


def _todo_phase_status_from_effective(
    phase: Any,
    state_by_task: Dict[int, Dict[str, Any]],
) -> str:
    items = list(getattr(phase, "items", []) or [])
    if not items:
        return "empty"
    statuses = [
        str((state_by_task.get(item.task_id) or {}).get("effective_status") or "pending")
        for item in items
    ]
    if statuses and all(status == "completed" for status in statuses):
        return "completed"
    if any(status == "failed" for status in statuses):
        return "partial_failure"
    if any(status == "blocked" for status in statuses):
        for item in items:
            item_state = state_by_task.get(item.task_id) or {}
            if str(item_state.get("effective_status") or "") != "blocked":
                continue
            for dep_id in list(item_state.get("incomplete_dependencies") or []):
                dep_state = state_by_task.get(dep_id) or {}
                if str(dep_state.get("effective_status") or "") == "failed":
                    return "partial_failure"
    if any(status in {"completed", "running"} for status in statuses):
        return "in_progress"
    return "pending"


def _todo_completed_count_from_effective(
    phase: Any,
    state_by_task: Dict[int, Dict[str, Any]],
) -> int:
    return sum(
        1
        for item in list(getattr(phase, "items", []) or [])
        if str((state_by_task.get(item.task_id) or {}).get("effective_status") or "") == "completed"
    )


def _todo_pending_order_from_effective(
    todo: Any,
    state_by_task: Dict[int, Dict[str, Any]],
    tree: Optional["PlanTree"] = None,
) -> List[int]:
    resolved = {
        item.task_id
        for phase in todo.phases
        for item in phase.items
        if str((state_by_task.get(item.task_id) or {}).get("effective_status") or "") == "completed"
    }
    runnable: List[int] = []
    runnable_set: Set[int] = set()
    runnable_statuses = {"pending", "failed", "skipped", "blocked"}

    for phase in todo.phases:
        for item in phase.items:
            effective_status = str((state_by_task.get(item.task_id) or {}).get("effective_status") or "pending")
            if effective_status not in runnable_statuses:
                continue
            # Skip composite parent tasks — only leaf/atomic tasks should be executed directly
            if tree is not None and tree.children_ids(item.task_id):
                continue
            deps = list(item.dependencies or [])
            # Expand composite parent dependencies to their leaf children
            def _dep_satisfied(dep_id: int) -> bool:
                if dep_id in resolved or dep_id in runnable_set:
                    return True
                # If dep is a composite parent, check if all its leaves are satisfied
                if tree is not None and tree.children_ids(dep_id):
                    leaf_deps = _collect_leaf_ids(tree, [dep_id])
                    return all(ld in resolved or ld in runnable_set for ld in leaf_deps)
                return False
            if all(_dep_satisfied(dep_id) for dep_id in deps):
                runnable.append(item.task_id)
                runnable_set.add(item.task_id)
    return runnable


def _todo_summary_from_effective(
    todo: Any,
    state_by_task: Dict[int, Dict[str, Any]],
) -> str:
    parts = [f"TodoList for task {todo.target_task_id}:"]
    total_completed = 0
    total_tasks = 0
    for phase in todo.phases:
        completed = _todo_completed_count_from_effective(phase, state_by_task)
        total = len(list(getattr(phase, "items", []) or []))
        total_completed += completed
        total_tasks += total
        parts.append(
            f"  {phase.label} — {completed}/{total} done [{_todo_phase_status_from_effective(phase, state_by_task)}]"
        )
        for item in phase.items:
            state = state_by_task.get(item.task_id) or {}
            effective_status = str(state.get("effective_status") or "pending")
            if effective_status == "completed":
                mark = "✓"
            elif effective_status in {"failed", "blocked"}:
                mark = "✗"
            else:
                mark = "○"
            parts.append(f"    {mark} [{item.task_id}] {item.name}")
    parts.append(f"  Total: {total_completed}/{total_tasks} completed")
    return "\n".join(parts)


def _topological_task_order(tree: "PlanTree", node_ids: Iterable[int]) -> Tuple[List[int], bool]:
    selected: Set[int] = {nid for nid in node_ids if nid in tree.nodes}
    if not selected:
        return [], False

    in_degree: Dict[int, int] = {nid: 0 for nid in selected}
    outgoing: Dict[int, Set[int]] = {nid: set() for nid in selected}

    def _add_edge(src: int, dst: int) -> None:
        if src == dst:
            return
        if src not in selected or dst not in selected:
            return
        edges = outgoing.setdefault(src, set())
        if dst in edges:
            return
        edges.add(dst)
        in_degree[dst] += 1

    for node_id in selected:
        node = tree.nodes[node_id]
        for dep_id in node.dependencies:
            _add_edge(dep_id, node_id)
        for child_id in tree.children_ids(node_id):
            # Parent execution should happen after child execution so parent output can
            # summarize/compose child results.
            _add_edge(child_id, node_id)

    heap: List[int] = [nid for nid, degree in in_degree.items() if degree == 0]
    heapq.heapify(heap)
    order: List[int] = []

    while heap:
        current = heapq.heappop(heap)
        order.append(current)
        for nxt in sorted(outgoing.get(current, set())):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                heapq.heappush(heap, nxt)

    has_cycle = len(order) != len(selected)
    return order, has_cycle


def _dedupe_cycle_paths(cycle_paths: Iterable[List[int]]) -> List[List[int]]:
    deduped: List[List[int]] = []
    seen: Set[Tuple[int, ...]] = set()
    for path in cycle_paths:
        if not path:
            continue
        key = tuple(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(list(path))
    return deduped


def _build_execution_dependency_plan(
    tree: "PlanTree",
    target_task_id: int,
    *,
    include_dependencies: bool,
    include_subtasks: bool,
    state_by_task: Optional[Dict[int, Dict[str, Any]]] = None,
) -> DependencyPlan:
    base_plan = compute_dependency_plan(tree, target_task_id, include_target_in_order=False)
    state_by_task = state_by_task or {}
    subtree_node_ids = (
        _collect_subtree_node_ids(tree, target_task_id)
        if include_subtasks
        else [target_task_id]
    )
    subtree_set: Set[int] = set(subtree_node_ids)

    satisfied = (
        set(base_plan.satisfied_statuses)
        if base_plan.satisfied_statuses
        else {"completed", "done"}
    )
    closure: Set[int] = set(base_plan.closure_dependencies)
    missing: Set[int] = set()
    running: Set[int] = set()
    cycle_detected = bool(base_plan.cycle_detected)
    cycle_paths: List[List[int]] = [list(path) for path in base_plan.cycle_paths]

    if include_dependencies and include_subtasks:
        for node_id in subtree_node_ids:
            if node_id == target_task_id:
                continue
            child_plan = compute_dependency_plan(
                tree,
                node_id,
                include_target_in_order=False,
            )
            closure.update(child_plan.closure_dependencies)
            missing.update(child_plan.missing_dependencies)
            running.update(child_plan.running_dependencies)
            if child_plan.cycle_detected:
                cycle_detected = True
            cycle_paths.extend(list(path) for path in child_plan.cycle_paths)

    for dep_id in closure:
        if dep_id not in tree.nodes:
            continue
        dep_status = str(
            (state_by_task.get(dep_id) or {}).get("effective_status")
            or _normalize_task_status(tree.nodes[dep_id].status)
            or "pending"
        )
        if dep_status == "running":
            running.add(dep_id)
        if dep_status not in satisfied:
            missing.add(dep_id)

    to_run: Set[int] = set(subtree_set)
    if include_dependencies:
        for dep_id in closure:
            if dep_id not in tree.nodes:
                continue
            dep_status = str(
                (state_by_task.get(dep_id) or {}).get("effective_status")
                or _normalize_task_status(tree.nodes[dep_id].status)
                or "pending"
            )
            if dep_status not in satisfied:
                to_run.add(dep_id)
                missing.add(dep_id)

    order, topo_cycle = _topological_task_order(tree, to_run)
    if topo_cycle:
        cycle_detected = True
    cycle_paths = _dedupe_cycle_paths(cycle_paths)

    return DependencyPlan(
        plan_id=base_plan.plan_id,
        target_task_id=base_plan.target_task_id,
        satisfied_statuses=tuple(sorted(satisfied)),
        direct_dependencies=list(base_plan.direct_dependencies),
        closure_dependencies=sorted(closure),
        missing_dependencies=sorted(missing),
        running_dependencies=sorted(running),
        execution_order=order,
        cycle_detected=cycle_detected,
        cycle_paths=cycle_paths,
    )


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


def _run_task_chain_job(
    *,
    job_id: str,
    plan_id: int,
    target_task_id: int,
    task_order: List[int],
    deep_think: bool = True,
    session_id: Optional[str] = None,
    paper_mode: bool = False,
) -> None:
    token = set_current_job(job_id)
    executed: List[int] = []
    failed: List[int] = []
    skipped: List[int] = []
    step_summaries: List[Dict[str, Any]] = []
    total_steps = len(task_order)

    def _build_progress_stats(
        *,
        current_step: Optional[int] = None,
        current_task_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        done_steps = len(executed) + len(failed) + len(skipped)
        progress_percent = 0
        if total_steps > 0:
            progress_percent = int(round((min(done_steps, total_steps) / total_steps) * 100))
            if done_steps < total_steps:
                progress_percent = max(0, min(99, progress_percent))
            else:
                progress_percent = 100

        stats_payload: Dict[str, Any] = {
            "executed": len(executed),
            "failed": len(failed),
            "skipped": len(skipped),
            "done": done_steps,
            "total_steps": total_steps,
            "progress_percent": progress_percent,
        }
        if current_step is not None:
            stats_payload["current_step"] = current_step
        if current_task_id is not None:
            stats_payload["current_task_id"] = current_task_id
        return stats_payload

    def _publish_progress(
        *,
        current_step: Optional[int] = None,
        current_task_id: Optional[int] = None,
    ) -> None:
        plan_decomposition_jobs.update_stats(
            job_id,
            _build_progress_stats(
                current_step=current_step,
                current_task_id=current_task_id,
            ),
        )
        log_job_event(
            "info",
            "Task chain progress update.",
            {
                "sub_type": "task_progress",
                "task_id": current_task_id,
                "step": current_step,
                "total": total_steps,
            },
        )

    try:
        plan_decomposition_jobs.mark_running(job_id)
        log_job_event(
            "info",
            "Task chain execution started.",
            {
                "plan_id": plan_id,
                "target_task_id": target_task_id,
                "steps": len(task_order),
                "task_order": task_order,
                "deep_think": deep_think,
                "paper_mode": paper_mode,
            },
        )
        _publish_progress(
            current_step=0,
            current_task_id=task_order[0] if task_order else None,
        )

        session_ctx = {
            "session_id": session_id,
            "user_message": (
                f"Execute task chain for task #{target_task_id} "
                f"(UI-triggered, deep_think={'on' if deep_think else 'off'})."
            ),
            "chat_history": [],
            "recent_tool_results": [],
            "deep_think_enabled": bool(deep_think),
            "paper_mode": bool(paper_mode),
        }

        for idx, task_id in enumerate(task_order, start=1):
            log_job_event(
                "info",
                "Executing chain step.",
                {
                    "plan_id": plan_id,
                    "target_task_id": target_task_id,
                    "step": idx,
                    "total_steps": len(task_order),
                    "task_id": task_id,
                },
            )
            _publish_progress(current_step=idx, current_task_id=task_id)

            try:
                exec_config = ExecutionConfig(session_context=session_ctx, paper_mode=bool(paper_mode))
                result = _plan_executor.execute_task(
                    plan_id,
                    task_id,
                    config=exec_config,
                )
            except Exception as exc:  # pragma: no cover - defensive
                failed.append(task_id)
                _publish_progress(current_step=idx, current_task_id=task_id)
                error = f"Task #{task_id} raised an exception: {exc}"
                log_job_event(
                    "error",
                    "Chain step raised exception; stopping.",
                    {"task_id": task_id, "error": str(exc)},
                )
                plan_decomposition_jobs.mark_failure(
                    job_id,
                    error,
                    result={
                        "plan_id": plan_id,
                        "target_task_id": target_task_id,
                        "execution_order": task_order,
                        "executed_task_ids": executed,
                        "failed_task_ids": failed,
                        "skipped_task_ids": skipped,
                        "steps": step_summaries,
                    },
                    stats={
                        **_build_progress_stats(
                            current_step=idx,
                            current_task_id=task_id,
                        ),
                    },
                )
                return

            step_summaries.append(
                {
                    "task_id": task_id,
                    "status": result.status,
                    "duration_sec": result.duration_sec,
                }
            )

            if result.status == "completed":
                executed.append(task_id)
                _publish_progress(current_step=idx, current_task_id=task_id)
                continue
            if result.status == "skipped":
                skipped.append(task_id)
                _publish_progress(current_step=idx, current_task_id=task_id)
                error = (
                    f"Task #{task_id} was skipped (likely blocked by dependencies); stopping the chain."
                )
                log_job_event(
                    "warning",
                    "Chain step skipped; stopping.",
                    {"task_id": task_id, "reason": result.content},
                )
                plan_decomposition_jobs.mark_failure(
                    job_id,
                    error,
                    result={
                        "plan_id": plan_id,
                        "target_task_id": target_task_id,
                        "execution_order": task_order,
                        "executed_task_ids": executed,
                        "failed_task_ids": failed,
                        "skipped_task_ids": skipped,
                        "steps": step_summaries,
                    },
                    stats={
                        **_build_progress_stats(
                            current_step=idx,
                            current_task_id=task_id,
                        ),
                    },
                )
                return

            failed.append(task_id)
            _publish_progress(current_step=idx, current_task_id=task_id)
            error = f"Task #{task_id} failed; stopping the chain."
            log_job_event(
                "error",
                "Chain step failed; stopping.",
                {"task_id": task_id, "reason": result.content},
            )
            plan_decomposition_jobs.mark_failure(
                job_id,
                error,
                result={
                    "plan_id": plan_id,
                    "target_task_id": target_task_id,
                    "execution_order": task_order,
                    "executed_task_ids": executed,
                    "failed_task_ids": failed,
                    "skipped_task_ids": skipped,
                    "steps": step_summaries,
                },
                stats={
                    **_build_progress_stats(
                        current_step=idx,
                        current_task_id=task_id,
                    ),
                },
            )
            return

        _publish_progress(
            current_step=total_steps,
            current_task_id=task_order[-1] if task_order else None,
        )
        plan_decomposition_jobs.mark_success(
            job_id,
            result={
                "plan_id": plan_id,
                "target_task_id": target_task_id,
                "execution_order": task_order,
                "executed_task_ids": executed,
                "failed_task_ids": failed,
                "skipped_task_ids": skipped,
                "steps": step_summaries,
            },
            stats={
                **_build_progress_stats(
                    current_step=total_steps,
                    current_task_id=task_order[-1] if task_order else None,
                ),
            },
        )
    finally:
        reset_current_job(token)


@plan_router.get("/{plan_id}/tree", summary="Get plan tree")
def get_plan_tree(plan_id: int, request: Request):
    """Return serialized PlanTree for the specified plan."""
    tree = _load_authorized_plan_tree(plan_id, request)
    state_by_task = _resolve_effective_task_states(plan_id, tree)
    return _serialize_plan_tree_with_effective_status(
        plan_id,
        tree,
        state_by_task=state_by_task,
    )


@plan_router.get(
    "/{plan_id}/results",
    response_model=PlanResultsResponse,
    summary="List plan execution results",
)
def get_plan_results(
    plan_id: int,
    request: Request,
    only_with_output: bool = Query(True, description="Only include tasks with execution output"),
):
    tree = _load_authorized_plan_tree(plan_id, request)
    state_by_task = _resolve_effective_task_states(plan_id, tree)

    items: List[TaskResultItem] = []
    for node in tree.ordered_nodes():
        content, notes, metadata, raw_payload = _parse_execution_result(node.execution_result)
        if content is None and not notes and not metadata and only_with_output:
            continue
        state = state_by_task.get(node.id)
        items.append(
            TaskResultItem(
                task_id=node.id,
                name=node.name,
                status=str((state or {}).get("effective_status") or node.status),
                **_effective_response_fields(state),
                content=content,
                notes=notes,
                metadata=metadata,
                raw=raw_payload,
            )
        )

    return PlanResultsResponse(plan_id=plan_id, total=len(items), items=items)


@task_router.get(
    "/{task_id}/result",
    response_model=TaskResultItem,
    summary="Get task execution result",
)
def get_task_result(
    task_id: int,
    request: Request,
    plan_id: int = Query(..., description="plan ID"),
):
    tree = _load_authorized_plan_tree(plan_id, request)
    if not tree.has_node(task_id):
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in plan {plan_id}")
    node = tree.get_node(task_id)
    state_by_task = _resolve_effective_task_states(plan_id, tree)
    state = state_by_task.get(task_id)

    content, notes, metadata, raw_payload = _parse_execution_result(node.execution_result)

    return TaskResultItem(
        task_id=node.id,
        name=node.name,
        status=str((state or {}).get("effective_status") or node.status),
        **_effective_response_fields(state),
        content=content,
        notes=notes,
        metadata=metadata,
        raw=raw_payload,
    )


@task_router.post(
    "/{task_id}/verify",
    response_model=VerifyTaskResponse,
    summary="Re-run deterministic verification for a task result",
)
def verify_task_result(
    task_id: int,
    request: Request,
    plan_id: int = Query(..., description="plan ID"),
):
    tree = _load_authorized_plan_tree(plan_id, request)
    if not tree.has_node(task_id):
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in plan {plan_id}")
    node = tree.get_node(task_id)
    if not node.execution_result:
        raise HTTPException(status_code=400, detail=f"Task {task_id} has no execution result to verify")

    try:
        finalization = _task_verifier.verify_task(
            _plan_repo,
            plan_id=plan_id,
            task_id=task_id,
            trigger="manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    content, notes, metadata, raw_payload = _parse_execution_result(finalization.payload)
    verification = metadata.get("verification") if isinstance(metadata, dict) else None
    verification_status = (
        str(verification.get("status")).strip().lower()
        if isinstance(verification, dict) and verification.get("status") is not None
        else "skipped"
    )
    artifact_authority = metadata.get("artifact_authority") if isinstance(metadata, dict) else None
    artifact_authority_status = (
        str(artifact_authority.get("status")).strip().lower()
        if isinstance(artifact_authority, dict) and artifact_authority.get("status") is not None
        else None
    )
    manual_acceptance_active = _task_verifier.is_manual_acceptance_active(metadata)
    manual_acceptance_reason = None
    if manual_acceptance_active:
        manual_acceptance = metadata.get("manual_acceptance") if isinstance(metadata, dict) else None
        if isinstance(manual_acceptance, dict):
            manual_acceptance_reason = str(manual_acceptance.get("reason") or "").strip() or None
    final_status = str(finalization.final_status or "").strip().lower()
    success = final_status not in {"failed", "error"}
    if artifact_authority_status == "failed":
        success = False
    if manual_acceptance_active:
        success = True
    if not success and artifact_authority_status == "failed" and verification_status != "failed":
        message = f"Task {task_id} verification finished, but artifact authority failed."
    elif manual_acceptance_active and verification_status == "failed":
        message = (
            f"Task {task_id} verification still failed deterministically, "
            f"but the task remains manually accepted."
        )
    elif verification_status == "passed":
        message = f"Task {task_id} verification passed."
    elif verification_status == "failed":
        message = f"Task {task_id} verification failed."
    else:
        message = f"Task {task_id} verification skipped."

    return VerifyTaskResponse(
        success=success,
        message=message,
        plan_id=plan_id,
        task_id=task_id,
        result=TaskResultItem(
            task_id=task_id,
            name=node.name,
            status="completed" if manual_acceptance_active else finalization.final_status,
            effective_status="completed" if manual_acceptance_active else finalization.final_status,
            status_reason=manual_acceptance_reason or _truncate_reason(content),
            content=content,
            notes=notes,
            metadata=metadata,
            raw=raw_payload,
        ),
    )


@task_router.post(
    "/{task_id}/accept",
    response_model=AcceptTaskResponse,
    summary="Manually accept a task result after review",
)
def accept_task_result(
    task_id: int,
    payload: AcceptTaskRequest,
    request: Request,
    plan_id: int = Query(..., description="plan ID"),
):
    tree = _load_authorized_plan_tree(plan_id, request)
    if not tree.has_node(task_id):
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in plan {plan_id}")
    node = tree.get_node(task_id)
    if not node.execution_result:
        raise HTTPException(status_code=400, detail=f"Task {task_id} has no execution result to accept")

    try:
        accepted_by = get_request_owner_id(request)
        finalization = _task_verifier.accept_task_result(
            _plan_repo,
            plan_id=plan_id,
            task_id=task_id,
            reason=payload.reason,
            accepted_by=str(accepted_by) if accepted_by is not None else None,
            task_name=payload.name,
            task_instruction=payload.instruction,
        )
        reset_count = _task_verifier.reset_downstream_skipped_tasks(
            _plan_repo,
            plan_id=plan_id,
            task_id=task_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    content, notes, metadata, raw_payload = _parse_execution_result(finalization.payload)
    updated_fields: List[str] = []
    if payload.name is not None and str(payload.name).strip():
        updated_fields.append("name")
    if payload.instruction is not None and str(payload.instruction).strip():
        updated_fields.append("instruction")
    message = f"Task {task_id} marked completed after manual review."
    if reset_count:
        message = (
            f"Task {task_id} marked completed after manual review; "
            f"reset {reset_count} downstream skipped task(s) to pending."
        )

    return AcceptTaskResponse(
        success=True,
        message=message,
        plan_id=plan_id,
        task_id=task_id,
        updated_fields=updated_fields,
        result=TaskResultItem(
            task_id=task_id,
            name=str(payload.name).strip() if payload.name is not None and str(payload.name).strip() else node.name,
            status="completed",
            effective_status="completed",
            status_reason=str(payload.reason).strip(),
            content=content,
            notes=notes,
            metadata=metadata,
            raw=raw_payload,
        ),
    )


@task_router.get(
    "/{task_id}/dependency-plan",
    response_model=DependencyPlanResponse,
    summary="Get task dependency plan",
)
def get_task_dependency_plan(
    task_id: int,
    request: Request,
    plan_id: int = Query(..., description="plan ID"),
    include_dependencies: bool = Query(
        True,
        description="Whether to include unresolved dependency closure in execution planning",
    ),
    include_subtasks: bool = Query(
        False,
        description="Whether to include subtree tasks rooted at target task in execution planning",
    ),
):
    tree = _load_authorized_plan_tree(plan_id, request)
    if not tree.has_node(task_id):
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in plan {plan_id}")
    state_by_task = _resolve_effective_task_states(plan_id, tree)

    plan = _build_execution_dependency_plan(
        tree,
        task_id,
        include_dependencies=bool(include_dependencies),
        include_subtasks=bool(include_subtasks),
        state_by_task=state_by_task,
    )
    return _to_dependency_plan_response(tree, plan, state_by_task=state_by_task)


@task_router.post(
    "/{task_id}/execute",
    response_model=ExecuteTaskResponse,
    summary="Execute task with dependencies",
)
def execute_task_with_dependencies(
    task_id: int,
    plan_id: int = Query(..., description="plan ID"),
    raw_request: Request = None,
    request: Optional[ExecuteTaskRequest] = Body(default=None),
):
    request = request or ExecuteTaskRequest()
    tree = _load_authorized_plan_tree(plan_id, raw_request)
    if not tree.has_node(task_id):
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in plan {plan_id}")
    state_by_task = _resolve_effective_task_states(plan_id, tree)

    dep_plan = _build_execution_dependency_plan(
        tree,
        task_id,
        include_dependencies=bool(request.include_dependencies),
        include_subtasks=bool(request.include_subtasks),
        state_by_task=state_by_task,
    )
    dep_response = _to_dependency_plan_response(tree, dep_plan, state_by_task=state_by_task)

    if dep_plan.cycle_detected:
        return ExecuteTaskResponse(
            success=False,
            message="Dependency cycle detected. Resolve the cycle before execution.",
            plan_id=plan_id,
            task_id=task_id,
            dependency_plan=dep_response,
            job=None,
            result=None,
        )

    scoped_task_ids: Set[int] = set(dep_plan.execution_order)
    scoped_task_ids.update(dep_plan.closure_dependencies)
    scoped_task_ids.add(task_id)
    if request.include_subtasks:
        scoped_task_ids.update(_collect_subtree_node_ids(tree, task_id))
    preflight = _artifact_preflight_service.validate_plan(
        plan_id,
        tree,
        task_ids=scoped_task_ids,
    )
    expanded_task_ids = _expand_artifact_preflight_scope(tree, scoped_task_ids, preflight)
    if expanded_task_ids != scoped_task_ids:
        preflight = _artifact_preflight_service.validate_plan(
            plan_id,
            tree,
            task_ids=expanded_task_ids,
        )
    if preflight.has_errors():
        return ExecuteTaskResponse(
            success=False,
            message=preflight.summary(),
            plan_id=plan_id,
            task_id=task_id,
            dependency_plan=dep_response,
            job=None,
            result=_artifact_preflight_failure_payload(preflight),
        )

    if request.include_dependencies and dep_response.running_dependencies:
        return ExecuteTaskResponse(
            success=False,
            message="Some dependencies are still running. Wait for completion and retry.",
            plan_id=plan_id,
            task_id=task_id,
            dependency_plan=dep_response,
            job=None,
            result=None,
        )

    task_order = list(dep_plan.execution_order) or [task_id]

    if not request.async_mode:
        # Synchronous path (best-effort; may take a long time depending on LLM/tool calls).
        executed: List[int] = []
        failed: List[int] = []
        skipped: List[int] = []
        for tid in task_order:
            session_ctx = {
                "session_id": request.session_id,
                "user_message": (
                    f"Execute task chain for task #{task_id} "
                    f"(UI-triggered, deep_think={'on' if request.deep_think else 'off'})."
                ),
                "chat_history": [],
                "recent_tool_results": [],
                "deep_think_enabled": bool(request.deep_think),
                "paper_mode": bool(request.paper_mode),
            }
            exec_config = ExecutionConfig(session_context=session_ctx, paper_mode=bool(request.paper_mode))
            result = _plan_executor.execute_task(plan_id, tid, config=exec_config)
            if result.status == "completed":
                executed.append(tid)
                continue
            if result.status == "skipped":
                skipped.append(tid)
                break
            failed.append(tid)
            break

        return ExecuteTaskResponse(
            success=len(failed) == 0 and len(skipped) == 0,
            message="Execution completed successfully." if len(failed) == 0 and len(skipped) == 0 else "Execution finished with failures.",
            plan_id=plan_id,
            task_id=task_id,
            dependency_plan=dep_response,
            job=None,
            result={
                "execution_order": task_order,
                "executed_task_ids": executed,
                "failed_task_ids": failed,
                "skipped_task_ids": skipped,
            },
        )

    owner_id = get_request_owner_id(raw_request)
    job = plan_decomposition_jobs.create_job(
        plan_id=plan_id,
        task_id=task_id,
        mode="task_chain",
        job_type="plan_execute",
        owner_id=owner_id,
        session_id=request.session_id,
        params={
            "include_dependencies": request.include_dependencies,
            "include_subtasks": request.include_subtasks,
            "deep_think": request.deep_think,
            "paper_mode": request.paper_mode,
            "steps": len(task_order),
        },
        metadata={
            "session_id": request.session_id,
            "plan_id": plan_id,
            "plan_title": tree.title,
            "target_task_id": task_id,
            "target_task_name": tree.nodes[task_id].display_name(),
        },
    )
    plan_decomposition_jobs.append_log(
        job.job_id,
        "info",
        "Task execution has been queued in background.",
        {
            "plan_id": plan_id,
            "task_id": task_id,
            "job_type": job.job_type,
            "mode": job.mode,
            "steps": len(task_order),
            "deep_think": request.deep_think,
            "paper_mode": request.paper_mode,
        },
    )

    # Prevent duplicate concurrent execution of the same plan+task.
    lock_key = (plan_id, task_id)
    with _task_execution_locks_guard:
        if lock_key in _task_execution_locks and _task_execution_locks[lock_key].locked():
            return ExecuteTaskResponse(
                success=False,
                message=f"Task {task_id} in plan {plan_id} is already being executed. Please wait for it to finish.",
                plan_id=plan_id,
                task_id=task_id,
                dependency_plan=dep_response,
                job=None,
                result=None,
            )
        if lock_key not in _task_execution_locks:
            _task_execution_locks[lock_key] = threading.Lock()
        execution_lock = _task_execution_locks[lock_key]

    def _locked_run(**kwargs):
        with execution_lock:
            try:
                _run_task_chain_job(**kwargs)
            finally:
                with _task_execution_locks_guard:
                    _task_execution_locks.pop(lock_key, None)

    thread = threading.Thread(
        target=_locked_run,
        kwargs={
            "job_id": job.job_id,
            "plan_id": plan_id,
            "target_task_id": task_id,
            "task_order": task_order,
            "deep_think": request.deep_think,
            "session_id": request.session_id,
            "paper_mode": request.paper_mode,
        },
        daemon=True,
    )
    thread.start()

    return ExecuteTaskResponse(
        success=True,
        message="Task execution started in background.",
        plan_id=plan_id,
        task_id=task_id,
        dependency_plan=dep_response,
        job=job.to_payload(),
        result={"job_id": job.job_id, "status": job.status},
    )


@plan_router.get(
    "/{plan_id}/execution/summary",
    response_model=PlanExecutionSummary,
    summary="Get plan execution status summary",
)
def get_plan_execution_summary(plan_id: int, request: Request):
    try:
        tree = _load_authorized_plan_tree(plan_id, request)
        state_by_task = _resolve_effective_task_states(plan_id, tree)
    except sqlite3.OperationalError as exc:
        if "database is locked" not in str(exc).lower():
            raise
        logger.warning(
            "Plan %s execution summary unavailable because plan database is locked; "
            "returning active execution snapshot.",
            plan_id,
        )
        snapshot = _build_plan_execution_snapshot(plan_id)
        running = len(snapshot.get("active_task_ids") or [])
        return PlanExecutionSummary(
            plan_id=plan_id,
            total_tasks=0,
            completed=0,
            failed=0,
            skipped=0,
            blocked=0,
            running=running,
            pending=0,
        )

    total = tree.node_count()
    status_counts = {
        "completed": 0,
        "failed": 0,
        "skipped": 0,
        "blocked": 0,
        "running": 0,
        "pending": 0,
    }
    for node in tree.nodes.values():
        st = str((state_by_task.get(node.id) or {}).get("effective_status") or "pending").lower()
        if st in status_counts:
            status_counts[st] += 1
        else:
            status_counts["pending"] += 1
    return PlanExecutionSummary(
        plan_id=plan_id,
        total_tasks=total,
        completed=status_counts["completed"],
        failed=status_counts["failed"],
        skipped=status_counts["skipped"],
        blocked=status_counts["blocked"],
        running=status_counts["running"],
        pending=status_counts["pending"],
    )


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


class ExecuteFullPlanResponse(BaseModel):
    success: bool
    message: str
    plan_id: int
    todo_list: Optional[Dict[str, Any]] = None
    job: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None


def _todo_list_to_dict(
    todo: Any,
    plan_id: int,
    *,
    state_by_task: Optional[Dict[int, Dict[str, Any]]] = None,
    tree: Optional["PlanTree"] = None,
) -> Dict[str, Any]:
    if tree is None:
        tree = _plan_repo.get_plan_tree(plan_id)
    state_by_task = state_by_task or _resolve_effective_task_states(plan_id, tree)
    phases_out = []
    for phase in todo.phases:
        phases_out.append({
            "phase_id": phase.phase_id,
            "label": phase.label,
            "status": _todo_phase_status_from_effective(phase, state_by_task),
            "total": phase.total,
            "completed": _todo_completed_count_from_effective(phase, state_by_task),
            "items": [
                {
                    "task_id": item.task_id,
                    "name": item.name,
                    "instruction": item.instruction,
                    "status": str((state_by_task.get(item.task_id) or {}).get("effective_status") or "pending"),
                    **_effective_response_fields(state_by_task.get(item.task_id)),
                    "dependencies": item.dependencies,
                    "phase": item.phase,
                }
                for item in phase.items
            ],
        })
    return {
        "plan_id": plan_id,
        "target_task_id": todo.target_task_id,
        "total_tasks": todo.total_tasks,
        "completed_tasks": sum(
            1
            for phase in todo.phases
            for item in phase.items
            if str((state_by_task.get(item.task_id) or {}).get("effective_status") or "") == "completed"
        ),
        "phases": phases_out,
        "execution_order": todo.execution_order,
        "pending_order": _todo_pending_order_from_effective(todo, state_by_task, tree=tree),
        "summary": _todo_summary_from_effective(todo, state_by_task),
    }


def _acquire_plan_execution_lock(plan_id: int, task_id: int = 0) -> Optional[threading.Lock]:
    """Acquire a non-blocking execution lock for a plan/task scope."""
    lock_key = (plan_id, task_id)
    with _task_execution_locks_guard:
        execution_lock = _task_execution_locks.get(lock_key)
        if execution_lock is None:
            execution_lock = threading.Lock()
            _task_execution_locks[lock_key] = execution_lock
        if not execution_lock.acquire(blocking=False):
            return None
        return execution_lock


def _release_plan_execution_lock(
    plan_id: int,
    task_id: int,
    execution_lock: threading.Lock,
) -> None:
    """Release and remove a previously acquired execution lock."""
    lock_key = (plan_id, task_id)
    with _task_execution_locks_guard:
        try:
            execution_lock.release()
        except RuntimeError:
            pass
        if _task_execution_locks.get(lock_key) is execution_lock:
            _task_execution_locks.pop(lock_key, None)


@plan_router.get(
    "/{plan_id}/full-todo-list",
    response_model=TodoListResponse,
    summary="Get phased todo-list for the entire plan",
)
def get_full_plan_todo_list(
    plan_id: int,
    request: Request,
    expand_composites: bool = Query(True, description="Expand composite tasks to atomic leaves"),
):
    """Build a phased TodoList covering ALL tasks in the plan tree.

    Unlike the per-task ``/todo-list`` endpoint which resolves dependencies
    of a single target task, this computes topological phase layers for every
    node in the plan (or their atomic leaf descendants).
    """
    tree = _load_authorized_plan_tree(plan_id, request)
    todo = _build_full_plan_todo_list(tree, expand_composites=expand_composites)
    state_by_task = _resolve_effective_task_states(plan_id, tree)
    todo_payload = _todo_list_to_dict(todo, plan_id, state_by_task=state_by_task, tree=tree)
    return TodoListResponse(**todo_payload)


@plan_router.get(
    "/{plan_id}/active-job",
    summary="Get the latest active plan_execute job for a plan",
)
def get_plan_active_job(plan_id: int, request: Request):
    """Return the latest running or queued plan_execute job for the given plan.

    Returns null if no active job exists.
    """
    job_ids = _list_plan_execute_job_ids(plan_id, limit=5)
    for job_id in job_ids:
        payload = plan_decomposition_jobs.get_job_payload(job_id, include_logs=False)
        if isinstance(payload, dict) and payload.get("status") in ("running", "queued"):
            return {"job_id": job_id, "status": payload.get("status"), "plan_id": plan_id}
    return {"job_id": None, "status": None, "plan_id": plan_id}


@plan_router.post(
    "/{plan_id}/execute-full",
    response_model=ExecuteFullPlanResponse,
    summary="Execute entire plan via phased TodoList",
)
def execute_full_plan(
    plan_id: int,
    raw_request: Request,
    request: Optional[ExecuteFullPlanRequest] = Body(default=None),
):
    """Execute all pending tasks in the plan, ordered by TodoList phases.

    This is the primary "auto-execute entire plan" endpoint. It:
    1. Builds a full-plan TodoList with topological phase layers
    2. Filters to only pending tasks (unless *skip_completed* is False)
    3. Executes tasks phase-by-phase in dependency order
    """
    request = request or ExecuteFullPlanRequest()
    tree = _load_authorized_plan_tree(plan_id, raw_request)
    state_by_task = _resolve_effective_task_states(plan_id, tree)

    # --- Artifact dependency enrichment ---
    try:
        _enrichment_result = enrich_plan_dependencies(tree)
        if _enrichment_result.added_edges:
            for _enode in tree.iter_nodes():
                try:
                    _plan_repo.update_task(plan_id, _enode.id, dependencies=list(_enode.dependencies))
                except Exception:
                    pass
            logging.getLogger("app.routers.plan_routes").info(
                "Enriched plan %s with %d implicit dependency edges.",
                plan_id, len(_enrichment_result.added_edges),
            )
        _dag_validation = validate_plan_dag(tree)
        if _dag_validation.has_errors():
            return ExecuteFullPlanResponse(
                success=False,
                message=_dag_validation.summary(),
                plan_id=plan_id,
            )
    except Exception as _enrich_exc:
        logging.getLogger("app.routers.plan_routes").warning(
            "Dependency enrichment failed (continuing with original graph): %s", _enrich_exc
        )

    todo = _build_full_plan_todo_list(tree, expand_composites=True)
    todo_dict = _todo_list_to_dict(todo, plan_id, state_by_task=state_by_task, tree=tree)
    task_order = list(todo_dict.get("execution_order") or [])
    if request.skip_completed:
        task_order = list(todo_dict.get("pending_order") or [])

    preflight = _artifact_preflight_service.validate_plan(plan_id, tree)
    if preflight.has_errors():
        return ExecuteFullPlanResponse(
            success=False,
            message=preflight.summary(),
            plan_id=plan_id,
            todo_list=todo_dict,
            result=_artifact_preflight_failure_payload(preflight),
        )

    if not task_order:
        has_running = any(
            str((state_by_task.get(item.task_id) or {}).get("effective_status") or "") == "running"
            for phase in todo.phases
            for item in phase.items
        )
        return ExecuteFullPlanResponse(
            success=True,
            message=(
                "No runnable tasks remain; unfinished work is already running."
                if has_running
                else "All tasks are already completed."
            ),
            plan_id=plan_id,
            todo_list=todo_dict,
        )

    if not request.async_mode:
        executed: List[int] = []
        failed: List[int] = []
        skipped: List[int] = []
        for tid in task_order:
            try:
                current_tree = _plan_repo.get_plan_tree(plan_id)

                # Safety guard: skip composite parent tasks — only leaf tasks should be executed
                if current_tree.children_ids(tid):
                    executed.append(tid)
                    continue

                current_state_by_task = _resolve_effective_task_states(plan_id, current_tree)
                current_status = str(
                    (current_state_by_task.get(tid) or {}).get("effective_status") or "pending"
                ).strip().lower()
                if current_status in ("completed", "running"):
                    executed.append(tid)
                    continue
                dependency_block = _build_dependency_block_details(
                    current_tree,
                    tid,
                    state_by_task=current_state_by_task,
                )
                if dependency_block is not None:
                    _persist_dependency_block(plan_id, tid, dependency_block)
                    skipped.append(tid)
                    if request.stop_on_failure:
                        break
                    continue
            except Exception:
                pass
            session_ctx = {
                "session_id": request.session_id,
                "user_message": f"Execute full plan (plan_id={plan_id}), task #{tid}.",
                "chat_history": [],
                "recent_tool_results": [],
                "deep_think_enabled": bool(request.deep_think),
                "paper_mode": bool(request.paper_mode),
            }
            exec_config = ExecutionConfig(
                session_context=session_ctx,
                paper_mode=bool(request.paper_mode),
            )
            try:
                result = _plan_executor.execute_task(plan_id, tid, config=exec_config)
            except Exception as exc:
                failed.append(tid)
                if request.stop_on_failure:
                    break
                continue

            if result.status == "completed":
                executed.append(tid)
            elif result.status == "skipped":
                skipped.append(tid)
                if request.stop_on_failure:
                    break
            else:
                failed.append(tid)
                if request.stop_on_failure:
                    break

        success = len(failed) == 0 and len(skipped) == 0
        if success:
            message = "Full plan execution completed successfully."
        elif skipped and not failed:
            message = (
                f"Full plan execution finished: {len(executed)} done, "
                f"{len(skipped)} blocked/skipped."
            )
        else:
            message = (
                f"Full plan execution finished: {len(executed)} done, "
                f"{len(failed)} failed, {len(skipped)} skipped."
            )
        return ExecuteFullPlanResponse(
            success=success,
            message=message,
            plan_id=plan_id,
            todo_list=todo_dict,
            result={
                "execution_order": task_order,
                "executed_task_ids": executed,
                "failed_task_ids": failed,
                "skipped_task_ids": skipped,
            },
        )

    execution_lock = _acquire_plan_execution_lock(plan_id, 0)
    if execution_lock is None:
        return ExecuteFullPlanResponse(
            success=False,
            message=f"Plan {plan_id} is already being executed. Please wait.",
            plan_id=plan_id,
            todo_list=todo_dict,
        )

    owner_id = get_request_owner_id(raw_request)
    job: Optional[Any] = None
    initial_completed_steps = int(todo_dict.get("completed_tasks") or 0) if request.skip_completed else 0
    overall_total_steps = int(todo_dict.get("total_tasks") or 0)
    try:
        job = plan_decomposition_jobs.create_job(
            plan_id=plan_id,
            task_id=None,
            mode="full_plan",
            job_type="plan_execute",
            owner_id=owner_id,
            session_id=request.session_id,
            params={
                "include_dependencies": True,
                "include_subtasks": True,
                "deep_think": request.deep_think,
                "paper_mode": request.paper_mode,
                "steps": len(task_order),
                "overall_total_steps": overall_total_steps,
                "initial_completed_steps": initial_completed_steps,
                "stop_on_failure": request.stop_on_failure,
            },
            metadata={
                "session_id": request.session_id,
                "plan_id": plan_id,
                "plan_title": tree.title,
                "target_task_id": None,
                "task_order": task_order,
                "todo_phases": len(todo.phases),
                "todo_total_tasks": overall_total_steps,
                "todo_completed_tasks": initial_completed_steps,
            },
        )
        plan_decomposition_jobs.append_log(
            job.job_id,
            "info",
            "Full plan execution queued in background.",
            {
                "plan_id": plan_id,
                "job_type": job.job_type,
                "steps": len(task_order),
                "overall_total_steps": overall_total_steps,
                "initial_completed_steps": initial_completed_steps,
                "phases": len(todo.phases),
                "deep_think": request.deep_think,
                "paper_mode": request.paper_mode,
                "stop_on_failure": request.stop_on_failure,
            },
        )

        def _locked_run(**kwargs):
            try:
                _run_full_plan_job(**kwargs)
            finally:
                _release_plan_execution_lock(plan_id, 0, execution_lock)

        thread = threading.Thread(
            target=_locked_run,
            kwargs={
                "job_id": job.job_id,
                "plan_id": plan_id,
                "task_order": task_order,
                "initial_completed_steps": initial_completed_steps,
                "overall_total_steps": overall_total_steps,
                "deep_think": request.deep_think,
                "session_id": request.session_id,
                "paper_mode": request.paper_mode,
                "stop_on_failure": request.stop_on_failure,
            },
            daemon=True,
        )
        thread.start()
    except Exception as exc:
        if job is not None:
            try:
                plan_decomposition_jobs.mark_failure(
                    job.job_id,
                    f"Failed to start full plan execution: {exc}",
                    result={
                        "plan_id": plan_id,
                        "execution_order": task_order,
                        "executed_task_ids": [],
                        "failed_task_ids": [],
                        "skipped_task_ids": [],
                        "steps": [],
                    },
                )
            except Exception:
                pass
        _release_plan_execution_lock(plan_id, 0, execution_lock)
        raise

    return ExecuteFullPlanResponse(
        success=True,
        message="Full plan execution started in background.",
        plan_id=plan_id,
        todo_list=todo_dict,
        job=job.to_payload(),
        result={"job_id": job.job_id, "status": job.status},
    )


def _run_full_plan_job(
    *,
    job_id: str,
    plan_id: int,
    task_order: List[int],
    initial_completed_steps: int = 0,
    overall_total_steps: Optional[int] = None,
    deep_think: bool = True,
    session_id: Optional[str] = None,
    paper_mode: bool = False,
    stop_on_failure: bool = True,
) -> None:
    """Background execution wrapper for full-plan TodoList-based execution."""
    token = set_current_job(job_id)
    executed: List[int] = []
    failed: List[int] = []
    skipped: List[int] = []
    step_summaries: List[Dict[str, Any]] = []
    total_steps = len(task_order)
    baseline_completed_steps = max(0, int(initial_completed_steps or 0))
    overall_steps = max(total_steps, int(overall_total_steps or 0), baseline_completed_steps)
    completed_steps = 0

    def _build_progress_stats(
        *,
        current_step: Optional[int] = None,
        current_task_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        done_steps = len(executed) + len(failed) + len(skipped)
        overall_done_steps = min(overall_steps, baseline_completed_steps + completed_steps)
        progress_percent = 0
        if overall_steps > 0:
            progress_percent = int(round((overall_done_steps / overall_steps) * 100))
            if overall_done_steps < overall_steps:
                progress_percent = max(0, min(99, progress_percent))
            else:
                progress_percent = 100
        stats_payload: Dict[str, Any] = {
            "executed": len(executed),
            "failed": len(failed),
            "skipped": len(skipped),
            "done": done_steps,
            "total_steps": total_steps,
            "overall_done_steps": overall_done_steps,
            "overall_total_steps": overall_steps,
            "progress_percent": progress_percent,
        }
        if current_step is not None:
            stats_payload["current_step"] = current_step
        if current_task_id is not None:
            stats_payload["current_task_id"] = current_task_id
        return stats_payload

    def _publish_progress(
        *,
        current_step: Optional[int] = None,
        current_task_id: Optional[int] = None,
    ) -> None:
        plan_decomposition_jobs.update_stats(job_id, _build_progress_stats(
            current_step=current_step, current_task_id=current_task_id,
        ))
        log_job_event(
            "info",
            "Full plan progress update.",
            {
                "sub_type": "task_progress",
                "task_id": current_task_id,
                "step": current_step,
                "total": total_steps,
            },
        )

    try:
        plan_decomposition_jobs.mark_running(job_id)
        log_job_event(
            "info",
            "Full plan execution started.",
            {
                "plan_id": plan_id,
                "steps": len(task_order),
                "overall_total_steps": overall_steps,
                "initial_completed_steps": baseline_completed_steps,
                "task_order": task_order,
                "deep_think": deep_think,
                "paper_mode": paper_mode,
                "stop_on_failure": stop_on_failure,
            },
        )
        _publish_progress(current_step=0, current_task_id=task_order[0] if task_order else None)

        session_ctx = {
            "session_id": session_id,
            "user_message": f"Execute full plan (plan_id={plan_id}, deep_think={'on' if deep_think else 'off'}).",
            "chat_history": [],
            "recent_tool_results": [],
            "deep_think_enabled": bool(deep_think),
            "paper_mode": bool(paper_mode),
        }

        for idx, task_id in enumerate(task_order, start=1):
            log_job_event(
                "info",
                "Executing plan step.",
                {"plan_id": plan_id, "step": idx, "total_steps": total_steps, "task_id": task_id},
            )
            _publish_progress(current_step=idx, current_task_id=task_id)

            # Re-check task status at execution time so background runs do not
            # re-execute work that finished after the queue was created.
            try:
                current_tree = _plan_repo.get_plan_tree(plan_id)

                # Safety guard: skip composite parent tasks — only leaf tasks should be executed
                if current_tree.children_ids(task_id):
                    log_job_event(
                        "info",
                        "Skipping composite parent task.",
                        {"plan_id": plan_id, "task_id": task_id, "step": idx, "reason": "has_children"},
                    )
                    executed.append(task_id)
                    completed_steps += 1
                    step_summaries.append({"task_id": task_id, "status": "composite_skipped", "duration_sec": 0.0})
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    continue

                current_state_by_task = _resolve_effective_task_states(
                    plan_id,
                    current_tree,
                    snapshot=_build_plan_execution_snapshot(
                        plan_id,
                        exclude_job_ids={job_id},
                    ),
                )
                current_status = str(
                    (current_state_by_task.get(task_id) or {}).get("effective_status") or "pending"
                ).strip().lower()
                if current_status in ("completed", "running"):
                    already_status = "already_running" if current_status == "running" else "already_completed"
                    executed.append(task_id)
                    if current_status != "running":
                        completed_steps += 1
                    step_summaries.append(
                        {
                            "task_id": task_id,
                            "status": already_status,
                            "duration_sec": 0.0,
                        }
                    )
                    log_job_event(
                        "info",
                        (
                            "Plan step already running; skipping execution."
                            if current_status == "running"
                            else "Plan step already completed; skipping execution."
                        ),
                        {
                            "plan_id": plan_id,
                            "task_id": task_id,
                            "step": idx,
                            "status": current_status,
                        },
                    )
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    continue
                dependency_block = _build_dependency_block_details(
                    current_tree,
                    task_id,
                    state_by_task=current_state_by_task,
                )
                if dependency_block is not None:
                    _persist_dependency_block(plan_id, task_id, dependency_block)
                    skipped.append(task_id)
                    step_summaries.append(
                        {
                            "task_id": task_id,
                            "status": "blocked_by_dependencies",
                            "duration_sec": 0.0,
                            "reason": dependency_block["reason"],
                            "metadata": dependency_block["metadata"],
                        }
                    )
                    log_job_event(
                        "warning",
                        "Plan step blocked by incomplete dependencies.",
                        {
                            "plan_id": plan_id,
                            "task_id": task_id,
                            "step": idx,
                            "incomplete_dependencies": dependency_block["metadata"]["incomplete_dependencies"],
                            "reason": dependency_block["reason"],
                        },
                    )
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    if stop_on_failure:
                        error = f"Task #{task_id} was blocked by incomplete dependencies; stopping chain."
                        plan_decomposition_jobs.mark_failure(
                            job_id,
                            error,
                            result={
                                "plan_id": plan_id,
                                "execution_order": task_order,
                                "executed_task_ids": executed,
                                "failed_task_ids": failed,
                                "skipped_task_ids": skipped,
                                "steps": step_summaries,
                            },
                            stats=_build_progress_stats(current_step=idx, current_task_id=task_id),
                        )
                        return
                    continue
            except Exception as exc:
                # Transient tree-refresh failure: retry once after a short
                # backoff before giving up on this step.  This avoids burning
                # through the queue on a momentary DB hiccup.
                import time as _time
                _time.sleep(1.0)
                try:
                    current_tree = _plan_repo.get_plan_tree(plan_id)
                    log_job_event(
                        "info",
                        "Plan tree refresh succeeded on retry.",
                        {"plan_id": plan_id, "task_id": task_id, "step": idx},
                    )
                except Exception as retry_exc:
                    log_job_event(
                        "error",
                        "Plan tree refresh failed on retry; aborting remaining steps.",
                        {"plan_id": plan_id, "task_id": task_id, "step": idx, "error": str(retry_exc)},
                    )
                    failed.append(task_id)
                    step_summaries.append({
                        "task_id": task_id,
                        "status": "tree_refresh_failed",
                        "duration_sec": 0.0,
                        "error": str(retry_exc),
                    })
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    # Without a valid tree, dependency/completion checks for
                    # subsequent tasks are unreliable.  Abort the entire job
                    # regardless of stop_on_failure to avoid cascading skips.
                    error = (
                        f"Task #{task_id}: plan tree refresh failed after retry ({retry_exc}); "
                        "aborting job — remaining tasks cannot be safely checked."
                    )
                    plan_decomposition_jobs.mark_failure(
                        job_id,
                        error,
                        result={
                            "plan_id": plan_id,
                            "execution_order": task_order,
                            "executed_task_ids": executed,
                            "failed_task_ids": failed,
                            "skipped_task_ids": skipped,
                            "steps": step_summaries,
                        },
                        stats=_build_progress_stats(current_step=idx, current_task_id=task_id),
                    )
                    return

                # Retry succeeded — re-run the same state checks that the
                # primary try block performs (composite parent, already
                # completed/running, dependency-blocked).
                if current_tree.children_ids(task_id):
                    log_job_event(
                        "info",
                        "Skipping composite parent task (after retry).",
                        {"plan_id": plan_id, "task_id": task_id, "step": idx, "reason": "has_children"},
                    )
                    executed.append(task_id)
                    completed_steps += 1
                    step_summaries.append({"task_id": task_id, "status": "composite_skipped", "duration_sec": 0.0})
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    continue

                current_state_by_task = _resolve_effective_task_states(
                    plan_id,
                    current_tree,
                    snapshot=_build_plan_execution_snapshot(plan_id, exclude_job_ids={job_id}),
                )
                current_status = str(
                    (current_state_by_task.get(task_id) or {}).get("effective_status") or "pending"
                ).strip().lower()
                if current_status in ("completed", "running"):
                    already_status = "already_running" if current_status == "running" else "already_completed"
                    executed.append(task_id)
                    if current_status != "running":
                        completed_steps += 1
                    step_summaries.append({"task_id": task_id, "status": already_status, "duration_sec": 0.0})
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    continue

                dependency_block = _build_dependency_block_details(
                    current_tree, task_id, state_by_task=current_state_by_task,
                )
                if dependency_block is not None:
                    _persist_dependency_block(plan_id, task_id, dependency_block)
                    skipped.append(task_id)
                    step_summaries.append({
                        "task_id": task_id,
                        "status": "blocked_by_dependencies",
                        "duration_sec": 0.0,
                        "reason": dependency_block["reason"],
                        "metadata": dependency_block["metadata"],
                    })
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    if stop_on_failure:
                        error = f"Task #{task_id} was blocked by incomplete dependencies; stopping chain."
                        plan_decomposition_jobs.mark_failure(
                            job_id, error,
                            result={
                                "plan_id": plan_id, "execution_order": task_order,
                                "executed_task_ids": executed, "failed_task_ids": failed,
                                "skipped_task_ids": skipped, "steps": step_summaries,
                            },
                            stats=_build_progress_stats(current_step=idx, current_task_id=task_id),
                        )
                        return
                    continue

            try:
                # --- Artifact readiness guard ---
                _readiness_block = check_artifact_readiness(
                    current_tree.nodes.get(task_id),
                    current_tree,
                    manifest=None,
                )
                if _readiness_block is not None:
                    log_job_event(
                        "warning",
                        "Task blocked by missing input artifacts.",
                        {"task_id": task_id, "step": idx, "reason": _readiness_block.reason},
                    )
                    skipped.append(task_id)
                    step_summaries.append({
                        "task_id": task_id,
                        "status": "missing_input_artifact",
                        "duration_sec": 0.0,
                        "reason": _readiness_block.reason,
                    })
                    _publish_progress(current_step=idx, current_task_id=task_id)
                    continue

                exec_config = ExecutionConfig(session_context=session_ctx, paper_mode=bool(paper_mode))
                result = _plan_executor.execute_task(plan_id, task_id, config=exec_config)
            except Exception as exc:
                failed.append(task_id)
                step_summaries.append(
                    {
                        "task_id": task_id,
                        "status": "exception",
                        "duration_sec": None,
                        "error": str(exc),
                    }
                )
                _publish_progress(current_step=idx, current_task_id=task_id)
                error = f"Task #{task_id} raised an exception: {exc}"
                if stop_on_failure:
                    log_job_event("error", "Plan step raised exception.", {"task_id": task_id, "error": str(exc)})
                    plan_decomposition_jobs.mark_failure(
                        job_id, error,
                        result={"plan_id": plan_id, "execution_order": task_order, "executed_task_ids": executed, "failed_task_ids": failed, "skipped_task_ids": skipped, "steps": step_summaries},
                        stats=_build_progress_stats(current_step=idx, current_task_id=task_id),
                    )
                    return
                log_job_event(
                    "warning",
                    "Plan step raised exception; continuing.",
                    {"task_id": task_id, "error": str(exc)},
                )
                continue

            step_summaries.append({"task_id": task_id, "status": result.status, "duration_sec": result.duration_sec})

            log_job_event(
                "info" if result.status == "completed" else "warning" if result.status == "skipped" else "error",
                f"Plan step completed: task #{task_id}",
                {
                    "sub_type": "step_complete",
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "step": idx,
                    "total_steps": total_steps,
                    "status": result.status,
                    "duration_sec": result.duration_sec,
                },
            )

            if result.status == "completed":
                executed.append(task_id)
                completed_steps += 1
                _publish_progress(current_step=idx, current_task_id=task_id)
                continue
            if result.status == "skipped":
                skipped.append(task_id)
                _publish_progress(current_step=idx, current_task_id=task_id)
                if stop_on_failure:
                    error = f"Task #{task_id} was skipped; stopping chain."
                    log_job_event("warning", "Plan step skipped; stopping.", {"task_id": task_id, "reason": result.content})
                    plan_decomposition_jobs.mark_failure(
                        job_id, error,
                        result={"plan_id": plan_id, "execution_order": task_order, "executed_task_ids": executed, "failed_task_ids": failed, "skipped_task_ids": skipped, "steps": step_summaries},
                        stats=_build_progress_stats(current_step=idx, current_task_id=task_id),
                    )
                    return
                continue

            failed.append(task_id)
            _publish_progress(current_step=idx, current_task_id=task_id)
            if stop_on_failure:
                error = f"Task #{task_id} failed; stopping chain."
                log_job_event("error", "Plan step failed; stopping.", {"task_id": task_id, "reason": result.content})
                plan_decomposition_jobs.mark_failure(
                    job_id, error,
                    result={"plan_id": plan_id, "execution_order": task_order, "executed_task_ids": executed, "failed_task_ids": failed, "skipped_task_ids": skipped, "steps": step_summaries},
                    stats=_build_progress_stats(current_step=idx, current_task_id=task_id),
                )
                return
            log_job_event("warning", "Plan step failed; continuing.", {"task_id": task_id, "reason": result.content})

        _publish_progress(current_step=total_steps, current_task_id=task_order[-1] if task_order else None)
        final_result = {
            "plan_id": plan_id,
            "execution_order": task_order,
            "executed_task_ids": executed,
            "failed_task_ids": failed,
            "skipped_task_ids": skipped,
            "steps": step_summaries,
        }
        final_stats = _build_progress_stats(
            current_step=total_steps,
            current_task_id=task_order[-1] if task_order else None,
        )
        if failed or skipped:
            plan_decomposition_jobs.mark_failure(
                job_id,
                f"Full plan execution finished with {len(failed)} failed and {len(skipped)} skipped task(s).",
                result=final_result,
                stats=final_stats,
            )
        else:
            plan_decomposition_jobs.mark_success(
                job_id,
                result=final_result,
                stats=final_stats,
            )
    finally:
        reset_current_job(token)


@plan_router.get(
    "/{plan_id}/todo-list",
    response_model=TodoListResponse,
    summary="Get phased todo-list for a target task",
)
def get_plan_todo_list(
    plan_id: int,
    request: Request,
    target_task_id: int = Query(..., description="Target task whose dependency subgraph to resolve"),
    expand_composites: bool = Query(True, description="Expand composite tasks to atomic leaves"),
):
    """Build a phased todo-list for *target_task_id* showing all dependencies
    grouped into execution phases with semantic labels."""
    tree = _load_authorized_plan_tree(plan_id, request)

    if not tree.has_node(target_task_id):
        raise HTTPException(
            status_code=404,
            detail=f"Task {target_task_id} not found in plan {plan_id}",
        )

    todo = _build_todo_list(
        tree,
        target_task_id,
        include_target=True,
        expand_composites=expand_composites,
    )
    state_by_task = _resolve_effective_task_states(plan_id, tree)
    todo_payload = _todo_list_to_dict(todo, plan_id, state_by_task=state_by_task, tree=tree)
    return TodoListResponse(**todo_payload)


@plan_router.get(
    "/{plan_id}/subgraph",
    response_model=SubgraphResponse,
    summary="Get plan subgraph",
)
def get_plan_subgraph(
    plan_id: int,
    request: Request,
    node_id: int = Query(..., description="Root node ID"),
    max_depth: int = Query(2, ge=1, le=6, description="Traversal depth limit"),
):
    tree = _load_authorized_plan_tree(plan_id, request)

    if not tree.has_node(node_id):
        raise HTTPException(
            status_code=404,
            detail=f"Node {node_id} not found in plan {plan_id}",
        )
    nodes = tree.subgraph_nodes(node_id, max_depth=max_depth)
    outline = tree.subgraph_outline(node_id, max_depth=max_depth)
    return SubgraphResponse(
        plan_id=plan_id,
        root_node=node_id,
        max_depth=max_depth,
        outline=outline,
        nodes=[node.model_dump() for node in nodes],
    )


@task_router.post(
    "/{task_id}/decompose",
    response_model=DecomposeTaskResponse,
    summary="Decompose task with LLM",
)
def decompose_task(
    task_id: int,
    background_tasks: BackgroundTasks,
    raw_request: Request,
    request: DecomposeTaskRequest = Body(...),
):
    plan_id = request.plan_id
    tree = _load_authorized_plan_tree(plan_id, raw_request)

    if not tree.has_node(task_id):
        raise HTTPException(
            status_code=404,
            detail=f"Task {task_id} not found in plan {plan_id}",
        )

    expand_depth = request.expand_depth
    node_budget = request.node_budget
    allow_existing_children = request.allow_existing_children

    if request.async_mode:
        owner_id = get_request_owner_id(raw_request)
        job = plan_decomposition_jobs.create_job(
            plan_id=plan_id,
            task_id=task_id,
            mode="single_node",
            owner_id=owner_id,
            params={
                "expand_depth": expand_depth,
                "node_budget": node_budget,
                "allow_existing_children": allow_existing_children,
            },
        )
        if background_tasks is None:
            raise HTTPException(
                status_code=500, detail="Background task manager is unavailable; cannot enqueue decomposition."
            )
        plan_decomposition_jobs.append_log(
            job.job_id,
            "info",
            "Task decomposition has been queued in background.",
            {
                "plan_id": plan_id,
                "task_id": task_id,
                "expand_depth": expand_depth,
                "node_budget": node_budget,
                "allow_existing_children": allow_existing_children,
            },
        )
        background_tasks.add_task(
            _run_decomposition_job,
            job.job_id,
            plan_id,
            task_id,
            expand_depth,
            node_budget,
            allow_existing_children,
        )
        message = (
            "Task decomposition started in background. Poll job status to track progress."
        )
        payload = job.to_payload()
        return DecomposeTaskResponse(
            success=True,
            message=message,
            result={"job_id": job.job_id, "status": job.status},
            job=payload,
        )

    try:
        result: DecompositionResult = _plan_decomposer.decompose_node(
            plan_id,
            task_id,
            expand_depth=expand_depth,
            node_budget=node_budget,
            allow_existing_children=allow_existing_children,
        )
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    message = (
        f"Created {len(result.created_tasks)} subtasks."
        if result.created_tasks
        else "Decomposition completed with no new tasks."
    )
    if result.stopped_reason:
        message += f" Reason: {result.stopped_reason}"

    return DecomposeTaskResponse(
        success=True,
        message=message,
        result=result.model_dump(),
        job=None,
    )


@task_router.get(
    "/decompose/jobs/{job_id}/stream",
    summary="Stream decomposition job logs",
)
async def stream_decomposition_job(job_id: str, request: Request):
    snapshot = plan_decomposition_jobs.get_job_payload(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Decomposition job not found.")
    ensure_owner_access(request, snapshot.get("owner_id"), detail="job owner mismatch")
    bus = await get_realtime_bus()
    subscription: EventSubscription = await bus.subscribe_job_events(job_id)

    async def event_generator() -> AsyncIterator[str]:
        try:
            yield _sse_message({"type": "snapshot", "job": snapshot})
            if _is_terminal_job_status(snapshot.get("status")):
                return
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await subscription.get(timeout=15.0)
                except asyncio.TimeoutError:
                    heartbeat = plan_decomposition_jobs.get_job_payload(job_id, include_logs=False)
                    if heartbeat is None:
                        break
                    if str(heartbeat.get("owner_id") or "legacy-local") != get_request_owner_id(request):
                        break
                    yield _sse_message({"type": "heartbeat", "job": heartbeat})
                    if _is_terminal_job_status(heartbeat.get("status")):
                        break
                    continue
                message.setdefault("type", "event")
                yield _sse_message(message)
                if _is_terminal_job_status(message.get("status")):
                    break
        except asyncio.CancelledError:  # pragma: no cover - defensive
            raise
        finally:
            await subscription.close()

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


@task_router.get(
    "/decompose/jobs/{job_id}",
    response_model=DecompositionJobStatusResponse,
    summary="Get decomposition job status",
)
def get_decomposition_job_status(job_id: str, request: Request):
    payload = plan_decomposition_jobs.get_job_payload(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Decomposition job not found.")
    ensure_owner_access(request, payload.get("owner_id"), detail="job owner mismatch")
    return DecompositionJobStatusResponse(
        job_id=payload.get("job_id"),
        job_type=payload.get("job_type") or "plan_decompose",
        status=payload.get("status"),
        plan_id=payload.get("plan_id"),
        task_id=payload.get("task_id"),
        mode=payload.get("mode"),
        result=payload.get("result"),
        stats=payload.get("stats") or {},
        params=payload.get("params") or {},
        metadata=payload.get("metadata") or {},
        error=payload.get("error"),
        created_at=payload.get("created_at"),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        logs=payload.get("logs", []),
    )


register_router(
    namespace="plans",
    version="v1",
    path="/plans",
    router=plan_router,
    tags=["plans"],
    description="Plan read and execution APIs",
)

register_router(
    namespace="tasks",
    version="v1",
    path="/tasks",
    router=task_router,
    tags=["tasks"],
    description="Task APIs backed by PlanTree",
)
