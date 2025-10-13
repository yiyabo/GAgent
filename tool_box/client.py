"""
MCP Client for Tool Box

This module provides a client for interacting with MCP servers
and calling tools.
"""

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class MCPToolBoxClient:
    """Client for interacting with MCP servers"""

    def __init__(self):
        self.servers: Dict[str, Dict[str, Any]] = {}
        self.processes: Dict[str, subprocess.Popen] = {}

    def register_server(self, name: str, command: Union[str, List[str]], cwd: Optional[str] = None) -> None:
        """Register an MCP server"""
        self.servers[name] = {"command": command, "cwd": cwd or str(Path.cwd()), "status": "stopped"}
        logger.info(f"Registered MCP server: {name}")

    async def start_server(self, name: str) -> bool:
        """Start an MCP server"""
        if name not in self.servers:
            logger.error(f"Server {name} not registered")
            return False

        if name in self.processes:
            logger.warning(f"Server {name} already running")
            return True

        server_config = self.servers[name]

        try:
            # Start the server process
            if isinstance(server_config["command"], str):
                process = subprocess.Popen(
                    server_config["command"],
                    shell=True,
                    cwd=server_config["cwd"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            else:
                process = subprocess.Popen(
                    server_config["command"],
                    cwd=server_config["cwd"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

            self.processes[name] = process
            self.servers[name]["status"] = "running"

            logger.info(f"Started MCP server: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to start server {name}: {e}")
            return False

    async def stop_server(self, name: str) -> bool:
        """Stop an MCP server"""
        if name not in self.processes:
            logger.warning(f"Server {name} not running")
            return True

        try:
            process = self.processes[name]
            process.terminate()

            # Wait for process to terminate
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

            del self.processes[name]
            self.servers[name]["status"] = "stopped"

            logger.info(f"Stopped MCP server: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to stop server {name}: {e}")
            return False

    async def list_tools(self, server_name: str) -> List[Dict[str, Any]]:
        """List available tools from a server"""
        if server_name not in self.processes:
            logger.error(f"Server {server_name} not running")
            return []

        try:
            process = self.processes[server_name]

            # Send list tools request
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

            # Write request to stdin
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()

            # Read response from stdout
            response_line = await asyncio.get_event_loop().run_in_executor(None, process.stdout.readline)

            if response_line:
                response = json.loads(response_line.strip())
                if "result" in response and "tools" in response["result"]:
                    return response["result"]["tools"]

        except Exception as e:
            logger.error(f"Failed to list tools from {server_name}: {e}")

        return []

    async def call_tool(self, server_name: str, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Call a tool on a server"""
        if server_name not in self.processes:
            logger.error(f"Server {server_name} not running")
            return None

        try:
            process = self.processes[server_name]

            # Send tool call request
            request = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments or {}},
            }

            # Write request to stdin
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()

            # Read response from stdout
            response_line = await asyncio.get_event_loop().run_in_executor(None, process.stdout.readline)

            if response_line:
                response = json.loads(response_line.strip())
                if "result" in response:
                    return response["result"]
                elif "error" in response:
                    logger.error(f"Tool call error: {response['error']}")
                    return None

        except Exception as e:
            logger.error(f"Failed to call tool {tool_name} on {server_name}: {e}")

        return None

    async def list_resources(self, server_name: str) -> List[Dict[str, Any]]:
        """List available resources from a server"""
        if server_name not in self.processes:
            logger.error(f"Server {server_name} not running")
            return []

        try:
            process = self.processes[server_name]

            # Send list resources request
            request = {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}}

            # Write request to stdin
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()

            # Read response from stdout
            response_line = await asyncio.get_event_loop().run_in_executor(None, process.stdout.readline)

            if response_line:
                response = json.loads(response_line.strip())
                if "result" in response and "resources" in response["result"]:
                    return response["result"]["resources"]

        except Exception as e:
            logger.error(f"Failed to list resources from {server_name}: {e}")

        return []

    async def read_resource(self, server_name: str, uri: str) -> Optional[str]:
        """Read a resource from a server"""
        if server_name not in self.processes:
            logger.error(f"Server {server_name} not running")
            return None

        try:
            process = self.processes[server_name]

            # Send read resource request
            request = {"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": uri}}

            # Write request to stdin
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()

            # Read response from stdout
            response_line = await asyncio.get_event_loop().run_in_executor(None, process.stdout.readline)

            if response_line:
                response = json.loads(response_line.strip())
                if "result" in response and "contents" in response["result"]:
                    contents = response["result"]["contents"]
                    if contents:
                        return contents[0].get("text", "")

        except Exception as e:
            logger.error(f"Failed to read resource {uri} from {server_name}: {e}")

        return None

    def get_server_status(self, name: str) -> str:
        """Get the status of a server"""
        return self.servers.get(name, {}).get("status", "unknown")

    def list_servers(self) -> List[str]:
        """List all registered servers"""
        return list(self.servers.keys())

    async def shutdown(self) -> None:
        """Shutdown all servers"""
        for server_name in list(self.processes.keys()):
            await self.stop_server(server_name)

        logger.info("All MCP servers shut down")
