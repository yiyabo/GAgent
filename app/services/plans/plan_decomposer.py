# noqa: D401 - module-level documentation handled in docs/decompose_task_plan.md
from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from ...config.decomposer_config import DecomposerSettings, get_decomposer_settings
from ...repository.plan_repository import PlanRepository
from .plan_models import PlanNode, PlanTree
from .dag_models import DAG
from ..llm.decomposer_service import (
    DecompositionChild,
    PlanDecomposerLLMService,
)

logger = logging.getLogger(__name__)


def _log_job(level: str, message: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    try:
        from .decomposition_jobs import log_job_event
    except Exception:  # pragma: no cover - defensive
        return
    log_job_event(level, message, metadata)


@dataclass
class QueueItem:
    node_id: Optional[int]
    relative_depth: int


class DecompositionResult(BaseModel):
    plan_id: int
    mode: str
    root_node_id: Optional[int] = None
    processed_nodes: List[Optional[int]] = Field(default_factory=list)
    created_tasks: List[PlanNode] = Field(default_factory=list)
    failed_nodes: List[Optional[int]] = Field(default_factory=list)
    stopped_reason: Optional[str] = None
    stats: Dict[str, Any] = Field(default_factory=dict)
    simplified_dag: Optional[Dict[str, Any]] = None


class DecompositionPromptBuilder:
    """Compose prompts for the decomposition LLM without sharing chat history."""

    SYSTEM_HEADER = (
        "You are a task planning assistant. You must return valid JSON that matches "
        "the provided schema. Decompose the target work item into direct child tasks only."
    )

    def build(
        self,
        *,
        plan: PlanTree,
        node: Optional[PlanNode],
        outline: str,
        mode: str,
        settings: DecomposerSettings,
        depth: int,
        max_depth: int,
        session_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        if node is None:
            node_title = plan.title
            node_instruction = plan.description or ""
            node_path = "/"
            node_children = []
        else:
            node_title = node.name
            node_instruction = node.instruction or ""
            node_path = node.path or f"/{node.id}"
            node_children = self._summarise_children(plan, node.id)

        constraints = {
            "mode": mode,
            "target_node_path": node_path,
            "current_depth": depth,
            "max_depth": max_depth,
            "min_children": settings.min_children,
            "max_children": settings.max_children,
            "stop_on_empty": settings.stop_on_empty,
        }

        prompt = [
            self.SYSTEM_HEADER,
        ]

        if session_context:
            user_message = session_context.get("user_message")
            if user_message:
                prompt.append("\n=== USER REQUEST ===")
                prompt.append(f"{user_message}")

            chat_history = session_context.get("chat_history", [])
            if chat_history:
                prompt.append("\n=== RECENT CONVERSATION ===")
                recent_history = chat_history[-6:] if len(chat_history) > 6 else chat_history
                for msg in recent_history:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if len(content) > 500:
                        content = content[:500] + "..."
                    prompt.append(f"[{role}]: {content}")

            tool_results = session_context.get("recent_tool_results", [])
            if tool_results:
                prompt.append("\n=== RECENT TOOL RESULTS ===")
                for tr in tool_results[-3:]:  # 3result
                    tool_name = tr.get("tool", "unknown")
                    summary = tr.get("summary", "")
                    prompt.append(f"- {tool_name}: {summary}")

        prompt.extend([
            "\n=== PLAN OVERVIEW ===",
            outline or "(empty plan)",
            "\n=== TARGET NODE ===",
            f"Name: {node_title}",
            f"Instruction: {node_instruction}",
            f"Existing children count: {len(node_children)}",
            *node_children,
            "\n=== CONSTRAINTS ===",
            self._format_constraints(constraints),
            "\n=== RESPONSE FORMAT ===",
            "{",
            '  "target_node_id": <int or null>,',
            '  "mode": "plan_bfs" | "single_node",',
            '  "should_stop": <true|false>,',
            '  "reason": "<optional string>",',
            '  "children": [',
            "  {",
            '  "name": "<task name>",',
            '  "instruction": "<execution details>",',
            '  "metadata": {',
            '  "paper_section": "<optional: abstract|introduction|method|experiment|result|discussion|conclusion|references>",',
            '  "paper_role": "<optional: evidence_collector|section_writer|manuscript_assembler|citation_validator>",',
            '  "paper_context_paths": ["<optional artifact path>", "..."],',
            '  "acceptance_criteria": {',
            '    "category": "<optional: file_data>",',
            '    "blocking": true,',
            '    "checks": [',
            '      {"type": "file_exists", "path": "<output file>"},',
            '      {"type": "file_nonempty", "path": "<output file>"}',
            '    ]',
            '  }',
            "  },",
            '  "dependencies": [<int>],',
            '  "leaf": <true|false>,',
            '  "context": {',
            '  "combined": "<optional summary>",',
            '  "sections": [',
            '  {',
            '  "title": "<section title>",',
            '  "content": "<section details>"',
            '  }',
            '  ],',
            '  "meta": {',
            '  "<key>": "<value>"',
            '  }',
            "  }",
            "  }",
            "  ]",
            "}",
            "\nSTRICT REQUIREMENTS:",
            "- The entire response must be valid JSON (no comments, no trailing commas, no Markdown code fences).",
            "- `children` must be an array. Each child must include `name`, `instruction`, `dependencies`, `leaf`, and `context`.",
            "- DEPENDENCY RULES (critical):",
            "  * `dependencies` specifies which sibling tasks (within THIS batch of children) must complete before this task can start.",
            "  * Use 0-based INDEX into the `children` array you are generating (e.g. if child[2] depends on child[0], set child[2].dependencies = [0]).",
            "  * Do NOT reference task IDs from the PLAN OVERVIEW — those are parent/ancestor nodes, not valid dependency targets.",
            "  * Do NOT invent IDs that do not correspond to a sibling index in your output.",
            "  * Use an empty array `[]` if the task has no dependencies.",
            "  * CROSS-LINK RULE (MANDATORY): If this batch contains BOTH evidence/data-preparation tasks (e.g. extracting/organizing evidence, preparing references, gathering source material) AND downstream tasks that consume that output (e.g. drafting a section, writing a report, running analysis on the evidence), the downstream task's `dependencies` MUST include every evidence/preparation sibling it relies on. Do NOT leave downstream writing/analysis tasks with empty dependencies when evidence siblings exist — the executor runs tasks as soon as their direct deps are satisfied, so missing edges cause writers to start before evidence is ready.",
            "  * Keywords that signal evidence/preparation roles: 整理, 提取, 收集, 证据, 资料, 参考, evidence, extract, collect, gather, prepare, references.",
            "  * Keywords that signal downstream consumer roles: 撰写, 写作, 起草, 初稿, 章节, 报告, 分析, draft, write, author, section, report, analyze, synthesize.",
            "- For paper-writing tasks, include `metadata.paper_section`, `metadata.paper_role`, and `metadata.paper_context_paths` when known.",
            "- For file/data tasks that download files, generate datasets, or write reports/artifacts, include `metadata.acceptance_criteria` when possible. Use deterministic checks only; prefer `file_exists`, `file_nonempty`, `glob_count_at_least`, `text_contains`, `json_field_equals`, `json_field_at_least`, or `pdb_residue_present`.",
            "- `context.sections` must be an array of JSON objects, never strings. Every object must provide `title` and `content` keys.",
            "- Use empty arrays (`[]`) or empty objects (`{}`) when there is no data.",
            "- Do not invent additional top-level keys beyond this schema.",
            f"- Aim to produce between {settings.min_children} and {settings.max_children} well-scoped child tasks when the work warrants it.",
            f"- Returning fewer than {settings.min_children} children is acceptable only if the task is inherently small; explain via `reason` when doing so.",
            "\nOnly return JSON. Do not wrap the response in Markdown code fences.",
        ])
        return "\n".join(prompt)

    def _summarise_children(self, plan: PlanTree, node_id: int) -> List[str]:
        summaries: List[str] = []
        for child_id in plan.children_ids(node_id):
            child = plan.nodes.get(child_id)
            if not child:
                continue
            instruction = (child.instruction or "").strip()
            if len(instruction) > 80:
                instruction = instruction[:77] + "..."
            summaries.append(f"- [{child.id}] {child.name} :: {instruction}")
        return summaries

    def _format_constraints(self, data: Dict[str, Any]) -> str:
        return "\n".join(f"- {key}: {value}" for key, value in data.items())


class PlanDecomposer:
    """High-level façade orchestrating BFS task decomposition."""

    def __init__(
        self,
        *,
        repo: Optional[PlanRepository] = None,
        llm_service: Optional[PlanDecomposerLLMService] = None,
        settings: Optional[DecomposerSettings] = None,
    ) -> None:
        self._repo = repo or PlanRepository()
        self._settings = settings or get_decomposer_settings()
        self._llm = llm_service or PlanDecomposerLLMService(settings=self._settings)
        self._prompt_builder = DecompositionPromptBuilder()

    @property
    def settings(self) -> DecomposerSettings:
        return self._settings

    def run_plan(
        self,
        plan_id: int,
        *,
        max_depth: Optional[int] = None,
        node_budget: Optional[int] = None,
        session_context: Optional[Dict[str, Any]] = None,
    ) -> DecompositionResult:
        """Decompose an entire plan by traversing from the plan root."""
        tree = self._repo.get_plan_tree(plan_id)
        queue: Deque[QueueItem] = deque()
        if tree.is_empty():
            # Use None to represent virtual plan root so LLM can produce top-level tasks.
            queue.append(QueueItem(node_id=None, relative_depth=0))
        else:
            for root_id in tree.root_node_ids():
                queue.append(QueueItem(node_id=root_id, relative_depth=0))
        root_reference = queue[0].node_id if queue else None
        return self._process_queue(
            plan_id,
            tree=tree,
            mode="plan_bfs",
            queue=queue,
            max_depth=max_depth if max_depth is not None else self._settings.max_depth,
            node_budget=node_budget
            if node_budget is not None
            else self._settings.total_node_budget,
            root_reference=root_reference,
            session_context=session_context,
        )

    def decompose_node(
        self,
        plan_id: int,
        node_id: int,
        *,
        expand_depth: Optional[int] = 1,
        node_budget: Optional[int] = None,
        allow_existing_children: Optional[bool] = None,
        session_context: Optional[Dict[str, Any]] = None,
    ) -> DecompositionResult:
        """Decompose a specific node and optionally continue BFS under it."""
        tree = self._repo.get_plan_tree(plan_id)
        if node_id not in tree.nodes:
            raise ValueError(f"Task {node_id} not found in plan {plan_id}")
        depth_limit = (
            expand_depth if expand_depth is not None else self._settings.max_depth
        )
        queue: Deque[QueueItem] = deque([QueueItem(node_id=node_id, relative_depth=0)])
        root_reference = node_id
        result = self._process_queue(
            plan_id,
            tree=tree,
            mode="single_node",
            queue=queue,
            max_depth=depth_limit,
            node_budget=node_budget
            if node_budget is not None
            else self._settings.total_node_budget,
            override_allow_existing_children=allow_existing_children,
            root_reference=root_reference,
            session_context=session_context,
        )

        if self._settings.enable_simplification and result.created_tasks:
            try:
                from .tree_simplifier import TreeSimplifier

                updated_tree = self._repo.get_plan_tree(plan_id)

                simplifier = TreeSimplifier(
                    use_llm=self._settings.simplification_use_llm,
                    use_cache=True,
                )
                if hasattr(simplifier.matcher, 'threshold'):
                    simplifier.matcher.threshold = self._settings.simplification_threshold

                dag_result = simplifier.simplify(updated_tree)

                logger.info(
                    "Graph simplification completed (decompose_node): "
                    f"original={len(updated_tree.nodes)}, "
                    f"simplified={dag_result.node_count()}, "
                    f"merged={len(dag_result.merge_map)}"
                )

                result.simplified_dag = dag_result.to_dict()
            except Exception as e:
                logger.warning(f"Graph simplification failed in decompose_node: {e}")

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_queue(
        self,
        plan_id: int,
        *,
        tree: PlanTree,
        mode: str,
        queue: Deque[QueueItem],
        max_depth: int,
        node_budget: int,
        override_allow_existing_children: Optional[bool] = None,
        root_reference: Optional[int] = None,
        session_context: Optional[Dict[str, Any]] = None,
    ) -> DecompositionResult:
        processed: List[Optional[int]] = []
        created_nodes: List[PlanNode] = []
        failed: List[Optional[int]] = []
        visited: set[Optional[int]] = set()
        outline_cache = tree.to_outline(max_depth=5, max_nodes=80)
        unlimited_budget = node_budget <= 0
        budget_remaining: Optional[int] = None if unlimited_budget else max(node_budget, 0)
        llm_calls = 0
        stopped_reason: Optional[str] = None

        if budget_remaining is not None and budget_remaining == 0:
            return DecompositionResult(
                plan_id=plan_id,
                mode=mode,
                root_node_id=root_reference,
                processed_nodes=processed,
                created_tasks=created_nodes,
                failed_nodes=failed,
                stopped_reason="node_budget_exhausted",
                stats={
                    "node_budget": node_budget if node_budget > 0 else None,
                    "consumed_budget": 0,
                    "queue_remaining": len(queue),
                    "llm_calls": 0,
                },
            )
        allow_existing = (
            self._settings.allow_existing_children
            if override_allow_existing_children is None
            else override_allow_existing_children
        )

        while queue and (budget_remaining is None or budget_remaining > 0):
            current = queue.popleft()
            if current.node_id in visited:
                continue
            visited.add(current.node_id)
            if current.relative_depth > max_depth:
                continue

            node = tree.nodes.get(current.node_id) if current.node_id else None
            if (
                not allow_existing
                and node is not None
                and tree.children_ids(node.id)
            ):
                logger.debug(
                    "Skip node %s because children already exist and allow_existing=False",
                    node.id,
                )
                _log_job(
                    "debug",
                    "Skipped node because it already has children",
                    {"node_id": node.id, "allow_existing_children": allow_existing},
                )
                next_depth = current.relative_depth + 1
                if next_depth <= max_depth:
                    for child_id in tree.children_ids(node.id):
                        if child_id not in visited:
                            queue.append(
                                QueueItem(node_id=child_id, relative_depth=next_depth)
                            )
                continue

            _log_job(
                "info",
                "Preparing to decompose node",
                {
                    "node_id": current.node_id,
                    "depth": current.relative_depth,
                    "queue_remaining": len(queue),
                    "budget_remaining": budget_remaining,
                    "created_count": len(created_nodes),
                    "processed_count": len(processed),
                },
            )
            prompt = self._prompt_builder.build(
                plan=tree,
                node=node,
                outline=outline_cache,
                mode=mode,
                settings=self._settings,
                depth=current.relative_depth,
                max_depth=max_depth,
                session_context=session_context,
            )

            try:
                llm_result = self._llm.generate(prompt)
                llm_calls += 1
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Decomposition failed for node %s: %s", current.node_id, exc)
                _log_job(
                    "error",
                    "LLM decomposition call failed",
                    {"node_id": current.node_id, "error": str(exc)},
                )
                failed.append(current.node_id)
                continue

            processed.append(current.node_id)

            children = self._trim_children(
                llm_result.children, self._settings.max_children
            )
            _log_job(
                "info",
                "LLM returned a decomposition payload",
                {
                    "node_id": current.node_id,
                    "children_count": len(children),
                    "should_stop": llm_result.should_stop,
                },
            )
            if not children:
                if llm_result.should_stop:
                    stopped_reason = llm_result.reason or "llm_requested_stop"
                    _log_job(
                        "info",
                        "LLM requested to stop decomposition",
                        {"node_id": current.node_id, "reason": stopped_reason},
                    )
                    break
                if self._settings.stop_on_empty:
                    stopped_reason = llm_result.reason or "empty_children"
                    _log_job(
                        "info",
                        "No new subtasks; stopping according to settings",
                        {"node_id": current.node_id, "reason": stopped_reason},
                    )
                    break
                continue

            created_sibling_ids = [n.id for n in created_nodes if n.parent_id == current.node_id]
            batch_created: List[PlanNode] = []
            for child in children:
                if budget_remaining is not None and budget_remaining <= 0:
                    break
                new_node = self._create_child_node(
                    plan_id,
                    parent_id=current.node_id,
                    child=child,
                    tree=tree,
                    created_sibling_ids=created_sibling_ids,
                )
                if budget_remaining is not None:
                    budget_remaining -= 1
                created_nodes.append(new_node)
                batch_created.append(new_node)
                created_sibling_ids.append(new_node.id)  # update
                self._update_tree_cache(tree, new_node)
                outline_cache = tree.to_outline(max_depth=5, max_nodes=80)
                _log_job(
                    "info",
                    "Created child task node",
                    {
                        "parent_id": current.node_id,
                        "task_id": new_node.id,
                        "name": new_node.name,
                    },
                )
                if (
                    not child.leaf
                    and current.relative_depth + 1 <= max_depth
                    and (budget_remaining is None or budget_remaining > 0)
                    ):
                        queue.append(
                            QueueItem(
                                node_id=new_node.id,
                                relative_depth=current.relative_depth + 1,
                            )
                        )

            # Fix C: after the full sibling batch exists, enforce
            # evidence → writer/consumer edges when the LLM omitted them.
            if batch_created:
                self._auto_link_evidence_to_writers(
                    plan_id=plan_id,
                    siblings=batch_created,
                    tree=tree,
                )

            if llm_result.should_stop:
                stopped_reason = llm_result.reason or "llm_requested_stop"
                _log_job(
                    "info",
                    "LLM requested to stop further decomposition",
                    {"node_id": current.node_id, "reason": stopped_reason},
                )
                break

        if budget_remaining is not None and budget_remaining <= 0:
            stopped_reason = stopped_reason or "node_budget_exhausted"
            _log_job(
                "info",
                "Decomposition budget exhausted; stopping",
                {"node_budget": node_budget},
            )

        simplified_dag: Optional[dict] = None
        if self._settings.enable_simplification and created_nodes:
            try:
                from .tree_simplifier import TreeSimplifier

                updated_tree = self._repo.get_plan_tree(plan_id)

                simplifier = TreeSimplifier(
                    use_llm=self._settings.simplification_use_llm,
                    use_cache=True,
                )
                if hasattr(simplifier.matcher, 'threshold'):
                    simplifier.matcher.threshold = self._settings.simplification_threshold

                dag_result = simplifier.simplify(updated_tree)

                _log_job(
                    "info",
                    "Graph simplification completed",
                    {
                        "original_nodes": len(updated_tree.nodes),
                        "simplified_nodes": dag_result.node_count(),
                        "merged_count": len(dag_result.merge_map),
                    },
                )
                simplified_dag = dag_result.to_dict()
            except Exception as e:
                logger.warning(f"Graph simplification failed: {e}")
                _log_job(
                    "warning",
                    "Graph simplification failed",
                    {"error": str(e)},
                )

        return DecompositionResult(
            plan_id=plan_id,
            mode=mode,
            root_node_id=root_reference,
            processed_nodes=processed,
            created_tasks=created_nodes,
            failed_nodes=failed,
            stopped_reason=stopped_reason,
            stats={
                "node_budget": node_budget if node_budget > 0 else None,
                "consumed_budget": len(created_nodes),
                "queue_remaining": len(queue),
                "llm_calls": llm_calls,
            },
            simplified_dag=simplified_dag,
        )

    def _trim_children(
        self, children: Iterable[DecompositionChild], limit: int
    ) -> List[DecompositionChild]:
        return list(children)[: max(limit, 0)]

    @staticmethod
    def _infer_paper_section(name: str, instruction: str) -> Optional[str]:
        text = f"{name}\n{instruction}".lower()
        patterns = {
            "abstract": (r"\babstract\b",),
            "introduction": (r"\bintroduction\b", r"\bintro\b"),
            "method": (r"\bmethods?\b", r"\bmethodology\b", r"\bapproach\b"),
            "experiment": (r"\bexperiments?\b", r"\bevaluation\b", r"\bbenchmark\b", r"\bablation\b"),
            "result": (r"\bresults?\b", r"\bfindings?\b"),
            "discussion": (r"\bdiscussions?\b",),
            "conclusion": (r"\bconclusions?\b", r"\bfuture work\b"),
            "references": (r"\breferences?\b", r"\bbib(tex)?\b", r"\bcitation(s)?\b"),
        }
        for section, regexes in patterns.items():
            if any(re.search(pattern, text) for pattern in regexes):
                return section
        return None

    @staticmethod
    def _infer_paper_role(name: str, instruction: str, section: Optional[str]) -> Optional[str]:
        text = f"{name}\n{instruction}".lower()
        if section:
            if section == "references":
                return "citation_validator"
            return "section_writer"
        if any(token in text for token in ("evidence", "literature", "retrieval", "collect", "survey")):
            return "evidence_collector"
        if any(token in text for token in ("assemble", "merge", "stitch", "full draft", "manuscript_writer")):
            return "manuscript_assembler"
        return None

    def _derive_paper_metadata(self, child: DecompositionChild) -> Dict[str, Any]:
        metadata = dict(child.metadata or {})
        section = metadata.get("paper_section")
        if not isinstance(section, str) or not section.strip():
            section = self._infer_paper_section(child.name or "", child.instruction or "")
        else:
            section = section.strip().lower()

        role = metadata.get("paper_role")
        if not isinstance(role, str) or not role.strip():
            role = self._infer_paper_role(child.name or "", child.instruction or "", section)
        else:
            role = role.strip().lower()

        raw_paths = metadata.get("paper_context_paths")
        if not isinstance(raw_paths, list):
            raw_paths = child.context_meta.get("paper_context_paths") if isinstance(child.context_meta, dict) else []
        paper_context_paths: List[str] = []
        if isinstance(raw_paths, list):
            for item in raw_paths:
                text = str(item).strip()
                if text and text not in paper_context_paths:
                    paper_context_paths.append(text)

        if section:
            metadata["paper_section"] = section
        if role:
            metadata["paper_role"] = role
        if paper_context_paths:
            metadata["paper_context_paths"] = paper_context_paths
        if section or role or paper_context_paths:
            metadata["paper_mode"] = True
        return metadata

    def _create_child_node(
        self,
        plan_id: int,
        *,
        parent_id: Optional[int],
        child: DecompositionChild,
        tree: PlanTree,
        created_sibling_ids: List[int],
    ) -> PlanNode:
        validated_deps = self._validate_dependencies(
            tree=tree,
            parent_id=parent_id,
            raw_deps=child.dependencies,
            created_sibling_ids=created_sibling_ids,
        )
        # Fix B: inherit parent's own direct dependencies so leaves can't run
        # before their ancestors' prerequisites are satisfied. This fixes the
        # class of bugs where a writer-child starts while its evidence
        # ancestor-dep is still failed/running.
        validated_deps = self._inherit_parent_dependencies(
            tree=tree,
            parent_id=parent_id,
            current_deps=validated_deps,
        )
        child_metadata = self._derive_paper_metadata(child)

        node = self._repo.create_task(
            plan_id,
            name=child.name,
            instruction=child.instruction,
            parent_id=parent_id,
            metadata=child_metadata if child_metadata else None,
            dependencies=validated_deps,
        )
        has_context = any(
            [
                child.context_combined,
                child.context_sections,
                child.context_meta,
            ]
        )
        if has_context:
            self._repo.update_task(
                plan_id,
                node.id,
                context_combined=child.context_combined,
                context_sections=child.context_sections,
                context_meta=child.context_meta,
            )
            node = self._repo.get_node(plan_id, node.id)
        return node

    def _validate_dependencies(
        self,
        tree: PlanTree,
        parent_id: Optional[int],
        raw_deps: List[int],
        created_sibling_ids: List[int],
    ) -> List[int]:
        """Validate dependency IDs and remove illegal references.

        The LLM is instructed to return **0-based sibling indices** into its
        ``children`` array.  We attempt index-based resolution first; if that
        fails we fall back to treating the value as a literal task ID for
        backward compatibility.

        Rules:
        1. Dependencies must refer to existing nodes in the tree or
           already-created siblings.
        2. Dependencies must not create cycles with ancestors.
        """
        if not raw_deps:
            return []

        ancestor_ids: set = set()
        current = parent_id
        while current is not None:
            ancestor_ids.add(current)
            node = tree.nodes.get(current)
            if node:
                current = node.parent_id
            else:
                break

        numeric_raw_deps = [
            dep for dep in raw_deps if isinstance(dep, int)
        ]
        in_range_values = [
            dep for dep in numeric_raw_deps
            if 0 <= dep < len(created_sibling_ids)
        ]
        prefer_index_mode = bool(
            created_sibling_ids
            and in_range_values
            and (
                0 in in_range_values
                or (
                    len(numeric_raw_deps) > 1
                    and len(in_range_values) == len(numeric_raw_deps)
                )
            )
        )

        valid_deps: List[int] = []
        for dep_val in raw_deps:
            # If the dependency set strongly looks like sibling indices,
            # resolve indices first to preserve the new prompt contract.
            if prefer_index_mode and 0 <= dep_val < len(created_sibling_ids):
                resolved_id = created_sibling_ids[dep_val]
                if resolved_id not in ancestor_ids:
                    if resolved_id not in valid_deps:
                        valid_deps.append(resolved_id)
                    continue
                else:
                    logger.warning(
                        "Skipping dependency index %s (resolved to %s): would create cycle with ancestor",
                        dep_val, resolved_id,
                    )
                    continue

            # --- Prefer literal task IDs when the value is otherwise ambiguous ---
            if dep_val in ancestor_ids:
                logger.warning(
                    "Skipping dependency %s: would create cycle with ancestor",
                    dep_val,
                )
                continue

            if dep_val in tree.nodes:
                if dep_val not in valid_deps:
                    valid_deps.append(dep_val)
                continue

            if dep_val in created_sibling_ids:
                if dep_val not in valid_deps:
                    valid_deps.append(dep_val)
                continue

            # --- Fallback: treat as 0-based sibling index ---
            if 0 <= dep_val < len(created_sibling_ids):
                resolved_id = created_sibling_ids[dep_val]
                if resolved_id not in ancestor_ids:
                    if resolved_id not in valid_deps:
                        valid_deps.append(resolved_id)
                    continue
                logger.warning(
                    "Skipping dependency index %s (resolved to %s): would create cycle with ancestor",
                    dep_val, resolved_id,
                )
                continue

            logger.warning(
                "Skipping dependency %s: task does not exist (not a valid sibling index or task ID)",
                dep_val,
            )

        return valid_deps

    # ------------------------------------------------------------------
    # Dependency enforcement helpers (Fix B + Fix C)
    # ------------------------------------------------------------------

    # Keyword heuristics used when the LLM does not explicitly tag
    # paper_role. Both Chinese and English forms are recognised.
    _EVIDENCE_KEYWORDS: Tuple[str, ...] = (
        "整理", "提取", "收集", "证据", "资料", "参考", "文献证据",
        "evidence", "extract", "collect", "gather", "prepare reference",
        "references", "literature", "data preparation", "prepare data",
    )
    _CONSUMER_KEYWORDS: Tuple[str, ...] = (
        "撰写", "写作", "起草", "初稿", "章节", "综述", "报告", "分析",
        "整合", "合成", "draft", "write", "author", "section", "report",
        "analyze", "analysis", "synthesize", "compose",
    )
    _EVIDENCE_ROLES = {
        "evidence_collector", "evidence_extractor", "evidence", "data_collector",
    }
    _CONSUMER_ROLES = {
        "section_writer", "manuscript_writer", "manuscript_assembler",
        "writer", "author", "analyst",
    }

    def _classify_sibling(self, node: PlanNode) -> str:
        """Return 'evidence' / 'consumer' / 'other' for a just-created node."""
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        role = str(metadata.get("paper_role") or "").strip().lower()
        if role in self._EVIDENCE_ROLES:
            return "evidence"
        if role in self._CONSUMER_ROLES:
            return "consumer"
        haystack = f"{node.name or ''} {node.instruction or ''}".lower()
        has_evidence_kw = any(kw.lower() in haystack for kw in self._EVIDENCE_KEYWORDS)
        has_consumer_kw = any(kw.lower() in haystack for kw in self._CONSUMER_KEYWORDS)
        # A node tagged as both (e.g. "整理并撰写") is treated as consumer so
        # that it still gets evidence siblings as dependencies.
        if has_consumer_kw:
            return "consumer"
        if has_evidence_kw:
            return "evidence"
        return "other"

    def _inherit_parent_dependencies(
        self,
        *,
        tree: PlanTree,
        parent_id: Optional[int],
        current_deps: List[int],
    ) -> List[int]:
        """Append the parent's own direct deps so leaves wait for ancestor prereqs.

        Skips ancestors of the child to avoid creating cycles, and dedupes.
        """
        if parent_id is None:
            return list(current_deps)
        parent_node = tree.nodes.get(parent_id)
        if not parent_node:
            return list(current_deps)
        parent_deps = [d for d in (parent_node.dependencies or []) if d in tree.nodes]
        if not parent_deps:
            return list(current_deps)

        # Ancestors of the CHILD being created (i.e. parent and above) must
        # never become dependencies — that would form a cycle.
        ancestor_ids: Set[int] = set()
        cursor = parent_id
        while cursor is not None:
            ancestor_ids.add(cursor)
            anc_node = tree.nodes.get(cursor)
            cursor = anc_node.parent_id if anc_node else None

        merged = list(current_deps)
        seen = set(merged)
        for dep in parent_deps:
            if dep in ancestor_ids or dep in seen:
                continue
            merged.append(dep)
            seen.add(dep)
        if len(merged) > len(current_deps):
            logger.info(
                "[DECOMPOSER] Inherited parent (%s) dependencies %s for new child",
                parent_id,
                [d for d in merged if d not in current_deps],
            )
        return merged

    def _auto_link_evidence_to_writers(
        self,
        *,
        plan_id: int,
        siblings: List[PlanNode],
        tree: PlanTree,
    ) -> None:
        """Ensure consumer siblings depend on every evidence sibling in the batch.

        The LLM is instructed to encode this via `dependencies`, but historically
        it drops the edges for mixed batches (see plan 68 tasks #24/#30). This
        post-pass is a safety net — it only *adds* edges and never removes or
        rewrites LLM-provided ones.
        """
        if len(siblings) < 2:
            return
        evidence_nodes: List[PlanNode] = []
        consumer_nodes: List[PlanNode] = []
        for node in siblings:
            kind = self._classify_sibling(node)
            if kind == "evidence":
                evidence_nodes.append(node)
            elif kind == "consumer":
                consumer_nodes.append(node)
        if not evidence_nodes or not consumer_nodes:
            return
        evidence_ids = [e.id for e in evidence_nodes]
        for consumer in consumer_nodes:
            existing = list(consumer.dependencies or [])
            missing = [eid for eid in evidence_ids if eid not in existing and eid != consumer.id]
            if not missing:
                continue
            new_deps = existing + missing
            try:
                updated = self._repo.update_task(
                    plan_id,
                    consumer.id,
                    dependencies=new_deps,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "[DECOMPOSER] Auto-link failed for task %s: %s",
                    consumer.id, exc,
                )
                continue
            tree.nodes[consumer.id] = updated
            logger.info(
                "[DECOMPOSER] Auto-linked consumer task %s → evidence siblings %s",
                consumer.id, missing,
            )
            _log_job(
                "info",
                "Auto-linked consumer task to evidence siblings",
                {
                    "task_id": consumer.id,
                    "added_dependencies": missing,
                    "parent_id": consumer.parent_id,
                },
            )

    def _update_tree_cache(self, tree: PlanTree, node: PlanNode) -> None:
        tree.nodes[node.id] = node
        tree.adjacency.setdefault(node.parent_id, []).append(node.id)
        tree.rebuild_adjacency()


def run_plan_decomposition(plan_id: int) -> DecompositionResult:
    """Convenience helper mirroring high-level API."""
    decomposer = PlanDecomposer()
    return decomposer.run_plan(plan_id)


def decompose_single_node(plan_id: int, node_id: int) -> DecompositionResult:
    decomposer = PlanDecomposer()
    return decomposer.decompose_node(plan_id, node_id)
