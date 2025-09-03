"""
任务管理相关API端点

包含基础的任务CRUD操作和任务输出管理。
"""

from fastapi import APIRouter, HTTPException

from ..models import TaskCreate
from ..repository.tasks import default_repo

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("")
def create_task(task: TaskCreate):
    """Create a new task with the provided details.

    Args:
        task: TaskCreate object containing task name and type

    Returns:
        dict: Dictionary containing the created task ID
    """
    task_id = default_repo.create_task(task.name, status="pending", priority=None, task_type=task.task_type)
    return {"id": task_id}


@router.get("")
def list_tasks():
    """List all tasks in the system.

    Returns:
        list: List of all tasks
    """
    return default_repo.list_all_tasks()


@router.get("/{task_id}/output")
def get_task_output(task_id: int):
    """Get the output content for a specific task.

    Args:
        task_id: The ID of the task to retrieve output for

    Returns:
        dict: Dictionary containing task ID and content

    Raises:
        HTTPException: If task output is not found (404)
    """
    content = default_repo.get_task_output_content(task_id)
    if content is None:
        raise HTTPException(status_code=404, detail="output not found")
    return {"id": task_id, "content": content}


@router.get("/{task_id}/children")
def get_task_children(task_id: int):
    """获取指定任务的所有子任务"""
    children = default_repo.get_children(task_id)
    return {"task_id": task_id, "children": children}


@router.get("/{task_id}/subtree")
def get_task_subtree(task_id: int):
    """获取指定任务的完整子树结构"""
    subtree = default_repo.get_subtree(task_id)
    if not subtree:
        raise HTTPException(status_code=404, detail="task not found")
    return {"task_id": task_id, "subtree": subtree}
