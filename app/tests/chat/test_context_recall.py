from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.database import get_db, init_db
from app.config.database_config import get_database_config
from app.services.memory.context_recall import recall, hydrate_context, format_recall_context, MAX_CONTEXT_CHARS


@pytest.fixture
def recall_db(isolated_app_env):
    init_db()
    with get_db() as conn:
        conn.executemany("INSERT INTO chat_sessions(id,owner_id,project_id,name) VALUES(?,?,?,?)", [
            ('a','u',1,'Alpha'), ('b','u',1,'Earlier'), ('other','u',2,'Other project'),
            ('stranger','v',1,'Someone else'), ('local','u',None,'No project'), ('local2','u',None,'Unrelated local')])
        conn.execute("""CREATE TABLE memories(id TEXT PRIMARY KEY,content TEXT,memory_type TEXT,
            importance TEXT,keywords TEXT,tags TEXT,related_task_id INTEGER,owner_id TEXT,created_at TEXT)""")
        conn.commit()
    return isolated_app_env


def note(identity, source=None, *, owner='u', content='lysin alignment', importance='medium', related=None, tags=None):
    with get_db() as conn:
        conn.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?)", (identity, content, 'knowledge', importance, '[]', json.dumps(tags if tags is not None else [f'session:{source}'] if source else []), related, owner, '2026-10-01'))
        conn.commit()


def message(session, content, *, role='assistant', metadata=None):
    with get_db() as conn:
        row = conn.execute('INSERT INTO chat_messages(session_id,role,content,metadata) VALUES(?,?,?,?)', (session,role,content,json.dumps(metadata or {})))
        conn.commit()
        return row.lastrowid


def test_scopes_apply_before_limits_and_user_facts_survive(recall_db):
    note('same','a',content='lysin alignment A'); note('project','b',content='lysin alignment B'); note('other','other'); note('stranger','stranger',owner='v')
    note('orphan','deleted'); note('ambiguous-task',related=1)
    note('profile',content='Always answer in Chinese',importance='high')
    for i in range(90): note(f'irrelevant-{i}','other')
    selected = recall('a','lysin alignment')['memories']
    assert {row['id'] for row in selected} == {'same','project','profile'}
    assert {row['scope'] for row in selected} == {'session','project','user'}
    note('local','local'); note('local2','local2')
    assert {row['id'] for row in recall('local','lysin')['memories']} == {'local','profile'}


def test_original_history_has_evidence_and_excludes_current_turn(recall_db):
    old = message('b','lysin alignment completed',metadata={'status':'failed'})
    message('other','lysin other project'); message('stranger','lysin private')
    message('a','之前 lysin alignment',role='user',metadata={'client_message_id':'this-turn'})
    message('a','lysin current optimistic answer',metadata={'client_message_id':'this-turn'})
    result = recall('a','之前 lysin alignment',client_message_id='this-turn')
    assert len(result['history']) == 1
    assert result['history'][0] == {'message_id':old,'session_id':'b','session_title':'Earlier','role':'assistant','content':'lysin alignment completed','created_at':result['history'][0]['created_at'],'status':'failed'}
    assert not recall('a','generate lysin alignment')['history']
    assert recall('a','lysin',history=True)['history']


def test_legacy_store_read_without_creation_and_deduplication(recall_db):
    path = get_database_config().get_session_db_path('a')
    assert not path.exists()
    assert recall('a','lysin')['memories'] == [] and not path.exists()
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE memories(id TEXT,content TEXT,memory_type TEXT,importance TEXT,keywords TEXT,created_at TEXT)')
        conn.execute("INSERT INTO memories VALUES('legacy','lysin alignment','knowledge','high','[]','2026-10-01')")
    note('duplicate','a')
    result = recall('a','lysin')
    assert len(result['memories']) == 1
    assert result['memories'][0]['content'] == 'lysin alignment'


def test_memory_off_has_no_reads_and_context_bounded(recall_db, monkeypatch):
    from app.repository import context_recall as repo
    monkeypatch.setattr(repo,'session_scope',lambda *_: pytest.fail('memory disabled must not query'))
    assert recall('a','lysin',enabled=False) == {'version':1,'memories':[],'history':[]}
    assert format_recall_context({'memory_enabled':False,'memories':[{'content':'hidden'}]}) == ''


def test_long_evidence_budget_shared_nonmutating_prompt(recall_db):
    from app.routers.chat import prompt_builder
    from app.services.deep_think_agent import DeepThinkAgent
    from app.services.plans.executor_prompts import ExecutorPromptBuilder
    from app.services.plans.plan_models import PlanNode
    for i in range(8):
        note(str(i),'b',content='lysin '+('甲'*1500))
        message('b','lysin '+('乙'*1500))
    evidence = recall('a','之前 lysin')
    assert len(json.dumps(evidence,ensure_ascii=False)) <= MAX_CONTEXT_CHARS
    context = {'recall_context':evidence,'memory_enabled':True}
    snapshot = deepcopy(context)
    block = format_recall_context(context)
    agent = SimpleNamespace(extra_context=context,plan_session=SimpleNamespace(plan_id=None,outline=lambda **_: 'No plan',summaries_for_prompt=lambda **_: ''),mode='assistant',conversation_id='a',history=[],plan_tree=None,
                            _format_plan_outline=lambda:'no plan', _format_plan_catalog=lambda:'', _build_artifact_access_hint=lambda:'')
    plain = prompt_builder.build_simple_stream_chat_prompt(agent,'lysin')
    assert block in plain
    native = DeepThinkAgent(SimpleNamespace(),[],lambda *_:None)
    assert block in native._append_reference_context('base',context)
    plan = ExecutorPromptBuilder().build(node=PlanNode(id=1,plan_id=1,name='Lysin'),parent=None,dependencies=[],plan_outline=None,include_context=False,session_context=context)
    assert block in plan and context == snapshot
    assert plain.count('RECALL REFERENCES') == 1


def test_optional_recall_failure_is_explicit(monkeypatch):
    from app.repository import context_recall as repo
    monkeypatch.setattr(repo,'session_scope',lambda *_: (_ for _ in ()).throw(RuntimeError('store down')))
    context = {}
    hydrate_context(context,'a','lysin')
    assert context['recall_context']['unavailable'] is True


def test_history_recall_includes_answer_that_does_not_repeat_query_words(recall_db):
    question = message('b','lysin alignment progress?',role='user')
    answer = message('b','Finished 12 samples, report is /previous/report.md',metadata={'status':'completed'})
    message('b','New unrelated question',role='user')
    result = recall('a','上次 lysin alignment')
    assert {row['message_id'] for row in result['history']} == {question,answer}
    assert any('/previous/report.md' in row['content'] for row in result['history'])


def test_high_user_preferences_have_reserved_space(recall_db):
    note('profile',content='Always answer in Chinese',importance='critical')
    for i in range(10): note(str(i),'b',content=f'lysin alignment note {i}')
    selected = recall('a','lysin alignment')['memories']
    assert len(selected) == 5 and selected[0]['id'] == 'profile'


def test_profile_and_project_candidates_cannot_starve_each_other(recall_db):
    note('profile',content='Chinese only',importance='critical')
    for i in range(80): note(f'project-{i}','b',content=f'lysin detail {i}')
    assert 'profile' in {row['id'] for row in recall('a','lysin')['memories']}
    for i in range(80): note(f'profile-{i}',content=f'User fact {i}',importance='high')
    selected = recall('a','lysin')['memories']
    assert any(row['scope'] == 'project' for row in selected)
    assert any(row['id'] == 'profile' for row in selected)
