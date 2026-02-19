from pathlib import Path

from app.routers.chat_routes import StructuredChatAgent
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
