"""
Tool Registry and Management

This module provides the tool registry system for managing
and discovering available tools in the MCP server.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """Tool definition with metadata"""

    name: str
    description: str
    category: str
    parameters_schema: Dict[str, Any]
    handler: Callable
    version: str = "1.0.0"
    author: str = "ToolBox"
    tags: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)


class ToolRegistry:
    """Registry for managing tools"""

    def __init__(self):
        self.tools: Dict[str, ToolDefinition] = {}
        self.categories: Dict[str, List[str]] = {}

    def register_tool(self, tool_def: ToolDefinition) -> None:
        """Register a new tool"""
        if tool_def.name in self.tools:
            logger.warning(f"Tool {tool_def.name} already registered, overwriting")

        self.tools[tool_def.name] = tool_def

        # Update category index
        if tool_def.category not in self.categories:
            self.categories[tool_def.category] = []
        if tool_def.name not in self.categories[tool_def.category]:
            self.categories[tool_def.category].append(tool_def.name)

        logger.info(f"Registered tool: {tool_def.name} (category: {tool_def.category})")

    def unregister_tool(self, tool_name: str) -> bool:
        """Unregister a tool"""
        if tool_name not in self.tools:
            logger.warning(f"Tool {tool_name} not found")
            return False

        tool_def = self.tools[tool_name]
        del self.tools[tool_name]

        # Update category index
        if tool_def.category in self.categories:
            if tool_name in self.categories[tool_def.category]:
                self.categories[tool_def.category].remove(tool_name)
            if not self.categories[tool_def.category]:
                del self.categories[tool_def.category]

        logger.info(f"Unregistered tool: {tool_name}")
        return True

    def get_tool(self, tool_name: str) -> Optional[ToolDefinition]:
        """Get a tool by name"""
        return self.tools.get(tool_name)

    def list_tools(self, category: Optional[str] = None) -> List[ToolDefinition]:
        """List all tools or tools in a specific category"""
        if category:
            tool_names = self.categories.get(category, [])
            return [self.tools[name] for name in tool_names if name in self.tools]
        else:
            return list(self.tools.values())

    def list_categories(self) -> List[str]:
        """List all categories"""
        return list(self.categories.keys())

    def search_tools(self, query: str) -> List[ToolDefinition]:
        """Search tools by name, description, or tags"""
        query_lower = query.lower()
        results = []

        for tool in self.tools.values():
            if (
                query_lower in tool.name.lower()
                or query_lower in tool.description.lower()
                or any(query_lower in tag.lower() for tag in tool.tags)
            ):
                results.append(tool)

        return results

    def get_tool_names(self) -> List[str]:
        """Get list of all tool names"""
        return list(self.tools.keys())

    def get_tools_by_tag(self, tag: str) -> List[ToolDefinition]:
        """Get tools by tag"""
        return [tool for tool in self.tools.values() if tag in tool.tags]

    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a tool"""
        tool = self.get_tool(tool_name)
        if not tool:
            return None

        return {
            "name": tool.name,
            "description": tool.description,
            "category": tool.category,
            "version": tool.version,
            "author": tool.author,
            "tags": tool.tags,
            "examples": tool.examples,
            "parameters_schema": tool.parameters_schema,
        }


# Global tool registry instance
_tool_registry = ToolRegistry()


def get_tool_registry() -> ToolRegistry:
    """Get the global tool registry"""
    return _tool_registry


def register_tool(
    name: str,
    description: str,
    category: str,
    parameters_schema: Dict[str, Any],
    handler: Callable,
    version: str = "1.0.0",
    author: str = "ToolBox",
    tags: Optional[List[str]] = None,
    examples: Optional[List[str]] = None,
) -> None:
    """Convenience function to register a tool"""
    tool_def = ToolDefinition(
        name=name,
        description=description,
        category=category,
        parameters_schema=parameters_schema,
        handler=handler,
        version=version,
        author=author,
        tags=tags or [],
        examples=examples or [],
    )

    _tool_registry.register_tool(tool_def)


def unregister_tool(tool_name: str) -> bool:
    """Convenience function to unregister a tool"""
    return _tool_registry.unregister_tool(tool_name)


def get_registered_tools() -> List[ToolDefinition]:
    """Get all registered tools"""
    return _tool_registry.list_tools()


def find_tools_by_category(category: str) -> List[ToolDefinition]:
    """Find tools by category"""
    return _tool_registry.list_tools(category)


def search_registered_tools(query: str) -> List[ToolDefinition]:
    """Search registered tools"""
    return _tool_registry.search_tools(query)
