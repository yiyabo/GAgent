"""
Smart Tool Router

This module provides intelligent routing capabilities to analyze user requests
and automatically select the most appropriate tools.
"""

import asyncio
import logging
import re
import os
import json
from typing import Any, Dict, List, Optional, Tuple

from .tools import get_tool_registry
from .integration import get_llm_integration

logger = logging.getLogger(__name__)


class SmartToolRouter:
    """Intelligent router for tool selection"""

    def __init__(self):
        self.tool_registry = get_tool_registry()
        self.llm_integration = None
        self.glm_api_key = "f887acb2128f41988821c38ee395f542.rmgIq0MwACMMh0Mw"  # GLM API Key

    async def initialize(self) -> None:
        """Initialize the router"""
        from .integration import get_llm_integration
        self.llm_integration = await get_llm_integration()

    async def _call_glm_api(self, prompt: str) -> str:
        """Call GLM API for intelligent routing"""
        try:
            import aiohttp

            url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.glm_api_key}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": "glm-4",
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.1,  # 降低随机性，提高确定性
                "max_tokens": 1000
            }

            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data["choices"][0]["message"]["content"]
                    else:
                        logger.error(f"GLM API error: HTTP {response.status}")
                        return ""

        except Exception as e:
            logger.error(f"GLM API call failed: {e}")
            return ""

    async def route_request(self, user_request: str,
                           context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Route user request using pure LLM intelligence

        Args:
            user_request: User's natural language request
            context: Additional context information

        Returns:
            Dict containing routing decision and tool calls
        """
        if not self.llm_integration:
            await self.initialize()

        logger.info("Using pure LLM routing for maximum intelligence")
        
        # Use enhanced LLM analysis for everything
        routing_result = await self._enhanced_llm_routing(user_request, context)
        
        if not routing_result or routing_result.get("confidence", 0.0) < 0.3:
            logger.error("LLM routing failed")
            raise ValueError("Unable to analyze request - insufficient confidence")

        return {
            "request": user_request,
            "analysis": routing_result,
            "tool_calls": routing_result.get("tool_calls", []),
            "confidence": routing_result.get("confidence", 0.0),
            "routing_method": "pure_llm",
            "execution_plan": routing_result.get("execution_plan", ""),
            "estimated_time": routing_result.get("estimated_time", "unknown")
        }

    async def _enhanced_llm_routing(self, request: str,
                                   context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Enhanced LLM-based routing with complete tool call generation"""
        try:
            # Get available tools with detailed information
            tools = self.tool_registry.list_tools()
            tool_details = []
            
            for tool in tools:
                tool_info = {
                    "name": tool.name,
                    "description": tool.description,
                    "category": tool.category,
                    "parameters": tool.parameters_schema,
                    "examples": tool.examples
                }
                tool_details.append(tool_info)

            # Build comprehensive LLM prompt
            context_str = ""
            if context:
                context_str = f"\n上下文信息:\n{json.dumps(context, ensure_ascii=False, indent=2)}"

            prompt = f"""
你是一个高级AI工具路由器，专门为智能agent系统设计。你需要分析用户请求并生成完整的工具执行计划。

可用工具详细信息:
{json.dumps(tool_details, ensure_ascii=False, indent=2)}

用户请求: {request}{context_str}

请进行深度分析并返回完整的路由决策。注意:
1. 仔细分析用户的真实意图
2. 选择最合适的工具组合
3. 为每个工具提取准确的参数
4. 考虑工具执行的先后顺序
5. 如果需要多个工具协作，请规划好依赖关系

返回JSON格式:
{{
    "intent": "详细的用户意图分析",
    "complexity": "simple|medium|complex",
    "tool_calls": [
        {{
            "tool_name": "具体工具名",
            "parameters": {{"参数名": "参数值"}},
            "reasoning": "选择此工具和参数的详细理由",
            "execution_order": 1
        }}
    ],
    "execution_plan": "整体执行计划描述",
    "estimated_time": "预估执行时间",
    "confidence": 0.0到1.0之间的置信度,
    "reasoning": "完整的分析推理过程"
}}

只返回JSON，不要其他内容。确保参数完整且符合工具的schema要求。
"""

            # Call GLM API
            llm_response = await self._call_glm_api(prompt)

            if not llm_response:
                return {"confidence": 0.0, "error": "LLM call failed"}

            # Parse LLM response
            try:
                # Clean response
                cleaned_response = llm_response.strip()
                if cleaned_response.startswith("```json"):
                    cleaned_response = cleaned_response[7:]
                if cleaned_response.endswith("```"):
                    cleaned_response = cleaned_response[:-3]
                cleaned_response = cleaned_response.strip()

                analysis = json.loads(cleaned_response)
                
                # Validate and normalize confidence
                analysis["confidence"] = min(max(analysis.get("confidence", 0.0), 0.0), 1.0)
                
                # Ensure tool_calls exist and are valid
                if "tool_calls" not in analysis:
                    analysis["tool_calls"] = []
                
                # Sort tool calls by execution order if specified
                if analysis["tool_calls"]:
                    analysis["tool_calls"].sort(key=lambda x: x.get("execution_order", 999))
                
                return analysis
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM response: {e}")
                logger.error(f"Original LLM response: {llm_response}")
                return {"confidence": 0.0, "error": "JSON parse failed"}

        except Exception as e:
            logger.error(f"Enhanced LLM routing failed: {e}")
            return {"confidence": 0.0, "error": str(e)}



# Global router instance
_smart_router = SmartToolRouter()


async def get_smart_router() -> SmartToolRouter:
    """Get the global smart router instance"""
    if not _smart_router.llm_integration:
        await _smart_router.initialize()
    return _smart_router


async def route_user_request(request: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Convenience function to route user requests"""
    router = await get_smart_router()
    return await router.route_request(request, context)

