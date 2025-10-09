"""
聊天相关API端点
提供自然语言对话功能，集成LLM进行智能回复
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from collections import Counter, defaultdict
import asyncio
import logging

from ..llm import get_default_client
from ..utils import parse_json_obj
from tool_box import execute_tool, list_available_tools, initialize_toolbox
from app.services.llm.llm_service import get_llm_service
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
    session_id: Optional[str] = None  # Chat session ID for task isolation


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
        # 构建带上下文的消息历史
        context_messages = []
        if request.history:
            context_messages = [{"role": msg.role, "content": msg.content} for msg in request.history[-5:]]  # 保留最近5条
        context_messages.append({"role": "user", "content": request.message})
        # 快速预筛选：识别明显的非工具需求（问候语、感谢等）
        if _is_simple_greeting(request.message):
            logger.info("💬 识别为简单问候语，跳过复杂路由")
            return ChatResponse(
                response=_get_simple_greeting_response(request.message),
                suggestions=["告诉我你需要什么帮助", "我可以协助你完成任务"],
                actions=[],
                metadata={"routing_method": "simple_greeting", "skipped_tool_analysis": True}
            )

        # 🔒 检查是否为内部分析请求，如果是则跳过工作流程创建  
        is_internal_analysis = request.context and request.context.get('internal_analysis', False)
        if is_internal_analysis:
            logger.debug(f"🔒 内部分析请求，跳过工作流程创建: {request.context.get('original_user_input', 'unknown')}")
        else:
            # 检查是否为Agent工作流程触发请求 - 使用上下文感知判断
            workflow_decision = await _should_create_new_workflow(
                request.message, 
                request.session_id, 
                request.context,
                context_messages
            )
            
            # 🔍 DEBUG: 打印完整的意图判断结果
            logger.info(f"🧠 LLM意图判断结果: {workflow_decision}")
            logger.info(f"📝 用户消息: {request.message}")
            logger.info(f"🆔 Session ID: {request.session_id}")
            
            if workflow_decision.get("create_new_root"):
                logger.info(f"🤖 ====> 路由到: 创建新ROOT任务")
                return await _handle_agent_workflow_creation(request, context_messages)
            elif workflow_decision.get("add_to_existing"):
                logger.info(f"📎 ====> 路由到: 在现有ROOT任务下添加子任务")
                return await _handle_add_subtask_to_existing(request, workflow_decision, context_messages)
            elif workflow_decision.get("decompose_task"):
                logger.info(f"🔀 ====> 路由到: 拆分任务")
                return await _handle_task_decomposition(request, workflow_decision, context_messages)
            elif workflow_decision.get("execute_task"):
                logger.info(f"▶️ ====> 路由到: 执行任务")
                return await _handle_task_execution(request, workflow_decision, context_messages)
            else:
                logger.info(f"💬 ====> 路由到: 普通对话")
                logger.debug(f"✅ 普通对话，无需创建任务: '{request.message}'")

        # 智能路由处理已移至tool_box集成中
        # 这里直接使用普通LLM处理，工具调用在后续流程中通过_pure_llm_intelligent_routing完成
        
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
        # 统一的错误消息，不使用关键词匹配
        error_type = type(e).__name__
        error_msg = f"⚠️ 处理请求时遇到问题: {error_type}。请稍后重试或联系管理员。"
        
        return ChatResponse(
            response=error_msg,
            suggestions=["重新尝试", "简化问题", "检查网络连接"],
            actions=[],
            metadata={
                "mode": request.mode,
                "error": True,
                "error_type": error_type
            }
        )


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


async def _handle_with_smart_router(message: str, context: Optional[Dict[str, Any]] = None, session_id: Optional[str] = None, context_messages: Optional[List[Dict[str, str]]] = None) -> Optional[Dict[str, Any]]:
    """使用LLM驱动的智能工具路由"""
    try:
        from ..llm import get_default_client
        
        # 获取所有可用工具定义
        tools_definition = await _get_tools_definition()
        
        # 检测是否需要专业知识搜索
        professional_keywords = ["因果推断", "机器学习", "深度学习", "统计学", "数据科学", "算法", "编程", "框架"]
        need_search = any(keyword in message for keyword in professional_keywords)
        
        # 如果是专业话题且LLM可能不确定，先搜索相关信息
        if need_search:
            logger.info(f"🔍 检测到专业话题，先搜索相关信息: {message}")
            search_result = await execute_tool("web_search", query=message, max_results=3)
            
            # 将搜索结果添加到上下文
            if search_result and search_result.get("success"):
                search_content = search_result.get("response", "")
                if search_content and not search_content.startswith("❌"):
                    # 添加搜索信息到上下文消息
                    if not context_messages:
                        context_messages = []
                    context_messages.insert(-1, {
                        "role": "system", 
                        "content": f"参考信息：{search_content[:1000]}"  # 限制长度
                    })
        
        # 构建智能工具选择提示
        system_prompt = await _get_smart_tool_selection_prompt(tools_definition)
        
        # 调用LLM进行工具选择和参数推理
        llm_client = get_default_client()
        
        full_prompt = f"{system_prompt}\n\n用户请求: {message}\n\n请分析用户意图，选择最合适的工具并提供参数。"
        
        # 使用GLM的function calling能力
        try:
            # 让LLM直接基于工具定义做决策（移除不支持的tools参数）
            response = llm_client.chat(
                full_prompt, 
                force_real=True
            )
            
            # 解析LLM的工具选择结果
            tool_result = await _parse_llm_tool_selection(response, message, tools_definition)
            
            if tool_result:
                return tool_result
                
        except Exception as llm_error:
            logger.warning(f"⚠️ LLM工具选择失败，使用备用路由: {llm_error}")
            
        # 科研项目要求：使用纯LLM智能路由替代正则匹配
        fallback_result = await _pure_llm_intelligent_routing(message, tools_definition)
        if fallback_result:
            return fallback_result
        
        # 最后尝试直接语义解析
        direct_result = await _direct_semantic_analysis(message, session_id)
        if direct_result:
            return direct_result
            
        return None
        
    except Exception as e:
        logger.error(f"❌ 智能路由处理失败: {e}")
        return None


async def _get_tools_definition() -> List[Dict[str, Any]]:
    """获取工具定义（集成Tool Box所有工具）"""
    try:
        # 获取Tool Box中的所有工具（tool-box已在main.py中初始化）
        available_tools = await list_available_tools()
        
        tools_definition = [
            # 意图路由工具（系统内置）
            {
                "type": "function",
                "function": {
                    "name": "intent_router",
                    "description": "判定用户意图，仅返回执行建议，不直接执行任何动作。返回 {action, args, confidence}。action ∈ ['show_plan','show_tasks','show_plan_graph','execute_task','search','database_query','unknown']。",
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
                                    "database_query",
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
                                    "max_results": {"type": "integer"},
                                    "operation": {"type": "string"},
                                    "table_name": {"type": "string"}
                                }
                            },
                            "confidence": {"type": "number"}
                        },
                        "required": ["action"]
                    }
                }
            }
        ]
        
        # 添加Tool Box中的所有工具
        for tool in available_tools:
            tool_def = {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool.get("parameters_schema", {
                        "type": "object",
                        "properties": {},
                        "required": []
                    })
                }
            }
            tools_definition.append(tool_def)
        
        logger.info(f"✅ 加载了 {len(tools_definition)} 个工具定义 (包含Tool Box: {len(available_tools)}个)")
        return tools_definition
        
    except Exception as e:
        logger.error(f"❌ 获取工具定义失败: {e}")
        # 返回基础工具定义作为备选
        return [
            {
                "type": "function", 
                "function": {
                    "name": "intent_router",
                    "description": "判定用户意图",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["search", "database_query", "unknown"]},
                            "args": {"type": "object"}
                        },
                        "required": ["action"]
                    }
                }
            }
        ]


async def _get_smart_tool_selection_prompt(tools_definition: List[Dict[str, Any]]) -> str:
    """构建LLM工具选择提示"""
    
    # 构建工具列表描述
    tools_desc = []
    for tool_def in tools_definition:
        if tool_def.get("type") == "function":
            func_info = tool_def.get("function", {})
            name = func_info.get("name", "unknown")
            desc = func_info.get("description", "无描述")
            
            # 获取参数信息
            params = func_info.get("parameters", {}).get("properties", {})
            param_list = []
            for param_name, param_info in params.items():
                param_type = param_info.get("type", "any")
                param_desc = param_info.get("description", "")
                param_list.append(f"{param_name}({param_type}): {param_desc}")
            
            tool_entry = f"🔧 **{name}**: {desc}"
            if param_list:
                tool_entry += f"\n   参数: {', '.join(param_list[:3])}" # 只显示前3个参数
            
            tools_desc.append(tool_entry)
    
    return f"""你是一个智能工具路由助手。你的任务是分析用户请求，然后选择最合适的工具来处理。

📋 **可用工具列表**:
{chr(10).join(tools_desc)}

🎯 **智能工具选择规则**:
- 🔍 **数据库查询**: 用户询问"任务/待办/工作/项目进度/完成情况"等 → `database_query`
  示例: "查看任务"、"还有哪些工作没完成"、"项目进度如何"
- 🌐 **网络搜索**: 用户询问"天气/新闻/最新信息/知识问答"等 → `web_search`  
  示例: "北京天气"、"最新AI新闻"、"什么是量子计算"
- 📁 **文件操作**: 用户要求"读取/保存/管理文件"等 → `file_operations`
  示例: "保存报告"、"读取配置文件"
- 💬 **直接对话**: 用户打招呼、咨询能力、闲聊等 → 直接文本回复

🧠 **语义理解重点**:
- 重点理解用户的**真实意图**，而不是表面词汇
- "工作"、"事项"、"完成情况" = 任务查询
- "怎么样"、"如何"、"什么" + 外部信息 = 搜索

🤖 **响应策略**:
1. 优先调用最匹配的工具函数
2. 如果意图不明确，选择最可能的工具
3. 对于纯对话性质的请求，直接文本回复

请智能分析用户意图，选择最佳工具。"""


def _get_smart_router_system_prompt() -> str:
    """获取智能路由系统提示（参考CLI端）"""
    return """你是GLM (General Language Model) by ZhipuAI, 一个工具驱动的助手。始终遵循这个决策协议：

- Step 1: 调用 `intent_router` 来决定行动，行动类型包括 ['show_plan','show_tasks','show_plan_graph','execute_task','search','database_query','unknown']。
- Step 2: 对于显示类行动 (show_* / search / database_query)，你可以直接调用相应的工具。
- Step 3: 对于执行类行动 (execute_task)，不要直接执行，等待人类确认。
- 永远不要绕过确认直接调用执行工具。

重要工具选择指南:
🔍 'database_query': 当用户询问任务、待办、清单、项目进度时 - 查询本地数据库
🌐 'search': 当用户询问天气、新闻、最新信息时 - 联网搜索
📋 'show_tasks': 显示任务列表
📊 'show_plan': 显示计划详情
⚡ 'execute_task': 执行特定任务（需确认）
❓ 'unknown': 当意图不明确时

请根据用户消息判断意图并执行相应操作。"""


def _normalize_generation_output(
    raw_text: str,
    default_suggestions: List[str],
    default_actions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Parse LLM输出，确保返回结构完整。"""
    parsed = parse_json_obj(raw_text) if raw_text else None

    if isinstance(parsed, dict):
        plan_text = str(parsed.get("plan") or parsed.get("content") or raw_text).strip()

        suggestions_raw = parsed.get("suggestions")
        if isinstance(suggestions_raw, list):
            suggestions = [str(item).strip() for item in suggestions_raw if str(item).strip()]
            if not suggestions:
                suggestions = default_suggestions
        else:
            suggestions = default_suggestions

        actions_raw = parsed.get("actions")
        if isinstance(actions_raw, list):
            actions = [item for item in actions_raw if isinstance(item, dict)]
            if not actions:
                actions = default_actions
        else:
            actions = default_actions
    else:
        plan_text = raw_text.strip()
        suggestions = default_suggestions
        actions = default_actions

    return {
        "plan": plan_text,
        "suggestions": suggestions,
        "actions": actions,
    }


async def _generate_learning_plan_with_llm(
    topic: str,
    user_message: str,
    search_info: str,
    plan_type: str,
) -> Dict[str, Any]:
    """调用LLM生成学习计划。"""
    llm_service = get_llm_service()

    detail_hint = "详细、分阶段的学习计划" if plan_type == "detailed" else "概览型学习计划"
    reference_section = search_info.strip() if search_info else "（无外部参考资料，按最佳实践给出建议）"

    prompt = (
        "你是一名专业的学习规划顾问，需要为用户制定可执行的学习方案。请用中文回答。\n"
        f"学习主题：{topic}\n"
        f"用户原始需求：{user_message}\n"
        f"计划颗粒度：{detail_hint}\n"
        "--- 参考资料开始 ---\n"
        f"{reference_section}\n"
        "--- 参考资料结束 ---\n\n"
        "请基于以上信息输出一个 JSON，包含以下字段：\n"
        "{\n"
        '  "plan": "使用 Markdown 写出的完整学习计划，至少包含阶段、目标和行动项",\n'
        '  "suggestions": ["下一步建议1", "下一步建议2", ...],\n'
        '  "actions": [{"type": "create_study_schedule", "label": "制定学习时间表", "data": {"topic": "<主题>"}}, ...]\n'
        "}\n"
        "如果参考资料不足，请结合通用最佳实践给出合理安排。严禁编造不存在的资源。"
    )

    raw_text = await llm_service.chat_async(prompt, force_real=True)

    default_actions = [
        {"type": "create_study_schedule", "label": "制定学习时间表", "data": {"topic": topic}},
    ]
    default_suggestions = [f"制定{topic}的学习时间表", "开始第一阶段学习", "根据反馈优化计划"]

    return _normalize_generation_output(raw_text, default_suggestions, default_actions)


async def _generate_task_breakdown_with_llm(
    target: str,
    user_message: str,
    search_info: str,
) -> Dict[str, Any]:
    """调用LLM生成任务拆分建议。"""
    llm_service = get_llm_service()

    reference_section = search_info.strip() if search_info else "（无外部参考资料，结合经验拆分）"

    prompt = (
        "你是一名任务拆解专家，需要帮助用户将目标转化为可执行任务。请用中文回答。\n"
        f"拆分目标：{target}\n"
        f"用户原始需求：{user_message}\n"
        "--- 参考资料开始 ---\n"
        f"{reference_section}\n"
        "--- 参考资料结束 ---\n\n"
        "请输出一个 JSON，包含以下字段：\n"
        "{\n"
        '  "plan": "使用 Markdown 表达的任务拆分建议，按阶段或步骤列出任务",\n'
        '  "suggestions": ["后续建议1", "后续建议2"],\n'
        '  "actions": [{"type": "create_tasks", "label": "创建任务", "data": {"target": "<目标>"}}, ...]\n'
        "}\n"
        "任务要具体、可执行，并给出必要的资源或产出要求。"
    )

    raw_text = await llm_service.chat_async(prompt, force_real=True)

    default_actions = [
        {"type": "create_tasks", "label": "创建任务", "data": {"target": target}},
    ]
    default_suggestions = ["继续细化任务", "制定时间表", "收集所需资源"]

    return _normalize_generation_output(raw_text, default_suggestions, default_actions)


async def _parse_llm_tool_selection(llm_response: str, original_message: str, tools_definition: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """解析LLM的工具选择结果"""
    try:
        # 检查LLM是否进行了function calling
        # 这里需要根据实际的LLM响应格式来解析
        
        # 🧠 完全基于LLM的智能路由分析 - 科研项目要求：零关键词匹配
        return await _pure_llm_intelligent_routing(original_message, tools_definition)
        
    except Exception as e:
        logger.error(f"❌ LLM工具选择解析失败: {e}")
        # 科研项目要求：即使出错也使用智能路由，不降级到正则匹配
        return await _pure_llm_intelligent_routing(original_message, tools_definition)


async def _pure_llm_intelligent_routing(user_message: str, tools_definition: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """完全基于LLM的智能路由分析 - 科研项目专用，零妥协"""
    try:
        from tool_box import route_user_request
        
        logger.info("🧠 启用纯LLM智能路由分析")
        
        # 使用Tool-box的SmartRouter进行完全智能分析
        routing_result = await route_user_request(user_message)
        
        if not routing_result or routing_result.get("confidence", 0.0) < 0.1:
            logger.warning("⚠️ LLM路由置信度过低，但仍采用智能路由结果")
            # 科研项目要求：即使置信度低也不降级，而是增强LLM分析
            routing_result = await _enhanced_llm_routing(user_message, tools_definition)
        
        # 执行智能路由选择的工具
        if routing_result and routing_result.get("tool_calls"):
            return await _execute_intelligent_routing(routing_result, user_message)
        
        return None
        
    except Exception as e:
        logger.error(f"❌ 纯LLM智能路由失败: {e}")
        # 最后兜底：仍然尝试增强LLM分析
        try:
            return await _enhanced_llm_routing(user_message, tools_definition)
        except:
            return None


async def _enhanced_llm_routing(user_message: str, tools_definition: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """增强的LLM路由分析 - 当基础路由失败时使用"""
    try:
        from tool_box.router import get_smart_router
        
        logger.info("🔬 启用增强LLM路由分析")
        
        # 获取智能路由器实例
        router = await get_smart_router()
        
        # 构建更详细的上下文
        enhanced_context = {
            "available_tools": tools_definition,
            "request_type": "scientific_research_routing",
            "precision_required": True,
            "user_intent_analysis": True
        }
        
        # 执行增强路由分析
        result = await router.route_request(user_message, context=enhanced_context)
        
        return result
        
    except Exception as e:
        logger.error(f"❌ 增强LLM路由分析失败: {e}")
        return None


async def _execute_intelligent_routing(routing_result: Dict[str, Any], original_message: str) -> Optional[Dict[str, Any]]:
    """执行智能路由结果"""
    try:
        tool_calls = routing_result.get("tool_calls", [])
        
        if not tool_calls:
            logger.warning("智能路由未返回工具调用")
            return None
        
        # 执行第一个推荐的工具
        first_tool = tool_calls[0]
        tool_name = first_tool.get("tool_name")
        parameters = first_tool.get("parameters", {})
        
        logger.info(f"🛠️ 执行智能路由选择的工具: {tool_name}")
        
        if tool_name == "database_query":
            return await _handle_database_tool_call(parameters, original_message)
        elif tool_name == "web_search":
            return await _handle_search_tool_call(parameters, original_message)
        elif tool_name == "file_operations":
            return await _handle_file_tool_call(parameters, original_message)
        elif tool_name == "internal_api":
            return await _handle_internal_api_tool_call(parameters, original_message)
        else:
            logger.warning(f"未知工具类型: {tool_name}")
            return None
            
    except Exception as e:
        logger.error(f"❌ 智能路由执行失败: {e}")
        return None


async def _handle_database_tool_call(parameters: Dict[str, Any], original_message: str) -> Dict[str, Any]:
    """处理数据库工具调用"""
    try:
        result = await execute_tool("database_query", **parameters)
        return await _format_database_result(result, "智能路由数据库查询")
    except Exception as e:
        logger.error(f"数据库工具调用失败: {e}")
        return None


async def _handle_search_tool_call(parameters: Dict[str, Any], original_message: str) -> Dict[str, Any]:
    """处理搜索工具调用"""
    try:
        result = await execute_tool("web_search", **parameters)
        return await _format_search_result(result, original_message)
    except Exception as e:
        logger.error(f"搜索工具调用失败: {e}")
        return None


async def _handle_file_tool_call(parameters: Dict[str, Any], original_message: str) -> Dict[str, Any]:
    """处理文件工具调用"""
    try:
        result = await execute_tool("file_operations", **parameters)
        return {"response": f"文件操作完成: {result}", "suggestions": ["查看结果", "继续操作"]}
    except Exception as e:
        logger.error(f"文件工具调用失败: {e}")
        return None


async def _handle_internal_api_tool_call(parameters: Dict[str, Any], original_message: str) -> Dict[str, Any]:
    """处理内部API工具调用"""
    try:
        result = await execute_tool("internal_api", **parameters)
        return {"response": f"内部API调用完成: {result}", "suggestions": ["查看结果", "继续操作"]}
    except Exception as e:
        logger.error(f"内部API工具调用失败: {e}")
        return None


async def _format_database_result(result: Dict[str, Any], description: str) -> Dict[str, Any]:
    """格式化数据库查询结果"""
    try:
        logger.info(f"🔍 格式化数据库结果: {result}")
        
        if isinstance(result, dict) and result.get("success"):
            # 统一处理数据库执行操作结果，不使用关键词匹配
            if result.get("operation") == "execute":
                rows_affected = result.get("rows_affected", 0)
                if rows_affected > 0:
                    response = f"✅ **{description}成功**：\n\n影响了 {rows_affected} 条记录"
                else:
                    response = f"📭 **{description}完成**：\n\n没有记录受到影响"
            else:
                # 查询操作 - Tool Box返回的数据在'rows'字段
                data = result.get("rows", [])
                if data:
                    response = f"📊 {description}结果：\n\n"
                    if isinstance(data, list) and len(data) > 0:
                        response += f"找到 {len(data)} 条记录：\n"
                        for i, item in enumerate(data[:10], 1):
                            if isinstance(item, dict):
                                name = item.get("name", f"记录{i}")
                                status = item.get("status", "未知")
                                # 清理任务名称，移除前缀
                                if name.startswith(('ROOT:', 'COMPOSITE:', 'ATOMIC:')):
                                    name = name.split(':', 1)[1].strip()
                                response += f"{i}. {name} ({status})\n"
                    else:
                        response += str(data)
                else:
                    response = "📭 暂无相关数据"
        else:
            response = f"❌ 数据库查询失败: {result}"
        
        return {
            "response": response,
            "suggestions": ["查看详细信息", "刷新数据", "修改筛选条件"],
            "actions": [{"type": "refresh_data", "label": "刷新数据", "data": {}}],
            "action": "database_query",
            "confidence": 0.95
        }
    except Exception as e:
        return {
            "response": f"❌ 结果格式化失败: {str(e)}",
            "suggestions": ["重试查询"],
            "actions": [],
            "action": "database_query",
            "confidence": 0.5
        }


async def _format_search_result(result: Dict[str, Any], query: str) -> Dict[str, Any]:
    """格式化搜索结果"""
    try:
        if isinstance(result, dict) and result.get("success"):
            search_engine = result.get("search_engine", "unknown")
            
            if search_engine == "perplexity":
                # Perplexity返回智能回答
                search_response = f"🧠 **智能搜索回答**：\n\n{result.get('response', '无搜索结果')}"
            elif search_engine == "tavily_fallback":
                # Perplexity fallback to Tavily
                if "results" in result:
                    results = result["results"]
                    if results:
                        search_response = f"🔍 **搜索结果** (Perplexity不可用，使用备用搜索，{len(results)}条)：\n\n"
                        for i, item in enumerate(results[:5], 1):
                            title = item.get("title", "无标题")
                            snippet = item.get("snippet", "无内容摘要")
                            source = item.get("source", "")
                            search_response += f"**{i}. {title}**\n{snippet}\n来源: {source}\n\n"
                    else:
                        search_response = "📭 未找到相关搜索结果"
                else:
                    search_response = "❌ 备用搜索也失败了"
            else:
                # Tavily等返回搜索结果列表
                if "results" in result:
                    results = result["results"]
                    if results:
                        search_response = f"🔍 **搜索结果** ({len(results)}条)：\n\n"
                        for i, item in enumerate(results[:5], 1):
                            title = item.get("title", "无标题")
                            snippet = item.get("snippet", "无内容摘要")
                            source = item.get("source", "")
                            search_response += f"**{i}. {title}**\n{snippet}\n来源: {source}\n\n"
                    else:
                        search_response = "📭 未找到相关搜索结果"
                else:
                    search_response = result.get("formatted_response", str(result))
        else:
            error_msg = result.get("error", "未知错误")
            search_response = f"❌ 搜索失败：{error_msg}"
        
        return {
            "response": search_response,
            "suggestions": ["搜索更多", "相关信息", "继续对话"],
            "actions": [{"type": "search_more", "label": "搜索更多", "data": {"query": query}}],
            "action": "search",
            "confidence": 0.9
        }
    except Exception as e:
        return {
            "response": f"❌ 搜索结果格式化失败: {str(e)}",
            "suggestions": ["重试搜索"],
            "actions": [],
            "action": "search",
            "confidence": 0.5
        }


async def _direct_semantic_analysis(message: str, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """直接语义分析 - 最后的备用方案"""
    try:
        message_lower = message.lower()
        
        # 任务查询的多种表达方式
        task_patterns = [
            # 直接询问
            any(word in message_lower for word in ["任务", "待办", "todo", "清单"]),
            # 工作相关
            ("工作" in message_lower and any(word in message_lower for word in ["完成", "没完成", "未完成", "剩余", "还有", "哪些"])),
            # 项目相关  
            ("项目" in message_lower and any(word in message_lower for word in ["进度", "状态", "完成"])),
            # 事项相关
            ("事项" in message_lower and any(word in message_lower for word in ["还有", "剩余", "未完成"])),
        ]
        
        # 删除动作词
        delete_actions = any(word in message_lower for word in ["删除", "清除", "清空", "移除", "删掉", "去掉", "清理"])
        
        # 创建动作词
        create_actions = any(word in message_lower for word in ["新建", "创建", "添加", "建立", "制定", "做个", "建个"])
        
        # 查询动作词
        query_actions = any(word in message_lower for word in ["看", "查", "显示", "列出", "告诉", "帮我"])
        
        # 检查删除操作
        if any(task_patterns) and delete_actions:
            logger.info(f"🗑️ 直接语义分析识别为任务删除: {message}")
            
            # 调用数据库删除工具 - 添加session_id支持
            session_filter = ""
            if session_id:
                session_filter = f" AND session_id = '{session_id}'"
            else:
                # 如果没有session_id，只删除没有session_id的任务（向后兼容）
                session_filter = " AND session_id IS NULL"
                
            sql = f"DELETE FROM tasks WHERE status = 'pending'{session_filter}"
            result = await execute_tool("database_query", 
                                      database="data/databases/main/tasks.db",
                                      sql=sql,
                                      operation="execute")
            
            return await _format_database_result(result, "任务删除")
        
        # 检查创建操作
        if any(task_patterns) and create_actions:
            logger.info(f"➕ 直接语义分析识别为任务创建: {message}")
            
            # 提取任务名称 - 简单的文本处理
            task_name = message
            # 清理动作词，保留任务描述
            for action_word in ["新建", "创建", "添加", "建立", "制定", "做个", "建个"]:
                task_name = task_name.replace(action_word, "")
            for task_word in ["任务", "待办", "清单", "事项"]:
                task_name = task_name.replace(task_word, "")
            
            # 清理标点和多余空格
            import re
            task_name = re.sub(r'[，。！？,!?]', '', task_name).strip()
            task_name = task_name.replace("，", "").replace("：", "").replace(":", "").strip()
            
            if not task_name:
                task_name = "新任务"
            
            # 调用数据库插入工具
            session_value = f"'{session_id}'" if session_id else "NULL"
            sql = f"""INSERT INTO tasks (name, status, priority, session_id, task_type) 
                     VALUES ('{task_name}', 'pending', 1, {session_value}, 'atomic')"""
            
            result = await execute_tool("database_query", 
                                      database="data/databases/main/tasks.db",
                                      sql=sql,
                                      operation="execute")
            
            # 格式化创建结果
            if isinstance(result, dict) and result.get("success"):
                rows_affected = result.get("rows_affected", 0)
                if rows_affected > 0:
                    response = f"✅ **任务创建成功**：\n\n已添加任务：「{task_name}」"
                else:
                    response = f"❌ **任务创建失败**：\n\n无法添加任务"
            else:
                response = f"❌ **任务创建失败**：\n\n{result.get('error', '未知错误')}"
                
            return {
                "response": response,
                "suggestions": ["查看任务", "继续添加", "开始工作"],
                "actions": [{"type": "view_tasks", "label": "查看任务", "data": {}}],
                "action": "task_create",
                "confidence": 0.9
            }
        
        if any(task_patterns) and query_actions:
            logger.info(f"🎯 直接语义分析识别为任务查询: {message}")
            
            # 强制会话隔离 - 数据库查询工具
            if not session_id:
                return {
                    "response": "🔒 请先在当前对话中创建一个任务或计划，然后我就能显示当前工作空间的任务了。",
                    "suggestions": ["创建新计划", "开始新对话"],
                    "actions": [],
                    "action": "database_query",
                    "confidence": 1.0
                }
                
            sql = f"SELECT * FROM tasks WHERE status = 'pending' AND session_id = '{session_id}' ORDER BY priority ASC, id DESC LIMIT 10"
            result = await execute_tool("database_query", 
                                      database="data/databases/main/tasks.db",
                                      sql=sql,
                                      operation="query")
            
            return await _format_database_result(result, f"当前工作空间待办任务 (会话: {session_id})")
        
        # 搜索查询检测
        search_patterns = [
            any(word in message_lower for word in ["天气", "新闻", "最新"]),
            ("什么是" in message_lower or "如何" in message_lower or "怎么" in message_lower),
            any(word in message_lower for word in ["搜索", "查找", "search"]),
        ]
        
        if any(search_patterns):
            logger.info(f"🎯 直接语义分析识别为搜索: {message}")
            
            result = await execute_tool("web_search", 
                                      query=message,
                                      max_results=5)
            
            return await _format_search_result(result, message)
        
        return None
        
    except Exception as e:
        logger.error(f"❌ 直接语义分析失败: {e}")
        return None


async def _direct_semantic_analysis(original_message: str, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """直接语义分析 - 完全基于LLM的最终兜底方案"""
    try:
        logger.info("🔬 启用直接语义分析兜底方案")
        
        from tool_box.router import get_smart_router
        
        # 获取智能路由器并进行最后的分析尝试
        router = await get_smart_router()
        
        # 使用更宽松的置信度阈值，但仍然是LLM分析
        result = await router._enhanced_llm_routing(original_message, context={
            "fallback_mode": True,
            "min_confidence": 0.05,  # 极低阈值，但仍是LLM分析
            "session_id": session_id
        })
        
        return result
        
    except Exception as e:
        logger.error(f"❌ 直接语义分析失败: {e}")
        # 科研项目要求：即使最终兜底也不使用正则匹配
        return {
            "response": "抱歉，我暂时无法理解您的请求。请尝试重新表达或提供更多详细信息。",
            "suggestions": ["重新表达请求", "提供更多上下文", "换个方式描述"],
            "metadata": {"fallback_used": True, "routing_failed": True}
        }


async def _execute_routed_action(intent_result: Dict[str, Any], original_message: str, context: Optional[Dict[str, Any]] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
    """执行路由的行动"""
    action = intent_result.get("action")
    args = intent_result.get("args", {})
    confidence = intent_result.get("confidence", 0.5)
    
    try:
        if action == "show_tasks":
            # 显示任务列表 - 传递会话信息以支持专事专办
            workflow_id = context.get("workflow_id") if context else None
            task_response = await _handle_task_query(
                original_message,
                session_id=session_id,
                workflow_id=workflow_id
            )
            return {
                "response": task_response,
                "suggestions": ["查看详细信息", "按优先级排序", "筛选特定状态"],
                "actions": [{"type": "show_task_details", "label": "查看详情", "data": {}}],
                "action": action,
                "confidence": confidence
            }
        
        elif action == "create_learning_plan":
            # 学习计划生成
            topic = args.get("topic", "学习计划")
            plan_type = args.get("type", "detailed")
            
            search_info = ""
            search_query = f"{topic} 学习计划 教材 课程 步骤"
            search_result = await execute_tool("web_search", query=search_query, max_results=3)

            if isinstance(search_result, dict) and search_result.get("success"):
                search_content = search_result.get("response", "") or search_result.get("formatted_response", "")
                if search_content and not str(search_content).startswith("❌"):
                    search_info = str(search_content)[:1200]

            generation = await _generate_learning_plan_with_llm(topic, original_message, search_info, plan_type)

            return {
                "response": generation["plan"],
                "suggestions": generation["suggestions"],
                "actions": generation["actions"],
                "action": action,
                "confidence": confidence
            }

        elif action == "task_breakdown":
            # 任务拆分处理
            target = args.get("target", "任务")
            
            # 先搜索相关信息
            search_query = f"{target} 拆分 步骤 行动 建议"
            search_result = await execute_tool("web_search", query=search_query, max_results=3)

            search_info = ""
            if isinstance(search_result, dict) and search_result.get("success"):
                search_content = search_result.get("response", "") or search_result.get("formatted_response", "")
                if search_content and not str(search_content).startswith("❌"):
                    search_info = str(search_content)[:1000]

            generation = await _generate_task_breakdown_with_llm(target, original_message, search_info)

            return {
                "response": generation["plan"],
                "suggestions": generation["suggestions"],
                "actions": generation["actions"],
                "action": action,
                "confidence": confidence
            }
        
        elif action == "database_query":
            # 执行数据库查询（使用Tool Box）
            try:
                operation = args.get("operation", "query")
                sql_query = args.get("query", "")
                description = args.get("description", "数据库查询")
                
                # 🔒 专事专办：强制在待办任务查询中添加session_id过滤
                if ("tasks" in sql_query.lower() and "status" in sql_query.lower() and 
                    "pending" in sql_query.lower() and "session_id" not in sql_query.lower() and 
                    "SELECT" in sql_query.upper()):
                    
                    logger.warning(f"🚨 检测到LLM生成的无会话过滤SQL: {sql_query}")
                    
                    # 强制添加会话过滤
                    if session_id:
                        # 在WHERE子句中添加session_id过滤
                        if "WHERE" in sql_query.upper():
                            sql_query = sql_query.replace("WHERE", f"WHERE session_id = '{session_id}' AND", 1)
                        else:
                            # 如果没有WHERE子句，添加一个
                            sql_query = sql_query.replace("FROM tasks", f"FROM tasks WHERE session_id = '{session_id}'")
                        
                        logger.info(f"✅ 已修正为带会话过滤的SQL: {sql_query}")
                    else:
                        # 没有session_id时，只返回全局任务
                        if "WHERE" in sql_query.upper():
                            sql_query = sql_query.replace("WHERE", "WHERE session_id IS NULL AND", 1)
                        else:
                            sql_query = sql_query.replace("FROM tasks", "FROM tasks WHERE session_id IS NULL")
                        
                        logger.info(f"🌐 已修正为全局任务SQL: {sql_query}")
                
                # 调用Tool Box的database_query工具（注意参数名是sql而不是query）
                result = await execute_tool("database_query", 
                                        database="data/databases/main/tasks.db",
                                        sql=sql_query, 
                                        operation=operation)
                
                if isinstance(result, dict) and result.get("success"):
                    # 统一处理execute操作，不使用关键词匹配
                    if operation == "execute":
                        rows_affected = result.get("rows_affected", 0)
                        if rows_affected > 0:
                            response = f"✅ **{description}成功**：\n\n影响了 {rows_affected} 条记录"
                        else:
                            response = f"📭 **{description}完成**：\n\n没有记录受到影响"
                    else:
                        # 查询操作
                        data = result.get("rows", [])
                        if data:
                            response = f"📊 {description}结果：\n\n"
                            if isinstance(data, list) and len(data) > 0:
                                response += f"找到 {len(data)} 条记录：\n"
                                for i, item in enumerate(data[:10], 1):  # 最多显示10条
                                    if isinstance(item, dict):
                                        name = item.get("name", f"记录{i}")
                                        status = item.get("status", "未知")
                                        # 清理任务名称，移除前缀
                                        if name.startswith(('ROOT:', 'COMPOSITE:', 'ATOMIC:')):
                                            name = name.split(':', 1)[1].strip()
                                        response += f"{i}. {name} ({status})\n"
                            else:
                                response += str(data)
                        else:
                            response = "📭 暂无相关数据"
                else:
                    response = f"❌ 数据库操作失败: {result}"
                
                return {
                    "response": response,
                    "suggestions": ["查看详细信息", "刷新数据", "修改筛选条件"],
                    "actions": [{"type": "refresh_data", "label": "刷新数据", "data": {}}],
                    "action": action,
                    "confidence": confidence
                }
            except Exception as e:
                logger.error(f"❌ 数据库查询执行失败: {e}")
                return {
                    "response": f"❌ 查询执行失败: {str(e)}",
                    "suggestions": ["重试查询", "检查连接"],
                    "actions": [],
                    "action": action,
                    "confidence": confidence
                }
        
        elif action == "search":
            # 执行网络搜索（使用Tool Box）
            try:
                query = args.get("query", original_message)
                max_results = args.get("max_results", 5)
                
                # 调用Tool Box的web_search工具
                result = await execute_tool("web_search", query=query, max_results=max_results)
                
                if isinstance(result, dict) and result.get("success"):
                    search_response = result.get("formatted_response", str(result))
                else:
                    search_response = f"🔍 搜索结果：{str(result)}"
                
                return {
                    "response": search_response,
                    "suggestions": ["搜索更多", "相关信息", "继续对话"],
                    "actions": [{"type": "search_more", "label": "搜索更多", "data": {"query": query}}],
                    "action": action,
                    "confidence": confidence
                }
            except Exception as e:
                logger.error(f"❌ 网络搜索执行失败: {e}")
                return {
                    "response": f"❌ 搜索执行失败: {str(e)}",
                    "suggestions": ["重试搜索", "修改查询"],
                    "actions": [],
                    "action": action,
                    "confidence": confidence
                }
        
        elif action == "show_plan":
            # 显示计划 - 传递会话信息以支持专事专办
            workflow_id = context.get("workflow_id") if context else None
            plan_response = await _handle_plan_query(
                args.get("title", ""),
                session_id=session_id,
                workflow_id=workflow_id
            )
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


async def _handle_plan_query(title: str, session_id: str = None, workflow_id: str = None) -> str:
    """处理计划查询请求 - 支持会话级隔离"""
    try:
        from ..repository.tasks import default_repo
        from ..utils.route_helpers import resolve_scope_params
        
        # 直接查询数据库，支持会话隔离
        try:
            resolved_session, resolved_workflow = resolve_scope_params(
                session_id, workflow_id, require_scope=True
            )
        except Exception:
            # 如果没有会话信息，返回提示
            return "🔒 请先创建一个任务或计划，然后我就能显示当前工作空间的内容了。"
        
        # 获取当前会话的所有任务
        tasks = default_repo.list_all_tasks(session_id=resolved_session, workflow_id=resolved_workflow)
        
        # 找出ROOT任务（计划）
        root_tasks = [t for t in tasks if t.get("task_type") == "root"]
        
        if not root_tasks:
            return "📋 当前工作空间中没有计划。您可以通过聊天创建新的计划。"
        
        response_text = f"📊 **当前工作空间计划概览**\n\n📝 **计划数量**: {len(root_tasks)}\n\n"
        
        # 显示每个ROOT计划的详细信息
        for i, plan in enumerate(root_tasks, 1):
            plan_title = plan.get("name", "未命名计划")
            status = plan.get("status", "pending")
            plan_id = plan.get("id")
            workflow = plan.get("workflow_id", "未知")
            
            # 获取这个计划下的子任务数量
            subtasks = [t for t in tasks if t.get("root_id") == plan_id]
            subtask_count = len(subtasks)
            
            status_emoji = {
                "pending": "⏳",
                "running": "🏃",
                "completed": "✅",
                "failed": "❌"
            }.get(status, "📌")
            
            response_text += f"{i}. {status_emoji} **{plan_title}**\n"
            response_text += f"   📋 计划ID: {plan_id}\n"
            response_text += f"   🔄 工作流: {workflow}\n" 
            response_text += f"   📊 状态: {status}\n"
            response_text += f"   👥 子任务数: {subtask_count}\n\n"
        
        response_text += f"💡 这是您当前工作空间的专属计划，实现了真正的'专事专办'。"
        return response_text
        
    except Exception as e:
        logger.error(f"❌ 计划查询失败: {e}")
        return f"📋 查询计划时出错: {str(e)}\n\n您可以通过聊天创建新的计划。"


async def _handle_task_query(message: str, session_id: str = None, workflow_id: str = None) -> str:
    """处理任务查询请求，支持会话级隔离"""
    try:
        from ..repository.tasks import default_repo
        from ..utils.route_helpers import resolve_scope_params
        
        # 强制会话隔离
        try:
            resolved_session, resolved_workflow = resolve_scope_params(
                session_id, workflow_id, require_scope=True
            )
        except Exception:
            return "🔒 请先在当前对话中创建一个任务或计划，然后我就能显示当前工作空间的任务了。"
        
        # 获取当前会话的任务
        all_tasks = default_repo.list_all_tasks(session_id=resolved_session, workflow_id=resolved_workflow)
        
        if not all_tasks:
            return "📋 当前工作空间中没有任务。您可以通过聊天创建新的计划和任务。"
        
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
        response = f"""📊 **当前工作空间任务统计**
        
🔒 **会话**: {resolved_session}
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
            
        response += f"\n\n💡 这是您当前工作空间的专属任务，实现了真正的'专事专办'。\n🎯 您可以询问特定任务的详情，或请求按优先级、类型筛选任务。"
        
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

async def _should_create_new_workflow(
    message: str, 
    session_id: Optional[str], 
    context: Optional[Dict[str, Any]],
    context_messages: Optional[List[Dict[str, str]]] = None
) -> Dict[str, Any]:
    """
    使用LLM智能判断用户意图
    
    Returns:
        {
            "create_new_root": bool,   # 是否创建新ROOT任务
            "add_to_existing": bool,   # 是否在现有ROOT下添加子任务
            "decompose_task": bool,    # 是否拆分现有任务
            "execute_task": bool,      # 是否执行现有任务
            "existing_root_id": int,   # 现有ROOT任务的ID
            "task_id": int,           # 要操作的任务ID
            "task_name": str,         # 要操作的任务名称
            "reasoning": str          # LLM的推理过程
        }
    """
    from ..repository.tasks import default_repo
    
    # 1. 检查session中是否已有ROOT任务
    existing_root = None
    # 查询当前session的任务
    all_pending_tasks = []
    if session_id:
        try:
            # 查询当前session的ROOT任务
            from ..database import get_db
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, name, status FROM tasks WHERE session_id = ? AND task_type = 'root' ORDER BY created_at DESC LIMIT 1",
                    (session_id,)
                )
                result = cursor.fetchone()
                if result:
                    existing_root = {"id": result[0], "name": result[1], "status": result[2]}
                    logger.info(f"📋 发现现有ROOT任务: {existing_root['name']} (ID: {existing_root['id']})")
                    
                    # 查询所有pending任务，让LLM了解上下文
                    cursor.execute(
                        """SELECT id, name, task_type, parent_id 
                           FROM tasks 
                           WHERE session_id = ? AND status = 'pending' 
                           ORDER BY id ASC
                           LIMIT 20""",
                        (session_id,)
                    )
                    all_pending_tasks = cursor.fetchall()
                    logger.info(f"📋 当前session有 {len(all_pending_tasks)} 个pending任务")
        except Exception as e:
            logger.warning(f"查询ROOT任务失败: {e}")
    
    # 2. 使用LLM判断用户意图
    from ..llm import get_default_client
    llm_client = get_default_client()
    
    # 构建分析prompt
    if existing_root:
        # 构建任务列表文本
        task_list_text = ""
        if all_pending_tasks:
            task_list_text = "\n**当前工作空间的任务列表**:\n"
            for task_id, task_name, task_type, parent_id in all_pending_tasks[:10]:  # 最多显示10个
                task_list_text += f"  • ID:{task_id} - {task_name} [{task_type.upper()}]\n"
        
        prompt = f"""你是一个智能任务规划助手。当前用户在一个对话session中已经有一个进行中的ROOT任务和子任务：

**现有ROOT任务**: {existing_root['name']} (ID: {existing_root['id']})
{task_list_text}

**用户当前消息**: {message}

**判断任务**:
分析用户的消息，判断用户的意图是：
A) 创建一个全新的、独立的ROOT任务（与现有任务完全无关的新项目）
B) 在现有ROOT任务下添加相关的子任务或补充内容
C) 拆分现有的任务为子任务（把一个任务分解成更小的子任务）
D) 执行/完成现有的任务（开始运行某个已创建的任务）
E) 普通对话，不需要创建或执行任务

**判断标准**（重要！请仔细匹配）:
1. **"拆分"、"分解"、"细化"、"拆成"关键词** → C（拆分任务）
   例如："拆分第1个任务"、"帮我拆分任务"、"分解这个任务"
   
2. **"完成"、"执行"、"运行"、"开始做"、"帮我做"关键词** → D（执行任务）
   例如："完成任务507"、"执行这个任务"、"帮我完成XXX"
   ⚠️ 注意："完成XXX研究"如果XXX在任务列表中，选D而不是A！
   
3. **"新的"、"另一个"、"不同的项目"、与现有任务完全不同的主题** → A（创建新ROOT）
   例如："我想研究另一个主题"、"创建一个新项目"
   
4. **"相关的"、"这个"、"补充"、"添加"** → B（添加子任务）
   
5. **问问题、闲聊、查询信息** → E（普通对话）

**特别注意**:
- 如果用户消息中提到的任务名称在上面的任务列表中出现，优先判断为C（拆分）或D（执行）
- "完成一下这个任务：XXX" → 检查XXX是否在任务列表中 → 如果在，选D；如果不在且是新主题，选A

请以JSON格式回复：
{{
  "intent": "A" | "B" | "C" | "D" | "E",
  "task_id": <任务ID，如果用户提到>,
  "task_name": "<任务名称，如果用户提到>",
  "reasoning": "你的分析理由",
  "confidence": 0.0-1.0
}}
"""
    else:
        # 没有现有ROOT任务，判断是否需要创建新任务
        prompt = f"""你是一个智能任务规划助手。分析用户消息，判断是否需要创建一个任务计划。

**用户消息**: {message}

**判断标准**:
- 需要创建任务：用户想要学习、研究、开发、规划某个复杂主题或项目
- 不需要创建：简单问答、闲聊、单一信息查询

请以JSON格式回复：
{{
  "needs_task": true | false,
  "reasoning": "你的分析理由",
  "confidence": 0.0-1.0
}}
"""
    
    try:
        response = llm_client.chat(prompt, force_real=True)
        logger.info(f"🤖 LLM原始回复: {response[:200]}...")  # 只打印前200字符
        
        from ..utils import parse_json_obj
        result = parse_json_obj(response)
        logger.info(f"📊 解析后的结果: {result}")
        
        if existing_root:
            intent = result.get("intent", "E")
            if intent == "A":
                return {
                    "create_new_root": True,
                    "add_to_existing": False,
                    "decompose_task": False,
                    "execute_task": False,
                    "existing_root_id": None,
                    "reasoning": result.get("reasoning", "")
                }
            elif intent == "B":
                return {
                    "create_new_root": False,
                    "add_to_existing": True,
                    "decompose_task": False,
                    "execute_task": False,
                    "existing_root_id": existing_root["id"],
                    "existing_root_name": existing_root["name"],
                    "reasoning": result.get("reasoning", "")
                }
            elif intent == "C":
                # 拆分任务
                return {
                    "create_new_root": False,
                    "add_to_existing": False,
                    "decompose_task": True,
                    "execute_task": False,
                    "existing_root_id": existing_root["id"],
                    "existing_root_name": existing_root["name"],
                    "task_id": result.get("task_id"),
                    "task_name": result.get("task_name"),
                    "reasoning": result.get("reasoning", "")
                }
            elif intent == "D":
                # 执行任务
                return {
                    "create_new_root": False,
                    "add_to_existing": False,
                    "decompose_task": False,
                    "execute_task": True,
                    "existing_root_id": existing_root["id"],
                    "existing_root_name": existing_root["name"],
                    "reasoning": result.get("reasoning", "")
                }
            else:
                # E - 普通对话
                return {
                    "create_new_root": False,
                    "add_to_existing": False,
                    "decompose_task": False,
                    "execute_task": False,
                    "existing_root_id": None,
                    "reasoning": result.get("reasoning", "")
                }
        else:
            needs_task = result.get("needs_task", False)
            if needs_task:
                return {
                    "create_new_root": True,
                    "add_to_existing": False,
                    "decompose_task": False,
                    "execute_task": False,
                    "existing_root_id": None,
                    "reasoning": result.get("reasoning", "")
                }
            else:
                return {
                    "create_new_root": False,
                    "add_to_existing": False,
                    "decompose_task": False,
                    "execute_task": False,
                    "existing_root_id": None,
                    "reasoning": result.get("reasoning", "")
                }
    except Exception as e:
        logger.error(f"LLM判断失败: {e}")
        # Fallback
        if existing_root:
            # 检查关键词
            decompose_keywords = ["拆分", "分解", "细化", "拆分第"]
            execute_keywords = ["执行", "完成", "开始", "运行", "做", "帮我做"]
            
            if any(kw in message for kw in decompose_keywords):
                return {
                    "create_new_root": False,
                    "add_to_existing": False,
                    "decompose_task": True,
                    "execute_task": False,
                    "existing_root_id": existing_root["id"],
                    "existing_root_name": existing_root["name"],
                    "reasoning": "Fallback: 检测到拆分关键词"
                }
            elif any(kw in message for kw in execute_keywords):
                return {
                    "create_new_root": False,
                    "add_to_existing": False,
                    "decompose_task": False,
                    "execute_task": True,
                    "existing_root_id": existing_root["id"],
                    "existing_root_name": existing_root["name"],
                    "reasoning": "Fallback: 检测到执行关键词"
                }
            elif len(message) < 50:
                return {
                    "create_new_root": False,
                    "add_to_existing": True,
                    "decompose_task": False,
                    "execute_task": False,
                    "existing_root_id": existing_root["id"],
                    "existing_root_name": existing_root["name"],
                    "reasoning": "Fallback: 简短消息 + 现有ROOT"
                }
        return {
            "create_new_root": False,
            "add_to_existing": False,
            "decompose_task": False,
            "execute_task": False,
            "existing_root_id": None,
            "reasoning": "LLM分析失败，默认为普通对话"
        }


async def _handle_task_decomposition(
    request: ChatRequest,
    workflow_decision: Dict[str, Any],
    context_messages: Optional[List[Dict[str, str]]] = None
) -> ChatResponse:
    """拆分现有任务为子任务"""
    from ..repository.tasks import default_repo
    from ..llm import get_default_client
    
    logger.info(f"🔀 进入任务拆分函数")
    logger.info(f"📝 用户消息: {request.message}")
    logger.info(f"🆔 Session ID: {request.session_id}")
    
    try:
        # 1. 查询session中的任务
        from ..database import get_db
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, name, status, task_type, parent_id, root_id 
                   FROM tasks 
                   WHERE session_id = ? AND status = 'pending' 
                   ORDER BY id ASC""",
                (request.session_id,)
            )
            all_tasks = cursor.fetchall()
        
        if not all_tasks:
            return ChatResponse(
                response="❌ 当前工作空间没有可拆分的任务。\n\n💡 请先创建一个ROOT任务。",
                suggestions=["创建新任务"],
                metadata={"error": "no_tasks"}
            )
        
        # 2. 使用LLM匹配用户想拆分的任务
        llm_client = get_default_client()
        
        # 构建任务列表
        task_list = []
        for i, task in enumerate(all_tasks):
            task_id, name, status, task_type, parent_id, root_id = task
            task_list.append(f"[{i+1}] ID: {task_id}, 名称: \"{name}\", 类型: {task_type}")
        
        prompt = f"""用户想要拆分一个任务。

**用户消息**: {request.message}

**可拆分任务列表**:
{chr(10).join(task_list)}

请分析用户最可能想要拆分哪个任务。

**规则**:
1. ROOT任务可以拆分为COMPOSITE任务
2. COMPOSITE任务可以拆分为ATOMIC任务
3. ATOMIC任务不能再拆分
4. 如果用户说"第1个"、"第一个"，选择对应序号
5. 如果用户提到任务名称，选择匹配度最高的
6. 优先选择ROOT和COMPOSITE类型的任务

返回JSON：
{{
  "task_id": <任务ID>,
  "reasoning": "为什么选择这个任务"
}}
"""
        
        response = llm_client.chat(prompt, force_real=True)
        from ..utils import parse_json_obj
        result = parse_json_obj(response)
        
        task_id = result.get("task_id")
        if not task_id:
            task_id = all_tasks[0][0]  # 默认选第一个
        
        # 3. 检查任务是否存在和类型
        task = default_repo.get_task_info(task_id)
        if not task:
            return ChatResponse(
                response=f"❌ 任务 ID: {task_id} 不存在。",
                metadata={"error": "task_not_found"}
            )
        
        task_name = task.get("name", "")
        task_type = task.get("task_type", "")
        
        # 4. 检查是否是ATOMIC任务
        if task_type == "atomic":
            return ChatResponse(
                response=f"""❌ **无法拆分ATOMIC任务！**

📋 **任务**: {task_name}
🆔 **ID**: {task_id}
📊 **类型**: atomic

⚠️ ATOMIC任务是最小执行单元，不能再拆分。

💡 你可以：
• 直接执行这个ATOMIC任务："帮我完成任务{task_id}"
• 拆分其他ROOT或COMPOSITE任务
• 查看任务列表选择其他任务""",
                suggestions=["执行ATOMIC任务", "查看任务列表"],
                metadata={"error": "atomic_cannot_decompose", "task_id": task_id}
            )
        
        logger.info(f"🔀 开始拆分任务: {task_name} (ID: {task_id}, Type: {task_type})")
        
        # 5. 调用拆分API
        logger.info(f"🔧 准备调用拆分API: /tasks/{task_id}/decompose")
        
        api_result = await execute_tool(
            "internal_api",
            endpoint=f"/tasks/{task_id}/decompose",
            method="POST",
            data={"max_subtasks": 5, "force": False, "tool_aware": True},
            timeout=60.0
        )
        
        logger.info(f"📦 拆分API返回结果: {api_result}")
        
        if not api_result or not api_result.get("success"):
            error_msg = api_result.get("error", "未知错误") if api_result else "API调用失败"
            return ChatResponse(
                response=f"❌ 拆分任务失败: {error_msg}",
                metadata={"error": error_msg}
            )
        
        # 6. 解析结果
        decompose_data = api_result.get("data", {})
        subtasks = decompose_data.get("subtasks", [])
        child_type = "ATOMIC" if task_type == "composite" else "COMPOSITE"
        
        return ChatResponse(
            response=f"""✅ **任务拆分完成！**

📋 **原任务**: {task_name}
🆔 **任务ID**: {task_id}
📊 **类型**: {task_type}

🔄 **已创建 {len(subtasks)} 个{child_type}子任务**:
{chr(10).join([f"{i+1}. {st.get('name', '未命名')} (ID: {st.get('id')})" for i, st in enumerate(subtasks[:5])])}

💡 下一步：
• 继续拆分{child_type}任务为更小的单元
• 开始执行ATOMIC任务
• 查看完整任务结构""",
            suggestions=["查看任务列表", "继续拆分", "开始执行"],
            metadata={
                "task_id": task_id,
                "subtask_count": len(subtasks),
                "child_type": child_type,
                "action": "task_decomposed"
            }
        )
        
    except Exception as e:
        logger.error(f"拆分任务失败: {e}")
        return ChatResponse(
            response=f"❌ 拆分任务时出错: {str(e)}",
            metadata={"error": str(e)}
        )


async def _handle_task_execution(
    request: ChatRequest,
    workflow_decision: Dict[str, Any],
    context_messages: Optional[List[Dict[str, str]]] = None
) -> ChatResponse:
    """执行现有任务"""
    from ..repository.tasks import default_repo
    from ..execution.executors.tool_enhanced import ToolEnhancedExecutor
    from ..llm import get_default_client
    
    logger.info(f"▶️ 进入任务执行函数")
    logger.info(f"📝 用户消息: {request.message}")
    logger.info(f"🆔 Session ID: {request.session_id}")
    
    try:
        # 1. 查询session中的ATOMIC任务
        from ..database import get_db
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, name, status, task_type, parent_id, root_id 
                   FROM tasks 
                   WHERE session_id = ? AND status = 'pending' 
                   ORDER BY task_type DESC, id ASC""",
                (request.session_id,)
            )
            pending_tasks = cursor.fetchall()
        
        logger.info(f"📋 查询到 {len(pending_tasks)} 个pending任务")
        if pending_tasks:
            for task in pending_tasks[:5]:  # 只打印前5个
                logger.info(f"   - ID: {task[0]}, 名称: {task[1]}, 类型: {task[3]}")
        
        if not pending_tasks:
            return ChatResponse(
                response="❌ 当前工作空间没有待执行的任务。\n\n💡 你可以先创建一个任务或说'查看任务列表'。",
                suggestions=["创建新任务", "查看所有任务"],
                metadata={"error": "no_pending_tasks"}
            )
        
        # 2. 使用LLM匹配用户想要执行的任务
        llm_client = get_default_client()
        
        # 构建任务列表
        task_list = []
        for i, task in enumerate(pending_tasks):
            task_id, name, status, task_type, parent_id, root_id = task
            task_list.append(f"[{i+1}] ID: {task_id}, 名称: \"{name}\", 类型: {task_type}")
        
        prompt = f"""用户想要执行一个任务。

**用户消息**: {request.message}

**可执行任务列表**:
{chr(10).join(task_list)}

请分析用户最可能想要执行哪个任务。

优先级：
1. ATOMIC任务（最小执行单元，可以直接执行）
2. 如果用户明确提到任务ID，选择该ID
3. 如果用户提到任务名称，选择匹配度最高的
4. 如果用户说"第一个"、"第二个"，选择对应序号

返回JSON：
{{
  "task_id": <任务ID>,
  "reasoning": "为什么选择这个任务"
}}
"""
        
        response = llm_client.chat(prompt, force_real=True)
        from ..utils import parse_json_obj
        result = parse_json_obj(response)
        
        task_id = result.get("task_id")
        if not task_id:
            task_id = pending_tasks[0][0]  # 默认选第一个
        
        # 3. 执行任务
        task = default_repo.get_task_info(task_id)
        if not task:
            return ChatResponse(
                response=f"❌ 任务 ID: {task_id} 不存在。",
                metadata={"error": "task_not_found"}
            )
        
        task_name = task.get("name", "")
        task_type = task.get("task_type", "")
        
        logger.info(f"▶️ 开始执行任务: {task_name} (ID: {task_id}, Type: {task_type})")
        
        # 执行任务
        executor = ToolEnhancedExecutor(repo=default_repo)
        status = await executor.execute_task(
            task=task,
            use_context=True,
            context_options={"force_save_output": True}
        )
        
        # 获取任务输出
        output_content = default_repo.get_task_output_content(task_id)
        
        return ChatResponse(
            response=f"""✅ **任务执行完成！**

📋 **任务名称**: {task_name}
🆔 **任务ID**: {task_id}
📊 **类型**: {task_type}
✨ **状态**: {status}

**执行结果**:
{output_content[:500] if output_content else '（无输出内容）'}
{'...' if output_content and len(output_content) > 500 else ''}

💾 完整输出已保存到 results/ 目录的层级结构中。

💡 你可以继续执行其他任务，或查看任务列表。""",
            suggestions=["查看任务列表", "执行下一个任务", "查看完整输出"],
            metadata={
                "task_id": task_id,
                "status": status,
                "has_output": bool(output_content),
                "action": "task_executed"
            }
        )
        
    except Exception as e:
        logger.error(f"执行任务失败: {e}")
        return ChatResponse(
            response=f"❌ 执行任务时出错: {str(e)}",
            metadata={"error": str(e)}
        )


async def _handle_add_subtask_to_existing(
    request: ChatRequest, 
    workflow_decision: Dict[str, Any],
    context_messages: Optional[List[Dict[str, str]]] = None
) -> ChatResponse:
    """在现有ROOT任务下添加子任务"""
    from ..repository.tasks import default_repo
    
    existing_root_id = workflow_decision.get("existing_root_id")
    existing_root_name = workflow_decision.get("existing_root_name", "现有项目")
    
    logger.info(f"📎 在ROOT任务 {existing_root_id} 下添加子任务: {request.message}")
    
    # 创建一个新的COMPOSITE或ATOMIC任务
    try:
        # 使用LLM生成任务描述
        from ..llm import get_default_client
        llm_client = get_default_client()
        
        prompt = f"""用户在项目"{existing_root_name}"下提出了新的需求：{request.message}

请生成一个简洁的任务名称（不超过50字）："""
        
        task_name = llm_client.chat(prompt, force_real=True).strip()
        # 清理任务名称
        task_name = task_name.strip('"\'')
        if len(task_name) > 50:
            task_name = task_name[:50]
        
        # 创建子任务
        task_id = default_repo.create_task(
            name=f"COMPOSITE: {task_name}",
            status="pending",
            priority=1,
            parent_id=existing_root_id,
            root_id=existing_root_id,
            task_type="composite",
            session_id=request.session_id
        )
        
        return ChatResponse(
            response=f"""✅ **已在现有项目下添加子任务！**

📋 **父任务**: {existing_root_name}
📝 **新任务**: {task_name}
🆔 **任务ID**: {task_id}
📊 **状态**: pending

🎯 该任务已加入您的项目计划中。系统会在执行时自动：
• 在 `results/{existing_root_name}/` 目录下创建相应的文件结构
• ATOMIC子任务会生成为 .md 文件

💡 你可以继续补充更多需求，或者说"开始执行任务"来运行它们。""",
            suggestions=["开始执行任务", "查看任务列表", "继续添加任务"],
            metadata={
                "task_id": task_id,
                "parent_id": existing_root_id,
                "root_name": existing_root_name,
                "action": "subtask_added"
            }
        )
    except Exception as e:
        logger.error(f"创建子任务失败: {e}")
        return ChatResponse(
            response=f"❌ 创建子任务时出错: {str(e)}",
            metadata={"error": str(e)}
        )


def _is_agent_workflow_intent(message: str) -> bool:
    """检测是否为Agent工作流程创建意图 - 加强过滤，避免简单问候触发任务
    
    ⚠️ DEPRECATED: 此函数已被 _should_create_new_workflow 替代
    """
    
    # 🚫 首先排除简单问候和常见对话
    simple_excludes = [
        # 问候语
        "你好", "hi", "hello", "嗨", "在吗", "在不在", "早上好", "下午好", "晚上好",
        # 简单询问  
        "怎么样", "如何", "什么", "哪里", "为什么", "干嘛", "在干嘛",
        # 状态询问
        "最近", "现在", "目前", "当前",
        # 简单回复
        "好的", "可以", "不行", "没问题", "谢谢", "不客气"
    ]
    
    message_clean = message.strip().lower()
    
    # 🔍 长度过滤：小于8个字符的消息通常不是复杂任务
    if len(message_clean) < 8:
        return False
        
    # 🔍 简单问候过滤  
    if any(exclude in message_clean for exclude in simple_excludes):
        # 如果包含问候词且长度<20，大概率是简单问候
        if len(message_clean) < 20:
            return False
    
    # 🔍 问号结尾的短句通常是询问，不是任务创建
    if message_clean.endswith('?') or message_clean.endswith('？'):
        if len(message_clean) < 30:
            return False
    
    # 排除纯学习计划请求 - 优先级最高
    learning_plan_indicators = [
        "学习C++", "学习Python", "学习Java", "学习JavaScript", "学习因果推断",
        "c++", "python", "java", "javascript",
        "学习计划", "教程", "课程", "培训"
    ]
    
    # 如果是纯学习计划请求，不触发Agent工作流程
    if any(indicator.lower() in message.lower() for indicator in learning_plan_indicators):
        # 进一步检查是否真的是纯学习请求
        pure_learning_patterns = [
            r"(学习|掌握).*(C\+\+|Python|Java|JavaScript|因果推断)",
            r"(写|制定|制作).*(计划|教程|指南).*(学习|掌握)",
            r"帮我.*(计划|规划).*(学习|教程)"
        ]
        if any(re.search(pattern, message, re.IGNORECASE) for pattern in pure_learning_patterns):
            return False
    
    # 基础工作流程关键词 - 排除学习相关
    workflow_keywords = [
        "构建", "开发", "制作", "建立", "设计", "实现",
        "项目", "应用", "平台", "工具", "框架", "系统",
        "方案", "流程"
    ]
    
    # 强意图检测模式 - 更精确
    strong_patterns = [
        # 软件开发相关
        r"(构建|开发|创建|制作|建立).+(系统|项目|应用|平台|工具)",
        r"(设计|实现).+(方案|流程|架构)",
        r"我想要.+(做|建|写|开发).+(系统|项目|应用)",
        # 复杂工作流程
        r"(帮我|帮忙).*(制定|规划|设计).*(方案|流程|步骤)",
        r"(整理|制定|规划).*(工作|项目|开发).*(流程|步骤)"
    ]
    
    # 检查强模式
    for pattern in strong_patterns:
        if re.search(pattern, message):
            return True
    
    # 检查基础关键词组合 - 需要至少2个工作流程关键词
    keyword_count = sum(1 for keyword in workflow_keywords if keyword in message.lower())
    return keyword_count >= 2




def _format_dag_preview(dag_nodes: List[Dict[str, Any]]) -> str:
    """将DAG节点渲染为文本树，方便在聊天窗口中快速预览。"""
    if not dag_nodes:
        return "（暂无DAG数据）"

    by_parent = defaultdict(list)
    for node in dag_nodes:
        by_parent[node.get("parent_id")].append(node)

    for siblings in by_parent.values():
        siblings.sort(key=lambda n: (n.get("depth", 0), n.get("id", 0), str(n.get("name", ""))))

    root_candidates = [n for n in dag_nodes if n.get("parent_id") is None]
    root = root_candidates[0] if root_candidates else dag_nodes[0]

    lines: List[str] = []

    def render(node: Dict[str, Any], prefix: str = "", is_last: bool = True) -> None:
        connector = "└──" if is_last else "├──"
        name = node.get("name") or "未命名任务"
        task_type = (node.get("task_type") or "unknown").upper()
        label = f"{name} [{task_type}]"
        if not prefix:
            lines.append(label)
        else:
            lines.append(f"{prefix}{connector} {label}")

        children = by_parent.get(node.get("id"), [])
        child_prefix = prefix + ("    " if is_last else "│   ")
        for idx, child in enumerate(children):
            render(child, child_prefix, idx == len(children) - 1)

    render(root)
    return "\n".join(lines)


def _format_execution_plan(execution_plan: List[Dict[str, Any]], max_steps: int = 5) -> str:
    """格式化执行计划，突出最早需要关注的任务。"""
    if not execution_plan:
        return "暂无执行计划数据"

    lines: List[str] = []
    for index, step in enumerate(execution_plan[:max_steps]):
        order = step.get("execution_order") or index + 1
        try:
            order_int = int(order)
        except Exception:
            order_int = index + 1
        name = step.get("name") or f"步骤{order_int}"
        prerequisites = step.get("prerequisites") or []
        prereq_text = ", ".join(str(p) for p in prerequisites) if prerequisites else "无"
        duration = step.get("estimated_duration") or "未估算"
        lines.append(f"{order_int}. {name}（前置: {prereq_text}，预计: {duration}）")

    if len(execution_plan) > max_steps:
        lines.append("...（更多任务已生成，可在DAG面板查看）")

    return "\n".join(lines)

async def _handle_agent_workflow_creation(request: ChatRequest, context_messages: Optional[List[Dict[str, str]]] = None) -> ChatResponse:
    """处理Agent工作流程创建"""
    try:
        # 先搜索相关专业信息以提高规划质量
        search_enhanced_goal = request.message
        if any(keyword in request.message for keyword in ["学习", "计划", "指南"]):
            logger.info(f"🔍 学习计划请求，先搜索相关信息: {request.message}")
            search_result = await execute_tool("web_search", query=request.message, max_results=3)
            if search_result and search_result.get("success"):
                search_content = search_result.get("response", "")
                if search_content and not search_content.startswith("❌"):
                    search_enhanced_goal = f"{request.message}\n\n参考信息：{search_content[:800]}"
        
        # 🔧 通过tool-box调用Agent工作流程创建API
        # 构建上下文信息（确保携带会话/工作流标识）
        context_info = request.context or {}
        # 强制补齐 session_id 与 workflow_id，避免后端创建到错误会话
        try:
            if request.session_id:
                context_info["session_id"] = request.session_id
        except Exception:
            pass
        try:
            # ChatRequest 可能不含 workflow_id 字段，做兼容处理
            wf_id = getattr(request, "workflow_id", None) or context_info.get("workflow_id")
            if wf_id:
                context_info["workflow_id"] = wf_id
        except Exception:
            pass
        if context_messages:
            context_info["conversation_history"] = context_messages[-3:]  # 最近3条消息
        
        agent_request = {
            "goal": search_enhanced_goal,
            "context": context_info,
            "user_preferences": {}
        }
        
        # 使用tool-box的internal_api工具替代直接的httpx调用
        api_result = await execute_tool(
            "internal_api",
            endpoint="/agent/create-workflow", 
            method="POST",
            data=agent_request,
            timeout=60.0
        )
        
        if api_result and api_result.get("success"):
            workflow_data = api_result.get("data", {})
            
            # 构建用户友好的响应并动态摘要工作流结构
            metadata = workflow_data.get('metadata') or {}
            dag_nodes = workflow_data.get('dag_structure') or []
            execution_plan = workflow_data.get('execution_plan') or []

            task_counts = Counter(node.get('task_type', 'unknown') for node in dag_nodes)
            total_tasks = metadata.get('total_tasks') or len(dag_nodes)
            root_count = task_counts.get('root', 0)
            composite_count = task_counts.get('composite', 0)
            atomic_count = task_counts.get('atomic', 0)

            dag_preview = _format_dag_preview(dag_nodes)
            execution_summary = _format_execution_plan(execution_plan)
            key_tasks = [node.get('name', '未命名任务') for node in dag_nodes if node.get('task_type') == 'composite'][:3]

            goal_text = workflow_data.get('goal', request.message)
            estimated_completion = metadata.get('estimated_completion') or '未提供'
            created_at = metadata.get('created_at')

            response_lines = [
                "🤖 **Agent工作流程已创建！**",
                "",
                f"📋 **目标**: {goal_text}",
                f"🔢 **任务总数**: {total_tasks} 个（ROOT {root_count}、COMPOSITE {composite_count}、ATOMIC {atomic_count}）",
                f"⏱️ **预计完成时间**: {estimated_completion}",
            ]
            if created_at:
                response_lines.append(f"🗓️ **创建时间戳**: {created_at}")
            if key_tasks:
                response_lines.append("")
                response_lines.append("**📌 关键任务概览**:")
                for name in key_tasks:
                    response_lines.append(f"- {name}")
            response_lines.append("")
            response_lines.append("**🧭 执行计划（前若干步）**:")
            response_lines.append(execution_summary)
            response_lines.append("")

            response_lines.append("**📊 DAG结构预览**:")
            response_lines.append("```")
            response_lines.append(dag_preview)
            response_lines.append("```")
            next_steps = [
                "打开右侧DAG视图检查依赖关系",
                "根据需要调整任务内容或顺序",
                "确认执行前置任务后继续推进",
            ]
            if key_tasks:
                next_steps.insert(0, f"细化任务：{key_tasks[0]}")
            response_lines.append("")
            response_lines.append("**🎯 下一步操作**:")
            for idx, item in enumerate(next_steps, 1):
                response_lines.append(f"{idx}. {item}")

            response_text = "\n".join(response_lines)
            suggestions = [
                "查看DAG结构图",
                "检查执行计划详情",
                "调整任务或依赖关系",
                "开始执行首个任务",
            ]
            if key_tasks:
                suggestions.insert(0, f"聚焦任务：{key_tasks[0]}")

            return ChatResponse(
                response=response_text,
                suggestions=suggestions,
                actions=[
                    {
                        "type": "show_dag",
                        "label": "显示DAG图",
                        "data": {"workflow_id": workflow_data.get('workflow_id')}
                    },
                    {
                        "type": "approve_workflow",
                        "label": "确认并开始执行",
                        "data": {"workflow_id": workflow_data.get('workflow_id')}
                    }
                ],
                metadata={
                    "mode": request.mode,
                    "agent_workflow": True,
                    "workflow_id": workflow_data.get('workflow_id'),
                    "session_id": request.session_id,  # ⭐ 回传session，便于前端修正上下文
                    "total_tasks": total_tasks,
                    "task_counts": dict(task_counts),
                    "dag_structure": dag_nodes,
                    "dag_preview": dag_preview,
                    "execution_plan": execution_plan,
                    "execution_plan_summary": execution_summary
                }
            )
        else:
            # API调用失败的情况
            api_error = api_result.get("error", "未知错误") if api_result else "API调用失败"
            return ChatResponse(
                response=f"❌ 工作流程创建失败: {api_error}",
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


def _is_simple_greeting(message: str) -> bool:
    """快速识别简单问候语，避免过度分析"""
    message_lower = message.lower().strip()
    
    # 常见问候语模式
    simple_greetings = [
        "你好", "您好", "hi", "hello", "hey", "嗨",
        "你好呀", "您好呀", "hello there", "hi there",
        "好久不见", "最近怎么样", "怎么样", "在吗",
        "早上好", "下午好", "晚上好", "晚安",
        "good morning", "good afternoon", "good evening", "good night"
    ]
    
    # 简单感谢语
    simple_thanks = [
        "谢谢", "感谢", "thanks", "thank you", "thx",
        "多谢", "谢了", "非常感谢"
    ]
    
    # 简单确认语  
    simple_confirmations = [
        "好的", "好", "ok", "okay", "行", "可以",
        "明白了", "知道了", "了解", "收到"
    ]
    
    all_simple_phrases = simple_greetings + simple_thanks + simple_confirmations
    
    # 检查是否完全匹配或非常接近
    return any(phrase in message_lower for phrase in all_simple_phrases) and len(message) <= 15


def _get_simple_greeting_response(message: str) -> str:
    """为简单问候语生成快速响应"""
    message_lower = message.lower().strip()
    
    if any(greeting in message_lower for greeting in ["你好", "您好", "hi", "hello", "hey", "嗨"]):
        return "你好！我是AI任务编排助手，很高兴为您服务。有什么我可以帮助您的吗？"
    elif any(thanks in message_lower for thanks in ["谢谢", "感谢", "thanks", "thank you"]):
        return "不客气！随时为您服务。还有其他需要帮助的地方吗？"
    elif any(confirm in message_lower for confirm in ["好的", "好", "ok", "okay", "明白"]):
        return "好的，请告诉我下一步需要做什么，我会全力协助您。"
    elif "好久不见" in message_lower:
        return "确实好久不见！我一直在这里等待为您提供帮助。今天有什么任务需要处理吗？"
    else:
        return "我收到了您的消息。作为您的AI助手，我随时准备帮助您处理各种任务。请告诉我您需要什么？"
