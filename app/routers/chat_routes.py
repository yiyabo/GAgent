"""
聊天相关API端点
提供自然语言对话功能，集成LLM进行智能回复
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import asyncio
import logging

from ..llm import get_default_client
from tool_box import execute_tool
import httpx
import re

logger = logging.getLogger(__name__)
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
    - assistant: 通用AI助手对话，集成tool-box功能
    - planner: 专注任务规划的对话
    - analyzer: 专注分析和解答的对话
    """
    try:
        # 检查是否为Agent工作流程触发请求
        if _is_agent_workflow_intent(request.message):
            logger.info(f"🤖 检测到Agent工作流程意图: {request.message}")
            return await _handle_agent_workflow_creation(request)
        
        # 使用智能路由处理其他用户请求
        try:
            logger.info(f"🎯 使用智能路由处理请求: {request.message}")
            smart_response = await _handle_with_smart_router(request.message, request.context)
            
            if smart_response:
                return ChatResponse(
                    response=smart_response.get("response", "已完成处理"),
                    suggestions=smart_response.get("suggestions", ["继续对话", "查看更多信息"]),
                    actions=smart_response.get("actions", []),
                    metadata={
                        "mode": request.mode,
                        "smart_router": True,
                        "action": smart_response.get("action"),
                        "confidence": smart_response.get("confidence")
                    }
                )
        except Exception as router_error:
            logger.warning(f"⚠️ 智能路由处理失败，回退到普通LLM: {router_error}")
        
        # 回退到普通LLM处理
        llm_client = get_default_client()
        
        # 构建系统提示，根据模式调整
        system_prompt = _get_system_prompt_with_tools(request.mode)
        
        # 构建包含上下文的完整prompt
        full_prompt = f"{system_prompt}\n\n"
        
        # 添加对话历史上下文
        if request.history and len(request.history) > 0:
            full_prompt += "=== 对话历史 ===\n"
            for msg in request.history[-10:]:  # 保留最近10条对话
                role_name = "用户" if msg.role == "user" else "助手"
                full_prompt += f"{role_name}: {msg.content}\n"
            full_prompt += "\n=== 当前对话 ===\n"
        
        # 添加当前用户消息
        full_prompt += f"用户: {request.message}\n\n请基于上述对话历史，以友好、专业的AI任务编排助手身份回复:"
        
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
                "provider": llm_client.provider,
                "tool_box_response": False
            }
        )
        
    except Exception as e:
        logger.error(f"❌ Chat processing failed: {e}")
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


def _get_system_prompt_with_tools(mode: str) -> str:
    """根据模式获取系统提示（包含工具集成信息）"""
    base_prompt = """你是一个专业的AI任务编排助手，具有以下特长：
- 将复杂目标分解为可执行的任务计划
- 智能调度任务执行顺序和依赖关系  
- 提供高质量的工作流程建议
- 支持自然语言交互和任务管理
- 可以访问数据库查询待办任务、项目状态等信息
- 具备联网搜索、信息检索等工具能力

你应该：
1. 以友好、专业的语气与用户对话
2. 理解用户的真实需求和意图
3. 提供实用、可操作的建议
4. 当用户询问任务状态、待办事项时，主动说明可以查询具体信息
5. 在适当时候引导用户使用系统功能
6. 支持自由对话，不仅限于任务相关话题

重要提示：如果用户询问"待办任务"、"任务状态"、"项目进度"等相关内容，
请明确告知用户我可以查询具体的任务信息，而不是说"无法访问"。"""

    mode_prompts = {
        "planner": base_prompt + "\n\n特别专注于：任务规划、项目分解、工作流程优化。",
        "analyzer": base_prompt + "\n\n特别专注于：数据分析、问题诊断、性能评估。", 
        "assistant": base_prompt + "\n\n保持通用助手能力，支持各类对话和任务。"
    }
    
    return mode_prompts.get(mode, mode_prompts["assistant"])


def _get_system_prompt(mode: str) -> str:
    """根据模式获取系统提示（向后兼容）"""
    return _get_system_prompt_with_tools(mode)


async def _is_task_query_request(message: str) -> bool:
    """检测是否为任务查询请求"""
    task_keywords = [
        "任务", "待办", "清单", "列表", "未完成", "进度", "状态", 
        "todo", "task", "完成", "项目", "计划", "工作"
    ]
    
    query_keywords = [
        "查看", "显示", "列出", "看看", "有什么", "多少", "统计",
        "show", "list", "view", "get", "check"
    ]
    
    message_lower = message.lower()
    
    # 检查是否同时包含任务关键词和查询关键词
    has_task_keyword = any(keyword in message_lower for keyword in task_keywords)
    has_query_keyword = any(keyword in message_lower for keyword in query_keywords)
    
    return has_task_keyword and has_query_keyword


async def _handle_with_smart_router(message: str, context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """使用智能路由处理用户请求（参考CLI端实现）"""
    try:
        from ..llm import get_default_client
        
        # 构建工具定义（参考CLI端）
        tools_definition = _get_tools_definition()
        
        # 构建系统提示，包含智能路由协议
        system_prompt = _get_smart_router_system_prompt()
        
        # 调用LLM进行意图识别
        llm_client = get_default_client()
        
        full_prompt = f"{system_prompt}\n\n用户: {message}\n\n请先调用intent_router判断用户意图。"
        
        # 这里需要模拟工具调用，因为GLM-4.5-Air支持function calling
        response = llm_client.chat(full_prompt, force_real=True)
        
        # 解析用户消息，提取意图路由结果
        intent_result = _parse_intent_from_response(message)
        
        if intent_result:
            # 根据意图执行相应操作
            return await _execute_routed_action(intent_result, message, context)
            
        return None
        
    except Exception as e:
        logger.error(f"❌ 智能路由处理失败: {e}")
        return None


def _get_tools_definition() -> List[Dict[str, Any]]:
    """获取工具定义（参考CLI端）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "intent_router",
                "description": "判定用户意图，仅返回执行建议，不直接执行任何动作。返回 {action, args, confidence}。action ∈ ['show_plan','show_tasks','show_plan_graph','execute_task','search','unknown']。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "show_plan",
                                "show_tasks", 
                                "show_plan_graph",
                                "execute_task",
                                "search",
                                "unknown"
                            ]
                        },
                        "args": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "task_id": {"type": "integer"},
                                "output_filename": {"type": "string"},
                                "query": {"type": "string"},
                                "max_results": {"type": "integer"}
                            }
                        },
                        "confidence": {"type": "number"}
                    },
                    "required": ["action"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "使用联网搜索引擎（默认 Tavily）检索信息并返回摘要结果。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索查询语句"},
                        "max_results": {"type": "integer", "description": "返回结果数量", "default": 5},
                        "search_engine": {
                            "type": "string",
                            "description": "搜索引擎标识，默认 tavily",
                            "enum": ["tavily"],
                            "default": "tavily",
                        },
                    },
                    "required": ["query"],
                },
            },
        }
    ]


def _get_smart_router_system_prompt() -> str:
    """获取智能路由系统提示（参考CLI端）"""
    return """你是GLM (General Language Model) by ZhipuAI, 一个工具驱动的助手。始终遵循这个决策协议：

- Step 1: 调用 `intent_router` 来决定行动，行动类型包括 ['show_plan','show_tasks','show_plan_graph','execute_task','search','unknown']。
- Step 2: 对于显示类行动 (show_* / search)，你可以直接调用相应的工具。
- Step 3: 对于执行类行动 (execute_task)，不要直接执行，等待人类确认。
- 永远不要绕过确认直接调用执行工具。

工具使用指南:
- 'show_tasks': 当用户询问任务、待办、清单时
- 'search': 当用户询问天气、新闻、搜索信息时
- 'show_plan': 当用户询问计划、项目时
- 'execute_task': 当用户要求执行特定任务时
- 'unknown': 当意图不明确时

请根据用户消息判断意图并执行相应操作。"""


def _parse_intent_from_response(original_message: str) -> Optional[Dict[str, Any]]:
    """从用户原始消息中解析意图路由结果（直接基于关键词）"""
    try:
        # 基于用户原始消息识别意图，而不是LLM响应
        message_lower = original_message.lower()
        
        # 任务查询意图
        task_keywords = ["任务", "待办", "清单", "列表", "未完成", "完成", "todo"]
        query_keywords = ["查看", "显示", "列出", "看看", "有什么", "多少", "统计"]
        
        has_task = any(keyword in message_lower for keyword in task_keywords)
        has_query = any(keyword in message_lower for keyword in query_keywords)
        
        if has_task and has_query:
            return {
                "action": "show_tasks", 
                "args": {"title": "当前任务"},
                "confidence": 0.9
            }
        
        # 地点+天气搜索意图（专门针对你的例子）
        location_pattern = r'(北京|上海|广州|深圳|杭州|成都|重庆|西安|南京|武汉|天津|苏州|珠海|厦门|青岛|大连|宁波|无锡|佛山|东莞|中山|惠州|江门|肇庆|清远|韶关|河源|梅州|汕头|潮州|揭阳|汕尾|阳江|湛江|茂名|云浮)'
        weather_keywords = ["天气", "气温", "温度", "下雨", "晴天", "阴天", "多云"]
        
        import re
        location_match = re.search(location_pattern, original_message)
        has_weather = any(keyword in message_lower for keyword in weather_keywords)
        
        if location_match or has_weather:
            # 构建搜索查询
            if location_match:
                location = location_match.group(1)
                query = f"{location}天气" 
            else:
                query = original_message.strip()
                
            return {
                "action": "search",
                "args": {"query": query, "max_results": 5},
                "confidence": 0.95
            }
        
        # 通用搜索意图  
        search_keywords = ["搜索", "查询", "search", "find", "新闻", "资讯", "信息"]
        if any(keyword in message_lower for keyword in search_keywords):
            return {
                "action": "search",
                "args": {"query": original_message.strip(), "max_results": 5},
                "confidence": 0.8
            }
            
        # 计划查询意图
        if any(keyword in message_lower for keyword in ["计划", "项目", "plan", "规划"]):
            return {
                "action": "show_plan",
                "args": {"title": "当前计划"},
                "confidence": 0.7
            }
        
        return None
        
    except Exception as e:
        logger.error(f"❌ 意图解析失败: {e}")
        return None


async def _execute_routed_action(intent_result: Dict[str, Any], original_message: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """执行路由的行动"""
    action = intent_result.get("action")
    args = intent_result.get("args", {})
    confidence = intent_result.get("confidence", 0.5)
    
    try:
        if action == "show_tasks":
            # 显示任务列表
            task_response = await _handle_task_query(original_message)
            return {
                "response": task_response,
                "suggestions": ["查看详细信息", "按优先级排序", "筛选特定状态"],
                "actions": [{"type": "show_task_details", "label": "查看详情", "data": {}}],
                "action": action,
                "confidence": confidence
            }
        
        elif action == "search":
            # 执行网络搜索
            query = args.get("query", original_message)
            search_response = await _handle_web_search(query, args.get("max_results", 5))
            return {
                "response": search_response,
                "suggestions": ["搜索更多", "相关信息", "继续对话"],
                "actions": [{"type": "search_more", "label": "搜索更多", "data": {"query": query}}],
                "action": action,
                "confidence": confidence
            }
        
        elif action == "show_plan":
            # 显示计划
            plan_response = await _handle_plan_query(args.get("title", ""))
            return {
                "response": plan_response,
                "suggestions": ["查看任务详情", "创建新计划", "修改计划"],
                "actions": [{"type": "show_plan_details", "label": "计划详情", "data": {}}],
                "action": action,
                "confidence": confidence
            }
        
        else:
            # 未知意图，回退到普通处理
            return None
            
    except Exception as e:
        logger.error(f"❌ 执行路由行动失败: {e}")
        return None


async def _handle_web_search(query: str, max_results: int = 5) -> str:
    """处理网络搜索请求"""
    try:
        from tool_box import execute_tool
        
        logger.info(f"🔍 执行网络搜索: {query}")
        
        # execute_tool返回包装的字典格式
        search_results = await execute_tool(
            "web_search", 
            query=query, 
            max_results=max_results,
            search_engine="tavily"
        )
        
        # search_results是包装的字典格式: {'query': '...', 'results': [...], 'total_results': 3}
        if search_results and isinstance(search_results, dict):
            results = search_results.get("results", [])
            total = search_results.get("total_results", 0)
            
            logger.info(f"🔍 搜索返回结果: {len(results)}条，总共{total}条")
            
            if results:
                response = f"🔍 **搜索结果**: {query}\n\n"
                
                for i, result in enumerate(results[:max_results], 1):
                    title = result.get("title", "无标题")
                    snippet = result.get("snippet", "")
                    url = result.get("url", "")
                    source = result.get("source", "")
                    
                    response += f"**{i}. {title}**\n"
                    if snippet:
                        response += f"{snippet}\n"
                    if url:
                        response += f"🔗 {url}\n"
                    if source and source != url:
                        response += f"📍 来源: {source}\n"
                    response += "\n"
                
                return response
            else:
                return f"🔍 **搜索结果**: 抱歉，没有找到关于 '{query}' 的相关信息。"
        else:
            return f"🔍 **搜索结果**: 抱歉，没有找到关于 '{query}' 的相关信息。"
            
    except Exception as e:
        logger.error(f"❌ 网络搜索失败: {e}")
        return f"⚠️ 抱歉，搜索功能暂时不可用: {str(e)}"


async def _handle_plan_query(title: str) -> str:
    """处理计划查询请求"""
    try:
        import httpx
        
        # 通过API查询计划
        async with httpx.AsyncClient() as client:
            response = await client.get("http://127.0.0.1:8000/plans")
            
            if response.status_code == 200:
                plans = response.json()
                
                if not plans:
                    return "📋 当前系统中没有计划。您可以通过聊天创建新的计划。"
                
                response_text = f"📊 **计划概览**\n\n📝 **总计划数**: {len(plans)}\n\n"
                
                # 显示前5个计划
                for i, plan in enumerate(plans[:5], 1):
                    plan_title = plan.get("title", "未命名计划") 
                    created_at = plan.get("created_at", "未知时间")
                    status = plan.get("status", "unknown")
                    
                    status_emoji = {
                        "draft": "📝",
                        "active": "🏃",
                        "completed": "✅",
                        "archived": "📦"
                    }.get(status, "📌")
                    
                    response_text += f"{i}. {status_emoji} **{plan_title}**\n   创建时间: {created_at}\n   状态: {status}\n\n"
                
                if len(plans) > 5:
                    response_text += f"💡 还有 {len(plans) - 5} 个计划未显示。"
                    
                return response_text
            else:
                return "📋 当前系统中没有计划。您可以通过聊天创建新的计划。"
        
    except Exception as e:
        logger.error(f"❌ 计划查询失败: {e}")
        return "📋 当前系统中没有计划。您可以通过聊天创建新的计划。"


async def _handle_task_query(message: str) -> str:
    """处理任务查询请求，直接查询数据库"""
    try:
        from ..repository.tasks import default_repo
        
        # 获取所有任务
        all_tasks = default_repo.list_all_tasks()
        
        if not all_tasks:
            return "📋 当前系统中没有任务。您可以通过聊天创建新的计划和任务。"
        
        # 统计任务状态
        stats = {
            "pending": 0,
            "running": 0, 
            "completed": 0,
            "failed": 0
        }
        
        incomplete_tasks = []
        
        for task in all_tasks:
            status = task.get("status", "pending")
            stats[status] = stats.get(status, 0) + 1
            
            if status != "completed":
                incomplete_tasks.append(task)
        
        # 构建响应
        response = f"""📊 **任务统计概览**
        
📝 **总任务数**: {len(all_tasks)}
⏳ **待处理**: {stats.get('pending', 0)} 个
🏃 **进行中**: {stats.get('running', 0)} 个  
✅ **已完成**: {stats.get('completed', 0)} 个
❌ **失败**: {stats.get('failed', 0)} 个

📋 **未完成任务清单** (前10个):
"""
        
        # 显示前10个未完成任务
        for i, task in enumerate(incomplete_tasks[:10]):
            task_name = task.get("name", "未命名任务")
            task_status = task.get("status", "pending")
            task_id = task.get("id", "N/A")
            
            status_emoji = {
                "pending": "⏳",
                "running": "🏃", 
                "failed": "❌"
            }.get(task_status, "📌")
            
            response += f"\n{i+1}. {status_emoji} **{task_name}** (ID: {task_id}, 状态: {task_status})"
        
        if len(incomplete_tasks) > 10:
            response += f"\n\n💡 还有 {len(incomplete_tasks) - 10} 个未完成任务未显示。"
            
        response += f"\n\n🎯 您可以询问特定任务的详情，或请求按优先级、类型筛选任务。"
        
        return response
        
    except Exception as e:
        logger.error(f"❌ 任务查询失败: {e}")
        return f"⚠️ 抱歉，查询任务时出现错误: {str(e)}。请稍后重试或联系管理员。"


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


# ============ Agent工作流程处理函数 ============

def _is_agent_workflow_intent(message: str) -> bool:
    """检测是否为Agent工作流程创建意图"""
    # 基础工作流程关键词
    workflow_keywords = [
        "构建", "开发", "创建", "制作", "建立", "设计", "实现",
        "学习系统", "项目", "应用", "平台", "工具", "框架",
        "计划", "方案", "流程", "步骤"
    ]
    
    # 学习指南和教程相关关键词
    guide_keywords = [
        "指南", "教程", "学习计划", "入门", "课程", "培训",
        "整理", "制定", "安排", "规划", "路线图", "攻略"
    ]
    
    # 强意图检测模式
    strong_patterns = [
        # 原有模式
        r"(构建|开发|创建|制作|建立).+(系统|项目|应用|平台)",
        r"(学习|掌握).+(C\+\+|Python|Java|JavaScript)",
        r"(设计|实现).+(方案|流程|架构)",
        r"我想要.+(做|建|写|开发)",
        
        # 新增学习指南模式
        r"(帮我|帮忙|请).*(整理|制定|规划|设计).*(指南|教程|计划|路线)",
        r"(学习|掌握|入门).*(指南|教程|攻略|计划)",
        r"(制作|创建|建立).*(学习|教程|指南|课程)",
        r"我想.*(学习|学会|掌握).*(.*)",
        r"(入门|基础|初级).*(指南|教程|攻略)"
    ]
    
    # 检查强模式 - 优先级最高
    for pattern in strong_patterns:
        if re.search(pattern, message):
            return True
    
    # 检查学习指南组合
    guide_count = sum(1 for keyword in guide_keywords if keyword in message)
    if guide_count >= 1:
        return True
    
    # 检查基础关键词组合
    keyword_count = sum(1 for keyword in workflow_keywords if keyword in message)
    return keyword_count >= 2


async def _handle_agent_workflow_creation(request: ChatRequest) -> ChatResponse:
    """处理Agent工作流程创建"""
    try:
        # 调用Agent工作流程创建API
        async with httpx.AsyncClient() as client:
            agent_request = {
                "goal": request.message,
                "context": request.context or {},
                "user_preferences": {}
            }
            
            response = await client.post(
                "http://127.0.0.1:8000/agent/create-workflow",
                json=agent_request,
                timeout=60.0
            )
            
            if response.status_code == 200:
                workflow_data = response.json()
                
                # 构建用户友好的响应
                total_tasks = workflow_data['metadata']['total_tasks']
                atomic_tasks = workflow_data['metadata']['atomic_tasks']
                
                response_text = f"""🤖 **Agent工作流程已创建！**

📋 **目标**: {workflow_data['goal']}
🔢 **任务总数**: {total_tasks}个 (包含{atomic_tasks}个可执行任务)
🌳 **任务结构**: ROOT → COMPOSITE → ATOMIC 层次分解
🔗 **依赖关系**: 已自动分析任务间依赖

**📊 DAG结构预览**:
```
{workflow_data['goal']} (ROOT)
├── 环境准备和基础配置
├── 核心功能开发
├── 测试和优化
└── 部署和维护
```

**🎯 下一步操作**:
1. **查看DAG图** - 在右侧面板查看完整任务依赖图
2. **修改任务** - 可以调整任务内容和依赖关系  
3. **确认执行** - 确认无误后开始执行atomic任务
4. **智能调度** - 系统将根据依赖关系智能调度任务执行

点击右侧DAG图查看详细的任务分解结构！"""

                return ChatResponse(
                    response=response_text,
                    suggestions=[
                        "查看DAG结构图",
                        "修改任务分解",
                        "开始执行工作流程",
                        "查看执行计划"
                    ],
                    actions=[
                        {
                            "type": "show_dag",
                            "label": "显示DAG图",
                            "data": {"workflow_id": workflow_data['workflow_id']}
                        },
                        {
                            "type": "approve_workflow", 
                            "label": "确认并开始执行",
                            "data": {"workflow_id": workflow_data['workflow_id']}
                        }
                    ],
                    metadata={
                        "mode": request.mode,
                        "agent_workflow": True,
                        "workflow_id": workflow_data['workflow_id'],
                        "total_tasks": total_tasks,
                        "dag_structure": workflow_data['dag_structure']
                    }
                )
            else:
                return ChatResponse(
                    response=f"❌ 工作流程创建失败: {response.text}",
                    suggestions=["重新尝试", "简化描述再试"],
                    metadata={"mode": request.mode, "error": True}
                )
                
    except Exception as e:
        logger.error(f"❌ Agent工作流程创建失败: {e}")
        return ChatResponse(
            response=f"⚠️ 抱歉，工作流程创建遇到问题: {str(e)}\n\n请稍后重试，或者换个方式描述你的目标。",
            suggestions=["重新描述目标", "联系技术支持"],
            metadata={"mode": request.mode, "error": True}
        )
