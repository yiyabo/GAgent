import json
from types import SimpleNamespace
import pytest
from app.database import get_db,init_db
from app.repository import skill_learning as repository
from app.services.skill_learning import recommendations as rec


@pytest.fixture
def skills(isolated_app_env):
    init_db()
    with get_db() as con:
        con.execute("INSERT INTO chat_sessions(id,owner_id,name) VALUES('s','owner','skills')")
        for i in range(100):
            identity=f'{i:032x}';draft={'name':'clean-data' if i==0 else 'unrelated','description':'清理缺失值，表格数据清洗' if i==0 else '图像绘制','when_to_use':'处理表格','inputs':['csv'],'verification':['唯一ID'],'keywords':['清洗'] if i==0 else ['图像'],'domain':'routine'}
            con.execute('INSERT INTO learned_skills(id,owner_id,session_id,current_version,state,created_at,updated_at) VALUES(?,?,?,1,?,?,?)',(identity,'owner','s','stable','2026-01-01',f'2026-01-{i%28+1:02}'))
            con.execute('INSERT INTO learned_skill_versions VALUES(?,?,?,?,?,?)',(identity,1,json.dumps(draft,ensure_ascii=False),f'hash-{i}',json.dumps({'validated_runs':[]}),repository.now()))
    return {'owner_id':'owner','project_id':None,'id':'s'}


def test_old_relevant_skill_is_not_hidden_by_recent_80(skills):
    result=rec.recommend(skills,'数据清洗',semantic=False)
    assert result['skills'][0]['id']=='0'*32
    assert rec.recommend({**skills,'owner_id':'other'},'数据清洗',semantic=False)['skills']==[]


def test_semantic_vectors_match_current_hash_model_and_dimension(skills,monkeypatch):
    monkeypatch.setattr(rec,'query_vector',lambda _:('test-model',[1.,0.]))
    identity='0'*32
    with get_db() as con:con.execute('INSERT INTO learned_skill_vectors VALUES(?,?,?,?,?,?,?)',(identity,1,'hash-0','test-model',2,'[1,0]',repository.now()))
    result=rec.recommend(skills,'处理缺漏记录')
    assert result['retrieval_mode']=='hybrid' and result['skills'][0]['id']==identity
    with get_db() as con:con.execute('UPDATE learned_skill_vectors SET content_hash=?',('stale',))
    assert rec.recommend(skills,'处理缺漏记录')['retrieval_mode']=='lexical'


def test_similar_methods_do_not_merge_or_transfer_evidence(skills):
    with get_db() as con:con.execute('UPDATE learned_skill_versions SET content_hash=? WHERE skill_id=?',('hash-0',f'{1:032x}'))
    skill=repository.get_skill('0'*32)
    similar=rec.similar(skill,skills)
    assert similar[0]['action']=='possible_duplicate_no_merge'
    assert repository.get_skill(f'{1:032x}')['current_version']==1
    assert rec.stats('0'*32,1)['co_used_run_tokens'] is None
