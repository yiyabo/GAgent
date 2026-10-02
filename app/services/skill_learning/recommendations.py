"""Scoped hybrid ranking; exposure, delivery and validated reuse remain separate."""
from __future__ import annotations
import asyncio,json,math,re,time
from app.database import get_db
from app.repository import skill_learning as repository
from app.services.foundation.settings import get_settings


def ensure_schema(con):
    con.execute('CREATE TABLE IF NOT EXISTS learned_skill_vectors(skill_id TEXT,version INTEGER,content_hash TEXT,model TEXT,dimension INTEGER,vector_json TEXT,created_at TEXT,PRIMARY KEY(skill_id,version,model))')
    con.execute('CREATE TABLE IF NOT EXISTS learned_skill_exposures(run_id TEXT,skill_id TEXT,version INTEGER,retrieval_mode TEXT,rank INTEGER,created_at TEXT,PRIMARY KEY(run_id,skill_id,version))')
    con.execute('CREATE TABLE IF NOT EXISTS learned_skill_index_calls(created_at TEXT NOT NULL)')


def terms(text):
    words=set(re.findall(r'[a-z0-9_-]+',text.lower()))
    for block in re.findall(r'[\u4e00-\u9fff]+',text):
        words.update(block[i:i+2] for i in range(max(1,len(block)-1)))
    return words


def representation(skill):
    draft=skill['draft']
    return '\n'.join(str(draft.get(k,'')) for k in ('name','description','when_to_use','inputs','verification','keywords'))


def cosine(a,b):
    if len(a)!=len(b) or not a:return 0
    denom=math.sqrt(sum(v*v for v in a)*sum(v*v for v in b))
    return sum(x*y for x,y in zip(a,b))/denom if denom else 0


def model_identity(service):
    client=getattr(service,'api_client',None)
    return str(getattr(client,'model',None) or getattr(service,'_provider','unknown'))


def scoped_skills(scope):
    # SQLite LIMIT -1 removes the old newest-80 visibility restriction.
    return [s for s in repository.list_skills(scope,-1) if s['state']=='stable']


def query_vector(query):
    from app.services.embeddings import get_embeddings_service
    service=get_embeddings_service();future=service.get_single_embedding_async(query)
    try:return model_identity(service),future.result(timeout=1.5)
    except Exception:
        future.cancel();return model_identity(service),None


def recommend(scope,query,*,semantic=True,limit=3):
    skills=scoped_skills(scope);tokens=terms(query)
    if not tokens or not skills:return {'skills':[],'retrieval_mode':'lexical','recommendations':[]}
    lexical=sorted([(len(tokens&terms(representation(s)))/max(1,len(tokens)),s) for s in skills],key=lambda x:(-x[0],x[1]['id']))
    lexical=[s for score,s in lexical[:20] if score>0]
    model,vector=query_vector(query) if semantic else (None,None)
    semantic_rows=[]
    if vector:
        with get_db() as con:
            rows=con.execute('''SELECT e.* FROM learned_skill_vectors e JOIN learned_skills s ON s.id=e.skill_id AND s.current_version=e.version WHERE e.model=? AND e.dimension=? AND s.owner_id=? AND ((s.project_id IS NULL AND ? IS NULL AND s.session_id=?) OR (s.project_id IS NOT NULL AND s.project_id=?))''',(model,len(vector),scope['owner_id'],scope['project_id'],scope['id'],scope['project_id'])).fetchall()
        candidates={(s['id'],s['current_version'],s['content_hash']):s for s in skills}
        for row in rows:
            skill=candidates.get((row['skill_id'],row['version'],row['content_hash']))
            if skill:
                score=cosine(vector,json.loads(row['vector_json']))
                if score>0:semantic_rows.append((score,skill))
    semantic_rows.sort(key=lambda x:(-x[0],x[1]['id']));rankings=[lexical,[s for _,s in semantic_rows[:20]]]
    scores={};selected={}
    for ranking in rankings:
        for i,skill in enumerate(ranking):scores[skill['id']]=scores.get(skill['id'],0)+1/(60+i+1);selected[skill['id']]=skill
    with get_db() as con:
        evidence_counts={(r[0],r[1]):r[2] for r in con.execute("SELECT skill_id,version,count(DISTINCT json_extract(COALESCE(evidence_json,'{}'),'$.input_digest')) FROM learned_skill_usage WHERE status='passed' GROUP BY skill_id,version")}
    ranked=sorted(selected.values(),key=lambda s:(-scores[s['id']],-evidence_counts.get((s['id'],s['current_version']),0),s['id']))
    vector_by_skill={s['id']:json.loads(r['vector_json']) for r in (rows if vector else []) for s in skills if s['id']==r['skill_id'] and s['current_version']==r['version'] and s['content_hash']==r['content_hash']}
    ordered=[];alternatives={}
    for skill in ranked:
        group=next((primary for primary in ordered if primary['draft']['domain']==skill['draft']['domain'] and (primary['content_hash']==skill['content_hash'] or cosine(vector_by_skill.get(primary['id'],[]),vector_by_skill.get(skill['id'],[]))>=.9)),None)
        if group:alternatives.setdefault(group['id'],[]).append({'id':skill['id'],'name':skill['draft']['name'],'version':skill['current_version']})
        elif len(ordered)<limit:ordered.append(skill)
    mode='hybrid' if semantic_rows else 'lexical'
    return {'skills':ordered,'retrieval_mode':mode,'recommendations':[{'id':s['id'],'version':s['current_version'],'score':scores[s['id']],'reason':'语义与关键词匹配' if mode=='hybrid' else '关键词匹配','alternatives':alternatives.get(s['id'],[]),'sample_scope':'current_version'} for s in ordered]}


def record_exposure(run_id,index,mode):
    from app.services.chat_run_state import chat_run_claim
    from app.repository.run_steps import _owned_write
    claim=chat_run_claim.get()
    if not claim or claim[0]!=run_id:return
    with _owned_write(run_id,claim[1]) as (con,_):
        for i,s in enumerate(index):con.execute('INSERT OR IGNORE INTO learned_skill_exposures VALUES(?,?,?,?,?,?)',(run_id,s['id'],s['version'],mode,i+1,repository.now()))


def stats(skill_id,version):
    with get_db() as con:
        exposures=con.execute('SELECT count(*) FROM learned_skill_exposures WHERE skill_id=? AND version=?',(skill_id,version)).fetchone()[0]
        usages=[dict(r) for r in con.execute('SELECT * FROM learned_skill_usage WHERE skill_id=? AND version=?',(skill_id,version))]
        runs=list({u['run_id'] for u in usages});tokens=0;duration=0;known=0
        for run in runs:
            rows=con.execute('''WITH RECURSIVE ids(id) AS (SELECT ? UNION SELECT l.run_id FROM llm_usage_log l JOIN ids ON l.parent_run_id=ids.id WHERE l.run_id IS NOT NULL) SELECT prompt_tokens,completion_tokens,duration_ms FROM llm_usage_log WHERE run_id IN (SELECT id FROM ids)''',(run,)).fetchall()
            if rows:known+=1
            tokens+=sum((r[0] or 0)+(r[1] or 0) for r in rows);duration+=sum(r[2] or 0 for r in rows)
    counts={name:sum(u['status']==name for u in usages) for name in ('passed','failed','unverified','pending')}
    material={json.loads(u.get('evidence_json') or '{}').get('input_digest') for u in usages if u['status']=='passed'};material.discard(None)
    return {'exposures':exposures,'body_deliveries':len(usages),'outcomes':counts,'independent_passed_materials':len(material),'co_used_run_tokens':tokens if known else None,'co_used_run_duration_ms':duration if known else None,'usage_known_runs':known,'usage_total_runs':len(runs),'cost_attribution':'co_used_run_not_causal'}


def similar(skill,scope):
    candidates=[s for s in repository.list_skills(scope,-1) if s['state']=='stable'];results=[]
    with get_db() as con:
        vectors={(r['skill_id'],r['version']):(r['model'],json.loads(r['vector_json']),r['content_hash']) for r in con.execute('''SELECT e.* FROM learned_skill_vectors e JOIN learned_skills s ON s.id=e.skill_id WHERE s.owner_id=? AND ((s.project_id IS NULL AND ? IS NULL AND s.session_id=?) OR (s.project_id IS NOT NULL AND s.project_id=?))''',(scope['owner_id'],scope['project_id'],scope['id'],scope['project_id']))}
    a=vectors.get((skill['id'],skill['loaded_version']))
    for other in candidates:
        if other['id']==skill['id'] or other['draft']['domain']!=skill['draft']['domain']:continue
        b=vectors.get((other['id'],other['current_version']))
        score=1.0 if other['content_hash']==skill['content_hash'] else cosine(a[1],b[1]) if a and b and a[0]==b[0] and a[2]==skill['content_hash'] and b[2]==other['content_hash'] else 0
        if score>=.9:results.append({'id':other['id'],'version':other['current_version'],'name':other['draft']['name'],'similarity':score,'action':'possible_duplicate_no_merge'})
    return sorted(results,key=lambda x:(-x['similarity'],x['id']))


def index_one():
    if not getattr(get_settings(),'skill_recommendation_v2_enabled',False):return
    from app.services.embeddings import get_embeddings_service
    service=get_embeddings_service();model=model_identity(service)
    from app.repository.chat_runs import _write_transaction
    with get_db() as con:
        with _write_transaction(con):
            if con.execute("SELECT count(*) FROM learned_skill_index_calls WHERE created_at>=datetime('now','-1 hour')").fetchone()[0]>=8:return
            row=con.execute('''SELECT s.id FROM learned_skills s JOIN learned_skill_versions v ON v.skill_id=s.id AND v.version=s.current_version
                WHERE s.state='stable' AND NOT EXISTS(SELECT 1 FROM learned_skill_vectors e WHERE e.skill_id=s.id AND e.version=s.current_version AND e.content_hash=v.content_hash AND e.model=?) ORDER BY s.updated_at LIMIT 1''',(model,)).fetchone()
            if not row:return
            con.execute('INSERT INTO learned_skill_index_calls VALUES(?)',(repository.now(),))
    skill=repository.get_skill(row[0]);vector=service.get_single_embedding(representation(skill))
    current=repository.get_skill(skill['id'])
    if not vector or current['current_version']!=skill['current_version'] or current['content_hash']!=skill['content_hash']:return
    with get_db() as con:con.execute('INSERT OR REPLACE INTO learned_skill_vectors VALUES(?,?,?,?,?,?,?)',(skill['id'],skill['current_version'],skill['content_hash'],model,len(vector),json.dumps(vector),repository.now()))
