"""A successful execute_code cell recovers a failed tool call; a failing cell is not "the main tool failed".

The answer gets "the main execution tool failed" when the last failed execution step is not followed by a
trusted success. Code mode's ``execute_code`` was not among the tools that count, so a run where bio_tools
refused a parameter and the model then computed the answer in a cell still ended with the warning, five times
in the evaluation rounds, each time with a correct answer.
"""

from __future__ import annotations

import json

import pytest

from app.services.deep_think.gating_truth import _collect_execute_truth_events
from app.services.deep_think.models import ThinkingStep
from app.services.deep_think_agent import DeepThinkAgent
from app.tests.chat.test_deep_think_loop_guards import _LoopLLM, _noop_tool_executor

BIO_REFUSED = {
    "success": False,
    "tool": "bio_tools",
    "result": {
        "tool": "bio_tools",
        "success": False,
        "operation": "view",
        "error_code": "invalid_parameter",
        "error": "Invalid/unsafe parameter: count",
    },
}


def _step(iteration, tool, params, payload):
    return ThinkingStep(
        iteration=iteration,
        thought="",
        action=json.dumps({"tools": [{"tool": tool, "params": params}]}),
        action_result=f"[{tool}] {json.dumps(payload)}",
        self_correction=None,
    )


def _cell(output="42", success=True, **extra):
    payload = {"success": success, "tool": "execute_code", "result": {"success": success, "output": output, **extra}}
    return _step(2, "execute_code", {"code": "print(42)"}, payload)


def _agent():
    return DeepThinkAgent(
        llm_client=_LoopLLM([]),
        available_tools=["bio_tools", "execute_code"],
        tool_executor=_noop_tool_executor,
        max_iterations=1,
        request_profile={"session_id": "s", "request_tier": "execute", "intent_type": "execute_task"},
    )


ANSWER = ("Mapped reads: 240 (counted with pysam). " + "The count excludes the 60 unmapped records. " * 6).strip()


def _barrier(steps, answer=ANSWER):
    return _agent()._apply_execute_failure_truth_barrier(answer, user_query="how many reads are mapped?", steps=steps)


REFUSED = _step(1, "bio_tools", {"tool_name": "samtools", "operation": "view"}, BIO_REFUSED)


def test_a_successful_cell_after_a_refused_tool_call_clears_the_warning():
    assert _barrier([REFUSED, _cell("240")]) == ANSWER


def test_a_cell_that_wrote_a_file_counts_even_without_output():
    assert _barrier([REFUSED, _cell("", produced_files=["results/out.tsv"])]) == ANSWER


def test_a_cell_that_printed_and_wrote_nothing_does_not_count():
    answer = _barrier([REFUSED, _cell("")])
    assert "Warning" in answer or "注意" in answer


def test_without_any_recovery_the_warning_stays():
    answer = _barrier([REFUSED])
    assert "main execution tool failed" in answer or "主执行工具未成功" in answer


def test_a_failing_cell_alone_is_not_a_failed_main_tool():
    failing = _cell("Traceback...", success=False)
    assert _barrier([failing]) == ANSWER


def test_a_failing_cell_after_a_success_does_not_bring_the_warning_back():
    ok = _step(1, "execute_code", {"code": "print(240)"}, {"success": True, "tool": "execute_code", "result": {"success": True, "output": "240"}})
    failing = _step(2, "execute_code", {"code": "boom"}, {"success": False, "tool": "execute_code", "result": {"success": False, "error": "NameError"}})
    assert _barrier([REFUSED, ok, failing]) == ANSWER


def test_a_cell_before_the_failure_does_not_recover_it():
    before = _step(1, "execute_code", {"code": "print(1)"}, {"success": True, "tool": "execute_code", "result": {"success": True, "output": "1"}})
    crashed = {"success": False, "tool": "bio_tools", "result": {"success": False, "tool": "bio_tools", "error": "samtools.view failed"}}
    failed = _step(2, "bio_tools", {"tool_name": "samtools", "operation": "view"}, crashed)
    answer = _barrier([before, failed])
    assert "main execution tool failed" in answer or "主执行工具未成功" in answer


def test_cells_are_events_but_never_failures():
    events = _collect_execute_truth_events(DeepThinkAgent, [REFUSED, _cell("240"), _cell("x", success=False)])
    assert [(e["tool"], e["success"], e["trusted"], e["counts_as_failure"]) for e in events] == [
        ("bio_tools", False, False, True),
        ("execute_code", True, True, False),
        ("execute_code", False, False, False),
    ]


# --------------------------------------------------------------------------- a refusal after verified work

def _ok_bio(iteration, operation):
    payload = {"success": True, "tool": "bio_tools", "result": {"success": True, "tool": "bio_tools", "operation": operation}}
    return _step(iteration, "bio_tools", {"tool_name": "samtools", "operation": operation}, payload)


def test_a_refused_call_after_verified_work_does_not_turn_the_answer_into_unverified():
    # S09 on p16: sort, index, flagstat and count all succeeded; a last extra call used an undeclared parameter.
    steps = [_ok_bio(1, "sort"), _ok_bio(2, "flagstat"), _step(3, "bio_tools", {"tool_name": "samtools", "operation": "view"}, BIO_REFUSED)]
    assert _barrier(steps) == ANSWER


def test_a_refusal_with_no_verified_work_still_warns():
    answer = _barrier([REFUSED])
    assert "main execution tool failed" in answer or "主执行工具未成功" in answer


@pytest.mark.parametrize("code", ["invalid_parameter", "missing_required_params", "missing_input_file", "path_not_allowed"])
def test_every_refusal_code_is_forgiven_after_verified_work(code):
    refusal = dict(BIO_REFUSED, result=dict(BIO_REFUSED["result"], error_code=code))
    steps = [_ok_bio(1, "sort"), _step(2, "bio_tools", {"tool_name": "samtools", "operation": "view"}, refusal)]
    assert _barrier(steps) == ANSWER


def test_a_real_failure_after_verified_work_still_warns():
    crashed = {"success": False, "tool": "bio_tools", "result": {"success": False, "tool": "bio_tools", "error": "samtools.sort failed"}}
    steps = [_ok_bio(1, "sort"), _step(2, "bio_tools", {"tool_name": "samtools", "operation": "sort"}, crashed)]
    answer = _barrier(steps)
    assert "main execution tool failed" in answer or "主执行工具未成功" in answer


def test_events_carry_the_error_code():
    events = _collect_execute_truth_events(DeepThinkAgent, [REFUSED])
    assert events[0]["error_code"] == "invalid_parameter"
