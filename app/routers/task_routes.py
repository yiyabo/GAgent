"""
任务管理相关API端点

包含基础的任务CRUD操作和任务输出管理。
"""

from fastapi import APIRouter, HTTPException

from typing import List
from ..models import Task, TaskCreate, TaskUpdate
from ..repository.tasks import default_repo

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=Task)
def create_task(task: TaskCreate):
    """Create a new task with the provided details.

    Args:
        task: TaskCreate object containing task name and type

    Returns:
        dict: Dictionary containing the created task ID
    """
    task_id = default_repo.create_task(task.name, status="pending", priority=None, task_type=task.task_type)
    created_task = default_repo.get_task_info(task_id)
    if not created_task:
        raise HTTPException(status_code=500, detail="Failed to create or retrieve task")
    return created_task


@router.get("", response_model=List[Task])
def list_tasks():
    """List all tasks in the system.

    Returns:
        list: List of all tasks
    """
    return default_repo.list_all_tasks()


@router.get("/stats")
def get_task_stats():
    """获取任务统计信息"""
    try:
        all_tasks = default_repo.list_all_tasks()
        
        # 按状态分组统计
        by_status = {}
        for task in all_tasks:
            status = task.get('status', 'unknown')
            by_status[status] = by_status.get(status, 0) + 1
        
        # 按类型分组统计  
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
def get_task(task_id: int):
    """Get a single task by its ID."""
    task = default_repo.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
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
