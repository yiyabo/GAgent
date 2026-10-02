"""Read-only impact previews and bounded, idempotent recompute jobs."""
import hashlib,json
from .artifact_versions import _read,definition,digest,freshness,ArtifactRevisionConflict
from .artifact_contracts import artifact_manifest_path,producer_candidates_for_alias,resolve_artifact_contract_with_provenance


def session_for(repo,plan_id):
    tree=repo.get_plan_tree(plan_id);store=tree.metadata.get('artifact_store_ref')
    if store:return store.get('session_id')
    from app.database import get_db
    with get_db() as con:rows=con.execute('SELECT id FROM chat_sessions WHERE plan_id=?',(plan_id,)).fetchall()
    if len(rows)>1:raise ArtifactRevisionConflict('ambiguous_artifact_store')
    return rows[0][0] if rows else None


def versions(repo,plan_id,alias=None,cursor=0):
    session=session_for(repo,plan_id);manifest=_read(artifact_manifest_path(plan_id,session))
    rows=[v for v in manifest['versions'].values() if alias is None or v['alias']==alias]
    rows.sort(key=lambda v:(v['created_at'],v['artifact_version_id']),reverse=True)
    nodes=repo.get_plan_tree(plan_id).nodes
    current={a:{**v,**(freshness(nodes[v['producer_task_id']],manifest) if v.get('producer_task_id') in nodes else {'freshness':'unknown'})} for a,v in manifest['artifacts'].items()}
    return {'plan_id':plan_id,'manifest_revision':manifest['revision'],'schema_version':manifest.get('schema_version',1),'current_version_id':manifest['artifacts'].get(alias,{}).get('artifact_version_id') if alias else None,'current_artifacts':current,'versions':rows[cursor:cursor+50],'next_cursor':cursor+50 if cursor+50<len(rows) else None}


def preview(repo,plan_id,request):
    tree=repo.get_plan_tree(plan_id);session=session_for(repo,plan_id);manifest=_read(artifact_manifest_path(plan_id,session))
    expected=request.get('expected_manifest_revision')
    if expected is not None and expected!=manifest['revision']:raise ArtifactRevisionConflict('manifest_revision_conflict')
    dependencies={n.id:set(n.dependencies) for n in tree.iter_nodes()};blocked=[]
    for node in tree.iter_nodes():
        contract=resolve_artifact_contract_with_provenance(task_name=node.name,instruction=node.instruction or '',metadata=node.metadata)
        for alias in contract.explicit_requires:
            producers=producer_candidates_for_alias(alias,tree.iter_nodes())
            if len(producers)==1 and producers[0]!=node.id:dependencies[node.id].add(producers[0])
            elif len(producers)>1 or (not producers and alias not in manifest['artifacts']):blocked.append(node.id)
    for n,deps in dependencies.items():
        if not deps.issubset(tree.nodes):blocked.append(n)
        deps.intersection_update(tree.nodes)
    changed=set(request.get('changed_task_ids') or [])
    for alias in request.get('changed_aliases') or []:
        changed.update(producer_candidates_for_alias(alias,tree.iter_nodes()))
        for n in tree.iter_nodes():
            if alias in resolve_artifact_contract_with_provenance(task_name=n.name,instruction=n.instruction or '',metadata=n.metadata).requires():changed.add(n.id)
    if not changed:changed={n.id for n in tree.iter_nodes() if freshness(n,manifest)['freshness']=='stale'}
    if not changed.issubset(tree.nodes):raise ValueError('unknown_changed_task')
    affected=set(changed)
    while True:
        expanded=affected|{n for n,deps in dependencies.items() if deps&affected}
        if expanded==affected:break
        affected=expanded
    targets=set(request.get('target_task_ids') or affected)
    if not targets.issubset(tree.nodes):raise ValueError('unknown_target_task')
    ancestors=set(targets)
    while True:
        expanded=ancestors|set().union(*(dependencies[n] for n in ancestors)) if ancestors else set()
        if expanded==ancestors:break
        ancestors=expanded
    selected=affected&ancestors;ordered=[];remaining=set(selected)
    while remaining:
        ready=sorted(n for n in remaining if not dependencies[n]&remaining)
        if not ready:raise ValueError('dependency_cycle')
        ordered.extend(ready);remaining.difference_update(ready)
    fingerprint=digest({'revision':manifest['revision'],'definitions':{n:definition(tree.nodes[n]) for n in sorted(ancestors)},'inputs':manifest['artifacts'],'selected':ordered})
    return {'plan_id':plan_id,'manifest_revision':manifest['revision'],'preview_fingerprint':fingerprint,'ordered_task_ids':ordered,'affected_task_ids':sorted(affected),'blocked_task_ids':sorted(set(blocked)&selected),'stale_reasons':{str(n):freshness(tree.nodes[n],manifest)['stale_reasons'] for n in sorted(selected)},'session_id':session}
