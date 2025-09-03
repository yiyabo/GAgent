"""
Tool Box - MCP Server for AI Agent Tools

This module provides a Model Context Protocol (MCP) compatible server
that exposes various tools for AI agents to use.
"""

__version__ = "0.1.0"
__author__ = "AI Agent Team"

from .cache import (
    cleanup_all_caches,
    get_cache_stats,
    get_memory_cache,
    get_persistent_cache,
)
from .client import MCPToolBoxClient
from .integration import (
    ToolBoxIntegration,
    ToolBoxLLMIntegration,
    execute_tool,
    get_toolbox_integration,
    initialize_toolbox,
    list_available_tools,
    search_available_tools,
)
from .router import SmartToolRouter, get_smart_router, route_user_request
from .server import ToolBoxMCPServer
from .tools import ToolRegistry

__all__ = [
    # Core classes
    "ToolBoxMCPServer",
    "MCPToolBoxClient",
    "ToolRegistry",
    "ToolBoxIntegration",
    "ToolBoxLLMIntegration",
    "SmartToolRouter",
    # Integration functions
    "initialize_toolbox",
    "list_available_tools",
    "execute_tool",
    "search_available_tools",
    "get_toolbox_integration",
    # Router functions
    "route_user_request",
    "get_smart_router",
    # Cache functions
    "get_memory_cache",
    "get_persistent_cache",
    "get_cache_stats",
    "cleanup_all_caches",
]
