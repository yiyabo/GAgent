"""Dependency-plan construction and execution-checklist serialization."""

from __future__ import annotations

import heapq
import json
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set, Tuple

from app.services.plans.acceptance_criteria import (
    derive_acceptance_criteria_from_text,
    derive_expected_deliverables,
)
from app.services.plans.artifact_contracts import producer_candidates_for_alias
from app.services.plans.artifact_preflight import ArtifactPreflightResult
from app.services.plans.dependency_planner import DependencyPlan, compute_dependency_plan

from .effective_state import _effective_response_fields, _normalize_task_status
from .schemas import (
    DependencyNodeSummary,
    DependencyPlanResponse,
    ExecutionChecklistItem,
)
from .state import logger

if TYPE_CHECKING:
    from app.services.plans.plan_models import PlanTree


def _facade() -> Any:
    """Return the package facade module (late-bound, patch-safe)."""
    from .. import plan_routes as facade

    return facade


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
        state_by_task = _facade()._resolve_effective_task_states(plan.plan_id, tree)

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
        _facade()._plan_repo.update_task(
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


def _dependency_warning_step(task_id: int, dependency_block: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(dependency_block.get("metadata") or {})
    metadata["dependency_warning"] = True
    metadata["degraded_input"] = True
    return {
        "task_id": task_id,
        "status": "dependency_warning",
        "duration_sec": 0.0,
        "reason": dependency_block.get("reason"),
        "metadata": metadata,
    }


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
        if dep_status in ("running", "delegating"):
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
