from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set, Tuple

from .plan_models import PlanTree


def _normalize_status(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _canonical_cycle(body: List[int]) -> Tuple[int, ...]:
    """Return a canonical representation of a cycle body (no repeated tail).

    Canonicalization is rotation + direction invariant so the same cycle
    reported from different DFS entrypoints can be deduplicated reliably.
    """

    if not body:
        return tuple()
    n = len(body)
    min_id = min(body)
    positions = [i for i, v in enumerate(body) if v == min_id]
    candidates: List[Tuple[int, ...]] = []
    for pos in positions:
        candidates.append(tuple(body[pos:] + body[:pos]))

    rev = list(reversed(body))
    min_rev = min(rev)
    positions_rev = [i for i, v in enumerate(rev) if v == min_rev]
    for pos in positions_rev:
        candidates.append(tuple(rev[pos:] + rev[:pos]))

    return min(candidates) if candidates else tuple(body)


@dataclass(frozen=True)
class DependencyPlan:
    plan_id: int
    target_task_id: int
    satisfied_statuses: Tuple[str, ...] = ("completed", "done")
    direct_dependencies: List[int] = field(default_factory=list)
    closure_dependencies: List[int] = field(default_factory=list)
    missing_dependencies: List[int] = field(default_factory=list)
    running_dependencies: List[int] = field(default_factory=list)
    execution_order: List[int] = field(default_factory=list)
    cycle_detected: bool = False
    cycle_paths: List[List[int]] = field(default_factory=list)


def compute_dependency_plan(
    tree: PlanTree,
    target_task_id: int,
    *,
    satisfied_statuses: Iterable[str] = ("completed", "done"),
    include_target_in_order: bool = True,
    max_cycles: int = 5,
) -> DependencyPlan:
    """Compute dependency closure and an executable topological order.

    - Dependencies are treated as directed edges: dep -> node.
    - Only dependencies reachable from target_task_id are considered.
    - Status satisfaction defaults to {"completed", "done"} to match PlanExecutor.
    """

    if target_task_id not in tree.nodes:
        raise ValueError(
            f"Target task {target_task_id} not found in plan {tree.id}"
        )

    satisfied = tuple(str(s).strip().lower() for s in satisfied_statuses if str(s).strip())
    satisfied_set = set(satisfied) if satisfied else {"completed", "done"}

    target = tree.nodes[target_task_id]
    direct = sorted([d for d in target.dependencies if d in tree.nodes])

    closure: Set[int] = set()
    visited: Set[int] = {target_task_id}
    stack: List[int] = []
    in_stack: Set[int] = set()
    cycles: List[List[int]] = []
    seen_cycles: Set[Tuple[int, ...]] = set()

    def dfs(current_id: int) -> None:
        stack.append(current_id)
        in_stack.add(current_id)
        node = tree.nodes[current_id]
        for dep_id in node.dependencies:
            if dep_id not in tree.nodes:
                continue
            closure.add(dep_id)
            if dep_id in in_stack:
                # cycle path: dep_id ... current_id -> dep_id
                try:
                    idx = stack.index(dep_id)
                except ValueError:  # pragma: no cover - defensive
                    idx = 0
                cycle_path = stack[idx:] + [dep_id]
                canon = _canonical_cycle(cycle_path[:-1])
                if canon and canon not in seen_cycles:
                    seen_cycles.add(canon)
                    cycles.append(cycle_path)
                    if len(cycles) >= max_cycles:
                        return
                continue
            if dep_id in visited:
                continue
            visited.add(dep_id)
            dfs(dep_id)
            if len(cycles) >= max_cycles:
                return
        in_stack.remove(current_id)
        stack.pop()

    dfs(target_task_id)

    closure_list = sorted(closure)
    cycle_detected = len(cycles) > 0

    missing: List[int] = []
    running: List[int] = []
    for dep_id in closure_list:
        st = _normalize_status(tree.nodes[dep_id].status)
        if st == "running":
            running.append(dep_id)
        if st not in satisfied_set:
            missing.append(dep_id)

    order: List[int] = []
    if not cycle_detected:
        to_run: Set[int] = set()
        if include_target_in_order:
            to_run.add(target_task_id)
        for dep_id in closure_list:
            st = _normalize_status(tree.nodes[dep_id].status)
            if st not in satisfied_set:
                to_run.add(dep_id)

        in_degree = {node_id: 0 for node_id in to_run}
        outgoing: dict[int, Set[int]] = {node_id: set() for node_id in to_run}
        for node_id in to_run:
            node = tree.nodes[node_id]
            for dep_id in node.dependencies:
                if dep_id in to_run:
                    in_degree[node_id] += 1
                    outgoing.setdefault(dep_id, set()).add(node_id)

        heap = [node_id for node_id, deg in in_degree.items() if deg == 0]
        heapq.heapify(heap)
        while heap:
            current = heapq.heappop(heap)
            order.append(current)
            for child_id in sorted(outgoing.get(current, set())):
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    heapq.heappush(heap, child_id)

        if len(order) != len(to_run):
            # Unexpected cycle in the filtered subgraph; mark as cycle to be safe.
            cycle_detected = True
            cycles = cycles or []
            order = []

    return DependencyPlan(
        plan_id=tree.id,
        target_task_id=target_task_id,
        satisfied_statuses=tuple(sorted(satisfied_set)),
        direct_dependencies=direct,
        closure_dependencies=closure_list,
        missing_dependencies=sorted(missing),
        running_dependencies=sorted(running),
        execution_order=order,
        cycle_detected=cycle_detected,
        cycle_paths=cycles,
    )


__all__ = ["DependencyPlan", "compute_dependency_plan"]

