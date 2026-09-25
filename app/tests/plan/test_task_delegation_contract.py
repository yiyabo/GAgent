"""S1 unified delegation contract: plan-bound and standalone shapes.

The plan-bound golden text is the pre-refactor output byte for byte; the
standalone shape is the new first-class usage (no plan context).
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

import tool_box
from app.services.execution.tool_executor import ToolExecutionContext, UnifiedToolExecutor
from app.services.plans.task_delegate_executor import CodeAgentTaskDelegateExecutor
from app.services.plans.task_delegate_executor import TaskDelegationSpec
from tool_box.context import ToolContext
from tool_box.tools_impl.code_executor import _validate_scope_contract

_GOLDEN_PLAN_BOUND_PROMPT = (
    "You are executing one atomic plan task delegated by the orchestration system.\n"
    "Complete only this task; do not create or modify the plan.\n"
    "The orchestration system, not you, decides final task completion after deterministic verification.\n"
    "If inputs are missing, report BLOCKED_DEPENDENCY with a concise DETAIL.\n"
    "Return the strict final response schema requested by the execution runtime.\n"
    "Do not claim that internal Phage-Agent tools were called; this delegate only has the external code-agent runtime tools.\n"
    "\n"
    "=== PLAN TASK ===\n"
    "Plan ID: 106\n"
    "Task ID: 1\n"
    "Task Name: JSON contract\n"
    "\n"
    "Task prompt body\n"
    "\n"
    "=== RESOLVED INPUT ARTIFACTS ===\n"
    "- input: /tmp/input.txt\n"
    "\n"
    "=== ACCEPTANCE CRITERIA ===\n"
    "{\n"
    '  "blocking": true,\n'
    '  "checks": [\n'
    "    {\n"
    '      "path": "answer.txt",\n'
    '      "type": "file_nonempty"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "=== ARTIFACT CONTRACT ===\n"
    "{\n"
    '  "publishes": [\n'
    '    "answer"\n'
    "  ]\n"
    "}"
)

_STANDALONE_PREAMBLE = (
    "You are executing one atomic task delegated by the orchestration system.\n"
    "Complete only this task; do not expand its scope or take on other work.\n"
    "The orchestration system, not you, decides final task completion after deterministic verification.\n"
    "If inputs are missing, report BLOCKED_DEPENDENCY with a concise DETAIL.\n"
    "Return the strict final response schema requested by the execution runtime.\n"
    "Do not claim that internal Phage-Agent tools were called; this delegate only has the external code-agent runtime tools."
)

_COMPLETED_PAYLOAD: dict[str, object] = {  # UnifiedToolExecutor.execute_sync shape
    "success": True,
    "result": {
        "success": True,
        "result": "Created results/answer.txt with the delegated sub-agent output.",
        "run_id": "run-standalone-1",
        "artifact_paths": ["/tmp/answer.txt"],
        "produced_files": ["/tmp/answer.txt"],
        "execution_backend": "qwen_code",
    },
}


class _ToolExecutorStub:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload: dict[str, object] = payload
        self.calls: list[tuple[str, dict[str, object], ToolExecutionContext | None]] = []

    def execute_sync(
        self,
        tool_name: str,
        params: dict[str, object],
        *,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, object]:
        self.calls.append((tool_name, params, context))
        return self.payload


def _make_executor(stub: _ToolExecutorStub) -> CodeAgentTaskDelegateExecutor:
    return CodeAgentTaskDelegateExecutor(tool_executor=cast(UnifiedToolExecutor, cast(object, stub)))


def _plan_bound_spec() -> TaskDelegationSpec:
    return TaskDelegationSpec(
        task_name="JSON contract",
        task_instruction="Create answer.txt",
        task_prompt="Task prompt body",
        executor_backend="local",
        plan_id=106,
        task_id=1,
        artifact_contract={"publishes": ["answer"]},
        acceptance_criteria={
            "blocking": True,
            "checks": [{"type": "file_nonempty", "path": "answer.txt"}],
        },
        resolved_input_artifacts={"input": "/tmp/input.txt"},
    )


def _standalone_spec() -> TaskDelegationSpec:
    return TaskDelegationSpec(
        task_name="Standalone literature scan",
        task_instruction="Scan the literature for X",
        task_prompt="Task prompt body",
        executor_backend="qwen_code",
        session_id="session-standalone",
    )


def test_plan_bound_prompt_is_byte_identical_to_pre_refactor_golden() -> None:
    spec = _plan_bound_spec()

    prompt = CodeAgentTaskDelegateExecutor()._build_delegate_prompt(spec)

    assert prompt == _GOLDEN_PLAN_BOUND_PROMPT


def test_plan_bound_prompt_without_optional_blocks_keeps_label_block() -> None:
    spec = TaskDelegationSpec(
        task_name="Minimal task",
        task_instruction="Do the thing",
        task_prompt="Body",
        executor_backend="local",
        plan_id=42,
        task_id=7,
    )

    prompt = CodeAgentTaskDelegateExecutor()._build_delegate_prompt(spec)

    assert prompt.startswith(
        "You are executing one atomic plan task delegated by the orchestration system.\n"
        "Complete only this task; do not create or modify the plan.\n"
        "The orchestration system, not you, decides final task completion after deterministic verification.\n"
        "If inputs are missing, report BLOCKED_DEPENDENCY with a concise DETAIL.\n"
        "Return the strict final response schema requested by the execution runtime.\n"
        "Do not claim that internal Phage-Agent tools were called; this delegate only has the external code-agent runtime tools.\n"
        "\n"
        "=== PLAN TASK ===\n"
        "Plan ID: 42\n"
        "Task ID: 7\n"
        "Task Name: Minimal task\n"
        "\n"
        "Body"
    )
    assert prompt.endswith("Task Name: Minimal task\n\nBody")


def test_standalone_prompt_drops_plan_labels_and_keeps_boundaries() -> None:
    prompt = CodeAgentTaskDelegateExecutor()._build_delegate_prompt(_standalone_spec())

    assert prompt == (
        f"{_STANDALONE_PREAMBLE}\n"
        "\n"
        "=== DELEGATED TASK ===\n"
        "Task Name: Standalone literature scan\n"
        "\n"
        "Task prompt body"
    )
    assert "None" not in prompt
    assert "Plan ID:" not in prompt
    assert "Task ID:" not in prompt
    assert "=== PLAN TASK ===" not in prompt
    assert "Complete only this task;" in prompt
    assert (
        "The orchestration system, not you, decides final task completion after deterministic verification." in prompt
    )
    assert "If inputs are missing, report BLOCKED_DEPENDENCY with a concise DETAIL." in prompt


def test_standalone_prompt_renders_optional_blocks() -> None:
    spec = TaskDelegationSpec(
        task_name="Standalone scan",
        task_instruction="Scan",
        task_prompt="Body",
        executor_backend="qwen_code",
        artifact_contract={"publishes": ["report"]},
        acceptance_criteria={"checks": [{"type": "file_exists", "path": "report.md"}]},
        resolved_input_artifacts={"paper": "/tmp/paper.pdf"},
    )

    prompt = CodeAgentTaskDelegateExecutor()._build_delegate_prompt(spec)

    assert "=== RESOLVED INPUT ARTIFACTS ===\n- paper: /tmp/paper.pdf" in prompt
    assert "=== ACCEPTANCE CRITERIA ===" in prompt
    assert "=== ARTIFACT CONTRACT ===" in prompt
    assert '"publishes": [' in prompt


def test_standalone_delegation_executes_without_plan_context() -> None:
    stub = _ToolExecutorStub(_COMPLETED_PAYLOAD)
    spec = _standalone_spec()

    result = _make_executor(stub).execute(spec)

    assert len(stub.calls) == 1
    tool_name, params, context = stub.calls[0]
    assert tool_name == "code_executor"
    assert context is not None
    assert context.plan_id is None
    assert context.task_id is None
    assert context.task_name == "Standalone literature scan"
    assert context.task_instruction == "Scan the literature for X"
    assert context.session_id == "session-standalone"
    assert context.channel == "chat"
    assert context.mode == "delegated_task_execution"
    assert params["task"] == CodeAgentTaskDelegateExecutor()._build_delegate_prompt(spec)
    assert params["execution_backend"] == "qwen_code"
    assert params["output_format"] == "json"
    assert params["auto_fix"] is True
    assert params["skip_permissions"] is True
    assert params["resolved_resources"] == {}
    assert "add_dirs" not in params
    assert result.status == "completed"
    assert result.summary == "Created results/answer.txt with the delegated sub-agent output."
    assert result.artifact_paths == ["/tmp/answer.txt"]
    assert result.executor == "qwen_code"
    assert result.executor_session_id == "run-standalone-1"


def test_plan_bound_delegation_context_is_unchanged() -> None:
    stub = _ToolExecutorStub(_COMPLETED_PAYLOAD)

    _make_executor(stub).execute(_plan_bound_spec())

    _, params, context = stub.calls[0]
    assert context is not None
    assert context.plan_id == 106
    assert context.task_id == 1
    assert context.channel == "plan_executor"
    assert context.mode == "delegated_task_execution"
    assert "add_dirs" not in params


def test_delegation_result_shape_is_identical_in_both_shapes() -> None:
    standalone_result = _make_executor(_ToolExecutorStub(_COMPLETED_PAYLOAD)).execute(
        replace(_standalone_spec(), executor_backend="local")
    )
    plan_bound_result = _make_executor(_ToolExecutorStub(_COMPLETED_PAYLOAD)).execute(
        replace(_plan_bound_spec(), executor_backend="local")
    )

    assert standalone_result.status == plan_bound_result.status == "completed"
    assert standalone_result.summary == plan_bound_result.summary
    assert standalone_result.artifact_paths == plan_bound_result.artifact_paths == ["/tmp/answer.txt"]
    assert standalone_result.stdout == plan_bound_result.stdout == ""
    assert standalone_result.stderr == plan_bound_result.stderr == ""
    assert standalone_result.metadata == plan_bound_result.metadata
    assert standalone_result.metadata["delegated_task_execution"] is True


def test_standalone_delegation_failure_shape_is_preserved() -> None:
    payload: dict[str, object] = {
        "success": False,
        "result": {
            "success": False,
            "error_summary": "Sub-agent exited with code 1 before producing artifacts.",
            "artifact_paths": [],
            "run_id": "run-standalone-2",
        },
    }

    result = _make_executor(_ToolExecutorStub(payload)).execute(_standalone_spec())

    assert result.status == "failed"
    assert result.summary == "Sub-agent exited with code 1 before producing artifacts."
    assert result.artifact_paths == []


def test_standalone_delegation_context_clears_the_atomic_scope_guardrail() -> None:
    stub = _ToolExecutorStub(_COMPLETED_PAYLOAD)
    _make_executor(stub).execute(_standalone_spec())

    _, params, context = stub.calls[0]
    assert context is not None
    normalized = UnifiedToolExecutor()._normalize_params("code_executor", dict(params), context)

    assert normalized["plan_id"] is None
    assert normalized["task_id"] is None
    assert normalized["require_task_context"] is False
    assert (
        _validate_scope_contract(
            plan_id=None,
            task_id=None,
            require_task_context=normalized["require_task_context"],
        )
        is None
    )


def test_plan_bound_delegation_context_keeps_the_atomic_scope_guardrail() -> None:
    stub = _ToolExecutorStub(_COMPLETED_PAYLOAD)
    _make_executor(stub).execute(_plan_bound_spec())

    _, params, context = stub.calls[0]
    assert context is not None
    normalized = UnifiedToolExecutor()._normalize_params("code_executor", dict(params), context)

    assert normalized["plan_id"] == 106
    assert normalized["task_id"] == 1
    assert normalized["require_task_context"] is True


def test_standalone_delegation_reaches_code_executor_unscoped(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_execute_tool(tool_name: str, **kwargs: object) -> dict[str, object]:
        captured["tool_name"] = tool_name
        captured["kwargs"] = kwargs
        return {
            "success": True,
            "result": "Delegated run finished without plan context and produced no artifacts.",
            "run_id": "run-standalone-3",
        }

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)

    result = CodeAgentTaskDelegateExecutor().execute(_standalone_spec())

    assert captured["tool_name"] == "code_executor"
    tool_kwargs = cast(dict[str, object], captured["kwargs"])
    assert tool_kwargs["plan_id"] is None
    assert tool_kwargs["task_id"] is None
    assert tool_kwargs["require_task_context"] is False
    assert tool_kwargs["task"] == CodeAgentTaskDelegateExecutor()._build_delegate_prompt(_standalone_spec())
    tool_context = cast(ToolContext, tool_kwargs["tool_context"])
    assert tool_context.plan_id is None
    assert tool_context.task_id is None
    assert result.status == "completed"
    assert result.summary == "Delegated run finished without plan context and produced no artifacts."


def test_half_set_plan_binding_is_rejected() -> None:
    half_set = TaskDelegationSpec(
        task_name="Half bound",
        task_instruction="Do it",
        task_prompt="Body",
        executor_backend="local",
        plan_id=9,
    )
    stub = _ToolExecutorStub(_COMPLETED_PAYLOAD)

    with pytest.raises(ValueError, match="both plan_id and task_id"):
        _make_executor(stub).execute(half_set)
    with pytest.raises(ValueError, match="both plan_id and task_id"):
        CodeAgentTaskDelegateExecutor()._build_delegate_prompt(half_set)
    assert not stub.calls
