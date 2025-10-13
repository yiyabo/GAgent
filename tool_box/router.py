"""
Smart Tool Router

This module provides intelligent routing capabilities to analyze user requests
and automatically select the most appropriate tools.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .integration import get_llm_integration
from .tools import get_tool_registry

logger = logging.getLogger(__name__)


class SmartToolRouter:
    """Intelligent router for tool selection"""

    def __init__(self):
        self.tool_registry = get_tool_registry()
        self.llm_integration = None
        # Use unified LLM client instead of direct API calls
        try:
            from app.services.foundation.settings import get_settings
            self.settings = get_settings()
        except Exception:
            self.settings = None

    async def initialize(self) -> None:
        """Initialize the router"""
        from .integration import get_llm_integration

        self.llm_integration = await get_llm_integration()

    async def _call_llm_api(self, prompt: str, max_retries: int = 3) -> str:
        """Call unified LLM API (supports GLM, QWEN, etc.) for intelligent routing"""
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # Use unified LLM client from app.llm
                from app.llm import get_default_client
                import asyncio
                
                client = get_default_client()
                provider = client.provider
                
                # Run sync client.chat in executor to avoid blocking
                loop = asyncio.get_event_loop()
                content = await loop.run_in_executor(None, client.chat, prompt)
                
                # Validate response quality
                if self._validate_api_response(content):
                    logger.info(f"✅ {provider.upper()} API成功 (尝试 {attempt + 1}/{max_retries})")
                    return content
                else:
                    logger.warning(f"⚠️ {provider.upper()} API响应质量不佳 (尝试 {attempt + 1})")
                    
            except asyncio.TimeoutError:
                logger.warning(f"⏱️ LLM API超时 (尝试 {attempt + 1}/{max_retries})")
                last_error = "Request timeout"
            except Exception as e:
                logger.error(f"LLM API调用失败 (尝试 {attempt + 1}): {e}")
                last_error = str(e)
                
                # Brief delay before retry
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)

        logger.error(f"❌ LLM API所有重试失败: {last_error}")
        return ""
        
    def _validate_api_response(self, content: str) -> bool:
        """Validate API response quality"""
        if not content or len(content.strip()) < 10:
            return False
            
        # Check for common error indicators
        error_indicators = ["error", "failed", "unable", "cannot", "sorry"]
        content_lower = content.lower()
        
        # If response is too short and contains error indicators, it's likely not useful
        if len(content) < 50 and any(indicator in content_lower for indicator in error_indicators):
            return False
            
        return True

    async def route_request(self, user_request: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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

        if not routing_result:
            logger.error("LLM routing returned no result")
            # 科研项目要求：即使失败也不放弃，尝试简化分析
            routing_result = await self._simplified_llm_routing(user_request, context)
            
        if not routing_result:
            raise ValueError("Complete LLM routing failure - all analysis methods exhausted")
            
        # 科研项目要求：接受更低的置信度，但记录详细信息
        confidence = routing_result.get("confidence", 0.0)
        if confidence < 0.1:
            logger.warning(f"⚠️ 极低置信度路由: {confidence}, 但科研项目要求继续处理")
            # 增强置信度评估
            routing_result = await self._enhance_confidence(routing_result, user_request)

        return {
            "request": user_request,
            "analysis": routing_result,
            "tool_calls": routing_result.get("tool_calls", []),
            "confidence": routing_result.get("confidence", 0.0),
            "routing_method": "pure_llm",
            "execution_plan": routing_result.get("execution_plan", ""),
            "estimated_time": routing_result.get("estimated_time", "unknown"),
        }

    async def _enhanced_llm_routing(self, request: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
                    "examples": tool.examples,
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
            llm_response = await self._call_llm_api(prompt)

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

    async def _simplified_llm_routing(self, request: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """简化的LLM路由 - 当标准路由失败时使用"""
        try:
            logger.info("🔄 启用简化LLM路由分析")
            
            tools = self.tool_registry.list_tools()
            tool_names = [tool.name for tool in tools]
            
            # 简化的提示，专注于工具选择
            prompt = f"""
用户请求: {request}

可用工具: {', '.join(tool_names)}

请简单分析用户意图并选择最合适的工具。返回JSON:
{{
    "intent": "用户意图简述",
    "tool_calls": [{{"tool_name": "选择的工具", "parameters": {{}}, "reasoning": "选择理由"}}],
    "confidence": 置信度(0-1)
}}

只返回JSON，不要其他内容。
"""
            
            llm_response = await self._call_llm_api(prompt)
            
            if not llm_response:
                return {"confidence": 0.0, "error": "Simplified LLM routing failed"}
                
            try:
                cleaned_response = llm_response.strip()
                if cleaned_response.startswith("```json"):
                    cleaned_response = cleaned_response[7:]
                if cleaned_response.endswith("```"):
                    cleaned_response = cleaned_response[:-3]
                cleaned_response = cleaned_response.strip()

                analysis = json.loads(cleaned_response)
                analysis["confidence"] = max(analysis.get("confidence", 0.0), 0.1)  # 最低保证置信度
                
                return analysis
                
            except json.JSONDecodeError as e:
                logger.error(f"简化路由JSON解析失败: {e}")
                return {"confidence": 0.0, "error": "JSON parse failed in simplified routing"}
                
        except Exception as e:
            logger.error(f"简化LLM路由失败: {e}")
            return {"confidence": 0.0, "error": str(e)}

    async def _enhance_confidence(self, routing_result: Dict[str, Any], user_request: str) -> Dict[str, Any]:
        """增强置信度评估"""
        try:
            logger.info("🔬 启用置信度增强分析")
            
            # 基于多个因素重新评估置信度
            confidence_factors = []
            
            # 因素1: 工具调用明确性
            tool_calls = routing_result.get("tool_calls", [])
            if tool_calls and len(tool_calls) > 0:
                confidence_factors.append(0.3)
                
            # 因素2: 意图描述详细程度
            intent = routing_result.get("intent", "")
            if intent and len(intent) > 20:
                confidence_factors.append(0.2)
                
            # 因素3: 执行计划存在性
            execution_plan = routing_result.get("execution_plan", "")
            if execution_plan:
                confidence_factors.append(0.2)
                
            # 因素4: 推理过程存在性
            reasoning = routing_result.get("reasoning", "")
            if reasoning and len(reasoning) > 30:
                confidence_factors.append(0.2)
                
            # 因素5: 用户请求复杂度适配
            request_complexity = len(user_request.split())
            if request_complexity <= 10:  # 简单请求更容易理解
                confidence_factors.append(0.1)
                
            # 计算增强后的置信度
            base_confidence = routing_result.get("confidence", 0.0)
            enhancement_boost = sum(confidence_factors)
            new_confidence = min(base_confidence + enhancement_boost, 0.95)
            
            routing_result["confidence"] = new_confidence
            routing_result["confidence_enhancement"] = {
                "original": base_confidence,
                "factors": confidence_factors,
                "enhanced": new_confidence
            }
            
            logger.info(f"🎯 置信度增强: {base_confidence:.2f} → {new_confidence:.2f}")
            
            return routing_result
            
        except Exception as e:
            logger.error(f"置信度增强失败: {e}")
            # 返回原始结果
            return routing_result


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
