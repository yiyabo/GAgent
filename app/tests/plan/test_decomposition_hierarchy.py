import asyncio
import json
from types import SimpleNamespace

import pytest
from app.database import init_db
from app.repository.plan_repository import PlanRepository
from app.config.decomposer_config import DecomposerSettings
from app.services.llm.decomposer_service import PlanDecomposerLLMService, DecompositionResponse
from app.services.plans.plan_decomposer import PlanDecomposer
from app.services.plans.artifact_preflight import ArtifactPreflightService


@pytest.fixture
def repo(isolated_app_env):
    init_db()
    return PlanRepository()


def test_composite_with_delivery_contract_still_builds_atomic_children(repo, monkeypatch):
    from app.services.plans import plan_generation, task_contracts
    monkeypatch.setattr(task_contracts, 'get_settings', lambda: SimpleNamespace(plan_output_contracts_required=True))
    async def forbidden(**kwargs):
        pytest.fail('The agreed method was sent for automatic rewriting')
    monkeypatch.setattr(plan_generation, '_auto_review_and_optimize_plan', forbidden)
    class LLM:
        def generate(self, prompt):
            assert 'never impute' in prompt and 'review.report_md' in prompt
            return DecompositionResponse(target_node_id=None, mode='plan_bfs', children_raw=[
                {'name': 'Write report', 'instruction': 'Write the report; never impute.', 'leaf': True,
                 'metadata': {'required_outputs': [{'kind': 'document', 'target_path': 'report.md'}],
                              'artifact_contract': {'publishes': ['review.report_md']}}},
                {'name': 'Verify report', 'instruction': 'Check report coverage.', 'leaf': True,
                 'dependencies': [0], 'metadata': {'artifact_contract': {'requires': ['review.report_md']}}},
            ])
    decomposer = PlanDecomposer(repo=repo, llm_service=LLM(),
        settings=DecomposerSettings(model='test', enable_simplification=False))
    outcome = asyncio.run(plan_generation.create_plan_and_generate(
        title='Review', description='Keep the agreed method', owner='owner', repo=repo, decomposer=decomposer,
        tasks=[{'name': 'Report stage', 'instruction': 'Collect evidence and write review; never impute.',
                'metadata': {'task_type': 'composite', 'required_outputs': [{'kind': 'document', 'target_path': 'report.md'}],
                             'artifact_contract': {'publishes': ['review.report_md']}}}],
    ))
    tree = outcome.plan_tree
    root = tree.nodes[outcome.root_task_id]
    stage = tree.nodes[outcome.seeded_tasks[0].id]
    assert root.metadata['task_type'] == 'root' and stage.metadata['task_type'] == 'composite'
    assert outcome.decomposition_status == 'completed'
    assert len(tree.children_ids(stage.id)) == 2
    assert all(tree.nodes[tid].metadata['task_type'] == 'atomic' for tid in tree.children_ids(stage.id))
    from app.services.plans.todo_list import build_full_plan_todo_list
    order = build_full_plan_todo_list(tree, expand_composites=True, ordering_mode='dependency_phase').execution_order
    assert set(order) == set(tree.nodes)
    assert all(order.index(tid) < order.index(stage.id) for tid in tree.children_ids(stage.id))
    assert order.index(stage.id) < order.index(root.id)
    assert 'never impute' in stage.instruction
    assert not ArtifactPreflightService().validate_plan(tree.id, tree, manifest={'artifacts': {}}).has_errors()


def test_atomic_stop_in_one_stage_does_not_drop_other_stages(repo):
    plan = repo.create_plan(title='Two stages')
    root = repo.create_task(plan.id, name='Root', metadata={'is_root': True, 'task_type': 'root'})
    first = repo.create_task(plan.id, name='First', parent_id=root.id, metadata={'task_type': 'composite'})
    second = repo.create_task(plan.id, name='Second', parent_id=root.id, metadata={'task_type': 'composite'})
    calls = []
    class LLM:
        def generate(self, prompt):
            calls.append(prompt)
            if 'Name: First' in prompt:
                return DecompositionResponse(target_node_id=first.id, mode='plan_bfs', should_stop=True, children_raw=[])
            return DecompositionResponse(target_node_id=second.id, mode='plan_bfs', children_raw=[
                {'name': 'Second atomic step', 'instruction': 'Do one step', 'leaf': True}])
    result = PlanDecomposer(repo=repo, llm_service=LLM(), settings=DecomposerSettings(enable_simplification=False)).run_plan(plan.id)
    tree = repo.get_plan_tree(plan.id)
    assert len(calls) == 2 and not result.failed_nodes
    assert tree.nodes[first.id].metadata['task_type'] == 'atomic'
    assert len(tree.children_ids(second.id)) == 1


def test_invalid_decomposition_has_one_bounded_retry_without_inventing_children():
    calls = []
    class Stream:
        def stream_chat(self, prompt, **kwargs):
            calls.append((prompt, kwargs))
            yield '' if len(calls) == 1 else json.dumps({'target_node_id': 4, 'mode': 'single_node', 'children': []})
    service = PlanDecomposerLLMService(llm=Stream(), settings=DecomposerSettings(model='test'))
    assert service.generate('Keep the method').children == []
    assert len(calls) == 2 and 'invalid JSON' in calls[1][0]
    assert calls[0][1]['max_tokens'] == 8192 and calls[0][1]['enable_thinking'] is False
    class Broken:
        def stream_chat(self, prompt, **kwargs):
            yield '{"children": ['
    with pytest.raises(ValueError):
        PlanDecomposerLLMService(llm=Broken(), settings=DecomposerSettings()).generate('keep it')


def test_failed_stage_can_execute_original_task_without_automatic_decomposition(repo):
    plan = repo.create_plan(title='Partial plan')
    root = repo.create_task(plan.id, name='Root', metadata={'is_root': True})
    stage = repo.create_task(plan.id, name='Stage', parent_id=root.id, metadata={'task_type': 'composite'})
    class Broken:
        def generate(self, prompt):
            raise ValueError('Invalid decomposition JSON')
    result = PlanDecomposer(repo=repo, llm_service=Broken(), settings=DecomposerSettings(enable_simplification=False)).run_plan(plan.id)
    tree = repo.get_plan_tree(plan.id)
    assert result.failed_nodes == [stage.id] and result.stats['llm_calls'] == 1
    assert tree.nodes[stage.id].metadata['task_type'] == 'composite'
    preflight = ArtifactPreflightService().validate_plan(plan.id, tree, manifest={'artifacts': {}})
    assert not any(issue.code == 'decomposition_incomplete' for issue in preflight.errors)
    assert any(issue.code == 'decomposition_incomplete' for issue in preflight.warnings)
    assert repo.get_plan_tree(plan.id).children_ids(stage.id) == []
