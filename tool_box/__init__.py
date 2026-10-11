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
from .context import ToolContext
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


def _rebind_cached_submodules() -> None:
    """Re-attach every already-loaded submodule to this package object.

    ``from .x import y`` binds ``y``; it never binds the *submodule* ``x``.  A
    submodule attribute is set by the import system only at the moment that
    submodule is first loaded, and that moment is not necessarily this
    module's own init: ``app.services.terminal.session_manager`` imports
    ``tool_box.bio_tools.remote_executor`` at module scope, which pulls in
    ``tool_box`` -> ``integration`` -> ``tool_registry`` -> ``tools_impl``
    while ``session_manager`` itself is only half-initialized.  The
    ``tools_impl`` init then raises; CPython removes ``tool_box`` from
    ``sys.modules`` but keeps the submodules it had already loaded.  A later
    ``import tool_box`` builds a fresh package object, and for each cached
    submodule the import lines above bind only the imported names -- so
    ``tool_box.tools``, ``tool_box.context``, ``tool_box.cache`` and
    ``tool_box.client`` come out missing (measured 2026-10-11), while names
    bound by ``from .x import y`` (``ToolContext``, ``ToolRegistry``, ...)
    survive, which is why the loss is invisible to most callers.

    Rebinding from ``sys.modules`` makes the package carry the same attributes
    in either import order, without changing what the lines above import.
    """
    import sys

    package = sys.modules[__name__]
    prefix = __name__ + "."
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith(prefix):
            continue
        child = module_name[len(prefix) :]
        if "." in child:
            # Nested submodule (e.g. ``bio_tools.remote_executor``); its own parent binds it.
            continue
        setattr(package, child, module)


_rebind_cached_submodules()


__all__ = [
    # Core classes
    "ToolBoxMCPServer",
    "MCPToolBoxClient",
    "ToolRegistry",
    "ToolBoxIntegration",
    "ToolBoxLLMIntegration",
    "SmartToolRouter",
    "ToolContext",
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
