"""Scoped, versioned skill proposals, leased learning jobs and observed reuse."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from app.database import get_db
from app.repository.chat_runs import _write_transaction


def now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def ensure_schema(conn) -> None:
    statements = [
        """CREATE TABLE IF NOT EXISTS skill_learning_jobs(
            run_id TEXT PRIMARY KEY REFERENCES chat_runs(run_id) ON DELETE CASCADE,
            owner_id TEXT NOT NULL,session_id TEXT NOT NULL,project_id INTEGER,
            forced INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,claim TEXT,lease_until TEXT,
            error_code TEXT,skill_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS learned_skills(
            id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,session_id TEXT NOT NULL,project_id INTEGER,
            source_run_id TEXT REFERENCES chat_runs(run_id) ON DELETE SET NULL,
            current_version INTEGER NOT NULL DEFAULT 1,state TEXT NOT NULL DEFAULT 'candidate',
            review_status TEXT NOT NULL DEFAULT 'pending',reason TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS learned_skill_versions(
            skill_id TEXT NOT NULL REFERENCES learned_skills(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,draft_json TEXT NOT NULL,content_hash TEXT NOT NULL,
            evidence_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(skill_id,version))""",
        """CREATE TABLE IF NOT EXISTS skill_run_contexts(
            run_id TEXT PRIMARY KEY REFERENCES chat_runs(run_id) ON DELETE CASCADE,
            input_digest TEXT NOT NULL,input_basis TEXT NOT NULL,selected_json TEXT NOT NULL,
            owner_id TEXT NOT NULL,session_id TEXT NOT NULL,project_id INTEGER,
            created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS learned_skill_usage(
            skill_id TEXT NOT NULL REFERENCES learned_skills(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,run_id TEXT NOT NULL,
            loaded_at TEXT NOT NULL,delivery TEXT NOT NULL DEFAULT 'tool',status TEXT NOT NULL DEFAULT 'pending',
            evidence_json TEXT,PRIMARY KEY(skill_id,version,run_id))""",
        """CREATE TABLE IF NOT EXISTS skill_run_feedback(
            run_id TEXT PRIMARY KEY REFERENCES chat_runs(run_id) ON DELETE CASCADE,
            rating TEXT NOT NULL CHECK(rating IN ('useful','needs_work')),comment TEXT NOT NULL,
            updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS learned_skill_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,skill_id TEXT NOT NULL,
            version INTEGER NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,
            details_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS skill_learning_calls(
            id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,created_at TEXT NOT NULL)""",
        'CREATE INDEX IF NOT EXISTS idx_learning_jobs_status ON skill_learning_jobs(status,lease_until,created_at)',
        'CREATE INDEX IF NOT EXISTS idx_learned_skills_scope ON learned_skills(owner_id,project_id,state)',
        'CREATE INDEX IF NOT EXISTS idx_skill_usage_run ON learned_skill_usage(run_id,status)',
        'CREATE INDEX IF NOT EXISTS idx_learning_calls_time ON skill_learning_calls(created_at)',
    ]
    for sql in statements: conn.execute(sql)


def _event(conn, skill_id, version, actor, action, details) -> None:
    conn.execute('INSERT INTO learned_skill_events(skill_id,version,actor,action,details_json,created_at) VALUES(?,?,?,?,?,?)',
                 (skill_id,version,actor,action,encode(details),now()))


def _decoded(row):
    if not row: return None
    out = dict(row)
    for key in ('draft_json','evidence_json'):
        if key in out: out[key[:-5]] = json.loads(out.pop(key))
    if 'id' in out and 'current_version' in out:
        out['public_name'] = f"learned:{out['id']}:v{out.get('loaded_version',out['current_version'])}"
    return out


def get_skill(skill_id: str, version: int | None = None):
    with get_db() as conn:
        return _decoded(conn.execute('''SELECT s.*,v.version AS loaded_version,v.draft_json,v.content_hash,v.evidence_json FROM learned_skills s
            JOIN learned_skill_versions v ON v.skill_id=s.id AND v.version=COALESCE(?,s.current_version) WHERE s.id=?''',
            (version,skill_id)).fetchone())


def list_skills(scope: dict, limit: int = 50):
    with get_db() as conn:
        return [_decoded(row) for row in conn.execute('''SELECT s.*,v.draft_json,v.content_hash,v.evidence_json FROM learned_skills s
            JOIN learned_skill_versions v ON v.skill_id=s.id AND v.version=s.current_version
            WHERE s.owner_id=? AND ((s.project_id IS NULL AND ? IS NULL AND s.session_id=?) OR (s.project_id IS NOT NULL AND s.project_id=?))
            ORDER BY s.updated_at DESC,s.id LIMIT ?''',
            (scope['owner_id'],scope['project_id'],scope['id'],scope['project_id'],limit)).fetchall()]


def enqueue(run: dict, scope: dict, forced: bool = False) -> None:
    stamp = now()
    with get_db() as conn:
        with _write_transaction(conn):
            conn.execute('''INSERT INTO skill_learning_jobs(run_id,owner_id,session_id,project_id,forced,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET forced=MAX(forced,excluded.forced),
                status=CASE WHEN skill_learning_jobs.status='skipped' AND excluded.forced=1 THEN 'pending' ELSE skill_learning_jobs.status END,
                updated_at=excluded.updated_at''',
                (run['run_id'],run['owner_id'],run['session_id'],scope['project_id'],int(forced),stamp,stamp))


def job(run_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM skill_learning_jobs WHERE run_id=?',(run_id,)).fetchone()
        return dict(row) if row else None


def due_jobs(auto: bool, limit: int = 5) -> list[str]:
    with get_db() as conn:
        return [row[0] for row in conn.execute('''SELECT run_id FROM skill_learning_jobs WHERE attempts<3
            AND (? OR forced=1) AND (COALESCE(error_code,'')!='hourly_budget' OR updated_at < datetime(?,'-1 hour')) AND (status='pending' OR (status='processing' AND lease_until<?))
            ORDER BY created_at LIMIT ?''',(int(auto),now(),now(),limit)).fetchall()]


def claim_job(run_id: str):
    token = uuid4().hex
    until = (datetime.now(timezone.utc)+timedelta(seconds=120)).strftime('%Y-%m-%d %H:%M:%S')
    with get_db() as conn:
        with _write_transaction(conn):
            n = conn.execute('''UPDATE skill_learning_jobs SET status='processing',claim=?,lease_until=?,attempts=attempts+1,updated_at=?
                WHERE run_id=? AND attempts<3 AND (status='pending' OR (status='processing' AND lease_until<?))''',
                (token,until,now(),run_id,now())).rowcount
            if not n: return None
            return dict(conn.execute('SELECT * FROM skill_learning_jobs WHERE run_id=?',(run_id,)).fetchone())


def reserve_model_call(run_id: str, token: str, limit: int) -> bool:
    cutoff = (datetime.now(timezone.utc)-timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    with get_db() as conn:
        with _write_transaction(conn):
            own = conn.execute("SELECT 1 FROM skill_learning_jobs WHERE run_id=? AND claim=? AND status='processing' AND lease_until>?",(run_id,token,now())).fetchone()
            if not own: return False
            count = conn.execute('SELECT count(*) FROM skill_learning_calls WHERE created_at>=?',(cutoff,)).fetchone()[0]
            if count >= limit: return False
            conn.execute('INSERT INTO skill_learning_calls(run_id,created_at) VALUES(?,?)',(run_id,now()))
            return True


def finish_job(run_id, token, *, status, error_code=None) -> bool:
    with get_db() as conn:
        with _write_transaction(conn):
            return conn.execute('''UPDATE skill_learning_jobs SET status=?,error_code=?,attempts=CASE WHEN ?='hourly_budget' THEN attempts-1 ELSE attempts END,claim=NULL,lease_until=NULL,updated_at=?
                WHERE run_id=? AND claim=? AND status='processing' AND lease_until>?''',
                (status,error_code,error_code,now(),run_id,token,now())).rowcount == 1


def create_candidate(job_row, draft: dict, content_hash: str, evidence: dict):
    skill_id = uuid4().hex
    with get_db() as conn:
        with _write_transaction(conn):
            own = conn.execute("SELECT 1 FROM skill_learning_jobs WHERE run_id=? AND claim=? AND status='processing' AND lease_until>?",(job_row['run_id'],job_row['claim'],now())).fetchone()
            if not own: return None
            conn.execute('''INSERT INTO learned_skills(id,owner_id,session_id,project_id,source_run_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)''',(skill_id,job_row['owner_id'],job_row['session_id'],job_row['project_id'],job_row['run_id'],now(),now()))
            conn.execute('INSERT INTO learned_skill_versions VALUES(?,?,?,?,?,?)',(skill_id,1,encode(draft),content_hash,encode(evidence),now()))
            _event(conn,skill_id,1,'model','candidate_created',{'source_run_id':job_row['run_id']})
            conn.execute("UPDATE skill_learning_jobs SET status='completed',skill_id=?,claim=NULL,lease_until=NULL,updated_at=? WHERE run_id=? AND claim=?",(skill_id,now(),job_row['run_id'],job_row['claim']))
    return skill_id


def save_run_context(run_id, digest, basis, selected: list[dict], *, scope: dict, worker_id=None) -> None:
    from app.services.chat_run_state import chat_run_claim
    from app.repository.chat_runs import _owns_active_run
    from app.repository.run_steps import _assert_context_open,StaleRunClaim
    claim=chat_run_claim.get()
    if claim:
        if claim[0]!=run_id:raise StaleRunClaim('input context run differs from owner')
        worker_id=claim[1]
    with get_db() as conn:
        with _write_transaction(conn):
            _assert_context_open()
            if not worker_id or not _owns_active_run(conn,run_id,worker_id):raise StaleRunClaim('input context requires a live run claim')
            conn.execute('INSERT OR IGNORE INTO skill_run_contexts VALUES(?,?,?,?,?,?,?,?)',(run_id,digest,basis,encode(selected),scope['owner_id'],scope['id'],scope['project_id'],now()))
            _assert_context_open()


def get_run_context(run_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM skill_run_contexts WHERE run_id=?',(run_id,)).fetchone()
        return dict(row) if row else None


def record_loaded(skill_id, version, run_id, delivery='tool') -> None:
    with get_db() as conn:
        with _write_transaction(conn):
            from app.services.chat_run_state import chat_run_claim
            from app.repository.chat_runs import _owns_active_run
            from app.repository.run_steps import _assert_context_open,StaleRunClaim
            claim=chat_run_claim.get()
            _assert_context_open()
            if not claim or claim[0]!=run_id or not _owns_active_run(conn,run_id,claim[1]):
                raise StaleRunClaim('loading a learned skill requires a live owning run')
            current=conn.execute("SELECT current_version,state FROM learned_skills WHERE id=?",(skill_id,)).fetchone()
            if not current or current['current_version']!=version or current['state'] in {'disabled','suspended'}:
                raise ValueError('skill changed before delivery')
            conn.execute("INSERT INTO learned_skill_usage(skill_id,version,run_id,loaded_at,delivery) VALUES(?,?,?,?,?) ON CONFLICT(skill_id,version,run_id) DO UPDATE SET delivery=CASE WHEN excluded.delivery='tool' THEN 'tool' ELSE delivery END",(skill_id,version,run_id,now(),delivery))
            _assert_context_open()


def pending_usages(run_id):
    with get_db() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM learned_skill_usage WHERE run_id=? AND status='pending'",(run_id,)).fetchall()]


def validation_runs(skill_id, version):
    with get_db() as conn:
        return [dict(row) for row in conn.execute('''SELECT u.*,c.input_digest,c.input_basis FROM learned_skill_usage u
            LEFT JOIN skill_run_contexts c ON c.run_id=u.run_id WHERE u.skill_id=? AND u.version=? ORDER BY u.loaded_at''',(skill_id,version)).fetchall()]


def save_usage_outcome(skill_id, version, run_id, status, evidence) -> bool:
    with get_db() as conn:
        with _write_transaction(conn):
            n=conn.execute("UPDATE learned_skill_usage SET status=?,evidence_json=? WHERE skill_id=? AND version=? AND run_id=? AND status='pending'",(status,encode(evidence),skill_id,version,run_id)).rowcount
            return n==1


def set_state(skill_id, version, state, reason, *, actor='system', review_status=None) -> bool:
    with get_db() as conn:
        with _write_transaction(conn):
            n=conn.execute('UPDATE learned_skills SET state=?,reason=?,review_status=COALESCE(?,review_status),updated_at=? WHERE id=? AND current_version=?',(state,reason,review_status,now(),skill_id,version)).rowcount
            if n: _event(conn,skill_id,version,actor,state,{'reason':reason,'review_status':review_status})
            return n==1


def edit_skill(skill_id, version, draft, content_hash):
    with get_db() as conn:
        with _write_transaction(conn):
            old=conn.execute('SELECT * FROM learned_skill_versions WHERE skill_id=? AND version=?',(skill_id,version)).fetchone()
            if not old: return False
            n=conn.execute("UPDATE learned_skills SET current_version=current_version+1,state='candidate',review_status='pending',reason='edited_requires_revalidation',updated_at=? WHERE id=? AND current_version=?",(now(),skill_id,version)).rowcount
            if not n: return False
            evidence=json.loads(old['evidence_json']);evidence['requires_human_review']=True;evidence['edited_procedure']=True
            conn.execute('INSERT INTO learned_skill_versions VALUES(?,?,?,?,?,?)',(skill_id,version+1,encode(draft),content_hash,encode(evidence),now()))
            _event(conn,skill_id,version+1,'user','edited',{'prior_version':version})
            return True


def feedback(run_id):
    with get_db() as conn:
        row=conn.execute('SELECT * FROM skill_run_feedback WHERE run_id=?',(run_id,)).fetchone()
        return dict(row) if row else None


def save_feedback(run_id, rating, comment):
    with get_db() as conn:
        with _write_transaction(conn):
            conn.execute('INSERT INTO skill_run_feedback VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET rating=excluded.rating,comment=excluded.comment,updated_at=excluded.updated_at',(run_id,rating,comment,now()))
            if rating=='needs_work':
                rows=conn.execute('''SELECT DISTINCT s.id,s.current_version FROM learned_skills s LEFT JOIN learned_skill_usage u ON s.id=u.skill_id
                    WHERE (s.source_run_id=? AND s.current_version=1) OR (u.run_id=? AND u.version=s.current_version)''',(run_id,run_id)).fetchall()
                for row in rows:
                    conn.execute("UPDATE learned_skills SET state='suspended',reason='negative_user_feedback',updated_at=? WHERE id=?",(now(),row['id']))
                    _event(conn,row['id'],row['current_version'],'user','suspended',{'run_id':run_id,'reason':'negative_user_feedback'})


def candidates_for_run(run_id):
    with get_db() as conn:
        ids=[row[0] for row in conn.execute('SELECT id FROM learned_skills WHERE source_run_id=?',(run_id,)).fetchall()]
    return [get_skill(identity) for identity in ids]


def event_history(skill_id):
    with get_db() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM learned_skill_events WHERE skill_id=? ORDER BY id DESC LIMIT 100',(skill_id,)).fetchall()]


def validation_runs_for_run(run_id):
    with get_db() as conn:
        return [dict(row) for row in conn.execute('SELECT skill_id,version FROM learned_skill_usage WHERE run_id=?',(run_id,)).fetchall()]


def terminal_pending_usage_runs():
    with get_db() as conn:
        return [row[0] for row in conn.execute("SELECT DISTINCT u.run_id FROM learned_skill_usage u JOIN chat_runs r ON r.run_id=u.run_id WHERE u.status='pending' AND r.status IN ('succeeded','failed','cancelled') LIMIT 20").fetchall()]
