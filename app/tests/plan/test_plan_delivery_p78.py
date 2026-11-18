from __future__ import annotations

import json

from app.services.plans.plan_delivery import (
    collect_task_evidence,
    generate_plan_delivery,
)
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.research_contract import build_research_contract


class Repo:
    def __init__(self, tree: PlanTree) -> None:
        self.tree = tree
        self.updates = []

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        assert plan_id == self.tree.id
        return self.tree

    def update_plan_metadata(self, plan_id: int, metadata: dict) -> None:
        assert plan_id == self.tree.id
        self.updates.append(metadata)
        self.tree.metadata.update(metadata)


def tree() -> PlanTree:
    contract = build_research_contract(
        {
            "user_message": "形成有完整证据与科学限制的发表级研究报告",
            "chat_history": [{"role": "assistant", "content": "宿主分类差异不能独立证明噬菌体谱系。"}],
        }
    )
    return PlanTree(
        id=9,
        title="delivery",
        metadata={"research_contract": contract},
        nodes={
            1: PlanNode(
                id=1,
                plan_id=9,
                name="stage",
                status="completed",
                execution_result=json.dumps({"auto_completed": True, "content": "262-char placeholder"}),
            ),
            2: PlanNode(
                id=2,
                plan_id=9,
                name="analysis",
                parent_id=1,
                status="completed",
                instruction="分析证据",
                execution_result=json.dumps(
                    {
                        "content": "Task 2 integrated finding with quantitative result 42%.",
                        "metadata": {"artifact_paths": ["results/analysis.md"]},
                    }
                ),
            ),
            3: PlanNode(
                id=3,
                plan_id=9,
                name="literature",
                parent_id=1,
                status="completed",
                execution_result=json.dumps({"content": "Task 3 reports contrary evidence and limitations."}),
            ),
        },
        adjacency={None: [1], 1: [2, 3]},
    )


def test_evidence_uses_real_leaf_results_not_composite_placeholders() -> None:
    evidence = collect_task_evidence(tree())
    assert "Task 2 integrated finding" in evidence
    assert "Task 3 reports contrary evidence" in evidence
    assert "results/analysis.md" in evidence
    assert "262-char placeholder" not in evidence


def test_delivery_runs_review_revision_and_preserves_report_after_publish(tmp_path, monkeypatch) -> None:
    repo = Repo(tree())
    calls = []

    def fake_chat(prompt: str, **kwargs) -> str:
        calls.append(prompt)
        if "independent domain reviewer" in prompt:
            return "Do not infer independent phage lineages from host taxonomy alone."
        if "DRAFT TO REVISE" in prompt:
            return "# Final integrated report\n\nNarrowed findings, methods, interpretation, limitations and deliverables."
        return "# Draft\n\nPreliminary report."

    class Publisher:
        def publish_from_tool_result(self, **kwargs):
            assert kwargs["plan_id"] == 9
            assert kwargs["session_id"] == "session_owner"
            assert len(kwargs["raw_result"]["deliverable_submit"]["artifacts"]) == 2
            return object()

    monkeypatch.setattr(
        "app.services.plans.plan_delivery.get_session_raw_files_dir",
        lambda session_id, create=True: tmp_path,
    )
    monkeypatch.setattr(
        "app.services.plans.plan_delivery.get_deliverable_publisher",
        lambda: Publisher(),
    )
    result = generate_plan_delivery(
        repo=repo,
        plan_id=9,
        session_id="session_owner",
        job_id="job_delivery",
        llm_chat=fake_chat,
    )
    assert result["status"] == "completed"
    assert result["review_status"] == "completed"
    assert result["revision_status"] == "completed"
    assert result["content"].startswith("# Final integrated report")
    assert isinstance(result["content"], str)
    assert result["report_path"] == "deliverables/latest/docs/plan_9_final_report.md"
    assert result["review_path"] == "deliverables/latest/docs/plan_9_domain_review.md"
    assert "content" not in repo.updates[-1]["delivery"]
    assert len(calls) == 3
    assert "Task 2 integrated finding" in calls[0]
    assert "host taxonomy differences alone do not establish independent phage lineages" in calls[0]


def test_delivery_failure_is_explicit_and_does_not_publish(tmp_path, monkeypatch) -> None:
    repo = Repo(tree())

    def fail_chat(prompt: str, **kwargs) -> str:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        "app.services.plans.plan_delivery.get_session_raw_files_dir",
        lambda session_id, create=True: tmp_path,
    )
    result = generate_plan_delivery(
        repo=repo,
        plan_id=9,
        session_id="session_owner",
        llm_chat=fail_chat,
    )
    assert result["status"] == "failed"
    assert result["error"] == "integrated_synthesis:RuntimeError"
    assert "report_path" not in result
    assert repo.updates[-1]["delivery"]["status"] == "failed"


def test_legacy_plan_skips_without_calling_a_model() -> None:
    legacy = PlanTree(id=10, title="legacy", metadata={})
    repo = Repo(legacy)

    def forbidden(prompt: str, **kwargs) -> str:
        raise AssertionError("legacy plan must not synthesize")

    result = generate_plan_delivery(repo=repo, plan_id=10, session_id=None, llm_chat=forbidden)
    assert result == {"status": "skipped", "reason": "legacy_plan_without_research_contract"}


def test_no_completed_leaf_evidence_fails_closed() -> None:
    empty = tree()
    for node in empty.nodes.values():
        node.status = "pending"
    repo = Repo(empty)

    def forbidden(prompt: str, **kwargs) -> str:
        raise AssertionError("no evidence must not synthesize")

    result = generate_plan_delivery(repo=repo, plan_id=9, session_id=None, llm_chat=forbidden)
    assert result == {"status": "failed", "error": "no_completed_leaf_evidence"}
