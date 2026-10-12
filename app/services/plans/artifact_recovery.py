"""Reviewable recovery of selected historical files; never certify scientific content.

prepare is read-only. apply copies hash-bound files, publishes unchecked records,
then atomically marks the audited tasks for review. A journal makes replay safe.
"""
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET

from app.repository.plan_artifact_queries import artifact_store_session
from .artifact_contracts import artifact_manifest_path, save_artifact_manifest


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def file_hash(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def task_fingerprint(node):
    return digest([node.name,node.instruction,node.parent_id,node.status,node.metadata,node.execution_result])


def inspect_file(path):
    if not path.is_file() or path.stat().st_size==0:
        raise ValueError('missing_or_empty_file')
    suffix=path.suffix.lower()
    facts={'format':suffix,'bytes':path.stat().st_size,'content_status':'unchecked'}
    if suffix in {'.csv','.tsv'}:
        with path.open(encoding='utf-8-sig',newline='') as f:
            rows=csv.reader(f,delimiter='\t' if suffix=='.tsv' else ',');header=next(rows)
            if not header or len(header)!=len(set(header)) or any(not x.strip() for x in header):
                raise ValueError('invalid_table_header')
            count=0
            for row in rows:
                if len(row)!=len(header): raise ValueError('inconsistent_table_width')
                count+=1
            if not count: raise ValueError('empty_table')
            facts.update(rows=count,columns=header)
    elif suffix in {'.json','.jsonl'}:
        text=path.read_text()
        value=[json.loads(line) for line in text.splitlines() if line.strip()] if suffix=='.jsonl' else json.loads(text)
        if not value: raise ValueError('empty_json')
        facts['entries']=len(value) if isinstance(value,(list,dict)) else 1
    elif suffix in {'.md','.txt','.bib'}:
        text=path.read_text(encoding='utf-8')
        if len(text.strip())<40: raise ValueError('insufficient_text')
        facts['characters']=len(text)
    elif suffix=='.pdf':
        from pypdf import PdfReader
        facts['pages']=len(PdfReader(path).pages)
        if not facts['pages']: raise ValueError('empty_pdf')
    elif suffix=='.svg':
        if ET.parse(path).getroot().tag.split('}')[-1]!='svg': raise ValueError('invalid_svg')
    elif suffix in {'.png','.jpg','.jpeg'}:
        from PIL import Image
        with Image.open(path) as im:
            facts['dimensions']=list(im.size);im.verify()
    else:
        raise ValueError('unsupported_recovery_format')
    facts['sha256']=file_hash(path)
    return facts


def _session_root(session_id):
    from app.services.path_router import get_path_router
    return get_path_router().get_session_dir(session_id,create=False).resolve()


def _source(tree,session,path,task_id):
    relative=Path(path)
    if relative.is_absolute() or '..' in relative.parts or any(x in relative.parts for x in ('scratch','_upstream','probe','uploads','tool_outputs')):
        raise ValueError('not_a_task_delivery_path')
    node=tree.get_node(task_id) if hasattr(tree,'get_node') else tree.nodes[task_id]
    ids=[node.id]
    while node.parent_id is not None:
        node=tree.nodes[node.parent_id];ids.insert(0,node.id)
    root=_session_root(session)
    task_root=root.joinpath('raw_files',*(f'task_{i}' for i in ids)).resolve()
    source=(root/relative).resolve()
    if not source.is_relative_to(task_root) or not source.is_relative_to(root):
        raise ValueError('task_source_mismatch')
    return source


def prepare_recovery(repo,plan_id,selections,*,review_task_ids):
    tree=repo.get_plan_tree(plan_id);session=artifact_store_session(plan_id)
    if not session: raise ValueError('missing_artifact_store')
    if set(review_task_ids)-set(tree.nodes): raise ValueError('unknown_review_task')
    entries=[]
    for selection in selections:
        task_id=int(selection['task_id']);source=_source(tree,session,selection['path'],task_id)
        facts=inspect_file(source)
        entries.append({'task_id':task_id,'relative_path':selection['path'],'checks':facts,
                        'note':str(selection.get('note') or 'Recovered historical file; content awaits review.')})
    manifest_path=artifact_manifest_path(plan_id,session)
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if manifest.get('schema_version',1)!=1: raise ValueError('recovery_requires_v1_manifest')
    payload={'plan_id':plan_id,'session_id':session,'entries':entries,'manifest_digest':digest(manifest),
             'task_fingerprints':{str(i):task_fingerprint(tree.nodes[i]) for i in review_task_ids}}
    return {**payload,'recovery_id':digest(payload)}


def apply_recovery(repo,preview):
    plan_id=preview['plan_id'];session=preview['session_id'];rid=preview['recovery_id']
    if digest({k:v for k,v in preview.items() if k!='recovery_id'})!=rid: raise ValueError('modified_recovery_preview')
    if artifact_store_session(plan_id)!=session: raise ValueError('artifact_store_changed')
    root=artifact_manifest_path(plan_id,session).parent;root.mkdir(parents=True,exist_ok=True)
    with (root/'recovery.lock').open('a') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        path=artifact_manifest_path(plan_id,session)
        manifest=json.loads(path.read_text()) if path.exists() else {}
        if (manifest.get('recoveries') or {}).get(rid,{}).get('status')=='applied':
            return {'recovery_id':rid,'status':'already_applied','files':len(preview['entries'])}
        if manifest.get('schema_version',1)!=1: raise ValueError('recovery_requires_v1_manifest')
        pending=(manifest.get('recoveries') or {}).get(rid)
        if not pending and digest(manifest)!=preview['manifest_digest']: raise ValueError('manifest_changed')
        tree=repo.get_plan_tree(plan_id)
        for tid,expected in preview['task_fingerprints'].items():
            node=tree.nodes[int(tid)]
            if node.metadata.get('artifact_recovery_id')!=rid and task_fingerprint(node)!=expected:
                raise ValueError('task_changed_since_preview')
        # Verify every source before copying or publishing any file.
        sources=[]
        for entry in preview['entries']:
            source=_source(tree,session,entry['relative_path'],entry['task_id'])
            if file_hash(source)!=entry['checks']['sha256']: raise ValueError('source_changed_since_preview')
            sources.append(source)
        from app.repository.plan_artifact_queries import pin_artifact_store
        pin_artifact_store(plan_id, session)
        records={}
        for entry,source in zip(preview['entries'],sources):
            sha=entry['checks']['sha256'];dest=root/'recovered_blobs'/sha/source.name
            dest.parent.mkdir(parents=True,exist_ok=True)
            if not dest.exists():
                fd,tmp=tempfile.mkstemp(dir=dest.parent);os.close(fd)
                try:
                    shutil.copyfile(source,tmp)
                    if file_hash(Path(tmp))!=sha: raise ValueError('source_changed_during_copy')
                    os.replace(tmp,dest)
                finally:
                    if os.path.exists(tmp):os.unlink(tmp)
            if file_hash(dest)!=sha: raise ValueError('recovered_blob_corrupt')
            alias=f"recovered_task_{entry['task_id']}.{source.stem}_{sha[:12]}{source.suffix.replace('.', '_')}"
            records[alias]={'alias':alias,'path':str(dest),'source_path':str(source),'sha256':sha,
                'producer_task_id':entry['task_id'],'producer_plan_id':plan_id,'session_id':session,
                'updated_at':time.time(),'origin':'recovered_workspace','validated':False,
                'storage_verified':True,'content_status':'unchecked','freshness':'unknown',
                'recovery_id':rid,'recovery_checks':entry['checks'],'recovery_note':entry['note']}
        journal=root/f'recovery-{rid}.json'
        if not journal.exists():journal.write_text(json.dumps(preview,ensure_ascii=False,indent=2))
        expected_manifest = digest(manifest)
        manifest.setdefault('plan_id', plan_id)
        manifest.setdefault('artifacts',{}).update(records)
        manifest.setdefault('recoveries',{})[rid]={'status':'prepared','files':len(records)}
        save_artifact_manifest(plan_id,manifest,session,expected_digest=expected_manifest)
        from app.repository.plan_artifact_queries import mark_recovered_tasks_for_review
        mark_recovered_tasks_for_review(plan_id,preview['task_fingerprints'],rid)
        expected_manifest = digest(manifest)
        manifest['recoveries'][rid]['status']='applied'
        save_artifact_manifest(plan_id,manifest,session,expected_digest=expected_manifest)
        return {'recovery_id':rid,'status':'applied','files':len(records),'review_tasks':len(preview['task_fingerprints'])}
