"""Paged, bounded artifact metadata; file hashing belongs to validation, not GET."""
import base64
import hashlib
import json
from .artifact_contracts import artifact_manifest_path
from app.repository.plan_artifact_queries import artifact_store_session, published_receipts

FIELDS = {'path','deliverable_path','version','artifact_version_id','freshness','origin',
          'validated','storage_verified','content_status','updated_at','created_at','sha256'}


class ArtifactListingConflict(ValueError):
    pass


def _entry(plan_id, task_id, alias, value, session_id):
    if not isinstance(value, dict):
        return None
    if value.get('plan_id', plan_id) != plan_id or value.get('producer_plan_id', plan_id) != plan_id:
        return None
    if task_id is not None and value.get('producer_task_id', task_id) != task_id:
        return None
    record = {k:v for k,v in value.items() if k in FIELDS and isinstance(v,(str,int,float,bool))}
    # Prevent arbitrary receipt strings becoming unbounded list responses.
    record = {k:(v[:4096] if isinstance(v,str) else v) for k,v in record.items()}
    binding = value.get('binding') if isinstance(value.get('binding'), dict) else {}
    if not isinstance(task_id, int): task_id = None
    record.update(alias=str(alias)[:512], producer_task_id=task_id,
                  session_id=binding.get('session_id') or value.get('session_id') or session_id)
    return record


def list_artifacts(plan_id, *, task_id=None, cursor=None, limit=50):
    session = artifact_store_session(plan_id)
    path = artifact_manifest_path(plan_id, session)
    # Corrupt manifests must produce a visible error, never a false empty list.
    manifest = json.loads(path.read_text()) if path.exists() else {'artifacts': {}}
    if not isinstance(manifest,dict) or not isinstance(manifest.get('artifacts',{}),dict):
        raise ValueError('invalid_artifact_manifest')
    entries = {}
    for producer, records in published_receipts(plan_id, task_id):
        if not isinstance(records,dict):
            continue
        for alias, record in records.items():
            entry = _entry(plan_id, producer, alias, record, session)
            if entry: entries[alias] = entry
    # Manifest pointers supersede older receipts, including changed producers.
    for alias, record in manifest.get('artifacts',{}).items():
        entries.pop(alias, None)
        producer = record.get('producer_task_id') if isinstance(record,dict) else None
        if task_id is not None and producer != task_id:
            continue
        entry = _entry(plan_id, producer, alias, record, session)
        if entry: entries[alias] = entry
    rows = sorted(entries.values(), key=lambda x:(x['producer_task_id'] or 0,x['alias']))
    unique = {}
    for row in rows:
        unique.setdefault((row['producer_task_id'], row.get('path') or row['alias']), row)
    rows = list(unique.values())
    fingerprint = hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()[:24]
    offset = 0
    if cursor:
        try:
            frozen, offset = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if not isinstance(offset,int) or offset<0: raise ValueError()
        except Exception as exc:
            raise ValueError('invalid_artifact_cursor') from exc
        if frozen != fingerprint: raise ArtifactListingConflict('artifact_list_changed')
    page, size = [], 0
    for entry in rows[offset:offset+limit]:
        weight = len(json.dumps(entry,ensure_ascii=False).encode())
        if page and size+weight>90000: break
        page.append(entry); size += weight
    end = offset+len(page)
    next_cursor = base64.urlsafe_b64encode(json.dumps([fingerprint,end]).encode()).decode() if end<len(rows) else None
    return {'plan_id':plan_id,'session_id':session,'schema_version':manifest.get('schema_version',1),
            'total':len(rows),'items':page,'next_cursor':next_cursor,'snapshot':fingerprint}
