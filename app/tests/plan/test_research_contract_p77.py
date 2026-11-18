from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.routers.plan_routes import execution_jobs
from app.services.plans.executor_prompts import ExecutorPromptBuilder
from app.services.plans.plan_generation import create_plan_and_generate
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.research_contract import (
    build_research_contract,
    merge_owner_history,
    research_contract_prompt,
    with_research_contract,
)
from app.tests.plan.test_explicit_seed_structure import definitions
from app.tests.plan.test_plan_generation_pipeline import (
    _FailingDecomposerStub,
    _GenerationRepoStub,
)


def test_contract_preserves_full_objective_and_scientific_evidence() -> None:
    objective = "发表级研究：" + "完整目标" * 3000
    evidence = "宿主不同不等于噬菌体独立；" + "证据" * 3000
    contract = build_research_contract(
        {
            "user_message": objective,
            "chat_history": [{"role": "assistant", "content": evidence}],
            "deliverables": ["完整报告", "候选表"],
            "source_preferences": ["PhageScope", "NCBI", "PubMed"],
        },
        description=objective,
        assumptions=["样本框待确认"],
    )
    prompt = research_contract_prompt(contract)
    assert objective in prompt
    assert evidence in prompt
    assert "完整报告" in prompt
    assert "PubMed" in prompt
    assert "样本框待确认" in prompt
    restored = with_research_contract({"user_message": "Execute full plan"}, contract)
    assert restored["user_message"] == objective
    assert evidence in restored["research_contract_prompt"]


def test_create_plan_persists_contract_and_returns_contract_aware_context(monkeypatch) -> None:
    async def forbidden(*args, **kwargs):
        raise AssertionError("explicit seeds must not start external work")

    for name in ("collect_plan_generation_materials", "_auto_review_and_optimize_plan", "generate_phase_titles"):
        monkeypatch.setattr(f"app.services.plans.plan_generation.{name}", forbidden)
    objective = "保留这项完整研究要求和所有交付物。" * 100
    outcome = asyncio.run(
        create_plan_and_generate(
            title="Research contract",
            description=objective,
            tasks=definitions(),
            repo=_GenerationRepoStub(),
            decomposer=_FailingDecomposerStub(),
            session_context={
                "user_message": objective,
                "chat_history": [{"role": "assistant", "content": "完整文献证据" * 100}],
            },
        )
    )
    assert outcome.plan_tree.metadata["research_contract"]["objective"] == objective
    session_context = outcome.session_context
    assert session_context is not None
    assert session_context["user_message"] == objective
    assert "完整文献证据" * 100 in session_context["research_contract_prompt"]


def test_executor_prompt_uses_full_contract_instead_of_truncated_history() -> None:
    evidence = "不能仅凭宿主分类推断独立谱系。" + "限制" * 600
    contract = build_research_contract(
        {
            "user_message": "完成有证据支持的研究报告",
            "chat_history": [{"role": "assistant", "content": evidence}],
        }
    )
    context = with_research_contract({}, contract)
    prompt = ExecutorPromptBuilder().build(
        node=PlanNode(id=1, plan_id=1, name="review", instruction="Review all evidence"),
        parent=None,
        dependencies=[],
        plan_outline=None,
        include_context=False,
        session_context=context,
    )
    assert evidence in prompt
    assert "=== PERSISTED RESEARCH CONTRACT ===" in prompt
    assert "=== RECENT CONVERSATION ===" not in prompt


def test_background_context_restores_contract_and_only_owner_history(monkeypatch) -> None:
    long_objective = "原始研究目标" * 1000
    contract = build_research_contract({"user_message": long_objective})
    tree = PlanTree(id=7, title="research", metadata={"research_contract": contract})
    repo = SimpleNamespace(get_plan_tree=lambda plan_id: tree)
    monkeypatch.setattr(execution_jobs, "_facade", lambda: SimpleNamespace(_plan_repo=repo))
    calls = []

    class Conn:
        def execute(self, sql, params):
            calls.append((sql, params))
            return SimpleNamespace(fetchall=lambda: [{"role": "user", "content": "后来确认的修订"}])

    class DB:
        def __enter__(self):
            return Conn()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("app.database.get_db", DB)
    result = execution_jobs._research_session_context(
        plan_id=7,
        session_id="session_owner",
        owner_id="owner",
        base={"user_message": "Execute full plan", "chat_history": []},
    )
    assert result["user_message"] == long_objective
    assert "后来确认的修订" in result["research_contract_prompt"]
    assert "s.owner_id=?" in calls[0][0]
    assert calls[0][1] == ("session_owner", "owner")


def test_background_context_without_contract_keeps_legacy_behavior(monkeypatch) -> None:
    tree = PlanTree(id=8, title="legacy", metadata={})
    monkeypatch.setattr(
        execution_jobs,
        "_facade",
        lambda: SimpleNamespace(_plan_repo=SimpleNamespace(get_plan_tree=lambda plan_id: tree)),
    )
    base = {"user_message": "Execute full plan", "chat_history": []}
    assert execution_jobs._research_session_context(
        plan_id=8, session_id=None, owner_id=None, base=base
    ) is base


def test_owner_history_merge_deduplicates_without_dropping_original_objective() -> None:
    contract = build_research_contract(
        {
            "user_message": "原始目标",
            "chat_history": [{"role": "user", "content": "原始目标"}],
        }
    )
    merged = merge_owner_history(
        contract,
        [
            {"role": "user", "content": "原始目标"},
            {"role": "user", "content": "授权加入时间维度"},
        ],
    )
    assert merged["objective"] == "原始目标"
    assert merged["evidence"] == [
        {"role": "user", "content": "原始目标"},
        {"role": "user", "content": "授权加入时间维度"},
    ]
