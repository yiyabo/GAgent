#!/usr/bin/env python3
"""
统一智能路由器 - 重新设计的架构

核心理念：
- 只使用GLM作为主LLM（支持工具调用）
- Perplexity作为web_search工具的实现（不再是独立引擎）
- 智能决策需要调用哪些工具来完成任务
- 提供思考过程的实时反馈
"""

import json
import logging
import time
from enum import Enum
from typing import Dict, List, Any, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class ToolType(Enum):
    """工具类型分类"""
    LOCAL_DATA = "local_data"          # 本地数据操作
    WEB_SEARCH = "web_search"          # 网络搜索
    FILE_OPERATION = "file_operation"  # 文件操作
    TASK_MANAGEMENT = "task_management" # 任务管理
    PLAN_CREATION = "plan_creation"    # 计划制定


@dataclass
class UnifiedRoutingDecision:
    """统一路由决策结果"""
    recommended_tools: List[str]       # 推荐的工具列表
    execution_strategy: str            # 执行策略描述
    confidence: float                  # 置信度
    reasoning: str                     # 详细推理过程
    use_web_search: bool = False       # 是否需要网络搜索


class UnifiedIntelligentRouter:
    """统一智能路由器 - 只使用GLM+工具的架构"""
    
    def __init__(self, api_client=None, console=None):
        self.api_client = api_client
        self.console = console
        self._available_tools = [
            "add_todo", "list_todos", "complete_todo",
            "propose_plan", "decompose_task", "visualize_plan", 
            "execute_atomic_task", "web_search", "save_content_to_file"
        ]
        
    def analyze_intent_with_thinking(self, user_query: str) -> UnifiedRoutingDecision:
        """分析用户意图并提供思考过程反馈"""
        
        # 显示思考过程
        if self.console:
            self.console.print("🤔 [dim]正在分析用户意图...[/dim]")
            
        try:
            # 构建更精准的系统提示词
            system_prompt = self._build_unified_system_prompt()
            analysis_request = self._build_unified_analysis_request(user_query)
            
            if self.console:
                self.console.print("🧠 [dim]LLM正在思考最佳执行策略...[/dim]")
            
            # 调用GLM进行工具选择决策
            response = self._call_routing_llm(system_prompt, analysis_request)
            
            if self.console:
                self.console.print("🎯 [dim]分析完成，准备执行...[/dim]")
            
            # 解析决策结果
            decision = self._parse_unified_response(response)
            
            logger.info(f"统一路由决策: 工具={decision.recommended_tools}, 置信度={decision.confidence:.2f}")
            return decision
            
        except Exception as e:
            logger.error(f"统一路由决策失败: {e}")
            return self._get_fallback_decision(user_query)
    
    def _build_unified_system_prompt(self) -> str:
        """构建统一的系统提示词"""
        return f"""你是一个智能工具选择助手，负责分析用户请求并推荐最合适的工具组合。

🎯 核心原则：
- 所有任务都通过GLM+工具来完成
- 网络搜索使用web_search工具（基于Perplexity API）
- 可以推荐多个工具的组合使用
- 优先考虑工具的协同效果

🛠️ 可用工具列表：
{', '.join(self._available_tools)}

🔍 工具详细说明：
- add_todo: 添加待办事项
- list_todos: 查看待办事项列表
- complete_todo: 完成待办事项
- propose_plan: 制定计划
- decompose_task: 分解任务
- visualize_plan: 可视化计划
- execute_atomic_task: 执行原子任务
- web_search: 网络搜索（使用Perplexity API获取实时信息）
- save_content_to_file: 保存内容到文件

📝 输出格式（必须是有效JSON）：
{{
    "recommended_tools": ["工具1", "工具2", ...],
    "execution_strategy": "详细的执行策略描述",
    "confidence": 0.0-1.0的置信度分数,
    "reasoning": "详细的分析推理过程",
    "use_web_search": true/false
}}

🎯 分析重点：
1. 是否需要实时信息？→ 包含web_search
2. 是否需要保存内容？→ 包含save_content_to_file  
3. 是否涉及任务管理？→ 包含相关todo工具
4. 是否需要制定计划？→ 包含plan相关工具
5. 复杂任务可能需要多个工具协同完成"""

    def _build_unified_analysis_request(self, user_query: str) -> str:
        """构建统一的分析请求"""
        return f"""请分析以下用户请求，推荐最合适的工具组合：

用户请求: "{user_query}"

请考虑：
1. 这个任务需要哪些步骤？
2. 每个步骤需要什么工具？
3. 是否需要先获取信息再进行操作？
4. 工具之间的执行顺序如何？
5. 如何确保任务完整完成？

请给出详细的工具推荐和执行策略。"""

    def _call_routing_llm(self, system_prompt: str, user_request: str) -> str:
        """调用GLM进行路由决策"""
        import os
        import requests
        
        try:
            from app.services.foundation.settings import get_settings
            settings = get_settings()
            
            api_key = settings.glm_api_key
            api_url = settings.glm_api_url
            
            if not api_key:
                raise ValueError("GLM API密钥未配置")
            
            payload = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_request}
                ],
                "model": "glm-4-flash",
                "temperature": 0.2,      # 稍高一点的创意性
                "max_tokens": 800        # 更多空间描述执行策略
            }
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            
            response = requests.post(api_url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            if "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]["message"]["content"]
            else:
                raise ValueError("LLM响应格式异常")
                
        except Exception as e:
            logger.error(f"调用统一路由LLM失败: {e}")
            raise
    
    def _parse_unified_response(self, llm_response: str) -> UnifiedRoutingDecision:
        """解析统一路由响应"""
        try:
            # 提取JSON部分
            response_text = llm_response.strip()
            
            if "```json" in response_text:
                start = response_text.find("```json") + 7
                end = response_text.find("```", start)
                if end != -1:
                    response_text = response_text[start:end].strip()
            elif "```" in response_text:
                start = response_text.find("```") + 3
                end = response_text.find("```", start)
                if end != -1:
                    response_text = response_text[start:end].strip()
            
            decision_data = json.loads(response_text)
            
            recommended_tools = decision_data.get("recommended_tools", [])
            execution_strategy = decision_data.get("execution_strategy", "使用推荐工具完成任务")
            confidence = float(decision_data.get("confidence", 0.7))
            reasoning = decision_data.get("reasoning", "统一路由决策")
            use_web_search = decision_data.get("use_web_search", False)
            
            # 验证工具列表
            if not isinstance(recommended_tools, list):
                recommended_tools = []
            
            # 过滤无效工具
            valid_tools = [tool for tool in recommended_tools if tool in self._available_tools]
            
            return UnifiedRoutingDecision(
                recommended_tools=valid_tools,
                execution_strategy=execution_strategy,
                confidence=max(0.0, min(1.0, confidence)),
                reasoning=reasoning,
                use_web_search=use_web_search
            )
            
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"解析统一路由响应失败: {e}")
            logger.error(f"原始响应: {llm_response}")
            return self._get_fallback_decision("")
    
    def _get_fallback_decision(self, user_query: str) -> UnifiedRoutingDecision:
        """获取回退决策"""
        # 简单的关键词分析作为回退
        query_lower = user_query.lower()
        
        recommended_tools = []
        use_web_search = False
        
        # 检测常见模式
        if any(keyword in query_lower for keyword in ["搜索", "查询", "新闻", "最新", "实时"]):
            recommended_tools.append("web_search")
            use_web_search = True
            
        if any(keyword in query_lower for keyword in ["保存", "写入", "文件", "文档"]):
            recommended_tools.append("save_content_to_file")
            
        if any(keyword in query_lower for keyword in ["待办", "任务", "todo"]):
            recommended_tools.extend(["list_todos", "add_todo"])
            
        if any(keyword in query_lower for keyword in ["计划", "规划", "plan"]):
            recommended_tools.append("propose_plan")
        
        # 如果没有匹配到任何工具，提供默认建议
        if not recommended_tools:
            if any(keyword in query_lower for keyword in ["什么", "如何", "为什么", "解释"]):
                recommended_tools.append("web_search")
                use_web_search = True
            else:
                recommended_tools.append("web_search")
                use_web_search = True
        
        return UnifiedRoutingDecision(
            recommended_tools=recommended_tools,
            execution_strategy="回退策略：基于关键词匹配的工具推荐",
            confidence=0.6,
            reasoning="LLM路由失败，使用回退的关键词匹配策略",
            use_web_search=use_web_search
        )


# 单例模式
_unified_router_instance = None

def get_unified_router(api_client=None, console=None) -> UnifiedIntelligentRouter:
    """获取统一路由器实例"""
    global _unified_router_instance
    if _unified_router_instance is None:
        _unified_router_instance = UnifiedIntelligentRouter(api_client, console)
    else:
        if api_client is not None:
            _unified_router_instance.api_client = api_client
        if console is not None:
            _unified_router_instance.console = console
    return _unified_router_instance


# 便捷函数
def route_user_query_unified(user_query: str, api_client=None, console=None) -> UnifiedRoutingDecision:
    """便捷的统一路由查询函数"""
    router = get_unified_router(api_client, console)
    return router.analyze_intent_with_thinking(user_query)
