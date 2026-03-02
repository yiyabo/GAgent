from __future__ import annotations

import asyncio
import json

import pytest

from app.llm import NativeStreamResult, NativeToolCall
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


class _NativeDummyLLM:
    def __init__(self, responses: list[NativeStreamResult]) -> None:
        self._responses = responses
        self._index = 0

    async def stream_chat_with_tools_async(self, **kwargs):  # type: ignore[override]
        _ = kwargs
        if self._index >= len(self._responses):
            raise RuntimeError("No more native mock responses")
        value = self._responses[self._index]
        self._index += 1
        return value

    async def chat_async(self, **kwargs):  # type: ignore[override]
        _ = kwargs
        return "DeepThink native summary"


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


def test_native_multi_tool_calls_execute_concurrently_and_append_results() -> None:
    started = []

    async def _tool_executor(name: str, params: dict):
        started.append(name)
        if name == "file_operations":
            await asyncio.sleep(0.01)
            return {
                "success": True,
                "path": "/tmp/report.csv",
                "taskid": "task-123",
                "summary": "saved to /tmp/report.csv",
            }
        if name == "web_search":
            await asyncio.sleep(0.01)
            raise RuntimeError("search backend down")
        return {"success": True, "params": params}

    llm = _NativeDummyLLM(
        [
            NativeStreamResult(
                content="Need parallel tools",
                tool_calls=[
                    NativeToolCall(id="tc1", name="file_operations", arguments={"operation": "list", "path": "/tmp"}),
                    NativeToolCall(id="tc2", name="web_search", arguments={"query": "agent"}),
                ],
            ),
            NativeStreamResult(
                content="All set",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={"answer": "done", "confidence": 0.9},
                    )
                ],
            ),
        ]
    )
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations", "web_search"],
        tool_executor=_tool_executor,
        max_iterations=3,
    )

    result = asyncio.run(agent.think("run native tools"))
    assert result.final_answer == "done"
    assert set(started) == {"file_operations", "web_search"}
    assert result.total_iterations >= 2
    assert result.thinking_steps
    first_step = result.thinking_steps[0]
    assert first_step.action is not None
    assert '"tools"' in first_step.action
    assert first_step.action_result is not None
    assert "[file_operations]" in first_step.action_result
    assert "[web_search]" in first_step.action_result
    assert any((ev.get("type") == "file" and ev.get("ref") == "/tmp/report.csv") for ev in first_step.evidence)
    assert any((ev.get("type") == "task" and ev.get("ref") == "task-123") for ev in first_step.evidence)


def test_native_stops_repeated_identical_polling_cycles() -> None:
    call_count = {"n": 0}

    async def _tool_executor(_name: str, _params: dict):
        call_count["n"] += 1
        return {
            "success": True,
            "data": {
                "results": {
                    "id": 37430,
                    "status": "Running",
                    "task_detail": json.dumps(
                        {
                            "task_status": "create",
                            "task_que": [
                                {"module": "annotation", "module_satus": "COMPLETED"},
                                {"module": "quality", "module_satus": "COMPLETED"},
                                {"module": "proteins", "module_satus": "waiting"},
                            ],
                        }
                    ),
                }
            },
        }

    repeated_calls = [
        NativeStreamResult(
            content="Polling task status",
            tool_calls=[
                NativeToolCall(
                    id=f"poll_{idx}",
                    name="phagescope",
                    arguments={"action": "task_detail", "taskid": 37430},
                )
            ],
        )
        for idx in range(20)
    ]
    llm = _NativeDummyLLM(repeated_calls)
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["phagescope"],
        tool_executor=_tool_executor,
        max_iterations=20,
    )

    result = asyncio.run(agent.think("keep polling"))
    assert result.total_iterations < 20
    assert call_count["n"] < 20
    assert "stopped active polling" in result.final_answer.lower()


def test_prompt_based_stops_repeated_identical_polling_cycles() -> None:
    responses = [
        '{"thinking":"poll","action":{"tool":"phagescope","params":{"action":"save_all","taskid":"act_bad_alias"}},"final_answer":null}'
        for _ in range(20)
    ]

    async def _tool_executor(_name: str, _params: dict):
        return {
            "success": False,
            "error": "taskid must be a numeric PhageScope task id",
            "error_code": "invalid_taskid",
        }

    agent = DeepThinkAgent(
        llm_client=_DummyLLM(responses),
        available_tools=["phagescope"],
        tool_executor=_tool_executor,
        max_iterations=20,
    )

    result = asyncio.run(agent.think("poll phagescope with alias"))
    assert result.total_iterations < 20
    assert "stopped active polling" in result.final_answer.lower()
