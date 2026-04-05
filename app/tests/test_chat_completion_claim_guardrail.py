from pathlib import Path
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
