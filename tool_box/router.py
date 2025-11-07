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
                    logger.info(f"✅ {provider.upper()} API succeeded (attempt {attempt + 1}/{max_retries})")
                    return content
                else:
                    logger.warning(f"⚠️ {provider.upper()} API returned low quality data (attempt {attempt + 1})")
                    
            except asyncio.TimeoutError:
                logger.warning(f"⏱️ LLM API timeout (attempt {attempt + 1}/{max_retries})")
                last_error = "Request timeout"
            except Exception as e:
                logger.error(f"LLM API call failed (attempt {attempt + 1}): {e}")
                last_error = str(e)
                
                # Brief delay before retry
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)

        logger.error(f"❌ All LLM API retries failed: {last_error}")
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
            # Research requirement: never give up—fall back to a simplified analysis
            routing_result = await self._simplified_llm_routing(user_request, context)
            
        if not routing_result:
            raise ValueError("Complete LLM routing failure - all analysis methods exhausted")
            
        # Research requirement: accept lower confidence but record detailed context
        confidence = routing_result.get("confidence", 0.0)
        if confidence < 0.1:
            logger.warning(f"⚠️ Very low confidence routing: {confidence}, but project requirements say to continue")
            # Confidence enhancement analysis
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
                context_str = f"\nContext:\n{json.dumps(context, ensure_ascii=False, indent=2)}"

            prompt = f"""
You are an advanced AI tool router for an intelligent agent. Analyse the user request and produce a complete tool execution plan.

Available tools:
{json.dumps(tool_details, ensure_ascii=False, indent=2)}

User request: {request}{context_str}

Perform a thorough analysis and return your routing decision. Follow these guidelines:
1. Identify the user's true intent.
2. Choose the most appropriate tool or tool combination.
3. Derive precise parameters for each tool call.
4. Consider the order in which tools should execute.
5. When multiple tools cooperate, describe dependencies clearly.

Return JSON only:
{{
    "intent": "Detailed analysis of user intent",
    "complexity": "simple|medium|complex",
    "tool_calls": [
        {{
            "tool_name": "specific tool name",
            "parameters": {{"parameter name": "parameter value"}},
            "reasoning": "Detailed reasoning for choosing this tool and parameters",
            "execution_order": 1
        }}
    ],
    "execution_plan": "Overall execution plan description",
    "estimated_time": "estimated execution time",
    "confidence": <float between 0 and 1>,
    "reasoning": "Comprehensive reasoning process"
}}

Return JSON only—no additional commentary. Ensure parameters are complete and comply with each tool's schema.
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
        """Simplified LLM routing fallback when the primary flow fails"""
        try:
            logger.info("🔄 Running simplified LLM routing analysis")
            
            tools = self.tool_registry.list_tools()
            tool_names = [tool.name for tool in tools]
            
            # Minimal prompt focused on tool selection
            prompt = f"""
User request: {request}

Available tools: {', '.join(tool_names)}

Briefly analyse the request and choose the best tool. Return JSON:
{{
    "intent": "Brief user intent summary",
    "tool_calls": [{{"tool_name": "selected tool", "parameters": {{}}, "reasoning": "selection reasoning"}}],
    "confidence": <float between 0 and 1>
}}

Return JSON only.
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
                analysis["confidence"] = max(analysis.get("confidence", 0.0), 0.1)  # enforce a minimum confidence floor
                
                return analysis
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON from simplified routing: {e}")
                return {"confidence": 0.0, "error": "JSON parse failed in simplified routing"}
                
        except Exception as e:
            logger.error(f"Simplified LLM routing failed: {e}")
            return {"confidence": 0.0, "error": str(e)}

    async def _enhance_confidence(self, routing_result: Dict[str, Any], user_request: str) -> Dict[str, Any]:
        """Confidence enhancement analysis"""
        try:
            logger.info("🔬 Running confidence enhancement analysis")
            
            # Re-evaluate confidence using multiple factors
            confidence_factors = []

            # Factor 1: clarity of tool calls
            tool_calls = routing_result.get("tool_calls", [])
            if tool_calls and len(tool_calls) > 0:
                confidence_factors.append(0.3)

            # Factor 2: richness of the intent description
            intent = routing_result.get("intent", "")
            if intent and len(intent) > 20:
                confidence_factors.append(0.2)

            # Factor 3: presence of an execution plan
            execution_plan = routing_result.get("execution_plan", "")
            if execution_plan:
                confidence_factors.append(0.2)

            # Factor 4: presence of detailed reasoning
            reasoning = routing_result.get("reasoning", "")
            if reasoning and len(reasoning) > 30:
                confidence_factors.append(0.2)

            # Factor 5: match between request complexity and response
            request_complexity = len(user_request.split())
            if request_complexity <= 10:  # simpler requests are easier to interpret
                confidence_factors.append(0.1)

            # Compute the enhanced confidence
            base_confidence = routing_result.get("confidence", 0.0)
            enhancement_boost = sum(confidence_factors)
            new_confidence = min(base_confidence + enhancement_boost, 0.95)
            
            routing_result["confidence"] = new_confidence
            routing_result["confidence_enhancement"] = {
                "original": base_confidence,
                "factors": confidence_factors,
                "enhanced": new_confidence
            }
            
            logger.info(f"🎯 Confidence adjusted: {base_confidence:.2f} → {new_confidence:.2f}")
            
            return routing_result
            
        except Exception as e:
            logger.error(f"Confidence enhancement failed: {e}")
            # Fall back to the original result
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
