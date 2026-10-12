import json
from pathlib import Path
import pytest
from app.database import init_db, get_db
from app.repository.plan_repository import PlanRepository
from app.services.plans.artifact_contracts import artifact_manifest_path, save_artifact_manifest
from app.services.plans.artifact_listing import list_artifacts, ArtifactListingConflict
from app.services.plans.artifact_recovery import prepare_recovery, apply_recovery


@pytest.fixture
def project(isolated_app_env, monkeypatch):
    from app.services import path_router
    monkeypatch.setattr(path_router, "_default_router", None)
    init_db();repo=PlanRepository();tree=repo.create_plan(title='Recovery',owner='owner')
    root=repo.create_task(tree.id,name='Root',status='completed')
    task=repo.create_task(tree.id,name='Clean',parent_id=root.id,status='completed')
    repo.update_task(tree.id,task.id,execution_result=json.dumps({'status':'completed','content':'Historical result','metadata':{}}))
    with get_db() as c:c.execute('INSERT INTO chat_sessions(id,owner_id,plan_id) VALUES(?,?,?)',('session_recovery','owner',tree.id))
    from app.services.path_router import get_path_router
    folder=get_path_router().get_task_output_dir('session_recovery',task.id,[root.id],create=True)
    (folder/'clean.csv').write_text('id,score\na,10\nb,20\n')
    return repo,tree.id,root.id,task.id,folder


def test_listing_is_small_even_with_megabytes_of_thoughts_and_raw_payloads(project):
    repo,plan,root,task,folder=project
    artifacts={f'a{i}':{'path':str(folder/f'{i}.csv'),'producer_task_id':task,'binding':{'instruction':'private'*100000}} for i in range(70)}
    repo.update_task(plan,task,execution_result=json.dumps({'content':'x'*5000000,'metadata':{
        'thinking_process':{'steps':['y'*5000000]},'published_artifacts':artifacts}}))
    first=list_artifacts(plan);assert len(first['items'])==50 and first['total']==70
    assert len(json.dumps(first).encode())<100000
    assert 'privateprivate' not in json.dumps(first) and 'thinking_process' not in json.dumps(first)
    second=list_artifacts(plan,cursor=first['next_cursor']);assert len(second['items'])==20
    assert not set(x['alias'] for x in first['items']) & set(x['alias'] for x in second['items'])
    assert second['next_cursor'] is None


def test_manifest_overrides_receipts_and_cursor_rejects_changes(project):
    repo,plan,root,task,folder=project
    repo.update_task(plan,task,execution_result=json.dumps({'metadata':{'published_artifacts':{'one':{'path':'old.csv'},'two':{'path':'two.csv'}}}}))
    first=list_artifacts(plan,limit=1)
    save_artifact_manifest(plan,{'artifacts':{'one':{'path':'new.csv','producer_task_id':root}}},'session_recovery')
    assert [x['alias'] for x in list_artifacts(plan,task_id=task)['items']]==['two']
    with pytest.raises(ArtifactListingConflict):list_artifacts(plan,cursor=first['next_cursor'])
    artifact_manifest_path(plan,'session_recovery').write_text('bad json')
    with pytest.raises(ValueError):list_artifacts(plan)


def preview(project):
    repo,plan,root,task,folder=project
    return prepare_recovery(repo,plan,[{'task_id':task,'path':f'raw_files/task_{root}/task_{task}/clean.csv'}],review_task_ids=[root,task])


def test_recovery_is_hash_bound_idempotent_and_never_marks_content_verified(project):
    repo,plan,root,task,folder=project;before=repo.get_plan_tree(plan).nodes[task]
    ready=preview(project)
    assert not artifact_manifest_path(plan,'session_recovery').exists()
    assert apply_recovery(repo,ready)['status']=='applied'
    after=repo.get_plan_tree(plan).nodes[task]
    assert after.status=='needs_review' and json.loads(after.execution_result)['content']==json.loads(before.execution_result)['content']
    listing=list_artifacts(plan);assert listing['total']==1
    artifact=listing['items'][0]
    assert artifact['content_status']=='unchecked' and not artifact['validated'] and artifact['storage_verified']
    assert Path(artifact['path']).read_bytes()==(folder/'clean.csv').read_bytes()
    assert apply_recovery(repo,ready)['status']=='already_applied'


@pytest.mark.parametrize('changed',['source','task','manifest'])
def test_recovery_conflict_never_publishes_or_overwrites_user_edit(project,changed):
    repo,plan,root,task,folder=project;ready=preview(project)
    if changed=='source':(folder/'clean.csv').write_text('id,score\na,99\n')
    elif changed=='task':repo.update_task(plan,task,instruction='New user instruction')
    else:save_artifact_manifest(plan,{'artifacts':{'user':{'path':'user.csv'}}},'session_recovery')
    with pytest.raises(ValueError):apply_recovery(repo,ready)
    assert repo.get_plan_tree(plan).nodes[task].status=='completed'
    assert all(x.get('origin')!='recovered_workspace' for x in list_artifacts(plan)['items'])


def test_recovery_replays_after_manifest_commit_without_republishing_files(project,monkeypatch):
    from app.repository import plan_artifact_queries as queries
    repo,plan,root,task,folder=project;ready=preview(project)
    original=queries.mark_recovered_tasks_for_review
    with monkeypatch.context() as patch:
        patch.setattr(queries,'mark_recovered_tasks_for_review',lambda *a:(_ for _ in ()).throw(RuntimeError('crash')))
        with pytest.raises(RuntimeError):apply_recovery(repo,ready)
    first=list_artifacts(plan)['items'][0]
    assert apply_recovery(repo,ready)['status']=='applied'
    assert list_artifacts(plan)['items'][0]['path']==first['path']


def test_recovery_rejects_cross_task_paths_and_corrupt_files(project):
    repo,plan,root,task,folder=project
    with pytest.raises(ValueError):prepare_recovery(repo,plan,[{'task_id':root,'path':f'raw_files/task_{root}/../other.csv'}],review_task_ids=[root])
    (folder/'clean.csv').write_text('id,score\na,10,20\n')
    with pytest.raises(ValueError,match='inconsistent_table_width'):preview(project)


def test_http_listing_is_owner_scoped_and_keeps_old_results_compatible(project,app_client_factory):
    repo,plan,root,task,folder=project
    ready=preview(project);apply_recovery(repo,ready)
    with app_client_factory() as client:
        response=client.get(f'/plans/{plan}/artifacts',headers={'X-Forwarded-User':'owner'})
        assert response.status_code==200,response.text
        assert response.json()['total']==1 and len(response.content)<100000
        assert client.get(f'/plans/{plan}/artifacts',headers={'X-Forwarded-User':'other'}).status_code==403
        legacy=client.get(f'/plans/{plan}/results',headers={'X-Forwarded-User':'owner'})
        assert legacy.status_code==200 and 'raw' in legacy.json()['items'][0]


def test_pending_recovery_cannot_overwrite_an_upgrade_to_v2(project,monkeypatch):
    from app.repository import plan_artifact_queries as queries
    repo,plan,root,task,folder=project;ready=preview(project)
    with monkeypatch.context() as patch:
        patch.setattr(queries,'mark_recovered_tasks_for_review',lambda *a:(_ for _ in ()).throw(RuntimeError('crash')))
        with pytest.raises(RuntimeError):apply_recovery(repo,ready)
    path=artifact_manifest_path(plan,'session_recovery');manifest=json.loads(path.read_text())
    manifest['schema_version']=2;path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='requires_v1'):apply_recovery(repo,ready)
    assert repo.get_plan_tree(plan).nodes[task].status=='completed'
