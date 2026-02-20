from __future__ import annotations

import asyncio

import pytest

from app.services.deep_think_agent import DeepThinkAgent, DeepThinkProtocolError


class _DummyLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._index = 0

    async def stream_chat_async(self, **kwargs):  # type: ignore[override]
        _ = kwargs
        if self._index >= len(self._responses):
            raise RuntimeError("No more mock responses")
        value = self._responses[self._index]
        self._index += 1
        yield value

    async def chat_async(self, **kwargs):  # type: ignore[override]
        if self._index >= len(self._responses):
            raise RuntimeError("No more mock responses")
        value = self._responses[self._index]
        self._index += 1
        return value


class _NoStreamLLM:
    async def chat_async(self, **kwargs):  # type: ignore[override]
        _ = kwargs
        return '{"thinking":"ok","action":null,"final_answer":{"answer":"done","confidence":0.9}}'


async def _noop_tool_executor(_name: str, _params: dict):
    return {"success": True}


def _build_agent(responses: list[str], *, max_iterations: int = 1) -> DeepThinkAgent:
    return DeepThinkAgent(
        llm_client=_DummyLLM(responses),
        available_tools=["web_search"],
        tool_executor=_noop_tool_executor,
        max_iterations=max_iterations,
    )


def test_parse_llm_response_requires_json_protocol() -> None:
    agent = _build_agent([])
    parsed = agent._parse_llm_response(
        '{"thinking":"ok","action":null,"final_answer":{"answer":"done","confidence":0.9}}'
    )
    assert parsed["is_final"] is True
    assert parsed["final_answer"] == "done"
    assert parsed["confidence"] == pytest.approx(0.9)

    with pytest.raises(DeepThinkProtocolError):
        agent._parse_llm_response("<thinking>legacy xml</thinking>")


def test_think_fails_fast_on_non_json_response() -> None:
    agent = _build_agent(["<thinking>legacy xml</thinking>"], max_iterations=1)
    result = asyncio.run(agent.think("analyze this dataset"))
    assert isinstance(result.final_answer, str)
    assert result.final_answer.strip()
    assert result.total_iterations >= 1


def test_think_fails_when_forced_conclusion_not_final() -> None:
    responses = [
        '{"thinking":"need one more step","action":null,"final_answer":null}',
        '{"thinking":"still no final","action":null,"final_answer":null}',
    ]
    agent = _build_agent(responses, max_iterations=1)
    result = asyncio.run(agent.think("analyze this dataset"))
    assert isinstance(result.final_answer, str)
    assert result.final_answer.strip()
    assert result.confidence <= 0.7


def test_think_requires_streaming_llm_client_in_strict_mode() -> None:
    agent = DeepThinkAgent(
        llm_client=_NoStreamLLM(),
        available_tools=["web_search"],
        tool_executor=_noop_tool_executor,
        max_iterations=1,
    )
    result = asyncio.run(agent.think("analyze this dataset"))
    assert result.final_answer.strip()
    assert result.confidence <= 0.5


def test_tool_result_callback_preserves_tool_failure_payload() -> None:
    callbacks = []

    async def _failing_tool_executor(_name: str, _params: dict):
        return {
            "success": False,
            "error": "tool failed",
            "summary": "tool failed summary",
            "result": {"success": False, "error": "tool failed"},
        }

    async def _on_tool_result(_tool: str, payload: dict):
        callbacks.append(payload)

    agent = DeepThinkAgent(
        llm_client=_DummyLLM(
            [
                '{"thinking":"need tool","action":{"tool":"web_search","params":{"query":"x"}},"final_answer":null}',
                '{"thinking":"done","action":null,"final_answer":{"answer":"ok","confidence":0.9}}',
            ]
        ),
        available_tools=["web_search"],
        tool_executor=_failing_tool_executor,
        max_iterations=2,
        on_tool_result=_on_tool_result,
    )

    result = asyncio.run(agent.think("analyze this dataset"))
    assert result.final_answer.strip()
    assert callbacks
    assert callbacks[0]["success"] is False
    assert callbacks[0]["error"] == "tool failed"
    assert callbacks[0]["summary"] == "tool failed summary"
    assert callbacks[0]["iteration"] == 1
