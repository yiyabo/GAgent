"""The production policy records execution and lets the existing Agent recover inputs."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.foundation.settings import get_settings
from app.services.plans.completion_policy import execution_status, acceptance_enabled
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.task_verification import TaskVerificationService
from app.services.plans.status_resolver import PlanStatusResolver
from app.services.plans import input_discovery
from app.services.deep_think.acceptance import prepare_acceptance_spec, acceptance_missing
from app.services.deep_think.models import TaskExecutionContext
from app.tests.plan.test_executor_task_completion_policy import task_case, _config


@pytest.fixture(autouse=True)
def agent_policy(monkeypatch):
    monkeypatch.setenv("PLAN_ACCEPTANCE_ENABLED", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("raw,meta,expected", [
    ("needs_review", {"execution_status": "completed"}, "completed"),
    ("failed", {"execution_status": "completed", "failure_kind": "contract_mismatch"}, "completed"),
    ("failed", {"execution_status": "failed"}, "failed"),
    ("completed", {"execution_status": "completed", "execution_issues": ["uncertain mutation"]}, "failed"),
    ("completed", {"termination_reason": "run_deadline_exceeded"}, "failed"),
    ("running", {"execution_status": "completed"}, "running"),
    ("pending", {"execution_status": "completed"}, "pending"),
    ("cancelled", {"execution_status": "completed"}, "cancelled"),
])
def test_execution_lifecycle_is_not_quality_approval(raw, meta, expected):
    assert execution_status(raw, meta) == expected


def test_invalid_rule_neither_runs_checks_nor_blocks_a_finished_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    service = TaskVerificationService()
    monkeypatch.setattr(service, "_run_check", lambda *a, **kw: pytest.fail("acceptance must be disabled"))
    node = PlanNode(id=1, plan_id=1, name="Collect", status="needs_review", metadata={
        "acceptance_criteria": {"blocking": True, "checks": [
            {"type": "glob_count_at_least", "path": "corpus.jsonl", "min_count": 80}]},
        "artifact_contract": {"publishes": ["missing.corpus_jsonl"]}})
    original = json.dumps(node.metadata, sort_keys=True)
    final = service.finalize_payload(node, {"content": "Finished this run."}, execution_status="completed")
    assert final.final_status == "completed"
    assert final.payload["metadata"]["verification_status"] == "disabled"
    assert service.apply_artifact_authority(1, node, final, manifest={}).final_status == "completed"
    node.execution_result = json.dumps(final.payload)
    tree = PlanTree(id=1, title="Fixture", nodes={1: node});tree.rebuild_adjacency()
    assert PlanStatusResolver().resolve_plan_states(1, tree)[1]["effective_status"] == "completed"
    assert json.dumps(node.metadata, sort_keys=True) == original


def test_missing_material_can_be_written_without_input_confirmation():
    context = {}
    tree = PlanTree(id=1, title="Fixture")
    node = PlanNode(id=1, plan_id=1, name="Use missing input")
    tree.nodes[1] = node
    assert input_discovery.prepare(node, tree, {}, ["data.notes_md"], context)
    assert input_discovery.guard(context, "file_operations", {"operation": "write", "path": "notes.md"}) is None
    assert input_discovery.guard(context, "execute_code", {"code": "print('recover')"}) is None
    text = input_discovery.prompt(context)
    assert "produce the necessary supplement" in text and "Only the user" in text
    assert "Resolve all missing inputs before" not in text


def test_plan_loop_has_no_acceptance_extension_but_ordinary_chat_keeps_its_spec(tmp_path):
    agent = SimpleNamespace(request_profile={})
    supplied = {"output_spec": {"required_outputs": [{"kind": "document", "target_path": "answer.md"}]},
                "output_spec_base_dir": str(tmp_path)}
    assert asyncio.run(prepare_acceptance_spec(agent, "write answer.md", {**supplied, "plan_id": 1, "task_id": 1},
        TaskExecutionContext(task_id=1))) is None
    assert acceptance_missing(agent, ["document"], []) == []
    assert asyncio.run(prepare_acceptance_spec(agent, "write answer.md", supplied)) is not None
    assert acceptance_missing(agent, ["document"], [])


@pytest.mark.parametrize("protocol", ["native", "strict"])
@pytest.mark.parametrize("produce", [False, True])
def test_real_executor_finishes_once_without_acceptance_or_repair(task_case, protocol, produce):
    executor, repo, plan, task, target, provider = task_case(protocol=protocol, produce=produce)
    before = repo.get_plan_tree(plan).nodes[task]
    result = executor.execute_task(plan, task, config=_config(contract_repair_attempts=3))
    assert result.status == "completed", result.to_dict()
    assert result.metadata["execution_policy"] == "agent"
    assert result.metadata["verification_status"] == "disabled"
    assert executor._contract_repair_limit() == 0
    assert provider.calls == 2
    after = repo.get_plan_tree(plan).nodes[task]
    assert (after.instruction, after.parent_id) == (before.instruction, before.parent_id)
    assert target.exists() is produce


def test_default_policy_is_disabled_without_environment_override(monkeypatch):
    monkeypatch.delenv("PLAN_ACCEPTANCE_ENABLED")
    get_settings.cache_clear()
    assert acceptance_enabled() is False


@pytest.mark.parametrize('composite', [False, True])
def test_downstream_agent_can_supplement_unpublished_input_without_tree_edits(task_case, composite):
    executor, repo, plan, task, target, provider = task_case()
    group = repo.create_task(plan, name='Existing stage', instruction='Stage of completed work') if composite else None
    upstream = repo.create_task(plan, name="Upstream notes", instruction="Provide existing notes",
                               parent_id=group.id if group else None)
    repo.update_task(plan, upstream.id, status="needs_review", execution_result=json.dumps({
        "status": "needs_review", "metadata": {"execution_status": "completed", "verification_status": "config_error"}}))
    dependency_id = group.id if group else upstream.id
    node = repo.get_plan_tree(plan).nodes[task]
    repo.update_task(plan, task, dependencies=[dependency_id], metadata={**node.metadata,
        "artifact_contract": {"requires": ["notes.unpublished_md"]}})
    material = target.parent / "existing_material.json"
    material.parent.mkdir(parents=True, exist_ok=True)
    material.write_text('{"count":2}')
    supplement = target.parent / "recovered_notes.md"
    original_response = provider.next_response
    def next_response(messages):
        result = original_response(messages)
        if result.get('action'):
            result['action']['params']['code'] = (
                'import json\nfrom pathlib import Path\n'
                f'data=json.loads(Path({str(material)!r}).read_text())\n'
                f'Path({str(supplement)!r}).write_text("Recovered count: " + str(data["count"]))\n'
                f'Path({str(target)!r}).write_text(json.dumps(data))')
        return result
    provider.next_response = next_response
    before = repo.get_plan_tree(plan)
    result = executor.execute_task(plan, task, config=_config())
    after = repo.get_plan_tree(plan)
    assert result.status == "completed", result.to_dict()
    assert provider.calls == 2 and supplement.read_text() == 'Recovered count: 2'
    assert json.loads(target.read_text()) == {'count': 2}
    assert set(before.nodes) == set(after.nodes)
    for key in before.nodes:
        a, b = before.nodes[key], after.nodes[key]
        assert (a.instruction, a.parent_id, a.dependencies) == (b.instruction, b.parent_id, b.dependencies)


def test_code_lane_removes_plan_checks_but_preserves_standalone_contract():
    from app.services.plans.completion_policy import code_spec
    from tool_box.tools_impl.code_executor_contracts import _build_cli_task_contract
    supplied = {'plan_id': 1, 'task_id': 2, 'acceptance_criteria': {'checks': [{'type': 'invalid'}]},
                'dependency_blockers': [{'task_id': 1}], 'dependency_outputs': []}
    new = code_spec(supplied)
    assert new['acceptance_criteria'] is None and new['dependency_blockers'] == []
    assert supplied['acceptance_criteria']['checks']
    assert 'produce the necessary supplement' in _build_cli_task_contract('Do the assigned work', new)
    assert code_spec({'plan_id': None, 'task_id': None, 'acceptance_criteria': {'checks': []}})['acceptance_criteria'] is not None


def test_task_api_projects_old_acceptance_result_without_rewriting_raw_history():
    from app.routers.plan_routes.effective_state import _parse_execution_result
    raw = {'status': 'needs_review', 'content': 'Original report', 'metadata': {'verification_status': 'config_error'}}
    content, _, metadata, original = _parse_execution_result(json.dumps(raw))
    assert content == 'Original report' and metadata['execution_policy'] == 'agent'
    assert original == raw and 'execution_policy' not in raw['metadata']


def test_standalone_verification_remains_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    node = PlanNode(id=0, plan_id=0, name='Standalone', metadata={'acceptance_criteria': {
        'blocking': True, 'checks': [{'type': 'file_exists', 'path': 'missing.md'}]}})
    result = TaskVerificationService().finalize_payload(node, {'status': 'completed'}, execution_status='completed')
    assert result.final_status == 'failed'
