"""Paper context is not permission to redraft a verified planning deliverable."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config.executor_config import get_executor_settings
from app.services.llm.decomposer_service import DecompositionChild
from app.services.plans.paper_task_policy import is_planning_document, requires_manuscript_content
from app.services.plans.plan_decomposer import PlanDecomposer
from app.services.plans.plan_executor import PlanExecutor, ExecutionConfig
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.executor_prompts import ExecutorPromptBuilder
from app.services.plans.task_verification import VerificationFinalization


@pytest.mark.parametrize("title,instruction", [
    ("综述定位与主线策略", "输出为一份定位策略笔记。"),
    ("章节级详细大纲设计", "基于定位策略，设计章节标题、要点和字数。"),
    ("整合输出最终大纲文档与差异分析", "整合定位与章节大纲。"),
    ("Write a positioning strategy note", "Decide audience and scope."),
    ("Draft manuscript outline", "Outline the future paper."),
    ("Generate detailed chapter outline", "Use the approved strategy."),
    ("Prepare research proposal", "Produce a research proposal."),
    ("Research protocol", "Describe the protocol."),
    ("Scope decisions", "Deliver a scope note."),
])
def test_planning_goal_overrides_stale_section_writer_metadata(title, instruction):
    metadata = {"paper_role": "section_writer", "paper_section": "introduction", "paper_mode": True}
    assert is_planning_document(title, instruction, metadata)
    assert not requires_manuscript_content(title, instruction, metadata)
    child = DecompositionChild(name=title, instruction=instruction, metadata=metadata)
    derived = PlanDecomposer._derive_paper_metadata(PlanDecomposer, child)
    assert derived['paper_role'] == 'planning_document'
    assert derived['paper_mode'] is False and 'paper_section' not in derived
    assert metadata['paper_mode'] is True  # caller/old task is not edited


@pytest.mark.parametrize("title,instruction", [
    ("Write introduction", "Use the approved outline to draft the introduction."),
    ("撰写综述引言", "根据定位策略与大纲撰写正文。"),
    ("Therapeutic strategies", "Draft the therapeutic strategies section of the manuscript."),
    ("Write full manuscript from outline", "Assemble and verify the complete manuscript."),
])
def test_manuscript_consuming_planning_material_remains_manuscript(title, instruction):
    metadata={'paper_role':'section_writer', 'paper_mode':True}
    assert not is_planning_document(title, instruction, metadata)
    assert requires_manuscript_content(title, instruction, metadata)


def test_planner_preserves_planning_output_contract_and_inputs():
    metadata={'paper_section':'introduction','paper_role':'section_writer',
              'required_outputs':[{'kind':'document','target_path':'results/strategy.md','extensions':['.md'],'min_count':2}],
              'artifact_contract':{'requires':['review.evidence_md'],'publishes':['review.strategy_md']},
              'paper_context_paths':['/inputs/evidence.md']}
    original=deepcopy(metadata)
    derived=PlanDecomposer._derive_paper_metadata(PlanDecomposer,
        DecompositionChild(name='综述定位与主线策略',instruction='输出为一份定位策略笔记。',metadata=metadata))
    assert derived['required_outputs'] == original['required_outputs']
    assert derived['artifact_contract'] == original['artifact_contract']
    assert derived['paper_context_paths'] == original['paper_context_paths']
    assert metadata == original


def _run(tmp_path, monkeypatch, *, planning=False, write=True, bad_quality=False, extra_missing=False, block=False):
    root=tmp_path/'task';root.mkdir()
    path=root/'note.md'
    meta={'paper_mode':True,'paper_section':'introduction','paper_role':'section_writer',
          'required_outputs':[{'kind':'document','target_path':'note.md','extensions':['.md']}],
          'output_spec_base_dir':str(root)}
    if bad_quality:
        meta['acceptance_criteria']={'category':'file_data','blocking':True,'checks':[
            {'type':'manuscript_markdown_quality','path':str(path),'hard':True,
             'min_text_chars':200,'min_sections':3,'required_terms':['Citations']}]}
    if extra_missing:
        meta['required_outputs'].append({'kind':'data','target_path':'data.csv','extensions':['.csv']})
    node=PlanNode(id=4,plan_id=7,name='综述定位与主线策略' if planning else 'Write manuscript',
                  instruction='输出为一份定位策略笔记。' if planning else 'Write the manuscript introduction.',metadata=meta)
    original=deepcopy(meta)
    tree=PlanTree(id=7,title='Paper project',nodes={4:node});tree.rebuild_adjacency()
    repo=MagicMock()
    executor=PlanExecutor(repo=repo,llm_service=MagicMock(),settings=replace(get_executor_settings(),plan_task_execution_backend='internal'))
    monkeypatch.setattr(executor,'_resolve_task_tool_workspace',lambda *args,**kwargs:(None,str(root)))
    observed=[]; calls=[]; order=[]
    finalize=executor._finalize_task_execution
    def verify(*args,**kwargs):
        result=finalize(*args,**kwargs)
        order.append(('verify',result[0].final_status))
        return result
    monkeypatch.setattr(executor,'_finalize_task_execution',verify)
    class Agent:
        def __init__(self,*args,**kwargs):pass
        async def think(self,query,*,context,task_context):
            observed.append((deepcopy(context),deepcopy(task_context)))
            if write:path.write_text('placeholder' if bad_quality else '# Positioning\nAudience, narrative, novelty and exclusions.\n',encoding='utf-8')
            return SimpleNamespace(final_answer=f'BLOCKED_DEPENDENCY: required evidence absent.' if block else f'Saved {path}',
                thinking_summary='Done',confidence=0.9,tools_used=['file_operations'],total_iterations=1,
                thinking_steps=[],tool_failures=[],termination_reason=None,
                execution_issues=[{'error':'step reconciliation required'}] if block else [])
    monkeypatch.setattr('app.services.plans.plan_executor.DeepThinkAgent',Agent)
    def recover(**kwargs):
        assert order and order[-1]==('verify','failed')
        order.append(('recover',None))
        calls.append(kwargs)
        path.write_text('# Manuscript\nRecovered actual output.\n',encoding='utf-8')
        return {'success':True,'output_path':str(path)}
    monkeypatch.setattr(executor,'_run_manuscript_writer_fallback',recover)
    result=executor._run_task_with_deep_think(plan_id=7,node=node,parent=None,dependencies=[],plan_outline=None,
        tree=tree,config=ExecutionConfig(session_context={'session_id':'test','paper_mode':True},
        paper_mode=True,enable_skills=False,contract_repair_attempts=0))
    # Only normal running/result persistence is allowed; immutable instructions
    # and the task's original delivery metadata stay intact.
    assert {key:node.metadata[key] for key in original} == original
    assert set(node.metadata)-set(original) <= {'skill_trace'}
    assert all('instruction' not in call.kwargs for call in repo.update_task.call_args_list)
    return result,path,calls,observed,executor,node,root


@pytest.mark.parametrize('planning',[False,True])
def test_verified_file_completes_without_whole_manuscript_fallback(tmp_path,monkeypatch,planning):
    result,path,calls,observed,*_= _run(tmp_path,monkeypatch,planning=planning)
    assert result.status=='completed' and path.read_text().startswith('# Positioning')
    assert calls == []
    if planning:
        context,task=observed[0]
        assert context['paper_mode'] is False
        assert any('planning document' in x for x in task.constraints)
        assert not any('max_pdfs>=20' in x for x in task.constraints)


def test_real_missing_manuscript_verifies_first_then_recovers(tmp_path,monkeypatch):
    result,path,calls,*_=_run(tmp_path,monkeypatch,write=False)
    assert result.status=='completed' and len(calls)==1
    assert path.read_text().startswith('# Manuscript')


def test_planning_missing_file_does_not_invoke_paper_pipeline(tmp_path,monkeypatch):
    result,path,calls,*_=_run(tmp_path,monkeypatch,planning=True,write=False)
    assert result.status=='failed' and not path.exists() and calls==[]


def test_missing_other_output_does_not_overwrite_existing_manuscript(tmp_path,monkeypatch):
    result,path,calls,*_=_run(tmp_path,monkeypatch,extra_missing=True)
    assert result.status=='failed' and calls==[]
    assert path.read_text().startswith('# Positioning')


def test_actual_manuscript_quality_check_still_rejects_placeholder(tmp_path,monkeypatch):
    result,path,calls,*_=_run(tmp_path,monkeypatch,bad_quality=True)
    assert result.status=='failed' and calls==[] and path.read_text()=='placeholder'
    assert result.metadata['verification']['status']=='failed'


def test_step_or_dependency_failure_is_not_hidden_by_writer_recovery(tmp_path,monkeypatch):
    result,path,calls,*_=_run(tmp_path,monkeypatch,write=False,block=True)
    assert result.status=='failed' and calls==[]


def test_delegate_planning_prompt_omits_paper_execution_template():
    node=PlanNode(id=4,plan_id=7,name='章节级详细大纲设计',instruction='生成章节大纲。',
        metadata={'paper_mode':True,'paper_section':'introduction','paper_role':'section_writer'})
    prompt=ExecutorPromptBuilder().build(node=node,parent=None,dependencies=[],plan_outline=None,include_context=False,
        session_context={'paper_mode':True},include_tool_hints=False)
    assert 'planning document' in prompt
    assert '=== PAPER MODE ===' not in prompt and 'assembly task -> call manuscript_writer' not in prompt


def test_output_spec_target_precedes_alias_guess():
    executor=PlanExecutor(repo=MagicMock(),llm_service=MagicMock())
    node=PlanNode(id=4,plan_id=7,name='Write manuscript',metadata={
        'required_outputs':[{'kind':'document','target_path':'results/chapter.md','extensions':['.md']}],
        'artifact_contract':{'publishes':['review.chapter_md']}})
    assert executor._derive_manuscript_output_path(node)=='results/chapter.md'


@pytest.mark.parametrize('termination',['cancelled','run_deadline_exceeded'])
def test_cancel_and_deadline_do_not_launch_writer(tmp_path,termination):
    executor=PlanExecutor(repo=MagicMock(),llm_service=MagicMock())
    node=PlanNode(id=4,plan_id=7,name='Write manuscript',metadata={
        'required_outputs':[{'kind':'document','target_path':'article.md','extensions':['.md']}]})
    final=VerificationFinalization(final_status='failed',execution_status='failed',payload={
        'metadata':{'failure_kind':'contract_mismatch','contract_diff':{},'termination_reason':termination}})
    assert not executor._needs_manuscript_recovery(node,final,str(tmp_path))


def test_paper_project_data_output_does_not_launch_writer(tmp_path):
    executor=PlanExecutor(repo=MagicMock(),llm_service=MagicMock())
    node=PlanNode(id=4,plan_id=7,name='Write report',metadata={'paper_mode':True,'paper_role':'section_writer',
        'required_outputs':[{'kind':'data','target_path':'matrix.csv','extensions':['.csv']}]})
    final=VerificationFinalization(final_status='failed',execution_status='failed',payload={
        'metadata':{'failure_kind':'contract_mismatch','contract_diff':{}}})
    assert not executor._needs_manuscript_recovery(node,final,str(tmp_path))


def test_missing_multiple_chapters_use_targeted_repair(tmp_path):
    executor=PlanExecutor(repo=MagicMock(),llm_service=MagicMock())
    node=PlanNode(id=4,plan_id=7,name='Write manuscript sections',metadata={
        'required_outputs':[{'kind':'document','target_path':'sections','min_count':3,'extensions':['.md']}]})
    final=VerificationFinalization(final_status='failed',execution_status='failed',payload={
        'metadata':{'failure_kind':'contract_mismatch','contract_diff':{}}})
    assert not executor._needs_manuscript_recovery(node,final,str(tmp_path))


def test_evidence_collector_is_not_manuscript_recovery():
    assert not requires_manuscript_content('Collect literature','Build evidence.md',{
        'paper_mode':True,'paper_role':'evidence_collector','paper_section':'references'})


@pytest.mark.parametrize('title',['Research protocol execution','Outline-based manuscript writing'])
def test_planning_word_used_as_input_or_executable_method_is_not_a_planning_deliverable(title):
    assert not is_planning_document(title,'Execute the specified method and deliver its results.')


def test_current_manuscript_goal_overrides_role_from_an_older_planning_task():
    metadata={'paper_role':'planning_document'}
    assert not is_planning_document('Write introduction','Draft the actual manuscript introduction.',metadata)
    assert requires_manuscript_content('Write introduction','Draft the actual manuscript introduction.',metadata)
