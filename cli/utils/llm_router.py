#!/usr/bin/env python3
"""
基于LLM的智能工具路由器

参考Cursor、Claude Code、LangChain等先进AI系统的实现方式，
使用LLM本身来进行路由决策，而不是依赖简单的正则表达式匹配。
"""

import json
import logging
import time
from enum import Enum
from typing import Dict, List, Any, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class ToolCapability(Enum):
    """工具能力分类"""
    LOCAL_DATA = "local_data"          # 本地数据操作（待办、任务、文件等）
    REAL_TIME_INFO = "real_time_info"  # 实时信息查询（新闻、天气等）
    GENERAL_KNOWLEDGE = "general_knowledge"  # 通用知识问答
    COMPLEX_REASONING = "complex_reasoning"  # 复杂推理分析


class ExecutionEngine(Enum):
    """执行引擎类型"""
    GLM_TOOLS = "glm_tools"           # GLM + 工具调用
    PERPLEXITY = "perplexity"         # Perplexity直接对话


@dataclass
class RoutingDecision:
    """路由决策结果"""
    engine: ExecutionEngine
    confidence: float
    reasoning: str
    tool_suggestions: List[str]
    fallback_engine: Optional[ExecutionEngine] = None


class LLMBasedRouter:
    """基于LLM的智能路由器"""
    
    def __init__(self, api_client=None):
        self.api_client = api_client
        self._available_tools = [
            "add_todo", "list_todos", "complete_todo",
            "propose_plan", "decompose_task", "visualize_plan", 
            "execute_atomic_task", "web_search", "save_content_to_file"
        ]
        
    def analyze_intent_with_llm(self, user_query: str) -> RoutingDecision:
        """使用LLM分析用户意图并做出路由决策"""
        
        # 构建系统提示词，让LLM理解可用工具和路由逻辑
        system_prompt = self._build_routing_system_prompt()
        
        # 构建用户查询分析请求
        analysis_request = self._build_analysis_request(user_query)
        
        try:
            # 调用GLM进行路由决策
            response = self._call_routing_llm(system_prompt, analysis_request)
            
            # 解析LLM的路由决策
            decision = self._parse_routing_response(response)
            
            logger.info(f"LLM路由决策: {decision.engine.value}, 置信度: {decision.confidence:.2f}")
            return decision
            
        except Exception as e:
            logger.error(f"LLM路由决策失败: {e}")
            # 回退到保守策略
            return self._get_fallback_decision(user_query)
    
    def _build_routing_system_prompt(self) -> str:
        """构建路由系统提示词"""
        return f"""你是一个智能工具路由器，负责分析用户请求并决定使用哪种执行引擎。

可用的执行引擎:
1. GLM_TOOLS: 适用于需要调用本地工具的任务
   - 工具列表: {', '.join(self._available_tools)}
   - 优势: 可以操作本地数据、执行具体任务、管理待办事项等
   - 适用场景: 待办管理、任务执行、文件操作、计划制定等

2. PERPLEXITY: 适用于需要实时信息和复杂推理的任务
   - 优势: 实时联网搜索、最新信息、复杂知识问答
   - 适用场景: 新闻查询、知识解释、趋势分析、研究问题等

你的任务是分析用户请求，判断应该使用哪个引擎，并给出明确的推理过程。

输出格式必须是有效的JSON:
{{
    "engine": "GLM_TOOLS" 或 "PERPLEXITY",
    "confidence": 0.0-1.0的置信度分数,
    "reasoning": "详细的决策推理过程",
    "tool_suggestions": ["建议使用的具体工具列表"],
    "fallback_engine": "备选引擎(可选)"
}}

关键判断原则:
- 如果涉及"我的"、"本地"、"当前"数据操作 → GLM_TOOLS
- 如果需要实时、最新信息 → PERPLEXITY  
- 如果需要操作待办、任务、文件 → GLM_TOOLS
- 如果是纯知识问答且不涉及个人数据 → PERPLEXITY"""

    def _build_analysis_request(self, user_query: str) -> str:
        """构建分析请求"""
        return f"""请分析以下用户请求，决定应该使用哪个执行引擎:

用户请求: "{user_query}"

请仔细考虑:
1. 用户是在询问个人/本地数据还是通用信息？
2. 是否需要调用特定工具来完成任务？
3. 是否需要实时、最新的信息？
4. 任务的复杂程度如何？

请给出你的路由决策和详细推理。"""

    def _call_routing_llm(self, system_prompt: str, user_request: str) -> str:
        """调用LLM进行路由决策 - 直接调用GLM API"""
        import os
        import requests
        
        try:
            # 获取GLM配置
            from app.services.foundation.settings import get_settings
            settings = get_settings()
            
            api_key = settings.glm_api_key
            api_url = settings.glm_api_url
            
            if not api_key:
                raise ValueError("GLM API密钥未配置")
            
            # 构建请求数据
            payload = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_request}
                ],
                "model": "glm-4-flash",  # 使用轻量级模型进行快速路由决策
                "temperature": 0.1,      # 低温度确保一致性
                "max_tokens": 500        # 限制输出长度
            }
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            
            # 直接调用GLM API
            response = requests.post(api_url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            
            result = response.json()
            if "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]["message"]["content"]
            else:
                raise ValueError("LLM响应格式异常")
                
        except Exception as e:
            logger.error(f"调用路由LLM失败: {e}")
            raise
    
    def _parse_routing_response(self, llm_response: str) -> RoutingDecision:
        """解析LLM的路由响应"""
        try:
            # 尝试提取JSON部分
            response_text = llm_response.strip()
            
            # 如果响应包含```json代码块，提取其中的JSON
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
            
            # 解析JSON
            decision_data = json.loads(response_text)
            
            # 验证必要字段
            engine_str = decision_data.get("engine", "PERPLEXITY")
            confidence = float(decision_data.get("confidence", 0.5))
            reasoning = decision_data.get("reasoning", "LLM路由决策")
            tool_suggestions = decision_data.get("tool_suggestions", [])
            fallback_str = decision_data.get("fallback_engine")
            
            # 转换为枚举类型
            try:
                engine = ExecutionEngine(engine_str.lower())
            except ValueError:
                logger.warning(f"未知引擎类型: {engine_str}, 回退到PERPLEXITY")
                engine = ExecutionEngine.PERPLEXITY
            
            fallback_engine = None
            if fallback_str:
                try:
                    fallback_engine = ExecutionEngine(fallback_str.lower())
                except ValueError:
                    pass
            
            return RoutingDecision(
                engine=engine,
                confidence=max(0.0, min(1.0, confidence)),
                reasoning=reasoning,
                tool_suggestions=tool_suggestions if isinstance(tool_suggestions, list) else [],
                fallback_engine=fallback_engine
            )
            
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"解析LLM路由响应失败: {e}")
            logger.error(f"原始响应: {llm_response}")
            return self._get_fallback_decision("")
    
    async def _get_fallback_decision(self, user_query: str) -> RoutingDecision:
        """获取回退决策 - 科研项目纯LLM版本"""
        try:
            # 使用纯LLM分析作为回退，不使用关键词匹配
            from tool_box.router import get_smart_router
            router = await get_smart_router()
            
            prompt = f"""
作为最后的回退分析，请判断以下用户请求应该使用哪个执行引擎:

用户请求: {user_query}

执行引擎选项:
1. GLM_TOOLS - 本地任务管理、文件操作、数据查询
2. PERPLEXITY - 网络搜索、实时信息、外部知识

请返回JSON:
{{
    "engine": "GLM_TOOLS 或 PERPLEXITY",
    "confidence": 0.0-1.0,
    "reasoning": "选择理由"
}}
"""
            
            response = await router._call_glm_api(prompt)
            
            try:
                import json
                result = json.loads(response.strip())
                
                engine = ExecutionEngine.GLM_TOOLS if result.get("engine") == "GLM_TOOLS" else ExecutionEngine.PERPLEXITY
                
                return RoutingDecision(
                    engine=engine,
                    confidence=max(0.1, result.get("confidence", 0.3)),
                    reasoning=f"LLM回退分析: {result.get('reasoning', '智能分析结果')}",
                    tool_suggestions=[],
                    fallback_engine=ExecutionEngine.PERPLEXITY if engine == ExecutionEngine.GLM_TOOLS else ExecutionEngine.GLM_TOOLS
                )
                
            except json.JSONDecodeError:
                # 如果JSON解析失败，使用默认的智能选择
                pass
                
        except Exception as e:
            logger.error(f"LLM回退分析失败: {e}")
            
        # 最终兜底：基于请求长度的智能判断（仍然是启发式，但不是关键词匹配）
        if len(user_query) > 20:  # 较长的请求可能需要搜索
            return RoutingDecision(
                engine=ExecutionEngine.PERPLEXITY,
                confidence=0.4,
                reasoning="智能回退: 复杂请求推荐搜索引擎",
                tool_suggestions=[],
                fallback_engine=ExecutionEngine.GLM_TOOLS
            )
        else:
            return RoutingDecision(
                engine=ExecutionEngine.PERPLEXITY,
                confidence=0.7,
                reasoning="回退策略: 默认使用Perplexity处理通用查询",
                tool_suggestions=[],
                fallback_engine=ExecutionEngine.GLM_TOOLS
            )


# 单例模式
_llm_router_instance = None

def get_llm_router(api_client=None) -> LLMBasedRouter:
    """获取LLM路由器实例"""
    global _llm_router_instance
    if _llm_router_instance is None:
        _llm_router_instance = LLMBasedRouter(api_client)
    elif api_client is not None:
        _llm_router_instance.api_client = api_client
    return _llm_router_instance


# 便捷函数
def route_user_query(user_query: str, api_client=None) -> RoutingDecision:
    """便捷的路由查询函数"""
    router = get_llm_router(api_client)
    return router.analyze_intent_with_llm(user_query)
