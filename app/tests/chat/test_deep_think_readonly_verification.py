"""Read-only verification delegation guard ("牛刀核验").

Behaviour spec for the gating_probe detector and the _native_real_execution_cycle
intercept: first hit redirects to cheap readers/kernel, repeated hits go through
verified-execution finalization, and the counter resets on genuine cycles.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from app.services.deep_think.gating_probe import (
    _build_readonly_verification_redirect_nudge,
    _cycle_is_readonly_verification,
    _is_readonly_verification_task_text,
)
from app.services.deep_think.controller import _NativeCycleState, _native_real_execution_cycle
from app.services.deep_think.models import TaskExecutionContext, ThinkingStep
from app.services.deep_think_agent import DeepThinkAgent


class _NoopLLM:
    async def stream_chat_with_tools_async(self, **kwargs):  # pragma: no cover
        raise AssertionError("not used")


async def _noop_tool_executor(_name: str, _params: dict):
    return {"success": True}


def _agent() -> DeepThinkAgent:
    return DeepThinkAgent(
        llm_client=_NoopLLM(),
        available_tools=["code_executor"],
        tool_executor=_noop_tool_executor,
        max_iterations=5,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "explicit_task_override": True,
            "current_task_id": 5,
        },
    )


def _task_context() -> TaskExecutionContext:
    return TaskExecutionContext(
        task_id=5,
        task_name="T5",
        task_instruction="execute task 5",
        explicit_task_override=True,
    )


def _step() -> ThinkingStep:
    return ThinkingStep(
        iteration=1,
        thought="",
        action=None,
        action_result=None,
        self_correction=None,
        timestamp=datetime.now(),
        status="thinking",
    )


def _ce_result(task_text: str, tool_result: dict | None = None) -> dict:
    return {
        "tool_name": "code_executor",
        "tool_params": {"task": task_text},
        "tool_result": tool_result if tool_result is not None else {"success": True},
    }


class TestReadonlyVerificationDetector:
    def test_chinese_readonly_audit_hits(self) -> None:
        assert _is_readonly_verification_task_text(
            "只读审计 plan7 的任务产出目录，核验报告数据是否完整，不修改任何文件"
        )
        assert _is_readonly_verification_task_text("只读核验，未命中写 NOT FOUND")

    def test_english_readonly_verification_hits(self) -> None:
        assert _is_readonly_verification_task_text(
            "Read-only audit of the generated CSVs; verify the totals; do not modify any file"
        )
        assert _is_readonly_verification_task_text(
            "verification-only check of outputs, no changes"
        )

    def test_production_signal_disqualifies(self) -> None:
        # 核验后再产出：产出信号压过只读措辞
        assert not _is_readonly_verification_task_text("核验数据后生成修正版报告并保存")
        # 先只读核验、随后写入修复文件：有写入产出
        assert not _is_readonly_verification_task_text("只读代码审查并修复发现的问题写入修复文件")
        # verify + update in English
        assert not _is_readonly_verification_task_text(
            "read-only verification first, then update the summary report"
        )

    def test_missing_intent_families_do_not_hit(self) -> None:
        assert not _is_readonly_verification_task_text("生成销售汇总图并保存为 PNG")
        assert not _is_readonly_verification_task_text("只读模式")
        assert not _is_readonly_verification_task_text("")

    def test_full_dump_reprint_hits_without_readonly_marker(self) -> None:
        # 生产实证漏网形态："Print the FULL stdout text of the previously gen..."
        assert _is_readonly_verification_task_text(
            "Print the FULL stdout text of the previously generated report"
        )
        assert _is_readonly_verification_task_text("全量打印刚生成的检索报告内容")
        assert _is_readonly_verification_task_text("dump the entire audit output")

    def test_full_dump_with_production_signal_does_not_hit(self) -> None:
        assert not _is_readonly_verification_task_text(
            "Print the FULL stdout and save it to out.txt"
        )
        assert not _is_readonly_verification_task_text("全量打印并保存修正版")
        # 非全量普通打印不命中
        assert not _is_readonly_verification_task_text("print the summary table")

    def test_cycle_level_requires_all_readonly_code_executor(self) -> None:
        assert _cycle_is_readonly_verification([_ce_result("只读核验，不修改文件")])
        assert not _cycle_is_readonly_verification(
            [_ce_result("只读核验，不修改文件"), _ce_result("生成报告并保存")]
        )
        assert not _cycle_is_readonly_verification(
            [_ce_result("只读核验，不修改文件"), {"tool_name": "document_reader", "tool_params": {}}]
        )
        assert not _cycle_is_readonly_verification([])


class TestReadonlyVerificationNudge:
    def test_first_hit_nudge_redirects_without_convergence_line(self) -> None:
        text = _build_readonly_verification_redirect_nudge(_agent(), user_query="帮我核验", count=1)
        assert "code_executor" in text
        assert "document_reader" in text and "file_operations" in text and "execute_code" in text
        assert "直接给结论" not in text

    def test_repeat_hit_nudge_adds_prose_convergence(self) -> None:
        text = _build_readonly_verification_redirect_nudge(_agent(), user_query="帮我核验", count=2)
        assert "直接给结论" in text
        assert "不要复述已读内容" in text
        assert "不要全量打印" in text


class TestReadonlyVerificationCycleGuard:
    def _run(self, tool_results, cycle) -> tuple[str, list[dict], ThinkingStep]:
        agent = _agent()
        step = _step()
        messages: list[dict] = []
        flow = asyncio.run(
            _native_real_execution_cycle(
                agent,
                tool_results=tool_results,
                iteration=1,
                current_step=step,
                messages=messages,
                task_context=_task_context(),
                user_query="execute task 5",
                cycle=cycle,
            )
        )
        return flow, messages, step

    def test_first_hit_redirects_without_finalization(self) -> None:
        cycle = _NativeCycleState()
        flow, messages, step = self._run(
            [_ce_result("只读审计产出目录，核验数据完整性，不修改文件")], cycle
        )
        assert flow == "ok"
        assert cycle.readonly_verification_cycles == 1
        assert cycle.force_verified_execution_finalization is False
        assert cycle.had_real_execution_tool is False
        assert any("document_reader" in str(m.get("content") or "") for m in messages)
        assert "code_executor" in (step.self_correction or "")

    def test_second_hit_with_verified_evidence_forces_finalization(self) -> None:
        cycle = _NativeCycleState()
        cycle.readonly_verification_cycles = 1
        flow, messages, step = self._run(
            [
                _ce_result(
                    "只读核验，未命中写 NOT FOUND",
                    tool_result={"success": True, "verification_state": "verified_success"},
                )
            ],
            cycle,
        )
        assert flow == "ok"
        assert cycle.readonly_verification_cycles == 2
        assert cycle.force_verified_execution_finalization is True
        assert cycle.had_real_execution_tool is False
        assert any("submit_final_answer" in str(m.get("content") or "") for m in messages)

    def test_second_hit_without_evidence_redirects_again_with_convergence(self) -> None:
        cycle = _NativeCycleState()
        cycle.readonly_verification_cycles = 1
        flow, messages, _ = self._run([_ce_result("只读核验，不修改文件")], cycle)
        assert flow == "ok"
        assert cycle.force_verified_execution_finalization is False
        # user_query is English in the cycle harness -> English convergence text
        assert any("directly" in str(m.get("content") or "") for m in messages)
        assert any("do not restate" in str(m.get("content") or "") for m in messages)

    def test_counter_resets_on_genuine_cycle(self) -> None:
        cycle = _NativeCycleState()
        cycle.readonly_verification_cycles = 2
        flow, _, _ = self._run([_ce_result("生成销售汇总报告并保存为 markdown")], cycle)
        assert flow == "ok"
        assert cycle.readonly_verification_cycles == 0
