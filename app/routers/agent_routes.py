#!/usr/bin/env python3
"""Legacy agent workflow routes.

This module predates the active PlanTree-based planning stack and used the old
``services.planning`` APIs. Those APIs have been retired. The route handlers
are kept only to return an explicit migration message instead of failing during
module import.
"""

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/agent", tags=["agent"])


_LEGACY_AGENT_ROUTE_DETAIL = (
    "Legacy /agent workflow routes have been retired. Use the PlanTree-backed "
    "planning flow instead: /tasks/{task_id}/decompose in plan_routes or the "
    "structured /chat JSON-action pipeline."
)


def _raise_legacy_agent_route_retired() -> None:
    raise HTTPException(status_code=410, detail=_LEGACY_AGENT_ROUTE_DETAIL)


class AgentRequest(BaseModel):
    """Request payload for creating an agent workflow."""
    goal: str
    context: Optional[Dict[str, Any]] = None
    user_preferences: Optional[Dict[str, Any]] = None


class TaskNode(BaseModel):
    """Task node returned in the generated workflow DAG."""
    id: int
    name: str
    task_type: str  # root, composite, atomic
    status: str
    parent_id: Optional[int] = None
    dependencies: List[int] = []
    depth: int
    estimated_time: Optional[str] = None


class AgentWorkflowResponse(BaseModel):
    """Response model for a generated agent workflow."""
    workflow_id: str
    goal: str
    root_task_id: int
    dag_structure: List[TaskNode]
    execution_plan: List[Dict[str, Any]]
    user_actions: List[Dict[str, Any]]
    metadata: Dict[str, Any]


@router.post("/create-workflow", response_model=AgentWorkflowResponse)
async def create_agent_workflow(request: AgentRequest):
    """Retired legacy workflow endpoint retained only for explicit guidance."""
    _raise_legacy_agent_route_retired()




@router.get("/workflow/{workflow_id}")
async def get_workflow_status(workflow_id: str):
    """Retired legacy workflow endpoint retained only for explicit guidance."""
    _raise_legacy_agent_route_retired()


@router.post("/workflow/{workflow_id}/approve")
async def approve_workflow(workflow_id: str):
    """Retired legacy workflow endpoint retained only for explicit guidance."""
    _raise_legacy_agent_route_retired()


@router.post("/workflow/{workflow_id}/modify")
async def modify_workflow(workflow_id: str, modifications: Dict[str, Any]):
    """Retired legacy workflow endpoint retained only for explicit guidance."""
    _raise_legacy_agent_route_retired()
