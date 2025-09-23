"""
聊天相关API端点
提供自然语言对话功能，集成LLM进行智能回复
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from ..llm import get_default_client

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[ChatMessage]] = None
    context: Optional[Dict[str, Any]] = None
    mode: Optional[str] = "assistant"  # "assistant" | "planner" | "analyzer"


class ChatResponse(BaseModel):
    response: str
    suggestions: Optional[List[str]] = None
    actions: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None


@router.post("/message", response_model=ChatResponse)
async def chat_message(request: ChatRequest):
    """
    处理聊天消息，提供智能回复
    
    支持不同模式:
    - assistant: 通用AI助手对话
    - planner: 专注任务规划的对话
    - analyzer: 专注分析和解答的对话
    """
    try:
        llm_client = get_default_client()
        
        # 构建系统提示，根据模式调整
        system_prompt = _get_system_prompt(request.mode)
        
        # 构建对话历史
        conversation = []
        if request.history:
            conversation.extend([
                {"role": msg.role, "content": msg.content} 
                for msg in request.history[-10:]  # 只保留最近10条
            ])
        
        # 添加系统提示和用户消息
        full_prompt = f"{system_prompt}\n\n用户: {request.message}\n\n请以友好、专业的AI任务编排助手身份回复:"
        
        # 调用LLM
        response = llm_client.chat(full_prompt, force_real=True)
        
        # 分析回复，提取建议和操作
        suggestions, actions = _extract_suggestions_and_actions(response, request.message)
        
        return ChatResponse(
            response=response,
            suggestions=suggestions,
            actions=actions,
            metadata={
                "mode": request.mode,
                "model": llm_client.model,
                "provider": llm_client.provider
            }
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat processing failed: {str(e)}")


@router.get("/suggestions")
async def get_chat_suggestions():
    """获取聊天建议"""
    return {
        "quick_actions": [
            "帮我创建一个学习计划",
            "查看当前任务状态", 
            "分析项目进度",
            "制定工作安排"
        ],
        "conversation_starters": [
            "你好，介绍一下你的功能",
            "我想了解任务编排系统",
            "如何提高工作效率？",
            "帮我分解复杂任务"
        ]
    }


def _get_system_prompt(mode: str) -> str:
    """根据模式获取系统提示"""
    base_prompt = """你是一个专业的AI任务编排助手，具有以下特长：
- 将复杂目标分解为可执行的任务计划
- 智能调度任务执行顺序和依赖关系  
- 提供高质量的工作流程建议
- 支持自然语言交互和任务管理

你应该：
1. 以友好、专业的语气与用户对话
2. 理解用户的真实需求和意图
3. 提供实用、可操作的建议
4. 在适当时候引导用户使用系统功能
5. 支持自由对话，不仅限于任务相关话题"""

    mode_prompts = {
        "planner": base_prompt + "\n\n特别专注于：任务规划、项目分解、工作流程优化。",
        "analyzer": base_prompt + "\n\n特别专注于：数据分析、问题诊断、性能评估。",
        "assistant": base_prompt + "\n\n保持通用助手能力，支持各类对话和任务。"
    }
    
    return mode_prompts.get(mode, mode_prompts["assistant"])


def _extract_suggestions_and_actions(response: str, user_message: str) -> tuple:
    """从回复中提取建议和可能的操作"""
    suggestions = []
    actions = []
    
    # 基于回复内容和用户消息分析可能的后续操作
    if any(keyword in user_message.lower() for keyword in ["计划", "规划", "安排"]):
        suggestions.extend([
            "创建详细计划",
            "查看现有任务",
            "设置提醒"
        ])
        actions.append({
            "type": "suggest_plan_creation",
            "label": "创建计划",
            "data": {"goal": user_message}
        })
    
    if any(keyword in user_message.lower() for keyword in ["状态", "进度", "完成"]):
        suggestions.extend([
            "查看任务统计",
            "生成进度报告",
            "分析效率"
        ])
        actions.append({
            "type": "show_status",
            "label": "查看状态", 
            "data": {}
        })
    
    return suggestions[:3], actions  # 最多返回3个建议


@router.get("/status")
async def get_chat_status():
    """获取聊天服务状态"""
    try:
        llm_client = get_default_client()
        return {
            "status": "online",
            "provider": llm_client.provider,
            "model": llm_client.model,
            "mock_mode": llm_client.mock,
            "features": {
                "free_chat": True,
                "task_planning": True,
                "context_awareness": True,
                "multi_mode": True
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }
