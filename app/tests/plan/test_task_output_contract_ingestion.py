"""Declared outputs survive every repository entry and govern task completion."""
import json
from types import SimpleNamespace

import pytest

from app.database import init_db
from app.repository.plan_repository import PlanRepository
from app.services.plans import task_contracts
from app.services.plans.output_spec import InvalidOutputSpec, output_spec_from_metadata
from app.services.plans.plan_generation import _create_seed_tasks
from app.services.plans.task_verification import TaskVerificationService
from app.services.plans.status_resolver import PlanStatusResolver


@pytest.fixture
def project(isolated_app_env, monkeypatch):
    init_db()
    policy = SimpleNamespace(plan_output_contracts_required=True)
    monkeypatch.setattr(task_contracts, 'get_settings', lambda: policy)
    repo = PlanRepository()
    plan = repo.create_plan(title='contract test')
    root = repo.create_task(plan.id, name='root', metadata={'is_root': True})
    return repo, plan.id, root.id, policy


def declaration(path='results/clean.csv'):
    return {'required_outputs': [{'kind':'data', 'extensions':['.csv'], 'target_path':path}]}


def test_seed_and_direct_repository_tasks_keep_declarations_and_unique_aliases(project):
    repo, plan, root, _ = project
    seed = _create_seed_tasks(repo, plan, root_task_id=root, tasks=[
        {'name':'clean', 'instruction':'clean data', **declaration()}])[0]
    direct = repo.create_task(plan, name='second', parent_id=root, metadata=declaration())
    for node in (seed, direct):
        spec = output_spec_from_metadata(node.metadata)
        assert spec.authoritative
        assert spec.required_outputs[0].target_path == 'results/clean.csv'
        assert node.metadata['delivery_contract_required']
        assert node.metadata['artifact_contract']['publishes'] == [f'task_{node.id}.clean_csv']
    assert seed.metadata['artifact_contract'] != direct.metadata['artifact_contract']


def test_bad_explicit_declaration_rolls_back_creation(project):
    repo, plan, root, _ = project
    before = set(repo.get_plan_tree(plan).nodes)
    with pytest.raises(InvalidOutputSpec):
        repo.create_task(plan, name='invalid', parent_id=root, metadata={'required_outputs':'a csv'})
    assert set(repo.get_plan_tree(plan).nodes) == before


def test_explicit_update_replaces_old_envelope_and_policy_survives_rollout_off(project):
    repo, plan, root, policy = project
    node = repo.create_task(plan, name='work', parent_id=root, metadata=declaration('old.csv'))
    metadata = {**node.metadata, **declaration('new.csv')}
    changed = repo.update_task(plan, node.id, metadata=metadata)
    assert output_spec_from_metadata(changed.metadata).required_outputs[0].target_path == 'new.csv'
    assert changed.metadata['artifact_contract']['publishes'] == [f'task_{node.id}.new_csv']
    policy.plan_output_contracts_required = False
    changed = repo.update_task(plan, node.id, metadata={})
    assert changed.metadata['delivery_contract_required'] is True
    legacy = repo.create_task(plan, name='legacy', parent_id=root)
    assert not legacy.metadata.get('delivery_contract_required')


def test_empty_contract_cannot_turn_successful_tool_prose_into_completed(project, monkeypatch, tmp_path):
    repo, plan, root, _ = project
    node = repo.create_task(plan, name='Interpret result', parent_id=root)
    monkeypatch.chdir(tmp_path)
    result = TaskVerificationService().finalize_payload(node,
        {'status':'completed', 'content':'An interpretation is available.', 'metadata':{'tools_used':['execute_code']}},
        execution_status='completed')
    assert result.final_status == 'needs_review'
    assert result.payload['metadata']['verification_status'] == 'unchecked'
    repo.update_task(plan, node.id, status=result.final_status, execution_result=json.dumps(result.payload))
    state = PlanStatusResolver().resolve_plan_states(plan, repo.get_plan_tree(plan))[node.id]
    assert state['effective_status'] == 'needs_review'


def test_actual_file_declaration_fails_when_missing_and_passes_when_present(project, tmp_path, monkeypatch):
    repo, plan, root, _ = project
    node = repo.create_task(plan, name='Write clean table', parent_id=root, metadata=declaration('clean.csv'))
    monkeypatch.chdir(tmp_path)
    verifier = TaskVerificationService()
    payload = {'status':'completed', 'content':'Created the clean table.',
               'metadata': {'output_spec_base_dir':str(tmp_path)}, 'artifact_paths':[str(tmp_path/'clean.csv')]}
    missing = verifier.finalize_payload(node, payload, execution_status='completed')
    assert missing.final_status == 'failed'
    (tmp_path/'clean.csv').write_text('sample,score\nS1,1\n')
    checked = verifier.finalize_payload(node, payload, execution_status='completed')
    assert checked.final_status == 'completed'
    assert checked.payload['metadata']['output_verification']['authoritative'] is True


def test_legacy_inferred_rules_are_not_promoted_to_declared_acceptance(project):
    repo, plan, root, _ = project
    node = repo.create_task(plan, name='legacy inference', parent_id=root, metadata={
        'acceptance_criteria':{'source':'inferred_text','checks':[{'type':'file_exists','path':'result.csv'}]}})
    assert not task_contracts.has_declared_acceptance(node.metadata)


def test_repository_preserves_inference_provenance_and_resources(project):
    repo, plan, root, policy = project
    policy.plan_output_contracts_required = False
    contract = {'source':'inferred_text', 'publishes':['output.result_csv'], 'required_resources':['reference.data']}
    node = repo.create_task(plan, name='legacy', parent_id=root, metadata={'artifact_contract':contract})
    assert node.metadata['artifact_contract'] == contract
    assert not task_contracts.has_declared_acceptance(node.metadata)
    updated = repo.update_task(plan, node.id, metadata=node.metadata)
    assert updated.metadata['artifact_contract'] == contract


def test_verified_output_publishes_through_real_executor_manifest(project, isolated_app_env):
    from pathlib import Path
    from app.database import get_db
    from app.services.plans.plan_executor import PlanExecutor
    from app.services.plans.artifact_contracts import load_artifact_manifest

    repo, plan, root, _ = project
    session = 'session_contract'
    with get_db() as conn:
        conn.execute('INSERT INTO chat_sessions(id,owner_id,plan_id) VALUES(?,?,?)', (session,'legacy-local',plan))
    node = repo.create_task(plan, name='Clean data', parent_id=root, metadata=declaration('clean.csv'))
    task_dir = isolated_app_env['runtime_root']/session/'raw_files'/f'task_{root}'/f'task_{node.id}'
    task_dir.mkdir(parents=True)
    data = task_dir/'clean.csv'
    data.write_text('sample,score\nS1,1\nS2,2\n')
    executor = PlanExecutor(repo=repo)
    payload = {'status':'completed', 'content':'Clean table generated and checked.',
               'artifact_paths':[str(data)], 'metadata':{'output_spec_base_dir':str(task_dir)}}
    verified, _ = executor._finalize_task_execution(plan, node, payload, execution_status='completed')
    published, _ = executor._materialize_finalization(plan, node, verified,
        session_context={'session_id':session,'owner_id':'legacy-local'})
    alias = f'task_{node.id}.clean_csv'
    manifest = load_artifact_manifest(plan, session)
    assert published.final_status == 'completed'
    assert manifest['artifacts'][alias]['producer_task_id'] == node.id
    assert Path(manifest['artifacts'][alias]['path']).read_bytes() == data.read_bytes()
    assert alias in published.payload['metadata']['artifact_authority']['published_aliases']
