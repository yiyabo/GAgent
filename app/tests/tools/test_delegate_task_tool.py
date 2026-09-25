"""``delegate_task`` handler: spec mapping, bounded return contract, env gate.

S2 of ``design/2026-09-25-subagent-delegation-plane.md``: the chat-side general
delegation tool on the S1 neutral contract (``plan_id`` / ``task_id`` unset).
The delegation itself is stubbed at ``CodeAgentTaskDelegateExecutor`` — the
handler's job is the mapping in, the bounded digest out.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from app.services.plans.task_delegate_executor import (
    TaskDelegationResult,
    TaskDelegationSpec,
)
from tool_box.context import ToolContext
from tool_box.tools_impl.delegate_task import (
    MAX_ARTIFACT_PATHS,
    MAX_CONTEXT_PATHS,
    MAX_SUMMARY_CHARS,
    delegate_task_handler,
)

_EXECUTOR_PATH = (
    "app.services.plans.task_delegate_executor.CodeAgentTaskDelegateExecutor"
)


class _Recorder:
    """Stands in for the executor class: records specs, returns a fixed result."""

    def __init__(
        self,
        result: Optional[TaskDelegationResult] = None,
        *,
        raises: Optional[BaseException] = None,
    ) -> None:
        self.result = result
        self.raises = raises
        self.specs: List[TaskDelegationSpec] = []

    def __call__(self, *args: Any, **kwargs: Any) -> "_Recorder":
        return self

    def execute(
        self, spec: TaskDelegationSpec, **_kwargs: Any
    ) -> TaskDelegationResult:
        self.specs.append(spec)
        if self.raises is not None:
            raise self.raises
        assert self.result is not None
        return self.result


@pytest.fixture(autouse=True)
def _delegation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DELEGATE_TASK_ENABLED", "1")


def _install(
    monkeypatch: pytest.MonkeyPatch,
    result: Optional[TaskDelegationResult] = None,
    *,
    raises: Optional[BaseException] = None,
) -> _Recorder:
    recorder = _Recorder(result, raises=raises)
    monkeypatch.setattr(_EXECUTOR_PATH, recorder)
    return recorder


def _completed_result() -> TaskDelegationResult:
    return TaskDelegationResult(
        status="completed",
        summary="Patched 12 files and wrote reports/deprecation_audit.md.",
        artifact_paths=["/data/pipeline/reports/deprecation_audit.md"],
        stdout="should never reach the parent",
        stderr="neither should this",
        executor="qwen_code",
        executor_session_id="20260925_101010_deadbeef",
        raw_result={
            "run_id": "20260925_101010_deadbeef",
            "execution_backend": "qwen_code",
            "run_directory": "/app/runtime/session_s1/_scratch/audit/run_20260925_101010_deadbeef",
            "task_directory_full": "/app/runtime/session_s1/_scratch/audit/run_20260925_101010_deadbeef",
            "log_path": "/app/runtime/session_s1/_scratch/audit/run_x/results/audit_code_executor.log",
            "debug_log_path": "/app/runtime/session_s1/_scratch/audit/run_x/results/audit_claude_debug.log",
            "cli_usage": {
                "provider": "qwen",
                "model": "qwen3-coder-plus",
                "prompt_tokens": 1200,
                "completion_tokens": 340,
                "total_tokens": 1540,
            },
            "stdout": "should never reach the parent",
            "stderr": "neither should this",
        },
        metadata={"delegated_task_execution": True, "executor": "qwen_code"},
    )


def _contract_keys(payload: Dict[str, Any]) -> set:
    return set(payload) - {"error"}


# --- spec mapping -----------------------------------------------------------------


async def test_handler_maps_params_onto_a_standalone_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _install(monkeypatch, _completed_result())
    context = ToolContext(
        session_id="session-1",
        owner_id="owner-9",
        job_id="job-3",
        work_dir="/app/runtime/session_s1/raw_files/chat_tools/delegate_task",
    )

    await delegate_task_handler(
        goal="Audit every Python file under data/pipeline and fix it",
        deliverable="patched files + a markdown report",
        context_paths=["data/pipeline", "data/pipeline", "extra/dir"],
        tool_context=context,
    )

    assert len(recorder.specs) == 1
    spec = recorder.specs[0]
    assert spec.plan_id is None
    assert spec.task_id is None
    assert spec.session_id == "session-1"
    assert spec.owner_id == "owner-9"
    assert spec.current_job_id == "job-3"
    assert spec.work_dir == "/app/runtime/session_s1/raw_files/chat_tools/delegate_task"
    assert spec.task_instruction == "Audit every Python file under data/pipeline and fix it"
    # Duplicates collapse; order is preserved and the mapper does not touch the disk.
    assert spec.readable_dirs == ["data/pipeline", "extra/dir"]
    # Empty backend = code_executor resolves its own lane instead of being pinned
    # to the plan-delegation override.
    assert spec.executor_backend == ""
    assert spec.task_name == "Audit every Python file under data/pipeline and fix it"


async def test_handler_prompt_carries_goal_deliverable_and_output_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _install(monkeypatch, _completed_result())

    await delegate_task_handler(
        goal="Produce the QC report",
        deliverable="report.md plus the cleaned FASTA set",
        tool_context=ToolContext(session_id="session-1"),
    )

    prompt = recorder.specs[0].task_prompt
    assert prompt.startswith("GOAL:\nProduce the QC report\n")
    assert "\nEXPECTED DELIVERABLE:\nreport.md plus the cleaned FASTA set\n" in prompt
    assert "absolute path of each one" in prompt


async def test_handler_prompt_omits_the_deliverable_block_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _install(monkeypatch, _completed_result())

    await delegate_task_handler(goal="Do the thing", tool_context=ToolContext())

    prompt = recorder.specs[0].task_prompt
    assert prompt.startswith("GOAL:\nDo the thing\n")
    assert "EXPECTED DELIVERABLE:" not in prompt


async def test_handler_without_tool_context_leaves_identity_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _install(monkeypatch, _completed_result())

    await delegate_task_handler(goal="Do the thing")

    spec = recorder.specs[0]
    assert spec.session_id is None
    assert spec.owner_id is None
    assert spec.current_job_id is None
    assert spec.work_dir is None


async def test_handler_caps_and_normalizes_context_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _install(monkeypatch, _completed_result())
    raw = [f"/data/dir_{index}" for index in range(MAX_CONTEXT_PATHS + 7)] + [""]

    await delegate_task_handler(goal="Sweep everything", context_paths=raw)

    assert recorder.specs[0].readable_dirs == raw[:MAX_CONTEXT_PATHS]


async def test_empty_goal_is_refused_without_touching_the_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _install(monkeypatch, _completed_result())

    payload = await delegate_task_handler(goal="   ")

    assert recorder.specs == []
    assert payload["success"] is False
    assert payload["status"] == "failed"
    assert payload["error"] == "empty_goal"
    assert _contract_keys(payload) == {
        "tool",
        "success",
        "status",
        "summary",
        "artifact_paths",
        "usage",
        "trace_ref",
    }


# --- return contract --------------------------------------------------------------


async def test_success_payload_has_exactly_the_contract_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _completed_result())

    payload = await delegate_task_handler(
        goal="Audit data/pipeline", tool_context=ToolContext(session_id="session-1")
    )

    assert set(payload) == {
        "tool",
        "success",
        "status",
        "summary",
        "artifact_paths",
        "usage",
        "trace_ref",
    }
    assert payload["tool"] == "delegate_task"
    assert payload["success"] is True
    assert payload["status"] == "completed"
    assert payload["summary"] == "Patched 12 files and wrote reports/deprecation_audit.md."
    assert payload["artifact_paths"] == ["/data/pipeline/reports/deprecation_audit.md"]


async def test_summary_is_truncated_at_the_hard_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_summary = "A" * (MAX_SUMMARY_CHARS * 3)
    _install(
        monkeypatch,
        TaskDelegationResult(status="completed", summary=long_summary),
    )

    payload = await delegate_task_handler(goal="Do the thing")

    assert len(payload["summary"]) <= MAX_SUMMARY_CHARS
    assert payload["summary"].startswith("A" * 100)
    assert "truncated" in payload["summary"]
    assert f"{len(long_summary)} chars total" in payload["summary"]


async def test_raw_stdout_and_stderr_never_reach_the_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _completed_result())

    payload = await delegate_task_handler(goal="Do the thing")
    serialized = json.dumps(payload)

    assert "should never reach the parent" not in serialized
    assert "neither should this" not in serialized
    assert "stdout" not in payload
    assert "stderr" not in payload


async def test_summary_that_is_raw_cli_output_is_replaced_by_a_status_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The neutral executor falls back to a stdout prefix when a run produced no
    result text; that prefix must not be handed to the parent as a summary."""
    raw_line = '{"type":"result","subtype":"success","duration_ms":1234}'
    _install(
        monkeypatch,
        TaskDelegationResult(
            status="completed",
            summary=raw_line,
            stdout=raw_line + "\nmore raw output\n",
            stderr="",
        ),
    )

    payload = await delegate_task_handler(goal="Do the thing")

    assert payload["summary"] == "Sub-agent delegation completed."
    assert raw_line not in json.dumps(payload)


async def test_empty_summary_falls_back_to_a_status_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        TaskDelegationResult(status="blocked", summary="", artifact_paths=[]),
    )

    payload = await delegate_task_handler(goal="Do the thing")

    assert payload["success"] is False
    assert payload["status"] == "blocked"
    assert payload["summary"] == (
        "Sub-agent delegation is blocked: a required input was missing (see trace_ref)."
    )


async def test_artifact_paths_are_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = [f"/data/out/file_{index}.tsv" for index in range(MAX_ARTIFACT_PATHS + 25)]
    _install(
        monkeypatch,
        TaskDelegationResult(
            status="completed", summary="Produced a lot.", artifact_paths=paths
        ),
    )

    payload = await delegate_task_handler(goal="Do the thing")

    assert payload["artifact_paths"] == paths[:MAX_ARTIFACT_PATHS]


async def test_trace_ref_exposes_the_run_and_says_paths_are_absolute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _completed_result())

    payload = await delegate_task_handler(goal="Do the thing")
    trace = payload["trace_ref"]

    assert trace["run_id"] == "20260925_101010_deadbeef"
    assert trace["execution_backend"] == "qwen_code"
    assert trace["log_path"].endswith("audit_code_executor.log")
    assert trace["debug_log_path"].endswith("audit_claude_debug.log")
    assert trace["run_directory"].startswith("/app/runtime/")
    assert trace["path_base"] == "absolute"


async def test_usage_reports_tokens_and_a_measured_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _completed_result())

    payload = await delegate_task_handler(goal="Do the thing")
    usage = payload["usage"]

    assert usage["prompt_tokens"] == 1200
    assert usage["completion_tokens"] == 340
    assert usage["total_tokens"] == 1540
    assert usage["model"] == "qwen3-coder-plus"
    assert usage["provider"] == "qwen"
    assert isinstance(usage["duration_ms"], float)
    assert usage["duration_ms"] >= 0.0


async def test_usage_still_reports_duration_when_the_backend_reports_no_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        TaskDelegationResult(
            status="completed", summary="Done locally.", raw_result={"run_id": "r1"}
        ),
    )

    payload = await delegate_task_handler(goal="Do the thing")

    assert set(payload["usage"]) == {"duration_ms"}
    assert payload["trace_ref"]["run_id"] == "r1"


async def test_failed_delegation_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        TaskDelegationResult(
            status="failed",
            summary="Sub-agent exited with code 1 before producing artifacts.",
            artifact_paths=[],
        ),
    )

    payload = await delegate_task_handler(goal="Do the thing")

    assert payload["success"] is False
    assert payload["status"] == "failed"
    assert payload["summary"].startswith("Sub-agent exited with code 1")


async def test_executor_exception_becomes_a_bounded_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, raises=RuntimeError("no qwen CLI available"))

    payload = await delegate_task_handler(goal="Do the thing")

    assert payload["success"] is False
    assert payload["status"] == "failed"
    assert payload["error"] == "delegate_task_error"
    assert "RuntimeError" in payload["summary"]
    assert payload["artifact_paths"] == []
    assert payload["usage"]["duration_ms"] >= 0.0


# --- wiring into the neutral S1 executor ------------------------------------------


async def test_handler_drives_the_real_neutral_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end wiring: handler -> TaskDelegationSpec -> code_executor params."""
    from typing import cast

    from app.services.execution.tool_executor import (
        ToolExecutionContext,
        UnifiedToolExecutor,
    )
    from app.services.plans import task_delegate_executor as delegate_module

    captured: Dict[str, Any] = {}

    class _ToolExecutorStub:
        def execute_sync(
            self,
            tool_name: str,
            params: Dict[str, Any],
            *,
            context: Optional[ToolExecutionContext] = None,
        ) -> Dict[str, Any]:
            captured["tool_name"] = tool_name
            captured["params"] = params
            captured["context"] = context
            return {
                "success": True,
                "result": {
                    "success": True,
                    "result": (
                        "The sub-agent swept the corpus and wrote the evidence table "
                        "for review."
                    ),
                    "run_id": "run-real-1",
                    "artifact_paths": ["/data/out/evidence.tsv"],
                    "execution_backend": "qwen_code",
                },
            }

    # Subclass, not a stub replacement: the neutral executor resolves its own
    # static helpers through this module-global name.
    class _StubbedDelegate(delegate_module.CodeAgentTaskDelegateExecutor):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(
                tool_executor=cast(UnifiedToolExecutor, _ToolExecutorStub())
            )

    monkeypatch.setattr(
        delegate_module, "CodeAgentTaskDelegateExecutor", _StubbedDelegate
    )

    payload = await delegate_task_handler(
        goal="Sweep the corpus and produce the evidence table",
        context_paths=["data/corpus"],
        tool_context=ToolContext(session_id="session-e2e", owner_id="owner-2"),
    )

    assert captured["tool_name"] == "code_executor"
    params = cast(Dict[str, Any], captured["params"])
    assert params["add_dirs"] == ["data/corpus"]
    assert params["execution_backend"] == ""
    assert params["output_format"] == "json"
    assert params["task"].startswith(
        "You are executing one atomic task delegated by the orchestration system.\n"
        "Complete only this task; do not expand its scope or take on other work."
    )
    assert "Sweep the corpus and produce the evidence table" in params["task"]
    assert "Plan ID:" not in params["task"]
    context = cast(ToolExecutionContext, captured["context"])
    assert context.plan_id is None
    assert context.task_id is None
    assert context.session_id == "session-e2e"
    assert context.owner_id == "owner-2"
    assert context.channel == "chat"
    assert context.mode == "delegated_task_execution"

    assert payload["success"] is True
    assert payload["summary"].startswith("The sub-agent swept the corpus")
    assert payload["artifact_paths"] == ["/data/out/evidence.tsv"]
    assert payload["trace_ref"]["run_id"] == "run-real-1"
    assert "The sub-agent swept the corpus" not in json.dumps(payload["usage"])


# --- env gate ---------------------------------------------------------------------


async def test_disabled_flag_refuses_with_a_structured_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DELEGATE_TASK_ENABLED", raising=False)
    recorder = _install(monkeypatch, _completed_result())

    payload = await delegate_task_handler(goal="Do the thing")

    assert recorder.specs == []
    assert payload["success"] is False
    assert payload["status"] == "disabled"
    assert payload["error"] == "delegate_task_disabled"
    assert "DELEGATE_TASK_ENABLED=1" in payload["summary"]
    assert payload["artifact_paths"] == []
    assert payload["usage"] == {}
    assert payload["trace_ref"] == {}


async def test_flag_value_must_be_exactly_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELEGATE_TASK_ENABLED", "true")

    payload = await delegate_task_handler(goal="Do the thing")

    assert payload["status"] == "disabled"
