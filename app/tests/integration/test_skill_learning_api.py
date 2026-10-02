from __future__ import annotations
import asyncio
import json
import pytest
from app.database import get_db
from app.repository import chat_runs,skill_learning as repo
from app.routers.chat.models import ChatRequest
from app.services.skill_learning.models import DistillationResult,SkillDraft
from app.services.skill_learning.service import SkillLearningService


class DraftOnly:
    async def distill(self,evidence):
        return DistillationResult(reusable=True,reason='用户指定的写作方法',draft=SkillDraft(name='report-outline',description='按用户确认的方法整理报告大纲',domain='writing',
            when_to_use='用户要求整理一份研究报告的大纲时',inputs=['报告主题'],steps=[{'instruction':'先写问题和证据，再组织报告结构。','evidence_ids':[]}],
            verification=['请用户确认结构是否适用'],limitations=['不证明研究结论正确'],keywords=['报告'],pitfalls=[]))


@pytest.mark.integration
def test_candidate_feedback_review_edit_and_scope_over_real_http(app_client_factory,monkeypatch):
    from app.services.skill_learning import service as service_module
    learning=SkillLearningService(DraftOnly())
    monkeypatch.setattr(service_module,'_service',learning)
    alice={'X-Forwarded-User':'alice'};bob={'X-Forwarded-User':'bob'}
    with app_client_factory() as client:
        for sid in ('source-s','other-s'):
            assert client.patch('/chat/sessions/'+sid,json={'name':sid},headers=alice).status_code==200
        request=ChatRequest(message='写报告大纲',session_id='source-s')
        chat_runs.create_chat_run('writing-source','source-s',request.model_dump_json(),owner_id='alice')
        assert chat_runs.claim_chat_run_lease('writing-source','worker')
        assert chat_runs.mark_chat_run_started('writing-source',worker_id='worker')
        assert chat_runs.finish_chat_run_with_event('writing-source','succeeded',{'type':'final','payload':{'response':'大纲内容','metadata':{'status':'completed'}}},worker_id='worker') is not None
        capture=client.post('/skill-learning/runs/writing-source/capture',json={'session_id':'source-s'},headers=alice)
        assert capture.status_code==200
        assert asyncio.run(learning.process_one('writing-source'))
        info=client.get('/skill-learning/runs/writing-source',params={'session_id':'source-s'},headers=alice)
        assert info.status_code==200
        skill=info.json()['skills'][0];identity=skill['id'];assert skill['state']=='candidate'
        assert client.get('/skill-learning/runs/writing-source',params={'session_id':'source-s'},headers=bob).status_code==403
        assert client.get('/skill-learning/skills/'+identity,params={'session_id':'other-s'},headers=alice).status_code==404
        review=client.post('/skill-learning/skills/'+identity+'/review',json={'session_id':'source-s','version':1,'decision':'accept'},headers=alice)
        assert review.status_code==200 and review.json()['state']=='candidate'
        feedback=client.post('/skill-learning/runs/writing-source/feedback',json={'session_id':'source-s','rating':'needs_work','comment':'结构需要修改'},headers=alice)
        assert feedback.status_code==200
        assert repo.get_skill(identity)['state']=='suspended'
        edit=skill['draft'];edit['steps'][0]['instruction']='先界定问题与证据范围，再组织报告。'
        updated=client.put('/skill-learning/skills/'+identity,json={'session_id':'source-s','version':1,'draft':edit},headers=alice)
        assert updated.status_code==200 and updated.json()['current_version']==2 and updated.json()['state']=='candidate'
        stale=client.post('/skill-learning/skills/'+identity+'/review',json={'session_id':'source-s','version':1,'decision':'accept'},headers=alice)
        assert stale.status_code==409
        markdown=client.get('/skill-learning/skills/'+identity+'/markdown',params={'session_id':'source-s'},headers=alice)
        assert markdown.status_code==200 and markdown.json()['version']==2
        assert '先界定问题' in markdown.json()['markdown']
