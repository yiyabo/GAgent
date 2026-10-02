from pathlib import Path
from types import SimpleNamespace
import pytest
from app.services.plans import artifact_versions as av
from app.repository.plan_repository import PlanRepository
from app.services.plans.plan_executor import PlanExecutor,ExecutionConfig
from app.services.plans.artifact_contracts import artifact_manifest_path


def test_immutable_files_directories_and_changed_source(tmp_path):
    source=tmp_path/'input.csv';source.write_text('a,b\n1,2\n')
    blob,path=av.snapshot(source,tmp_path/'store');source.write_text('changed')
    assert Path(path).read_text()=='a,b\n1,2\n' and av.content_hash(Path(path))==blob
    tree=tmp_path/'tree';tree.mkdir();(tree/'a').write_text('a')
    tree_blob,tree_path=av.snapshot(tree,tmp_path/'store')
    (tree/'a').write_text('b')
    assert av.content_hash(Path(tree_path))==tree_blob and av.content_hash(tree)!=tree_blob


def test_binding_changes_are_stale_and_old_result_cannot_overwrite_definition(version_db,monkeypatch,tmp_path):
    monkeypatch.setattr(av,'get_settings',lambda:SimpleNamespace(artifact_versioning_enabled=True))
    monkeypatch.setattr(av,'project',lambda *args:None)
    repo=PlanRepository();tree=repo.create_plan('versions',owner='tester');node=repo.create_task(tree.id,name='summary',instruction='mean')
    executor=PlanExecutor(repo=repo);cfg=ExecutionConfig(session_context={})
    binding=av.bind(executor,tree.id,node,cfg)
    output=tmp_path/'summary.json';output.write_text('{"mean":15}')
    av._version(binding.pending,'contract:summary.json',str(output),node.id,artifact_manifest_path(tree.id).parent,binding.to_dict(),{'validated':True,'schema_valid':True})
    assert av.commit_result(executor,binding,{'content':'verified','metadata':{}},'completed')
    manifest=av._read(artifact_manifest_path(tree.id));assert manifest['schema_version']==2
    current=repo.get_plan_tree(tree.id).nodes[node.id]
    assert av.freshness(current,manifest)['freshness']=='reconciling'
    repo.update_task(tree.id,node.id,instruction='median')
    assert av.freshness(repo.get_plan_tree(tree.id).nodes[node.id],manifest)['freshness']=='stale'
    binding2=av.bind(executor,tree.id,current,cfg)
    with pytest.raises(av.StaleArtifactInputs):av.commit_result(executor,binding2,{'content':'old mean','metadata':{}},'completed')
    assert repo.get_plan_tree(tree.id).nodes[node.id].instruction=='median'
    assert repo.get_plan_tree(tree.id).nodes[node.id].execution_result!= '{"content":"old mean"}'


def test_readonly_preview_selects_only_affected_dependency_closure(version_db):
    from app.services.plans.artifact_recompute import preview
    repo=PlanRepository();tree=repo.create_plan('impact',owner='tester')
    a=repo.create_task(tree.id,name='input');b=repo.create_task(tree.id,name='figure',dependencies=[a.id]);c=repo.create_task(tree.id,name='report',dependencies=[b.id]);d=repo.create_task(tree.id,name='unrelated')
    result=preview(repo,tree.id,{'changed_task_ids':[a.id],'target_task_ids':[c.id]})
    assert result['ordered_task_ids']==[a.id,b.id,c.id] and d.id not in result['affected_task_ids']
    with pytest.raises(av.ArtifactRevisionConflict):preview(repo,tree.id,{'changed_task_ids':[a.id],'expected_manifest_revision':42})


@pytest.fixture
def version_db(isolated_app_env,tmp_path,monkeypatch):
    from app.database import init_db
    import app.services.path_router as routing
    monkeypatch.setenv("APP_RUNTIME_ROOT",str(tmp_path/"runtime"))
    monkeypatch.setattr(routing,"_default_router",None)
    from app.services.plans import artifact_contracts
    monkeypatch.setattr(artifact_contracts,'_repo_root',lambda:tmp_path/'plan-artifacts')
    init_db()
    return tmp_path


def test_atomic_manifest_commit_can_recover_after_sql_rollback(version_db,monkeypatch,tmp_path):
    monkeypatch.setattr(av,'get_settings',lambda:SimpleNamespace(artifact_versioning_enabled=True))
    real=av._atomic
    repo=PlanRepository();tree=repo.create_plan('crash',owner='tester');node=repo.create_task(tree.id,name='result',instruction='produce')
    executor=PlanExecutor(repo=repo);binding=av.bind(executor,tree.id,node,ExecutionConfig(session_context={}))
    out=tmp_path/'result.json';out.write_text('{}')
    av._version(binding.pending,'contract:result.json',str(out),node.id,artifact_manifest_path(tree.id).parent,binding.to_dict(),{'validated':True,'schema_valid':True})
    def crash(path,data):real(path,data);raise OSError('power-loss-after-manifest')
    monkeypatch.setattr(av,'_atomic',crash)
    with pytest.raises(OSError):av.commit_result(executor,binding,{'metadata':{},'content':'produced'},'completed')
    assert repo.get_plan_tree(tree.id).nodes[node.id].status=='pending'
    monkeypatch.setattr(av,'_atomic',real)
    av.recover(repo,tree.id)
    assert repo.get_plan_tree(tree.id).nodes[node.id].status=='completed'
    manifest=av._read(artifact_manifest_path(tree.id));assert len(manifest['versions'])==1 and manifest['publications'][binding.publication_id]['applied']
    av.recover(repo,tree.id);assert len(av._read(artifact_manifest_path(tree.id))['versions'])==1


def test_runtime_output_spec_normalization_is_not_a_user_method_change():
    from app.services.plans.plan_models import PlanNode
    from app.services.plans.output_spec import OutputSpec,RequiredOutput
    spec=OutputSpec(required_outputs=[RequiredOutput(kind='data',extensions=['.json'],target_path='/tmp/summary.json')],source='explicit').to_dict()
    node=PlanNode(id=1,plan_id=1,name='summary',instruction='calculate',metadata={'output_spec':spec,'required_outputs':spec['required_outputs']})
    original=av.definition(node)
    node.metadata['output_spec']['artifact_contract']={'requires':[],'publishes':[]}
    assert av.definition(node)==original
    node.metadata['output_spec']['artifact_contract']['requires']=['stats.group_summary']
    assert av.definition(node)!=original


def test_actual_executor_verifies_promoted_files_before_version_commit(version_db,monkeypatch,tmp_path):
    from dataclasses import replace
    from app.config.executor_config import get_executor_settings
    from app.services.plans.task_delegate_executor import TaskDelegationResult
    from app.services.deep_think import native_validation
    monkeypatch.setattr(av,'get_settings',lambda:SimpleNamespace(artifact_versioning_enabled=True))
    monkeypatch.setattr(native_validation,'get_settings',lambda:SimpleNamespace(agent_runtime_v2_enabled=True))
    repo=PlanRepository();tree=repo.create_plan('real delegate pipeline',owner='tester')
    from app.database import get_db
    from app.services.path_router import get_path_router
    from app.services.plans.output_spec import OutputSpec,RequiredOutput
    session='version-exec'
    with get_db() as con:con.execute('INSERT INTO chat_sessions(id,owner_id,name) VALUES(?,?,?)',(session,'tester','executor'))
    target=get_path_router().get_task_output_dir(session,1,[],create=True)/'answer.json'
    spec=OutputSpec(required_outputs=[RequiredOutput(kind='data',extensions=['.json'],target_path=str(target))],source='explicit').to_dict()
    node=repo.create_task(tree.id,name='answer',instruction='Write answer.json',metadata={'required_outputs':spec['required_outputs'],'output_spec':spec})
    source=get_path_router().get_session_dir(session,create=True)/'_scratch'/'run'/'answer.json';source.parent.mkdir(parents=True);source.write_text('{"count":2}')
    class Delegate:
        def execute(self,spec):return TaskDelegationResult(status='completed',summary='answer.json produced',artifact_paths=[str(source)],executor='local',raw_result={'artifact_paths':[str(source)]})
    executor=PlanExecutor(repo=repo,settings=replace(get_executor_settings(),plan_task_execution_backend='external_agent',plan_task_agent_backend='local'),task_delegate_executor=Delegate())
    monkeypatch.setattr(executor,'_resolve_task_tool_workspace',lambda *args,**kwargs:([],str(target.parent)))
    result=executor.execute_task(tree.id,node.id,config=ExecutionConfig(session_context={'session_id':session,'owner_id':'tester'}))
    assert result.status=='completed',__import__('json').dumps(result.to_dict(),ensure_ascii=False,indent=2)
    assert target.exists()
    data=av._read(artifact_manifest_path(tree.id,session))
    assert data['schema_version']==2 and data['bindings'][str(node.id)]['definition_hash']==av.definition(repo.get_plan_tree(tree.id).nodes[node.id])
