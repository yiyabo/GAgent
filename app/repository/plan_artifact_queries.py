"""Small artifact projections: never return task thoughts, content or raw results."""
import json
from app.database import get_db, plan_db_connection
from .plan_storage import get_plan_db_path


def artifact_store_session(plan_id):
    with get_db() as conn:
        row = conn.execute("SELECT CASE WHEN json_valid(metadata) THEN json_extract(metadata,'$.artifact_store_ref') END AS store FROM plans WHERE id=?", (plan_id,)).fetchone()
        store = json.loads(row['store'] or '{}') if row else {}
        if isinstance(store, dict) and store.get('session_id'):
            return store['session_id']
        sessions = conn.execute('SELECT id FROM chat_sessions WHERE plan_id=? ORDER BY id', (plan_id,)).fetchall()
    if len(sessions) > 1:
        raise ValueError('ambiguous_artifact_store')
    return sessions[0]['id'] if sessions else None


def published_receipts(plan_id, task_id=None):
    where, args = (' WHERE id=?', (task_id,)) if task_id is not None else ('', ())
    with plan_db_connection(get_plan_db_path(plan_id)) as conn:
        rows = conn.execute("SELECT id, CASE WHEN json_valid(execution_result) THEN "
            "json_extract(execution_result,'$.metadata.published_artifacts') END AS artifacts "
            "FROM tasks"+where+' ORDER BY id', args).fetchall()
    return [(row['id'], json.loads(row['artifacts'] or '{}')) for row in rows]


def mark_recovered_tasks_for_review(plan_id, expected, recovery_id):
    from app.services.plans.artifact_recovery import digest
    with plan_db_connection(get_plan_db_path(plan_id)) as conn:
        for task_id, fingerprint in expected.items():
            row=conn.execute('SELECT * FROM tasks WHERE id=?',(int(task_id),)).fetchone()
            meta=json.loads(row['metadata'] or '{}')
            if meta.get('artifact_recovery_id')==recovery_id: continue
            current=digest([row['name'],row['instruction'],row['parent_id'],row['status'],meta,row['execution_result']])
            if current!=fingerprint: raise ValueError('task_changed_during_recovery')
            result=json.loads(row['execution_result'] or '{}');rm=result.setdefault('metadata',{})
            meta['artifact_recovery_id']=recovery_id
            rm['historical_execution_status']=row['status']
            rm['delivery_verification']={'status':'needs_review','reason':'historical_outputs_unverified','message':'Historical files were recovered; content verification is still pending.','recovery_id':recovery_id}
            rm['verification_status']='unchecked';result['status']='needs_review'
            conn.execute("UPDATE tasks SET status='needs_review',metadata=?,execution_result=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(meta,ensure_ascii=False),json.dumps(result,ensure_ascii=False),int(task_id)))


def pin_artifact_store(plan_id, session_id):
    from .chat_runs import _write_transaction
    with get_db() as conn:
        with _write_transaction(conn):
            row=conn.execute('SELECT metadata FROM plans WHERE id=?',(plan_id,)).fetchone()
            metadata=json.loads(row['metadata'] or '{}')
            existing=(metadata.get('artifact_store_ref') or {}).get('session_id')
            if existing:
                if existing!=session_id: raise ValueError('artifact_store_changed')
                return
            sessions=conn.execute('SELECT id FROM chat_sessions WHERE plan_id=?',(plan_id,)).fetchall()
            if len(sessions)!=1 or sessions[0]['id']!=session_id: raise ValueError('ambiguous_artifact_store')
            metadata['artifact_store_ref']={'session_id':session_id}
            conn.execute('UPDATE plans SET metadata=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                (json.dumps(metadata,ensure_ascii=False),plan_id))
