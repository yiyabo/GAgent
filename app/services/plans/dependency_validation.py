from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .plan_models import PlanTree

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DependencyIssue:
    code: str
    message: str
    task_id: int
    dependency_id: Optional[int] = None
    replacement_ids: List[int] = field(default_factory=list)


@dataclass(frozen=True)
class DependencyNormalization:
    task_id: int
    original_dependencies: List[int]
    normalized_dependencies: List[int]
    issues: List[DependencyIssue] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.original_dependencies != self.normalized_dependencies


@dataclass(frozen=True)
class PlanDependencyNormalization:
    dependencies_by_task: Dict[int, List[int]]
    issues: List[DependencyIssue] = field(default_factory=list)

    @property
    def changed_task_ids(self) -> List[int]:
        return sorted(self.dependencies_by_task)


def ancestor_ids(tree: PlanTree, node_id: int) -> Set[int]:
    ancestors: Set[int] = set()
    current = tree.nodes.get(node_id)
    while current is not None and current.parent_id is not None:
        parent_id = int(current.parent_id)
        if parent_id in ancestors:
            break
        ancestors.add(parent_id)
        current = tree.nodes.get(parent_id)
    return ancestors


def descendant_ids(tree: PlanTree, node_id: int) -> Set[int]:
    descendants: Set[int] = set()
    stack = list(tree.children_ids(node_id))
    while stack:
        child_id = int(stack.pop())
        if child_id in descendants:
            continue
        descendants.add(child_id)
        stack.extend(tree.children_ids(child_id))
    return descendants


def leaf_descendant_ids(tree: PlanTree, node_id: int) -> List[int]:
    if node_id not in tree.nodes:
        return []
    leaves: List[int] = []
    stack = [node_id]
    seen: Set[int] = set()
    while stack:
        current_id = int(stack.pop())
        if current_id in seen:
            continue
        seen.add(current_id)
        children = list(tree.children_ids(current_id))
        if not children:
            leaves.append(current_id)
            continue
        stack.extend(reversed(children))
    return leaves


def _dedupe(values: Iterable[int]) -> List[int]:
    out: List[int] = []
    seen: Set[int] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _has_dependency_path(
    dep_map: Dict[int, List[int]],
    *,
    start_id: int,
    target_id: int,
    ignored_edge: Optional[Tuple[int, int]] = None,
) -> bool:
    stack = [start_id]
    seen: Set[int] = set()
    while stack:
        current_id = int(stack.pop())
        if current_id == target_id:
            return True
        if current_id in seen:
            continue
        seen.add(current_id)
        for dep_id in dep_map.get(current_id, []):
            if ignored_edge is not None and ignored_edge == (current_id, dep_id):
                continue
            if dep_id not in seen:
                stack.append(dep_id)
    return False


def _structurally_valid_dependency(
    tree: PlanTree,
    *,
    task_id: int,
    dep_id: int,
) -> Optional[DependencyIssue]:
    if dep_id == task_id:
        return DependencyIssue(
            code="self_dependency",
            message=f"Task {task_id} cannot depend on itself.",
            task_id=task_id,
            dependency_id=dep_id,
        )
    if dep_id not in tree.nodes:
        return DependencyIssue(
            code="missing_dependency",
            message=f"Task {task_id} depends on missing task {dep_id}.",
            task_id=task_id,
            dependency_id=dep_id,
        )
    ancestors = ancestor_ids(tree, task_id)
    if dep_id in ancestors:
        return DependencyIssue(
            code="ancestor_dependency",
            message=f"Task {task_id} cannot depend on its ancestor task {dep_id}.",
            task_id=task_id,
            dependency_id=dep_id,
        )
    descendants = descendant_ids(tree, task_id)
    if dep_id in descendants:
        return DependencyIssue(
            code="descendant_dependency",
            message=f"Task {task_id} cannot depend on its descendant task {dep_id}.",
            task_id=task_id,
            dependency_id=dep_id,
        )
    return None


def _expand_and_clean_dependencies(
    tree: PlanTree,
    *,
    task_id: int,
    dependencies: Iterable[int],
) -> Tuple[List[int], List[DependencyIssue]]:
    cleaned: List[int] = []
    issues: List[DependencyIssue] = []
    for raw_dep in dependencies:
        try:
            dep_id = int(raw_dep)
        except (TypeError, ValueError):
            issues.append(
                DependencyIssue(
                    code="invalid_dependency",
                    message=f"Task {task_id} has a non-integer dependency {raw_dep!r}.",
                    task_id=task_id,
                )
            )
            continue

        issue = _structurally_valid_dependency(tree, task_id=task_id, dep_id=dep_id)
        if issue is not None:
            issues.append(issue)
            continue

        children = list(tree.children_ids(dep_id))
        if not children:
            cleaned.append(dep_id)
            continue

        replacement_ids: List[int] = []
        for leaf_id in leaf_descendant_ids(tree, dep_id):
            leaf_issue = _structurally_valid_dependency(tree, task_id=task_id, dep_id=leaf_id)
            if leaf_issue is not None:
                issues.append(leaf_issue)
                continue
            replacement_ids.append(leaf_id)
        if replacement_ids:
            issues.append(
                DependencyIssue(
                    code="composite_dependency_expanded",
                    message=(
                        f"Task {task_id} dependency on composite task {dep_id} "
                        f"was expanded to leaf task(s) {replacement_ids}."
                    ),
                    task_id=task_id,
                    dependency_id=dep_id,
                    replacement_ids=replacement_ids,
                )
            )
            cleaned.extend(replacement_ids)
        else:
            issues.append(
                DependencyIssue(
                    code="composite_dependency_dropped",
                    message=(
                        f"Task {task_id} dependency on composite task {dep_id} "
                        "had no valid executable leaf replacements."
                    ),
                    task_id=task_id,
                    dependency_id=dep_id,
                )
            )
    return _dedupe(cleaned), issues


def normalize_dependencies_for_task(
    tree: PlanTree,
    task_id: int,
    dependencies: Iterable[int],
) -> DependencyNormalization:
    original: List[int] = []
    seen_original: Set[int] = set()
    for dep in dependencies:
        try:
            value = int(dep)
        except (TypeError, ValueError):
            continue
        if value not in seen_original:
            seen_original.add(value)
            original.append(value)
    cleaned, issues = _expand_and_clean_dependencies(
        tree,
        task_id=task_id,
        dependencies=dependencies,
    )
    dep_map: Dict[int, List[int]] = {}
    for node in tree.nodes.values():
        node_id = int(node.id)
        if node_id == task_id:
            continue
        node_cleaned, _node_issues = _expand_and_clean_dependencies(
            tree,
            task_id=node_id,
            dependencies=list(node.dependencies or []),
        )
        dep_map[node_id] = node_cleaned
    dep_map[task_id] = list(cleaned)

    normalized: List[int] = []
    for dep_id in cleaned:
        if _has_dependency_path(
            dep_map,
            start_id=dep_id,
            target_id=task_id,
            ignored_edge=(task_id, dep_id),
        ):
            issues.append(
                DependencyIssue(
                    code="dependency_cycle",
                    message=f"Task {task_id} dependency on task {dep_id} would create a cycle.",
                    task_id=task_id,
                    dependency_id=dep_id,
                )
            )
            continue
        normalized.append(dep_id)
    return DependencyNormalization(
        task_id=task_id,
        original_dependencies=original,
        normalized_dependencies=_dedupe(normalized),
        issues=issues,
    )


def normalize_plan_dependencies(tree: PlanTree) -> PlanDependencyNormalization:
    dep_map: Dict[int, List[int]] = {}
    issues: List[DependencyIssue] = []
    changed: Dict[int, List[int]] = {}

    for node in tree.iter_nodes():
        cleaned, node_issues = _expand_and_clean_dependencies(
            tree,
            task_id=node.id,
            dependencies=list(node.dependencies or []),
        )
        dep_map[node.id] = cleaned
        issues.extend(node_issues)

    for task_id, deps in list(dep_map.items()):
        normalized: List[int] = []
        for dep_id in deps:
            if _has_dependency_path(
                dep_map,
                start_id=dep_id,
                target_id=task_id,
                ignored_edge=(task_id, dep_id),
            ):
                issues.append(
                    DependencyIssue(
                        code="dependency_cycle",
                        message=f"Task {task_id} dependency on task {dep_id} would create a cycle.",
                        task_id=task_id,
                        dependency_id=dep_id,
                    )
                )
                continue
            normalized.append(dep_id)
        normalized = _dedupe(normalized)
        dep_map[task_id] = normalized
        node = tree.nodes.get(task_id)
        if node is not None and list(node.dependencies or []) != normalized:
            changed[task_id] = normalized

    return PlanDependencyNormalization(dependencies_by_task=changed, issues=issues)
