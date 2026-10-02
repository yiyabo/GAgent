import pytest
from app.repository.plan_repository import PlanRepository


@pytest.mark.integration
def test_preview_history_and_recompute_are_scoped_and_idempotent(app_client_factory,monkeypatch):
    import app.routers.plan_routes as routes
    monkeypatch.setattr(routes,'_run_full_plan_job',lambda **_:None)
    alice={'X-Forwarded-User':'alice'};bob={'X-Forwarded-User':'bob'}
    with app_client_factory() as client:
        repo=PlanRepository();tree=repo.create_plan('version API',owner='alice')
        a=repo.create_task(tree.id,name='clean');b=repo.create_task(tree.id,name='chart',dependencies=[a.id])
        history=client.get(f'/plans/{tree.id}/artifact-versions',headers=alice)
        assert history.status_code==200 and history.json()['versions']==[]
        assert client.get(f'/plans/{tree.id}/artifact-versions',headers=bob).status_code==403
        payload={'changed_task_ids':[a.id],'target_task_ids':[b.id],'expected_manifest_revision':0}
        preview=client.post(f'/plans/{tree.id}/recompute-preview',headers=alice,json=payload)
        assert preview.status_code==200 and preview.json()['ordered_task_ids']==[a.id,b.id]
        request={**payload,'preview_fingerprint':preview.json()['preview_fingerprint'],'idempotency_key':'once'}
        first=client.post(f'/plans/{tree.id}/recompute',headers=alice,json=request)
        assert first.status_code==200
        again=client.post(f'/plans/{tree.id}/recompute',headers=alice,json=request)
        assert again.status_code==200 and again.json()['job_id']==first.json()['job_id']
        wrong=client.post(f'/plans/{tree.id}/recompute',headers=alice,json={**request,'changed_task_ids':[b.id]})
        assert wrong.status_code==409
