from pathlib import Path
import json
from types import SimpleNamespace

from app.routers.chat_routes import StructuredChatAgent
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.llm.structured_response import LLMReply, LLMStructuredResponse


def _build_agent() -> StructuredChatAgent:
    return StructuredChatAgent.__new__(StructuredChatAgent)


def test_completion_claim_guardrail_rewrites_missing_file_claim(tmp_path: Path) -> None:
    agent = _build_agent()
    missing_path = tmp_path / "missing" / "result.txt"
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(
            message=f"taskcompleted, file: {missing_path}",
        ),
        actions=[],
    )

    patched = agent._apply_completion_claim_guardrail(structured)

    assert "cannot be confirmed" in patched.llm_reply.message.lower()
    assert str(missing_path) in patched.llm_reply.message


def test_completion_claim_guardrail_rewrites_empty_file_claim(tmp_path: Path) -> None:
    agent = _build_agent()
    empty_path = tmp_path / "empty" / "result.txt"
    empty_path.parent.mkdir(parents=True, exist_ok=True)
    empty_path.write_text("", encoding="utf-8")
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(
            message=f"taskcompleted, file: {empty_path}",
        ),
        actions=[],
    )

    patched = agent._apply_completion_claim_guardrail(structured)

    assert "missing or empty" in patched.llm_reply.message.lower()
    assert f"{empty_path} (empty)" in patched.llm_reply.message


def test_completion_claim_guardrail_keeps_message_when_paths_exist(tmp_path: Path) -> None:
    agent = _build_agent()
    output = tmp_path / "output.txt"
    output.write_text("ok", encoding="utf-8")
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(
            message=f"completed, outputfile: {output}",
        ),
        actions=[],
    )

    patched = agent._apply_completion_claim_guardrail(structured)

    assert patched.llm_reply.message == structured.llm_reply.message


def test_completion_claim_guardrail_blocks_claim_when_bound_task_failed() -> None:
    agent = _build_agent()
    tree = PlanTree(
        id=68,
        title="Plan 68",
        nodes={
            66: PlanNode(
                id=66,
                plan_id=68,
                name="数据来源与预处理方法描述",
                status="failed",
            )
        },
        adjacency={None: [66], 66: []},
    )

    class _Repo:
        def get_plan_tree(self, plan_id: int) -> PlanTree:
            assert plan_id == 68
            return tree

    agent.plan_session = SimpleNamespace(plan_id=68, repo=_Repo())
    agent.plan_tree = tree
    agent.extra_context = {"current_task_id": 66}

    structured = LLMStructuredResponse(
        llm_reply=LLMReply(
            message="Task 66 已完成，文件已生成。",
        ),
        actions=[],
    )

    patched = agent._apply_completion_claim_guardrail(structured)

    assert "bound task [66]" in patched.llm_reply.message
    assert "`failed`" in patched.llm_reply.message


def test_completion_claim_guardrail_blocks_claim_when_bound_task_verification_failed() -> None:
    agent = _build_agent()
    tree = PlanTree(
        id=68,
        title="Plan 68",
        nodes={
            66: PlanNode(
                id=66,
                plan_id=68,
                name="数据来源与预处理方法描述",
                status="completed",
                execution_result=json.dumps(
                    {
                        "status": "completed",
                        "metadata": {
                            "verification_status": "failed",
                            "failure_kind": "contract_mismatch",
                        },
                    },
                    ensure_ascii=False,
                ),
            )
        },
        adjacency={None: [66], 66: []},
    )

    class _Repo:
        def get_plan_tree(self, plan_id: int) -> PlanTree:
            assert plan_id == 68
            return tree

    agent.plan_session = SimpleNamespace(plan_id=68, repo=_Repo())
    agent.plan_tree = tree
    agent.extra_context = {"current_task_id": 66}

    structured = LLMStructuredResponse(
        llm_reply=LLMReply(
            message="Task 66 已完成，所有结果文件都已经生成。",
        ),
        actions=[],
    )

    patched = agent._apply_completion_claim_guardrail(structured)

    assert "deterministic artifact verification" in patched.llm_reply.message
    assert "`failed`" in patched.llm_reply.message


def test_completion_claim_guardrail_blocks_completed_subtasks_without_evidence() -> None:
    agent = _build_agent()
    tree = PlanTree(
        id=67,
        title="Plan 67",
        nodes={
            2: PlanNode(
                id=2,
                plan_id=67,
                name="Environment setup and data inventory",
                status="completed",
                execution_result=json.dumps(
                    {
                        "status": "completed",
                        "metadata": {
                            "verification_status": "passed",
                        },
                        "artifact_paths": [
                            "/tmp/runtime/session_x/plan67_task2/run_1/results/data_inventory.md"
                        ],
                    },
                    ensure_ascii=False,
                ),
            ),
            4: PlanNode(
                id=4,
                plan_id=67,
                name="Sequence similarity and group stratification",
                status="completed",
                execution_result="completed as part of parent task",
            ),
            5: PlanNode(
                id=5,
                plan_id=67,
                name="Genomic diversity quantification",
                status="completed",
                execution_result="completed without recorded outputs",
            ),
        },
        adjacency={None: [2, 4, 5], 2: [], 4: [], 5: []},
    )

    class _Repo:
        def get_plan_tree(self, plan_id: int) -> PlanTree:
            assert plan_id == 67
            return tree

    agent.plan_session = SimpleNamespace(plan_id=67, repo=_Repo())
    agent.plan_tree = tree
    agent.extra_context = {}

    structured = LLMStructuredResponse(
        llm_reply=LLMReply(
            message=(
                "## Completed Sub-Tasks\n\n"
                "#### Task 2: Environment setup and data inventory\n"
                "- Generated results/data_inventory.md\n\n"
                "#### Task 4: Sequence similarity and group stratification\n"
                "- Completed successfully.\n\n"
                "| Task | Name | Status |\n"
                "| --- | --- | --- |\n"
                "| 5 | Genomic diversity quantification | Completed |\n"
            ),
        ),
        actions=[],
    )

    patched = agent._apply_completion_claim_guardrail(structured)

    assert "no trustworthy execution evidence" in patched.llm_reply.message
    assert "[4]" in patched.llm_reply.message
    assert "[5]" in patched.llm_reply.message


def test_completion_claim_guardrail_blocks_task_output_misattribution() -> None:
    agent = _build_agent()
    tree = PlanTree(
        id=67,
        title="Plan 67",
        nodes={
            3: PlanNode(
                id=3,
                plan_id=67,
                name="Subset definition and quality control",
                status="completed",
                execution_result=json.dumps(
                    {
                        "status": "completed",
                        "metadata": {"verification_status": "passed"},
                        "artifact_paths": [
                            "/tmp/runtime/session_x/plan67_task3/run_1/results/terminal_code_stats.csv",
                            "/tmp/runtime/session_x/plan67_task3/run_1/results/terminal_code_summary.md",
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
        },
        adjacency={None: [3], 3: []},
    )

    class _Repo:
        def get_plan_tree(self, plan_id: int) -> PlanTree:
            assert plan_id == 67
            return tree

    agent.plan_session = SimpleNamespace(plan_id=67, repo=_Repo())
    agent.plan_tree = tree
    agent.extra_context = {}

    structured = LLMStructuredResponse(
        llm_reply=LLMReply(
            message=(
                "## Completed Sub-Tasks\n\n"
                "#### Task 3: Subset definition and quality control\n"
                "- Generated deliverables:\n"
                "  - `results/subset_manifest.tsv`\n"
                "  - `results/qc_stats.csv`\n"
            ),
        ),
        actions=[],
    )

    patched = agent._apply_completion_claim_guardrail(structured)

    assert "do not match" in patched.llm_reply.message.lower()
    assert "Task [3]" in patched.llm_reply.message
    assert "subset_manifest.tsv" in patched.llm_reply.message
