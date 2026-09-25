"""Effective task-state resolution and todo-list serialization for plan routes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from app.database import get_db
from app.services.plans.status_resolver import _looks_like_retry_or_blocked_failure_text
from app.services.plans.todo_list import _collect_leaf_ids

if TYPE_CHECKING:
    from app.services.plans.plan_models import PlanTree


def _facade() -> Any:
    """Return the package facade module (late-bound, patch-safe)."""
    from .. import plan_routes as facade

    return facade


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
    for job_id in _facade()._list_plan_execute_job_ids(plan_id):
        if job_id in exclude_job_ids:
            continue
        payload = _facade().plan_decomposition_jobs.get_job_payload(job_id, include_logs=False)
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


def _lookup_session_id_for_plan(plan_id: int) -> Optional[str]:
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id FROM chat_sessions WHERE plan_id = ? LIMIT 1",
                (plan_id,),
            ).fetchone()
            if row:
                return str(row["id"])
    except Exception:
        pass
    return None


def _resolve_effective_task_states(
    plan_id: int,
    tree: "PlanTree",
    *,
    snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[int, Dict[str, Any]]:
    snapshot = snapshot or _facade()._build_plan_execution_snapshot(plan_id)
    session_id = _lookup_session_id_for_plan(plan_id)
    states = _facade()._plan_status_resolver.resolve_plan_states(
        plan_id,
        tree,
        snapshot=snapshot,
        session_id=session_id,
    )
    for task_id, state in states.items():
        if str(state.get("effective_status") or "").strip().lower() != "completed":
            continue
        node = tree.nodes.get(task_id)
        if node is None:
            continue
        content, _notes, metadata, raw_payload = _parse_execution_result(getattr(node, "execution_result", None))
        if _facade()._task_verifier.is_manual_acceptance_active(metadata):
            continue
        payload_status = _normalize_task_status(raw_payload.get("status")) if isinstance(raw_payload, dict) else ""
        verification_status = _normalize_task_status(metadata.get("verification_status"))
        if payload_status == "completed" and verification_status == "passed":
            continue
        if _looks_like_retry_or_blocked_failure_text(content or getattr(node, "execution_result", None)):
            state["effective_status"] = "failed"
            state["status_reason"] = _truncate_reason(content or getattr(node, "execution_result", None)) or "Task requires retry."
            state["reason_code"] = "retry_or_blocked_failure"
    return states


def _normalize_task_status(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


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
    state_by_task = state_by_task or _facade()._resolve_effective_task_states(plan_id, tree)
    nodes_payload: Dict[str, Any] = {}
    for task_id, node in tree.nodes.items():
        payload = node.model_dump(exclude={'execution_result'})
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
    if str(getattr(todo, "ordering_mode", "") or "").strip().lower() == "structure":
        runnable_statuses = {"pending", "failed", "skipped", "blocked"}
        ordered: List[int] = []
        for phase in todo.phases:
            for item in phase.items:
                effective_status = str((state_by_task.get(item.task_id) or {}).get("effective_status") or "pending")
                if effective_status not in runnable_statuses:
                    continue
                if tree is not None and tree.children_ids(item.task_id):
                    continue
                ordered.append(item.task_id)
        return ordered

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


def _todo_item_to_dict(
    item: Any,
    state_by_task: Dict[int, Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "task_id": item.task_id,
        "name": item.name,
        "instruction": item.instruction,
        "status": str((state_by_task.get(item.task_id) or {}).get("effective_status") or "pending"),
        **_effective_response_fields(state_by_task.get(item.task_id)),
        "dependencies": item.dependencies,
        "phase": item.phase,
    }


def _todo_list_to_dict(
    todo: Any,
    plan_id: int,
    *,
    state_by_task: Optional[Dict[int, Dict[str, Any]]] = None,
    tree: Optional["PlanTree"] = None,
) -> Dict[str, Any]:
    if tree is None:
        tree = _facade()._plan_repo.get_plan_tree(plan_id)
    state_by_task = state_by_task or _facade()._resolve_effective_task_states(plan_id, tree)
    phases_out = []
    for phase in todo.phases:
        phases_out.append({
            "phase_id": phase.phase_id,
            "label": phase.label,
            "status": _todo_phase_status_from_effective(phase, state_by_task),
            "total": phase.total,
            "completed": _todo_completed_count_from_effective(phase, state_by_task),
            "items": [_todo_item_to_dict(item, state_by_task) for item in phase.items],
        })
    workflow_sections_out = []
    for section in getattr(todo, "workflow_sections", []) or []:
        workflow_sections_out.append({
            "section_id": section.section_id,
            "label": section.label,
            "status": _todo_phase_status_from_effective(section, state_by_task),
            "total": section.total,
            "completed": _todo_completed_count_from_effective(section, state_by_task),
            "items": [_todo_item_to_dict(item, state_by_task) for item in section.items],
        })
    return {
        "plan_id": plan_id,
        "target_task_id": todo.target_task_id,
        "ordering_mode": str(getattr(todo, "ordering_mode", "dependency_phase") or "dependency_phase"),
        "total_tasks": todo.total_tasks,
        "completed_tasks": sum(
            1
            for phase in todo.phases
            for item in phase.items
            if str((state_by_task.get(item.task_id) or {}).get("effective_status") or "") == "completed"
        ),
        "phases": phases_out,
        "workflow_sections": workflow_sections_out,
        "execution_order": todo.execution_order,
        "pending_order": _todo_pending_order_from_effective(todo, state_by_task, tree=tree),
        "summary": _todo_summary_from_effective(todo, state_by_task),
    }
