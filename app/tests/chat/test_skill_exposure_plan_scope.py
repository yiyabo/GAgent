import json
from app.database import get_db,init_db
from app.repository import chat_runs
from app.services.chat_run_state import chat_run_claim
from app.services.skill_learning.context import hydrate_context
from app.services.harness_eval.skill_fixtures import seed


def test_plan_lexical_path_records_exposure_from_matching_active_claim(isolated_app_env,monkeypatch):
    monkeypatch.setenv('SKILL_CONTEXT_PROGRESSIVE_ENABLED','1')
    monkeypatch.setenv('SKILL_RECOMMENDATION_V2_ENABLED','0')
    from app.services.foundation.settings import get_settings
    get_settings.cache_clear();init_db()
    with get_db() as con:
        con.execute("INSERT INTO chat_sessions(id,owner_id,name) VALUES('s','harness-eval','source')")
        con.execute("INSERT INTO chat_sessions(id,owner_id,name) VALUES('other','harness-eval','other')");con.commit()
    seed('s')
    chat_runs.create_chat_run('r','s',json.dumps({'session_id':'s','message':'clean csv'}),owner_id='harness-eval')
    assert chat_runs.claim_chat_run_lease('r','worker')
    assert chat_runs.mark_chat_run_started('r',worker_id='worker')
    token=chat_run_claim.set(('r','worker'))
    try:
        context={};hydrate_context(context,'s','clean csv scores')
        assert context['learned_skill_context']['skills']
        with get_db() as con:
            assert con.execute("SELECT count(*) FROM learned_skill_exposures WHERE run_id='r'").fetchone()[0]>0
            assert con.execute("SELECT count(*) FROM learned_skill_usage WHERE run_id='r'").fetchone()[0]==0
        hydrate_context({},'other','clean csv')
        with get_db() as con:
            assert con.execute("SELECT session_id FROM skill_run_contexts WHERE run_id='r'").fetchone()[0]=='s'
    finally:
        chat_run_claim.reset(token)
        chat_runs.mark_chat_run_finished('r','succeeded',worker_id='worker')
        chat_runs.release_chat_run_lease('r','worker');get_settings.cache_clear()
