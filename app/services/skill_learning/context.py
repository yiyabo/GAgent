"""Progressive, scoped discovery. Exposure is not counted as successful skill use."""
from __future__ import annotations
import json
import re
import sqlite3
import logging

logger=logging.getLogger(__name__)
from app.repository.context_recall import session_scope
from app.repository import skill_learning as repository
from app.repository.chat_runs import get_chat_run, is_chat_run_owned
from app.services.chat_run_state import chat_run_claim
from .evidence import fingerprint_input
from .models import SkillDraft


def in_scope(skill: dict, scope: dict) -> bool:
    return skill['owner_id']==scope['owner_id'] and (
        skill['project_id']==scope['project_id'] if skill['project_id'] is not None
        else scope['project_id'] is None and skill['session_id']==scope['id'])


def _hydrate_context(context: dict, session_id: str | None, query: str, run_id: str | None = None) -> None:
    context.pop('learned_skill_context',None)
    if context.get('learned_skills_disabled') or not session_id:return
    scope=session_scope(session_id)
    if not scope:return
    if run_id is None and (claim:=chat_run_claim.get()):
        active=get_chat_run(claim[0])
        if (active and active['session_id']==session_id and active['owner_id']==scope['owner_id']
                and active['status']=='running' and is_chat_run_owned(claim[0],claim[1])):
            run_id=claim[0]
    requested=context.get('learned_skill_ids') or []
    if not isinstance(requested,list) or len(requested)>3 or not all(isinstance(x,str) for x in requested):
        raise ValueError('learned_skill_ids must contain at most three skill IDs')
    expected=context.get('learned_skill_versions') or {}
    if not isinstance(expected,dict) or len(expected)>3 or any(not isinstance(v,int) or isinstance(v,bool) or v<1 for v in expected.values()):raise ValueError('invalid learned skill version map')
    resume_source=context.get('resume_from_run_id')
    if resume_source:
        parent=get_chat_run(str(resume_source))
        if not parent or parent['owner_id']!=scope['owner_id'] or parent['session_id']!=session_id:raise ValueError('resume source session mismatch')
        original=repository.get_run_context(str(resume_source))
        if original:
            recorded=json.loads(original['selected_json'])
            requested=[item['id'] for item in recorded]
            expected={item['id']:item['version'] for item in recorded}
    selected=[]
    for identity in dict.fromkeys(requested):
        skill=repository.get_skill(identity)
        if not skill or not in_scope(skill,scope):raise ValueError('requested skill is unavailable in this project')
        if identity in expected and skill['current_version']!=expected[identity]:raise ValueError('requested skill version changed')
        if skill['state'] in {'disabled','suspended'}:raise ValueError('requested skill is disabled or needs revision')
        selected.append(skill)
    from app.services.foundation.settings import get_settings
    hybrid=getattr(get_settings(),"skill_recommendation_v2_enabled",False)
    progressive=getattr(get_settings(),"skill_context_progressive_enabled",False)
    mode="explicit" if requested else "lexical"
    if not selected and hybrid:
        from .recommendations import recommend
        result=recommend(scope,query);selected=result["skills"];mode=result["retrieval_mode"]
    if not selected and not hybrid:
        candidates=repository.list_skills(scope,80)
        for skill in candidates:
            if not in_scope(skill,scope) or skill['state']!='stable':continue
            score=sum(str(word).lower() in query.lower() for word in skill['draft']['keywords'])
            if score:selected.append((score,skill))
        selected=[item[1] for item in sorted(selected,key=lambda item:item[0],reverse=True)[:3]]
    index=[{'id':skill['id'],'version':skill['current_version'],'name':skill['public_name'],
            'description':skill['draft']['description'],'state':skill['state'],
            'validated_dimensions':skill['evidence'].get('validated_dimensions',[]),
            'requires_human_review':skill['evidence'].get('requires_human_review',True)} for skill in selected]
    bodies=[]
    budget=12000
    for skill,item in zip(selected,index):
        if progressive:continue
        body=SkillDraft.model_validate(skill['draft']).markdown(item['name'])
        if len(body)>budget:continue
        bodies.append({'name':item['name'],'body':body});budget-=len(body)
    if index:context['learned_skill_context']={'skills':index,'procedures':bodies,'experimental':bool(requested)}
    if run_id:
        digest,basis=fingerprint_input(query,context)
        repository.save_run_context(run_id,digest,basis,index,scope=scope)
        from .recommendations import record_exposure
        record_exposure(run_id,index,mode)
        for item in index:
            if any(body['name']==item['name'] for body in bodies):
                repository.record_loaded(item['id'],item['version'],run_id,delivery='prompt')


def hydrate_context(context: dict, session_id: str | None, query: str, run_id: str | None = None) -> None:
    try:
        _hydrate_context(context,session_id,query,run_id)
    except sqlite3.DatabaseError as exc:
        if context.get('learned_skill_ids'):
            raise ValueError('learned skill storage is unavailable; trial has not started') from exc
        logger.warning('Optional learned skill discovery unavailable: %s',type(exc).__name__)
        context['learned_skill_context']={'unavailable':True,'skills':[]}


def format_skill_context(context: dict | None) -> str:
    payload=(context or {}).get('learned_skill_context')
    if not isinstance(payload,dict) or not payload.get('skills'):return ''
    return ('=== LEARNED PROCEDURES AVAILABLE ===\n'
            'Read the supplied relevant procedure before applying it. Use load_skill with its exact name for sections not supplied. '
            'Candidate/trial procedures are experiments, not verified general solutions. Follow the current user request and independently verify outputs. '
            'Read validated_dimensions and limitations; file-contract validation does not prove scientific correctness.\n'
            +json.dumps(payload,ensure_ascii=False))


def load_learned_skill(name: str, session_id: str | None, *, record=True):
    match=re.fullmatch(r'learned:([0-9a-f]{32}):v([1-9][0-9]*)',name)
    if not match:return None
    claim=chat_run_claim.get()
    run=get_chat_run(claim[0]) if claim else None
    if run:
        if session_id and session_id!=run['session_id']:raise ValueError('skill session mismatch')
        session_id=run['session_id']
    scope=session_scope(session_id) if session_id else None
    skill=repository.get_skill(match[1])
    if not scope or not skill or not in_scope(skill,scope):raise ValueError('learned skill not found in this scope')
    if skill['current_version']!=int(match[2]):raise ValueError('skill version changed; reload the current index')
    if skill['state'] in {'disabled','suspended'}:raise ValueError('skill is disabled or needs revision')
    if skill['state']!='stable':
        stored=repository.get_run_context(run['run_id']) if run else None
        selected=json.loads(stored['selected_json']) if stored else []
        if not any(item['id']==skill['id'] and item['version']==skill['current_version'] for item in selected):
            raise ValueError('candidate and trial skills require an explicit trial request')
    if run and record:
        from app.repository.run_steps import assert_run_owned
        assert_run_owned(run['run_id'])
        repository.record_loaded(skill['id'],skill['current_version'],run['run_id'])
    draft=SkillDraft.model_validate(skill['draft'])
    return {'success':True,'name':name,'description':draft.description,
            'content':draft.markdown(name),'skill_id':skill['id'],'skill_version':skill['current_version'],
            'lifecycle_state':skill['state'],'validated_dimensions':skill['evidence'].get('validated_dimensions',[]),
            'requires_human_review':skill['evidence'].get('requires_human_review',True)}
