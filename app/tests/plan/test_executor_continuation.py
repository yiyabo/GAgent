"""Real controller/kernel repairs inherit settled work, never redispatch its transcript."""
import json
from pathlib import Path
import pytest

from app.llm import NativeStreamResult, NativeToolCall
from app.services.deep_think.conversation import capture, seed_messages
from app.services.plans.executor_history import save_history, load_history
from app.services.deep_think.checkpointing import ControllerRestoreError
from app.tests.plan.test_executor_task_completion_policy import task_case, _config


class ContinuationProvider:
    model = "qwen3.8-flash"
    def __init__(self, target, native):
        self.target, self.native = target, native
        self.phases = set()
        self.requests = []
    def response(self, messages):
        self.requests.append(messages)
        text = json.dumps(messages)
        phase = 2 if "Repair attempt: 2." in text else 1 if "Repair contract mismatch" in text else 0
        if phase:
            assert "FIRST_WRITE_ALREADY_DONE" in text
        if phase == 2:
            assert "REPAIR_ONE_ALREADY_CHECKED" in text
        code = None
        if phase not in self.phases:
            self.phases.add(phase)
            if phase == 0:
                code = (f"from pathlib import Path\np=Path({str(self.target.parent / 'passed.md')!r})\n"
                        "p.write_text('# Checked evidence\\nCount: 2\\n')\n"
                        f"p=Path({str(self.target.parent / 'writes.txt')!r})\n"
                        "p.write_text(str(int(p.read_text())+1) if p.exists() else '1')\n"
                        "print('FIRST_WRITE_ALREADY_DONE')")
            elif phase == 1:
                code = "print('REPAIR_ONE_ALREADY_CHECKED')"
            else:
                code = f"from pathlib import Path\nPath({str(self.target)!r}).write_text('{{\"count\":2}}')"
        if code:
            return {"thinking": "Use existing evidence to fill the remaining output.",
                    "action": {"tool": "execute_code", "params": {"code": code}}, "final_answer": None}
        return {"thinking": "Submit for independent verification.", "action": None,
                "final_answer": {"answer": "The known evidence has count two; verify the declared files.", "confidence": 0.9}}
    async def stream_chat_async(self, **kwargs):
        yield json.dumps(self.response(kwargs["messages"]))


class NativeContinuationProvider(ContinuationProvider):
    async def stream_chat_with_tools_async(self, **kwargs):
        value = self.response(kwargs["messages"])
        action = value["action"]
        call = (NativeToolCall(f"call_{len(self.requests)}", action["tool"], action["params"])
                if action else NativeToolCall(f"final_{len(self.requests)}", "submit_final_answer", value["final_answer"]))
        return NativeStreamResult(content=value["thinking"], tool_calls=[call], finish_reason="tool_calls")


@pytest.mark.parametrize("native", [True, False])
def test_two_repairs_retain_receipts_and_do_not_repeat_successful_write(task_case, native):
    executor, repo, plan, task, target, _ = task_case(
        filenames=("answer.json", "passed.md"), protocol="native" if native else "strict", produce=False)
    provider = (NativeContinuationProvider if native else ContinuationProvider)(target, native)
    executor._llm._llm = provider
    before = repo.get_node(plan, task).metadata
    result = executor.execute_task(plan, task, config=_config())
    assert result.status == "completed", result.content
    assert result.metadata["repair_attempts"] == 2
    assert json.loads(target.read_text()) == {"count": 2}
    assert (target.parent / "writes.txt").read_text() == "1"
    current = repo.get_node(plan, task).metadata["output_spec"]
    assert current["required_outputs"] == before["output_spec"]["required_outputs"]
    assert current["acceptance_criteria"] == before["output_spec"]["acceptance_criteria"]
    assert result.metadata["execution_history_ref"]["sha256"]
    assert "messages" not in result.metadata


def _history():
    return [{"role": "system", "content": "old"}, {"role": "user", "content": "original goal"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "write", "type": "function",
            "function": {"name": "file_operations", "arguments": '{"operation":"write"}'}}]},
        {"role": "tool", "tool_call_id": "write", "content": "file written once"}]


def test_native_history_pairs_survive_and_are_copied():
    scope = {"plan_id": 1, "task_id": 2, "session_id": "one"}
    context = {"execution_history_scope": scope}
    history = capture(context, messages=_history(), final_answer="Candidate", protocol="native")
    seeded = seed_messages({**context, "execution_continuation": history},
        system_prompt="current repair", user_message="Write only the missing file", protocol="native")
    assert seeded[2]["tool_calls"][0]["function"]["arguments"] == '{"operation":"write"}'
    assert seeded[3]["tool_call_id"] == "write"
    assert seeded[0]["content"] == "current repair"
    seeded[3]["content"] = "changed"
    assert history["messages"][3]["content"] == "file written once"


@pytest.mark.parametrize("change", ["task_id", "plan_id", "session_id", "goal_binding", "inputs"])
def test_different_execution_scope_is_rejected(change):
    scope = {"plan_id": 1, "task_id": 2, "session_id": "one", "goal_binding": "a", "inputs": "a"}
    history = capture({"execution_history_scope": scope}, messages=_history(), final_answer="Done", protocol="native")
    with pytest.raises(ValueError, match="different task"):
        seed_messages({"execution_history_scope": {**scope, change: "other"}, "execution_continuation": history},
            system_prompt="new", user_message="repair", protocol="native")


@pytest.mark.parametrize("messages", [_history()[:-1], _history()[3:],
    [{"role": "user", "content": "x"}, {"role": "system", "content": "wrong place"}]])
def test_unsettled_or_orphaned_history_is_not_replayed(messages):
    with pytest.raises(ValueError):
        capture({"execution_history_scope": {"task_id": 1}}, messages=messages, final_answer="", protocol="native")


def test_strict_and_native_history_cannot_be_mixed():
    history = capture({"execution_history_scope": {"task_id": 1}}, messages=_history(), final_answer="", protocol="native")
    with pytest.raises(ValueError):
        seed_messages({"execution_history_scope": {"task_id": 1}, "execution_continuation": history},
            system_prompt="new", user_message="repair", protocol="strict")


def test_history_reference_restores_and_rejects_corruption(tmp_path):
    scope = {"task_id": 1}
    history = capture({"execution_history_scope": scope}, messages=_history(), final_answer="Candidate", protocol="native")
    reference = save_history(tmp_path, history)
    assert load_history(reference, tmp_path, scope) == history
    assert load_history(reference, tmp_path, {"task_id": 2}) is None
    Path(reference["path"]).write_text("corrupted")
    with pytest.raises(ControllerRestoreError):
        load_history(reference, tmp_path, scope)


def test_chat_has_no_implicit_execution_memory():
    assert capture({}, messages=[{"role": "user", "content": "Explain enzymes"}], final_answer="Answer", protocol="native") is None
    assert seed_messages({}, system_prompt="chat", user_message="Explain enzymes", protocol="native") == [
        {"role": "system", "content": "chat"}, {"role": "user", "content": "Explain enzymes"}]


@pytest.mark.parametrize("native", [True, False])
def test_new_job_restores_failed_task_history_without_repeating_initial_write(task_case, native):
    executor, repo, plan, task, target, _ = task_case(filenames=("answer.json", "passed.md"),
        protocol="native" if native else "strict", produce=False)
    provider = (NativeContinuationProvider if native else ContinuationProvider)(target, native)
    original = provider.response
    def fail_delivery(messages):
        value = original(messages)
        if value["action"] and "write_text('{" in value["action"]["params"]["code"]:
            value["action"]["params"]["code"] = "print('The last check still lacks the final output')"
        return value
    provider.response = fail_delivery
    executor._llm._llm = provider
    first = executor.execute_task(plan, task, config=_config())
    assert first.status == "failed"
    assert first.metadata["execution_history_ref"]
    provider = (NativeContinuationProvider if native else ContinuationProvider)(target, native)
    executor._llm._llm = provider
    result = executor.execute_task(plan, task, config=_config())
    assert result.status == "completed", result.content
    assert (target.parent / "writes.txt").read_text() == "1"
    assert "FIRST_WRITE_ALREADY_DONE" in json.dumps(provider.requests[0])


@pytest.mark.parametrize("budget", [False, True])
def test_execution_intent_does_not_depend_on_budget_switch(monkeypatch, budget):
    from app.services.plans import execution_budget
    from app.services.deep_think_agent import DeepThinkAgent
    monkeypatch.setattr(execution_budget, "enabled", lambda: budget)
    profile = execution_budget.request_profile(1, 2, {})
    agent = DeepThinkAgent(llm_client=object(), available_tools=[], tool_executor=lambda *a: None,
        request_profile=profile)
    assert agent._is_execute_task_request()
    assert agent._loop_guard_endgame_armed()
    assert bool(profile.get("plan_budget_enabled")) == budget


def test_reuse_rechecks_actual_file_and_never_spends_model_calls(task_case):
    from app.services.plans.executor_reuse import completed_result
    executor, repo, plan, task, target, provider = task_case(produce=True)
    assert executor.execute_task(plan, task, config=_config()).status == "completed"
    calls = provider.calls
    result = completed_result(executor, plan, task, _config().session_context)
    assert result and result.metadata["reused"] and result.attempts == 0
    assert provider.calls == calls
    target.unlink()
    assert completed_result(executor, plan, task, _config().session_context) is None


def test_internal_transcript_is_not_a_data_deliverable(tmp_path):
    from app.services.plans.output_spec import parse_output_spec, validate_output_spec
    from app.services.plans.task_verification import TaskVerificationService
    reference = save_history(tmp_path, {"scope": {"task_id": 1}, "messages": [{"role": "user", "content": "x"}]})
    spec = parse_output_spec({"required_outputs": [{"kind": "data", "extensions": [".json"]}]})
    assert TaskVerificationService._is_internal_artifact_path(reference["path"])
    assert validate_output_spec(spec, [reference["path"]], base_dir=tmp_path)["status"] == "failed"


def test_corrupt_saved_history_cannot_restart_work_or_leave_task_running(task_case):
    executor, repo, plan, task, target, provider = task_case(produce=False)
    result = executor.execute_task(plan, task, config=_config())
    assert result.status == "failed"
    Path(result.metadata["execution_history_ref"]["path"]).write_text("broken")
    calls = provider.calls
    with pytest.raises(ControllerRestoreError):
        executor.execute_task(plan, task, config=_config())
    assert provider.calls == calls
    assert repo.get_node(plan, task).status == "failed"


@pytest.mark.parametrize("native", [True, False])
def test_plan_model_calls_honor_shared_thinking_switch(task_case, monkeypatch, native):
    from app.services.foundation.settings import get_settings
    executor, _, plan, task, _, provider = task_case(protocol="native" if native else "strict")
    monkeypatch.setattr(get_settings(), "thinking_enabled", False)
    method = "stream_chat_with_tools_async" if native else "stream_chat_async"
    original = getattr(provider, method)
    controls = []
    if native:
        async def invoke(**kwargs):
            controls.append((kwargs["enable_thinking"], kwargs["thinking_budget"]))
            return await original(**kwargs)
    else:
        async def invoke(**kwargs):
            controls.append((kwargs["enable_thinking"], kwargs["thinking_budget"]))
            async for chunk in original(**kwargs):
                yield chunk
    monkeypatch.setattr(provider, method, invoke)
    assert executor.execute_task(plan, task, config=_config()).status == "completed"
    assert controls and all(control == (False, 8000) for control in controls)
