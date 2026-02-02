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
    # 图简化结果（可选，已序列化为字典）
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
        
        # 新增：会话上下文（用户消息、聊天历史等）
        if session_context:
            user_message = session_context.get("user_message")
            if user_message:
                prompt.append("\n=== USER REQUEST ===")
                prompt.append(f"{user_message}")
            
            chat_history = session_context.get("chat_history", [])
            if chat_history:
                prompt.append("\n=== RECENT CONVERSATION ===")
                # 限制历史条数，避免过长
                recent_history = chat_history[-6:] if len(chat_history) > 6 else chat_history
                for msg in recent_history:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    # 截断过长的消息
                    if len(content) > 500:
                        content = content[:500] + "..."
                    prompt.append(f"[{role}]: {content}")
            
            # 最近的工具结果
            tool_results = session_context.get("recent_tool_results", [])
            if tool_results:
                prompt.append("\n=== RECENT TOOL RESULTS ===")
                for tr in tool_results[-3:]:  # 最近3个工具结果
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

        # 图简化处理（与 decompose 方法保持一致）
        if self._settings.enable_simplification and result.created_tasks:
            try:
                from .tree_simplifier import TreeSimplifier

                # 重新获取最新的计划树
                updated_tree = self._repo.get_plan_tree(plan_id)

                simplifier = TreeSimplifier(
                    use_llm=self._settings.simplification_use_llm,
                    use_cache=True,
                )
                # 设置阈值
                if hasattr(simplifier.matcher, 'threshold'):
                    simplifier.matcher.threshold = self._settings.simplification_threshold

                dag_result = simplifier.simplify(updated_tree)

                logger.info(
                    "Graph simplification completed (decompose_node): "
                    f"original={len(updated_tree.nodes)}, "
                    f"simplified={dag_result.node_count()}, "
                    f"merged={len(dag_result.merge_map)}"
                )

                # 更新结果
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

            # 收集当前批次中已创建的兄弟节点 ID
            created_sibling_ids = [n.id for n in created_nodes if n.parent_id == current.node_id]
            for child in children:
                if budget_remaining <= 0:
                    break
                new_node = self._create_child_node(
                    plan_id,
                    parent_id=current.node_id,
                    child=child,
                    tree=tree,
                    created_sibling_ids=created_sibling_ids,
                )
                budget_remaining -= 1
                created_nodes.append(new_node)
                created_sibling_ids.append(new_node.id)  # 更新兄弟列表
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

        # 图简化处理（根据配置）
        simplified_dag: Optional[dict] = None
        if self._settings.enable_simplification and created_nodes:
            try:
                from .tree_simplifier import TreeSimplifier

                # 重新获取最新的计划树
                updated_tree = self._repo.get_plan_tree(plan_id)

                simplifier = TreeSimplifier(
                    use_llm=self._settings.simplification_use_llm,
                    use_cache=True,
                )
                # 设置阈值
                if hasattr(simplifier.matcher, 'threshold'):
                    simplifier.matcher.threshold = self._settings.simplification_threshold

                # 使用独立变量存储中间结果，避免异常时类型不一致
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
                # 转换为可序列化的字典
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
                "node_budget": node_budget,
                "consumed_budget": node_budget - budget_remaining,
                "queue_remaining": len(queue),
                "llm_calls": llm_calls,
            },
            simplified_dag=simplified_dag,
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
        tree: PlanTree,
        created_sibling_ids: List[int],
    ) -> PlanNode:
        # 验证并过滤依赖，防止循环引用
        validated_deps = self._validate_dependencies(
            tree=tree,
            parent_id=parent_id,
            raw_deps=child.dependencies,
            created_sibling_ids=created_sibling_ids,
        )
        
        node = self._repo.create_task(
            plan_id,
            name=child.name,
            instruction=child.instruction,
            parent_id=parent_id,
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
        """验证并过滤依赖 ID，防止循环引用。
        
        只允许依赖：
        1. 已存在于 tree 中的任务（且不是祖先节点）
        2. 同一批次中已创建的兄弟节点
        
        Args:
            tree: 当前的计划树
            parent_id: 当前节点的父节点 ID
            raw_deps: LLM 返回的原始依赖 ID 列表
            created_sibling_ids: 当前批次中已创建的兄弟节点 ID 列表
            
        Returns:
            验证后的有效依赖 ID 列表
        """
        if not raw_deps:
            return []
        
        # 收集祖先节点 ID（不能依赖祖先，会造成循环）
        ancestor_ids: set = set()
        current = parent_id
        while current is not None:
            ancestor_ids.add(current)
            node = tree.nodes.get(current)
            if node:
                current = node.parent_id
            else:
                break
        
        valid_deps: List[int] = []
        for dep_id in raw_deps:
            # 跳过祖先节点
            if dep_id in ancestor_ids:
                logger.warning(
                    "Skipping dependency %s: would create cycle with ancestor",
                    dep_id
                )
                continue
            
            # 检查是否存在于树中
            if dep_id in tree.nodes:
                valid_deps.append(dep_id)
                continue
            
            # 检查是否是刚创建的兄弟节点
            if dep_id in created_sibling_ids:
                valid_deps.append(dep_id)
                continue
            
            logger.warning(
                "Skipping dependency %s: task does not exist",
                dep_id
            )
        
        return valid_deps

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
