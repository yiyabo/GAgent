from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from .artifact_contracts import (
    canonical_artifact_path,
    infer_artifact_contract,
    load_artifact_manifest,
    resolve_manifest_aliases,
)
from .plan_models import PlanNode, PlanTree

_COMPLETED_LIKE = {"completed", "done", "success"}


class ArtifactPreflightIssue(BaseModel):
    code: str
    severity: str
    message: str
    alias: Optional[str] = None
    task_id: Optional[int] = None
    related_task_ids: List[int] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskArtifactContractSnapshot(BaseModel):
    task_id: int
    task_name: str
    raw_status: str = "pending"
    explicit_requires: List[str] = Field(default_factory=list)
    explicit_publishes: List[str] = Field(default_factory=list)
    inferred_requires: List[str] = Field(default_factory=list)
    inferred_publishes: List[str] = Field(default_factory=list)
    requires: List[str] = Field(default_factory=list)
    publishes: List[str] = Field(default_factory=list)
    contract_source: str = "none"


class ArtifactPreflightResult(BaseModel):
    plan_id: int
    ok: bool = True
    errors: List[ArtifactPreflightIssue] = Field(default_factory=list)
    warnings: List[ArtifactPreflightIssue] = Field(default_factory=list)
    task_contracts: List[TaskArtifactContractSnapshot] = Field(default_factory=list)
    manifest_resolved_aliases: Dict[str, str] = Field(default_factory=dict)

    def has_errors(self) -> bool:
        return bool(self.errors)

    def affected_task_ids(self) -> List[int]:
        task_ids: Set[int] = set()
        for issue in self.errors:
            if issue.task_id is not None and issue.task_id > 0:
                task_ids.add(issue.task_id)
            for related_task_id in issue.related_task_ids:
                if related_task_id > 0:
                    task_ids.add(related_task_id)
        return sorted(task_ids)

    def summary(self, *, max_issues: int = 3) -> str:
        if not self.errors:
            return f"Artifact preflight passed for plan #{self.plan_id}."
        messages = [issue.message for issue in self.errors[:max_issues]]
        remaining = max(0, len(self.errors) - len(messages))
        suffix = f" (+{remaining} more issue(s))" if remaining else ""
        return f"Artifact preflight failed for plan #{self.plan_id}: {'; '.join(messages)}{suffix}"


class ArtifactPreflightService:
    def validate_plan(
        self,
        plan_id: int,
        tree: PlanTree,
        *,
        task_ids: Optional[Iterable[int]] = None,
        manifest: Optional[Dict[str, Any]] = None,
    ) -> ArtifactPreflightResult:
        selected_task_ids = self._select_task_ids(tree, task_ids)
        manifest = manifest if isinstance(manifest, dict) else load_artifact_manifest(plan_id)

        task_contracts: List[TaskArtifactContractSnapshot] = []
        errors: List[ArtifactPreflightIssue] = []
        warnings: List[ArtifactPreflightIssue] = []
        publisher_map: Dict[str, List[int]] = {}
        consumer_map: Dict[str, List[int]] = {}
        selected_nodes = [tree.nodes[task_id] for task_id in selected_task_ids]

        for node in selected_nodes:
            snapshot, snapshot_issues = self._build_contract_snapshot(plan_id, node)
            task_contracts.append(snapshot)
            for issue in snapshot_issues:
                if issue.severity == "error":
                    errors.append(issue)
                else:
                    warnings.append(issue)
            for alias in snapshot.explicit_requires:
                consumer_map.setdefault(alias, []).append(node.id)
            for alias in snapshot.explicit_publishes:
                publisher_map.setdefault(alias, []).append(node.id)

        aliases_to_resolve = set(consumer_map) | set(publisher_map)
        manifest_resolved = resolve_manifest_aliases(manifest, aliases_to_resolve)

        for alias, publishers in sorted(publisher_map.items()):
            if len(publishers) <= 1:
                continue
            consumers = consumer_map.get(alias) or []
            related_task_ids = sorted(set(publishers + consumers))
            # Downgrade to warning: multiple producers for the same alias is
            # common when decomposer assigns generic aliases (e.g. evidence_md)
            # to parallel tasks that produce different files.  Blocking all of
            # them is too aggressive — each task's output is path-isolated.
            warnings.append(
                ArtifactPreflightIssue(
                    code="ambiguous_producer",
                    severity="warning",
                    alias=alias,
                    related_task_ids=related_task_ids,
                    message=(
                        f"Artifact alias '{alias}' has multiple producer tasks {sorted(publishers)}; "
                        "canonical authority prefers a single producer."
                    ),
                )
            )

        edges = self._build_producer_edges(consumer_map, publisher_map, manifest_resolved)
        for cycle in self._detect_cycles(edges):
            errors.append(
                ArtifactPreflightIssue(
                    code="artifact_cycle",
                    severity="error",
                    related_task_ids=list(cycle),
                    message=(
                        "Artifact contract cycle detected across tasks "
                        + " -> ".join(f"#{task_id}" for task_id in cycle)
                        + "."
                    ),
                )
            )

        for alias, consumers in sorted(consumer_map.items()):
            if alias in manifest_resolved:
                continue
            publishers = publisher_map.get(alias) or []
            if publishers:
                continue
            # If the alias is not in the canonical registry, downgrade to
            # warning — it was likely LLM-generated and has no registered
            # canonical path.  Only registered aliases that genuinely lack
            # a producer should block execution.
            is_registered = canonical_artifact_path(plan_id, alias) is not None
            severity = "error" if is_registered else "warning"
            target_list = errors if is_registered else warnings
            for consumer_task_id in sorted(set(consumers)):
                target_list.append(
                    ArtifactPreflightIssue(
                        code="missing_producer",
                        severity=severity,
                        alias=alias,
                        task_id=consumer_task_id,
                        related_task_ids=[consumer_task_id],
                        message=(
                            f"Task #{consumer_task_id} requires artifact alias '{alias}', "
                            "but no in-plan producer or canonical manifest entry was found."
                        ),
                    )
                )

        manifest_artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
        for snapshot in task_contracts:
            if snapshot.raw_status not in _COMPLETED_LIKE:
                continue
            missing_aliases: List[str] = []
            for alias in snapshot.publishes:
                entry = manifest_artifacts.get(alias) if isinstance(manifest_artifacts, dict) else None
                producer_task_id = int(entry.get("producer_task_id") or -1) if isinstance(entry, dict) else -1
                if alias not in manifest_resolved or producer_task_id != snapshot.task_id:
                    missing_aliases.append(alias)
            if not missing_aliases:
                continue
            warnings.append(
                ArtifactPreflightIssue(
                    code="completed_task_missing_publish",
                    severity="warning",
                    task_id=snapshot.task_id,
                    alias=missing_aliases[0],
                    related_task_ids=[snapshot.task_id],
                    metadata={"missing_aliases": missing_aliases},
                    message=(
                        f"Task #{snapshot.task_id} is marked {snapshot.raw_status}, but canonical "
                        f"published artifacts are missing for aliases {missing_aliases}."
                    ),
                )
            )

        return ArtifactPreflightResult(
            plan_id=plan_id,
            ok=not errors,
            errors=errors,
            warnings=warnings,
            task_contracts=sorted(task_contracts, key=lambda item: item.task_id),
            manifest_resolved_aliases=dict(sorted(manifest_resolved.items())),
        )

    @staticmethod
    def _select_task_ids(tree: PlanTree, task_ids: Optional[Iterable[int]]) -> List[int]:
        nodes = getattr(tree, "nodes", None)
        if not isinstance(nodes, dict):
            return []
        if task_ids is None:
            return sorted(nodes)
        selected = sorted({int(task_id) for task_id in task_ids if int(task_id) in nodes})
        return selected if selected else sorted(nodes)

    def _build_contract_snapshot(
        self,
        plan_id: int,
        node: PlanNode,
    ) -> Tuple[TaskArtifactContractSnapshot, List[ArtifactPreflightIssue]]:
        metadata = getattr(node, "metadata", None)
        metadata = metadata if isinstance(metadata, dict) else {}
        task_id = int(getattr(node, "id", 0) or 0)
        display_name_getter = getattr(node, "display_name", None)
        if callable(display_name_getter):
            task_name = str(display_name_getter() or "").strip() or f"Task {task_id}"
        else:
            task_name = str(getattr(node, "name", "") or "").strip() or f"Task {task_id}"
        instruction = str(getattr(node, "instruction", "") or "")
        raw_contract = metadata.get("artifact_contract") if isinstance(metadata.get("artifact_contract"), dict) else {}
        explicit_requires, explicit_require_errors = self._normalize_explicit_aliases(
            plan_id,
            raw_contract.get("requires"),
            task_id=task_id,
            field_name="requires",
        )
        explicit_publishes, explicit_publish_errors = self._normalize_explicit_aliases(
            plan_id,
            raw_contract.get("publishes"),
            task_id=task_id,
            field_name="publishes",
        )

        inferred_contract = infer_artifact_contract(
            task_name=task_name,
            instruction=instruction,
            metadata=metadata,
        )
        inferred_requires = [
            alias
            for alias in inferred_contract.get("requires", [])
            if alias not in explicit_requires
        ]
        inferred_publishes = [
            alias
            for alias in inferred_contract.get("publishes", [])
            if alias not in explicit_publishes
        ]

        requires = list(explicit_requires)
        requires.extend(alias for alias in inferred_requires if alias not in requires)
        publishes = list(explicit_publishes)
        publishes.extend(alias for alias in inferred_publishes if alias not in publishes)

        contract_source = "none"
        if explicit_requires or explicit_publishes:
            contract_source = "explicit"
            if inferred_requires or inferred_publishes:
                contract_source = "mixed"
        elif inferred_requires or inferred_publishes:
            contract_source = "inferred"

        snapshot = TaskArtifactContractSnapshot(
            task_id=task_id,
            task_name=task_name,
            raw_status=str(getattr(node, "status", None) or "pending").strip().lower() or "pending",
            explicit_requires=explicit_requires,
            explicit_publishes=explicit_publishes,
            inferred_requires=inferred_requires,
            inferred_publishes=inferred_publishes,
            requires=requires,
            publishes=publishes,
            contract_source=contract_source,
        )
        return snapshot, explicit_require_errors + explicit_publish_errors

    @staticmethod
    def _normalize_explicit_aliases(
        plan_id: int,
        raw_items: Any,
        *,
        task_id: int,
        field_name: str,
    ) -> Tuple[List[str], List[ArtifactPreflightIssue]]:
        if raw_items is None:
            return [], []
        if not isinstance(raw_items, list):
            return [], [
                ArtifactPreflightIssue(
                    code="invalid_artifact_contract_field",
                    severity="error",
                    task_id=task_id,
                    message=(
                        f"Task #{task_id} has non-list artifact_contract.{field_name}; "
                        "expected a list of canonical artifact aliases."
                    ),
                    metadata={"field": field_name},
                )
            ]

        aliases: List[str] = []
        errors: List[ArtifactPreflightIssue] = []
        seen: Set[str] = set()
        for item in raw_items:
            alias = str(item or "").strip()
            if not alias or alias in seen:
                continue
            if canonical_artifact_path(plan_id, alias) is None:
                # Unknown alias: downgrade to warning so review/optimize are
                # not blocked.  The alias is still tracked so dependency
                # analysis remains functional for LLM-generated contracts.
                errors.append(
                    ArtifactPreflightIssue(
                        code="unknown_artifact_alias",
                        severity="warning",
                        task_id=task_id,
                        alias=alias,
                        message=(
                            f"Task #{task_id} declares unregistered artifact alias '{alias}' in "
                            f"artifact_contract.{field_name}. It will be tracked but may not "
                            f"resolve to a canonical path."
                        ),
                        metadata={"field": field_name},
                    )
                )
            seen.add(alias)
            aliases.append(alias)
        return aliases, errors

    @staticmethod
    def _build_producer_edges(
        consumer_map: Dict[str, List[int]],
        publisher_map: Dict[str, List[int]],
        manifest_resolved: Dict[str, str],
    ) -> Dict[int, Set[int]]:
        edges: Dict[int, Set[int]] = {}
        for alias, consumers in consumer_map.items():
            if alias in manifest_resolved and not publisher_map.get(alias):
                continue
            publishers = publisher_map.get(alias) or []
            if len(publishers) != 1:
                continue
            producer_id = publishers[0]
            targets = edges.setdefault(producer_id, set())
            for consumer_id in consumers:
                targets.add(consumer_id)
        return edges

    @staticmethod
    def _detect_cycles(edges: Dict[int, Set[int]]) -> List[List[int]]:
        visited: Set[int] = set()
        visiting: Set[int] = set()
        stack: List[int] = []
        seen_cycles: Set[Tuple[int, ...]] = set()
        cycles: List[List[int]] = []

        def _dfs(node_id: int) -> None:
            if node_id in visiting:
                if node_id in stack:
                    start = stack.index(node_id)
                    cycle = stack[start:] + [node_id]
                    cycle_tuple = tuple(cycle)
                    if cycle_tuple not in seen_cycles:
                        seen_cycles.add(cycle_tuple)
                        cycles.append(cycle)
                return
            if node_id in visited:
                return
            visiting.add(node_id)
            stack.append(node_id)
            for child_id in sorted(edges.get(node_id, set())):
                _dfs(child_id)
            stack.pop()
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in sorted(edges):
            _dfs(node_id)
        return cycles


__all__ = [
    "ArtifactPreflightIssue",
    "ArtifactPreflightResult",
    "ArtifactPreflightService",
    "TaskArtifactContractSnapshot",
]