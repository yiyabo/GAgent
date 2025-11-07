"""Shell execution tool implementation."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, Union

from app.services.execution import command_runner, workspace_manager


async def shell_execute_handler(
    owner: str,
    command: Union[str, Sequence[str]],
    *,
    timeout: Optional[int] = None,
    reset_workspace: bool = False,
    env: Optional[Mapping[str, str]] = None,
    files: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Execute a shell command within the owner's isolated workspace.

    Args:
        owner: Logical workspace / session identifier.
        command: Shell command to run (string or argv sequence).
        timeout: Optional timeout in seconds (defaults to backend setting).
        reset_workspace: Whether to wipe the workspace before execution.
        env: Optional environment variable overrides.
        files: Optional mapping of relative paths to file content (written before execution).
    """

    workspace = await workspace_manager.prepare_workspace(owner, reset=reset_workspace)

    if files:
        for relative_path, content in files.items():
            relative_path = relative_path.lstrip("/\\")
            await workspace_manager.write_file(owner, relative_path, content)

    argv = command_runner.parse_command(command)
    result = await command_runner.run_shell_command(
        argv,
        cwd=workspace,
        timeout=timeout,
        env=dict(env) if env else None,
    )

    payload = result.to_dict()
    payload.update(
        {
            "workspace": str(workspace),
            "files_written": list(files.keys()) if files else [],
        }
    )
    return payload


shell_execution_tool = {
    "name": "shell_execute",
    "description": "Execute simple shell commands in an ISOLATED workspace (NOT the project directory). Use ONLY for basic commands that don't need access to project files. For complex tasks, file analysis, or ML/data science work, use claude_code instead.",
    "category": "execution",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "owner": {
                "type": "string",
                "description": "工作区/会话标识",
            },
            "command": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
                "description": "要执行的命令（字符串或 argv 数组）",
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 600,
                "description": "超时时间（秒）",
            },
            "reset_workspace": {
                "type": "boolean",
                "description": "执行前是否清理工作区",
                "default": False,
            },
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "额外的环境变量",
            },
            "files": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "在执行前写入工作区的文件内容 (路径 -> 内容)",
            },
        },
        "required": ["owner", "command"],
    },
    "handler": shell_execute_handler,
    "tags": ["shell", "execution", "workspace"],
    "examples": ["执行 python 脚本", "运行 make 构建"],
}
