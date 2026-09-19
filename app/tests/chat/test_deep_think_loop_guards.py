"""Loop-guard tests: deliverable acceptance, failure trap, no-progress endgame.

The guards only fire on pathological patterns; a healthy run must see zero
behaviour change (scenario C asserts exactly that).

Deliverable fixtures live under ./runtime/ rather than pytest's tmp_path:
production code deliberately ignores /tmp/ paths as scratch.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from app.llm import NativeStreamResult, NativeToolCall
from app.services.deep_think_agent import DeepThinkAgent


SYNTH_ANSWER = "综合当前已收集的证据，本轮任务的关键结论如下：交付文件已生成并验证，可以直接使用。"

_SANDBOX = Path("runtime") / "test_loop_guards_sandbox"


async def _noop_tool_executor(_name: str, _params: dict):
    return {"success": True}


@pytest.fixture()
def deliverable_file():
    sandbox = _SANDBOX / "deliverables" / "latest" / "chart"
    sandbox.mkdir(parents=True, exist_ok=True)
    path = (sandbox / "overview.png").resolve()
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    yield path
    shutil.rmtree(_SANDBOX, ignore_errors=True)


class _LoopLLM:
    """Yields scripted native tool-call responses; records prompt messages."""

    def __init__(self, responses: list[NativeStreamResult]) -> None:
        self._responses = responses
        self._index = 0
        self.calls: list[list[dict]] = []
        self.chat_async_called = False

    async def stream_chat_with_tools_async(self, **kwargs):  # type: ignore[override]
        messages = kwargs.get("messages") or []
        self.calls.append(list(messages))
        if self._index >= len(self._responses):
            raise RuntimeError("No more scripted responses")
        value = self._responses[self._index]
        self._index += 1
        return value

    async def stream_chat_async(self, **kwargs):  # type: ignore[override]
        # synthesis path must stream (non-streaming gets 504'd by upstream)
        half = len(SYNTH_ANSWER) // 2
        yield SYNTH_ANSWER[:half]
        yield SYNTH_ANSWER[half:]

    async def chat_async(self, **kwargs):  # type: ignore[override]
        self.chat_async_called = True
        return SYNTH_ANSWER


def _tool_call_responses(tool: str, params: dict, count: int) -> list[NativeStreamResult]:
    return [
        NativeStreamResult(
            content=f"step {i}",
            tool_calls=[NativeToolCall(id=f"tc{i}", name=tool, arguments=dict(params))],
        )
        for i in range(count)
    ]


def _all_message_text(llm: _LoopLLM) -> str:
    parts: list[str] = []
    for messages in llm.calls:
        for msg in messages:
            parts.append(str(msg.get("content") or ""))
    return "\n".join(parts)


def test_no_progress_endgame_breaks_after_verified_deliverable(deliverable_file) -> None:
    """Deliverable verified at step 3, model never finishes -> nudge, then
    break far below max_iterations, then forced synthesis closes the run."""
    deliverable = deliverable_file

    call_count = {"n": 0}

    async def _executor(name: str, params: dict):
        call_count["n"] += 1
        if call_count["n"] <= 3:
            return {"success": True, "artifact_paths": [str(deliverable)]}
        return {"success": True, "summary": "still probing, nothing new"}

    llm = _LoopLLM(
        _tool_call_responses("file_operations", {"operation": "read", "path": "/x"}, 40)
    )
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations"],
        tool_executor=_executor,
        max_iterations=40,
        request_profile={"request_tier": "execute", "intent_type": "execute_task"},
    )

    result = asyncio.run(agent.think("create the overview figure"))

    assert result.final_answer == SYNTH_ANSWER
    # synthesis must go through streaming — non-streaming gets 504'd upstream
    assert not llm.chat_async_called
    # 3 producing steps + break streak (default 14) -> must stop << 40
    assert result.total_iterations <= 18
    text = _all_message_text(llm)
    assert "verified on disk" in text
    assert str(deliverable) in text
    # the early break reason is surfaced on the step for the UI
    assert any(
        step.self_correction and "without new deliverables" in step.self_correction
        for step in result.thinking_steps
    )


def test_failure_signature_trap_warns_at_three_and_breaks_at_five() -> None:
    """Same failure five times -> one warning message, then an early break."""
    async def _executor(name: str, params: dict):
        return {
            "success": False,
            "error": "target_task_not_atomic",
            "summary": "code_executor can only execute atomic tasks",
        }

    llm = _LoopLLM(
        _tool_call_responses("code_executor", {"task_id": 3, "code": "print(1)"}, 30)
    )
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["code_executor"],
        tool_executor=_executor,
        max_iterations=30,
        request_profile={"request_tier": "execute", "intent_type": "execute_task"},
    )

    result = asyncio.run(agent.think("run task 3"))

    # terminal tool errors route to the pre-existing blocked-tool answer,
    # which fires before forced synthesis — accept either close
    assert result.final_answer.strip()
    assert result.final_answer == SYNTH_ANSWER or "target_task_not_atomic" in result.final_answer
    # break at the 5th identical failure, not at the 30-iteration cap
    assert result.total_iterations <= 8
    # the warning is injected exactly once (it shows up in several recorded
    # snapshots only because message history accumulates across calls)
    per_snapshot_counts = [
        sum(
            1
            for msg in messages
            if "same failure has now occurred 3 times" in str(msg.get("content") or "")
        )
        for messages in llm.calls
    ]
    assert max(per_snapshot_counts) == 1
    assert sum(per_snapshot_counts) >= 1
    assert any(
        step.self_correction and "repetitions of the same failure" in step.self_correction
        for step in result.thinking_steps
    )


def test_healthy_run_sees_no_guard_nudges(deliverable_file) -> None:
    """Deliverable produced then submit_final_answer: zero guard interference."""
    deliverable = deliverable_file

    async def _executor(name: str, params: dict):
        return {"success": True, "artifact_paths": [str(deliverable)]}

    llm = _LoopLLM(
        [
            NativeStreamResult(
                content="make file",
                tool_calls=[
                    NativeToolCall(id="tc1", name="file_operations", arguments={"operation": "write"})
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
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations"],
        tool_executor=_executor,
        max_iterations=10,
        request_profile={"request_tier": "execute", "intent_type": "execute_task"},
    )

    result = asyncio.run(agent.think("create the figure"))

    assert result.final_answer == "done"
    assert result.total_iterations <= 3
    text = _all_message_text(llm)
    assert "same failure" not in text
    assert "without producing any deliverable" not in text
    assert "No new deliverable" not in text


def test_deliverable_display_names_exclude_inputs_and_logs() -> None:
    from app.services.deep_think_agent import _collect_deliverable_display_names

    evidence = (
        "- 文件读取 (数据体检.md)：数据源：uploads/37d08f2f56cd______.xlsx ，工作表 分析用数据\n"
        "- 已写入文件：results/profile/data_audit_and_research_directions.md (9748 B)\n"
        "- 终端输出：run_20260919_072353_960198_3718d9d9_replace_log.txt written\n"
    )
    names = _collect_deliverable_display_names(evidence)
    assert names == ["data_audit_and_research_directions.md"]


def test_humanizer_strips_embedded_cli_protocol_json() -> None:
    agent = DeepThinkAgent(
        llm_client=_LoopLLM([]),
        available_tools=["code_executor"],
        tool_executor=_noop_tool_executor,
    )
    noisy_stdout = (
        '[{"type":"system","subtype":"init","uuid":"5f0d4824-cd69-568b-8706-799f58187604",'
        '"session_id":"5f0d4824","cwd":"/app/runtime/session_x","tools":["computer_use_bring_to_front"]}]'
        "\n"
        "analysis finished, 30 distinct rows"
    )
    humanized = agent._humanize_single_tool_result(
        "code_executor",
        {"success": True, "stdout": noisy_stdout, "artifact_paths": ["/app/runtime/s/results/out.csv"]},
    )
    assert "subtype" not in humanized
    assert "analysis finished" in humanized


def test_time_budget_breaks_long_tool_runs(monkeypatch) -> None:
    """Nested CLI tool calls take minutes each; the endgame must bound the run
    by wall-clock time, not iteration count."""
    # env floors (60s/120s) protect production; patch the helpers directly
    import app.services.deep_think_agent as dta

    monkeypatch.setattr(dta, "_time_budget_nudge_seconds", lambda: 1)
    monkeypatch.setattr(dta, "_time_budget_break_seconds", lambda: 2)

    async def _executor(name: str, params: dict):
        await asyncio.sleep(0.5)
        return {"success": True, "summary": "heavy nested cli run"}

    llm = _LoopLLM(
        _tool_call_responses("code_executor", {"task_id": 3, "code": "print(1)"}, 30)
    )
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["code_executor"],
        tool_executor=_executor,
        max_iterations=30,
        request_profile={"request_tier": "execute", "intent_type": "execute_task"},
    )

    result = asyncio.run(agent.think("analyze the graph"))

    assert result.final_answer == SYNTH_ANSWER
    assert not llm.chat_async_called
    # 0.5s per cycle against a 2s budget: must break within a handful of cycles
    assert result.total_iterations <= 8
    text = _all_message_text(llm)
    assert "wall-clock time" in text
    assert any(
        step.self_correction and "wall-clock" in step.self_correction
        for step in result.thinking_steps
    )
