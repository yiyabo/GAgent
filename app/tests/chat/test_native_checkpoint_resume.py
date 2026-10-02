"""Real SQLite/controller crash windows, with scripted tools and no provider calls."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.database import get_db, init_db
from app.llm import NativeStreamResult, NativeToolCall
from app.repository import chat_runs
from app.routers.chat import run_routes
from app.routers.chat.models import ChatRequest
from app.services.chat_run_state import chat_run_claim
from app.services.deep_think import checkpointing
from app.services.deep_think_agent import DeepThinkAgent
from app.services.execution.step_ledger import StepLedger
from app.services.run_resume import prepare_run_resume


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    async def stream_chat_with_tools_async(self, **kwargs):
        self.messages.append(kwargs["messages"])
        return next(self.responses)

    async def stream_chat_async(self, **kwargs):
        yield "The confirmed output is ready."


def _call(name, args, call_id="provider-slot"):
    return NativeStreamResult(content="produce the output", tool_calls=[
        NativeToolCall(id=call_id, name=name, arguments=args),
    ])


def _create(run_id, request=None):
    request = request or ChatRequest(message="Write the result", session_id="resume-session")
    chat_runs.create_chat_run(run_id, request.session_id, request.model_dump_json(), owner_id="resume-owner",
                             idempotency_key=request.client_message_id)
    assert chat_runs.claim_chat_run_lease(run_id, f"claim-{run_id}", ttl_seconds=300)
    assert chat_runs.mark_chat_run_started(run_id, worker_id=f"claim-{run_id}")


@pytest.fixture
def resumable_db(isolated_app_env):
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO chat_sessions(id, owner_id, name) VALUES(?, ?, ?)",
                     ("resume-session", "resume-owner", "resume"))
        conn.commit()
    handle = chat_run_claim.set(None)
    yield isolated_app_env
    chat_run_claim.reset(handle)


@pytest.mark.parametrize("change_output", [False, True])
def test_crash_after_mutation_commit_replays_or_requires_reconciliation(
    resumable_db, monkeypatch, change_output,
):
    target = resumable_db["runtime_root"] / "result.csv"
    writes = []
    callbacks = []

    async def write_file(name, args):
        writes.append(args)
        target.write_text("value\n1\n")
        return {"success": True, "produced_files": [str(target)]}

    source_agent = DeepThinkAgent(
        ScriptedLLM([_call("file_operations", {"operation": "write", "path": str(target)})]),
        ["file_operations"], write_file, max_iterations=4,
    )
    _create("parent")
    save = checkpointing.save_native_checkpoint

    async def crash_before_next_model(agent, **kwargs):
        if agent is source_agent and kwargs["phase"] == "native_ready" and kwargs["iteration"] == 1:
            raise asyncio.CancelledError("simulated worker loss after the effect was committed")
        await save(agent, **kwargs)

    monkeypatch.setattr(checkpointing, "save_native_checkpoint", crash_before_next_model)

    async def scenario():
        handle = chat_run_claim.set(("parent", "claim-parent"))
        try:
            with pytest.raises(asyncio.CancelledError):
                await source_agent.think("Write the result")
            assert StepLedger("parent").load_checkpoint(
                checkpoint_key=checkpointing.checkpoint_key(source_agent, "Write the result"),
            ).phase == "native_tools_pending"
        finally:
            chat_run_claim.reset(handle)
        assert chat_runs.mark_chat_run_finished("parent", "failed", worker_id="claim-parent")
        if change_output:
            target.write_text("changed after interruption\n")
        _create("child")
        handle = chat_run_claim.set(("child", "claim-child"))
        try:
            await prepare_run_resume("child", {"resume_from_run_id": "parent"})
            resumed = DeepThinkAgent(
                ScriptedLLM([NativeStreamResult(content="The result is complete.", tool_calls=[])]),
                ["file_operations"], write_file, max_iterations=4,
                on_tool_result=lambda name, result: callbacks.append(result),
            )
            result = await resumed.think("Write the result", context={"resume_from_run_id": "parent"})
            assert len(writes) == 1
            if change_output:
                assert result.execution_issues[0]["code"] == "step_reconciliation_required"
                assert resumed.llm_client.messages == []
                from app.routers.chat.response_metadata import _build_deep_think_response_metadata
                metadata = _build_deep_think_response_metadata(
                    result=result, routing_metadata={}, plan_id=None, plan_title=None,
                    reasoning_language="en", thinking_visible=False, progress_visible=False,
                )
                assert metadata["status"] == "failed"
                assert metadata["failure_kind"] == "step_reconciliation_required"
                issues = result.execution_issues
                ledger = StepLedger("child", worker_id="claim-child")
                assert checkpointing.unresolved_execution_issues(resumed, ledger, issues) == issues
                target.write_text("value\n1\n")
                assert checkpointing.unresolved_execution_issues(resumed, ledger, issues) == []
            else:
                assert callbacks[0]["replayed"] is True
                assert result.execution_issues == []
        finally:
            chat_run_claim.reset(handle)
        assert chat_runs.get_chat_run("parent")["status"] == "failed"

    asyncio.run(scenario())


def test_replayed_python_cell_defers_pending_dependent_cell_for_replanning(resumable_db):
    _create("python-run")
    handle = chat_run_claim.set(("python-run", "claim-python-run"))
    calls = []
    agent = DeepThinkAgent(ScriptedLLM([]), ["execute_code"], lambda *args: None)
    agent._checkpoint_namespace = "native:0:0:python"
    agent._acceptance_base_dir = str(resumable_db["runtime_root"])
    first = NativeToolCall(id="first", name="execute_code", arguments={"code": "x = 3"})
    second = NativeToolCall(id="second", name="execute_code", arguments={"code": "print(x)"})

    async def execute_first():
        calls.append("first")
        return {"tool_result": {"success": True, "kernel": {"variables": ["x"]}}, "tool_result_text": "x=3"}

    async def execute_second():
        calls.append("second")
        raise AssertionError("a pending dependent cell must not run before state reconstruction")

    async def scenario():
        await checkpointing.execute_recorded_tool(agent, first, 1, 0, execute_first)
        replayed = await checkpointing.execute_recorded_tool(agent, first, 1, 0, execute_first)
        assert "kernel" not in replayed["tool_result"]
        assert "not restored" in replayed["tool_result"]["hint"]
        blocked = await checkpointing.execute_recorded_tool(agent, second, 1, 1, execute_second)
        assert blocked["tool_result"]["error"] == "python_state_rebuild_required"
        assert calls == ["first"]

    try:
        asyncio.run(scenario())
    finally:
        chat_run_claim.reset(handle)


def test_replayed_binding_and_schema_loading_restore_runtime_state(resumable_db):
    from app.services.deep_think.schema_disclosure import SchemaDisclosure

    _create("binding-run")
    handle = chat_run_claim.set(("binding-run", "claim-binding-run"))
    agent = DeepThinkAgent(ScriptedLLM([]), ["plan_operation"], lambda *args: None)
    agent._checkpoint_namespace = "native:0:0:binding"
    agent._schema_disclosure = SchemaDisclosure([], ["file_operations"])

    async def scenario():
        call = NativeToolCall(id="bind", name="plan_operation", arguments={"operation": "bind", "plan_id": 7})

        async def bind():
            return {"tool_result": {"success": True, "operation": "bind", "plan_id": 7}, "tool_result_text": "bound"}

        await checkpointing.execute_recorded_tool(agent, call, 1, 0, bind)
        agent.request_profile.clear()
        await checkpointing.execute_recorded_tool(agent, call, 1, 0, bind)
        assert agent.request_profile["current_plan_id"] == 7
        schema = NativeToolCall(id="schema", name="load_tool_schema", arguments={"name": "file_operations"})

        async def load():
            return {"tool_result": {"success": True}, "tool_result_text": "loaded"}

        await checkpointing.execute_recorded_tool(agent, schema, 1, 1, load)
        await checkpointing.execute_recorded_tool(agent, schema, 1, 1, load)
        assert "file_operations" in agent._schema_disclosure.loaded

    try:
        asyncio.run(scenario())
    finally:
        chat_run_claim.reset(handle)


def test_missing_declared_output_does_not_confirm_or_repeat_a_returned_mutation(resumable_db):
    _create("missing-output")
    handle = chat_run_claim.set(("missing-output", "claim-missing-output"))
    agent = DeepThinkAgent(ScriptedLLM([]), ["file_operations"], lambda *args: None)
    agent._checkpoint_namespace = "native:0:0:missing"
    agent._acceptance_base_dir = str(resumable_db["runtime_root"])
    call = NativeToolCall(id="missing", name="file_operations", arguments={"operation": "write"})
    executions = []

    async def execute():
        executions.append(1)
        return {"tool_result": {"success": True, "output_file": "missing.csv"}, "tool_result_text": "done"}

    async def scenario():
        result = await checkpointing.execute_recorded_tool(agent, call, 1, 0, execute)
        assert result["tool_result"]["error"] == "step_reconciliation_required"
        second = await checkpointing.execute_recorded_tool(agent, call, 2, 0, execute)
        assert second["tool_result"]["error"] == "step_reconciliation_required"
        assert executions == [1]

    try:
        asyncio.run(scenario())
    finally:
        chat_run_claim.reset(handle)


def test_interrupted_schema_disclosure_can_restart_without_mutation_reconciliation(resumable_db):
    _create("schema-interrupted")
    handle = chat_run_claim.set(("schema-interrupted", "claim-schema-interrupted"))
    agent = DeepThinkAgent(ScriptedLLM([]), [], lambda *args: None)
    agent._checkpoint_namespace = "native:0:0:schema"
    call = NativeToolCall(id="schema", name="load_tool_schema", arguments={"name": "vision_reader"})
    ledger = StepLedger("schema-interrupted", worker_id="claim-schema-interrupted")
    decision = ledger.prepare("native:0:0:schema:1:0:schema", call.name, call.arguments,
                              replay_policy=checkpointing.replay_policy(agent, call.name, call.arguments))
    assert ledger.claim(decision.step.key)
    ledger.interrupt(decision.step.key)
    executions = []

    async def execute():
        executions.append(1)
        return {"tool_result": {"success": True}, "tool_result_text": "schema loaded"}

    try:
        result = asyncio.run(checkpointing.execute_recorded_tool(agent, call, 1, 0, execute))
        assert result["tool_result"]["success"] is True and executions == [1]
        assert not getattr(agent, "_execution_issues", [])
    finally:
        chat_run_claim.reset(handle)


def test_explicit_resume_request_mismatch_blocks_before_external_tool(resumable_db):
    from types import SimpleNamespace

    agent = SimpleNamespace(request_profile={}, _checkpoint_namespace="native:0:0:new")
    checkpoint = SimpleNamespace(controller_state={
        "namespace": "native:0:0:old", "query_sha256": "old-query", "task_id": None,
    })
    with pytest.raises(checkpointing.ControllerRestoreError, match="request/task mismatch"):
        checkpointing.restore_checkpoint(agent, checkpoint, "different user query", None,
                                         {"resume_from_run_id": "parent"})


def test_task_slots_and_checkpoints_are_isolated_across_plans(resumable_db):
    from types import SimpleNamespace

    first = SimpleNamespace(request_profile={"current_plan_id": 1})
    second = SimpleNamespace(request_profile={"current_plan_id": 2})
    task = SimpleNamespace(task_id=1)
    assert checkpointing.namespace(first, "same task", task) != checkpointing.namespace(second, "same task", task)
    assert checkpointing.checkpoint_key(first, "same task", task) != checkpointing.checkpoint_key(second, "same task", task)
    assert checkpointing.checkpoint_key(first, "same task", None).startswith("chat:")


def test_resume_endpoint_creates_one_continuation_and_keeps_terminal_source(resumable_db, monkeypatch):
    from app.services.execution.step_ledger import ControllerCheckpoint

    original = ChatRequest(message="Write the result", session_id="resume-session", client_message_id="original")
    _create("parent", original)
    StepLedger("parent", worker_id="claim-parent").save_checkpoint(ControllerCheckpoint(run_id="parent"))
    assert chat_runs.mark_chat_run_finished("parent", "failed", worker_id="claim-parent")
    spawned = []
    monkeypatch.setattr(run_routes, "_spawn_chat_run_worker", spawned.append)
    monkeypatch.setattr(run_routes, "_save_run_user_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_routes, "ensure_owner_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_routes, "get_request_owner_id", lambda request: "resume-owner")

    def bind(raw, model):
        assert isinstance(raw, Request) and isinstance(model, ChatRequest)
        return model

    monkeypatch.setattr(run_routes, "bind_chat_request_to_principal", bind)
    request = Request({"type": "http", "headers": []})

    async def scenario():
        first = await run_routes.resume_run("parent", request, {})
        # Simulate worker ownership after dispatch; a duplicate POST must return it.
        assert chat_runs.claim_chat_run_lease(first["run_id"], "child-worker")
        second = await run_routes.resume_run("parent", request, {})
        assert first == second and len(spawned) == 1
        child = chat_runs.get_chat_run(first["run_id"])
        assert json.loads(child["request_json"])["context"]["resume_from_run_id"] == "parent"
        assert chat_runs.get_chat_run("parent")["status"] == "failed"
        with pytest.raises(HTTPException) as error:
            await run_routes.resume_run("parent", request, {"session_id": "wrong-session"})
        assert error.value.status_code == 403
        with pytest.raises(HTTPException) as error:
            await run_routes.resume_run("parent", request, {"client_message_id": "original"})
        assert error.value.status_code == 409

    asyncio.run(scenario())


def test_resume_info_reports_checkpoint_and_uncertain_mutation_without_fork(resumable_db):
    from app.services.execution.step_ledger import ControllerCheckpoint
    from app.services.run_resume import resume_info

    _create('inspect')
    ledger = StepLedger('inspect',worker_id='claim-inspect')
    assert resume_info(chat_runs.get_chat_run('inspect'))['reason_code'] == 'not_terminal'
    ledger.save_checkpoint(ControllerCheckpoint(run_id='inspect'))
    decision = ledger.prepare('write','file_operations',{'operation':'write'},replay_policy='mutating')
    assert ledger.claim(decision.step.key)
    ledger.interrupt(decision.step.key)
    assert chat_runs.mark_chat_run_finished('inspect','cancelled',worker_id='claim-inspect')
    info = resume_info(chat_runs.get_chat_run('inspect'))
    assert info['can_resume'] is False and info['reason_code'] == 'reconciliation_required'
    assert chat_runs.get_chat_run('inspect')['status'] == 'cancelled'


def test_resume_info_missing_and_available_checkpoint_scope(resumable_db, monkeypatch):
    from app.services.execution.step_ledger import ControllerCheckpoint
    from app.services.run_resume import resume_info

    _create('inspect-ready')
    ledger = StepLedger('inspect-ready',worker_id='claim-inspect-ready')
    ledger.save_checkpoint(ControllerCheckpoint(run_id='inspect-ready'))
    assert chat_runs.mark_chat_run_finished('inspect-ready','failed',worker_id='claim-inspect-ready')
    _create('no-checkpoint')
    assert chat_runs.mark_chat_run_finished('no-checkpoint','failed',worker_id='claim-no-checkpoint')
    assert resume_info(chat_runs.get_chat_run('no-checkpoint'))['reason_code'] == 'checkpoint_missing'
    info = resume_info(chat_runs.get_chat_run('inspect-ready'))
    assert info['can_resume'] and info['message'] == 'Write the result'
    monkeypatch.setattr(run_routes,'ensure_owner_access',lambda *args,**kwargs:None)
    request = Request({'type':'http','headers':[]})
    assert asyncio.run(run_routes.get_resume_info('inspect-ready',request,'resume-session')) == info
    with pytest.raises(HTTPException) as error:
        asyncio.run(run_routes.get_resume_info('inspect-ready',request,'another-session'))
    assert error.value.status_code == 403


def test_cancelled_turn_without_answer_retains_resume_entry_on_history_refresh(resumable_db):
    from app.routers.chat.models import ChatMessage
    from app.services.run_resume import annotate_unanswered_turns
    _create('cancel-no-answer')
    with get_db() as conn:
        cursor = conn.execute("INSERT INTO chat_messages(session_id,role,content) VALUES('resume-session','user','Write the result')")
        message_id = cursor.lastrowid
        conn.commit()
    chat_runs.set_chat_run_user_message_id('cancel-no-answer',message_id)
    assert chat_runs.mark_chat_run_finished('cancel-no-answer','cancelled',worker_id='claim-cancel-no-answer')
    messages = [ChatMessage(id=message_id,role='user',content='Write the result')]
    annotate_unanswered_turns(messages,'resume-session','resume-owner')
    assert messages[0].metadata == {'resume_run_id':'cancel-no-answer','resume_run_status':'cancelled'}
    other = [ChatMessage(id=message_id,role='user',content='Write the result')]
    annotate_unanswered_turns(other,'resume-session','different-owner')
    assert other[0].metadata is None
    assert chat_runs.get_chat_run('cancel-no-answer')['status'] == 'cancelled'
