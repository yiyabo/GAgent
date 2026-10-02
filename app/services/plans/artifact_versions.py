"""Immutable plan versions and recoverable fenced manifest publication.

The manifest is the linear publication point. SQL and UI are replayable receipts.
"""
from __future__ import annotations
import contextvars,fcntl,hashlib,json,os,shutil,tempfile,time,uuid
from contextlib import contextmanager
from dataclasses import dataclass,field
from pathlib import Path
from app.services.foundation.settings import get_settings
from app.services.run_budget import check_run_active

active_binding=contextvars.ContextVar('artifact_binding',default=None)


class ArtifactRevisionConflict(RuntimeError):pass
class StaleArtifactInputs(RuntimeError):pass


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def definition(node):
    from .output_spec import parse_output_spec
    meta=node.metadata or {};raw=meta.get('output_spec') or {}
    outputs=meta.get('required_outputs') or raw.get('required_outputs') or []
    parsed=parse_output_spec({'required_outputs':outputs,'source':'explicit'})
    contract=meta.get('artifact_contract') or raw.get('artifact_contract') or {}
    canonical_contract={name:sorted(set(contract.get(name) or [])) for name in ('requires','publishes','required_resources')}
    criteria=meta.get('acceptance_criteria') or raw.get('acceptance_criteria') or None
    if meta.get('acceptance_criteria_source')=='inferred_text':criteria=None
    return digest({'instruction':node.instruction or node.name,'dependencies':sorted(node.dependencies),'outputs':parsed.to_dict()['required_outputs'] if parsed else outputs,'contract':canonical_contract,'criteria':criteria,'method_parameters':meta.get('method_parameters'),'source_inputs':meta.get('source_inputs')})


def content_hash(path:Path):
    if path.is_symlink():raise ValueError('version_source_is_symlink')
    if path.is_file():
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for part in iter(lambda:stream.read(1024*1024),b''):h.update(part)
        return h.hexdigest()
    if path.is_dir():
        rows=[]
        for p in sorted(path.rglob('*')):
            if p.is_symlink():raise ValueError('version_tree_has_symlink')
            rows.append((str(p.relative_to(path)), 'file' if p.is_file() else 'directory',p.stat().st_size if p.is_file() else 0,content_hash(p) if p.is_file() else None))
        return digest(rows)
    raise FileNotFoundError(str(path))


def snapshot(source:Path,root:Path):
    before=content_hash(source);objects=root/'version_blobs';objects.mkdir(parents=True,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix='.stage-',dir=objects));copy=temporary/source.name
    try:
        if source.is_dir():shutil.copytree(source,copy)
        else:shutil.copy2(source,copy)
        for file in ([copy] if copy.is_file() else [p for p in copy.rglob("*") if p.is_file()]):
            with file.open("rb") as stream:os.fsync(stream.fileno())
        if content_hash(copy)!=before or content_hash(source)!=before:raise StaleArtifactInputs('source_changed_during_snapshot')
        target=objects/before
        try:os.rename(temporary,target)
        except OSError:
            if not (target/source.name).exists():
                # Same bytes with a different name retain an independent immutable path.
                target=objects/(before+'-'+digest(source.name)[:12]);os.rename(temporary,target)
        path=target/source.name
        descriptor=os.open(objects,os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
        if content_hash(path)!=before:raise ValueError('immutable_blob_corrupt')
        return before,str(path)
    finally:
        if temporary.exists():shutil.rmtree(temporary)


def _read(path):
    if not path.exists():return {'schema_version':1,'revision':0,'artifacts':{},'versions':{},'publications':{},'bindings':{}}
    data=json.loads(path.read_text());data.setdefault('revision',0)
    for key in ('artifacts','versions','publications','bindings'):data.setdefault(key,{})
    return data


def _atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.manifest-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:json.dump(data,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_suffix(path.suffix+'.lock').open('a+') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


def versioned(plan_id,session_id=None):
    from .artifact_contracts import artifact_manifest_path
    return bool(getattr(get_settings(),'artifact_versioning_enabled',False) or _read(artifact_manifest_path(plan_id,session_id)).get('schema_version')==2)


def _version(manifest,alias,source,task_id,root,binding=None,validation=None):
    blob,path=snapshot(Path(source),root)
    identity=uuid.uuid4().hex
    record={'artifact_version_id':identity,'blob_id':blob,'alias':alias,'path':path,'source_path':str(source),'producer_task_id':task_id,'created_at':time.time(),'validation':validation or {},'validated':bool((validation or {}).get('validated',True)),'binding':binding,'origin':'execution' if binding else 'legacy_import'}
    manifest['versions'][identity]=record
    manifest['artifacts'][alias]=dict(record)
    return record


def import_legacy(manifest,root):
    if manifest.get('schema_version')==2:return manifest
    old=json.loads(json.dumps(manifest));manifest.setdefault('versions',{});manifest.setdefault('publications',{});manifest.setdefault('bindings',{});manifest.setdefault('revision',0)
    manifest['schema_version']=2;manifest['legacy_manifest']=old
    for alias,entry in old.get('artifacts',{}).items():
        if isinstance(entry,dict) and entry.get('path') and Path(entry['path']).exists():
            _version(manifest,alias,entry['path'],entry.get('producer_task_id'),root,validation=entry.get('validation'))
    return manifest


def stage(plan_id,alias,source_path,producer_task_id,manifest,session_id=None):
    from .artifact_contracts import canonical_plan_root
    from .artifact_contracts import validate_artifact
    root=canonical_plan_root(plan_id,session_id)
    import_legacy(manifest,root)
    validation=validate_artifact(alias,source_path).to_dict()
    binding=active_binding.get()
    record=_version(manifest,alias,source_path,producer_task_id,root,binding.to_dict() if binding else None,validation)
    record['validated']=bool(validation.get('validated') and validation.get('schema_valid'))
    manifest['artifacts'][alias]=dict(record)
    if binding:binding.pending=manifest
    return record


@dataclass
class Binding:
    plan_id:int
    task_id:int
    session_id:str|None
    definition_hash:str
    revision:int
    inputs:dict
    expected_inputs:dict
    source_inputs:dict=field(default_factory=dict)
    pending:dict|None=None
    publication_id:str=field(default_factory=lambda:uuid.uuid4().hex)
    instruction:str=""
    def to_dict(self):return {"publication_id":self.publication_id,"instruction":self.instruction,'definition_hash':self.definition_hash,'inputs':self.inputs,'source_inputs':self.source_inputs,'task_id':self.task_id,'session_id':self.session_id}


def bind(executor,plan_id,node,cfg):
    from .artifact_contracts import artifact_manifest_path,canonical_plan_root,resolve_artifact_contract_with_provenance
    session=(cfg.session_context or {}).get('session_id')
    tree=executor._repo.get_plan_tree(plan_id);store=tree.metadata.get('artifact_store_ref')
    if store and store.get('session_id')!=session:raise ArtifactRevisionConflict('artifact_store_mismatch')
    if not store:executor._repo.update_plan_metadata(plan_id,{'artifact_store_ref':{'session_id':session}})
    manifest=_read(artifact_manifest_path(plan_id,session));expected={a:e.get('artifact_version_id') for a,e in manifest['artifacts'].items() if isinstance(e,dict)}
    import_legacy(manifest,canonical_plan_root(plan_id,session))
    contract=resolve_artifact_contract_with_provenance(task_name=node.name,instruction=node.instruction or '',metadata=node.metadata)
    required=contract.requires()
    inputs={a:manifest['artifacts'][a]['artifact_version_id'] for a in required if a in manifest['artifacts'] and manifest['artifacts'][a].get('artifact_version_id')}
    source_inputs={}
    raw=(node.metadata or {}).get('source_inputs',{})
    for name,path in raw.items() if isinstance(raw,dict) else []:
        blob,immutable=snapshot(Path(path),canonical_plan_root(plan_id,session));source_inputs[name]={'source':str(path),'blob_id':blob,'path':immutable}
    binding=Binding(plan_id,node.id,session,definition(node),manifest['revision'],inputs,{a:expected.get(a) for a in inputs},source_inputs,manifest)
    binding.instruction=node.instruction or node.name
    context=dict(cfg.session_context or {});context['frozen_input_artifacts']={a:manifest['versions'][v]['path'] for a,v in inputs.items()};context['frozen_input_artifacts'].update({n:x['path'] for n,x in source_inputs.items()})
    cfg.session_context=context
    return binding


def execute_bound(executor,plan_id,node,tree,cfg,factory):
    from .artifact_contracts import canonical_plan_root
    session=(cfg.session_context or {}).get('session_id')
    if not versioned(plan_id,session):return factory()
    root=canonical_plan_root(plan_id,session);root.mkdir(parents=True,exist_ok=True)
    with (root/f'.task-{node.id}.execution.lock').open('a+') as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ArtifactRevisionConflict('task_already_running')
        try:return _execute_bound(executor,plan_id,node,tree,cfg,factory)
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


def _execute_bound(executor,plan_id,node,tree,cfg,factory):
    session=(cfg.session_context or {}).get('session_id')
    if not versioned(plan_id,session):return factory()
    recover(executor._repo,plan_id)
    binding=bind(executor,plan_id,node,cfg)
    from app.services.run_resume import current_resume_source
    if current_resume_source():
        from .artifact_contracts import artifact_manifest_path
        prior=_read(artifact_manifest_path(plan_id,session))['bindings'].get(str(node.id))
        if prior and (prior.get('definition_hash')!=binding.definition_hash or prior.get('inputs')!=binding.inputs):raise StaleArtifactInputs('resume_binding_changed')
    handle=active_binding.set(binding)
    try:return factory()
    finally:active_binding.reset(handle)


def save(plan_id,manifest,session_id=None):
    from .artifact_contracts import artifact_manifest_path
    path=artifact_manifest_path(plan_id,session_id);binding=active_binding.get()
    if binding and binding.plan_id==plan_id:
        from .artifact_contracts import canonical_plan_root
        import_legacy(manifest,canonical_plan_root(plan_id,session_id))
        for alias,entry in list(manifest['artifacts'].items()):
            if entry.get('path') and not entry.get('artifact_version_id'):
                version=_version(manifest,alias,entry['path'],entry.get('producer_task_id'),canonical_plan_root(plan_id,session_id),binding.to_dict(),entry.get('validation'))
                manifest['artifacts'][alias]={**entry,**version,'validated':entry.get('validated',version['validated'])}
        binding.pending=manifest
        return path
    with locked(path):
        current=_read(path)
        if manifest.get('revision',0)!=current['revision']:raise ArtifactRevisionConflict('manifest_revision_conflict')
        check_run_active();data=dict(manifest);data['schema_version']=2;data['revision']=current['revision']+1;_atomic(path,data)
    return path


def overlay(plan_id,session_id):
    binding=active_binding.get()
    return binding.pending if binding and binding.plan_id==plan_id and binding.session_id==session_id else None


def commit_result(executor,binding,payload,status):
    from app.database import plan_db_connection
    from app.repository.plan_storage import get_plan_db_path
    from .artifact_contracts import artifact_manifest_path
    path=artifact_manifest_path(binding.plan_id,binding.session_id)
    with locked(path):
        current=_read(path)
        if binding.publication_id in current['publications']:return True
        if current['revision']!=binding.revision:raise ArtifactRevisionConflict('manifest_revision_conflict')
        proposed=json.loads(json.dumps(binding.pending or current));proposed['schema_version']=2
        with plan_db_connection(get_plan_db_path(binding.plan_id),before_commit=lambda:_atomic(path,proposed)) as con:
            con.execute('BEGIN IMMEDIATE')
            latest=executor._repo._get_node_from_conn(con,binding.plan_id,binding.task_id)
            stale=definition(latest)!=binding.definition_hash or any(current['artifacts'].get(a,{}).get('artifact_version_id')!=v for a,v in binding.expected_inputs.items())
            stale=stale or any(not Path(x['source']).exists() or content_hash(Path(x['source']))!=x['blob_id'] for x in binding.source_inputs.values())
            if stale or status not in {'completed','done'}:
                proposed['artifacts']=current['artifacts'];proposed['bindings']=current['bindings']
            else:
                payload.setdefault('metadata',{})['artifact_binding']=binding.to_dict()
                proposed.setdefault('bindings',{})[str(binding.task_id)]=binding.to_dict()
                executor._repo._update_task_with_conn(con,binding.plan_id,binding.task_id,status=status,execution_result=json.dumps(payload,ensure_ascii=False))
            from app.services.chat_run_state import chat_run_claim
            claim=chat_run_claim.get()
            receipt={'prepared_binding':binding.to_dict(),'run_id':claim[0] if claim else None,'worker_id':claim[1] if claim else None,'publication_id':binding.publication_id,'task_id':binding.task_id,'payload':payload,'status':status,'stale':stale,'session_id':binding.session_id,'applied':False}
            proposed['revision']=current['revision']+1;proposed.setdefault('publications',{})[binding.publication_id]=receipt
            con.execute('CREATE TABLE IF NOT EXISTS artifact_publications(id TEXT PRIMARY KEY,receipt_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)')
            con.execute('INSERT OR IGNORE INTO artifact_publications(id,receipt_json) VALUES(?,?)',(binding.publication_id,json.dumps(receipt,ensure_ascii=False)))
    if stale:raise StaleArtifactInputs('desired_inputs_changed')
    project(binding.plan_id,path,binding.publication_id)
    return True


def project(plan_id,path,publication_id):
    data=_read(path);receipt=data['publications'].get(publication_id)
    if not receipt or receipt.get('applied') or receipt.get('stale'):return
    from app.services.artifacts.events import ArtifactEvent
    from app.services.artifacts.projector import get_registry_projector
    session=receipt.get('session_id')
    try:
        if session:
            events=[ArtifactEvent(session_id=session,file_path=e['path'],alias=a,producer_kind='plan_task',producer_plan_id=plan_id,producer_task_id=receipt['task_id'],file_sha256=e['blob_id'],publish_requested=True) for a,e in data['artifacts'].items() if e.get('producer_task_id')==receipt['task_id']]
            get_registry_projector().consume_plan_events(session_id=session,events=events,plan_id=plan_id,task_id=receipt['task_id'],task_name='',task_instruction='')
        with locked(path):
            latest=_read(path);latest['publications'][publication_id]['applied']=True;_atomic(path,latest)
    except Exception:
        # The committed version remains available; reconciliation can retry projection.
        return


def freshness(node,manifest):
    if manifest.get('schema_version')!=2:return {'freshness':'unknown','stale_reasons':[]}
    binding=manifest.get('bindings',{}).get(str(node.id))
    if not binding:return {'freshness':'unknown','stale_reasons':[]}
    reasons=[]
    if binding.get('definition_hash')!=definition(node):reasons.append('task_definition_changed')
    for alias,version in binding.get('inputs',{}).items():
        entry=manifest.get('artifacts',{}).get(alias,{})
        if entry.get('artifact_version_id')!=version:reasons.append('input_version_changed:'+alias)
        elif not Path(entry.get('path','')).exists() or content_hash(Path(entry['path']))!=entry.get('blob_id'):reasons.append('input_blob_unavailable:'+alias)
    for name,entry in binding.get('source_inputs',{}).items():
        if not Path(entry['source']).exists() or content_hash(Path(entry['source']))!=entry['blob_id']:reasons.append('source_changed:'+name)
    for alias,entry in manifest.get('artifacts',{}).items():
        if entry.get('producer_task_id')==node.id and (not Path(entry.get('path','')).exists() or content_hash(Path(entry['path']))!=entry.get('blob_id')):reasons.append('output_unavailable:'+alias)
    pending=any(r.get('publication_id')==binding.get('publication_id') and not r.get('applied') and not r.get('stale') for r in manifest.get('publications',{}).values())
    return {'freshness':'stale' if reasons else 'reconciling' if pending else 'fresh','stale_reasons':reasons}


def recover(repo,plan_id):
    """Replay committed receipts, never a tool call or a cancelled parent run."""
    from .artifact_recompute import session_for
    from .artifact_contracts import artifact_manifest_path
    from app.database import plan_db_connection
    from app.repository.plan_storage import get_plan_db_path
    from app.repository.chat_runs import get_chat_run
    session=session_for(repo,plan_id);path=artifact_manifest_path(plan_id,session)
    if not path.exists():return
    for identity,receipt in list(_read(path)['publications'].items()):
        if receipt.get('applied') or receipt.get('stale'):continue
        with locked(path):
            manifest=_read(path);r=manifest['publications'].get(identity)
            if not r or r.get('applied'):continue
            binding=(r['payload'].get('metadata') or {}).get('artifact_binding') or {}
            if manifest.get('bindings',{}).get(str(r['task_id']),{}).get('publication_id')!=identity:continue
            parent=get_chat_run(r['run_id']) if r.get('run_id') else None
            if parent and parent['status'] in {'failed','cancelled'}:continue
            with plan_db_connection(get_plan_db_path(plan_id)) as con:
                con.execute('BEGIN IMMEDIATE')
                con.execute('CREATE TABLE IF NOT EXISTS artifact_publications(id TEXT PRIMARY KEY,receipt_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)')
                saved=con.execute('SELECT id FROM artifact_publications WHERE id=?',(identity,)).fetchone()
                if not saved:
                    node=repo._get_node_from_conn(con,plan_id,r['task_id'])
                    if definition(node)!=binding.get('definition_hash'):continue
                    repo._update_task_with_conn(con,plan_id,r['task_id'],status=r['status'],execution_result=json.dumps(r['payload'],ensure_ascii=False))
                    con.execute('INSERT INTO artifact_publications(id,receipt_json) VALUES(?,?)',(identity,json.dumps(r,ensure_ascii=False)))
        project(plan_id,path,identity)


def reconcile_recent():
    from app.database import get_db
    from app.repository.plan_repository import PlanRepository
    with get_db() as con:ids=[r[0] for r in con.execute("SELECT id FROM plans WHERE metadata LIKE '%artifact_store_ref%' ORDER BY updated_at DESC LIMIT 50")]
    repo=PlanRepository()
    for identity in ids:
        try:recover(repo,identity)
        except (ValueError,ArtifactRevisionConflict,OSError):continue


def promote_delegate_outputs(executor,node,payload,context):
    """Resolve producer-scoped scratch outputs, not basename-only guesses."""
    from app.services.session_paths import get_runtime_session_dir
    session=(context or {}).get('session_id')
    if not session:return payload
    root=get_runtime_session_dir(session).resolve()
    _,directory=executor._resolve_task_tool_workspace(node,session_id=session)
    task=Path(directory).resolve();groups={}
    for raw in executor._extract_path_like_values(payload):
        path=Path(raw).resolve()
        if not path.is_file() or root not in path.parents:continue
        if path.parent==task:continue
        groups.setdefault(path.name,{}).setdefault(content_hash(path),path)
    promoted=[]
    for name,hashes in groups.items():
        if len(hashes)!=1:raise ArtifactRevisionConflict('ambiguous_delegate_output:'+name)
        checksum,source=next(iter(hashes.items()));target=task/name
        if target.exists() and content_hash(target)!=checksum:raise ArtifactRevisionConflict('delegate_target_conflict:'+name)
        if not target.exists():task.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        promoted.append(str(target))
    if not promoted:return payload
    result=dict(payload);metadata=dict(payload.get('metadata') or {})
    metadata['source_artifact_paths']=list(payload.get('artifact_paths') or [])
    canonical=[str(Path(p).resolve()) for p in payload.get('artifact_paths',[]) if Path(p).resolve().parent==task]
    result['artifact_paths']=list(dict.fromkeys([*canonical,*promoted]))
    metadata['artifact_paths']=list(result['artifact_paths'])
    metadata['derived_mirrors']=[{'path':p,'source':str(next(iter(groups[Path(p).name].values()))),'sha256':content_hash(Path(p))} for p in promoted]
    result['metadata']=metadata;return result
