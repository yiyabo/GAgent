import asyncio

from app.routers.chat.guardrail_handlers import apply_experiment_fallback
from app.routers.chat.guardrails import explicit_manuscript_request, extract_review_topic
from app.services.llm.structured_response import LLMAction, LLMReply, LLMStructuredResponse


class _DummyAgent:
    def __init__(self, user_message: str) -> None:
        self._current_user_message = user_message


def test_explicit_manuscript_request_supports_chinese_review_prompt() -> None:
    assert explicit_manuscript_request(
        "请帮我生成一篇关于 Pseudomonas phage 的英文综述初稿，并附参考文献。"
    )


def test_extract_review_topic_from_chinese_prompt() -> None:
    assert (
        extract_review_topic("请帮我生成一篇关于 Pseudomonas phage 的英文综述初稿，并附参考文献。")
        == "Pseudomonas phage"
    )


def test_review_request_rewrites_retrieval_only_action_to_review_pack_writer() -> None:
    agent = _DummyAgent(
        "请帮我生成一篇关于 Pseudomonas phage 的英文综述初稿，带参考文献，只写 abstract 即可。"
    )
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="I'll search the graph first."),
        actions=[
            LLMAction(
                kind="tool_operation",
                name="graph_rag",
                parameters={"query": "Pseudomonas phage"},
                order=1,
                blocking=True,
            )
        ],
    )

    rewritten = asyncio.run(apply_experiment_fallback(agent, structured))

    assert len(rewritten.actions) == 1
    action = rewritten.actions[0]
    assert action.name == "review_pack_writer"
    assert action.parameters["topic"] == "Pseudomonas phage"
    assert action.parameters["query"] == "Pseudomonas phage"
    assert action.parameters["sections"] == ["abstract"]
