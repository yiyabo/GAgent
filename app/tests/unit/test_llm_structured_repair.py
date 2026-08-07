from __future__ import annotations

import asyncio
import json

from app.services.llm.structured_response import (
    build_repair_prompt,
    fallback_reply_response,
    parse_structured_response,
)

_VALID = {"llm_reply": {"message": "相等，三个内角都是 60 度。"}, "actions": []}


def test_parse_direct_json() -> None:
    resp = parse_structured_response(json.dumps(_VALID, ensure_ascii=False))
    assert resp is not None
    assert resp.llm_reply.message == _VALID["llm_reply"]["message"]


def test_parse_fenced_json() -> None:
    raw = "```json\n" + json.dumps(_VALID, ensure_ascii=False) + "\n```"
    assert parse_structured_response(raw) is not None


def test_parse_extracts_json_embedded_in_prose() -> None:
    raw = "好的，我来回答。\n" + json.dumps(_VALID, ensure_ascii=False) + "\n希望对你有帮助。"
    resp = parse_structured_response(raw)
    assert resp is not None
    assert resp.llm_reply.message == _VALID["llm_reply"]["message"]


def test_parse_pure_prose_returns_none() -> None:
    assert parse_structured_response("在现代文明、法律框架下，这是合理的做法。") is None
    assert parse_structured_response("") is None


def test_fallback_wraps_prose_as_reply() -> None:
    resp = fallback_reply_response("  相等，都是 60 度。  ")
    assert resp.llm_reply.message == "相等，都是 60 度。"
    assert resp.actions == []


def test_fallback_never_raises_on_empty() -> None:
    resp = fallback_reply_response("")
    assert len(resp.llm_reply.message) >= 1


def test_repair_prompt_contains_schema_and_excerpt() -> None:
    prompt = build_repair_prompt("这是散文输出")
    assert "llm_reply" in prompt
    assert "这是散文输出" in prompt
    assert "JSON" in prompt


class _FakeService:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls: list[str] = []

    async def chat_async(self, prompt, **kwargs):
        self.calls.append(prompt)
        return self.outputs.pop(0)


def _bare_agent(outputs):
    from app.routers.chat.agent import StructuredChatAgent

    agent = object.__new__(StructuredChatAgent)
    agent.extra_context = {}
    agent._build_prompt = lambda message: "prompt"
    agent.llm_service = _FakeService(outputs)
    return agent


def test_invoke_llm_valid_json_no_repair() -> None:
    agent = _bare_agent([json.dumps(_VALID, ensure_ascii=False)])
    resp = asyncio.run(agent._invoke_llm("等边三角形三个内角都相等吗？"))
    assert resp.llm_reply.message == _VALID["llm_reply"]["message"]
    assert len(agent.llm_service.calls) == 1


def test_invoke_llm_prose_then_repaired() -> None:
    agent = _bare_agent(["直接回答：相等。", json.dumps(_VALID, ensure_ascii=False)])
    resp = asyncio.run(agent._invoke_llm("hi"))
    assert resp.llm_reply.message == _VALID["llm_reply"]["message"]
    assert len(agent.llm_service.calls) == 2
    assert "Assistant output to convert" in agent.llm_service.calls[1]


def test_invoke_llm_all_prose_falls_back_to_plain_reply() -> None:
    agent = _bare_agent(["相等，都是六十度。", "还是散文。"])
    resp = asyncio.run(agent._invoke_llm("等边三角形三个内角都相等吗？"))
    assert resp.llm_reply.message == "相等，都是六十度。"
    assert resp.actions == []
    assert len(agent.llm_service.calls) == 2
