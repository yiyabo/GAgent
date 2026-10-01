"""In-place output evidence survives the real native continuation path."""
import asyncio

import pytest

from app.database import get_db, init_db
from app.llm import NativeStreamResult, NativeToolCall
from app.repository import chat_runs
from app.routers.chat.models import ChatRequest
from app.services.chat_run_state import chat_run_claim
from app.services.deep_think import checkpointing
from app.services.deep_think_agent import DeepThinkAgent
from app.services.plans.output_spec import file_snapshot
from app.services.run_resume import prepare_run_resume


def test_in_place_resume_keeps_pre_effect_snapshot_despite_new_input_context(isolated_app_env, monkeypatch):
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO chat_sessions(id,owner_id,name) VALUES('inplace','owner','inplace')")
        conn.commit()
    target = isolated_app_env["runtime_root"] / "config.json"
    target.write_text('{"value":1}')
    before = file_snapshot(target)
    request = ChatRequest(message="Update config.json", session_id="inplace")

    def create(run_id, context=None):
        req = request.model_copy(update={"context": context}) if context else request
        chat_runs.create_chat_run(run_id, "inplace", req.model_dump_json(), owner_id="owner")
        assert chat_runs.claim_chat_run_lease(run_id, "claim-" + run_id)
        assert chat_runs.mark_chat_run_started(run_id, worker_id="claim-" + run_id)

    class LLM:
        def __init__(self, first=False):
            self.first = first

        async def stream_chat_with_tools_async(self, **kwargs):
            if self.first:
                self.first = False
                return NativeStreamResult(content="update configuration", tool_calls=[NativeToolCall(
                    id="update", name="file_operations", arguments={"operation": "write", "path": str(target)},
                )])
            return NativeStreamResult(content="The requested configuration has been updated and verified.", tool_calls=[])

        async def stream_chat_async(self, **kwargs):
            yield "The requested configuration has been updated and verified."

    writes = []

    async def execute(name, params):
        writes.append(1)
        target.write_text('{"value":2}')
        return {"success": True, "produced_files": [str(target)]}

    source = DeepThinkAgent(LLM(True), ["file_operations"], execute, max_iterations=4)
    context = {"output_spec_base_dir": str(target.parent), "output_spec": {
        "required_outputs": [{"kind": "data", "extensions": [".json"], "target_path": "config.json", "in_place": True}],
    }}
    save = checkpointing.save_native_checkpoint

    async def crash(agent, **kwargs):
        if agent is source and kwargs["phase"] == "native_ready" and kwargs["iteration"] == 1:
            raise asyncio.CancelledError("worker disappeared after confirmed write")
        await save(agent, **kwargs)

    monkeypatch.setattr(checkpointing, "save_native_checkpoint", crash)

    async def scenario():
        create("source")
        handle = chat_run_claim.set(("source", "claim-source"))
        try:
            with pytest.raises(asyncio.CancelledError):
                await source.think(request.message, context=context)
        finally:
            chat_run_claim.reset(handle)
        assert chat_runs.mark_chat_run_finished("source", "failed", worker_id="claim-source")
        create("continuation", {"resume_from_run_id": "source"})
        handle = chat_run_claim.set(("continuation", "claim-continuation"))
        try:
            await prepare_run_resume("continuation", {"resume_from_run_id": "source"})
            resumed = DeepThinkAgent(LLM(), ["file_operations"], execute, max_iterations=4)
            # A freshly rebuilt execution context sees the changed file. The
            # checkpoint must override this with the original pre-effect hash.
            result = await resumed.think(request.message, context={
                **context, "output_input_snapshot": {str(target): file_snapshot(target)},
            })
            assert writes == [1]
            assert result.output_input_snapshot[str(target)] == before
            assert result.output_verification["status"] == "passed"
            assert result.execution_issues == []
        finally:
            chat_run_claim.reset(handle)

    asyncio.run(scenario())
