from __future__ import annotations

import json

import pytest

from app.services.plans.plan_delivery import (
    collect_task_evidence,
    generate_plan_delivery,
)
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.research_contract import build_research_contract
from app.tests.plan.test_deliverable_publisher import _build_publisher


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
        assert self.tree.metadata is not None
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


@pytest.mark.parametrize("empty_result", [None, "  ", "null", '{"content": "", "metadata": {}}'])
def test_empty_completed_leaf_does_not_hide_later_evidence(empty_result) -> None:
    plan = tree()
    plan.nodes[2].execution_result = empty_result

    evidence = collect_task_evidence(plan)

    assert "=== TASK 2:" not in evidence
    assert "Task 3 reports contrary evidence and limitations." in evidence


@pytest.fixture
def delivery_publisher(tmp_path, monkeypatch):
    publisher = _build_publisher(tmp_path)
    monkeypatch.setattr("app.services.plans.plan_delivery.get_deliverable_publisher", lambda: publisher)
    return publisher


@pytest.mark.integration
def test_delivery_publishes_reviewed_report_and_both_files_download(
    app_client_factory, delivery_publisher,
) -> None:
    plan = tree()
    plan.nodes[2].execution_result = ""
    plan.nodes[3].execution_result = '{"content":"周一 12、周二 18，合计 30。样本仅有两天。修订标记 W47-R1。"}'
    assert plan.metadata is not None
    plan.metadata["research_contract"] = build_research_contract({
        "user_message": "只用梧桐-47 周报的两项输入，周一 12 个请求、周二 18 个。用中文报告合计和样本限制，禁止外部检索。",
    })
    repo = Repo(plan)
    calls = []
    report_text = "# 梧桐-47 周报 W47-R1\n\n周一 12、周二 18，合计 30 个请求。样本仅有两天。"
    review_text = "合计正确。结果仅描述给定的两天，不能外推。"

    def fake_chat(prompt: str, **kwargs) -> str:
        calls.append(prompt)
        if "independent domain reviewer" in prompt:
            return review_text
        if "DRAFT TO REVISE" in prompt:
            return report_text
        return "# 草稿\n\n合计 30。"

    session_id = "p80-plan-delivery"
    with app_client_factory() as client:
        created = client.patch(f"/chat/sessions/{session_id}", json={"name": "Synthetic weekly report"})
        assert created.status_code == 200
        result = generate_plan_delivery(
            repo=repo,
            plan_id=9,
            session_id=session_id,
            job_id="job_delivery",
            llm_chat=fake_chat,
        )
        assert result["status"] == "completed"
        assert result["review_status"] == "completed"
        assert result["revision_status"] == "completed"
        assert result["publish_status"] == "completed"
        assert result["session_id"] == session_id
        assert result["content"] == report_text
        assert result["report_path"] == "deliverables/latest/docs/plan_9_final_report.md"
        assert result["review_path"] == "deliverables/latest/docs/plan_9_domain_review.md"
        for field, expected in (("report_path", report_text), ("review_path", review_text)):
            path = result[field].removeprefix("deliverables/latest/")
            response = client.get(f"/artifacts/sessions/{session_id}/deliverables/file", params={"path": path})
            assert response.status_code == 200
            assert response.content.decode("utf-8") == f"{expected}\n\n---\n\n*本内容由AI生成，请核实*\n"

    saved = repo.updates[-1]["delivery"]
    assert "content" not in saved and "review" not in saved
    assert saved["report_path"] == result["report_path"]
    assert saved["review_path"] == result["review_path"]
    assert len(calls) == 3
    assert "禁止外部检索" in calls[0]
    assert "合计 30。样本仅有两天。修订标记 W47-R1。" in calls[0]
    assert "=== TASK 2:" not in calls[0]
    assert "host taxonomy differences alone do not establish independent phage lineages" in calls[0]


@pytest.mark.parametrize("incomplete_stage", ["review", "revision"])
def test_actual_report_survives_incomplete_review_or_revision(
    tmp_path, isolated_app_env, delivery_publisher, incomplete_stage,
) -> None:
    repo = Repo(tree())
    draft = "# Report\n\nThe original evidence."

    def fake_chat(prompt: str, **kwargs) -> str:
        if "independent domain reviewer" in prompt:
            if incomplete_stage == "review":
                raise TimeoutError("synthetic review outage")
            return "Narrow the conclusion."
        if "DRAFT TO REVISE" in prompt:
            return "  "
        return draft

    result = generate_plan_delivery(repo=repo, plan_id=9, session_id="partial-delivery", llm_chat=fake_chat)

    assert result["status"] == "partial"
    assert result["publish_status"] == "completed"
    assert result[f"{incomplete_stage}_status"] == "failed"
    if incomplete_stage == "review":
        assert result["revision_status"] == "skipped"
        assert "review_path" not in result
    else:
        assert result["review_path"] == "deliverables/latest/docs/plan_9_domain_review.md"
    path = tmp_path / "runtime" / "session_partial-delivery" / result["report_path"]
    assert path.read_text(encoding="utf-8") == f"{draft}\n\n---\n\n*本内容由AI生成，请核实*\n"


def test_review_and_publish_failure_does_not_reuse_old_report(tmp_path, isolated_app_env, monkeypatch) -> None:
    repo = Repo(tree())
    previous_report = tmp_path / "runtime/session_failed-delivery/deliverables/latest/docs/plan_9_final_report.md"
    previous_report.parent.mkdir(parents=True)
    previous_report.write_text("A prior run's report", encoding="utf-8")

    def fake_chat(prompt: str, **kwargs) -> str:
        if "independent domain reviewer" in prompt:
            raise TimeoutError("synthetic review outage")
        return "# New report"

    def fail_publish() -> None:
        raise OSError("synthetic publisher outage")

    monkeypatch.setattr("app.services.plans.plan_delivery.get_deliverable_publisher", fail_publish)
    result = generate_plan_delivery(repo=repo, plan_id=9, session_id="failed-delivery", llm_chat=fake_chat)

    assert result["status"] == "failed"
    assert result["error"] == "report_not_published"
    assert result["review_status"] == "failed"
    assert result["publish_status"] == "failed"
    assert "report_path" not in result and "review_path" not in result
    assert "report_path" not in repo.updates[-1]["delivery"]
    assert previous_report.read_text(encoding="utf-8") == "A prior run's report"


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
