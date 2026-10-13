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
    def __init__(self, target, *, produce=True, extra_targets=()):
        self.target = target
        self.extra_targets = tuple(extra_targets)
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
            targets = (self.target, *self.extra_targets)
            code = "from pathlib import Path\n" + "\n".join(
                f"Path({str(path)!r}).write_text({('# Reused evidence\\nCount: 2\\n' if path.suffix == '.md' else 'item,count\\nsource,2\\n' if path.suffix == '.csv' else '{\"count\":2}')!r})"
                for path in targets
            ) if self.produce else "print('No output produced')"
            return {"thinking": "Produce the requested output using Python.", "action": {"tool": "execute_code", "params": {"code": code}}, "final_answer": None}
        return {"thinking": "Deliver the output for independent verification.", "action": None,
                "final_answer": {"answer": "Produced " + ", ".join(f"[{path.name}]({path})" for path in (self.target, *self.extra_targets)) + ". The task outputs are available for inspection.", "confidence": 1.0}}

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

    def create(*, protocol="native", produce=True, setting_limit=2, filenames=("answer.json",)):
        repo = PlanRepository()
        plan = repo.create_plan("Completion policy", owner="tester")
        node = repo.create_task(plan.id, name="Produce answer", instruction="Use Python to write the declared outputs from existing evidence with a count of two.")
        target = path_router.get_path_router().get_task_output_dir_from_tree(
            "completion-policy", node.id, repo.get_plan_tree(plan.id), create=True,
        ) / filenames[0]
        targets = [target.parent / filename for filename in filenames]
        spec = {"source": "explicit", "required_outputs": [
            {"kind": "document" if path.suffix == ".md" else "data", "extensions": [path.suffix], "target_path": str(path)}
            for path in targets
        ]}
        repo.update_task(plan.id, node.id, metadata={
            "output_spec": spec,
            **({"acceptance_criteria": {"blocking": True, "checks": [
                {"type": "file_nonempty", "path": str(path)} for path in targets
            ]}} if filenames != ("answer.json",) else {}),
        })
        provider = (_NativeProvider if protocol == "native" else _StrictProvider)(
            target, produce=produce, extra_targets=targets[1:])
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
        self.queries = []
        self.limits = []
        self.max_iterations = 152

    async def think(self, query, context=None, task_context=None):
        self.calls.append(context.get("contract_repair") if context else None)
        self.queries.append(query)
        self.limits.append(self.max_iterations)
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


def test_repair_feedback_is_specific_and_second_attempt_changes_method(task_case):
    executor,repo,plan,task,_,_=task_case(setting_limit=2)
    node=repo.get_node(plan,task)
    final,_=executor._finalize_task_execution(plan,node,{'status':'success','content':'Partial',
        'metadata':{'tool_failures':[{'error':"KeyError: summary"}]}},execution_status='completed')
    agent=_RepairAgent()
    result=executor._attempt_contract_repair_with_deep_think(plan_id=plan,node=node,
        task_context=TaskExecutionContext(task_id=task),session_context={},deep_think_agent=agent,
        finalization=final,tool_result_context={})
    assert 'answer.json' in agent.queries[0] and 'KeyError: summary' in agent.queries[0]
    assert 'simpler method' in agent.queries[1]
    assert agent.limits==[12,12] and agent.max_iterations==152
    assert result.final_status=='failed' and len(result.payload['metadata']['repair_history'])==2


def test_repair_respects_existing_deadline_without_opening_another_attempt(task_case):
    from app.services.cancellation import CancelToken,set_cancel_token,reset_cancel_token
    from app.services.run_budget import RunDeadlineExceeded
    executor,repo,plan,task,_,_=task_case();node=repo.get_node(plan,task)
    final,_=executor._finalize_task_execution(plan,node,{'status':'success','content':'Missing','metadata':{}},execution_status='completed')
    token=CancelToken();token.set('run_deadline_exceeded');handle=set_cancel_token(token);agent=_RepairAgent()
    try:
        with pytest.raises(RunDeadlineExceeded):
            executor._attempt_contract_repair_with_deep_think(plan_id=plan,node=node,
                task_context=TaskExecutionContext(task_id=task),session_context={},deep_think_agent=agent,
                finalization=final,tool_result_context={})
        assert agent.calls==[] and agent.max_iterations==152
    finally:
        reset_cancel_token(handle)


def test_mutated_invalid_config_is_rejected_before_initial_provider_call(task_case):
    executor, repo, plan, task, target, provider = task_case()
    config = _config()
    config.contract_repair_attempts = -1
    with pytest.raises(ValueError, match="contract_repair_attempts"):
        executor.execute_task(plan, task, config=config)
    assert provider.calls == 0 and not target.exists()


@pytest.mark.parametrize('protocol',['native','strict'])
def test_targeted_repair_uses_real_controller_and_kernel_after_failed_verification(task_case,monkeypatch,protocol):
    executor,repo,plan,task,target,provider=task_case(protocol=protocol,produce=False)
    original=provider.next_response;state={'repair':False,'initial_calls':0}
    def response(messages):
        if 'Repair contract mismatch' in json.dumps(messages) and not state['repair']:
            state.update(repair=True,initial_calls=provider.calls)
            provider.calls=0;provider.produce=True
        return original(messages)
    monkeypatch.setattr(provider,'next_response',response)
    result=executor.execute_task(plan,task,config=_config())
    assert result.status=='completed',(state,provider.calls,target.exists(),result.metadata.get('repair_history'),result.metadata.get('failure_kind'),result.metadata.get('output_verification'))
    assert state['repair'] and state['initial_calls']>0
    assert json.loads(target.read_text())=={'count':2}
    assert result.metadata['repair_attempts']==1
    assert result.metadata['output_verification']['status']=='passed'
    assert repo.get_node(plan,task).status=='completed'


@pytest.mark.parametrize("protocol", ["native", "strict"])
@pytest.mark.parametrize("filenames", [("answer.md",), ("answer.md", "answer.json")])
def test_next_run_repairs_prior_missing_outputs_from_existing_evidence(task_case, monkeypatch, protocol, filenames):
    import app.services.plans.plan_executor as facade

    executor, repo, plan, task, target, provider = task_case(
        protocol=protocol, filenames=filenames)
    evidence = target.parent / "source_notes.txt"
    evidence.write_text("Existing checked evidence: source count is two.\n")
    node = repo.get_node(plan, task)
    old, _ = executor._finalize_task_execution(
        plan, node, {"status": "success", "content": "Analysis done; files missing",
                     "artifact_paths": [str(evidence)], "metadata": {}},
        execution_status="completed",
    )
    assert old.final_status == "failed"
    assert len(old.payload["metadata"]["contract_diff"]["missing_required_outputs"]) == len(filenames), (
        old.payload["metadata"]["contract_diff"], old.payload["metadata"].get("output_verification"))
    repo.update_task(plan, task, status="failed", execution_result=json.dumps(old.payload))
    from app.services.plans.executor_resume_repair import previous_delivery_gap
    assert previous_delivery_gap(repo.get_node(plan, task)) is not None

    real_agent = facade.DeepThinkAgent
    offered = []
    def capture_agent(**kwargs):
        offered.extend(kwargs["available_tools"])
        return real_agent(**kwargs)
    monkeypatch.setattr(facade, "DeepThinkAgent", capture_agent)
    real_response = provider.next_response
    prompts = []
    def capture_response(messages):
        prompts.append(json.dumps(messages, ensure_ascii=False))
        return real_response(messages)
    monkeypatch.setattr(provider, "next_response", capture_response)

    result = executor.execute_task(plan, task, config=_config())
    assert result.status == "completed", (
        result.status, provider.calls, prompts[:1], result.metadata.get("repair_history"),
        result.metadata.get("output_verification"),
        [(filename, (target.parent / filename).exists()) for filename in filenames],
    )
    assert result.metadata["resumed_delivery_gap"] is True
    assert result.metadata["repair_attempts"] == 1
    assert 2 <= provider.calls <= 4  # write + final; native may add file-profile checks
    assert "Repair contract mismatch" in prompts[0]
    assert "source_notes.txt" in prompts[0]
    assert "web_search" not in offered and "literature_pipeline" not in offered
    assert "scientific_figure_generator" in offered
    assert all((target.parent / filename).is_file() for filename in filenames)
    assert result.metadata["output_verification"]["status"] == "passed"


def test_prior_missing_output_without_reusable_evidence_keeps_normal_execution(task_case, monkeypatch):
    import app.services.plans.plan_executor as facade

    executor, repo, plan, task, target, provider = task_case(filenames=("answer.md",))
    node = repo.get_node(plan, task)
    old, _ = executor._finalize_task_execution(
        plan, node, {"status": "success", "content": "File missing", "metadata": {}},
        execution_status="completed",
    )
    repo.update_task(plan, task, status="failed", execution_result=json.dumps(old.payload))
    real_agent = facade.DeepThinkAgent
    offered = []
    def capture_agent(**kwargs):
        offered.extend(kwargs["available_tools"])
        return real_agent(**kwargs)
    monkeypatch.setattr(facade, "DeepThinkAgent", capture_agent)

    result = executor.execute_task(plan, task, config=_config())
    assert result.status == "completed", result.to_dict()
    assert result.metadata.get("resumed_delivery_gap") is not True
    assert "web_search" in offered
    assert provider.calls == 2


def test_previous_gap_uses_completed_dependency_files_when_task_has_no_prior_files(task_case):
    from app.services import path_router

    executor, repo, plan, task, target, provider = task_case(
        protocol="strict", filenames=("answer.md", "answer.json"))
    dependency = repo.create_task(plan, name="Collected evidence", instruction="Evidence already collected.")
    source_dir = path_router.get_path_router().get_task_output_dir_from_tree(
        "completion-policy", dependency.id, repo.get_plan_tree(plan), create=True)
    source = source_dir / "evidence.txt"
    source.write_text("Source count: two.\n")
    repo.update_task(plan, dependency.id, status="completed", execution_result=json.dumps({
        "status": "completed", "content": "Source ready", "artifact_paths": [str(source)],
    }))
    repo.update_task(plan, task, dependencies=[dependency.id])
    node = repo.get_node(plan, task)
    old, _ = executor._finalize_task_execution(
        plan, node, {"status": "success", "content": "Outputs missing", "metadata": {}},
        execution_status="completed",
    )
    repo.update_task(plan, task, status="failed", execution_result=json.dumps(old.payload))
    result = executor.execute_task(plan, task, config=_config())
    assert result.status == "completed", result.to_dict()
    assert result.metadata["resumed_delivery_gap"] is True
    assert result.metadata["repair_attempts"] == 1
    assert provider.calls == 2
    assert target.is_file() and (target.parent / "answer.json").is_file()


def test_previous_gap_is_not_reused_after_output_contract_changes(task_case):
    from app.services.plans.executor_resume_repair import previous_delivery_gap

    executor, repo, plan, task, target, _ = task_case(filenames=("answer.md",))
    node = repo.get_node(plan, task)
    old, _ = executor._finalize_task_execution(
        plan, node, {"status": "success", "content": "Missing answer.md", "metadata": {}},
        execution_status="completed",
    )
    repo.update_task(plan, task, status="failed", execution_result=json.dumps(old.payload))
    assert previous_delivery_gap(repo.get_node(plan, task)) is not None
    changed = dict(repo.get_node(plan, task).metadata)
    changed["output_spec"] = {"source": "explicit", "required_outputs": [
        {"kind": "document", "extensions": [".md"], "target_path": str(target.parent / "new_request.md")}
    ]}
    repo.update_task(plan, task, metadata=changed)
    assert previous_delivery_gap(repo.get_node(plan, task)) is None


def test_explicit_zero_disables_previous_gap_resume(task_case):
    executor, repo, plan, task, target, provider = task_case(protocol="strict", filenames=("answer.md",))
    evidence = target.parent / "source_notes.txt"
    evidence.write_text("Existing count: two.\n")
    node = repo.get_node(plan, task)
    old, _ = executor._finalize_task_execution(
        plan, node, {"status": "success", "content": "Missing answer.md",
                     "artifact_paths": [str(evidence)], "metadata": {}},
        execution_status="completed",
    )
    repo.update_task(plan, task, status="failed", execution_result=json.dumps(old.payload))
    result = executor.execute_task(plan, task, config=_config(contract_repair_attempts=0))
    assert result.status == "completed" and provider.calls == 2
    assert result.metadata.get("resumed_delivery_gap") is not True


@pytest.mark.parametrize('backend',['legacy','external'])
def test_other_backends_enter_the_same_local_repair_without_repeating_initial_work(task_case,monkeypatch,backend):
    from app.services.plans.executor_models import ExecutionResponse
    from app.services.plans.task_delegate_executor import TaskDelegationResult
    executor,repo,plan,task,target,provider=task_case(produce=True)
    calls=[]
    if backend=='legacy':
        monkeypatch.setattr(executor,'_should_use_deep_think',lambda config:False)
        def generate(*a,**kw):
            calls.append(1)
            return ExecutionResponse(status='success',content='Initial attempt did not write the file')
        executor._llm.generate=generate
    else:
        monkeypatch.setattr(executor,'_should_delegate_plan_task',lambda config:True)
        def delegate(*a,**kw):
            calls.append(1)
            return TaskDelegationResult(status='completed',summary='Delegate omitted the requested file')
        monkeypatch.setattr(executor._task_delegate_executor,'execute',delegate)
    result=executor.execute_task(plan,task,config=_config())
    assert result.status=='completed',result.to_dict()
    assert calls==[1] and json.loads(target.read_text())=={'count':2}
    assert result.metadata['contract_repair'] is True
