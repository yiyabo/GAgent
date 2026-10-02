"""Candidate skill library, explicit method feedback and versioned experiments."""
from __future__ import annotations
import asyncio
from typing import Literal
from fastapi import APIRouter,HTTPException,Query,Request
from pydantic import BaseModel,Field
from app.routers import register_router
from app.repository import skill_learning as repository
from app.repository.chat_runs import get_chat_run
from app.repository.context_recall import session_scope
from app.services.request_principal import ensure_owner_access
from app.services.skill_learning.context import in_scope
from app.services.skill_learning.models import SkillDraft
from app.services.skill_learning.service import get_skill_learning_service

router=APIRouter(prefix='/skill-learning',tags=['skill-learning'])
register_router(namespace='skill-learning',version='v1',path='/skill-learning',router=router,tags=['skill-learning'],description='Evidence-backed procedural learning')


class RunAction(BaseModel):
    session_id:str


class Feedback(RunAction):
    rating:Literal['useful','needs_work']
    comment:str=Field(default='',max_length=1000)


class Review(RunAction):
    version:int=Field(ge=1)
    decision:Literal['accept','reject','disable']


class Edit(RunAction):
    version:int=Field(ge=1)
    draft:SkillDraft


def scoped_run(run_id,session_id,request):
    run=get_chat_run(run_id)
    if not run:raise HTTPException(404,'run not found')
    ensure_owner_access(request,run['owner_id'],detail='run owner mismatch')
    if run['session_id']!=session_id:raise HTTPException(403,'session mismatch')
    return run


def scoped_skill(skill_id,session_id,request):
    skill=repository.get_skill(skill_id)
    scope=session_scope(session_id)
    if scope:ensure_owner_access(request,scope['owner_id'],detail='session owner mismatch')
    if not scope or not skill or not in_scope(skill,scope):raise HTTPException(404,'skill unavailable in this project')
    return skill


@router.get('/sessions/{session_id}')
async def list_session_skills(session_id:str,request:Request):
    scope=await asyncio.to_thread(session_scope,session_id)
    if not scope:raise HTTPException(404,'session not found')
    ensure_owner_access(request,scope['owner_id'],detail='session owner mismatch')
    return {'skills':await asyncio.to_thread(repository.list_skills,scope)}


@router.get('/runs/{run_id}')
async def run_learning_info(run_id:str,request:Request,session_id:str=Query(...)):
    run=scoped_run(run_id,session_id,request)
    skills=await asyncio.to_thread(repository.candidates_for_run,run_id)
    # An old run may belong to a project from which its session was moved.
    scope=await asyncio.to_thread(session_scope,session_id)
    skills=[skill for skill in skills if scope and in_scope(skill,scope)]
    return {'run_id':run_id,'session_id':session_id,'run_status':run['status'],
            'job':repository.job(run_id),'skills':skills,'feedback':repository.feedback(run_id),
            'uses':repository.validation_runs_for_run(run_id)}


@router.post('/runs/{run_id}/capture')
async def capture_run(run_id:str,body:RunAction,request:Request):
    scoped_run(run_id,body.session_id,request)
    try:await asyncio.to_thread(get_skill_learning_service().capture,run_id,forced=True)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    return {'run_id':run_id,'job':repository.job(run_id)}


@router.post('/runs/{run_id}/feedback')
async def feedback(run_id:str,body:Feedback,request:Request):
    run=scoped_run(run_id,body.session_id,request)
    if run['status'] not in {'succeeded','failed','cancelled'}:raise HTTPException(409,'task has not finished')
    await asyncio.to_thread(get_skill_learning_service().feedback,run_id,body.rating,body.comment)
    return {'run_id':run_id,'feedback':repository.feedback(run_id)}


@router.get('/skills/{skill_id}')
async def skill_detail(skill_id:str,request:Request,session_id:str=Query(...)):
    skill=scoped_skill(skill_id,session_id,request)
    usages=repository.validation_runs(skill_id,skill['current_version'])
    for usage in usages:
        usage['feedback_rating']=(repository.feedback(usage['run_id']) or {}).get('rating')
    return {**skill,'usage':usages,'events':repository.event_history(skill_id)}


@router.get('/skills/{skill_id}/markdown')
async def skill_markdown(skill_id:str,request:Request,session_id:str=Query(...)):
    skill=scoped_skill(skill_id,session_id,request)
    return {'name':skill['draft']['name'],'version':skill['current_version'],
            'markdown':SkillDraft.model_validate(skill['draft']).markdown(skill['public_name'])}


@router.post('/skills/{skill_id}/review')
async def review_skill(skill_id:str,body:Review,request:Request):
    scoped_skill(skill_id,body.session_id,request)
    try:await asyncio.to_thread(get_skill_learning_service().review,skill_id,body.version,body.decision)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    return repository.get_skill(skill_id)


@router.put('/skills/{skill_id}')
async def edit_skill(skill_id:str,body:Edit,request:Request):
    scoped_skill(skill_id,body.session_id,request)
    try:await asyncio.to_thread(get_skill_learning_service().edit,skill_id,body.version,body.draft)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    return repository.get_skill(skill_id)
