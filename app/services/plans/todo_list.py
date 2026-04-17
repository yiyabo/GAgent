"""Todo-List generation with topological phase grouping.

Given a target task in a PlanTree, this module:
1. Computes the full transitive dependency closure
2. Groups tasks into execution *phases* via topological layering
3. Returns a structured TodoList suitable for staged cascade execution

Phase assignment uses the Longest-Path Layering algorithm:
  - Phase 0: tasks with zero in-scope dependencies (roots of the subgraph)
  - Phase N: tasks whose deps are ALL in phases 0..N-1
  - O(V+E) complexity
"""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .plan_models import PlanNode, PlanTree
from .dependency_planner import compute_dependency_plan


_DONE_STATUSES = {"completed", "done", "success"}
_RUNNABLE_STATUSES = {"pending", "failed", "skipped"}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


def _normalize_status(value: Optional[str]) -> str:
    return str(value or "").strip().lower()

@dataclass
class TodoItem:
    """Single executable task within a phase."""

    task_id: int
    name: str
    instruction: Optional[str]
    status: str  # pending / completed / failed / skipped
    dependencies: List[int]
    phase: int

    @property
    def is_done(self) -> bool:
        return _normalize_status(self.status) in _DONE_STATUSES

    @property
    def is_runnable(self) -> bool:
        return _normalize_status(self.status) in _RUNNABLE_STATUSES

    @property
    def is_running(self) -> bool:
        return _normalize_status(self.status) == "running"


@dataclass
class TodoPhase:
    """A group of tasks that can execute once all prior phases are done."""

    phase_id: int
    label: str
    items: List[TodoItem] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.items:
            return "empty"
        if all(item.is_done for item in self.items):
            return "completed"
        if any(_normalize_status(item.status) == "failed" for item in self.items):
            return "partial_failure"
        if any(item.is_done or item.is_running for item in self.items):
            return "in_progress"
        return "pending"

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def completed_count(self) -> int:
        return sum(1 for item in self.items if item.is_done)


@dataclass
class TodoList:
    """Phased execution plan for a target task and its dependencies."""

    target_task_id: int
    phases: List[TodoPhase] = field(default_factory=list)

    @property
    def total_tasks(self) -> int:
        return sum(p.total for p in self.phases)

    @property
    def completed_tasks(self) -> int:
        return sum(p.completed_count for p in self.phases)

    @property
    def execution_order(self) -> List[int]:
        """Flat list of task IDs in phase-then-id order."""
        return [item.task_id for phase in self.phases for item in phase.items]

    @property
    def pending_order(self) -> List[int]:
        """Dependency-safe task IDs that are still runnable, in order."""
        resolved = {
            item.task_id
            for phase in self.phases
            for item in phase.items
            if item.is_done
        }
        runnable: List[int] = []
        runnable_set: Set[int] = set()

        for phase in self.phases:
            for item in phase.items:
                if not item.is_runnable:
                    continue
                deps = list(item.dependencies or [])
                if all(dep_id in resolved or dep_id in runnable_set for dep_id in deps):
                    runnable.append(item.task_id)
                    runnable_set.add(item.task_id)
        return runnable

    def summary(self) -> str:
        parts = [f"TodoList for task {self.target_task_id}:"]
        for phase in self.phases:
            parts.append(
                f"  {phase.label} — {phase.completed_count}/{phase.total} done "
                f"[{phase.status}]"
            )
            for item in phase.items:
                mark = "✓" if item.is_done else ("✗" if item.status == "failed" else "○")
                parts.append(f"    {mark} [{item.task_id}] {item.name}")
        parts.append(
            f"  Total: {self.completed_tasks}/{self.total_tasks} completed"
        )
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------

def _collect_leaf_ids(tree: PlanTree, task_ids: List[int]) -> List[int]:
    """Expand composite tasks to their atomic (leaf) descendants via DFS."""
    leaves: List[int] = []
    seen: Set[int] = set()
    stack = list(reversed(task_ids))
    while stack:
        tid = stack.pop()
        if tid in seen:
            continue
        seen.add(tid)
        children = tree.children_ids(tid)
        if children:
            stack.extend(reversed(children))
        else:
            leaves.append(tid)
    return leaves


def _compute_phase_layers(
    task_ids: Set[int],
    deps_map: Dict[int, List[int]],
) -> Dict[int, int]:
    """Assign each task to a phase via Longest-Path Layering.

    Phase 0 = tasks with no in-scope dependencies.
    Phase N = max(phase(dep) for dep in in-scope deps) + 1.

    Returns mapping {task_id: phase_number}.
    """
    # Build in-scope adjacency
    in_degree: Dict[int, int] = {tid: 0 for tid in task_ids}
    forward: Dict[int, List[int]] = {tid: [] for tid in task_ids}
    for tid in task_ids:
        for dep_id in deps_map.get(tid, []):
            if dep_id in task_ids:
                in_degree[tid] += 1
                forward.setdefault(dep_id, []).append(tid)

    # BFS from zero-degree nodes, computing longest path distance
    phase_of: Dict[int, int] = {}
    queue: List[Tuple[int, int]] = []  # (phase, task_id) min-heap
    for tid, deg in in_degree.items():
        if deg == 0:
            phase_of[tid] = 0
            heapq.heappush(queue, (0, tid))

    while queue:
        phase, tid = heapq.heappop(queue)
        # Skip if we've already assigned a higher phase (shouldn't happen
        # in a DAG but be defensive)
        if phase_of.get(tid, 0) > phase:
            continue
        for child_id in forward.get(tid, []):
            new_phase = phase + 1
            if new_phase > phase_of.get(child_id, -1):
                phase_of[child_id] = new_phase
            in_degree[child_id] -= 1
            if in_degree[child_id] == 0:
                heapq.heappush(queue, (phase_of[child_id], child_id))

    # Any remaining tasks (cycles) get assigned to phase 0 as fallback
    for tid in task_ids:
        if tid not in phase_of:
            phase_of[tid] = 0

    return phase_of


def _resolve_expanded_dependencies(
    tree: PlanTree,
    task_id: int,
    *,
    subgraph_ids: Set[int],
    expand_composites: bool,
) -> List[int]:
    """Resolve a task's in-scope dependencies, expanding composite deps to leaves."""
    node = tree.nodes.get(task_id)
    if node is None:
        return []

    resolved: List[int] = []
    for dep_id in getattr(node, "dependencies", []) or []:
        candidate_ids: List[int]
        if dep_id in subgraph_ids:
            candidate_ids = [dep_id]
        elif expand_composites and dep_id in tree.nodes:
            candidate_ids = [
                leaf_id
                for leaf_id in _collect_leaf_ids(tree, [dep_id])
                if leaf_id in subgraph_ids
            ]
        else:
            candidate_ids = []

        for candidate_id in candidate_ids:
            if candidate_id == task_id:
                continue
            if candidate_id not in resolved:
                resolved.append(candidate_id)

    return resolved


def _build_scoped_dependency_map(
    tree: PlanTree,
    *,
    subgraph_ids: Set[int],
    expand_composites: bool,
    include_child_dependencies: bool = False,
) -> Dict[int, List[int]]:
    """Build dependency map for phase layering and item rendering."""
    deps_map: Dict[int, List[int]] = {}
    for tid in subgraph_ids:
        resolved = _resolve_expanded_dependencies(
            tree,
            tid,
            subgraph_ids=subgraph_ids,
            expand_composites=expand_composites,
        )
        if include_child_dependencies:
            for child_id in tree.children_ids(tid):
                candidate_ids: List[int]
                if child_id in subgraph_ids:
                    candidate_ids = [child_id]
                elif expand_composites and child_id in tree.nodes:
                    candidate_ids = [
                        leaf_id
                        for leaf_id in _collect_leaf_ids(tree, [child_id])
                        if leaf_id in subgraph_ids
                    ]
                else:
                    candidate_ids = []

                for candidate_id in candidate_ids:
                    if candidate_id == tid:
                        continue
                    if candidate_id not in resolved:
                        resolved.append(candidate_id)
        deps_map[tid] = resolved
    return deps_map


# ---------------------------------------------------------------------------
# Phase semantic labels (heuristic)
# ---------------------------------------------------------------------------

# Ordered list of (label, keyword_patterns).  First match wins per task.
_PHASE_LABEL_RULES: List[Tuple[str, List[str]]] = [
    ("Data Preparation", [
        r"data.*(?:prep|collect|gather|download|fetch|load|import|input|source|manifest)",
        r"(?:download|fetch|collect|gather|acquire)\b",
        r"\binput\b.*(?:file|data)",
    ]),
    ("Quality Control", [
        r"\bqc\b", r"quality.*control", r"quality.*check", r"quality.*assess",
        r"\bfilter(?:ing)?\b", r"\bclean(?:ing)?\b", r"\btrim(?:ming)?\b",
        r"contamination", r"completeness",
    ]),
    ("Preprocessing", [
        r"preprocess", r"pre-process", r"normali[sz]", r"transform",
        r"format.*convert", r"standard[iz]", r"\bconvert\b",
        r"\bmerge\b", r"\bcombine\b", r"\bintegrat",
    ]),
    ("Assembly", [
        r"\bassembl", r"\bscaffold", r"\bcontig",
    ]),
    ("Annotation", [
        r"\bannotat", r"\bgene.*predict", r"\borf\b", r"\bprodigal\b",
        r"\bprokka\b", r"\bfunctional.*annotat",
    ]),
    ("Alignment", [
        r"\balign", r"\bblast\b", r"\bhmm", r"\bmsa\b",
        r"\bmuscle\b", r"\bmafft\b", r"\bclustal",
    ]),
    ("Phylogenetics", [
        r"\bphylogen", r"\btree.*build", r"\bbootstrap",
        r"\biqtree\b", r"\braxml\b", r"\bnewick\b",
    ]),
    ("Analysis", [
        r"\banalys[ie]s?\b", r"\bstatistic", r"\bcompar",
        r"\bdiversit", r"\babundance", r"\bcluster",
        r"\bpca\b", r"\bumap\b", r"\bt-sne\b",
    ]),
    ("Visualization", [
        r"\bvisuali[sz]", r"\bplot\b", r"\bfigure\b", r"\bchart\b",
        r"\bheatmap\b", r"\bgraph(?!ql)\b", r"\bdraw\b", r"\brender\b",
    ]),
    ("Reporting", [
        r"\breport\b", r"\bsummar", r"\bmanuscript", r"\bwrite.*up",
        r"\bdocument", r"\bconclu",
    ]),
]


def _classify_task_label(name: str, instruction: Optional[str]) -> Optional[str]:
    """Return the best semantic label for a single task, or None."""
    text = (name or "").lower()
    if instruction:
        text += " " + instruction[:300].lower()
    for label, patterns in _PHASE_LABEL_RULES:
        for pat in patterns:
            if re.search(pat, text):
                return label
    return None


def assign_phase_labels(phases: List[TodoPhase]) -> None:
    """Mutate *phases* in-place, assigning semantic labels via heuristic.

    For each phase, classify every task and pick the most common label.
    Falls back to "Phase N" if no majority label is found.
    """
    for phase in phases:
        votes: Dict[str, int] = {}
        for item in phase.items:
            label = _classify_task_label(item.name, item.instruction)
            if label:
                votes[label] = votes.get(label, 0) + 1
        if votes:
            winner = max(votes, key=lambda k: votes[k])
            phase.label = winner
        # else keep default "Phase N"


def build_todo_list(
    tree: PlanTree,
    target_task_id: int,
    *,
    include_target: bool = True,
    expand_composites: bool = True,
) -> TodoList:
    """Build a phased TodoList for *target_task_id* and all its dependencies.

    Parameters
    ----------
    tree : PlanTree
        The plan tree containing all tasks.
    target_task_id : int
        The goal task whose dependency subgraph to resolve.
    include_target : bool
        Whether the target itself appears in the TodoList.
    expand_composites : bool
        If True, composite (non-leaf) tasks are expanded to their
        atomic descendants.

    Returns
    -------
    TodoList with phases ordered from 0 (no deps) to N (deepest).
    """
    # 1. Dependency closure via existing algorithm
    dep_plan = compute_dependency_plan(
        tree,
        target_task_id,
        include_target_in_order=include_target,
    )

    # Collect all task IDs in the execution subgraph
    subgraph_ids: Set[int] = set(dep_plan.execution_order)
    if not subgraph_ids and not dep_plan.cycle_detected:
        # Target has no unmet deps and was excluded — just include it
        if include_target:
            subgraph_ids = {target_task_id}

    # Also include already-satisfied deps so phases reflect the full picture
    for dep_id in dep_plan.closure_dependencies:
        if dep_id in tree.nodes:
            subgraph_ids.add(dep_id)
    if include_target:
        subgraph_ids.add(target_task_id)

    # 2. Optionally expand composites to leaves
    if expand_composites:
        expanded = _collect_leaf_ids(tree, sorted(subgraph_ids))
        subgraph_ids = set(expanded)

    if not subgraph_ids:
        return TodoList(target_task_id=target_task_id)

    # 3. Build dependency map for in-scope tasks
    deps_map = _build_scoped_dependency_map(
        tree,
        subgraph_ids=subgraph_ids,
        expand_composites=expand_composites,
    )

    # 4. Compute phase layers
    phase_of = _compute_phase_layers(subgraph_ids, deps_map)

    # 5. Group into TodoPhases
    max_phase = max(phase_of.values()) if phase_of else 0
    phases: List[TodoPhase] = []
    for p in range(max_phase + 1):
        task_ids_in_phase = sorted(
            tid for tid, ph in phase_of.items() if ph == p
        )
        items: List[TodoItem] = []
        for tid in task_ids_in_phase:
            node = tree.nodes.get(tid)
            if node is None:
                continue
            items.append(
                TodoItem(
                    task_id=tid,
                    name=node.name,
                    instruction=node.instruction,
                    status=node.status,
                    dependencies=list(deps_map.get(tid, [])),
                    phase=p,
                )
            )
        label = f"Phase {p + 1}"
        phases.append(TodoPhase(phase_id=p, label=label, items=items))

    # Apply semantic labels via heuristic keyword matching
    assign_phase_labels(phases)

    return TodoList(target_task_id=target_task_id, phases=phases)


def build_full_plan_todo_list(
    tree: PlanTree,
    *,
    expand_composites: bool = True,
) -> TodoList:
    """Build a phased TodoList covering the *entire* plan tree.

    Unlike :func:`build_todo_list` which starts from a single target task,
    this function computes phase layers for every node in the tree.

    This is the main entry point for the "auto-execute entire plan" feature:
    the returned ``TodoList.pending_order`` gives a dependency-correct
    execution sequence; the ``phases`` attribute groups tasks into
    topological stages that can be presented to the user.

    Parameters
    ----------
    tree : PlanTree
        The plan tree containing all tasks.
    expand_composites : bool
        Retained for API compatibility. Full-plan execution always preserves
        composite tasks and enforces parent-after-child ordering so synthesis
        nodes are not dropped from the runnable queue.

    Returns
    -------
    TodoList with ``target_task_id`` set to 0 (plan-level sentinel).
    """
    all_ids: Set[int] = set(tree.nodes.keys())
    subgraph_ids = all_ids

    if not subgraph_ids:
        return TodoList(target_task_id=0)

    deps_map = _build_scoped_dependency_map(
        tree,
        subgraph_ids=subgraph_ids,
        expand_composites=expand_composites,
        include_child_dependencies=True,
    )

    phase_of = _compute_phase_layers(subgraph_ids, deps_map)

    max_phase = max(phase_of.values()) if phase_of else 0
    phases: List[TodoPhase] = []
    for p in range(max_phase + 1):
        task_ids_in_phase = sorted(tid for tid, ph in phase_of.items() if ph == p)
        items: List[TodoItem] = []
        for tid in task_ids_in_phase:
            node = tree.nodes.get(tid)
            if node is None:
                continue
            items.append(
                TodoItem(
                    task_id=tid,
                    name=node.name,
                    instruction=node.instruction,
                    status=node.status,
                    dependencies=list(deps_map.get(tid, [])),
                    phase=p,
                )
            )
        label = f"Phase {p + 1}"
        phases.append(TodoPhase(phase_id=p, label=label, items=items))

    assign_phase_labels(phases)

    return TodoList(target_task_id=0, phases=phases)


__all__ = [
    "TodoItem",
    "TodoPhase",
    "TodoList",
    "build_todo_list",
    "build_full_plan_todo_list",
    "assign_phase_labels",
    "_compute_phase_layers",
]
