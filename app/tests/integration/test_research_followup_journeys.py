"""Follow-up HTTP journeys: only the model/embedding boundaries are scripted."""
import csv
import io
import json
import re
import textwrap
from pathlib import Path
from uuid import uuid4

import pytest

from app.database import get_db
from app.llm import LLMClient, NativeStreamResult, NativeToolCall
from app.tests.integration import test_research_journey_delivery as base
from app.tests.integration.test_research_journey_delivery import cleaning_provider  # fixture

HEADERS = base.HEADERS


def run_turn(client, body):
    response = client.post('/chat/runs', json=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    run_id = response.json()['run_id']
    stream = client.get(response.json()['events_stream_url'], params={'session_id': body['session_id']}, headers=HEADERS)
    assert stream.status_code == 200
    events = base._sse_events(stream.text)
    terminals = [event for _, event in events if event['type'] in {'final','error'}]
    assert len(terminals) == 1, events
    assert terminals[0]['type'] == 'final', terminals[0]
    assert terminals[0]['payload']['metadata']['status'] == 'completed', terminals[0]
    return run_id, terminals[0]['payload']


def tool(name, args, identity='followup'):
    return NativeStreamResult(content='Carry out the current requested step.', finish_reason='tool_calls',
                              tool_calls=[NativeToolCall(identity, name, args)])


def final(answer):
    return NativeStreamResult(content=answer, finish_reason='tool_calls',
                              tool_calls=[NativeToolCall('finish-followup','submit_final_answer',{'answer':answer,'confidence':1.0})])


def install_script(monkeypatch, provider, produce):
    captured = []
    original_stream = LLMClient.stream_chat_async
    def capture(messages):
        captured.append(json.loads(json.dumps(messages)))
    async def native(_client, **kwargs):
        capture(kwargs['messages'])
        return produce(captured)
    async def stream(client, *args, **kwargs):
        if kwargs.get('messages'):
            capture(kwargs['messages'])
        async for chunk in original_stream(client,*args,**kwargs):
            yield chunk
    monkeypatch.setattr(LLMClient,'stream_chat_with_tools_async',native)
    monkeypatch.setattr(LLMClient,'stream_chat_async',stream)
    monkeypatch.setattr(provider,'next_response',lambda:produce(captured))
    return captured


def output_context(output, names, *, memory=False):
    return {'memory_enabled':memory,'output_spec_base_dir':str(output),'output_spec':{'source':'explicit',
        'required_outputs':[{'kind':'data','extensions':[Path(n).suffix],'target_path':str(output/n)} for n in names]}}


@pytest.mark.integration
@pytest.mark.timeout(60)
def test_latest_user_correction_replaces_statistics_and_preserves_requested_history(
    app_client_factory, cleaning_provider, monkeypatch,
):
    # A has three distinct scores so median and mean cannot accidentally agree.
    monkeypatch.setattr(base,'INPUT_CSV',base.INPUT_CSV+'g,A,90\n')
    monkeypatch.setenv('AGENT_TOOL_RECEIPT_COMPACTION_ENABLED','1')
    with app_client_factory() as client:
        body, output = base._prepare_cleaning_request(client,cleaning_provider)
        first, _ = run_turn(client,body)
        assert json.loads((output/'summary.json').read_text())['A']['mean']==40
        before = (output/'clean.csv').read_bytes()
        responses = iter([
            tool('execute_code',{'code':textwrap.dedent(f'''
                import csv,json,statistics
                from pathlib import Path
                out=Path({str(output)!r})
                (out/'summary-v1.json').write_bytes((out/'summary.json').read_bytes())
                groups={{}}
                for row in csv.DictReader((out/'clean.csv').open()):
                    groups.setdefault(row['group'],[]).append(float(row['score']))
                (out/'summary.json').write_text(json.dumps({{
                    k:{{'count':len(v),'median':statistics.median(v)}} for k,v in groups.items()
                }}))
            ''')}),
            final(f'Updated [summary.json]({output}/summary.json); original [summary-v1.json]({output}/summary-v1.json) retained.'),
        ])
        capture = install_script(monkeypatch,cleaning_provider,lambda _:next(responses))
        body = {**body,'client_message_id':'correction-turn','message':'Change the previous cleaning result: use medians instead of means. Keep clean.csv unchanged, preserve old statistics in summary-v1.json, and update summary.json.',
                'context':output_context(output,['summary.json','summary-v1.json'])}
        second, payload = run_turn(client,body)
        assert second != first
        assert 'medians instead of means' in json.dumps(capture[0])
        downloaded=client.get('/artifacts/sessions/research-journey/file',params={'path':'workspace/summary.json'},headers=HEADERS)
        assert downloaded.status_code==200
        assert downloaded.json()=={'A':{'count':3,'median':20},'B':{'count':2,'median':40}}
        assert (output/'clean.csv').read_bytes()==before
        assert json.loads((output/'summary-v1.json').read_text())['A']['mean']==40
        assert 'summary-v1.json' in payload['response']
        assert (output/'_writes.txt').read_text()=='1'


@pytest.mark.integration
@pytest.mark.timeout(60)
def test_new_project_session_uses_recalled_source_in_a_new_run(
    app_client_factory, cleaning_provider, monkeypatch, request,
):
    from tool_box.tools_impl.execute_code.kernel import shutdown_kernels_for_session
    request.addfinalizer(lambda:shutdown_kernels_for_session('followup-journey'))
    with app_client_factory() as client:
        body, source_output = base._prepare_cleaning_request(client,cleaning_provider)
        with get_db() as con:
            con.execute("UPDATE chat_sessions SET project_id=71 WHERE id='research-journey'");con.commit()
        first,_=run_turn(client,body)
        assert client.patch('/chat/sessions/followup-journey',json={'name':'Continuation'},headers=HEADERS).status_code==200
        with get_db() as con:
            con.execute("UPDATE chat_sessions SET project_id=71 WHERE id='followup-journey'")
            con.execute("INSERT INTO chat_sessions(id,owner_id,project_id,name) VALUES('decoy-journey','journey-owner',72,'Other project')")
            con.execute("INSERT INTO chat_messages(session_id,role,content) VALUES('decoy-journey','assistant','Previous cleaning [clean.csv](/other-project/private-clean.csv) contains 999 rows.')")
            con.commit()
        from app.services.path_router import get_path_router
        output=get_path_router().get_session_dir('followup-journey',create=True)/'workspace';output.mkdir(exist_ok=True)
        phase=0; sources=[]
        def produce(captured):
            nonlocal phase
            phase+=1
            if phase==1:
                text='\n'.join(str(m.get('content','')) for m in captured[-1])
                assert 'RECALL REFERENCES' in text and 'research-journey' in text
                assert '/other-project/private-clean.csv' not in text
                paths=re.findall(r'"source_path":\s*"([^"\n]+/clean\.csv)"',text)
                assert paths, text[-1500:]
                sources.append(paths[0])
                return tool('execute_code',{'code':f"import csv,json\nfrom pathlib import Path\nrows=list(csv.DictReader(Path({paths[0]!r}).open()))\nPath({str(output/'count.json')!r}).write_text(json.dumps({{'count':len(rows)}}))"})
            return final(f'Continued from the earlier cleaning output. [count.json]({output}/count.json)')
        install_script(monkeypatch,cleaning_provider,produce)
        second,payload=run_turn(client,{'session_id':'followup-journey','client_message_id':'project-followup',
            'message':'Continue the previous cleaning research. Read the existing clean.csv from our earlier project conversation, count its records and write count.json. Reuse the earlier output without cleaning again.',
            'context':output_context(output,['count.json'],memory=True)})
        assert second!=first and Path(sources[0])==source_output/'clean.csv'
        result=client.get('/artifacts/sessions/followup-journey/file',params={'path':'workspace/count.json'},headers=HEADERS)
        assert result.status_code==200 and result.json()=={'count':4}
        assert (source_output/'_writes.txt').read_text()=='1'


@pytest.mark.integration
@pytest.mark.timeout(60)
def test_natural_skill_index_then_body_then_verified_delivery(
    app_client_factory, cleaning_provider, monkeypatch,
):
    from app.repository import skill_learning as repo
    from app.services.skill_learning import recommendations
    from app.services.skill_learning.models import SkillDraft
    from app.services import embeddings
    from types import SimpleNamespace
    # The app's real background indexer also runs when recommendation v2 is on.
    # Script its provider boundary, not just the foreground query, so shutdown
    # cannot leave a real embedding request running after fixture teardown.
    embedding_client=SimpleNamespace(api_client=SimpleNamespace(model='offline-test'),get_single_embedding=lambda _:[1.0,0.0])
    monkeypatch.setattr(embeddings,'get_embeddings_service',lambda:embedding_client)
    monkeypatch.setenv('SKILL_RECOMMENDATION_V2_ENABLED','1')
    monkeypatch.setenv('SKILL_CONTEXT_PROGRESSIVE_ENABLED','1')
    monkeypatch.setattr(recommendations,'query_vector',lambda _:('offline-test',None))
    with app_client_factory() as client:
        body,output=base._prepare_cleaning_request(client,cleaning_provider)
        identity=uuid4().hex
        draft=SkillDraft(name='clean-grouped-data',description='Clean CSV scores and validate grouped statistics',domain='routine',
            when_to_use='CSV cleaning with duplicate IDs and group statistics',inputs=['A CSV file with id, group and score'],
            steps=[{'instruction':'DROP_BAD_THEN_DEDUP: discard missing/non-numeric scores, keep first valid record for each ID, then group valid scores.'}],
            verification=['Check IDs are unique and group counts match cleaned records.'],limitations=['Fixture method; not evidence of scientific validity.'],keywords=['clean','csv','scores','group'],pitfalls=[])
        with get_db() as con:
            repo.ensure_schema(con);recommendations.ensure_schema(con)
            con.execute('INSERT INTO learned_skills(id,owner_id,session_id,current_version,state,review_status,created_at,updated_at) VALUES(?,?,?,1,?,?,?,?)',
                        (identity,'journey-owner','research-journey','stable','accepted',repo.now(),repo.now()))
            con.execute('INSERT INTO learned_skill_versions VALUES(?,?,?,?,?,?)',(identity,1,json.dumps(draft.model_dump()),draft.fingerprint(),json.dumps({'validated_dimensions':[],'requires_human_review':True}),repo.now()))
            con.commit()
        original=cleaning_provider.next_response; phase=0; observed=[]
        def produce(captured):
            nonlocal phase
            text=json.dumps(captured[-1],ensure_ascii=False)
            if phase==0:
                assert 'DROP_BAD_THEN_DEDUP' not in text
                names=re.findall(r'learned:[0-9a-f]{32}:v1',text)
                assert names and names[0]==f'learned:{identity}:v1'
                phase=1
                with get_db() as con:
                    assert con.execute('SELECT count(*) FROM learned_skill_exposures WHERE skill_id=?',(identity,)).fetchone()[0]==1
                    assert con.execute('SELECT count(*) FROM learned_skill_usage WHERE skill_id=?',(identity,)).fetchone()[0]==0
                return tool('load_skill',{'name':names[0]},'load-method')
            assert 'DROP_BAD_THEN_DEDUP' in text
            observed.append(True)
            return original()
        install_script(monkeypatch,cleaning_provider,produce)
        run_id,payload=run_turn(client,body)
        assert observed
        result=client.get('/artifacts/sessions/research-journey/file',params={'path':'workspace/summary.json'},headers=HEADERS)
        assert result.json()=={'A':{'count':2,'mean':15},'B':{'count':2,'mean':40}}
        with get_db() as con:
            row=con.execute('SELECT version,delivery FROM learned_skill_usage WHERE skill_id=? AND run_id=?',(identity,run_id)).fetchone()
            assert tuple(row)==(1,'tool')
        assert 'load_skill' in payload['metadata']['tools_used']
