"""Task APIs for CRUD operations and task output retrieval."""

from fastapi import APIRouter, HTTPException, Query, Body

from typing import List, Optional, Dict, Any
from ..models import Task, TaskCreate, TaskUpdate
from ..repository.tasks import default_repo
from ..utils.route_helpers import resolve_scope_params
from ..services.llm.llm_service import get_llm_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=Task)
def create_task(task: TaskCreate):
    """Create a new task with the provided details.

    Args:
        task: TaskCreate object containing task name and type

    Returns:
        dict: Dictionary containing the created task ID
    """
    task_id = default_repo.create_task(
        task.name,
        status="pending",
        priority=None,
        task_type=task.task_type,
        session_id=task.session_id,
        workflow_id=task.workflow_id,
        root_id=task.root_id,
    )
    created_task = default_repo.get_task_info(task_id)
    if not created_task:
        raise HTTPException(status_code=500, detail="Failed to create or retrieve task")
    return created_task


@router.post("/intelligent-create", response_model=Task)
async def intelligent_create_task(payload: Dict[str, Any] = Body(...)):
    """Intelligent task creation via LLM-based title extraction."""
    user_input = payload.get("user_input", "")
    session_id = payload.get("session_id")
    workflow_id = payload.get("workflow_id")

    if not user_input or not user_input.strip():
        raise HTTPException(status_code=400, detail="User input cannot be empty")

    try:
        # Use LLM to extract a concise ROOT task title.
        llm_service = get_llm_service()

        extraction_prompt = f"""Extract a concise and accurate ROOT task title from the user's natural-language request.

User input:
\"\"\"{user_input}\"\"\"

Requirements:
1. Capture the core objective and remove filler phrases (e.g., "help me", "I want to").
2. Keep the title concise (roughly 6-20 words).
3. Preserve critical domain terminology and technical entities.
4. Use a declarative phrase (not a question).
5. For research requests, preserve research object and method.
6. For engineering requests, preserve technology stack and product names.

Return only the extracted task title. No explanation or extra text."""

        llm_response = await llm_service.chat_async(extraction_prompt)
        task_name = llm_response.strip()

        # Fallback if extraction fails or becomes too long.
        if not task_name or len(task_name) > 100:
            task_name = user_input[:50].strip()

        # Create ROOT task.
        task_id = default_repo.create_task(
            name=task_name,
            status="pending",
            priority=2,
            task_type="root",  # Explicitly mark as ROOT task.
            session_id=session_id,
            workflow_id=workflow_id,
            root_id=None,
        )

        created_task = default_repo.get_task_info(task_id)
        if not created_task:
            raise HTTPException(status_code=500, detail="Failed to create or retrieve task")

        return created_task

    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Intelligent task creation failed: {str(e)}"
        )


@router.get("", response_model=List[Task])
def list_tasks(
    session_id: Optional[str] = Query(None, description="Session scope for task filtering"),
    workflow_id: Optional[str] = Query(None, description="Workflow scope for task filtering"),
):
    """List tasks within the resolved session/workflow scope."""
    resolved_session, resolved_workflow = resolve_scope_params(
        session_id, workflow_id, require_scope=True  # 🔒 session
    )
    return default_repo.list_all_tasks(session_id=resolved_session, workflow_id=resolved_workflow)


@router.get("/stats")
def get_task_stats(
    session_id: Optional[str] = Query(None, description="Session scope for task filtering"),
    workflow_id: Optional[str] = Query(None, description="Workflow scope for task filtering"),
):
    """Get task statistics within the resolved session/workflow scope."""
    try:
        resolved_session, resolved_workflow = resolve_scope_params(
            session_id, workflow_id, require_scope=True  # 🔒 session
        )
        all_tasks = default_repo.list_all_tasks(session_id=resolved_session, workflow_id=resolved_workflow)

        by_status = {}
        for task in all_tasks:
            status = task.get('status', 'unknown')
            by_status[status] = by_status.get(status, 0) + 1

        by_type = {}
        for task in all_tasks:
            task_type = task.get('task_type', 'unknown')
            by_type[task_type] = by_type.get(task_type, 0) + 1

        return {
            "total": len(all_tasks),
            "by_status": by_status,
            "by_type": by_type,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get task stats: {str(e)}")


@router.get("/{task_id}", response_model=Task)
def get_task(
    task_id: int,
    session_id: Optional[str] = Query(None, description="Task session scope"),
    workflow_id: Optional[str] = Query(None, description="Task workflow scope"),
):
    """Get a single task by its ID."""
    task = default_repo.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if session_id is not None or workflow_id is not None:
        resolved_session, resolved_workflow = resolve_scope_params(session_id, workflow_id)
        if resolved_session and task.get("session_id") and task["session_id"] != resolved_session:
            raise HTTPException(status_code=403, detail="Task does not belong to the requested session")
        if resolved_workflow and task.get("workflow_id") and task["workflow_id"] != resolved_workflow:
            raise HTTPException(status_code=403, detail="Task does not belong to the requested workflow")
    return task


@router.put("/{task_id}", response_model=Task)
def update_task(task_id: int, task_update: TaskUpdate):
    """Update a task's properties, such as its status."""
    if task_update.status:
        default_repo.update_task_status(task_id, task_update.status)

    updated_task = default_repo.get_task_info(task_id)
    if not updated_task:
        raise HTTPException(status_code=404, detail="Task not found after update")
    return updated_task


@router.get("/{task_id}/output")
def get_task_output(
    task_id: int,
    session_id: Optional[str] = Query(None, description="Task session scope"),
    workflow_id: Optional[str] = Query(None, description="Task workflow scope"),
):
    """Get the output content for a specific task.

    Args:
        task_id: The ID of the task to retrieve output for

    Returns:
        dict: Dictionary containing task ID and content

    Raises:
        HTTPException: If task output is not found (404)
    """
    task = default_repo.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if session_id is not None or workflow_id is not None:
        resolved_session, resolved_workflow = resolve_scope_params(session_id, workflow_id)
        if resolved_session and task.get("session_id") and task["session_id"] != resolved_session:
            raise HTTPException(status_code=403, detail="Task does not belong to the requested session")
        if resolved_workflow and task.get("workflow_id") and task["workflow_id"] != resolved_workflow:
            raise HTTPException(status_code=403, detail="Task does not belong to the requested workflow")

    content = default_repo.get_task_output_content(task_id)
    if content is None:
        raise HTTPException(status_code=404, detail="output not found")
    return {"id": task_id, "content": content}


@router.get("/{task_id}/children")
def get_task_children(
    task_id: int,
    session_id: Optional[str] = Query(None, description="Task session scope"),
    workflow_id: Optional[str] = Query(None, description="Task workflow scope"),
):
    """Get direct child tasks for the provided task ID."""
    parent = default_repo.get_task_info(task_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Task not found")
    if session_id is not None or workflow_id is not None:
        resolved_session, resolved_workflow = resolve_scope_params(session_id, workflow_id)
        if resolved_session and parent.get("session_id") and parent["session_id"] != resolved_session:
            raise HTTPException(status_code=403, detail="Task does not belong to the requested session")
        if resolved_workflow and parent.get("workflow_id") and parent["workflow_id"] != resolved_workflow:
            raise HTTPException(status_code=403, detail="Task does not belong to the requested workflow")
        children = [
            child
            for child in default_repo.get_children(task_id)
            if (not resolved_session or child.get("session_id") == resolved_session)
            and (not resolved_workflow or child.get("workflow_id") == resolved_workflow)
        ]
    else:
        children = default_repo.get_children(task_id)
    return {"task_id": task_id, "children": children}


@router.get("/{task_id}/subtree")
def get_task_subtree(
    task_id: int,
    session_id: Optional[str] = Query(None, description="Task session scope"),
    workflow_id: Optional[str] = Query(None, description="Task workflow scope"),
):
    """Get the full subtree rooted at a task."""
    root = default_repo.get_task_info(task_id)
    if not root:
        raise HTTPException(status_code=404, detail="Task not found")

    if session_id is not None or workflow_id is not None:
        resolved_session, resolved_workflow = resolve_scope_params(session_id, workflow_id)
        if resolved_session and root.get("session_id") and root["session_id"] != resolved_session:
            raise HTTPException(status_code=403, detail="Task does not belong to the requested session")
        if resolved_workflow and root.get("workflow_id") and root["workflow_id"] != resolved_workflow:
            raise HTTPException(status_code=403, detail="Task does not belong to the requested workflow")
        subtree = [
            node
            for node in default_repo.get_subtree(task_id)
            if (not resolved_session or node.get("session_id") == resolved_session)
            and (not resolved_workflow or node.get("workflow_id") == resolved_workflow)
        ]
    else:
        subtree = default_repo.get_subtree(task_id)
    if not subtree:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task_id, "subtree": subtree}
