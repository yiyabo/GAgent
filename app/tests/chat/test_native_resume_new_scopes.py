"""Continuations distinguish untouched tasks from lost/uncertain task scopes."""
import asyncio

import pytest

from app.database import get_db, init_db
from app.repository import chat_runs
from app.routers.chat.models import ChatRequest
from app.services.chat_run_state import chat_run_claim
from app.services.deep_think import checkpointing
from app.services.deep_think.models import TaskExecutionContext
from app.services.deep_think_agent import DeepThinkAgent
from app.services.execution.step_ledger import ControllerCheckpoint, StepLedger
from app.services.run_resume import prepare_run_resume


@pytest.mark.parametrize("scenario", ["new_task", "changed_interrupted_task", "missing_entered_scope", "unknown_step", "repair_after_final"])
def test_new_scope_requires_positive_continuation_evidence(isolated_app_env, scenario):
    from types import SimpleNamespace
    from app.llm import NativeStreamResult

    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO chat_sessions(id,owner_id,name) VALUES('scopes','owner','scopes')")
        conn.commit()

    def create(run_id, context=None):
        request = ChatRequest(message="continue plan", session_id="scopes", context=context)
        chat_runs.create_chat_run(run_id, "scopes", request.model_dump_json(), owner_id="owner")
        assert chat_runs.claim_chat_run_lease(run_id, "claim-" + run_id)
        assert chat_runs.mark_chat_run_started(run_id, worker_id="claim-" + run_id)

    original = SimpleNamespace(request_profile={"current_plan_id": 1})
    original_key = checkpointing.checkpoint_key(original, "task two", TaskExecutionContext(task_id=2))
    create("source")
    StepLedger("source", worker_id="claim-source").save_checkpoint(
        ControllerCheckpoint(run_id="source", phase="native_ready"), checkpoint_key=original_key,
    )
    assert chat_runs.mark_chat_run_finished("source", "failed", worker_id="claim-source")

    class LLM:
        calls = 0

        async def stream_chat_with_tools_async(self, **kwargs):
            self.calls += 1
            return NativeStreamResult(content="The remaining task has completed its requested work and observations.", tool_calls=[])

        async def stream_chat_async(self, **kwargs):
            yield "The remaining task has completed its requested work and observations."

    async def forbidden_tool(*args, **kwargs):
        raise AssertionError("This scope proof test must not invoke an external tool")

    async def run():
        create("child", {"resume_from_run_id": "source"})
        handle = chat_run_claim.set(("child", "claim-child"))
        try:
            await prepare_run_resume("child", {"resume_from_run_id": "source"})
            task_id = 2 if scenario in {"changed_interrupted_task", "repair_after_final"} else 3
            entered = scenario in {"changed_interrupted_task", "missing_entered_scope", "repair_after_final"}
            ledger = StepLedger("child", worker_id="claim-child")
            if scenario == "unknown_step":
                step = ledger.prepare("native:1:3:old-query:1:0:unknown", "writer", {}, replay_policy="mutating")
                assert ledger.claim(step.step.key)
            if scenario == "repair_after_final":
                ledger.save_checkpoint(ControllerCheckpoint(run_id="child", phase="native_final"), checkpoint_key=original_key)
            llm = LLM()
            agent = DeepThinkAgent(llm, [], forbidden_tool, request_profile={"current_plan_id": 1}, max_iterations=2)
            kwargs = {"context": {"resume_scope_entered": entered}, "task_context": TaskExecutionContext(task_id=task_id)}
            if scenario in {"new_task", "repair_after_final"}:
                result = await agent.think("a new scope query", **kwargs)
                assert result.execution_issues == [] and llm.calls > 0
                assert ledger.load_checkpoint(checkpoint_key=original_key).phase == (
                    "native_final" if scenario == "repair_after_final" else "native_ready"
                )
            else:
                with pytest.raises(checkpointing.ControllerRestoreError):
                    await agent.think("a new scope query", **kwargs)
                assert llm.calls == 0
        finally:
            chat_run_claim.reset(handle)

    asyncio.run(run())
