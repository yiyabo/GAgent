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

    # After the fix: a single probe-only cycle should NOT force BLOCKED_DEPENDENCY.
    # The nudge is still injected, but the AI's real answer ("done") is accepted.
    assert result.final_answer == "done"
    assert len(llm.calls) >= 2
    second_call_messages = llm.calls[1]
    # The followthrough nudge should still be injected after the first probe cycle.
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


def test_probe_only_with_available_upstream_outputs_forces_code_executor() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Inspect upstream outputs first",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/task42/results"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Read the upstream summary",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="document_reader",
                        arguments={"operation": "read_text", "file_path": "/tmp/task42/gsea_summary.json"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Summarize the export",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "已完成任务 43，并导出了 GSEA 汇总表。",
                            "confidence": 0.93,
                        },
                    )
                ],
            ),
        ]
    )

    observed_calls: list[tuple[str, dict]] = []

    async def _tool_executor(name: str, params: dict):
        observed_calls.append((name, dict(params)))
        if name == "code_executor":
            return {
                "success": True,
                "produced_files": ["/tmp/task43/gsea_export.csv"],
            }
        return {"success": True, "summary": "inspected upstream artifacts"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations", "document_reader", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=5,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 43,
            "explicit_task_override": True,
        },
    )

    result = asyncio.run(
        agent.think(
            "继续执行 task 43",
            task_context=TaskExecutionContext(
                task_id=43,
                task_name="3.2.3.3 GSEA 结果处理与导出",
                task_instruction="提取 GSEA 显著通路并导出汇总表。",
                dependency_outputs=[
                    {
                        "task_id": 42,
                        "task_name": "3.2.3.2 GSEA 分析执行",
                        "status": "completed",
                        "artifact_paths": [
                            "/tmp/task42/gsea_results.rds",
                            "/tmp/task42/gsea_summary.json",
                        ],
                    }
                ],
                explicit_task_ids=[8],
                explicit_task_override=True,
            ),
        )
    )

    assert result.final_answer == "已完成任务 43，并导出了 GSEA 汇总表。"
    assert "BLOCKED_DEPENDENCY" not in result.final_answer
    code_exec_calls = [params for name, params in observed_calls if name == "code_executor"]
    assert code_exec_calls
    assert "/tmp/task42/gsea_summary.json" in code_exec_calls[0]["task"]


def test_probe_only_after_real_execution_tool_stops_with_neutral_summary() -> None:
    """After a real execution tool runs, at most one observation-only verification
    cycle is allowed. A second probe-only cycle should stop with a neutral
    post-execution summary instead of BLOCKED_DEPENDENCY."""
    llm = _RecordingNativeLLM(
        [
            # Iteration 1: observation-only (file_operations list)
            NativeStreamResult(
                content="Inspecting workspace",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/task17"},
                    )
                ],
            ),
            # Iteration 2: REAL execution tool (code_executor)
            NativeStreamResult(
                content="Running analysis code",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="code_executor",
                        arguments={"code": "print('analysis done')", "language": "python"},
                    )
                ],
            ),
            # Iteration 3: observation-only (document_reader)
            NativeStreamResult(
                content="Checking output",
                tool_calls=[
                    NativeToolCall(
                        id="tc3",
                        name="document_reader",
                        arguments={"operation": "read_text", "file_path": "/tmp/task17/summary.md"},
                    )
                ],
            ),
            # Iteration 4: observation-only (file_operations)
            NativeStreamResult(
                content="Listing outputs",
                tool_calls=[
                    NativeToolCall(
                        id="tc4",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/task17/results"},
                    )
                ],
            ),
        ]
    )

    async def _tool_executor(_name: str, _params: dict):
        return {"success": True, "summary": "executed successfully"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations", "document_reader", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=6,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 17,
        },
    )

    result = asyncio.run(
        agent.think(
            "请执行任务 17",
            task_context=TaskExecutionContext(
                task_id=17,
                task_name="差异表达分析",
                task_instruction="对各细胞类型进行差异表达基因分析。",
            ),
        )
    )

    # The system should stop the repeated post-execution probing without
    # rewriting the outcome into BLOCKED_DEPENDENCY.
    assert "BLOCKED_DEPENDENCY" not in result.final_answer
    assert "任务 17" in result.final_answer or "差异表达" in result.final_answer
    assert "观察循环" in result.final_answer or "验证" in result.final_answer
    assert result.confidence >= 0.8


def test_blocked_dependency_answer_prefers_structured_document_reader_summary() -> None:
    agent = DeepThinkAgent(
        llm_client=_RecordingNativeLLM([]),
        available_tools=["document_reader", "code_executor"],
        tool_executor=lambda *_args, **_kwargs: None,
    )

    noisy_tool_result_text = json.dumps(
        {
            "success": True,
            "tool": "document_reader",
            "result": {
                "tool": "document_reader",
                "success": True,
                "text": "{\"tool\":\"code_executor\",\"task\":\"[OUTER AGENT EXECUTION CONTRACT] ...\"}",
                "summary": "Successfully read text file, extracted 2048 characters",
            },
            "error": None,
        },
        ensure_ascii=False,
    )

    answer = agent._build_blocked_dependency_answer(
        task_context=TaskExecutionContext(
            task_id=6,
            task_name="2.2 细胞类型注释",
            task_instruction="继续执行细胞类型注释",
        ),
        user_query="继续执行 task 6",
        tool_results=[
            {
                "tool_name": "document_reader",
                "tool_params": {"operation": "read_text"},
                "tool_result_text": noisy_tool_result_text,
            }
        ],
    )

    assert "Observed clues:" in answer
    assert "Successfully read text file, extracted 2048 characters" in answer
    assert "[OUTER AGENT EXECUTION CONTRACT]" not in answer


def test_native_large_file_listing_is_compacted_before_next_llm_call() -> None:
    large_items = [
        {
            "name": f"sample_{idx}.fasta",
            "path": f"/tmp/gvd/sample_{idx}.fasta",
            "type": "file",
            "size": 5000 + idx,
        }
        for idx in range(5000)
    ]
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Inspect the extracted directory first",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/gvd"},
                    )
                ],
            ),
            NativeStreamResult(
                content="done",
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

    async def _tool_executor(name: str, params: dict):
        assert name == "file_operations"
        assert params["operation"] == "list"
        return {
            "operation": "list",
            "path": params["path"],
            "success": True,
            "items": large_items,
            "count": len(large_items),
        }

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations"],
        tool_executor=_tool_executor,
        max_iterations=3,
    )

    result = asyncio.run(agent.think("check whether the directory is extracted"))

    assert result.final_answer == "done"
    assert len(llm.calls) >= 2
    second_call_messages = llm.calls[1]
    tool_messages = [msg for msg in second_call_messages if msg.get("role") == "tool"]
    assert len(tool_messages) == 1

    payload = json.loads(str(tool_messages[0]["content"]))
    compact_result = payload["result"]
    assert compact_result["llm_compacted"] is True
    assert compact_result["count"] == len(large_items)
    assert compact_result["omitted_items"] > 0
    assert len(compact_result["sample_items"]) < len(large_items)
    assert len(str(tool_messages[0]["content"])) < DeepThinkAgent.MAX_TOOL_RESULT_TEXT_CHARS
    assert "sample_4999.fasta" not in str(tool_messages[0]["content"])


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


def test_native_plan_create_injects_finalize_nudge() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Create the requested plan",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="plan_operation",
                        arguments={
                            "operation": "create",
                            "title": "Genome diversity plan",
                            "description": "Plan the genomic diversity analysis.",
                            "tasks": [
                                {
                                    "name": "Define subset",
                                    "instruction": "Select representative phage groups.",
                                }
                            ],
                        },
                    )
                ],
            ),
            NativeStreamResult(
                content="Finalize the response",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "Structured plan created successfully.",
                            "confidence": 0.92,
                        },
                    )
                ],
            ),
        ]
    )

    async def _tool_executor(name: str, _params: dict):
        assert name == "plan_operation"
        return {
            "success": True,
            "operation": "create",
            "plan_id": 58,
            "title": "Genome diversity plan",
        }

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["plan_operation"],
        tool_executor=_tool_executor,
        max_iterations=3,
        request_profile={
            "requires_structured_plan": True,
            "plan_request_mode": "create",
        },
    )

    result = asyncio.run(
        agent.think("Create a structured plan for phage genomic diversity analysis")
    )

    second_call_messages = llm.calls[1]
    user_messages = [
        str(message.get("content") or "")
        for message in second_call_messages
        if message.get("role") == "user"
    ]
    assert any(
        "Do not call `plan_operation` with `create` again" in message
        for message in user_messages
    )
    assert result.structured_plan_satisfied is True
    assert result.structured_plan_plan_id == 58


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


def test_post_execution_probe_stop_answer_filters_outputs_to_current_task_scope() -> None:
    agent = DeepThinkAgent(
        llm_client=_NoStreamLLM(),
        available_tools=["file_operations"],
        tool_executor=_noop_tool_executor,
        max_iterations=1,
    )
    step = ThinkingStep(
        iteration=1,
        thought="",
        action=None,
        action_result=None,
        self_correction=None,
        evidence=[
            {
                "type": "file",
                "title": "Generated file",
                "ref": "/tmp/runtime/session/results/qc_summary.csv",
                "snippet": "old session output",
            },
            {
                "type": "file",
                "title": "Generated file",
                "ref": "/tmp/runtime/session/results/plan68_task34/run_1/results/upregulated_genes.csv",
                "snippet": "task 34 output",
            },
        ],
    )

    answer = agent._build_post_execution_probe_stop_answer(
        task_context=TaskExecutionContext(
            task_id=35,
            task_name="基因 ID 转换与标准化",
        ),
        user_query="请执行任务35",
        steps=[step],
    )

    assert "qc_summary.csv" not in answer
    assert "plan68_task34" not in answer
    assert "当前 task 作用域内" in answer


def test_post_execution_probe_stop_answer_reports_verified_completion_when_tool_passed() -> None:
    agent = DeepThinkAgent(
        llm_client=_NoStreamLLM(),
        available_tools=["code_executor"],
        tool_executor=_noop_tool_executor,
        max_iterations=1,
    )
    step = ThinkingStep(
        iteration=1,
        thought="",
        action=None,
        action_result=None,
        self_correction=None,
        evidence=[
            {
                "type": "file",
                "title": "Generated file",
                "ref": "/tmp/runtime/session/results/plan68_task36/run_1/enrichment/background_genes.csv",
                "snippet": "task 36 output",
            },
        ],
    )

    answer = agent._build_post_execution_probe_stop_answer(
        task_context=TaskExecutionContext(
            task_id=36,
            task_name="背景基因集构建",
        ),
        user_query="执行任务36",
        steps=[step],
        tool_results=[
            {
                "tool_name": "code_executor",
                "tool_result": {
                    "success": True,
                    "result": {
                        "verification_status": "passed",
                        "artifact_paths": [
                            "/tmp/runtime/session/results/plan68_task36/run_1/enrichment/background_genes.csv",
                        ],
                        "metadata": {
                            "verification": {"status": "passed"},
                        },
                    },
                },
            },
        ],
    )

    assert "执行与验证实际上已完成" in answer
    assert "background_genes.csv" in answer
    assert "可直接基于这些结果继续后续任务" in answer


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


def test_structured_fallback_uses_user_facing_evidence_format() -> None:
    agent = DeepThinkAgent(
        llm_client=_DummyLLM([]),
        available_tools=["file_operations"],
        tool_executor=_noop_tool_executor,
        max_iterations=1,
    )
    steps = [
        ThinkingStep(
            iteration=1,
            thought="先看目录结构，再总结。",
            action='{"tool":"file_operations","params":{"operation":"list","path":"/tmp"}}',
            action_result='{"success": true, "summary": "Found 2 files: a.tsv, b.tsv"}',
            self_correction=None,
        )
    ]

    answer = agent._build_structured_fallback(steps, "看看结果")

    assert "[Step 1]" not in answer
    assert "我先给出目前能确认的信息" in answer
    assert "Found 2 files" in answer


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


# ---------------------------------------------------------------------------
# Partial Completion Retry Tests
# ---------------------------------------------------------------------------


def _make_code_executor_partial_payload(ratio: str = "2/6", produced_files: list | None = None) -> dict:
    """Build a code_executor tool result dict with partial completion flagged.

    Returns the raw payload as the real code_executor handler would return it
    (before deep_think wraps it in ``{"success": ..., "tool": ..., "result": <this>}``).
    """
    files = produced_files or [
        "/tmp/task20/results/enrichment_Epithelial.csv",
        "/tmp/task20/results/enrichment_Fibroblast.csv",
    ]
    return {
        "success": True,
        "produced_files": files,
        "produced_files_count": len(files),
        "task_directory_full": "/tmp/task20",
        "partial_completion_suspected": True,
        "partial_ratio": ratio,
    }


def _make_code_executor_full_payload(produced_files: list | None = None) -> dict:
    """Build a code_executor tool result dict with NO partial completion.

    Returns the raw payload as the real code_executor handler would return it.
    """
    files = produced_files or [
        "/tmp/task20/results/enrichment_Epithelial.csv",
        "/tmp/task20/results/enrichment_Fibroblast.csv",
        "/tmp/task20/results/enrichment_Tcell.csv",
        "/tmp/task20/results/enrichment_Bcell.csv",
        "/tmp/task20/results/enrichment_Myeloid.csv",
        "/tmp/task20/results/enrichment_Endothelial.csv",
    ]
    return {
        "success": True,
        "produced_files": files,
        "produced_files_count": len(files),
        "task_directory_full": "/tmp/task20",
    }


def test_detect_partial_completion_helper() -> None:
    """Unit test for _detect_partial_completion_in_tool_results static method."""
    agent = DeepThinkAgent(
        llm_client=_NoStreamLLM(),
        available_tools=["code_executor"],
        tool_executor=_noop_tool_executor,
        max_iterations=1,
    )

    # Positive case: partial completion detected
    # Wrap as deep_think_agent does: {"success": ..., "tool": ..., "result": <raw_payload>}
    partial_payload = _make_code_executor_partial_payload("2/6")
    partial_results = [
        {
            "tool_name": "code_executor",
            "tool_params": {"task": "enrichment"},
            "tool_result_text": json.dumps({
                "success": True,
                "tool": "code_executor",
                "result": partial_payload,
                "error": None,
            }, ensure_ascii=False),
        }
    ]
    info = agent._detect_partial_completion_in_tool_results(partial_results)
    assert info is not None
    assert info["partial_ratio"] == "2/6"
    assert len(info["produced_files"]) == 2
    assert info["task_directory_full"] == "/tmp/task20"

    # Negative case: full completion — no detection
    full_payload = _make_code_executor_full_payload()
    full_results = [
        {
            "tool_name": "code_executor",
            "tool_params": {"task": "enrichment"},
            "tool_result_text": json.dumps({
                "success": True,
                "tool": "code_executor",
                "result": full_payload,
                "error": None,
            }, ensure_ascii=False),
        }
    ]
    assert agent._detect_partial_completion_in_tool_results(full_results) is None

    # Negative case: non-code_executor tool — ignored
    other_results = [
        {
            "tool_name": "file_operations",
            "tool_params": {"operation": "list"},
            "tool_result_text": json.dumps({"success": True}),
        }
    ]
    assert agent._detect_partial_completion_in_tool_results(other_results) is None

    # Edge case: empty list
    assert agent._detect_partial_completion_in_tool_results([]) is None


def test_partial_completion_injects_retry_nudge() -> None:
    """When code_executor returns partial completion, a retry nudge should be
    injected, and the agent should call code_executor again."""
    llm = _RecordingNativeLLM(
        [
            # Iteration 1: code_executor runs → partial result (2/6)
            NativeStreamResult(
                content="Running enrichment analysis",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="code_executor",
                        arguments={"task": "GO enrichment for all cell types"},
                    )
                ],
            ),
            # Iteration 2: after nudge, code_executor again → full result
            NativeStreamResult(
                content="Retrying remaining cell types",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="code_executor",
                        arguments={"task": "GO enrichment for remaining 4 cell types"},
                    )
                ],
            ),
            # Iteration 3: submit final answer
            NativeStreamResult(
                content="All done",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "已完成全部 6 种细胞类型的 GO 富集分析。",
                            "confidence": 0.95,
                        },
                    )
                ],
            ),
        ]
    )

    call_count = {"n": 0}

    async def _tool_executor(name: str, _params: dict):
        if name == "code_executor":
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call: partial
                return _make_code_executor_partial_payload("2/6")
            else:
                # Second call: full
                return _make_code_executor_full_payload()
        return {"success": True, "summary": "ok"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=5,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 20,
        },
    )

    result = asyncio.run(
        agent.think(
            "请执行任务 20",
            task_context=TaskExecutionContext(
                task_id=20,
                task_name="GO/KEGG富集分析",
                task_instruction="对各细胞类型进行 GO/KEGG 功能富集分析。",
            ),
        )
    )

    # The final answer should be the agent's real answer, not BLOCKED_DEPENDENCY
    assert "BLOCKED_DEPENDENCY" not in result.final_answer
    assert "6" in result.final_answer or "富集" in result.final_answer
    # code_executor was called twice
    assert call_count["n"] == 2
    # Check that a retry nudge was injected (present in messages sent to LLM)
    # The second LLM call should have the nudge in its messages
    assert len(llm.calls) >= 2
    second_call_messages = llm.calls[1]
    assert any(
        "部分完成" in str(msg.get("content", "")) or "partial completion" in str(msg.get("content", "")).lower()
        for msg in second_call_messages
        if isinstance(msg, dict)
    )


def test_partial_completion_respects_max_retries() -> None:
    """Every code_executor call returns partial → max 6 retry nudges then stops."""

    # Build 8 iterations: each returns partial, to exceed _MAX_PARTIAL_RETRIES=6
    responses = [
        NativeStreamResult(
            content=f"Running batch attempt {i+1}",
            tool_calls=[
                NativeToolCall(
                    id=f"tc{i+1}",
                    name="code_executor",
                    arguments={"task": f"enrichment attempt {i+1}"},
                )
            ],
        )
        for i in range(8)
    ]
    llm = _RecordingNativeLLM(responses)

    async def _tool_executor(name: str, _params: dict):
        if name == "code_executor":
            return _make_code_executor_partial_payload("2/6")
        return {"success": True}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["code_executor"],
        tool_executor=_tool_executor,
        max_iterations=10,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 20,
        },
    )

    result = asyncio.run(
        agent.think(
            "请执行任务 20",
            task_context=TaskExecutionContext(
                task_id=20,
                task_name="GO/KEGG富集分析",
                task_instruction="对各细胞类型进行 GO/KEGG 功能富集分析。",
            ),
        )
    )

    # Count actual nudge injections: each call's message list grows as nudges
    # accumulate. Count how many calls have MORE nudge messages than the previous.
    nudge_count = 0
    prev_nudge_total = 0
    for call_messages in llm.calls:
        current_nudge_total = sum(
            1
            for msg in call_messages
            if isinstance(msg, dict)
            and ("部分完成" in str(msg.get("content", ""))
                 or "partial completion" in str(msg.get("content", "")).lower())
        )
        if current_nudge_total > prev_nudge_total:
            nudge_count += (current_nudge_total - prev_nudge_total)
        prev_nudge_total = current_nudge_total

    # Should inject at most 6 retry nudges (_MAX_PARTIAL_RETRIES)
    assert nudge_count <= 6
    # The agent should have iterated but eventually stopped (max_iterations or no more nudges)
    assert result.total_iterations <= 10


def test_full_completion_no_retry_nudge() -> None:
    """When code_executor returns a full result (no partial), no retry nudge
    should be injected."""
    llm = _RecordingNativeLLM(
        [
            # Iteration 1: code_executor runs → full result
            NativeStreamResult(
                content="Running enrichment analysis",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="code_executor",
                        arguments={"task": "GO enrichment for all cell types"},
                    )
                ],
            ),
            # Iteration 2: submit final answer directly
            NativeStreamResult(
                content="All done",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "已完成全部 6 种细胞类型的 GO 富集分析。",
                            "confidence": 0.95,
                        },
                    )
                ],
            ),
        ]
    )

    async def _tool_executor(name: str, _params: dict):
        if name == "code_executor":
            return _make_code_executor_full_payload()
        return {"success": True}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=4,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 20,
        },
    )

    result = asyncio.run(
        agent.think(
            "请执行任务 20",
            task_context=TaskExecutionContext(
                task_id=20,
                task_name="GO/KEGG富集分析",
                task_instruction="对各细胞类型进行 GO/KEGG 功能富集分析。",
            ),
        )
    )

    # No retry nudge should be injected
    for call_messages in llm.calls:
        for msg in call_messages:
            if isinstance(msg, dict):
                content = str(msg.get("content", ""))
                assert "部分完成" not in content
                assert "partial completion" not in content.lower()

    # Final answer should be the real answer
    assert "BLOCKED_DEPENDENCY" not in result.final_answer
    assert "6" in result.final_answer or "富集" in result.final_answer
    assert result.total_iterations == 2


def test_plan_operation_does_not_set_had_real_execution_tool() -> None:
    """When the AI calls plan_operation (coordination) + file_operations (observation)
    but never code_executor, the system should NOT emit '任务代码已执行完毕'.
    Instead it should produce a BLOCKED_DEPENDENCY since no code was executed.

    This reproduces the bug: plan_operation was incorrectly treated as a 'real
    execution tool', causing had_real_execution_tool=True even though no code ran.
    """
    llm = _RecordingNativeLLM(
        [
            # Iteration 1: plan_operation (coordination, NOT code execution)
            NativeStreamResult(
                content="Updating plan info",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="plan_operation",
                        arguments={"operation": "get", "plan_id": 68},
                    )
                ],
            ),
            # Iteration 2: file_operations (observation)
            NativeStreamResult(
                content="Checking results directory",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/task34/results"},
                    )
                ],
            ),
            # Iteration 3: file_operations (observation)
            NativeStreamResult(
                content="Checking again",
                tool_calls=[
                    NativeToolCall(
                        id="tc3",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/task34/results"},
                    )
                ],
            ),
            # Iteration 4: file_operations (observation) — would trigger hard stop
            NativeStreamResult(
                content="Still checking",
                tool_calls=[
                    NativeToolCall(
                        id="tc4",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/task34/results"},
                    )
                ],
            ),
        ]
    )

    async def _tool_executor(_name: str, _params: dict):
        return {"success": True, "summary": "ok"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["plan_operation", "file_operations", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=5,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 34,
        },
    )

    result = asyncio.run(
        agent.think(
            "请执行任务 8",
            task_context=TaskExecutionContext(
                task_id=34,
                task_name="差异基因提取与分类",
                task_instruction="从差异表达结果中提取基因列表并分类。",
            ),
        )
    )

    # Key assertion: should NOT say "任务代码已执行完毕" since no code ran
    assert "任务代码已执行完毕" not in result.final_answer
    assert "Task code executed" not in result.final_answer
    # Should produce BLOCKED_DEPENDENCY (the correct behavior when no code ran)
    assert "BLOCKED_DEPENDENCY" in result.final_answer


def test_code_executor_still_sets_had_real_execution_tool() -> None:
    """When code_executor actually runs, the flag should still be True and the
    neutral message should appear if the AI then enters a probe loop."""
    llm = _RecordingNativeLLM(
        [
            # Iteration 1: code_executor (REAL execution)
            NativeStreamResult(
                content="Running code",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="code_executor",
                        arguments={"task": "extract DE genes"},
                    )
                ],
            ),
            # Iterations 2-4: observation loop
            NativeStreamResult(
                content="Checking output 1",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/results"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Checking output 2",
                tool_calls=[
                    NativeToolCall(
                        id="tc3",
                        name="document_reader",
                        arguments={"operation": "read_text", "file_path": "/tmp/results/out.txt"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Checking output 3",
                tool_calls=[
                    NativeToolCall(
                        id="tc4",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/results"},
                    )
                ],
            ),
        ]
    )

    async def _tool_executor(name: str, _params: dict):
        if name == "code_executor":
            return {"success": True, "produced_files": ["/tmp/results/de_genes.csv"]}
        return {"success": True, "summary": "ok"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations", "document_reader", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=5,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 34,
        },
    )

    result = asyncio.run(
        agent.think(
            "请执行任务 34",
            task_context=TaskExecutionContext(
                task_id=34,
                task_name="差异基因提取与分类",
                task_instruction="从差异表达结果中提取基因列表并分类。",
            ),
        )
    )

    # code_executor DID run, so it should not degrade into BLOCKED_DEPENDENCY
    assert "BLOCKED_DEPENDENCY" not in result.final_answer
    # The fallback should still clearly communicate that execution completed.
    assert "执行已完成" in result.final_answer or "finished execution" in result.final_answer


def test_unverified_terminal_session_does_not_set_real_execution_flag() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Peek at plan",
                tool_calls=[
                    NativeToolCall(
                        id="ts1",
                        name="terminal_session",
                        arguments={"operation": "write", "data": "cat plan_68.json\n"},
                    )
                ],
            ),
            NativeStreamResult(
                content="List outputs",
                tool_calls=[
                    NativeToolCall(
                        id="fo1",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/results"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Read summary",
                tool_calls=[
                    NativeToolCall(
                        id="dr1",
                        name="document_reader",
                        arguments={"operation": "read_text", "file_path": "/tmp/results/out.txt"},
                    )
                ],
            ),
            NativeStreamResult(
                content="List outputs again",
                tool_calls=[
                    NativeToolCall(
                        id="fo2",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/results"},
                    )
                ],
            ),
        ]
    )

    async def _tool_executor(name: str, _params: dict):
        if name == "terminal_session":
            return {
                "success": True,
                "operation": "write",
                "status": "completed",
                "verification_state": "not_attempted",
                "command_state": "unverified",
                "output": "cat plan_68.json",
            }
        return {"success": True, "summary": "ok"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["terminal_session", "file_operations", "document_reader"],
        tool_executor=_tool_executor,
        max_iterations=5,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 35,
        },
    )

    result = asyncio.run(
        agent.think(
            "请执行任务 35",
            task_context=TaskExecutionContext(
                task_id=35,
                task_name="基因 ID 转换与标准化",
                task_instruction="将基因 SYMBOL 转换为 ENTREZID。",
            ),
        )
    )

    assert "代码执行已完成" not in result.final_answer
    assert "finished execution" not in result.final_answer
    assert "BLOCKED_DEPENDENCY" in result.final_answer


def test_execute_task_failed_execution_cannot_finish_with_success_shaped_report() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Run the analysis code",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="code_executor",
                        arguments={"task": "analyze the dataset"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Inspect the workspace",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/phagescope"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Submit the polished report",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "数据分析已完成，并得出了关键生物学结论。",
                            "confidence": 0.94,
                        },
                    )
                ],
            ),
        ]
    )

    async def _tool_executor(name: str, _params: dict):
        if name == "code_executor":
            return {
                "success": False,
                "error": "Docker image not found: gagent-python-runtime:latest",
            }
        return {"success": True, "summary": "listed /tmp/phagescope"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=4,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
        },
    )

    result = asyncio.run(agent.think("继续分析这个数据"))

    assert "gagent-python-runtime:latest" in result.final_answer
    assert "不能把后续分析性表述视为已验证结论" in result.final_answer
    assert "数据分析已完成，并得出了关键生物学结论。" not in result.final_answer
    assert result.fallback_used is True


def test_execute_task_failed_execution_prefers_verified_profile_recovery() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Run the heavy execution path first",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="code_executor",
                        arguments={"task": "analyze the dataset"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Use a deterministic profile instead",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="result_interpreter",
                        arguments={
                            "operation": "profile",
                            "file_paths": ["/tmp/gvd.tsv", "/tmp/batch_test_phageids.txt"],
                        },
                    )
                ],
            ),
            NativeStreamResult(
                content="Submit the full narrative",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "完整分析已经完成，并验证了所有统计结论。",
                            "confidence": 0.91,
                        },
                    )
                ],
            ),
        ]
    )

    async def _tool_executor(name: str, _params: dict):
        if name == "code_executor":
            return {
                "success": False,
                "error": "Docker image not found: gagent-python-runtime:latest",
            }
        if name == "result_interpreter":
            return {
                "success": True,
                "operation": "profile",
                "task_type": "text_only",
                "profile_mode": "deterministic",
                "execution_status": "success",
                "execution_output": (
                    "Deterministic dataset profile (code-derived, no model synthesis):\n"
                    "- gvd.tsv: 2 rows x 3 columns\n"
                    "- batch_test_phageids.txt: 2 lookup IDs\n"
                    "- ID match batch_test_phageids.txt -> gvd.tsv (column Phage_ID): "
                    "1/2 matched, 1 missing"
                ),
                "profile": {
                    "summary": (
                        "Deterministic dataset profile (code-derived, no model synthesis):\n"
                        "- gvd.tsv: 2 rows x 3 columns\n"
                        "- batch_test_phageids.txt: 2 lookup IDs\n"
                        "- ID match batch_test_phageids.txt -> gvd.tsv (column Phage_ID): "
                        "1/2 matched, 1 missing"
                    )
                },
            }
        return {"success": True}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["code_executor", "result_interpreter"],
        tool_executor=_tool_executor,
        max_iterations=4,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
        },
    )

    result = asyncio.run(agent.think("继续分析这个数据"))

    assert "执行工具未成功完成" in result.final_answer
    assert "Deterministic dataset profile" in result.final_answer
    assert "1/2 matched, 1 missing" in result.final_answer
    assert "完整分析已经完成" not in result.final_answer
    assert result.fallback_used is True


def test_execute_task_filters_plan_operation_from_available_tools() -> None:
    agent = DeepThinkAgent(
        llm_client=_RecordingNativeLLM([]),
        available_tools=["plan_operation", "file_operations", "code_executor"],
        tool_executor=lambda *_args, **_kwargs: None,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 34,
        },
    )

    assert "plan_operation" not in agent.available_tools
    assert "code_executor" in agent.available_tools


def test_post_execution_probe_injects_summary_nudge() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Run task code",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="code_executor",
                        arguments={"task": "run task 34"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Check results directory",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/results"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Summarize outputs",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "已完成任务 34，并生成差异基因结果文件。",
                            "confidence": 0.92,
                        },
                    )
                ],
            ),
        ]
    )

    async def _tool_executor(name: str, _params: dict):
        if name == "code_executor":
            return {"success": True, "produced_files": ["/tmp/results/upregulated_genes.csv"]}
        return {"success": True, "summary": "listed"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["plan_operation", "file_operations", "result_interpreter", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=4,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 34,
        },
    )

    result = asyncio.run(
        agent.think(
            "请执行任务 34",
            task_context=TaskExecutionContext(
                task_id=34,
                task_name="差异基因提取与分类",
                task_instruction="从差异表达结果中提取基因列表并分类。",
            ),
        )
    )

    assert result.final_answer == "已完成任务 34，并生成差异基因结果文件。"
    assert len(llm.calls) >= 3
    third_call_messages = llm.calls[2]
    assert any(
        "任务代码已经执行过" in str(msg.get("content", ""))
        or "Task code has already executed" in str(msg.get("content", ""))
        for msg in third_call_messages
        if isinstance(msg, dict)
    )


def test_verified_execution_finalization_skips_exploratory_tool_calls() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Run task 40",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="code_executor",
                        arguments={"task": "run task 40"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Inspect the old directory first",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/old-results"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Finalize",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "已完成任务 40，并生成富集汇总结果。",
                            "confidence": 0.94,
                        },
                    )
                ],
            ),
        ]
    )

    executed_tools: list[str] = []

    async def _tool_executor(name: str, _params: dict):
        executed_tools.append(name)
        if name == "code_executor":
            return {
                "success": True,
                "verification_state": "verified_success",
                "produced_files": ["/tmp/results/go_summary.csv", "/tmp/results/kegg_summary.csv"],
            }
        return {"success": True, "summary": "listed"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations", "result_interpreter", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=4,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 40,
            "explicit_task_override": True,
            "pending_scope_task_ids": [],
        },
    )

    result = asyncio.run(
        agent.think(
            "请继续执行 task 40",
            task_context=TaskExecutionContext(
                task_id=40,
                task_name="PPI 网络构建与关键模块识别",
                task_instruction="执行 Task 40 并在完成后直接总结结果。",
                explicit_task_ids=[39, 40],
                explicit_task_override=True,
            ),
        )
    )

    assert result.final_answer == "已完成任务 40，并生成富集汇总结果。"
    assert executed_tools == ["code_executor"]
    assert len(llm.calls) >= 2
    second_call_messages = llm.calls[1]
    assert any(
        "当前绑定任务已经执行并验证通过" in str(msg.get("content", ""))
        or "already executed and passed verification" in str(msg.get("content", ""))
        for msg in second_call_messages
        if isinstance(msg, dict)
    )


def test_verified_execution_finalization_uses_task_outputs_when_verification_flag_missing() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Run task 41",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="code_executor",
                        arguments={"task": "run task 41"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Verify it again",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="verify_task",
                        arguments={"task_id": 41},
                    )
                ],
            ),
            NativeStreamResult(
                content="Finalize",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "已完成任务 41，并生成排序基因列表文件。",
                            "confidence": 0.91,
                        },
                    )
                ],
            ),
        ]
    )

    executed_tools: list[str] = []

    async def _tool_executor(name: str, _params: dict):
        executed_tools.append(name)
        if name == "code_executor":
            return {
                "success": True,
                "produced_files": ["/tmp/task41/results/gsea/ranked_gene_list.rnk"],
            }
        return {"success": False, "error": "verify should have been skipped"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["verify_task", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=4,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 41,
            "explicit_task_override": True,
            "pending_scope_task_ids": [],
        },
    )

    result = asyncio.run(
        agent.think(
            "请执行 task 41",
            task_context=TaskExecutionContext(
                task_id=41,
                task_name="基因排序列表构建",
                task_instruction="生成 ranked gene list 并在完成后直接总结。",
                explicit_task_ids=[41],
                explicit_task_override=True,
            ),
        )
    )

    assert result.final_answer == "已完成任务 41，并生成排序基因列表文件。"
    assert executed_tools == ["code_executor"]


def test_explicit_override_no_blocked_dependency_when_upstream_artifacts_missing() -> None:
    """When user explicitly requests a task but upstream artifact paths are empty,
    the system should force code_executor instead of returning BLOCKED_DEPENDENCY."""
    llm = _RecordingNativeLLM(
        [
            # Cycle 1: LLM probes with file_operations (observation-only)
            NativeStreamResult(
                content="Let me check available files",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/session/results"},
                    )
                ],
            ),
            # Cycle 2: LLM probes again (observation-only)
            NativeStreamResult(
                content="Let me check another directory",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/session/data"},
                    )
                ],
            ),
            # After forced code_executor, LLM submits final answer
            NativeStreamResult(
                content="Task completed",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "已执行任务 67，方法部分已生成。",
                            "confidence": 0.9,
                        },
                    )
                ],
            ),
        ]
    )

    executed_tools: list[str] = []

    async def _tool_executor(name: str, _params: dict):
        executed_tools.append(name)
        if name == "file_operations":
            return {"files": [], "status": "success"}
        if name == "code_executor":
            return {
                "status": "completed",
                "success": True,
                "produced_files": ["/tmp/session/manuscript/methods/cell_annotation.md"],
            }
        return {"success": True}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations", "code_executor", "submit_final_answer"],
        tool_executor=_tool_executor,
        max_iterations=8,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 67,
            "explicit_task_override": True,
            "pending_scope_task_ids": [],
        },
    )

    result = asyncio.run(
        agent.think(
            "重新执行任务31",
            task_context=TaskExecutionContext(
                task_id=67,
                task_name="细胞注释与聚类分析方法",
                task_instruction="撰写细胞注释与聚类分析方法部分",
                # No dependency_outputs — upstream artifacts are missing
                dependency_outputs=[],
                explicit_task_ids=[31],
                explicit_task_override=True,
            ),
        )
    )

    # Should NOT contain BLOCKED_DEPENDENCY
    assert "BLOCKED_DEPENDENCY" not in (result.final_answer or "")
    # code_executor should have been force-called
    assert "code_executor" in executed_tools


def test_execute_task_replaces_verify_only_cycle_with_rerun_task() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="先验证任务 66",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="verify_task",
                        arguments={"task_id": 66},
                    )
                ],
            ),
            NativeStreamResult(
                content="Finalize",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "已重新执行任务 66，并生成方法部分文件。",
                            "confidence": 0.92,
                        },
                    )
                ],
            ),
        ]
    )

    executed_tools: list[str] = []

    async def _tool_executor(name: str, _params: dict):
        executed_tools.append(name)
        if name == "rerun_task":
            return {
                "status": "completed",
                "success": True,
                "produced_files": ["/tmp/task66/manuscript/methods/data_preprocessing.md"],
            }
        return {"success": False, "error": "verify should have been replaced"}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["verify_task", "rerun_task"],
        tool_executor=_tool_executor,
        max_iterations=4,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 66,
            "explicit_task_override": True,
            "pending_scope_task_ids": [],
        },
    )

    result = asyncio.run(
        agent.think(
            "请继续执行 task 66",
            task_context=TaskExecutionContext(
                task_id=66,
                task_name="数据来源与预处理方法描述",
                task_instruction="重跑任务 66，重新生成方法部分文件。",
                explicit_task_ids=[66],
                explicit_task_override=True,
            ),
        )
    )

    assert result.final_answer == "已重新执行任务 66，并生成方法部分文件。"
    assert executed_tools == ["rerun_task"]


def test_task_handoff_after_execution_injects_execute_next_task_nudge() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Run task 39 first",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="code_executor",
                        arguments={"task": "run task 39"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Run task 40 now",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="code_executor",
                        arguments={"task": "run task 40"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Finish",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "已完成任务 40，并生成网络分析结果文件。",
                            "confidence": 0.93,
                        },
                    )
                ],
            ),
        ]
    )

    task_context = TaskExecutionContext(
        task_id=39,
        task_name="KEGG 通路富集分析执行",
        task_instruction="先执行 Task 39，再继续 Task 40。",
        explicit_task_ids=[39, 40],
        explicit_task_override=True,
    )

    call_count = 0

    async def _tool_executor(name: str, _params: dict):
        nonlocal call_count
        call_count += 1
        if name == "code_executor" and call_count == 1:
            agent.request_profile["current_task_id"] = 40
            task_context.task_id = 40
            task_context.task_name = "PPI 网络构建与关键模块识别"
            task_context.task_instruction = "使用 Task 39 产物继续执行 Task 40。"
            return {"success": True, "produced_files": ["/tmp/task39/kegg_enrichment_summary.txt"]}
        return {"success": True, "produced_files": ["/tmp/task40/network_metrics.csv"]}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations", "code_executor"],
        tool_executor=_tool_executor,
        max_iterations=4,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 39,
            "explicit_task_override": True,
        },
    )

    result = asyncio.run(agent.think("继续执行 Task 39，然后 Task 40", task_context=task_context))

    assert result.final_answer == "已完成任务 40，并生成网络分析结果文件。"
    assert len(llm.calls) >= 2
    second_call_messages = llm.calls[1]
    assert any(
        "当前绑定任务已自动推进到 Task 40" in str(msg.get("content", ""))
        or "has automatically advanced to Task 40" in str(msg.get("content", ""))
        for msg in second_call_messages
        if isinstance(msg, dict)
    )
    assert any(
        "Task ID=40" in str(msg.get("content", ""))
        for msg in second_call_messages
        if isinstance(msg, dict)
    )


def test_task_handoff_prefers_request_profile_when_task_context_is_stale() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Run task 56 first",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="code_executor",
                        arguments={"task": "run task 56"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Run task 26 now",
                tool_calls=[
                    NativeToolCall(
                        id="tc2",
                        name="code_executor",
                        arguments={"task": "run task 26"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Finish",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "已继续执行到任务 26，并生成差异通讯比较结果。",
                            "confidence": 0.92,
                        },
                    )
                ],
            ),
        ]
    )

    task_context = TaskExecutionContext(
        task_id=56,
        task_name="可视化结果整理与导出",
        task_instruction="先完成 Task 56，再继续 Task 26。",
        explicit_task_ids=[9],
        explicit_task_override=True,
    )
    executed_task_ids: list[int] = []

    async def _tool_executor(name: str, _params: dict):
        assert name == "code_executor"
        current_task_id = int(agent.request_profile.get("current_task_id") or 0)
        executed_task_ids.append(current_task_id)
        if current_task_id == 56:
            agent.request_profile["current_task_id"] = 26
            agent.request_profile["pending_scope_task_ids"] = []
            return {"success": True, "produced_files": ["/tmp/task56/visualization_summary.txt"]}
        return {"success": True, "produced_files": ["/tmp/task26/differential_communication.csv"]}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["code_executor"],
        tool_executor=_tool_executor,
        max_iterations=4,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 56,
            "pending_scope_task_ids": [26],
            "explicit_task_override": True,
        },
    )

    result = asyncio.run(agent.think("继续执行 Task 9", task_context=task_context))

    assert result.final_answer == "已继续执行到任务 26，并生成差异通讯比较结果。"
    assert executed_task_ids == [56, 26]
    second_call_messages = llm.calls[1]
    assert any(
        "Task ID=26" in str(msg.get("content", ""))
        for msg in second_call_messages
        if isinstance(msg, dict)
    )


def test_handoff_followthrough_forces_code_executor_after_no_tool_response() -> None:
    llm = _RecordingNativeLLM(
        [
            NativeStreamResult(
                content="Run task 45",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="code_executor",
                        arguments={"task": "run task 45"},
                    )
                ],
            ),
            NativeStreamResult(
                content="Need the user to provide Task 9 definition first.",
                tool_calls=[],
            ),
            NativeStreamResult(
                content="Finish",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={
                            "answer": "已继续执行到任务 46，并生成当前子任务输出。",
                            "confidence": 0.91,
                        },
                    )
                ],
            ),
        ]
    )

    task_context = TaskExecutionContext(
        task_id=45,
        task_name="Task 45",
        task_instruction="继续执行 Task 9 子任务链。",
        explicit_task_ids=[9],
        explicit_task_override=True,
    )
    executed_task_ids: list[int] = []

    async def _tool_executor(name: str, params: dict):
        assert name == "code_executor"
        _ = params
        task_id = task_context.task_id or 0
        executed_task_ids.append(task_id)
        if task_id == 45:
            agent.request_profile["current_task_id"] = 46
            task_context.task_id = 46
            task_context.task_name = "Task 46"
            task_context.task_instruction = "继续执行 Task 46。"
            return {"success": True, "produced_files": ["/tmp/task45/output.txt"]}
        return {"success": True, "produced_files": ["/tmp/task46/output.txt"]}

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["code_executor"],
        tool_executor=_tool_executor,
        max_iterations=3,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 45,
            "explicit_task_override": True,
        },
    )

    result = asyncio.run(agent.think("继续执行 Task 9", task_context=task_context))

    assert result.final_answer == "已继续执行到任务 46，并生成当前子任务输出。"
    assert executed_task_ids == [45, 46]
    assert result.fallback_used is False


def test_bound_execute_task_fallback_never_asks_for_missing_task_definition() -> None:
    llm = _NativeDummyLLM(
        [
            NativeStreamResult(
                content="Inspect task outputs first",
                tool_calls=[
                    NativeToolCall(
                        id="tc1",
                        name="file_operations",
                        arguments={"operation": "list", "path": "/tmp/task48"},
                    )
                ],
            ),
        ]
    )

    async def _tool_executor(_name: str, _params: dict):
        return {
            "success": True,
            "summary": "listed /tmp/task48",
            "produced_files": ["/tmp/task48/output.txt"],
        }

    async def _bad_fallback(*_args, **_kwargs):
        return "请提供 Task 9 的具体内容，并确认是否存在 plan68_task9 目录。"

    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations"],
        tool_executor=_tool_executor,
        max_iterations=1,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "current_task_id": 48,
            "explicit_task_override": True,
        },
    )
    agent._generate_fallback_from_evidence = _bad_fallback  # type: ignore[method-assign]

    result = asyncio.run(
        agent.think(
            "继续执行 Task 9",
            task_context=TaskExecutionContext(
                task_id=48,
                task_name="Task 48",
                task_instruction="继续执行 Task 48。",
                explicit_task_ids=[9],
                explicit_task_override=True,
            ),
        )
    )

    assert "请提供 Task 9" not in result.final_answer
    assert "plan68_task9" not in result.final_answer
    assert "当前绑定任务" in result.final_answer
