"""A moved control job retains history but cannot masquerade as live work."""
import json
import pytest
from app.database import init_db, get_db, plan_db_connection
from app.repository.plan_repository import PlanRepository
from app.repository.plan_storage import record_decomposition_job, load_decomposition_job, register_decomposition_job_index
from app.repository.chat_job_projection import reconcile_terminal_chat_jobs
from app.repository.chat_runs import create_chat_run, mark_chat_run_started, mark_chat_run_finished


@pytest.fixture
def pair(isolated_app_env):
    init_db()
    repo=PlanRepository();plan=repo.create_plan(title='Projection')
    with get_db() as c:c.execute("INSERT INTO chat_sessions (id,owner_id) VALUES ('session','legacy-local')")
    create_chat_run('run','session','{}')
    mark_chat_run_started('run')
    for plan_id in (None,plan.id):
        record_decomposition_job(plan_id,job_id='run',job_type='chat_deep_think',mode='chat_deep_think',
            target_task_id=None,owner_id='legacy-local',session_id='session',status='running',metadata={'keep':'history'})
    register_decomposition_job_index('run',plan.id,job_type='chat_deep_think',owner_id='legacy-local',session_id='session')
    return plan.id


@pytest.mark.parametrize('status',['succeeded','failed','cancelled'])
def test_terminal_reconciles_system_and_plan_copies_idempotently(pair,status):
    assert not reconcile_terminal_chat_jobs('run')
    mark_chat_run_finished('run',status)
    changed=reconcile_terminal_chat_jobs('run')
    assert len(changed)==2
    for plan_id in (None,pair):
        row=load_decomposition_job(plan_id,'run')
        assert row['status']==status and row['finished_at']
        assert row['metadata']['keep']=='history'
        assert row['metadata']['terminal_projection']['source']=='chat_runs'
    assert not reconcile_terminal_chat_jobs('run')


def test_live_lease_or_identity_mismatch_never_closes_a_job(pair):
    mark_chat_run_finished('run','succeeded')
    with get_db() as c:c.execute("UPDATE chat_runs SET lease_expires_at=datetime('now','+100 seconds') WHERE run_id='run'")
    assert not reconcile_terminal_chat_jobs('run')
    with get_db() as c:c.execute("UPDATE chat_runs SET lease_expires_at=NULL,owner_id='other' WHERE run_id='run'")
    assert not reconcile_terminal_chat_jobs('run')
    with get_db() as c:
        c.execute("INSERT INTO chat_sessions (id,owner_id) VALUES ('other','legacy-local')")
        c.execute("UPDATE chat_runs SET owner_id='legacy-local',session_id='other' WHERE run_id='run'")
    assert not reconcile_terminal_chat_jobs('run')


def test_global_reconcile_leaves_non_chat_plan_jobs_untouched(pair):
    mark_chat_run_finished('run','succeeded')
    record_decomposition_job(pair,job_id='plan-only',job_type='plan_execute',mode='full_plan',
        target_task_id=None,owner_id='legacy-local',session_id='session',status='running')
    assert len(reconcile_terminal_chat_jobs())==2
    assert load_decomposition_job(pair,'plan-only')['status']=='running'


def test_expired_terminal_lease_can_reconcile_but_terminal_job_is_never_overwritten(pair):
    from app.repository.plan_storage import update_decomposition_job_status
    mark_chat_run_finished('run','succeeded')
    with get_db() as c:c.execute("UPDATE chat_runs SET lease_expires_at=datetime('now','-10 seconds') WHERE run_id='run'")
    update_decomposition_job_status(pair,job_id='run',status='cancelled',error='Already closed')
    assert len(reconcile_terminal_chat_jobs('run'))==1
    assert load_decomposition_job(pair,'run')['status']=='cancelled'
