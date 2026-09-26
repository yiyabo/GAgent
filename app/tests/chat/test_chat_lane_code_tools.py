"""Chat-lane execution for the env-gated / progressive-disclosure tools.

``execute_code``, ``delegate_task`` and ``load_skill`` are advertised to the
model (``request_routing.get_all_tools``), and every deep-think tool call in the
chat lane lands in ``action_handlers.handle_tool_action`` — but that handler's
per-tool normalization chain had no branch for them, so they hit the chain's
final ``else`` and were rejected with ``error="unsupported_tool"``.

These tests pin the three tools onto the generic execution path, keep the
rejection for genuinely unknown names, and check that the sanitized result each
handler returns still carries the payload the model needs.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

import app.routers.chat.action_handlers as action_handlers
from app.routers.chat.action_handlers import handle_tool_action
from app.routers.chat.models import AgentStep
from app.routers.chat.tool_results import sanitize_tool_result, summarize_tool_result
from app.services.llm.structured_response import LLMAction
from tool_box.context import ToolContext

_SANDBOX = "runtime/test_chat_lane_code_tools_sandbox"
_SESSION_ID = "chat-lane-code-tools"


class _AgentStub:
    """Minimal agent surface ``handle_tool_action`` needs."""

    def __init__(self, session_id: Optional[str] = _SESSION_ID) -> None:
        self.session_id = session_id
        self.conversation_id = None
        self.extra_context: Dict[str, Any] = {}
        self.plan_session = SimpleNamespace(plan_id=None, repo=None)

    def _sync_task_status_after_tool_execution(self, **_: Any) -> None:
        return None


def _action(name: str, parameters: Dict[str, Any]) -> LLMAction:
    return LLMAction(
        kind="tool_operation",
        name=name,
        parameters=parameters,
        order=1,
        metadata={"origin": "deep_think"},
    )


def _record_executor(
    monkeypatch: pytest.MonkeyPatch,
    results: Dict[str, Dict[str, Any]],
) -> List[Tuple[str, Dict[str, Any]]]:
    """Stub ``execute_tool``; the first parameter name must match production.

    ``handle_tool_action`` calls ``execute_tool(tool_name, **params)``, and
    ``load_skill``'s own parameter is literally ``name``, so a stub whose first
    parameter is called ``name`` would collide with it.
    """
    calls: List[Tuple[str, Dict[str, Any]]] = []

    async def _fake_execute_tool(registered_tool_name: str, **kwargs: Any) -> Any:
        calls.append((registered_tool_name, kwargs))
        return results[registered_tool_name]

    monkeypatch.setattr(action_handlers, "execute_tool", _fake_execute_tool)
    return calls


@pytest.fixture
def chat_lane(monkeypatch: pytest.MonkeyPatch) -> str:
    """Isolated runtime root plus the env gates that advertise the two gated tools."""
    monkeypatch.setenv("APP_RUNTIME_ROOT", _SANDBOX)
    monkeypatch.setenv("CODE_MODE_ENABLED", "1")
    monkeypatch.setenv("DELEGATE_TASK_ENABLED", "1")
    monkeypatch.setattr(action_handlers, "get_tool_policy", lambda: {})
    monkeypatch.setattr(action_handlers, "is_tool_allowed", lambda _name, _policy: True)
    return _SANDBOX


# ---------------------------------------------------------------------------
# the three tools reach the executor
# ---------------------------------------------------------------------------


def test_execute_code_reaches_executor_with_code_and_tool_context(
    chat_lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_result = {
        "success": True,
        "status": "ok",
        "output": "2\n",
        "exit_code": 0,
        "tool_calls_made": 0,
        "duration_seconds": 0.42,
        "kernel": {"reused": True, "execution_count": 3, "state_reset": False},
    }
    calls = _record_executor(monkeypatch, {"execute_code": raw_result})

    step = asyncio.run(
        handle_tool_action(_AgentStub(), _action("execute_code", {"code": "print(1+1)"}))
    )

    assert isinstance(step, AgentStep)
    assert step.success is True
    assert len(calls) == 1
    name, kwargs = calls[0]
    assert name == "execute_code"
    assert kwargs["code"] == "print(1+1)"

    tool_context = kwargs.get("tool_context")
    assert isinstance(tool_context, ToolContext)
    assert tool_context.session_id == _SESSION_ID
    assert tool_context.work_dir.endswith("chat_tools/execute_code")

    details = step.details
    assert details["tool"] == "execute_code"
    assert details["result"].get("error") != "unsupported_tool"
    # stdout is stored trimmed of surrounding whitespace by `_trim_text`.
    assert details["result"]["output"] == "2"
    assert details["result"]["kernel"] == {
        "reused": True,
        "execution_count": 3,
        "state_reset": False,
    }


def test_delegate_task_reaches_executor(
    chat_lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_result = {
        "tool": "delegate_task",
        "success": True,
        "status": "completed",
        "summary": "Audited 12 files and patched 3.",
        "artifact_paths": ["/work/report.md"],
        "usage": {"duration_ms": 1200.0, "total_tokens": 4321, "model": "qwen"},
        "trace_ref": {"run_id": "run-7", "log_path": "/work/run.log"},
    }
    calls = _record_executor(monkeypatch, {"delegate_task": raw_result})

    step = asyncio.run(
        handle_tool_action(_AgentStub(), _action("delegate_task", {"goal": "audit the pipeline"}))
    )

    assert step.success is True
    assert calls[0][0] == "delegate_task"
    assert calls[0][1]["goal"] == "audit the pipeline"
    details = step.details
    assert details["result"].get("error") != "unsupported_tool"
    assert details["result"]["summary"] == "Audited 12 files and patched 3."
    assert details["result"]["artifact_paths"] == ["/work/report.md"]


def test_load_skill_reaches_executor(
    chat_lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_result = {
        "success": True,
        "name": "xlsx",
        "description": "Spreadsheet skill",
        "content": "# xlsx\n\nSteps.",
        "truncated": False,
        "content_chars": 16,
        "total_chars": 16,
    }
    calls = _record_executor(monkeypatch, {"load_skill": raw_result})

    step = asyncio.run(
        handle_tool_action(_AgentStub(), _action("load_skill", {"name": "xlsx"}))
    )

    assert step.success is True
    assert calls[0][0] == "load_skill"
    assert calls[0][1]["name"] == "xlsx"
    details = step.details
    assert details["result"].get("error") != "unsupported_tool"
    assert details["result"]["content"] == "# xlsx\n\nSteps."


def test_unknown_tool_is_still_rejected(
    chat_lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`unsupported_tool` still fires for an advertised tool with no branch.

    The capability guard (``get_all_tools``) rejects unadvertised names first,
    so the chain's final ``else`` is what an advertised-but-unhandled name hits.
    """
    calls = _record_executor(monkeypatch, {})
    monkeypatch.setattr(
        action_handlers, "get_all_tools", lambda: ["totally_unknown_tool"]
    )

    step = asyncio.run(
        handle_tool_action(
            _AgentStub(),
            _action("totally_unknown_tool", {"code": "print(1+1)"}),
        )
    )

    assert step.success is False
    assert step.message == "Tool totally_unknown_tool is not supported yet."
    assert step.details == {"error": "unsupported_tool", "tool": "totally_unknown_tool"}
    assert calls == []


def test_tool_outside_the_pool_is_rejected_before_the_chain(
    chat_lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record_executor(monkeypatch, {})

    step = asyncio.run(
        handle_tool_action(_AgentStub(), _action("totally_unknown_tool", {"code": "1"}))
    )

    assert step.success is False
    assert step.message == "Tool 'totally_unknown_tool' is not a registered tool."
    assert step.details["error"] == "tool_not_available"
    assert step.details["tool"] == "totally_unknown_tool"
    assert calls == []


def test_normalizer_path_tools_do_not_receive_tool_context(
    chat_lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The context injection stays scoped to the three generic-execution tools."""
    calls = _record_executor(
        monkeypatch,
        {
            "file_operations": {
                "operation": "exists",
                "path": chat_lane,
                "success": True,
                "exists": True,
            }
        },
    )

    asyncio.run(
        handle_tool_action(
            _AgentStub(),
            _action("file_operations", {"operation": "exists", "path": chat_lane}),
        )
    )

    assert calls[0][0] == "file_operations"
    assert "tool_context" not in calls[0][1]


# ---------------------------------------------------------------------------
# sanitized payloads keep what the model needs
# ---------------------------------------------------------------------------


def test_sanitize_execute_code_keeps_cell_payload() -> None:
    raw_result = {
        "success": False,
        "status": "error",
        "output": "Traceback (most recent call last):\nValueError: boom",
        "error": "ValueError: boom",
        "exit_code": 1,
        "tool_calls_made": 4,
        "duration_seconds": 12.5,
        "kernel": {"reused": False, "execution_count": 2, "state_reset": True},
        "hint": "gagent_tools functions return DICTS",
        "warning": "execute_code stdout was truncated",
        "stdout_truncated": True,
        "stdout_bytes_captured": 50_000,
        "stdout_bytes_total": 80_000,
        "stdout_bytes_omitted": 30_000,
        "stdout_spill_path": "/work/spill/stdout-abc.txt",
    }

    sanitized = sanitize_tool_result("execute_code", raw_result)

    assert sanitized["tool"] == "execute_code"
    assert sanitized["success"] is False
    assert sanitized["status"] == "error"
    assert sanitized["output"].startswith("Traceback")
    assert sanitized["error"] == "ValueError: boom"
    assert sanitized["exit_code"] == 1
    assert sanitized["tool_calls_made"] == 4
    assert sanitized["duration_seconds"] == 12.5
    assert sanitized["kernel"] == {"reused": False, "execution_count": 2, "state_reset": True}
    assert sanitized["hint"] == "gagent_tools functions return DICTS"
    assert sanitized["warning"] == "execute_code stdout was truncated"
    assert sanitized["stdout_truncated"] is True
    assert sanitized["stdout_bytes_omitted"] == 30_000
    assert sanitized["stdout_spill_path"] == "/work/spill/stdout-abc.txt"


def test_sanitize_execute_code_trims_oversized_output() -> None:
    sanitized = sanitize_tool_result(
        "execute_code",
        {"success": True, "output": "x" * 60_000},
    )

    assert sanitized["output"].endswith("...")
    assert len(sanitized["output"]) < 60_000


def test_sanitize_delegate_task_keeps_bounded_digest() -> None:
    raw_result = {
        "tool": "delegate_task",
        "success": True,
        "status": "completed",
        "summary": "Fixed every call site.",
        "artifact_paths": ["/work/a.md", "/work/b.md"],
        "usage": {"duration_ms": 900.0, "total_tokens": 1234, "model": "qwen", "noise": "drop"},
        "trace_ref": {"run_id": "run-3", "log_path": "/work/run.log", "noise": "drop"},
    }

    sanitized = sanitize_tool_result("delegate_task", raw_result)

    assert sanitized["tool"] == "delegate_task"
    assert sanitized["success"] is True
    assert sanitized["status"] == "completed"
    assert sanitized["summary"] == "Fixed every call site."
    assert sanitized["artifact_paths"] == ["/work/a.md", "/work/b.md"]
    assert sanitized["usage"] == {"duration_ms": 900.0, "total_tokens": 1234, "model": "qwen"}
    assert sanitized["trace_ref"] == {"run_id": "run-3", "log_path": "/work/run.log"}


def test_sanitize_delegate_task_keeps_disabled_failure() -> None:
    sanitized = sanitize_tool_result(
        "delegate_task",
        {
            "tool": "delegate_task",
            "success": False,
            "status": "disabled",
            "summary": "delegate_task is disabled. Set DELEGATE_TASK_ENABLED=1 to enable sub-agent delegation.",
            "artifact_paths": [],
            "usage": {},
            "trace_ref": {},
            "error": "delegate_task_disabled",
        },
    )

    assert sanitized["success"] is False
    assert sanitized["status"] == "disabled"
    assert sanitized["error"] == "delegate_task_disabled"
    assert sanitized["summary"].startswith("delegate_task is disabled")
    assert "usage" not in sanitized


def test_sanitize_load_skill_keeps_body_and_recovery_list() -> None:
    loaded = sanitize_tool_result(
        "load_skill",
        {
            "success": True,
            "name": "xlsx",
            "description": "Spreadsheet skill",
            "content": "# xlsx\n\nSteps.",
            "truncated": True,
            "content_chars": 30_000,
            "total_chars": 45_000,
            "section": "editing",
        },
    )

    assert loaded["tool"] == "load_skill"
    assert loaded["success"] is True
    assert loaded["name"] == "xlsx"
    assert loaded["description"] == "Spreadsheet skill"
    assert loaded["content"] == "# xlsx\n\nSteps."
    assert loaded["truncated"] is True
    assert loaded["content_chars"] == 30_000
    assert loaded["total_chars"] == 45_000
    assert loaded["section"] == "editing"

    missing = sanitize_tool_result(
        "load_skill",
        {
            "success": False,
            "error": "skill_not_found: nope",
            "available_skills": ["pdf", "xlsx"],
            "summary": "Skill 'nope' not found. Available skills (first 2): pdf, xlsx",
        },
    )

    assert missing["success"] is False
    assert missing["error"] == "skill_not_found: nope"
    assert missing["available_skills"] == ["pdf", "xlsx"]
    assert missing["summary"].startswith("Skill 'nope' not found")


# ---------------------------------------------------------------------------
# one-line summaries: real state, not the generic fallback
# ---------------------------------------------------------------------------


def test_summaries_describe_the_three_tools() -> None:
    execute_ok = summarize_tool_result(
        "execute_code",
        sanitize_tool_result("execute_code", {"success": True, "output": "2\n"}),
    )
    assert execute_ok.startswith("execute_code succeeded")
    assert "Output: 2" in execute_ok

    execute_failed = summarize_tool_result(
        "execute_code",
        sanitize_tool_result(
            "execute_code",
            {"success": False, "error": "ValueError: boom", "hint": "Do not json.loads() the result."},
        ),
    )
    assert execute_failed.startswith("execute_code failed: ValueError: boom")
    assert "Hint: Do not json.loads() the result." in execute_failed

    delegated = summarize_tool_result(
        "delegate_task",
        sanitize_tool_result(
            "delegate_task",
            {
                "success": True,
                "status": "completed",
                "summary": "Fixed every call site.",
                "artifact_paths": ["/work/a.md"],
            },
        ),
    )
    assert delegated.startswith("delegate_task completed")
    assert "Fixed every call site." in delegated

    skill = summarize_tool_result(
        "load_skill",
        sanitize_tool_result(
            "load_skill",
            {"success": True, "name": "xlsx", "content_chars": 120},
        ),
    )
    assert skill == "load_skill loaded 'xlsx' (120 chars)."

    for summary in (execute_ok, execute_failed, delegated, skill):
        assert not summary.endswith("finished execution.")

    assert (
        summarize_tool_result("totally_unknown_tool", {"success": True})
        == "totally_unknown_tool finished execution."
    )


# ---------------------------------------------------------------------------
# why the chat lane has to rebuild the tool context
# ---------------------------------------------------------------------------


def test_generic_execution_set_covers_exactly_the_three_tools() -> None:
    assert action_handlers._GENERIC_EXECUTION_TOOLS == frozenset(
        {"execute_code", "delegate_task", "load_skill"}
    )


def test_deep_think_param_sanitizer_drops_tool_context() -> None:
    """Guard for the workaround in the chain branch.

    ``tool_wrapper`` strips ``tool_context`` out of the action parameters before
    ``_handle_tool_action`` sees them, which is why the branch rebuilds the
    context from the agent instead of relying on the inbound params. If this
    sanitizer ever starts preserving the object, the rebuild is redundant — but
    not wrong; if the branch's rebuild is removed, `execute_code` loses its
    per-session kernel.
    """
    from app.routers.chat.response_metadata import _sanitize_deep_think_tool_params

    sanitized = _sanitize_deep_think_tool_params(
        {
            "code": "print(1+1)",
            "tool_context": ToolContext(session_id=_SESSION_ID, work_dir="/work"),
        }
    )

    assert sanitized["code"] == "print(1+1)"
    assert "tool_context" not in sanitized
