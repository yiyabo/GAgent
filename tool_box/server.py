"""
MCP Server implementation for Tool Box

This module implements a Model Context Protocol (MCP) compatible server
that provides tools for AI agents.
"""

import asyncio
import json
import logging
import sys
from typing import Any, Dict, List, Optional

from .tools import ToolDefinition, get_tool_registry

logger = logging.getLogger(__name__)


class ToolBoxMCPServer:
    """MCP Server for Tool Box"""

    def __init__(self):
        self.tool_registry = get_tool_registry()
        self.resources: Dict[str, Dict[str, Any]] = {}
        self.running = False

    def register_resource(self, uri: str, name: str, description: str, content: Any = None) -> None:
        """Register a resource"""
        self.resources[uri] = {"name": name, "description": description, "content": content}
        logger.info(f"Registered resource: {uri}")

    async def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle incoming MCP requests"""
        try:
            method = request.get("method")
            params = request.get("params", {})

            if method == "tools/list":
                return await self._handle_list_tools()
            elif method == "tools/call":
                return await self._handle_call_tool(params)
            elif method == "resources/list":
                return await self._handle_list_resources()
            elif method == "resources/read":
                return await self._handle_read_resource(params)
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }

        except Exception as e:
            logger.error(f"Error handling request: {e}")
            return {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32603, "message": str(e)}}

    async def _handle_list_tools(self) -> Dict[str, Any]:
        """Handle tools/list request"""
        tools_list = []
        for tool in self.tool_registry.list_tools():
            tools_list.append(
                {"name": tool.name, "description": tool.description, "inputSchema": tool.parameters_schema}
            )

        return {"jsonrpc": "2.0", "result": {"tools": tools_list}}

    async def _handle_call_tool(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools/call request"""
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})

        tool_def = self.tool_registry.get_tool(tool_name)
        if not tool_def:
            return {"jsonrpc": "2.0", "error": {"code": -32602, "message": f"Tool not found: {tool_name}"}}

        try:
            result = await tool_def.handler(**tool_args)

            return {"jsonrpc": "2.0", "result": result}

        except Exception as e:
            logger.error(f"Error calling tool {tool_name}: {e}")
            return {"jsonrpc": "2.0", "error": {"code": -32603, "message": f"Tool execution failed: {str(e)}"}}

    async def _handle_list_resources(self) -> Dict[str, Any]:
        """Handle resources/list request"""
        resources_list = []
        for uri, resource in self.resources.items():
            resources_list.append({"uri": uri, "name": resource["name"], "description": resource["description"]})

        return {"jsonrpc": "2.0", "result": {"resources": resources_list}}

    async def _handle_read_resource(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle resources/read request"""
        uri = params.get("uri")

        if uri not in self.resources:
            return {"jsonrpc": "2.0", "error": {"code": -32602, "message": f"Resource not found: {uri}"}}

        resource = self.resources[uri]
        return {
            "jsonrpc": "2.0",
            "result": {"contents": [{"uri": uri, "mimeType": "text/plain", "text": str(resource["content"])}]},
        }

    async def run_stdio(self) -> None:
        """Run the server using stdio transport"""
        self.running = True
        logger.info("Starting MCP server with stdio transport")

        try:
            while self.running:
                # Read request from stdin
                line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)

                if not line:
                    break

                try:
                    request = json.loads(line.strip())
                    response = await self.handle_request(request)

                    # Write response to stdout
                    response_json = json.dumps(response, ensure_ascii=False)
                    print(response_json, flush=True)

                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON received: {e}")
                    error_response = {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}
                    print(json.dumps(error_response), flush=True)

        except KeyboardInterrupt:
            logger.info("Server shutdown requested")
        finally:
            self.running = False
            logger.info("MCP server stopped")

    def stop(self) -> None:
        """Stop the server"""
        self.running = False
        logger.info("Server stop requested")
