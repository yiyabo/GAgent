#!/usr/bin/env python3
"""
Agent workflow routes.

Implements workflow creation with task decomposition:
goal -> root task -> composite tasks -> dependency DAG -> execution plan.
"""

import logging
import time
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.planning.planning import propose_plan_service
from ..scheduler import bfs_schedule
from ..repository.tasks import default_repo
from ..llm import get_default_client
from ..utils.task_path_generator import get_task_file_path, ensure_task_directory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])


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
    """
    Create an agent workflow from a user goal.

    Steps:
    1. Generate a decomposition plan with the LLM.
    2. Create the ROOT task.
    3. Create COMPOSITE tasks as children.
    4. Build task dependencies.
    5. Build an execution plan.
    6. Return user actions for review/approval.
    """
    try:
        logger.info(f"🚀 Creating agent workflow: {request.goal}")

        logger.info("📋 Step 1/6: Generate decomposition plan with LLM")
        plan_result = propose_plan_service({
            "goal": request.goal,
            "title": f"Agent workflow: {request.goal[:50]}",
            "style": "hierarchical_decomposition",
            "notes": "Create task decomposition with ROOT -> COMPOSITE -> ATOMIC structure",
        })

        logger.info("🌳 Step 2/6: Create ROOT task")
        session_context = request.context or {}
        session_id = session_context.get("session_id")
        workflow_id = session_context.get("workflow_id")
        if not workflow_id:
            workflow_id = f"workflow_{int(time.time())}"

        root_task_name = plan_result['title']
        root_task_id = default_repo.create_task(
            name=root_task_name,
            status="pending",
            priority=1,
            task_type="root",
            session_id=session_id,
            workflow_id=workflow_id,
        )
        try:
            root_task_info = default_repo.get_task_info(root_task_id)
            root_dir = get_task_file_path(root_task_info, default_repo)  # results/<root_name>/
            if ensure_task_directory(root_dir):
                summary_path = os.path.join(root_dir, "summary.md")
                paper_path = os.path.join(root_dir, "paper.md")
                if not os.path.exists(summary_path):
                    with open(summary_path, "w", encoding="utf-8") as f:
                        f.write(f"# {root_task_name}\n\nThis file summarizes outputs from COMPOSITE tasks.\n")
                if not os.path.exists(paper_path):
                    with open(paper_path, "w", encoding="utf-8") as f:
                        f.write(
                            f"# {root_task_name}\n\n"
                            "This file aggregates final content synthesized from ATOMIC tasks.\n"
                        )
        except Exception as e:
            logger.warning(f"Failed to initialize ROOT task result folder: {e}")

        logger.info("🔄 Step 3/6: Create COMPOSITE tasks")
        composite_tasks = []

        for i, task in enumerate(plan_result['tasks']):
            composite_name = task['name']
            composite_task_id = default_repo.create_task(
                name=composite_name,
                status="pending", 
                priority=i + 1,
                parent_id=root_task_id,
                root_id=root_task_id,  # Keep root path linkage.
                task_type="composite",
                session_id=session_id,  # Keep session scope.
                workflow_id=workflow_id,
            )
            try:
                comp_info = default_repo.get_task_info(composite_task_id)
                comp_dir = get_task_file_path(comp_info, default_repo)  # results/<root>/<composite>/
                if ensure_task_directory(comp_dir):
                    comp_summary_path = os.path.join(comp_dir, "summary.md")
                    if not os.path.exists(comp_summary_path):
                        with open(comp_summary_path, "w", encoding="utf-8") as f:
                            f.write(
                                f"# {composite_name}\n\n"
                                "This summary should aggregate outputs from child ATOMIC tasks.\n"
                            )
            except Exception as e:
                logger.warning(f"Failed to initialize COMPOSITE folder for task {composite_task_id}: {e}")
            composite_tasks.append({
                "id": composite_task_id,
                "name": task['name'],
                "prompt": task['prompt'],
                "parent_id": root_task_id
            })

        logger.info("🔗 Step 4/6: Build task dependencies")
        dependencies = {}
        for i, task in enumerate(composite_tasks):
            if i > 0:
                dependencies[task["id"]] = [composite_tasks[i-1]["id"]]
            else:
                dependencies[task["id"]] = []

        logger.info("📊 Step 5/6: Build DAG structure")
        dag_structure = []

        dag_structure.append(TaskNode(
            id=root_task_id,
            name=root_task_name,
            task_type="root",
            status="pending",
            parent_id=None,
            dependencies=[],
            depth=0
        ))

        for task in composite_tasks:
            dag_structure.append(TaskNode(
                id=task["id"],
                name=task["name"],
                task_type="composite",
                status="pending",
                parent_id=root_task_id,
                dependencies=dependencies.get(task["id"], []),
                depth=1
            ))

        logger.info("📅 Step 6/6: Build execution plan")
        execution_plan = []

        for i, task in enumerate(composite_tasks):
            execution_plan.append({
                "task_id": task["id"],
                "name": task["name"],
                "execution_order": i + 1,
                "prerequisites": dependencies.get(task["id"], []),
                "estimated_duration": "30-60"
            })


        return AgentWorkflowResponse(
            workflow_id=workflow_id,
            goal=request.goal,
            root_task_id=root_task_id,
            dag_structure=dag_structure,
            execution_plan=execution_plan,
            user_actions=[
                {"type": "approve_workflow", "label": "Approve and execute"},
                {"type": "modify_tasks", "label": "Modify tasks"},
                {"type": "adjust_dependencies", "label": "Adjust dependencies"},
                {"type": "cancel_workflow", "label": "Cancel workflow"},
            ],
            metadata={
                "total_tasks": len(dag_structure),
                "composite_tasks": len([t for t in dag_structure if t.task_type == "composite"]),
                "estimated_completion": "2-4",
                "created_at": time.time()
            }
        )

    except Exception as e:
        logger.error(f"❌ Failed to create agent workflow: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create workflow: {str(e)}")




@router.get("/workflow/{workflow_id}")
async def get_workflow_status(workflow_id: str):
    """Get workflow status by workflow ID."""
    pass


@router.post("/workflow/{workflow_id}/approve")
async def approve_workflow(workflow_id: str):
    """Approve a workflow for execution."""
    pass


@router.post("/workflow/{workflow_id}/modify")
async def modify_workflow(workflow_id: str, modifications: Dict[str, Any]):
    """Modify an existing workflow."""
    pass
