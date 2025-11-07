# noqa: D401 - module-level documentation handled in docs/decompose_task_plan.md
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from ...config.decomposer_config import DecomposerSettings, get_decomposer_settings
from ...repository.plan_repository import PlanRepository
from .plan_models import PlanNode, PlanTree
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
            "    {",
            '      "name": "<task name>",',
            '      "instruction": "<execution details>",',
            '      "dependencies": [<int>],',
            '      "leaf": <true|false>,',
            '      "context": {',
            '         "combined": "<optional summary>",',
            '         "sections": [',
            '             {',
            '                 "title": "<section title>",',
            '                 "content": "<section details>"',
            '             }',
            '         ],',
            '         "meta": {',
            '             "<key>": "<value>"',
            '         }',
            "      }",
            "    }",
            "  ]",
            "}",
            "\nSTRICT REQUIREMENTS:",
            "- The entire response must be valid JSON (no comments, no trailing commas, no Markdown code fences).",
            "- `children` must be an array. Each child must include `name`, `instruction`, `dependencies`, `leaf`, and `context`.",
            "- `context.sections` must be an array of JSON objects, never strings. Every object must provide `title` and `content` keys.",
            "- Use empty arrays (`[]`) or empty objects (`{}`) when there is no data.",
            "- Do not invent additional top-level keys beyond this schema.",
            f"- Aim to produce between {settings.min_children} and {settings.max_children} well-scoped child tasks when the work warrants it.",
            f"- Returning fewer than {settings.min_children} children is acceptable only if the task is inherently small; explain via `reason` when doing so.",
            "\nOnly return JSON. Do not wrap the response in Markdown code fences.",
        ]
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
        )

    def decompose_node(
        self,
        plan_id: int,
        node_id: int,
        *,
        expand_depth: Optional[int] = 1,
        node_budget: Optional[int] = None,
        allow_existing_children: Optional[bool] = None,
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
        return self._process_queue(
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
        )

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
    ) -> DecompositionResult:
        processed: List[Optional[int]] = []
        created_nodes: List[PlanNode] = []
        failed: List[Optional[int]] = []
        outline_cache = tree.to_outline(max_depth=5, max_nodes=80)
        budget_remaining = max(node_budget, 0)
        llm_calls = 0
        stopped_reason: Optional[str] = None

        if budget_remaining == 0:
            return DecompositionResult(
                plan_id=plan_id,
                mode=mode,
                root_node_id=root_reference,
                processed_nodes=processed,
                created_tasks=created_nodes,
                failed_nodes=failed,
                stopped_reason="node_budget_exhausted",
                stats={
                    "node_budget": node_budget,
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

        while queue and budget_remaining > 0:
            current = queue.popleft()
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
                continue

            _log_job(
                "info",
                "Preparing to decompose node",
                {
                    "node_id": current.node_id,
                    "depth": current.relative_depth,
                    "queue_remaining": len(queue),
                    "budget_remaining": budget_remaining,
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

            for child in children:
                if budget_remaining <= 0:
                    break
                new_node = self._create_child_node(
                    plan_id, parent_id=current.node_id, child=child
                )
                budget_remaining -= 1
                created_nodes.append(new_node)
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
                    and budget_remaining > 0
                ):
                    queue.append(
                        QueueItem(
                            node_id=new_node.id,
                            relative_depth=current.relative_depth + 1,
                        )
                    )

            if llm_result.should_stop:
                stopped_reason = llm_result.reason or "llm_requested_stop"
                _log_job(
                    "info",
                    "LLM requested to stop further decomposition",
                    {"node_id": current.node_id, "reason": stopped_reason},
                )
                break

        if budget_remaining <= 0:
            stopped_reason = stopped_reason or "node_budget_exhausted"
            _log_job(
                "info",
                "Decomposition budget exhausted; stopping",
                {"node_budget": node_budget},
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
                "node_budget": node_budget,
                "consumed_budget": node_budget - budget_remaining,
                "queue_remaining": len(queue),
                "llm_calls": llm_calls,
            },
        )

    def _trim_children(
        self, children: Iterable[DecompositionChild], limit: int
    ) -> List[DecompositionChild]:
        return list(children)[: max(limit, 0)]

    def _create_child_node(
        self,
        plan_id: int,
        *,
        parent_id: Optional[int],
        child: DecompositionChild,
    ) -> PlanNode:
        node = self._repo.create_task(
            plan_id,
            name=child.name,
            instruction=child.instruction,
            parent_id=parent_id,
            dependencies=child.dependencies,
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
