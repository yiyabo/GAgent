from __future__ import annotations

import asyncio
import json
import logging
import threading
import heapq
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.repository.plan_repository import PlanRepository
from app.services.plans.dependency_planner import DependencyPlan, compute_dependency_plan
from app.services.plans.plan_decomposer import PlanDecomposer, DecompositionResult
from app.services.plans.plan_executor import ExecutionConfig, PlanExecutor
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
logger = logging.getLogger(__name__)


def _sse_message(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


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
def list_plans():
    """Return plan summaries."""
    summaries = _plan_repo.list_plans()
    return [summary.model_dump() for summary in summaries]


class SubgraphResponse(BaseModel):
    plan_id: int
    root_node: int
    max_depth: int
    outline: str
    nodes: list[dict[str, Any]]


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
    content: Optional[str] = None
    notes: List[str] = []
    metadata: Dict[str, Any] = {}
    raw: Optional[Dict[str, Any]] = None


class PlanResultsResponse(BaseModel):
    plan_id: int
    total: int
    items: List[TaskResultItem]


class PlanExecutionSummary(BaseModel):
    plan_id: int
    total_tasks: int
    completed: int
    failed: int
    skipped: int
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


class DependencyNodeSummary(BaseModel):
    id: int
    name: str
    status: str


class DependencyPlanResponse(BaseModel):
    plan_id: int
    target_task_id: int
    satisfied_statuses: List[str] = Field(default_factory=list)
    direct_dependencies: List[int] = Field(default_factory=list)
    closure_dependencies: List[int] = Field(default_factory=list)
    missing_dependencies: List[DependencyNodeSummary] = Field(default_factory=list)
    running_dependencies: List[DependencyNodeSummary] = Field(default_factory=list)
    execution_order: List[int] = Field(default_factory=list)
    cycle_detected: bool = False
    cycle_paths: List[List[int]] = Field(default_factory=list)


def _to_dependency_plan_response(
    tree: "PlanTree",
    plan: DependencyPlan,
) -> DependencyPlanResponse:
    def _node_summary(task_id: int) -> DependencyNodeSummary:
        node = tree.nodes[task_id]
        return DependencyNodeSummary(id=node.id, name=node.display_name(), status=node.status)

    return DependencyPlanResponse(
        plan_id=plan.plan_id,
        target_task_id=plan.target_task_id,
        satisfied_statuses=list(plan.satisfied_statuses),
        direct_dependencies=list(plan.direct_dependencies),
        closure_dependencies=list(plan.closure_dependencies),
        missing_dependencies=[_node_summary(tid) for tid in plan.missing_dependencies],
        running_dependencies=[_node_summary(tid) for tid in plan.running_dependencies],
        execution_order=list(plan.execution_order),
        cycle_detected=plan.cycle_detected,
        cycle_paths=[list(path) for path in plan.cycle_paths],
    )


def _normalize_task_status(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


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
) -> DependencyPlan:
    base_plan = compute_dependency_plan(tree, target_task_id, include_target_in_order=False)
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
    missing: Set[int] = set(base_plan.missing_dependencies)
    running: Set[int] = set(base_plan.running_dependencies)
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

    to_run: Set[int] = set(subtree_set)
    if include_dependencies:
        for dep_id in closure:
            if dep_id not in tree.nodes:
                continue
            dep_status = _normalize_task_status(tree.nodes[dep_id].status)
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
                exec_config = ExecutionConfig(session_context=session_ctx)
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
def get_plan_tree(plan_id: int):
    """Return serialized PlanTree for the specified plan."""
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return tree.model_dump()


@plan_router.get(
    "/{plan_id}/results",
    response_model=PlanResultsResponse,
    summary="List plan execution results",
)
def get_plan_results(
    plan_id: int,
    only_with_output: bool = Query(True, description="Only include tasks with execution output"),
):
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    items: List[TaskResultItem] = []
    for node in tree.ordered_nodes():
        content, notes, metadata, raw_payload = _parse_execution_result(node.execution_result)
        if content is None and not notes and not metadata and only_with_output:
            continue
        items.append(
            TaskResultItem(
                task_id=node.id,
                name=node.name,
                status=node.status,
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
def get_task_result(task_id: int, plan_id: int = Query(..., description="plan ID")):
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not tree.has_node(task_id):
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in plan {plan_id}")
    node = tree.get_node(task_id)

    content, notes, metadata, raw_payload = _parse_execution_result(node.execution_result)

    return TaskResultItem(
        task_id=node.id,
        name=node.name,
        status=node.status,
        content=content,
        notes=notes,
        metadata=metadata,
        raw=raw_payload,
    )


@task_router.get(
    "/{task_id}/dependency-plan",
    response_model=DependencyPlanResponse,
    summary="Get task dependency plan",
)
def get_task_dependency_plan(
    task_id: int,
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
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not tree.has_node(task_id):
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in plan {plan_id}")

    plan = _build_execution_dependency_plan(
        tree,
        task_id,
        include_dependencies=bool(include_dependencies),
        include_subtasks=bool(include_subtasks),
    )
    return _to_dependency_plan_response(tree, plan)


@task_router.post(
    "/{task_id}/execute",
    response_model=ExecuteTaskResponse,
    summary="Execute task with dependencies",
)
def execute_task_with_dependencies(
    task_id: int,
    plan_id: int = Query(..., description="plan ID"),
    request: Optional[ExecuteTaskRequest] = Body(default=None),
):
    request = request or ExecuteTaskRequest()
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not tree.has_node(task_id):
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in plan {plan_id}")

    dep_plan = _build_execution_dependency_plan(
        tree,
        task_id,
        include_dependencies=bool(request.include_dependencies),
        include_subtasks=bool(request.include_subtasks),
    )
    dep_response = _to_dependency_plan_response(tree, dep_plan)

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

    if request.include_dependencies and dep_plan.running_dependencies:
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
            }
            exec_config = ExecutionConfig(session_context=session_ctx)
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

    job = plan_decomposition_jobs.create_job(
        plan_id=plan_id,
        task_id=task_id,
        mode="task_chain",
        job_type="plan_execute",
        params={
            "include_dependencies": request.include_dependencies,
            "include_subtasks": request.include_subtasks,
            "deep_think": request.deep_think,
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
        },
    )

    thread = threading.Thread(
        target=_run_task_chain_job,
        kwargs={
            "job_id": job.job_id,
            "plan_id": plan_id,
            "target_task_id": task_id,
            "task_order": task_order,
            "deep_think": request.deep_think,
            "session_id": request.session_id,
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
def get_plan_execution_summary(plan_id: int):
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    total = tree.node_count()
    status_counts = {"completed": 0, "failed": 0, "skipped": 0, "running": 0, "pending": 0}
    for node in tree.nodes.values():
        st = (node.status or "pending").lower()
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
        running=status_counts["running"],
        pending=status_counts["pending"],
    )


@plan_router.get(
    "/{plan_id}/subgraph",
    response_model=SubgraphResponse,
    summary="Get plan subgraph",
)
def get_plan_subgraph(
    plan_id: int,
    node_id: int = Query(..., description="Root node ID"),
    max_depth: int = Query(2, ge=1, le=6, description="Traversal depth limit"),
):
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

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
    request: DecomposeTaskRequest = Body(...),
):
    plan_id = request.plan_id
    try:
        tree = _plan_repo.get_plan_tree(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not tree.has_node(task_id):
        raise HTTPException(
            status_code=404,
            detail=f"Task {task_id} not found in plan {plan_id}",
        )

    expand_depth = request.expand_depth
    node_budget = request.node_budget
    allow_existing_children = request.allow_existing_children

    if request.async_mode:
        job = plan_decomposition_jobs.create_job(
            plan_id=plan_id,
            task_id=task_id,
            mode="single_node",
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
async def stream_decomposition_job(job_id: str):
    snapshot = plan_decomposition_jobs.get_job_payload(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Decomposition job not found.")

    loop = asyncio.get_running_loop()
    queue = plan_decomposition_jobs.register_subscriber(job_id, loop)
    if queue is None:
        async def snapshot_only() -> AsyncIterator[str]:
            yield _sse_message({"type": "snapshot", "job": snapshot})

        headers = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(snapshot_only(), media_type="text/event-stream", headers=headers)

    async def event_generator() -> AsyncIterator[str]:
        try:
            yield _sse_message({"type": "snapshot", "job": snapshot})
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    heartbeat = plan_decomposition_jobs.get_job_payload(job_id, include_logs=False)
                    if heartbeat is None:
                        break
                    yield _sse_message({"type": "heartbeat", "job": heartbeat})
                    continue
                message.setdefault("type", "event")
                yield _sse_message(message)
                if message.get("status") in {"succeeded", "failed"}:
                    break
        except asyncio.CancelledError:  # pragma: no cover - defensive
            raise
        finally:
            plan_decomposition_jobs.unregister_subscriber(job_id, queue)

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
def get_decomposition_job_status(job_id: str):
    payload = plan_decomposition_jobs.get_job_payload(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Decomposition job not found.")
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
