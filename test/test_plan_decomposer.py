from __future__ import annotations

from dataclasses import replace
from typing import Iterable, List, Optional

from app.config.decomposer_config import DecomposerSettings
from app.repository.plan_repository import PlanRepository
from app.services.llm.decomposer_service import DecompositionResponse
from app.services.plans.plan_decomposer import PlanDecomposer


def _make_response(
    *,
    target: Optional[int],
    mode: str,
    should_stop: bool,
    reason: Optional[str],
    children: List[dict],
) -> DecompositionResponse:
    return DecompositionResponse(
        target_node_id=target,
        mode=mode,
        should_stop=should_stop,
        reason=reason,
        children_raw=children,
    )


class StubDecomposerLLM:
    def __init__(self, responses: Iterable[DecompositionResponse]) -> None:
        self._responses = iter(responses)
        self.prompts: List[str] = []

    def generate(self, prompt: str) -> DecompositionResponse:
        self.prompts.append(prompt)
        try:
            return next(self._responses)
        except StopIteration as exc:  # pragma: no cover - defensive
            raise AssertionError("No more stub responses available") from exc


def _settings(**overrides) -> DecomposerSettings:
    base = DecomposerSettings(
        max_depth=3,
        max_children=6,
        total_node_budget=20,
        model="stub-model",
        auto_on_create=True,
        stop_on_empty=True,
        retry_limit=0,
        allow_existing_children=False,
    )
    return replace(base, **overrides)


def test_run_plan_creates_root_tasks(plan_repo: PlanRepository):
    responses = [
        _make_response(
            target=None,
            mode="plan_bfs",
            should_stop=False,
            reason=None,
            children=[
                {"name": "Task Alpha", "instruction": "Do alpha work", "leaf": False},
                {"name": "Task Beta", "instruction": "Do beta work", "leaf": True},
            ],
        ),
        _make_response(
            target=1,
            mode="plan_bfs",
            should_stop=True,
            reason="depth limit",
            children=[],
        ),
    ]
    stub_llm = StubDecomposerLLM(responses)
    decomposer = PlanDecomposer(
        repo=plan_repo,
        llm_service=stub_llm,
        settings=_settings(),
    )
    plan = plan_repo.create_plan("Demo Plan", description="Stub plan")

    result = decomposer.run_plan(plan.id, max_depth=2, node_budget=5)

    assert len(result.created_tasks) == 2
    assert [node.name for node in result.created_tasks] == ["Task Alpha", "Task Beta"]
    assert result.stopped_reason == "depth limit"
    assert result.stats["llm_calls"] == 2

    tree = plan_repo.get_plan_tree(plan.id)
    assert tree.node_count() == 2
    assert tree.root_node_ids()  # ensure tasks recorded as roots


def test_decompose_node_with_context(plan_repo: PlanRepository):
    plan = plan_repo.create_plan("Context Plan")
    root = plan_repo.create_task(plan.id, name="Root Task")
    responses = [
        _make_response(
            target=root.id,
            mode="single_node",
            should_stop=True,
            reason="node complete",
            children=[
                {
                    "name": "Write spec",
                    "instruction": "Draft a detailed specification",
                    "dependencies": [str(root.id)],
                    "leaf": True,
                    "context": {
                        "combined": "Spec summary",
                        "sections": [{"title": "Outline", "content": "..."}],
                        "meta": {"source": "llm"},
                    },
                }
            ],
        )
    ]
    stub_llm = StubDecomposerLLM(responses)
    decomposer = PlanDecomposer(
        repo=plan_repo,
        llm_service=stub_llm,
        settings=_settings(),
    )

    result = decomposer.decompose_node(plan.id, root.id, expand_depth=1)

    assert len(result.created_tasks) == 1
    child = result.created_tasks[0]
    assert child.parent_id == root.id
    assert child.context_combined == "Spec summary"
    assert child.context_meta.get("source") == "llm"
    assert child.dependencies == [root.id]


def test_decompose_node_skips_when_children_exist(plan_repo: PlanRepository):
    stub_llm = StubDecomposerLLM([])
    decomposer = PlanDecomposer(
        repo=plan_repo,
        llm_service=stub_llm,
        settings=_settings(),
    )
    plan = plan_repo.create_plan("Existing Children Plan")
    parent = plan_repo.create_task(plan.id, name="Parent")
    plan_repo.create_task(plan.id, name="Existing Child", parent_id=parent.id)

    result = decomposer.decompose_node(plan.id, parent.id, expand_depth=1)

    assert result.created_tasks == []
    assert result.processed_nodes == []
    assert result.stats["llm_calls"] == 0
