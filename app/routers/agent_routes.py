#!/usr/bin/env python3
"""
Agent工作流程路由模块

专门处理Agent任务编排的完整工作流程：
意图识别 → 任务分解 → DAG生成 → 用户确认 → 执行调度
"""

import logging
import time
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.planning.planning import propose_plan_service
from ..scheduler import bfs_schedule
from ..repository.tasks import default_repo
from ..llm import get_default_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRequest(BaseModel):
    """Agent请求模型"""
    goal: str
    context: Optional[Dict[str, Any]] = None
    user_preferences: Optional[Dict[str, Any]] = None


class TaskNode(BaseModel):
    """任务节点模型"""
    id: int
    name: str
    task_type: str  # root, composite, atomic
    status: str
    parent_id: Optional[int] = None
    dependencies: List[int] = []
    depth: int
    estimated_time: Optional[str] = None


class AgentWorkflowResponse(BaseModel):
    """Agent工作流程响应"""
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
    创建完整的Agent工作流程
    
    流程：
    1. 意图分析和确认
    2. ROOT任务创建  
    3. 递归分解为COMPOSITE和ATOMIC任务
    4. 生成DAG结构
    5. 创建执行计划
    6. 返回用户确认界面数据
    """
    try:
        logger.info(f"🚀 开始创建Agent工作流程: {request.goal}")
        
        # 步骤1: 使用LLM进行意图分析和任务分解
        logger.info("📋 步骤1: LLM驱动的任务分解")
        plan_result = propose_plan_service({
            "goal": request.goal,
            "title": f"Agent工作流程: {request.goal[:50]}",
            "style": "hierarchical_decomposition", 
            "notes": "创建具有明确层次结构的任务分解，支持ROOT→COMPOSITE→ATOMIC的递归分解"
        })
        
        # 步骤2: 创建ROOT任务
        logger.info("🌳 步骤2: 创建ROOT任务")
        root_task_id = default_repo.create_task(
            name=f"ROOT: {plan_result['title']}",
            status="pending",
            priority=1,
            task_type="root"
        )
        
        # 步骤3: 创建简化的任务层次结构
        logger.info("🔄 步骤3: 创建任务层次")
        composite_tasks = []
        
        # 创建COMPOSITE任务（直接作为可执行任务）
        for i, task in enumerate(plan_result['tasks']):
            composite_task_id = default_repo.create_task(
                name=f"COMPOSITE: {task['name']}",
                status="pending", 
                priority=i + 1,
                parent_id=root_task_id,
                task_type="composite"
            )
            composite_tasks.append({
                "id": composite_task_id,
                "name": task['name'],
                "prompt": task['prompt'],
                "parent_id": root_task_id
            })
        
        # 步骤4: 简化依赖关系（顺序执行）
        logger.info("🔗 步骤4: 构建简化依赖关系")
        dependencies = {}
        for i, task in enumerate(composite_tasks):
            if i > 0:
                # 每个任务依赖前一个任务
                dependencies[task["id"]] = [composite_tasks[i-1]["id"]]
            else:
                dependencies[task["id"]] = []
        
        # 步骤5: 生成DAG结构（简化版）
        logger.info("📊 步骤5: 生成DAG结构")
        dag_structure = []
        
        # 添加ROOT任务
        dag_structure.append(TaskNode(
            id=root_task_id,
            name=f"ROOT: {plan_result['title']}",
            task_type="root",
            status="pending",
            parent_id=None,
            dependencies=[],
            depth=0
        ))
        
        # 添加COMPOSITE任务
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
        
        # 步骤6: 生成简化执行计划
        logger.info("📅 步骤6: 生成执行计划")
        execution_plan = []
        
        for i, task in enumerate(composite_tasks):
            execution_plan.append({
                "task_id": task["id"],
                "name": task["name"],
                "execution_order": i + 1,
                "prerequisites": dependencies.get(task["id"], []),
                "estimated_duration": "30-60分钟"
            })
        
        # 生成工作流程ID
        workflow_id = f"workflow_{root_task_id}_{int(time.time())}"
        
        return AgentWorkflowResponse(
            workflow_id=workflow_id,
            goal=request.goal,
            root_task_id=root_task_id,
            dag_structure=dag_structure,
            execution_plan=execution_plan,
            user_actions=[
                {"type": "approve_workflow", "label": "确认并开始执行"},
                {"type": "modify_tasks", "label": "修改任务结构"},
                {"type": "adjust_dependencies", "label": "调整依赖关系"},
                {"type": "cancel_workflow", "label": "取消工作流程"}
            ],
            metadata={
                "total_tasks": len(dag_structure),
                "composite_tasks": len([t for t in dag_structure if t.task_type == "composite"]),
                "estimated_completion": "2-4小时",
                "created_at": time.time()
            }
        )
        
    except Exception as e:
        logger.error(f"❌ Agent工作流程创建失败: {e}")
        raise HTTPException(status_code=500, detail=f"工作流程创建失败: {str(e)}")


# 简化版本 - 移除复杂的LLM调用链，避免级联失败


@router.get("/workflow/{workflow_id}")
async def get_workflow_status(workflow_id: str):
    """获取工作流程状态"""
    # TODO: 实现工作流程状态查询
    pass


@router.post("/workflow/{workflow_id}/approve")
async def approve_workflow(workflow_id: str):
    """用户确认并开始执行工作流程"""
    # TODO: 实现工作流程确认和启动
    pass


@router.post("/workflow/{workflow_id}/modify")
async def modify_workflow(workflow_id: str, modifications: Dict[str, Any]):
    """用户修改工作流程"""
    # TODO: 实现工作流程修改
    pass
