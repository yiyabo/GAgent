"""
Tool Box - MCP Server for AI Agent Tools

This module provides a Model Context Protocol (MCP) compatible server
that exposes various tools for AI agents to use.
"""

__version__ = "0.1.0"
__author__ = "AI Agent Team"

from .server import ToolBoxMCPServer
from .client import MCPToolBoxClient
from .tools import ToolRegistry
from .integration import (
    initialize_toolbox,
    list_available_tools,
    execute_tool,
    search_available_tools,
    get_toolbox_integration,
    ToolBoxIntegration,
    ToolBoxLLMIntegration
)
from .router import (
    route_user_request,
    get_smart_router,
    SmartToolRouter
)
from .cache import (
    get_memory_cache,
    get_persistent_cache,
    get_cache_stats,
    cleanup_all_caches
)

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
    "cleanup_all_caches"
]