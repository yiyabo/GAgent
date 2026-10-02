"""Task completion uses the controller protocol and per-run repair limits."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from app.config.executor_config import get_executor_settings
from app.database import get_db, init_db
from app.llm import LLMClient, NativeStreamResult, NativeToolCall
from app.repository.plan_repository import PlanRepository
from app.services.deep_think_agent import DeepThinkResult, TaskExecutionContext
from app.services.plans.plan_executor import ExecutionConfig, PlanExecutor


def _missing_result():
    return DeepThinkResult(final_answer="The requested answer.json has not been produced.",
        thinking_steps=[], total_iterations=1, tools_used=[], confidence=0.0,
        thinking_summary="No output file was produced.")


class _StrictProvider:
    def __init__(self, target, *, produce=True):
        self.target = target
        self.produce = produce
        self.calls = 0

    def next_response(self, messages):
        text = json.dumps(messages, ensure_ascii=False)
        # The normal "do not submit prematurely" guard remains valid. Reject
        # the old unconditional task-mode ban, which contradicted termination.
        assert "Do NOT call submit_final_answer —" not in text
        assert "submit_final_answer" in text and "PlanExecutor" in text
        self.calls += 1
        if self.calls == 1:
            code = f"from pathlib import Path\nPath({str(self.target)!r}).write_text('{{\"count\":2}}')" if self.produce else "print('No output produced')"
            return {"thinking": "Produce the requested output using Python.", "action": {"tool": "execute_code", "params": {"code": code}}, "final_answer": None}
        return {"thinking": "Deliver the output for independent verification.", "action": None,
                "final_answer": {"answer": f"Produced [answer.json]({self.target}) containing the requested count. The task output is available for inspection.", "confidence": 1.0}}

    async def stream_chat_async(self, **kwargs):
        yield json.dumps(self.next_response(kwargs["messages"]))


class _NativeProvider(_StrictProvider):
    async def stream_chat_with_tools_async(self, **kwargs):
        value = self.next_response(kwargs["messages"])
        if value["action"]:
            action = value["action"]
            call = NativeToolCall("write", action["tool"], action["params"])
        else:
            call = NativeToolCall("finish", "submit_final_answer", value["final_answer"])
        return NativeStreamResult(content=value["thinking"], tool_calls=[call], finish_reason="tool_calls")


@pytest.fixture
def task_case(isolated_app_env, monkeypatch):
    from app.services import path_router, tool_schemas
    from app.services.deliverables import publisher
    from tool_box import integration, tools
    from tool_box.tool_registry import register_all_tools
    from tool_box.tools_impl.execute_code.kernel import shutdown_kernels_for_session
    for flag in ("AGENT_RUNTIME_V2_ENABLED", "ARTIFACT_VERSIONING_ENABLED", "SKILL_RECOMMENDATION_V2_ENABLED", "SKILL_CONTEXT_PROGRESSIVE_ENABLED"):
        monkeypatch.setenv(flag, "0")
    monkeypatch.setenv("CODE_MODE_ENABLED", "1")
    monkeypatch.setenv("CHAT_RUN_SYNTHESIS_RESERVE_SECONDS", "0")
    monkeypatch.setattr(path_router, "_default_router", None)
    monkeypatch.setattr(tool_schemas, "_TOOL_REGISTRY_CACHE", None)
    monkeypatch.setattr(tools, "_tool_registry", tools.ToolRegistry())
    monkeypatch.setattr(integration, "_toolbox_integration", integration.ToolBoxIntegration())
    register_all_tools()
    monkeypatch.setattr(publisher, "_publisher", publisher.DeliverablePublisher(
        project_root=isolated_app_env["runtime_root"].parent,
        runtime_dir=isolated_app_env["runtime_root"],
    ))
    network_calls = []

    def no_model(*args, **kwargs):
        network_calls.append(1)
        raise AssertionError("Only the scripted provider may be called")

    for name in ("chat", "chat_async", "stream_chat", "stream_chat_async", "stream_chat_with_tools_async"):
        monkeypatch.setattr(LLMClient, name, no_model)
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO chat_sessions(id,owner_id,name) VALUES('completion-policy','tester','completion')")
        conn.commit()

    def create(*, protocol="native", produce=True, setting_limit=2):
        repo = PlanRepository()
        plan = repo.create_plan("Completion policy", owner="tester")
        node = repo.create_task(plan.id, name="Produce answer", instruction="Use Python to write answer.json with a numeric count of two.")
        target = path_router.get_path_router().get_task_output_dir_from_tree(
            "completion-policy", node.id, repo.get_plan_tree(plan.id), create=True,
        ) / "answer.json"
        spec = {"source": "explicit", "required_outputs": [{"kind": "data", "extensions": [".json"], "target_path": str(target)}]}
        repo.update_task(plan.id, node.id, metadata={"output_spec": spec})
        provider = (_NativeProvider if protocol == "native" else _StrictProvider)(target, produce=produce)
        settings = replace(get_executor_settings(), plan_task_execution_backend="internal",
                           contract_repair_attempts=setting_limit, deep_think_max_iterations=4)
        executor = PlanExecutor(repo=repo, settings=settings, llm_service=SimpleNamespace(_llm=provider))
        return executor, repo, plan.id, node.id, target, provider

    yield create
    shutdown_kernels_for_session("completion-policy")
    assert network_calls == []


def _config(**kwargs):
    return ExecutionConfig(session_context={"session_id": "completion-policy", "owner_id": "tester", "memory_enabled": False},
                           enable_skills=False, **kwargs)


@pytest.mark.parametrize("protocol", ["native", "strict"])
@pytest.mark.parametrize("produce", [False, True])
def test_real_controller_completion_protocol_does_not_bypass_output_verification(task_case, protocol, produce):
    executor, repo, plan, task, target, provider = task_case(protocol=protocol, produce=produce)
    result = executor.execute_task(plan, task, config=_config(contract_repair_attempts=0))
    assert result.status == ("completed" if produce else "failed"), result.to_dict()
    assert repo.get_plan_tree(plan).nodes[task].status == result.status
    assert result.metadata["output_verification"]["status"] == ("passed" if produce else "failed")
    if produce:
        assert json.loads(target.read_text()) == {"count": 2}
        assert provider.calls == 2  # write, then explicit controller completion
    else:
        assert not target.exists()


class _RepairAgent:
    def __init__(self, *args, **kwargs):
        self.calls = []

    async def think(self, query, context=None, task_context=None):
        self.calls.append(context.get("contract_repair") if context else None)
        return _missing_result()


@pytest.mark.parametrize("override,setting,expected", [(0, 2, 0), (1, 3, 1), (3, 1, 3), (None, 2, 2)])
def test_actual_repair_helper_honors_zero_override_and_settings_inheritance(task_case, override, setting, expected):
    executor, repo, plan, task, target, provider = task_case(setting_limit=setting)
    node = repo.get_plan_tree(plan).nodes[task]
    finalization, _ = executor._finalize_task_execution(plan, node,
        {"status": "success", "content": "Output is missing", "metadata": {}}, execution_status="completed")
    assert finalization.payload["metadata"]["failure_kind"] == "contract_mismatch"
    agent = _RepairAgent()
    result = executor._attempt_contract_repair_with_deep_think(
        plan_id=plan, node=node, task_context=TaskExecutionContext(task_id=task),
        session_context={}, deep_think_agent=agent, finalization=finalization,
        tool_result_context={}, config=_config(contract_repair_attempts=override),
    )
    assert result.final_status == "failed"
    assert len(agent.calls) == expected
    assert [call["attempt"] for call in agent.calls] == list(range(1, expected + 1))
    assert all(call["max_attempts"] == expected for call in agent.calls)
    assert provider.calls == 0


@pytest.mark.parametrize("override,expected", [(0, 1), (1, 2), (None, 3)])
def test_execute_task_passes_per_run_policy_to_repair_via_facade(task_case, monkeypatch, override, expected):
    import app.services.plans.plan_executor as facade
    executor, repo, plan, task, target, provider = task_case(setting_limit=2)
    agent = _RepairAgent()
    monkeypatch.setattr(facade, "DeepThinkAgent", lambda **kwargs: agent)
    result = executor.execute_task(plan, task, config=_config(contract_repair_attempts=override))
    assert result.status == "failed"
    assert len(agent.calls) == expected and agent.calls[0] is None
    assert provider.calls == 0


@pytest.mark.parametrize("setting_limit", [0, 2])
def test_helper_without_config_inherits_settings_and_from_settings_remains_explicit(task_case, setting_limit):
    executor, repo, plan, task, target, provider = task_case(setting_limit=setting_limit)
    node = repo.get_plan_tree(plan).nodes[task]
    finalization, _ = executor._finalize_task_execution(plan, node,
        {"status": "success", "content": "Missing", "metadata": {}}, execution_status="completed")
    agent = _RepairAgent()
    result = executor._attempt_contract_repair_with_deep_think(
        plan_id=plan, node=node, task_context=TaskExecutionContext(task_id=task),
        session_context={}, deep_think_agent=agent, finalization=finalization, tool_result_context={},
    )
    assert result.final_status == "failed"
    assert len(agent.calls) == setting_limit
    assert all(call["max_attempts"] == setting_limit for call in agent.calls)
    assert ExecutionConfig().contract_repair_attempts is None
    assert ExecutionConfig.from_settings(executor._settings).contract_repair_attempts == setting_limit


@pytest.mark.parametrize("value", [-1, 4, True, False, 1.5, "1", "invalid"])
def test_invalid_per_run_limit_is_rejected(value):
    with pytest.raises(ValueError, match="contract_repair_attempts"):
        ExecutionConfig(contract_repair_attempts=value)


def test_mutated_invalid_config_is_rejected_before_initial_provider_call(task_case):
    executor, repo, plan, task, target, provider = task_case()
    config = _config()
    config.contract_repair_attempts = -1
    with pytest.raises(ValueError, match="contract_repair_attempts"):
        executor.execute_task(plan, task, config=config)
    assert provider.calls == 0 and not target.exists()
