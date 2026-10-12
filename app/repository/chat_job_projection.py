"""Close chat-control job copies only from an immutable chat terminal without a live lease.

attach_plan can leave a historical copy in system_jobs.sqlite. Its logs remain
useful; it must not remain running after the authoritative chat run has closed.
This is projection repair, never task verification or evidence of content quality.
"""
import json
from app.database import get_db, plan_db_connection
from app.config.database_config import get_database_config
from app.repository.plan_storage import get_system_job_db_path, get_plan_db_path, lookup_decomposition_job_plan


def reconcile_terminal_chat_jobs(run_id=None):
    paths = [get_system_job_db_path()]
    if run_id:
        plan_id = lookup_decomposition_job_plan(run_id)
        if plan_id is not None:
            paths.append(get_plan_db_path(plan_id))
    else:
        paths.extend(sorted(get_database_config().get_plan_store_dir().glob('plan_*.sqlite')))
    repaired = []
    for path in paths:
        if not path.exists():
            continue
        # Preserve plan -> main ordering. Never hold a main write transaction
        # while acquiring the plan connection / claim fence.
        with plan_db_connection(path) as jobs:
            clause, values = (' AND job_id=?', (run_id,)) if run_id else ('', ())
            rows = jobs.execute("SELECT job_id,owner_id,session_id,metadata_json FROM decomposition_jobs "
                "WHERE status IN ('queued','running') AND job_type='chat_deep_think' "
                "AND mode='chat_deep_think'"+clause, values).fetchall()
            for row in rows:
                with get_db() as main:
                    terminal = main.execute("SELECT status,finished_at,error FROM chat_runs "
                        "WHERE run_id=? AND owner_id=? AND session_id=? "
                        "AND status IN ('succeeded','failed','cancelled') "
                        "AND finished_at IS NOT NULL AND (lease_expires_at IS NULL OR lease_expires_at <= datetime('now'))",
                        (row['job_id'],row['owner_id'],row['session_id'])).fetchone()
                if terminal is None:
                    continue
                metadata = json.loads(row['metadata_json'] or '{}')
                metadata['terminal_projection'] = {'source':'chat_runs', 'run_id':row['job_id'],
                    'status':terminal['status'], 'finished_at':terminal['finished_at']}
                changed = jobs.execute("UPDATE decomposition_jobs SET status=?,finished_at=?,error=?,metadata_json=? "
                    "WHERE job_id=? AND status IN ('queued','running')",
                    (terminal['status'],terminal['finished_at'],terminal['error'],
                     json.dumps(metadata,ensure_ascii=False),row['job_id'])).rowcount
                if changed:
                    repaired.append({'database':path.name,'job_id':row['job_id'],'status':terminal['status']})
    return repaired
