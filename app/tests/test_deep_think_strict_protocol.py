from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.llm import NativeStreamResult, NativeToolCall
from app.services.deep_think_agent import (
    DeepThinkAgent,
    DeepThinkProtocolError,
    TaskExecutionContext,
    ThinkingStep,
    is_process_only_answer,
)


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


class _RecordingNativeLLM:
    def __init__(self, responses: list[NativeStreamResult]) -> None:
        self._responses = responses
        self._index = 0
        self.calls: list[list[dict]] = []

    async def stream_chat_with_tools_async(self, **kwargs):  # type: ignore[override]
        messages = kwargs.get("messages") or []
        self.calls.append(list(messages))
        if self._index >= len(self._responses):
            raise RuntimeError("No more native mock responses")
        value = self._responses[self._index]
        self._index += 1
        return value

    async def chat_async(self, **kwargs):  # type: ignore[override]
        _ = kwargs
        return "DeepThink native summary"


class _FailingNativeLLM:
    async def stream_chat_with_tools_async(self, **kwargs):  # type: ignore[override]
        _ = kwargs
        raise httpx.ReadTimeout(
            "",
            request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
        )

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


def _build_plan_agent(request_profile: dict) -> DeepThinkAgent:
    return DeepThinkAgent(
        llm_client=_NoStreamLLM(),
        available_tools=["plan_operation"],
        tool_executor=_noop_tool_executor,
        max_iterations=1,
        request_profile=request_profile,
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


def test_native_execute_task_probe_only_cycle_injects_followthrough_nudge() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Need to inspect first",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/task4"},
                    )
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

    async def _tool_executor(_name: str, _params: dict):
        return {"success": True, "summary": "listed /tmp/task4"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=3,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 4,
        },
    )

    result = asyncio.run(
        agent.think(
            "请继续执行 task 4",
            task_context=TaskExecutionContext(
                task_id=4,
                task_name="样本间整合与标准化",
                task_instruction="继续执行 task 4 的整合分析，不要只做目录探查。",
            ),
        )
    )

    assert "BLOCKED_DEPENDENCY" in result.final_answer
    assert len(llm.calls) >= 2
    second_call_messages = llm.calls[1]
    assert any(
        "不要继续做目录清点式" in str(message.get("content") or "")
        for message in second_call_messages
        if isinstance(message, dict)
    )


def test_native_execute_task_second_probe_cycle_requires_execute_or_blocked_dependency() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Need to inspect first",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/task4"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Read the note",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="document_reader",
                        arguments={"operation": "read_text", "file_path": "/tmp/task4/README.txt"},
                    )
                ],
            ),
            NativeStreamResult(
                content="All set",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={"answer": "BLOCKED_DEPENDENCY: missing filtered inputs", "confidence": 0.9},
                    )
                ],
            ),
        ]
    )

    async def _tool_executor(_name: str, _params: dict):
        return {"success": True, "summary": "inspected task inputs"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations", "document_reader", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=4,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 4,
        },
    )

    result = asyncio.run(
        agent.think(
            "请继续执行 task 4",
            task_context=TaskExecutionContext(
                task_id=4,
                task_name="样本间整合与标准化",
                task_instruction="继续执行 task 4 的整合分析，不要只做目录探查。",
            ),
        )
    )

    assert "BLOCKED_DEPENDENCY" in result.final_answer
    assert len(llm.calls) >= 3
    third_call_messages = llm.calls[2]
    assert any(
        "BLOCKED_DEPENDENCY" in str(message.get("content") or "")
        and "二选一" in str(message.get("content") or "")
        for message in third_call_messages
        if isinstance(message, dict)
    )


def test_native_execute_task_third_probe_cycle_stops_with_blocked_dependency() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Inspect files",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/task4"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Inspect notes",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="document_reader",
                        arguments={"operation": "read_text", "file_path": "/tmp/task4/README.txt"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Inspect figure",
                tool_calls=[
                    NativeToolCall(
                        id="tc3",
                        name="vision_reader",
                        arguments={"operation": "read_image", "file_path": "/tmp/task4/summary.png"},
                    )
                ],
            ),
        ]
    )

    async def _tool_executor(_name: str, _params: dict):
        return {"success": True, "summary": "still missing prerequisites"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations", "document_reader", "vision_reader", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=4,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 4,
        },
    )

    result = asyncio.run(
        agent.think(
            "请继续执行 task 4",
            task_context=TaskExecutionContext(
                task_id=4,
                task_name="样本间整合与标准化",
                task_instruction="继续执行 task 4 的整合分析，不要只做目录探查。",
            ),
        )
    )

    assert "BLOCKED_DEPENDENCY" in result.final_answer
    assert "上游交付物" in result.final_answer or "上游" in result.final_answer
    assert len(llm.calls) == 3


def test_structured_plan_outcome_detects_created_plan() -> None:
    agent = _build_plan_agent(
        {
            "requires_structured_plan": True,
            "plan_request_mode": "create",
        }
    )
    step = ThinkingStep(
        iteration=1,
        thought="",
        action=json.dumps({"tool": "plan_operation", "params": {"operation": "create"}}),
        action_result='[plan_operation] {"success": true, "tool": "plan_operation", "result": {"success": true, "operation": "create", "plan_id": 55, "title": "Plan 55"}, "error": null}',
        self_correction=None,
    )

    outcome = agent._summarize_structured_plan_outcome([step], user_query="做一个plan")
    assert outcome["required"] is True
    assert outcome["state"] == "created"
    assert outcome["satisfied"] is True
    assert outcome["plan_id"] == 55


def test_structured_plan_outcome_detects_bound_plan_update() -> None:
    agent = _build_plan_agent(
        {
            "requires_structured_plan": True,
            "plan_request_mode": "update_bound",
            "current_plan_id": 42,
            "current_plan_title": "Plan 42",
        }
    )
    step = ThinkingStep(
        iteration=1,
        thought="",
        action=json.dumps({"tool": "plan_operation", "params": {"operation": "review", "plan_id": 42}}),
        action_result='[plan_operation] {"success": true, "tool": "plan_operation", "result": {"success": true, "operation": "review", "plan_id": 42, "plan_title": "Plan 42"}, "error": null}',
        self_correction=None,
    )

    outcome = agent._summarize_structured_plan_outcome([step], user_query="更新这个plan")
    assert outcome["state"] == "updated"
    assert outcome["satisfied"] is True
    assert outcome["plan_id"] == 42


def test_structured_plan_review_requirement_rejects_get_only() -> None:
    agent = _build_plan_agent(
        {
            "requires_structured_plan": True,
            "plan_request_mode": "update_bound",
            "requires_plan_review": True,
            "current_plan_id": 42,
            "current_plan_title": "Plan 42",
        }
    )
    step = ThinkingStep(
        iteration=1,
        thought="",
        action=json.dumps({"tool": "plan_operation", "params": {"operation": "get", "plan_id": 42}}),
        action_result='[plan_operation] {"success": true, "tool": "plan_operation", "result": {"success": true, "operation": "get", "plan_id": 42, "plan_title": "Plan 42"}, "error": null}',
        self_correction=None,
    )

    outcome = agent._summarize_structured_plan_outcome([step], user_query="审核一下这个任务")
    assert outcome["state"] == "failed"
    assert outcome["satisfied"] is False
    assert "审核" in outcome["message"]


def test_structured_plan_review_and_optimize_requirement_requires_both_ops() -> None:
    agent = _build_plan_agent(
        {
            "requires_structured_plan": True,
            "plan_request_mode": "update_bound",
            "requires_plan_review": True,
            "requires_plan_optimize": True,
            "current_plan_id": 42,
            "current_plan_title": "Plan 42",
        }
    )
    step = ThinkingStep(
        iteration=1,
        thought="",
        action=json.dumps({"tool": "plan_operation", "params": {"operation": "review", "plan_id": 42}}),
        action_result='[plan_operation] {"success": true, "tool": "plan_operation", "result": {"success": true, "operation": "review", "plan_id": 42, "plan_title": "Plan 42"}, "error": null}',
        self_correction=None,
    )

    outcome = agent._summarize_structured_plan_outcome([step], user_query="审核并优化这个计划")
    assert outcome["state"] == "failed"
    assert outcome["satisfied"] is False
    assert "优化" in outcome["message"]


def test_structured_plan_optimize_requirement_rejects_zero_applied_changes() -> None:
    agent = _build_plan_agent(
        {
            "requires_structured_plan": True,
            "plan_request_mode": "update_bound",
            "requires_plan_optimize": True,
            "current_plan_id": 42,
            "current_plan_title": "Plan 42",
        }
    )
    step = ThinkingStep(
        iteration=1,
        thought="",
        action=json.dumps({"tool": "plan_operation", "params": {"operation": "optimize", "plan_id": 42}}),
        action_result='[plan_operation] {"success": true, "tool": "plan_operation", "result": {"success": true, "operation": "optimize", "plan_id": 42, "plan_title": "Plan 42", "applied_changes": 0, "failed_changes": 0, "message": "Applied 0 changes, 0 failed. No real plan updates were applied."}, "error": null}',
        self_correction=None,
    )

    outcome = agent._summarize_structured_plan_outcome([step], user_query="更新一下这个plan")
    assert outcome["state"] == "failed"
    assert outcome["satisfied"] is False
    assert "优化" in outcome["message"]


def test_structured_plan_outcome_marks_text_only_when_tool_never_called() -> None:
    agent = _build_plan_agent(
        {
            "requires_structured_plan": True,
            "plan_request_mode": "create",
        }
    )
    step = ThinkingStep(
        iteration=1,
        thought="这里有一段文本计划",
        action=None,
        action_result=None,
        self_correction=None,
    )

    outcome = agent._summarize_structured_plan_outcome([step], user_query="做一个plan")
    assert outcome["state"] == "text_only"
    assert outcome["satisfied"] is False


def test_structured_plan_outcome_marks_failed_when_plan_tool_fails() -> None:
    agent = _build_plan_agent(
        {
            "requires_structured_plan": True,
            "plan_request_mode": "create",
        }
    )
    step = ThinkingStep(
        iteration=1,
        thought="",
        action=json.dumps({"tool": "plan_operation", "params": {"operation": "create"}}),
        action_result='[plan_operation] {"success": false, "tool": "plan_operation", "result": {"success": false, "operation": "create", "error": "Plan title is required"}, "error": "Plan title is required"}',
        self_correction=None,
    )

    outcome = agent._summarize_structured_plan_outcome([step], user_query="做一个plan")
    assert outcome["state"] == "failed"
    assert outcome["satisfied"] is False


def test_extract_evidence_ignores_unverified_terminal_session_echoed_paths() -> None:
    agent = DeepThinkAgent(
        llm_client=_NoStreamLLM(),
        available_tools=["terminal_session"],
        tool_executor=_noop_tool_executor,
        max_iterations=1,
    )

    evidence = agent._extract_evidence(
        "terminal_session",
        {"operation": "write"},
        {
            "tool": "terminal_session",
            "operation": "write",
            "verification_state": "not_attempted",
            "command_state": "unverified",
            "output": (
                "python3 << 'EOF'\n"
                "input_file = '/Users/apple/LLM/agent/phagescope/gvd_phage_meta_data.tsv'\n"
                "output_file = '/Users/apple/LLM/agent/phagescope/filtered_phages.tsv'\n"
                "EOF"
            ),
        },
    )

    file_refs = [item["ref"] for item in evidence if item.get("type") == "file"]
    assert file_refs == []


def test_process_only_answer_detection_flags_collection_preface() -> None:
    assert is_process_only_answer("我来帮您系统梳理热点方向。让我先收集最新的文献证据。")
    assert is_process_only_answer("Let me first gather the latest evidence and then summarize it.")
    assert not is_process_only_answer("综合近期文献，更值得优先投入的方向是宿主范围预测与鸡尾酒优化。")


def test_research_fallback_prefers_evidence_synthesis_over_process_sentence() -> None:
    agent = DeepThinkAgent(
        llm_client=_DummyLLM([]),
        available_tools=["web_search"],
        tool_executor=_noop_tool_executor,
        max_iterations=1,
        request_profile={"request_tier": "research"},
    )
    steps = [
        ThinkingStep(
            iteration=1,
            thought="我来帮您系统梳理近期噬菌体研究的热点方向。让我先收集最新的文献证据。",
            action='{"tool":"web_search","params":{"query":"recent bacteriophage research 2025 2026"}}',
            action_result='{"success": true, "summary": "宿主范围预测、鸡尾酒优化、递送稳定性和快速检测是近期高频方向。"}',
            self_correction=None,
        )
    ]

    async def _fake_synthesis(user_query: str, evidence_snippets: str, _steps: list[ThinkingStep]) -> str:
        assert "宿主范围预测" in evidence_snippets
        return "综合现有证据，更值得优先关注的方向是宿主范围预测和鸡尾酒优化。"

    agent._generate_fallback_from_evidence = _fake_synthesis  # type: ignore[method-assign]

    answer = asyncio.run(agent._fallback_answer_from_steps(steps, "帮我选一个方向"))
    assert "更值得优先关注的方向" in answer
    assert "让我先收集" not in answer


def test_research_failure_marks_answer_as_unverified_when_external_search_fails() -> None:
    async def _tool_executor(_name: str, _params: dict):
        return {
            "success": False,
            "error": "request_failed",
            "summary": "web search timed out",
        }

    llm = _NativeDummyLLM(
        [
            NativeStreamResult(
                content="Need recent search",
                tool_calls=[
                    NativeToolCall(id="tc1", name="web_search", arguments={"query": "AnewSampling 2025"}),
                ],
            )
        ]
    )

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["web_search"],
        tool_executor=_tool_executor,
        max_iterations=1,
        request_profile={"request_tier": "research"},
    )

    async def _fake_synthesis(_user_query: str, evidence_snippets: str, _steps: list[ThinkingStep]) -> str:
        assert "request_failed" in evidence_snippets
        return "基于现有信息，AnewSampling 更像是生成式全原子采样方向中的新方法，但最新外部检索未拿到可验证来源。"

    agent._generate_fallback_from_evidence = _fake_synthesis  # type: ignore[method-assign]

    result = asyncio.run(agent.think("搜索 AnewSampling 最近的论文并比较方法差异"))
    assert result.search_verified is False
    assert result.fallback_used is True
    assert result.tool_failures
    assert result.tool_failures[0]["tool"] == "web_search"
    assert "未经过本轮在线检索验证" in result.final_answer


def test_native_retries_external_search_tool_once_before_succeeding() -> None:
    attempts: list[str] = []

    async def _tool_executor(name: str, _params: dict):
        attempts.append(name)
        if len(attempts) == 1:
            return {
                "success": False,
                "error": "request_failed",
                "summary": "search backend unavailable",
            }
        return {
            "success": True,
            "summary": "recent phage work collected",
            "items": ["host range prediction", "cocktail optimization"],
        }

    llm = _NativeDummyLLM(
        [
            NativeStreamResult(
                content="Need recent search",
                tool_calls=[
                    NativeToolCall(id="tc1", name="web_search", arguments={"query": "recent phage work"}),
                ],
            ),
            NativeStreamResult(
                content="Done",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={"answer": "优先关注宿主范围预测。", "confidence": 0.8},
                    )
                ],
            ),
        ]
    )
    events: list[dict] = []

    async def _on_tool_result(_tool: str, payload: dict) -> None:
        events.append(payload)

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["web_search"],
        tool_executor=_tool_executor,
        max_iterations=3,
        on_tool_result=_on_tool_result,
        request_profile={"request_tier": "research"},
    )

    result = asyncio.run(agent.think("看一下最近的噬菌体研究"))
    assert result.final_answer == "优先关注宿主范围预测。"
    assert attempts == ["web_search", "web_search"]
    assert any(event.get("retrying") is True for event in events)


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


def test_native_llm_failure_records_exception_type_in_step() -> None:
    agent = DeepThinkAgent(
        llm_client=_FailingNativeLLM(),
        available_tools=["web_search"],
        tool_executor=_noop_tool_executor,
        max_iterations=1,
    )

    result = asyncio.run(agent.think("run native tools"))

    assert result.thinking_steps
    assert result.thinking_steps[0].status == "error"
    assert result.thinking_steps[0].thought == "Error: ReadTimeout"
