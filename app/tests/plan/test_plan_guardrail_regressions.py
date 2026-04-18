from __future__ import annotations

from collections import deque
from types import SimpleNamespace

from app.config.decomposer_config import DecomposerSettings
from app.routers.chat_routes import StructuredChatAgent
from app.services.llm.decomposer_service import DecompositionResponse
from app.services.llm.structured_response import LLMAction, LLMReply, LLMStructuredResponse
from app.services.plans.plan_decomposer import PlanDecomposer, QueueItem
from app.services.plans.plan_models import PlanNode, PlanTree


class _FakeDecomposerLLM:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, _prompt: str) -> DecompositionResponse:
        self.calls += 1
        if self.calls == 1:
            return DecompositionResponse(
                target_node_id=1,
                mode="plan_bfs",
                should_stop=False,
                children_raw=[
                    {
                        "name": "child-task",
                        "instruction": "execute child task",
                        "dependencies": [],
                        "leaf": False,
                    }
                ],
            )
        return DecompositionResponse(
            target_node_id=1,
            mode="plan_bfs",
            should_stop=True,
            reason="done",
            children_raw=[],
        )


def test_decomposer_unlimited_budget_does_not_crash_on_child_enqueue() -> None:
    tree = PlanTree(
        id=35,
        title="Demo Plan",
        nodes={
            1: PlanNode(id=1, plan_id=35, name="root", status="pending", parent_id=None, path="/1")
        },
        adjacency={None: [1], 1: []},
    )
    settings = DecomposerSettings(
        max_depth=2,
        min_children=1,
        max_children=5,
        total_node_budget=0,
        enable_simplification=False,
    )
    decomposer = PlanDecomposer(
        repo=SimpleNamespace(),
        llm_service=_FakeDecomposerLLM(),
        settings=settings,
    )

    def _fake_create_child_node(
        _plan_id: int,
        *,
        parent_id: int | None,
        child,
        tree: PlanTree,
        created_sibling_ids,
    ) -> PlanNode:
        node_id = 2
        return PlanNode(
            id=node_id,
            plan_id=tree.id,
            name=child.name,
            instruction=child.instruction,
            parent_id=parent_id,
            path=f"/{parent_id}/{node_id}" if parent_id is not None else f"/{node_id}",
        )

    def _fake_update_tree_cache(tree: PlanTree, node: PlanNode) -> None:
        tree.nodes[node.id] = node
        tree.adjacency.setdefault(node.parent_id, []).append(node.id)
        tree.adjacency.setdefault(node.id, [])

    decomposer._create_child_node = _fake_create_child_node
    decomposer._update_tree_cache = _fake_update_tree_cache

    result = decomposer._process_queue(
        35,
        tree=tree,
        mode="plan_bfs",
        queue=deque([QueueItem(node_id=1, relative_depth=0)]),
        max_depth=2,
        node_budget=0,
        root_reference=1,
    )

    assert result.failed_nodes == []
    assert len(result.created_tasks) >= 1


def test_plan_first_guardrail_uses_previous_context_for_generic_confirmation() -> None:
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=None)
    agent.history = [
        {
            "role": "user",
            "content": "Please reproduce DeepSEA P0 metrics and output ROC plots plus an AUC comparison table.",
        }
    ]
    agent._current_user_message = "Okay, create it."

    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="Okay"),
        actions=[
            LLMAction(
                kind="tool_operation",
                name="code_executor",
                parameters={
                    "task": (
                        "Train and evaluate a DeepSEA baseline, then output ROC plots and an AUC table."
                    )
                },
                order=1,
            )
        ],
    )

    patched = agent._apply_plan_first_guardrail(structured)

    assert len(patched.actions) == 1
    assert patched.actions[0].name == "code_executor"


def test_explicit_plan_review_guardrail_rewrites_show_tasks_to_review_plan() -> None:
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=67)
    agent.extra_context = {"requires_plan_review": True}

    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="我来先看一下任务结构。"),
        actions=[
            LLMAction(
                kind="task_operation",
                name="show_tasks",
                parameters={"plan_id": 67},
                order=1,
            )
        ],
    )

    patched = agent._apply_explicit_plan_review_guardrail(structured)

    assert len(patched.actions) == 1
    assert patched.actions[0].kind == "plan_operation"
    assert patched.actions[0].name == "review_plan"
    assert patched.actions[0].parameters["plan_id"] == 67
