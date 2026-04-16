from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routers import chat_routes
from app.routers.chat_routes import AgentStep, StructuredChatAgent
from app.routers.chat.agent import _apply_grounded_local_answer
from app.routers.chat import action_handlers as action_handlers_module
from app.routers.chat import session_helpers as session_helpers_module
from app.routers.chat.session_helpers import _extract_taskid_from_result
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.deliverables.publisher import PublishReport
from app.services.llm.structured_response import (
    LLMAction,
    LLMReply,
    LLMStructuredResponse,
    RetryPolicy,
)
from app.services.paper_replication import ExperimentCard
from tool_box.tools_impl import generate_experiment_card as generate_card_module


def _build_minimal_agent() -> StructuredChatAgent:
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=None)
    agent._resolve_job_meta = lambda: (None, "chat_action")
    agent._resolve_action_placeholders = lambda action, _source=None: action
    agent._build_suggestions = lambda _structured, _steps: []
    agent._build_actions_summary = lambda _steps: []
    agent._maybe_synthesize_phagescope_saveall_analysis = lambda _steps: None
    agent._append_summary_to_reply = lambda reply, _summary: reply
    agent._persist_if_dirty = lambda: False
    agent._include_action_summary = False
    agent._decomposition_errors = []
    agent._current_user_message = None
    agent._sync_job_id = None
    agent.mode = "assistant"
    agent.session_id = "test-session"
    agent.conversation_id = None
    agent.extra_context = {}
    agent.history = []
    return agent


def test_generate_experiment_card_reuse_does_not_reference_pdf_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    card_path = tmp_path / "card.yaml"
    card = ExperimentCard(
        paper={"title": "Demo", "pdf_path": str(pdf_path)},
        experiment={"id": "demo_exp", "name": "demo"},
        task={"description": "demo"},
    )
    monkeypatch.setattr(
        generate_card_module,
        "_find_existing_card_for_pdf",
        lambda _pdf: ("demo_exp", card, card_path),
    )

    async def _unexpected_read_pdf(_path: str):
        raise AssertionError("read_pdf should not be called when reusing a card")

    monkeypatch.setattr(generate_card_module, "read_pdf", _unexpected_read_pdf)

    result = asyncio.run(
        generate_card_module.generate_experiment_card_handler(
            pdf_path=str(pdf_path),
            overwrite=False,
        )
    )

    assert result["success"] is True
    assert result["metadata"]["reused"] is True
    assert result["metadata"]["pdf_file"] == "paper.pdf"


def test_execute_structured_stops_on_blocking_failure() -> None:
    agent = _build_minimal_agent()
    executed: list[str] = []

    async def _fake_execute_action(action: LLMAction) -> AgentStep:
        executed.append(action.name)
        if action.name == "first":
            return AgentStep(action=action, success=False, message="failed", details={})
        return AgentStep(action=action, success=True, message="ok", details={})

    agent._execute_action = _fake_execute_action

    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="run"),
        actions=[
            LLMAction(kind="system_operation", name="first", blocking=True, order=1),
            LLMAction(kind="system_operation", name="second", blocking=True, order=2),
        ],
    )
    result = asyncio.run(agent.execute_structured(structured))

    assert executed == ["first"]
    assert len(result.steps) == 1
    assert result.success is False


def test_execute_structured_retries_before_failing() -> None:
    agent = _build_minimal_agent()
    attempts = {"count": 0}

    async def _fake_execute_action(action: LLMAction) -> AgentStep:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return AgentStep(action=action, success=False, message="transient", details={})
        return AgentStep(
            action=action,
            success=True,
            message="ok",
            details={"result": {"value": 1}},
        )

    agent._execute_action = _fake_execute_action

    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="run"),
        actions=[
            LLMAction(
                kind="system_operation",
                name="retryable",
                blocking=True,
                order=1,
                retry_policy=RetryPolicy(max_retries=1, backoff_sec=0),
            )
        ],
    )
    result = asyncio.run(agent.execute_structured(structured))

    assert attempts["count"] == 2
    assert result.success is True
    assert result.steps[0].details["attempt"] == 2
    assert result.steps[0].details["max_attempts"] == 2


def test_execute_plan_step_marks_failure_when_plan_has_failed_or_skipped_tasks() -> None:
    tree = SimpleNamespace(id=34)
    summary = SimpleNamespace(
        executed_task_ids=[1],
        failed_task_ids=[],
        skipped_task_ids=[2],
        to_dict=lambda: {
            "executed_task_ids": [1],
            "failed_task_ids": [],
            "skipped_task_ids": [2],
        },
    )
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent._require_plan_bound = lambda: tree
    agent.plan_executor = SimpleNamespace(execute_plan=lambda _plan_id, config=None: summary)
    agent._refresh_plan_tree = lambda force_reload=True: None
    agent.session_id = "s1"
    agent._current_user_message = "run"
    agent.history = []
    agent.extra_context = {}

    action = LLMAction(kind="plan_operation", name="execute_plan", parameters={}, order=1)
    step = asyncio.run(agent._handle_plan_action(action))

    assert step.success is False
    assert step.details["skipped_task_ids"] == [2]


def test_optimize_plan_step_uses_atomic_repo_apply() -> None:
    tree = SimpleNamespace(id=34, title="Demo plan")
    recorded: dict[str, object] = {}
    refreshed = {"count": 0}

    def _apply_changes(plan_id: int, changes: list[dict[str, object]]):
        recorded["plan_id"] = plan_id
        recorded["changes"] = changes
        return [{"action": "reorder_task", "task_id": 2, "new_position": 0, "parent_id": 1}]

    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent._require_plan_bound = lambda: tree
    agent.plan_session = SimpleNamespace(
        plan_id=34,
        repo=SimpleNamespace(apply_changes_atomically=_apply_changes),
    )
    agent._refresh_plan_tree = lambda force_reload=True: refreshed.__setitem__("count", refreshed["count"] + 1)
    agent.session_id = "s1"
    agent._current_user_message = "optimize"
    agent.history = []
    agent.extra_context = {}

    action = LLMAction(
        kind="plan_operation",
        name="optimize_plan",
        parameters={"changes": [{"action": "reorder_task", "task_id": 2, "new_position": 0}]},
        order=1,
    )
    step = asyncio.run(agent._handle_plan_action(action))

    assert step.success is True
    assert step.details["applied_changes"] == 1
    assert step.details["failed_changes"] == 0
    assert refreshed["count"] == 1
    assert recorded == {
        "plan_id": 34,
        "changes": [{"action": "reorder_task", "task_id": 2, "new_position": 0}],
    }


def test_rerun_task_step_marks_failure_on_skipped_status() -> None:
    tree = SimpleNamespace(id=34)
    result = SimpleNamespace(
        status="skipped",
        to_dict=lambda: {"status": "skipped", "content": "blocked by dependencies"},
    )
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent._require_plan_bound = lambda: tree
    agent.plan_executor = SimpleNamespace(execute_task=lambda _plan_id, _task_id, config=None: result)
    agent._refresh_plan_tree = lambda force_reload=True: None
    agent.session_id = "s1"
    agent._current_user_message = "run"
    agent.history = []
    agent.extra_context = {}

    action = LLMAction(
        kind="task_operation",
        name="rerun_task",
        parameters={"task_id": 23},
        order=1,
    )
    step = agent._handle_task_action(action)

    assert step.success is False
    assert step.message == "Task [23] was skipped."


def test_optional_file_read_failure_remains_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()
    agent.session_id = None

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)

    async def _fake_execute_tool(name: str, **kwargs):
        assert name == "file_operations"
        return {
            "operation": "read",
            "path": kwargs.get("path"),
            "success": False,
            "error": "read_failed",
        }

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)

    action = LLMAction(
        kind="tool_operation",
        name="file_operations",
        parameters={"operation": "read", "path": "/tmp/missing.txt"},
        order=1,
        metadata={"optional": True},
    )

    step = asyncio.run(agent._handle_tool_action(action))

    assert step.success is False
    assert isinstance(step.details, dict)
    result = step.details.get("result")
    assert isinstance(result, dict)
    assert result.get("success") is False


def test_tools_floor_allows_code_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the simplified 2-mode routing, code_executor is available at 'tools' floor."""
    agent = _build_minimal_agent()
    agent.session_id = None
    agent.extra_context = {
        "capability_floor": "tools", "intent_type": "local_inspect",
    }

    monkeypatch.setattr(action_handlers_module, "get_tool_policy", lambda: {})
    monkeypatch.setattr(action_handlers_module, "is_tool_allowed", lambda _name, _policy: True)

    action = LLMAction(
        kind="tool_operation",
        name="code_executor",
        parameters={"task": "inspect"},
        order=1,
    )

    guard_result = action_handlers_module._enforce_capability_guard(
        agent, action, "code_executor", {"task": "inspect"},
    )

    assert guard_result is None  # No block — code_executor is allowed


def test_local_read_blocks_file_operations_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()
    agent.session_id = None
    agent.extra_context = {"capability_floor": "tools", "intent_type": "local_read"}

    action = LLMAction(
        kind="tool_operation",
        name="file_operations",
        parameters={"operation": "write", "path": "/tmp/demo.txt", "content": "x"},
        order=1,
    )

    guard_result = action_handlers_module._enforce_capability_guard(
        agent, action, "file_operations", {"operation": "write", "path": "/tmp/demo.txt", "content": "x"},
    )

    assert guard_result is not None
    assert guard_result.success is False
    assert guard_result.details["error"] == "operation_not_permitted"


def test_grounded_local_answer_overrides_unverified_success_claim() -> None:
    agent = _build_minimal_agent()
    agent.extra_context = {
        "active_subject": {
            "kind": "workspace",
            "canonical_ref": "data/demo",
            "display_ref": "data/demo",
            "verification_state": "not_found",
            "salience": 5,
        },
        "last_failure_state": {
            "subject_ref": "data/demo",
            "tool_name": "file_operations",
            "operation": "list",
            "error_message": "Directory not found",
            "timestamp": "2026-03-29T00:00:00Z",
        },
        "last_evidence_state": {
            "status": "failed",
            "verified_facts": [],
            "produced_artifacts": [],
            "unresolved": ["Directory not found"],
            "timestamp": "2026-03-29T00:00:00Z",
        },
    }

    grounded = _apply_grounded_local_answer(
        agent,
        "我已经读取了目录内容，并看到了 result.json 和 preview.json。",
        SimpleNamespace(
            capability_floor="tools",
            intent_type="local_inspect",
            effective_user_message="都有哪些数据在里面哇",
        ),
    )

    assert "不能确认" in grounded
    assert "Directory not found" in grounded


def test_local_tool_path_alias_is_normalized_to_active_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()
    agent.session_id = None
    canonical_ref = str((Path.cwd() / "data/demo").resolve())
    agent.extra_context = {
        "capability_floor": "tools", "intent_type": "local_inspect",
        "active_subject": {
            "kind": "workspace",
            "canonical_ref": canonical_ref,
            "display_ref": "data/demo",
            "aliases": ["data/demo", canonical_ref],
            "verification_state": "verified",
            "salience": 5,
        },
    }

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)

    captured: dict[str, str] = {}

    async def _fake_execute_tool(name: str, **kwargs):
        captured["tool"] = name
        captured["path"] = kwargs.get("path")
        return {
            "success": False,
            "tool": "file_operations",
            "error": "Directory not found",
        }

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)
    monkeypatch.setattr(action_handlers_module, "execute_tool", _fake_execute_tool)

    action = LLMAction(
        kind="tool_operation",
        name="file_operations",
        parameters={"operation": "list", "path": "/data/demo"},
        order=1,
    )

    step = asyncio.run(agent._handle_tool_action(action))

    assert step.success is False
    assert captured["tool"] == "file_operations"
    assert captured["path"] == canonical_ref
    assert agent.extra_context["last_failure_state"]["subject_ref"] == canonical_ref


def test_tools_floor_allows_terminal_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With 2-mode routing, terminal_session is available at 'tools' floor."""
    agent = _build_minimal_agent()
    agent.session_id = None
    agent.extra_context = {"capability_floor": "tools", "intent_type": "local_inspect"}

    monkeypatch.setattr(action_handlers_module, "get_tool_policy", lambda: {})
    monkeypatch.setattr(action_handlers_module, "is_tool_allowed", lambda _name, _policy: True)

    action = LLMAction(
        kind="tool_operation",
        name="terminal_session",
        parameters={"operation": "write", "data": "unzip demo.zip\n"},
        order=1,
    )

    guard_result = action_handlers_module._enforce_capability_guard(
        agent, action, "terminal_session", {"operation": "write", "data": "unzip demo.zip\n"},
    )

    assert guard_result is None  # No block — terminal_session is allowed


def test_grounded_local_mutation_failure_overrides_plain_success_claim() -> None:
    agent = _build_minimal_agent()
    agent.extra_context = {
        "active_subject": {
            "kind": "directory",
            "canonical_ref": "data/demo",
            "display_ref": "data/demo",
            "verification_state": "verified",
            "salience": 5,
        },
        "last_failure_state": {
            "subject_ref": "data/demo",
            "tool_name": "terminal_session",
            "operation": "write",
            "error_message": "zip file not found",
            "timestamp": "2026-03-29T00:00:00Z",
        },
        "last_evidence_state": {
            "status": "failed",
            "verified_facts": [],
            "produced_artifacts": [],
            "unresolved": ["zip file not found"],
            "timestamp": "2026-03-29T00:00:00Z",
        },
    }

    grounded = _apply_grounded_local_answer(
        agent,
        "我已经完成了解压，文件都放在原目录里了。",
        SimpleNamespace(
            capability_floor="execute",
            intent_type="local_mutation",
            effective_user_message="你帮我直接解压吧，就放在对应的zip包那里即可",
        ),
    )

    assert "本地修改" in grounded
    assert "zip file not found" in grounded


def test_bio_tools_is_supported_in_tool_action_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()
    agent.session_id = None

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)

    async def _fake_execute_tool(name: str, **kwargs):
        assert name == "bio_tools"
        assert kwargs.get("tool_name") == "seqkit"
        assert kwargs.get("operation") == "stats"
        assert kwargs.get("input_file") == "/tmp/a.fa"
        assert isinstance(kwargs.get("params"), dict)
        return {
            "success": False,
            "tool": "bio_tools",
            "error": "intentional test failure",
        }

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)

    action = LLMAction(
        kind="tool_operation",
        name="bio_tools",
        parameters={
            "tool_name": "seqkit",
            "operation": "stats",
            "input_file": "/tmp/a.fa",
            "params": {"k": "v"},
        },
        order=1,
    )

    step = asyncio.run(agent._handle_tool_action(action))

    assert step.success is False
    assert isinstance(step.details, dict)
    result = step.details.get("result")
    assert isinstance(result, dict)
    assert result.get("tool") == "bio_tools"
    assert result.get("error") == "intentional test failure"


def test_code_executor_is_blocked_after_bio_tools_input_preparation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()
    agent.session_id = None

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)

    call_counts = {"code_executor": 0}

    async def _fake_execute_tool(name: str, **kwargs):
        if name == "bio_tools":
            return {
                "success": False,
                "tool": "bio_tools",
                "operation": kwargs.get("operation"),
                "error": "sequence_text contains unsupported characters",
                "error_code": "invalid_sequence_text",
                "error_stage": "input_preparation",
                "no_claude_fallback": True,
            }
        if name == "code_executor":
            call_counts["code_executor"] += 1
            return {"success": True}
        return {"success": True}

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)

    bio_action = LLMAction(
        kind="tool_operation",
        name="bio_tools",
        parameters={
            "tool_name": "seqkit",
            "operation": "stats",
            "sequence_text": "ACGT;touch /tmp/pwn",
        },
        order=1,
    )
    bio_step = asyncio.run(agent._handle_tool_action(bio_action))
    assert bio_step.success is False

    claude_action = LLMAction(
        kind="tool_operation",
        name="code_executor",
        parameters={"task": "write script"},
        order=2,
    )
    claude_step = asyncio.run(agent._handle_tool_action(claude_action))

    assert claude_step.success is False
    assert "blocked" in claude_step.message.lower()
    assert isinstance(claude_step.details, dict)
    assert claude_step.details.get("error_code") == "bio_tools_input_preparation_failed"
    assert call_counts["code_executor"] == 0


def test_bio_tools_sequence_text_is_forwarded_with_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()
    agent.session_id = "session_abc123"

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)

    async def _fake_execute_tool(name: str, **kwargs):
        assert name == "bio_tools"
        assert str(kwargs.get("sequence_text") or "").strip() == ">seq1\nACGT"
        assert kwargs.get("session_id") == "session_abc123"
        return {"success": True, "tool": "bio_tools", "operation": "stats"}

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)

    action = LLMAction(
        kind="tool_operation",
        name="bio_tools",
        parameters={
            "tool_name": "seqkit",
            "operation": "stats",
            "sequence_text": ">seq1\nACGT\n",
        },
        order=1,
    )
    step = asyncio.run(agent._handle_tool_action(action))
    assert step.success is True


def test_code_executor_is_blocked_after_sequence_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()
    agent.session_id = None

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)

    call_counts = {"code_executor": 0}

    async def _fake_execute_tool(name: str, **kwargs):
        if name == "sequence_fetch":
            return {
                "success": False,
                "tool": "sequence_fetch",
                "error": "invalid accession",
                "error_code": "invalid_accession",
                "error_stage": "input_validation",
                "no_claude_fallback": True,
            }
        if name == "code_executor":
            call_counts["code_executor"] += 1
            return {"success": True}
        return {"success": True}

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)

    fetch_action = LLMAction(
        kind="tool_operation",
        name="sequence_fetch",
        parameters={"accession": "bad$$id"},
        order=1,
    )
    fetch_step = asyncio.run(agent._handle_tool_action(fetch_action))
    assert fetch_step.success is False

    claude_action = LLMAction(
        kind="tool_operation",
        name="code_executor",
        parameters={"task": "fallback download script"},
        order=2,
    )
    claude_step = asyncio.run(agent._handle_tool_action(claude_action))

    assert claude_step.success is False
    assert isinstance(claude_step.details, dict)
    assert claude_step.details.get("error_code") == "sequence_fetch_failed_no_fallback"
    assert call_counts["code_executor"] == 0


def test_sequence_fetch_success_clears_block_and_reenables_code_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()
    agent.session_id = None

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)

    call_counts = {"sequence_fetch": 0, "code_executor": 0}

    async def _fake_execute_tool(name: str, **kwargs):
        if name == "sequence_fetch":
            call_counts["sequence_fetch"] += 1
            if call_counts["sequence_fetch"] == 1:
                return {
                    "success": False,
                    "tool": "sequence_fetch",
                    "error": "invalid accession",
                    "error_code": "invalid_accession",
                    "error_stage": "input_validation",
                    "no_claude_fallback": True,
                }
            return {
                "success": True,
                "tool": "sequence_fetch",
                "accessions": ["NC_001416.1"],
                "output_file": "/tmp/nc_001416.fasta",
                "record_count": 1,
            }
        if name == "code_executor":
            call_counts["code_executor"] += 1
            return {"success": True}
        return {"success": True}

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)

    first_fetch = LLMAction(
        kind="tool_operation",
        name="sequence_fetch",
        parameters={"accession": "bad$$id"},
        order=1,
    )
    first_step = asyncio.run(agent._handle_tool_action(first_fetch))
    assert first_step.success is False

    second_fetch = LLMAction(
        kind="tool_operation",
        name="sequence_fetch",
        parameters={"accession": "NC_001416.1"},
        order=2,
    )
    second_step = asyncio.run(agent._handle_tool_action(second_fetch))
    assert second_step.success is True

    claude_action = LLMAction(
        kind="tool_operation",
        name="code_executor",
        parameters={"task": "analyze downloaded fasta"},
        order=3,
    )
    claude_step = asyncio.run(agent._handle_tool_action(claude_action))

    assert claude_step.success is True
    assert call_counts["code_executor"] == 1


def test_guardrail_injects_sequence_fetch_then_bio_tools_for_download_and_analysis() -> None:
    agent = _build_minimal_agent()
    agent._current_user_message = "Please download NC_001416.1 FASTA and analyze it."
    agent.history = []

    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="working on it"),
        actions=[],
    )
    patched = agent._apply_phagescope_fallback(structured)

    assert len(patched.actions) == 2
    first = patched.actions[0]
    second = patched.actions[1]
    assert first.name == "sequence_fetch"
    assert first.parameters.get("accessions") == ["NC_001416.1"]
    assert second.name == "bio_tools"
    assert second.parameters.get("tool_name") == "seqkit"
    assert second.parameters.get("operation") == "stats"
    assert second.parameters.get("input_file") == "{{ previous.output_file }}"


def test_phagescope_save_all_maps_tracking_alias_to_remote_taskid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)

    captured: dict[str, object] = {}

    async def _fake_execute_tool(name: str, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True, "action": kwargs.get("action"), "taskid": kwargs.get("taskid")}

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)
    monkeypatch.setattr(
        action_handlers_module,
        "_resolve_phagescope_taskid_alias",
        lambda _taskid, session_id=None: "37468",
    )

    action = LLMAction(
        kind="tool_operation",
        name="phagescope",
        parameters={"action": "save_all", "taskid": "act_a1c0d8007a554d9a98d688d7394f5ecd"},
        order=1,
    )
    step = asyncio.run(agent._handle_tool_action(action))

    assert step.success is True
    assert captured["name"] == "phagescope"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("taskid") == "37468"
    assert kwargs.get("action") == "save_all"
    assert kwargs.get("session_id") == "test-session"


def test_review_pack_writer_forwards_session_id_from_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)

    captured: dict[str, object] = {}

    async def _fake_execute_tool(name: str, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)

    action = LLMAction(
        kind="tool_operation",
        name="review_pack_writer",
        parameters={"topic": "Pseudomonas phage"},
        order=1,
    )
    step = asyncio.run(agent._handle_tool_action(action))

    assert step.success is True
    assert captured["name"] == "review_pack_writer"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("session_id") == "test-session"


def test_literature_pipeline_forwards_session_id_from_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)

    captured: dict[str, object] = {}

    async def _fake_execute_tool(name: str, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)

    action = LLMAction(
        kind="tool_operation",
        name="literature_pipeline",
        parameters={"query": "Pseudomonas phage"},
        order=1,
    )
    step = asyncio.run(agent._handle_tool_action(action))

    assert step.success is True
    assert captured["name"] == "literature_pipeline"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("session_id") == "test-session"


def test_vision_reader_accepts_file_path_alias_from_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)

    captured: dict[str, object] = {}

    async def _fake_execute_tool(name: str, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True, "tool": name, "image_path": kwargs.get("image_path")}

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)

    action = LLMAction(
        kind="tool_operation",
        name="vision_reader",
        parameters={"operation": "read_image", "file_path": "/tmp/chart.png"},
        order=1,
    )
    step = asyncio.run(agent._handle_tool_action(action))

    assert step.success is True
    assert captured["name"] == "vision_reader"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("image_path") == str(Path("/tmp/chart.png").resolve())
    assert "file_path" not in kwargs


def test_manuscript_writer_aligns_output_path_with_bound_task_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()

    dep_node = PlanNode(
        id=4,
        plan_id=68,
        name="整合分析",
        status="completed",
        execution_result=json.dumps(
            {
                "status": "completed",
                "artifact_paths": [
                    "/tmp/results/harmony_integration_umap.png",
                ],
            },
            ensure_ascii=False,
        ),
    )
    task_node = PlanNode(
        id=66,
        plan_id=68,
        name="数据来源与预处理方法描述",
        status="pending",
        dependencies=[4],
        metadata={
            "paper_mode": True,
            "paper_section": "method",
            "paper_context_paths": ["/tmp/paper/qc_summary.csv"],
            "acceptance_criteria": {
                "category": "paper",
                "blocking": True,
                "checks": [
                    {"type": "file_nonempty", "path": "methods/data_preprocessing.md"},
                ],
            },
        },
    )
    tree = PlanTree(
        id=68,
        title="Plan 68",
        nodes={4: dep_node, 66: task_node},
        adjacency={None: [4, 66], 4: [], 66: []},
    )

    class _Repo:
        def get_plan_tree(self, plan_id: int) -> PlanTree:
            assert plan_id == 68
            return tree

    agent.plan_session = SimpleNamespace(plan_id=68, repo=_Repo())
    agent.plan_tree = tree
    agent.extra_context = {"current_task_id": 66}
    agent._sync_task_status_after_tool_execution = lambda **_kwargs: None

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)
    monkeypatch.setattr(action_handlers_module, "get_tool_policy", lambda: {})
    monkeypatch.setattr(action_handlers_module, "is_tool_allowed", lambda _name, _policy: True)

    captured: dict[str, object] = {}

    async def _fake_execute_tool(name: str, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {
            "success": True,
            "tool": "manuscript_writer",
            "output_path": kwargs.get("output_path"),
            "analysis_path": "runtime/session_demo/manuscript/methods/data_preprocessing.md.analysis.md",
        }

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)
    monkeypatch.setattr(action_handlers_module, "execute_tool", _fake_execute_tool)

    action = LLMAction(
        kind="tool_operation",
        name="manuscript_writer",
        parameters={
            "task": "Write the data preprocessing methods subsection.",
            "output_path": "/tmp/wrong/data_source_preprocessing.md",
            "context_paths": [],
        },
        order=1,
    )
    step = asyncio.run(agent._handle_tool_action(action))

    assert step.success is True
    assert captured["name"] == "manuscript_writer"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("output_path") == "manuscript/methods/data_preprocessing.md"
    assert kwargs.get("sections") == ["method"]
    assert "/tmp/paper/qc_summary.csv" in kwargs.get("context_paths", [])
    assert "/tmp/results/harmony_integration_umap.png" in kwargs.get("context_paths", [])
    assert "harmony_integration_umap.png" in kwargs.get("task", "")
    assert "Do not substitute alternative algorithms" in kwargs.get("task", "")
    assert "manuscript/methods/data_preprocessing.md" in kwargs.get("task", "")


def test_manuscript_writer_defaults_to_session_draft_for_non_paper_bound_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()
    agent._current_user_message = "请基于现有输出整合生成最终论文草稿，不要查文献。"

    task_node = PlanNode(
        id=81,
        plan_id=68,
        name="任务合并优化-报告生成",
        status="pending",
        metadata={
            "acceptance_criteria": {
                "category": "analysis",
                "blocking": True,
                "checks": [
                    {"type": "file_nonempty", "path": "qc_report/comprehensive_qc_report.md"},
                ],
            },
        },
    )
    tree = PlanTree(
        id=68,
        title="Plan 68",
        nodes={81: task_node},
        adjacency={None: [81], 81: []},
    )

    class _Repo:
        def get_plan_tree(self, plan_id: int) -> PlanTree:
            assert plan_id == 68
            return tree

    agent.plan_session = SimpleNamespace(plan_id=68, repo=_Repo())
    agent.plan_tree = tree
    agent.extra_context = {"current_task_id": 81}
    agent._sync_task_status_after_tool_execution = lambda **_kwargs: None

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)
    monkeypatch.setattr(action_handlers_module, "get_tool_policy", lambda: {})
    monkeypatch.setattr(action_handlers_module, "is_tool_allowed", lambda _name, _policy: True)

    captured: dict[str, object] = {}

    async def _fake_execute_tool(name: str, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {
            "success": True,
            "tool": "manuscript_writer",
            "output_path": kwargs.get("output_path"),
        }

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)
    monkeypatch.setattr(action_handlers_module, "execute_tool", _fake_execute_tool)

    action = LLMAction(
        kind="tool_operation",
        name="manuscript_writer",
        parameters={},
        order=1,
    )
    step = asyncio.run(agent._handle_tool_action(action))

    assert step.success is True
    assert captured["name"] == "manuscript_writer"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("output_path") == "manuscript/manuscript_draft.md"
    assert "最终论文草稿" in kwargs.get("task", "")
    assert kwargs.get("draft_only") is True


def test_deliverable_submit_forwards_session_id_and_uses_publish_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_minimal_agent()

    monkeypatch.setattr(chat_routes, "get_tool_policy", lambda: {})
    monkeypatch.setattr(chat_routes, "is_tool_allowed", lambda _name, _policy: True)

    captured: dict[str, object] = {}

    async def _fake_execute_tool(name: str, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {
            "success": True,
            "tool": "deliverable_submit",
            "deliverable_submit": {
                "publish": True,
                "artifacts": [{"path": "/tmp/plot.png", "module": "image_tabular"}],
            },
        }

    class _Publisher:
        def publish_from_tool_result(self, **_kwargs):
            return PublishReport(
                version_id="v1",
                published_files_count=1,
                published_modules=["image_tabular"],
                manifest_path="/tmp/manifest.json",
                paper_status={},
                submit_artifacts_requested=2,
                submit_artifacts_published=1,
                submit_artifacts_skipped=1,
                warnings=["artifact[1] skipped: path '/tmp/missing.png' does not exist"],
            )

    monkeypatch.setattr(chat_routes, "execute_tool", _fake_execute_tool)
    monkeypatch.setattr(action_handlers_module, "execute_tool", _fake_execute_tool)
    monkeypatch.setattr(action_handlers_module, "get_deliverable_publisher", lambda: _Publisher())

    action = LLMAction(
        kind="tool_operation",
        name="deliverable_submit",
        parameters={"artifacts": [{"path": "/tmp/plot.png", "module": "image_tabular"}]},
        order=1,
    )
    step = asyncio.run(agent._handle_tool_action(action))

    assert step.success is True
    assert captured["name"] == "deliverable_submit"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("session_id") == "test-session"
    assert step.message.startswith("Deliverable submit published 1 artifact(s); skipped 1 with warnings")
    deliverables = step.details.get("deliverables") or {}
    assert deliverables["submit_artifacts_requested"] == 2
    assert deliverables["submit_artifacts_published"] == 1
    assert deliverables["submit_artifacts_skipped"] == 1


def test_extract_taskid_from_result_prefers_numeric_remote_taskid() -> None:
    payload = {
        "job_id": "act_a1c0d8007a554d9a98d688d7394f5ecd",
        "data": {
            "taskid": "act_zzzzzzzzzz",
            "remote_taskid": "37468",
            "results": {"task_id": "taskid=37430"},
        },
    }

    assert _extract_taskid_from_result(payload) == "37468"


def test_resolve_phagescope_taskid_alias_falls_back_to_action_run_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        session_helpers_module,
        "_lookup_phagescope_remote_taskid_by_job_id",
        lambda _job_id, session_id=None: None,
    )
    monkeypatch.setattr(
        session_helpers_module,
        "_lookup_phagescope_remote_taskid_from_action_run",
        lambda _run_id, session_id=None: "37468",
    )

    resolved = session_helpers_module._resolve_phagescope_taskid_alias(
        "act_a1c0d8007a554d9a98d688d7394f5ecd",
        session_id="session-x",
    )
    assert resolved == "37468"
