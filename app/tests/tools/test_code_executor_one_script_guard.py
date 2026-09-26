"""One-script delegation guard: refuse the small stuff once, then step aside.

Offer-side copy alone did not stop the model delegating one-script work
(measured 2026-09-26 on the 28-task arms: the same task ran 25-80s through
`execute_code` and 100-900s through a delegation, which starts a whole coding
agent first). This guard closes the gap, with two boundaries so it stays a
nudge rather than a wall: plan-bound executions are never refused, and the same
ask repeated in the same session is let through.

The task texts below are the real openings of the delegations recorded in the
run logs (`_code_executor.log`), lengths included, so the classifier is
calibrated against production phrasing rather than invented examples.
"""

from __future__ import annotations

import pytest

from app.services.interpreter.runtime_guardrails import (
    ONE_SCRIPT_REFUSAL_MESSAGE,
    ONE_SCRIPT_GUARD_ENV,
    looks_like_one_script_delegation,
    should_refuse_one_script_delegation,
)

# --- flagged: one short script answers these (real run-log phrasings) --------
_ONE_SCRIPT_TASKS = (
    # sample28_split/t01 (532 chars in the log)
    "Read the CSV file at /app/data/evals/sample28_split/_work/t01_read_csv_rows/"
    "runtime/session_benchT01_5f2a/uploads/orders.csv (columns: order_id, "
    "product, amount, quantity) and report the row count, the total amount and "
    "the average amount. Print every row so I can check it.",
    # sample28_split/t20
    "Create a vertical bar chart with matplotlib and save it to an exact path.",
    # sample28_split/t22
    "Create a publication-quality pie chart of market share and save it as a PNG.",
    # sample28_split/t16
    "Verify a top-3-per-group CSV against the source data using pandas.",
    # sample28_split/t17
    "Read these two CSVs with pandas: orders.csv and customers.csv.",
    # streamfix2_split/t26
    "Create and RUN a Python script that produces a 2x2 multi-panel matplotlib "
    "figure with Chinese subplot titles and save it as a PNG.",
    # streamfix_split/t09 (Chinese, 523 chars in the log)
    "读取 CSV 文件 /app/data/evals/streamfix_split/_work/t09_groupby_agg/runtime/"
    "session_benchT09_694353/uploads/orders2.csv（列名：订单号, 类别, 金额）。"
    "按“类别”分组汇总，生成汇总表并保存为 results/category_summary.csv，"
    "并按总金额从高到低排序后打印全文。",
    # streamfix2_split/t35
    "Analyze the 2025 daily sales dataset at: /app/data/evals/x/uploads/daily.csv",
)


@pytest.mark.parametrize("task", _ONE_SCRIPT_TASKS)
def test_one_script_tasks_are_flagged(task: str) -> None:
    assert looks_like_one_script_delegation(task), f"missed: {task[:60]}"


# --- not flagged: these should reach the coding agent ------------------------
_AGENT_TASKS = (
    "Refactor the parser module and add unit tests.",
    "Install pyarrow and rerun the failing pipeline end to end.",
    "Debug the traceback in task_code.py and fix the failing step.",
    "读取 orders.csv 并按类别汇总，然后把这个流程封装成可复用模块并安装依赖",
    # A code dump is out of scope: running someone else's script may really want
    # the isolated execution image.
    "Execute the following Python code:\n```python\nimport pandas as pd\nprint(pd.read_csv('x.csv').shape)\n```",
)


@pytest.mark.parametrize("task", _AGENT_TASKS)
def test_agent_shaped_tasks_are_not_flagged(task: str) -> None:
    assert not looks_like_one_script_delegation(task), f"false positive: {task[:60]}"


def test_empty_task_is_not_flagged() -> None:
    assert not looks_like_one_script_delegation("")
    assert not looks_like_one_script_delegation(None)


def test_a_long_spec_is_left_alone() -> None:
    """The length ceiling is calibrated on run-log texts (longest trivial: 790)."""
    long_task = (
        "Read the CSV at /app/data/uploads/sensor.csv and report how many rows "
        "it has, the total, and the average per column. "
    ) * 12
    assert len(long_task) > 900
    assert not looks_like_one_script_delegation(long_task)


# --- policy boundaries -------------------------------------------------------
def test_refused_only_when_the_model_chose_it_freely() -> None:
    task = "Count the rows in orders.csv and print the total."

    assert should_refuse_one_script_delegation(
        task, require_task_context=False, session_key="sess-a"
    )
    # Plan-bound: the plan decided to run code here, not the model.
    assert not should_refuse_one_script_delegation(
        task, require_task_context=True, session_key="sess-a"
    )


def test_the_same_ask_is_allowed_through_on_the_second_attempt() -> None:
    task = "Count the rows in orders.csv and print the total."

    assert should_refuse_one_script_delegation(
        task, require_task_context=False, session_key="sess-memory-1"
    )
    assert not should_refuse_one_script_delegation(
        task, require_task_context=False, session_key="sess-memory-1"
    )
    # A different session gets its own single refusal.
    assert should_refuse_one_script_delegation(
        task, require_task_context=False, session_key="sess-memory-2"
    )


def test_env_kill_switch_disables_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ONE_SCRIPT_GUARD_ENV, "0")
    assert not should_refuse_one_script_delegation(
        "Count the rows in orders.csv and print the total.",
        require_task_context=False,
        session_key="sess-off",
    )


def test_agent_shaped_task_never_refused() -> None:
    assert not should_refuse_one_script_delegation(
        "Debug the traceback and fix the failing step.",
        require_task_context=False,
        session_key="sess-b",
    )


async def test_handler_refuses_with_actionable_guidance() -> None:
    from tool_box.tools_impl.code_executor import code_executor_handler

    result = await code_executor_handler(
        task="Count the rows in orders.csv and print the total.",
        require_task_context=False,
        session_id="sess-handler-guard",
    )

    assert result["success"] is False
    assert result["blocked_reason"] == "delegation_too_small"
    assert result["blocked_by_delegation_size_guardrail"] is True
    assert result["error"] == ONE_SCRIPT_REFUSAL_MESSAGE
    assert "DELEGATION_TOO_SMALL" in result["error"]
    assert "execute_code" in result["error"]
    assert result["alternatives"][0] == "execute_code"
    # No lane ran: nothing that identifies an execution was produced.
    assert "execution_lane" not in result


def test_a_refusal_is_not_an_execution_failure() -> None:
    """The truth barrier must not read a policy nudge as "the tool failed"."""
    import json

    from app.services.deep_think.gating_truth import _collect_execute_truth_events
    from app.services.deep_think.models import ThinkingStep
    from app.services.deep_think_agent import DeepThinkAgent

    step = ThinkingStep(
        iteration=1,
        thought="",
        action='{"tool":"code_executor","params":{"task":"count rows"}}',
        action_result=json.dumps(
            {
                "success": False,
                "error": ONE_SCRIPT_REFUSAL_MESSAGE,
                "blocked_reason": "delegation_too_small",
                "blocked_by_delegation_size_guardrail": True,
            }
        ),
        self_correction=None,
    )

    assert _collect_execute_truth_events(DeepThinkAgent, [step]) == []


def test_a_real_execution_failure_still_registers() -> None:
    """Guard the guard: the exclusion is keyed on the refusal, not on failure."""
    import json

    from app.services.deep_think.gating_truth import _collect_execute_truth_events
    from app.services.deep_think.models import ThinkingStep
    from app.services.deep_think_agent import DeepThinkAgent

    step = ThinkingStep(
        iteration=1,
        thought="",
        action='{"tool":"code_executor","params":{"task":"count rows"}}',
        action_result=json.dumps({"success": False, "error": "boom"}),
        self_correction=None,
    )

    events = _collect_execute_truth_events(DeepThinkAgent, [step])
    assert len(events) == 1
    assert events[0]["kind"] == "execution"
    assert events[0]["success"] is False
