"""
任务管理相关API端点

包含基础的任务CRUD操作和任务输出管理。
"""

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
    """🧠 智能任务创建 - 使用LLM从用户输入中提炼任务名称
    
    科研项目要求：完全使用LLM理解用户意图，不使用正则表达式或关键词匹配
    
    Args:
        payload: 包含user_input, session_id, workflow_id的字典
        
    Returns:
        Task: 创建的任务对象
    """
    user_input = payload.get("user_input", "")
    session_id = payload.get("session_id")
    workflow_id = payload.get("workflow_id")
    
    if not user_input or not user_input.strip():
        raise HTTPException(status_code=400, detail="用户输入不能为空")
    
    try:
        # 🧠 使用LLM提炼ROOT任务名称
        llm_service = get_llm_service()
        
        extraction_prompt = f"""请从用户的自然语言输入中提炼出一个简洁、精准的ROOT任务名称。

用户输入：
\"\"\"{user_input}\"\"\"

提炼要求：
1. 提取核心目标，去除冗余词汇（如"帮我"、"我想"等）
2. 长度控制在10-30字
3. 保留关键的专业术语和领域词汇
4. 使用陈述性语句（不要疑问句）
5. 如果是科研任务，保留研究对象和方法
6. 如果是工程任务，保留技术栈和产品名称

只返回提炼后的任务名称，不要任何解释、标点或额外文字。"""

        llm_response = await llm_service.chat_async(extraction_prompt)
        task_name = llm_response.strip()
        
        # 如果LLM返回为空或过长，使用截断的原始输入
        if not task_name or len(task_name) > 100:
            task_name = user_input[:50].strip()
        
        # 创建ROOT任务
        task_id = default_repo.create_task(
            name=task_name,
            status="pending",
            priority=2,
            task_type="root",  # 明确标记为ROOT任务
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
            detail=f"智能任务创建失败: {str(e)}"
        )


@router.get("", response_model=List[Task])
def list_tasks(
    session_id: Optional[str] = Query(None, description="仅返回指定会话(session)下的任务"),
    workflow_id: Optional[str] = Query(None, description="仅返回指定工作流(workflow)下的任务"),
):
    """列出系统任务，可按会话或工作流进行过滤。🔒 实现专事专办，必须提供会话信息"""
    resolved_session, resolved_workflow = resolve_scope_params(
        session_id, workflow_id, require_scope=True  # 🔒 强制要求会话信息
    )
    return default_repo.list_all_tasks(session_id=resolved_session, workflow_id=resolved_workflow)


@router.get("/stats")
def get_task_stats(
    session_id: Optional[str] = Query(None, description="统计指定会话(session)下的任务"),
    workflow_id: Optional[str] = Query(None, description="统计指定工作流(workflow)下的任务"),
):
    """获取任务统计信息，支持按会话/工作流过滤。🔒 实现专事专办"""
    try:
        resolved_session, resolved_workflow = resolve_scope_params(
            session_id, workflow_id, require_scope=True  # 🔒 强制要求会话信息
        )
        all_tasks = default_repo.list_all_tasks(session_id=resolved_session, workflow_id=resolved_workflow)
        
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
def get_task(
    task_id: int,
    session_id: Optional[str] = Query(None, description="验证任务所属的会话"),
    workflow_id: Optional[str] = Query(None, description="验证任务所属的工作流"),
):
    """Get a single task by its ID."""
    task = default_repo.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if session_id is not None or workflow_id is not None:
        resolved_session, resolved_workflow = resolve_scope_params(session_id, workflow_id)
        if resolved_session and task.get("session_id") and task["session_id"] != resolved_session:
            raise HTTPException(status_code=403, detail="任务不属于指定的 session")
        if resolved_workflow and task.get("workflow_id") and task["workflow_id"] != resolved_workflow:
            raise HTTPException(status_code=403, detail="任务不属于指定的 workflow")
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
    session_id: Optional[str] = Query(None, description="验证任务所属的会话"),
    workflow_id: Optional[str] = Query(None, description="验证任务所属的工作流"),
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
        raise HTTPException(status_code=404, detail="task not found")
    if session_id is not None or workflow_id is not None:
        resolved_session, resolved_workflow = resolve_scope_params(session_id, workflow_id)
        if resolved_session and task.get("session_id") and task["session_id"] != resolved_session:
            raise HTTPException(status_code=403, detail="任务不属于指定的 session")
        if resolved_workflow and task.get("workflow_id") and task["workflow_id"] != resolved_workflow:
            raise HTTPException(status_code=403, detail="任务不属于指定的 workflow")

    content = default_repo.get_task_output_content(task_id)
    if content is None:
        raise HTTPException(status_code=404, detail="output not found")
    return {"id": task_id, "content": content}


@router.get("/{task_id}/children")
def get_task_children(
    task_id: int,
    session_id: Optional[str] = Query(None, description="验证任务所属的会话"),
    workflow_id: Optional[str] = Query(None, description="验证任务所属的工作流"),
):
    """获取指定任务的所有子任务"""
    parent = default_repo.get_task_info(task_id)
    if not parent:
        raise HTTPException(status_code=404, detail="task not found")
    if session_id is not None or workflow_id is not None:
        resolved_session, resolved_workflow = resolve_scope_params(session_id, workflow_id)
        if resolved_session and parent.get("session_id") and parent["session_id"] != resolved_session:
            raise HTTPException(status_code=403, detail="任务不属于指定的 session")
        if resolved_workflow and parent.get("workflow_id") and parent["workflow_id"] != resolved_workflow:
            raise HTTPException(status_code=403, detail="任务不属于指定的 workflow")
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
    session_id: Optional[str] = Query(None, description="验证任务所属的会话"),
    workflow_id: Optional[str] = Query(None, description="验证任务所属的工作流"),
):
    """获取指定任务的完整子树结构"""
    root = default_repo.get_task_info(task_id)
    if not root:
        raise HTTPException(status_code=404, detail="task not found")

    if session_id is not None or workflow_id is not None:
        resolved_session, resolved_workflow = resolve_scope_params(session_id, workflow_id)
        if resolved_session and root.get("session_id") and root["session_id"] != resolved_session:
            raise HTTPException(status_code=403, detail="任务不属于指定的 session")
        if resolved_workflow and root.get("workflow_id") and root["workflow_id"] != resolved_workflow:
            raise HTTPException(status_code=403, detail="任务不属于指定的 workflow")
        subtree = [
            node
            for node in default_repo.get_subtree(task_id)
            if (not resolved_session or node.get("session_id") == resolved_session)
            and (not resolved_workflow or node.get("workflow_id") == resolved_workflow)
        ]
    else:
        subtree = default_repo.get_subtree(task_id)
    if not subtree:
        raise HTTPException(status_code=404, detail="task not found")
    return {"task_id": task_id, "subtree": subtree}
