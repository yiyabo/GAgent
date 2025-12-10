"""
Tool Box Integration Module

This module provides integration utilities to connect the tool box
with existing LLM workflows and agent systems.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .client import MCPToolBoxClient
from .tools import get_tool_registry, register_tool
from .tools_impl import (
    claude_code_tool,
    database_query_tool,
    file_operations_tool,
    generate_experiment_card_tool,
    graph_rag_tool,
    internal_api_tool,
    web_search_tool,
    document_reader_tool,
    vision_reader_tool,
    paper_replication_tool,
)

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
        """Register built-in tools"""
        # Register web search tool
        register_tool(
            name=web_search_tool["name"],
            description=web_search_tool["description"],
            category=web_search_tool["category"],
            parameters_schema=web_search_tool["parameters_schema"],
            handler=web_search_tool["handler"],
            tags=web_search_tool.get("tags", []),
            examples=web_search_tool.get("examples", []),
        )

        # Register file operations tool
        register_tool(
            name=file_operations_tool["name"],
            description=file_operations_tool["description"],
            category=file_operations_tool["category"],
            parameters_schema=file_operations_tool["parameters_schema"],
            handler=file_operations_tool["handler"],
            tags=file_operations_tool.get("tags", []),
            examples=file_operations_tool.get("examples", []),
        )

        # Register database query tool
        register_tool(
            name=database_query_tool["name"],
            description=database_query_tool["description"],
            category=database_query_tool["category"],
            parameters_schema=database_query_tool["parameters_schema"],
            handler=database_query_tool["handler"],
            tags=database_query_tool.get("tags", []),
            examples=database_query_tool.get("examples", []),
        )

        # Register internal API tool
        register_tool(
            name=internal_api_tool["name"],
            description=internal_api_tool["description"],
            category=internal_api_tool["category"],
            parameters_schema=internal_api_tool["parameters_schema"],
            handler=internal_api_tool["handler"],
            tags=internal_api_tool.get("tags", []),
            examples=internal_api_tool.get("examples", []),
        )

        # Register document reader tool
        register_tool(
            name=document_reader_tool["name"],
            description=document_reader_tool["description"],
            category=document_reader_tool["category"],
            parameters_schema=document_reader_tool["parameters_schema"],
            handler=document_reader_tool["handler"],
            tags=document_reader_tool.get("tags", []),
            examples=document_reader_tool.get("examples", []),
        )

        # Register vision reader tool (vision-based OCR/figure/equation reader)
        register_tool(
            name=vision_reader_tool["name"],
            description=vision_reader_tool["description"],
            category=vision_reader_tool["category"],
            parameters_schema=vision_reader_tool["parameters_schema"],
            handler=vision_reader_tool["handler"],
            tags=vision_reader_tool.get("tags", []),
            examples=vision_reader_tool.get("examples", []),
        )

        # Register paper replication tool (ExperimentCard loader for replication targets)
        register_tool(
            name=paper_replication_tool["name"],
            description=paper_replication_tool["description"],
            category=paper_replication_tool["category"],
            parameters_schema=paper_replication_tool["parameters_schema"],
            handler=paper_replication_tool["handler"],
            tags=paper_replication_tool.get("tags", []),
            examples=paper_replication_tool.get("examples", []),
        )

        # Register experiment card generator
        register_tool(
            name=generate_experiment_card_tool["name"],
            description=generate_experiment_card_tool["description"],
            category=generate_experiment_card_tool["category"],
            parameters_schema=generate_experiment_card_tool["parameters_schema"],
            handler=generate_experiment_card_tool["handler"],
            tags=generate_experiment_card_tool.get("tags", []),
            examples=generate_experiment_card_tool.get("examples", []),
        )

        register_tool(
            name=graph_rag_tool["name"],
            description=graph_rag_tool["description"],
            category=graph_rag_tool["category"],
            parameters_schema=graph_rag_tool["parameters_schema"],
            handler=graph_rag_tool["handler"],
            tags=graph_rag_tool.get("tags", []),
            examples=graph_rag_tool.get("examples", []),
        )

        register_tool(
            name=claude_code_tool["name"],
            description=claude_code_tool["description"],
            category="execution",
            parameters_schema=claude_code_tool["parameters"],
            handler=claude_code_tool["handler"],
            tags=["code", "execution", "claude", "local"],
            examples=[
                "Train a machine learning model on data/code_task/train.csv",
                "Analyze all files in data/code_task and provide a summary",
                "Write and execute a Python script to process CSV files",
            ],
        )

        logger.info("Built-in tools registered")

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

    async def call_tool(self, tool_name: str, **kwargs) -> Any:
        """Call a tool by name"""
        registry = get_tool_registry()
        tool_def = registry.get_tool(tool_name)

        if not tool_def:
            raise ValueError(f"Tool '{tool_name}' not found")

        return await tool_def.handler(**kwargs)

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


async def execute_tool(tool_name: str, **kwargs) -> Any:
    """Execute a tool with given parameters"""
    integration = await get_toolbox_integration()
    return await integration.call_tool(tool_name, **kwargs)


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
                desc += f" (示例: {', '.join(tool['examples'][:2])})"
            tool_descriptions.append(desc)

        enhanced_prompt = f"""
你是一个智能助手，可以使用以下工具来帮助完成任务：

{chr(10).join(tool_descriptions)}

当你需要使用工具时，请使用以下格式：
TOOL_CALL: {{"tool": "tool_name", "parameters": {{"param1": "value1"}}}}

用户请求: {user_prompt}

请分析请求并决定是否需要使用工具。如果需要，请使用TOOL_CALL格式调用相应工具。
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
