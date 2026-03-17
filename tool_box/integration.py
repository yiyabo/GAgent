"""
Tool Box Integration Module

This module provides integration utilities to connect the tool box
with existing LLM workflows and agent systems.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .call_utils import prepare_handler_kwargs
from .client import MCPToolBoxClient
from .tools import get_tool_registry
from .tool_registry import register_all_tools

logger = logging.getLogger(__name__)


class ToolBoxIntegration:
    """Integration layer for Tool Box"""

    def __init__(self):
        self.client = MCPToolBoxClient()
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the tool box integration"""
        if self._initialized:
            return

        # Register built-in tools
        await self._register_builtin_tools()

        # Start MCP server if configured
        await self._start_mcp_server()

        self._initialized = True
        logger.info("Tool Box integration initialized")

    async def _register_builtin_tools(self) -> None:
        """Register built-in tools from the declarative registry."""
        register_all_tools()

    async def _start_mcp_server(self) -> None:
        """Start MCP server if configured"""
        # This would be configured based on environment variables
        # For now, we'll use direct tool calls
        pass

    async def get_available_tools(self) -> List[Dict[str, Any]]:
        """Get list of available tools"""
        registry = get_tool_registry()
        tools = registry.list_tools()

        return [
            {
                "name": tool.name,
                "description": tool.description,
                "category": tool.category,
                "parameters": tool.parameters_schema,
                "tags": tool.tags,
                "examples": tool.examples,
            }
            for tool in tools
        ]

    async def call_tool(self, registered_tool_name: str, **kwargs) -> Any:
        """Call a tool by name
        
        Args:
            registered_tool_name: Name of the tool as registered in the registry
            **kwargs: Parameters to pass to the tool handler
        """
        registry = get_tool_registry()
        tool_def = registry.get_tool(registered_tool_name)

        if not tool_def:
            raise ValueError(f"Tool '{registered_tool_name}' not found")

        safe_kwargs = prepare_handler_kwargs(tool_def.handler, kwargs)
        return await tool_def.handler(**safe_kwargs)

    async def search_tools(self, query: str) -> List[Dict[str, Any]]:
        """Search for tools by query"""
        registry = get_tool_registry()
        tools = registry.search_tools(query)

        return [
            {"name": tool.name, "description": tool.description, "category": tool.category, "tags": tool.tags}
            for tool in tools
        ]

    async def get_tools_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Get tools by category"""
        registry = get_tool_registry()
        tools = registry.list_tools(category)

        return [{"name": tool.name, "description": tool.description, "tags": tool.tags} for tool in tools]


# Global integration instance
_toolbox_integration = ToolBoxIntegration()


async def get_toolbox_integration() -> ToolBoxIntegration:
    """Get the global toolbox integration instance"""
    if not _toolbox_integration._initialized:
        await _toolbox_integration.initialize()
    return _toolbox_integration


# Convenience functions for easy integration
async def initialize_toolbox() -> None:
    """Initialize the tool box"""
    integration = await get_toolbox_integration()
    await integration.initialize()


async def list_available_tools() -> List[Dict[str, Any]]:
    """List all available tools"""
    integration = await get_toolbox_integration()
    return await integration.get_available_tools()


async def execute_tool(registered_tool_name: str, **kwargs) -> Any:
    """Execute a tool with given parameters
    
    Args:
        registered_tool_name: Name of the tool as registered (e.g., 'bio_tools', 'vision_reader')
        **kwargs: Parameters to pass to the tool handler
    """
    integration = await get_toolbox_integration()
    return await integration.call_tool(registered_tool_name, **kwargs)


async def search_available_tools(query: str) -> List[Dict[str, Any]]:
    """Search for tools matching the query"""
    integration = await get_toolbox_integration()
    return await integration.search_tools(query)


# Integration with existing LLM workflow
class ToolBoxLLMIntegration:
    """Integration with LLM workflows"""

    def __init__(self):
        self.toolbox = None

    async def initialize(self) -> None:
        """Initialize the integration"""
        self.toolbox = await get_toolbox_integration()

    async def enhance_llm_prompt(self, user_prompt: str) -> str:
        """Enhance LLM prompt with tool information"""
        if not self.toolbox:
            await self.initialize()

        tools = await self.toolbox.get_available_tools()

        # Create tool descriptions for LLM
        tool_descriptions = []
        for tool in tools:
            desc = f"- {tool['name']}: {tool['description']}"
            if tool["examples"]:
                desc += f" (Examples: {', '.join(tool['examples'][:2])})"
            tool_descriptions.append(desc)

        enhanced_prompt = f"""
You are an intelligent assistant that can use the following tools to help complete tasks:

{chr(10).join(tool_descriptions)}

When you need to use a tool, please use the following format:
TOOL_CALL: {{"tool": "tool_name", "parameters": {{"param1": "value1"}}}}

User request: {user_prompt}

Please analyze the request and decide whether you need to use tools. If so, use the TOOL_CALL format to call the appropriate tool.
"""

        return enhanced_prompt

    async def process_llm_response(self, response: str) -> Dict[str, Any]:
        """Process LLM response and handle tool calls"""
        if "TOOL_CALL:" in response:
            try:
                # Extract tool call
                tool_call_start = response.find("TOOL_CALL:")
                tool_call_json = response[tool_call_start + 10 :].strip()

                # Parse tool call
                import json

                tool_call = json.loads(tool_call_json)

                tool_name = tool_call.get("tool")
                parameters = tool_call.get("parameters", {})

                # Execute tool
                result = await execute_tool(tool_name, **parameters)

                return {"type": "tool_result", "tool_name": tool_name, "result": result}

            except Exception as e:
                return {"type": "error", "error": f"Failed to process tool call: {e}"}

        return {"type": "text_response", "content": response}


# Global LLM integration instance
_llm_integration = ToolBoxLLMIntegration()


async def get_llm_integration() -> ToolBoxLLMIntegration:
    """Get the LLM integration instance"""
    if not _llm_integration.toolbox:
        await _llm_integration.initialize()
    return _llm_integration
