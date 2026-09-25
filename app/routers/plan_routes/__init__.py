"""Plan and task routes (compatibility facade).

This package is the split-out form of the former ``app/routers/plan_routes.py``
module. The import path ``app.routers.plan_routes`` is unchanged: it is the
registration contract used by ``app/routers/__init__.py``.

Package layout:

- ``schemas.py``         Pydantic DTOs (pure data)
- ``state.py``           shared singletons, logger, execution locks
- ``effective_state.py`` effective task-state resolution, todo serialization
- ``dependency_plan.py`` dependency plan and execution checklist serialization
- ``execution_jobs.py``  background job runners, audit-repair orchestration

The HTTP endpoints, the two routers and registration stay in this facade, and
every original module-level name is re-exported here (private names included),
so ``from app.routers.plan_routes import X`` and ``plan_routes.X`` access keep
working unchanged. Sibling modules must not import facade names at import time:
the names tests patch (``_plan_repo``, ``_plan_executor``, ``plan_decomposition_jobs``,
``log_job_event``, ``_resolve_effective_task_states``, ``_build_plan_execution_snapshot``,
``_list_plan_execute_job_ids``, ``_run_task_audit_repair_after_execution``, ...) are
read through the facade at call time (``from .. import plan_routes as facade``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.database import get_db
from app.services.plans.artifact_preflight import ArtifactPreflightResult
from app.services.plans.decomposition_jobs import (
    log_job_event,
    plan_decomposition_jobs,
)
from app.services.plans.dependency_enrichment import (
    enrich_plan_dependencies,
    validate_plan_dag,
)
from app.services.plans.dependency_validation import normalize_plan_dependencies
from app.services.plans.phase_narrator import extract_phase_labels as _extract_phase_labels
from app.services.plans.plan_decomposer import DecompositionResult
from app.services.plans.plan_executor import ExecutionConfig
from app.services.plans.todo_list import (
    build_todo_list as _build_todo_list,
    build_full_plan_todo_list as _build_full_plan_todo_list,
)
from app.services.realtime_bus import EventSubscription, get_realtime_bus
from app.services.request_principal import ensure_owner_access, get_request_owner_id
from .. import register_router
from .dependency_plan import (
    _build_dependency_block_details,
    _build_dependency_block_reason,
    _build_execution_checklist_items,
    _build_execution_dependency_plan,
    _collect_subtree_node_ids,
    _dedupe_cycle_paths,
    _dependency_warning_step,
    _expand_artifact_preflight_scope,
    _expected_deliverables_for_node,
    _persist_dependency_block,
    _to_dependency_plan_response,
    _topological_task_order,
)
from .effective_state import (
    _build_plan_execution_snapshot,
    _effective_response_fields,
    _list_plan_execute_job_ids,
    _lookup_session_id_for_plan,
    _normalize_task_status,
    _parse_execution_result,
    _resolve_effective_task_states,
    _serialize_plan_tree_with_effective_status,
    _to_int,
    _todo_completed_count_from_effective,
    _todo_item_to_dict,
    _todo_list_to_dict,
    _todo_pending_order_from_effective,
    _todo_phase_status_from_effective,
    _todo_summary_from_effective,
    _truncate_reason,
)
from .execution_jobs import (
    _is_terminal_job_status,
    _publish_deliverables_after_audit_repair,
    _run_decomposition_job,
    _run_full_plan_job,
    _run_task_audit_repair_after_execution,
    _run_task_chain_job,
    _status_after_audit_repair,
)
from .schemas import (
    AcceptTaskRequest,
    AcceptTaskResponse,
    DecomposeTaskRequest,
    DecomposeTaskResponse,
    DecompositionJobStatusResponse,
    DependencyNodeSummary,
    DependencyPlanResponse,
    ExecuteFullPlanRequest,
    ExecuteFullPlanResponse,
    ExecuteTaskRequest,
    ExecuteTaskResponse,
    ExecutionChecklistItem,
    PlanExecutionSummary,
    PlanResultsResponse,
    ReverifyPlanDryRunResponse,
    SubgraphResponse,
    TaskResultItem,
    TodoItemResponse,
    TodoListResponse,
    TodoPhaseResponse,
    TodoWorkflowSectionResponse,
    VerifyTaskResponse,
    _default_plan_paper_mode,
)
from .state import (
    _acquire_plan_execution_lock,
    _artifact_preflight_service,
    _audit_repair_loop_service,
    _plan_decomposer,
    _plan_executor,
    _plan_repo,
    _plan_status_resolver,
    _release_plan_execution_lock,
    _task_execution_locks,
    _task_execution_locks_guard,
    _task_verifier,
    logger,
)

plan_router = APIRouter(prefix="/plans", tags=["plans"])
task_router = APIRouter(prefix="/tasks", tags=["tasks"])


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


@plan_router.get("", summary="List plans")
def list_plans(request: Request):
    """Return plan summaries."""
    summaries = _plan_repo.list_plans(owner=get_request_owner_id(request))
    return [summary.model_dump() for summary in summaries]


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


@plan_router.post(
    "/{plan_id}/reverify/dry-run",
    response_model=ReverifyPlanDryRunResponse,
    summary="Dry-run deterministic verification for plan task results",
)
def dry_run_reverify_plan(
    plan_id: int,
    request: Request,
    task_ids: Optional[List[int]] = Body(default=None),
):
    _load_authorized_plan_tree(plan_id, request)
    if not isinstance(task_ids, list):
        task_ids = None
    result = _task_verifier.dry_run_reverify_plan(
        _plan_repo,
        plan_id=plan_id,
        task_ids=task_ids,
    )
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    would_change = int(summary.get("would_change_status") or 0)
    return ReverifyPlanDryRunResponse(
        success=True,
        message=(
            f"Dry-run reverified {summary.get('verifiable', 0)} task(s); "
            f"{would_change} status change(s) would be made if persisted."
        ),
        plan_id=plan_id,
        dry_run=True,
        summary=summary,
        items=list(result.get("items") or []),
    )


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


def _build_unverifiable_task_response(
    *,
    plan_id: int,
    task_id: int,
    node: Any,
    tree: Any,
    reason: str,
) -> VerifyTaskResponse:
    state_by_task = _resolve_effective_task_states(plan_id, tree)
    state = state_by_task.get(task_id)
    child_ids = list(tree.children_ids(task_id)) if hasattr(tree, "children_ids") else []
    leaf_child_ids: List[int] = []
    for child_id in child_ids:
        if not tree.children_ids(child_id):
            leaf_child_ids.append(child_id)
    if child_ids and not leaf_child_ids:
        leaf_child_ids = [
            child_id
            for child_id in child_ids
            if tree.has_node(child_id)
        ]
    details = {
        "verification_status": "not_run",
        "reason": reason,
    }
    if child_ids:
        details["child_task_ids"] = child_ids
        details["verifiable_task_ids"] = leaf_child_ids
    return VerifyTaskResponse(
        success=False,
        message=reason,
        plan_id=plan_id,
        task_id=task_id,
        result=TaskResultItem(
            task_id=task_id,
            name=node.name,
            status=str((state or {}).get("effective_status") or node.status or "pending"),
            **_effective_response_fields(state),
            content=None,
            notes=[],
            metadata=details,
            raw=None,
        ),
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
    child_ids = list(tree.children_ids(task_id))
    if child_ids and not node.execution_result:
        return _build_unverifiable_task_response(
            plan_id=plan_id,
            task_id=task_id,
            node=node,
            tree=tree,
            reason=(
                f"Task {task_id} is a composite parent and has no direct execution result to verify; "
                "verify one of its executable child tasks instead."
            ),
        )
    if not node.execution_result:
        return _build_unverifiable_task_response(
            plan_id=plan_id,
            task_id=task_id,
            node=node,
            tree=tree,
            reason=f"Task {task_id} has not produced an execution result yet; run it before verification.",
        )

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
        audit_repairs: Dict[str, Any] = {}
        owner_id = get_request_owner_id(raw_request)
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
    phase_labels = _extract_phase_labels(tree.metadata)
    todo = _build_full_plan_todo_list(
        tree,
        expand_composites=expand_composites,
        ordering_mode="structure",
        phase_labels=phase_labels,
    )
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
    request = request or ExecuteFullPlanRequest(
        skip_completed=True,
        stop_on_failure=True,
        ordering_mode="dependency_phase",
        dependency_block_mode="block",
    )
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
        _normalization = normalize_plan_dependencies(tree)
        if _normalization.dependencies_by_task:
            for _tid, _deps in _normalization.dependencies_by_task.items():
                _plan_repo.update_task(plan_id, _tid, dependencies=list(_deps))
                if _tid in tree.nodes:
                    tree.nodes[_tid].dependencies = list(_deps)
            logging.getLogger("app.routers.plan_routes").info(
                "Normalized dependency edges for plan %s tasks %s.",
                plan_id,
                _normalization.changed_task_ids,
            )
        for _issue in _normalization.issues:
            logging.getLogger("app.routers.plan_routes").warning(
                "Plan dependency validation: %s",
                _issue.message,
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

    todo = _build_full_plan_todo_list(
        tree,
        expand_composites=True,
        ordering_mode=request.ordering_mode,
    )
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
        audit_repairs: Dict[str, Any] = {}
        owner_id = get_request_owner_id(raw_request)
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
                if current_status in ("completed", "running", "delegating"):
                    executed.append(tid)
                    continue
                dependency_block = _build_dependency_block_details(
                    current_tree,
                    tid,
                    state_by_task=current_state_by_task,
                )
                if dependency_block is not None:
                    if str(request.dependency_block_mode or "warn").strip().lower() == "block":
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
                enforce_dependencies=str(request.dependency_block_mode or "warn").strip().lower() == "block",
            )
            try:
                result = _plan_executor.execute_task(plan_id, tid, config=exec_config)
            except Exception as exc:
                failure_payload = {
                    "status": "failed",
                    "content": f"Task execution raised an exception: {exc}",
                    "metadata": {"execution_exception": True, "error": str(exc)},
                }
                try:
                    _plan_repo.update_task(
                        plan_id,
                        tid,
                        status="failed",
                        execution_result=json.dumps(failure_payload, ensure_ascii=False),
                    )
                    repair_result = _run_task_audit_repair_after_execution(
                        plan_id=plan_id,
                        task_id=tid,
                        result_status="failed",
                        session_id=request.session_id,
                        owner_id=owner_id,
                    )
                    audit_repairs[str(tid)] = repair_result
                    if _status_after_audit_repair("failed", repair_result) == "completed":
                        executed.append(tid)
                        _publish_deliverables_after_audit_repair(
                            plan_id=plan_id, task_id=tid, session_id=request.session_id,
                        )
                        continue
                except Exception as repair_exc:
                    audit_repairs[str(tid)] = {"success": False, "error": str(repair_exc), "trigger_status": "failed"}
                failed.append(tid)
                if request.stop_on_failure:
                    break
                continue

            repair_result = _run_task_audit_repair_after_execution(
                plan_id=plan_id,
                task_id=tid,
                result_status=result.status,
                session_id=request.session_id,
                owner_id=owner_id,
            )
            audit_repairs[str(tid)] = repair_result
            final_status = _status_after_audit_repair(result.status, repair_result)

            if final_status == "completed":
                executed.append(tid)
            elif final_status in {"skipped", "blocked"}:
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
                "audit_repairs": audit_repairs,
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
                "ordering_mode": request.ordering_mode,
                "dependency_block_mode": request.dependency_block_mode,
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
                "ordering_mode": str(getattr(todo, "ordering_mode", request.ordering_mode) or request.ordering_mode),
                "dependency_block_mode": request.dependency_block_mode,
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
                "ordering_mode": str(getattr(todo, "ordering_mode", request.ordering_mode) or request.ordering_mode),
                "dependency_block_mode": request.dependency_block_mode,
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
                "dependency_block_mode": request.dependency_block_mode,
                "owner_id": owner_id,
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
