"""Artifact-based dependency enrichment for plan trees.

Scans a PlanTree, builds artifact producer/consumer maps, injects missing
dependency edges, validates the enriched DAG, and provides runtime readiness
guards for task execution.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, cast

from .dependency_validation import normalize_plan_dependencies
from .plan_models import PlanNode, PlanTree

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class EnrichmentEdge:
    consumer_task_id: int
    producer_task_id: int
    artifact_alias: str


@dataclass
class EnrichmentResult:
    added_edges: List[EnrichmentEdge] = field(default_factory=list)
    skipped_ambiguous_aliases: List[str] = field(default_factory=list)
    skipped_cycle_edges: List[EnrichmentEdge] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class DagValidationIssue:
    code: str          # "orphan_consumer" | "dependency_cycle"
    severity: str      # "warning" | "error"
    message: str
    task_ids: List[int] = field(default_factory=list)
    alias: Optional[str] = None


@dataclass
class DagValidationResult:
    errors: List[DagValidationIssue] = field(default_factory=list)
    warnings: List[DagValidationIssue] = field(default_factory=list)

    def has_errors(self) -> bool:
        return bool(self.errors)

    def summary(self, *, max_issues: int = 3) -> str:
        if not self.errors:
            return "DAG validation passed."
        messages = [issue.message for issue in self.errors[:max_issues]]
        remaining = max(0, len(self.errors) - len(messages))
        suffix = f" (+{remaining} more)" if remaining else ""
        return f"DAG validation failed: {'; '.join(messages)}{suffix}"


@dataclass
class MissingArtifact:
    alias: str
    expected_producer_task_id: Optional[int]
    reason: str  # "producer_not_completed" | "file_not_found"


@dataclass
class ArtifactReadinessBlock:
    task_id: int
    missing_artifacts: List[MissingArtifact] = field(default_factory=list)
    reason: str = ""


# ---------------------------------------------------------------------------
# Regex helpers for instruction-based artifact extraction
# ---------------------------------------------------------------------------

_FILE_RE = re.compile(r'[\w\-/]+\.(md|json|csv|txt|tex|yaml|yml|bib|pdf)')

# Keywords that indicate consuming an artifact
_CONSUME_KEYWORDS = re.compile(
    r'(?:从|基于|根据|对|读取|加载|使用|合并|整合|提取|筛选|校验|润色|'
    r'from|based on|using|merge|integrate|extract|polish|review|read)\s',
    re.IGNORECASE,
)

# Keywords that indicate producing an artifact
_PRODUCE_KEYWORDS = re.compile(
    r'(?:输出|生成|写入|保存|撰写|起草|创建|'
    r'output|generate|write|save|produce|create|draft)\s',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_artifacts_for_node(node: PlanNode) -> Tuple[List[str], List[str]]:
    """Extract consumed (requires) and produced (publishes) artifact aliases.

    First tries the existing ``artifact_contract`` metadata block.  Falls back
    to regex-based extraction from the instruction text.
    """
    requires: List[str] = []
    publishes: List[str] = []

    # Try explicit metadata first
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    contract = metadata.get("artifact_contract")
    if isinstance(contract, dict):
        raw_requires = contract.get("requires") or contract.get("consumes") or []
        raw_publishes = contract.get("publishes") or contract.get("produces") or []
        if isinstance(raw_requires, list):
            requires = [str(a).strip() for a in raw_requires if str(a).strip()]
        if isinstance(raw_publishes, list):
            publishes = [str(a).strip() for a in raw_publishes if str(a).strip()]

    # Fall back to instruction-based extraction if no explicit contract
    if not requires and not publishes:
        instruction = node.instruction or ""
        requires, publishes = _extract_artifacts_from_instruction(instruction)

    return requires, publishes


def _extract_artifacts_from_instruction(text: str) -> Tuple[List[str], List[str]]:
    """Extract file references from instruction text using regex + keyword heuristics."""
    if not text:
        return [], []

    requires: List[str] = []
    publishes: List[str] = []

    for m in _FILE_RE.finditer(text):
        match = m.group(0)
        # Look at the text window before the file reference
        idx = m.start()
        window_start = max(0, idx - 30)
        window = text[window_start:idx].lower()

        if _PRODUCE_KEYWORDS.search(window):
            if match not in publishes:
                publishes.append(match)
        elif _CONSUME_KEYWORDS.search(window):
            if match not in requires:
                requires.append(match)
        else:
            # Default: if file appears in instruction, treat as consumed
            # (conservative — better to over-connect than under-connect)
            if match not in requires:
                requires.append(match)

    return requires, publishes


def _would_create_cycle(tree: PlanTree, producer_id: int, consumer_id: int) -> bool:
    """Check if adding consumer -> producer dependency would create a cycle.

    Returns True if *consumer_id* is reachable from *producer_id* via existing
    dependencies (meaning producer already depends on consumer, directly or
    transitively).
    """
    visited: Set[int] = set()
    stack = [producer_id]

    while stack:
        current = stack.pop()
        if current == consumer_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        node = tree.nodes.get(current)
        if node is None:
            continue
        for dep_id in (node.dependencies or []):
            if dep_id not in visited:
                stack.append(dep_id)

    return False


def _strip_namespace(alias: str) -> str:
    if '.' in alias:
        return alias.split('.', 1)[1]
    return alias


def _normalize_base_name(name: str) -> str:
    name = name.lower().strip()
    for ext in ('.csv', '.tsv', '.json', '.txt', '.md', '_csv', '_tsv', '_json', '_txt', '_md'):
        if name.endswith(ext):
            name = name[:-len(ext)]
            break
    name = re.sub(r'[_\-\s]+', '_', name)
    for suffix in ('_table', '_file', '_data'):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name


def _fuzzy_match_alias(
    required_alias: str,
    producer_map: Dict[str, int],
    tree: PlanTree,
) -> Optional[Tuple[str, int]]:
    req_base = _normalize_base_name(_strip_namespace(required_alias))
    
    candidates: List[Tuple[float, str, int]] = []
    
    for published_alias, producer_id in producer_map.items():
        pub_base = _normalize_base_name(_strip_namespace(published_alias))
        
        if req_base == pub_base:
            candidates.append((1.0, published_alias, producer_id))
            continue
        
        req_keywords = set(re.split(r'[_\-\s]+', req_base))
        pub_keywords = set(re.split(r'[_\-\s]+', pub_base))
        
        if not req_keywords or not pub_keywords:
            continue
        
        intersection = req_keywords & pub_keywords
        union = req_keywords | pub_keywords
        similarity = len(intersection) / len(union) if union else 0.0
        
        core_concepts = {'dataset', 'metadata', 'table', 'annotation', 'working', 'annotated'}
        shared_core = req_keywords & pub_keywords & core_concepts
        
        if similarity >= 0.5 or (shared_core and similarity >= 0.3):
            candidates.append((similarity, published_alias, producer_id))
    
    if not candidates:
        return None
    
    candidates.sort(key=lambda x: (-x[0], x[2]))
    
    best_similarity, best_alias, best_producer = candidates[0]
    
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.1:
        consumer_task_ids = [
            node.id for node in tree.iter_nodes()
            if required_alias in (node.metadata.get('artifact_contract', {}).get('requires', []) if isinstance(node.metadata, dict) else [])
        ]
        if consumer_task_ids:
            consumer_node = tree.nodes.get(consumer_task_ids[0])
            if consumer_node and consumer_node.instruction:
                instruction_lower = consumer_node.instruction.lower()
                for sim, alias, prod_id in candidates:
                    prod_node = tree.nodes.get(prod_id)
                    if prod_node and prod_node.name:
                        if prod_node.name.lower() in instruction_lower:
                            return (alias, prod_id)
    
    return (best_alias, best_producer)


def _resolve_fuzzy_aliases(
    tree: PlanTree,
    producer_map: Dict[str, int],
    consumer_map: Dict[str, List[int]],
    ambiguous: Set[str],
) -> None:
    unresolved_aliases = [
        alias for alias in consumer_map.keys()
        if alias not in producer_map and alias not in ambiguous
    ]
    
    for required_alias in unresolved_aliases:
        match = _fuzzy_match_alias(required_alias, producer_map, tree)
        if not match:
            continue
        
        matched_alias, producer_id = match
        producer_node = tree.nodes.get(producer_id)
        if not producer_node:
            continue
        
        metadata = producer_node.metadata if isinstance(producer_node.metadata, dict) else {}
        contract = metadata.get('artifact_contract', {})
        if not isinstance(contract, dict):
            contract = {}
        
        publishes = contract.get('publishes', [])
        if not isinstance(publishes, list):
            publishes = []
        
        if required_alias not in publishes:
            publishes.append(required_alias)
            contract['publishes'] = publishes
            metadata['artifact_contract'] = contract
            producer_node.metadata = metadata
            
            producer_map[required_alias] = producer_id
            
            logger.info(
                "Fuzzy-matched artifact alias: '%s' (required) -> '%s' (published by task %d)",
                required_alias,
                matched_alias,
                producer_id,
            )


def _detect_cycles(tree: PlanTree) -> List[List[int]]:
    """Detect cycles in the dependency graph using DFS (white/gray/black)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[int, int] = {nid: WHITE for nid in tree.nodes}
    cycles: List[List[int]] = []
    path: List[int] = []

    def dfs(node_id: int) -> None:
        color[node_id] = GRAY
        path.append(node_id)
        node = tree.nodes.get(node_id)
        if node:
            for dep_id in (node.dependencies or []):
                if dep_id not in color:
                    continue
                if color[dep_id] == GRAY:
                    # Found a cycle
                    cycle_start = path.index(dep_id)
                    cycles.append(list(path[cycle_start:]))
                elif color[dep_id] == WHITE:
                    dfs(dep_id)
        path.pop()
        color[node_id] = BLACK

    for node_id in sorted(tree.nodes):
        if color.get(node_id) == WHITE:
            dfs(node_id)

    return cycles


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enrich_plan_dependencies(tree: PlanTree) -> EnrichmentResult:
    """Scan a PlanTree, build artifact producer/consumer maps, and inject
    missing dependency edges.

    The function is **idempotent** — running it twice produces the same graph
    as running it once.  On unexpected errors the tree is left unmodified and
    an ``EnrichmentResult`` with a non-None ``error`` field is returned.
    """
    try:
        return _enrich_impl(tree)
    except Exception as exc:
        logger.error("Unexpected error during dependency enrichment: %s", exc)
        return EnrichmentResult(error=str(exc))


def _enrich_impl(tree: PlanTree) -> EnrichmentResult:
    result = EnrichmentResult()

    # Step 1: Build producer/consumer maps from artifact contracts
    producer_map: Dict[str, int] = {}   # alias -> single producer task_id
    consumer_map: Dict[str, List[int]] = {}  # alias -> list of consumer task_ids
    ambiguous: Set[str] = set()

    for node in tree.iter_nodes():
        try:
            requires, publishes = _extract_artifacts_for_node(node)
        except Exception as exc:
            logger.warning(
                "Failed to extract artifacts for task %s: %s", node.id, exc
            )
            continue

        for alias in publishes:
            if alias in ambiguous:
                continue
            if alias in producer_map and producer_map[alias] != node.id:
                logger.warning(
                    "Ambiguous artifact producer for '%s': tasks %s and %s",
                    alias,
                    producer_map[alias],
                    node.id,
                )
                ambiguous.add(alias)
                del producer_map[alias]
                result.skipped_ambiguous_aliases.append(alias)
                continue
            producer_map[alias] = node.id

        for alias in requires:
            consumer_map.setdefault(alias, []).append(node.id)

    # Step 1.5: Fuzzy alias matching — resolve namespace/format mismatches
    # For each required alias with no exact producer, try to find a semantically
    # similar published alias and add it to the producer's contract.
    _resolve_fuzzy_aliases(tree, producer_map, consumer_map, ambiguous)

    # Step 2: Inject missing dependency edges
    for alias, consumers in consumer_map.items():
        if alias in ambiguous or alias not in producer_map:
            continue
        producer_id = producer_map[alias]

        for consumer_id in consumers:
            if producer_id == consumer_id:
                continue
            consumer_node = tree.nodes.get(consumer_id)
            if consumer_node is None:
                continue
            if producer_id in (consumer_node.dependencies or []):
                continue  # Already exists — idempotent

            # Cycle check: verify consumer is not reachable from producer
            if _would_create_cycle(tree, producer_id, consumer_id):
                edge = EnrichmentEdge(consumer_id, producer_id, alias)
                result.skipped_cycle_edges.append(edge)
                logger.info(
                    "Skipped cycle-creating edge: task %s -> task %s via '%s'",
                    consumer_id,
                    producer_id,
                    alias,
                )
                continue

            # Inject the edge
            consumer_node.dependencies.append(producer_id)
            edge = EnrichmentEdge(consumer_id, producer_id, alias)
            result.added_edges.append(edge)
            logger.info(
                "Injected dependency: task %s depends on task %s (artifact: '%s')",
                consumer_id,
                producer_id,
                alias,
            )

    # Step 3: Role-based structural enrichment — identify task roles
    # (PARALLEL_SHARD, AGGREGATOR, FINISHER, PIPELINE_STEP) and inject
    # missing dependency edges based on sibling frontier analysis.
    _enrich_structural(tree, result)

    if not result.added_edges:
        logger.info("No implicit dependencies found during enrichment.")

    return result


# ---------------------------------------------------------------------------
# Role-based structural enrichment
# ---------------------------------------------------------------------------

_FINISHER_KWS = (
    "最终", "最后", "全文", "全局", "统一格式", "格式标准化",
    "语言润色", "润色", "校对", "定稿", "发布", "导出", "提交",
    "final", "polish", "proofread", "format check", "publish", "export",
)

_AGGREGATOR_KWS = (
    "合并", "整合", "汇总", "综合", "统一", "归并",
    "生成统一", "整编", "汇编",
    "merge", "integrate", "combine", "consolidate", "unify",
)

_PARALLEL_ACTION_WORDS = (
    "收集", "整理", "撰写", "分析", "提取", "汇总",
    "collect", "gather", "write", "analyze", "extract",
)

_SCOPE_GLOBAL_KWS = ("全文", "全局", "整体", "最终版", "full text", "global", "overall")
_SCOPE_PARENT_KWS = ("统一", "整合各章节", "当前部分", "本节", "this section")


def _infer_role(node: PlanNode, siblings: List[PlanNode]) -> str:
    """Infer a task's structural role from its title and instruction."""
    text = (node.name or "") + " " + (node.instruction or "")[:300]

    if any(kw in text for kw in _FINISHER_KWS):
        return "FINISHER"
    if any(kw in text for kw in _AGGREGATOR_KWS):
        return "AGGREGATOR"

    # Parallel shard detection: same action template as siblings, different topic
    if siblings and _looks_like_parallel_shard(node, siblings):
        return "PARALLEL_SHARD"

    return "UNKNOWN"


def _infer_scope(node: PlanNode) -> str:
    """Infer whether a task operates on local, parent, or global scope."""
    text = (node.name or "") + " " + (node.instruction or "")[:300]
    if any(kw in text for kw in _SCOPE_GLOBAL_KWS):
        return "GLOBAL"
    if any(kw in text for kw in _SCOPE_PARENT_KWS):
        return "PARENT"
    return "LOCAL"


def _looks_like_parallel_shard(node: PlanNode, siblings: List[PlanNode]) -> bool:
    """Check if node shares an action template with siblings (different topic)."""
    title = node.name or ""
    matching = 0
    for sib in siblings:
        sib_title = sib.name or ""
        if any(w in title and w in sib_title for w in _PARALLEL_ACTION_WORDS):
            matching += 1
    return matching >= 1


def _sibling_frontier(
    node: PlanNode,
    siblings: List[PlanNode],
    tree: PlanTree,
) -> List[PlanNode]:
    """Compute the frontier: preceding siblings not dominated by another preceding sibling.

    A sibling A is "dominated" if there exists another preceding sibling B
    such that A → B is reachable (A's output flows through B).
    """
    prev = [s for s in siblings if (getattr(s, "position", 0) or 0) < (getattr(node, "position", 0) or 0)]
    if not prev:
        return []

    prev_ids = {s.id for s in prev}
    frontier = []
    for a in prev:
        dominated = False
        for b in prev:
            if a.id == b.id:
                continue
            if _has_path(tree, a.id, b.id):
                dominated = True
                break
        if not dominated:
            frontier.append(a)
    return frontier


def _has_path(tree: PlanTree, src: int, dst: int) -> bool:
    """BFS check: is dst reachable from src via dependencies?"""
    visited: Set[int] = set()
    queue = [src]
    while queue:
        current = queue.pop(0)
        if current == dst:
            return True
        if current in visited:
            continue
        visited.add(current)
        # Follow forward edges: find nodes that depend on current
        for node in tree.iter_nodes():
            if current in (node.dependencies or []) and node.id not in visited:
                queue.append(node.id)
    return False


def _enrich_structural(tree: PlanTree, result: EnrichmentResult) -> None:
    """Role-based structural enrichment using frontier analysis.

    1. Group leaf tasks by parent
    2. Infer role (PARALLEL_SHARD / AGGREGATOR / FINISHER / UNKNOWN) for each
    3. For AGGREGATOR tasks: inject deps on all relevant frontier siblings
    4. For FINISHER tasks: inject deps on the frontier (typically the preceding sibling)
    5. Parallel shards are never wired to each other
    """
    # Group leaf children by parent
    parent_children: Dict[Optional[int], List[PlanNode]] = {}
    for node in tree.iter_nodes():
        if tree.children_ids(node.id):
            continue
        parent_children.setdefault(node.parent_id, []).append(node)

    for parent_id, children in parent_children.items():
        if len(children) < 2:
            continue

        children.sort(key=lambda n: (getattr(n, "position", 0) or 0, n.id))

        # Infer roles
        roles: Dict[int, str] = {}
        for child in children:
            siblings = [c for c in children if c.id != child.id]
            roles[child.id] = _infer_role(child, siblings)

        sibling_ids = {n.id for n in children}

        for child in children:
            role = roles[child.id]
            existing_deps = set(child.dependencies or [])
            has_sibling_dep = bool(existing_deps & sibling_ids)

            if role == "PARALLEL_SHARD":
                continue  # Never wire parallel shards to each other

            if role not in ("AGGREGATOR", "FINISHER"):
                continue  # Only enrich aggregators and finishers

            if has_sibling_dep:
                continue  # Already has at least one sibling dependency

            frontier = _sibling_frontier(child, children, tree)
            if not frontier:
                continue

            # For AGGREGATOR: connect to all frontier nodes
            # For FINISHER: connect to all frontier nodes (usually just one)
            for upstream in frontier:
                # Don't wire two parallel shards together
                if roles.get(upstream.id) == "PARALLEL_SHARD" and role == "FINISHER":
                    # Finisher should depend on the aggregator, not individual shards.
                    # But if there's no aggregator in the frontier, allow it.
                    has_aggregator_in_frontier = any(
                        roles.get(f.id) == "AGGREGATOR" for f in frontier
                    )
                    if has_aggregator_in_frontier:
                        continue

                if _would_create_cycle(tree, upstream.id, child.id):
                    continue

                child.dependencies.append(upstream.id)
                alias = f"__structural_{role.lower()}__"
                edge = EnrichmentEdge(child.id, upstream.id, alias)
                result.added_edges.append(edge)
                logger.info(
                    "Injected structural dep: task %s (%s) depends on task %s "
                    "(frontier under parent %s)",
                    child.id,
                    role,
                    upstream.id,
                    parent_id,
                )


def validate_plan_dag(
    tree: PlanTree,
    manifest: Optional[Dict[str, Any]] = None,
) -> DagValidationResult:
    """Validate the enriched dependency graph for structural problems.

    Checks for orphan consumers (warnings) and dependency cycles (errors).
    """
    result = DagValidationResult()

    # Load manifest if not provided (lazy import to avoid circular deps)
    if manifest is None:
        try:
            from .artifact_contracts import load_artifact_manifest

            manifest = load_artifact_manifest(tree.id)
        except Exception:
            manifest = {}

    manifest_artifacts = (
        manifest.get("artifacts", {}) if isinstance(manifest, dict) else {}
    )

    # Build producer map for orphan check
    producer_map: Dict[str, int] = {}
    consumer_map: Dict[str, List[int]] = {}

    for node in tree.iter_nodes():
        try:
            requires, publishes = _extract_artifacts_for_node(node)
        except Exception:
            continue
        for alias in publishes:
            producer_map.setdefault(alias, node.id)
        for alias in requires:
            consumer_map.setdefault(alias, []).append(node.id)

    # Check 1: Orphan consumers
    for alias, consumers in consumer_map.items():
        if alias not in producer_map and alias not in manifest_artifacts:
            for consumer_id in consumers:
                issue = DagValidationIssue(
                    code="orphan_consumer",
                    severity="warning",
                    message=(
                        f"Task {consumer_id} consumes '{alias}' but no producer "
                        f"found in plan or manifest."
                    ),
                    task_ids=[consumer_id],
                    alias=alias,
                )
                result.warnings.append(issue)
                logger.warning(issue.message)

    # Check 2: Generic structural dependency validation
    normalization = normalize_plan_dependencies(tree)
    structural_codes = {
        "self_dependency",
        "ancestor_dependency",
        "descendant_dependency",
        "missing_dependency",
        "invalid_dependency",
    }
    for structural_issue in normalization.issues:
        if structural_issue.code not in structural_codes:
            continue
        issue = DagValidationIssue(
            code=structural_issue.code,
            severity="error",
            message=structural_issue.message,
            task_ids=[
                task_id for task_id in (structural_issue.task_id, structural_issue.dependency_id)
                if task_id is not None
            ],
        )
        result.errors.append(issue)
        logger.error(issue.message)

    # Check 3: Cycle detection via DFS
    cycles = _detect_cycles(tree)
    for cycle in cycles:
        issue = DagValidationIssue(
            code="dependency_cycle",
            severity="error",
            message=f"Dependency cycle detected involving tasks: {cycle}",
            task_ids=list(cycle),
        )
        result.errors.append(issue)
        logger.error(issue.message)

    return result


def check_artifact_readiness(
    node: Optional[PlanNode],
    tree: PlanTree,
    manifest: Optional[Dict[str, Any]] = None,
    *,
    state_by_task: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Optional[ArtifactReadinessBlock]:
    """Check if a task's consumed artifacts are ready for execution.

    Returns ``None`` if ready, or an :class:`ArtifactReadinessBlock` with
    details about what is missing.  On unexpected errors the function
    returns ``None`` (fail-open) so that execution is not blocked.
    """
    if node is None:
        return None
    try:
        return _check_readiness_impl(node, tree, manifest, state_by_task)
    except Exception as exc:
        logger.error(
            "Readiness check failed for task %s: %s (allowing execution)",
            node.id,
            exc,
        )
        return None  # Fail-open


def _check_readiness_impl(
    node: PlanNode,
    tree: PlanTree,
    manifest: Optional[Dict[str, Any]],
    state_by_task: Optional[Dict[int, Dict[str, Any]]],
) -> Optional[ArtifactReadinessBlock]:
    try:
        requires, _ = _extract_artifacts_for_node(node)
    except Exception:
        return None

    if not requires:
        return None

    # Build producer map
    producer_map: Dict[str, int] = {}
    for other_node in tree.iter_nodes():
        if other_node.id == node.id:
            continue
        try:
            _, publishes = _extract_artifacts_for_node(other_node)
        except Exception:
            continue
        for alias in publishes:
            producer_map.setdefault(alias, other_node.id)

    missing: List[MissingArtifact] = []

    for alias in requires:
        producer_id = producer_map.get(alias)
        if producer_id is None:
            # External input — skip check
            continue

        producer_node = tree.nodes.get(producer_id)
        if producer_node is None:
            continue

        producer_status = _producer_effective_status(producer_node, state_by_task)
        if producer_status not in ("completed", "done", "success"):
            missing.append(
                MissingArtifact(
                    alias=alias,
                    expected_producer_task_id=producer_id,
                    reason="producer_not_completed",
                )
            )

    if not missing:
        return None

    reasons = [
        f"'{m.alias}' (producer task {m.expected_producer_task_id}: {m.reason})"
        for m in missing
    ]
    return ArtifactReadinessBlock(
        task_id=node.id,
        missing_artifacts=missing,
        reason=f"Missing input artifacts: {', '.join(reasons)}",
    )


def _producer_effective_status(
    producer_node: PlanNode,
    state_by_task: Optional[Dict[int, Dict[str, Any]]],
) -> str:
    """Return producer status using resolver state when available.

    The full-plan runner already computes effective states that include soft
    verification warnings.  Artifact readiness must use the same authority;
    otherwise a producer with raw status ``failed`` but completed execution
    evidence can still block downstream consumers.
    """

    if isinstance(state_by_task, dict):
        state = state_by_task.get(producer_node.id) or {}
        effective_status = str(state.get("effective_status") or "").strip().lower()
        if effective_status:
            return effective_status

    raw_status = str(producer_node.status or "pending").strip().lower()
    payload_status = _execution_payload_status(getattr(producer_node, "execution_result", None))
    if payload_status in {"completed", "done", "success"}:
        return payload_status
    return raw_status


def _execution_payload_status(raw_value: Any) -> str:
    if raw_value in (None, ""):
        return ""
    payload: Any = raw_value
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return ""
    if not isinstance(payload, dict):
        return ""
    payload_dict = cast(Dict[str, Any], payload)
    raw_metadata = payload_dict.get("metadata")
    metadata: Dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    for value in (metadata.get("execution_status"), payload_dict.get("status")):
        status = str(value or "").strip().lower()
        if status in {"completed", "done", "success"}:
            return status
    return ""
