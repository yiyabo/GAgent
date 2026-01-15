#!/usr/bin/env python3
"""
Bio Tools Handler

执行生物信息学 Docker 工具的处理器。
支持 35+ 生物信息学工具，每个工具运行在独立 Docker 容器中。
"""

import asyncio
import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 配置
BIO_TOOLS_CONFIG_PATH = Path(__file__).parent / "tools_config.json"
RUNTIME_BASE_DIR = os.getenv("BIO_TOOLS_RUNTIME_DIR", "/home/zczhao/GAgent/runtime/bio_tools")
DEFAULT_TIMEOUT = 3600  # 1小时默认超时


def load_tools_config() -> Dict[str, Any]:
    """加载工具配置"""
    if not BIO_TOOLS_CONFIG_PATH.exists():
        logger.error(f"Tools config not found: {BIO_TOOLS_CONFIG_PATH}")
        return {}
    
    with open(BIO_TOOLS_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# 全局配置缓存
_tools_config: Optional[Dict[str, Any]] = None


def get_tools_config() -> Dict[str, Any]:
    """获取工具配置（带缓存）"""
    global _tools_config
    if _tools_config is None:
        _tools_config = load_tools_config()
    return _tools_config


def get_available_bio_tools() -> List[Dict[str, Any]]:
    """获取可用的生物信息学工具列表"""
    config = get_tools_config()
    tools = []
    for name, info in config.items():
        tools.append({
            "name": name,
            "description": info.get("description", ""),
            "category": info.get("category", ""),
            "operations": list(info.get("operations", {}).keys())
        })
    return tools


def ensure_tool_directory(tool_name: str) -> Path:
    """确保工具运行目录存在"""
    tool_dir = Path(RUNTIME_BASE_DIR) / tool_name
    tool_dir.mkdir(parents=True, exist_ok=True)
    return tool_dir


def build_docker_command(
    tool_name: str,
    operation: str,
    input_file: Optional[str] = None,
    output_file: Optional[str] = None,
    extra_params: Optional[Dict[str, str]] = None,
) -> str:
    """构建 Docker 运行命令"""
    config = get_tools_config()
    
    if tool_name not in config:
        raise ValueError(f"Unknown tool: {tool_name}")
    
    tool_config = config[tool_name]
    operations = tool_config.get("operations", {})
    
    if operation not in operations:
        available_ops = list(operations.keys())
        raise ValueError(f"Unknown operation '{operation}' for {tool_name}. Available: {available_ops}")
    
    op_config = operations[operation]
    image = tool_config["image"]
    command_template = op_config["command"]
    
    # 确保工具目录存在
    tool_dir = ensure_tool_directory(tool_name)
    
    # 准备挂载目录 - 必须使用绝对路径
    tool_dir_abs = str(tool_dir.resolve())
    mounts = [f"-v {tool_dir_abs}:/work"]
    
    # 如果有输入文件，挂载输入文件目录
    if input_file:
        input_path = Path(input_file)
        # 转换为绝对路径
        if not input_path.is_absolute():
            input_path = input_path.resolve()
        
        if input_path.exists():
            input_dir_abs = str(input_path.parent.resolve())
            mounts.append(f"-v {input_dir_abs}:/input:ro")
            input_file = f"/input/{input_path.name}"
        else:
            logger.warning(f"Input file not found: {input_path}")
    
    # 如果是 checkv，添加数据库挂载
    if tool_name == "checkv":
        # CheckV 数据库路径 (服务器上的固定位置)
        db_path = "/home/zczhao/GAgent/data/databases/bio_tools/checkv/checkv-db-v1.5"
        mounts.append(f"-v {db_path}:/work/database")
    
    # 构建命令参数
    params = {
        "input": input_file or "",
        "output": f"/work/{output_file}" if output_file else "",
        "output_dir": "/work",
    }
    
    # 添加额外参数
    if extra_params:
        params.update(extra_params)
    
    # 替换模板中的占位符
    command = command_template.format(**params)
    
    # 构建完整的 Docker 命令
    mount_str = " ".join(mounts)
    docker_cmd = f"docker run --rm {mount_str} -w /work {image} {command}"
    
    return docker_cmd


async def execute_docker_command(
    command: str,
    timeout: int = DEFAULT_TIMEOUT,
    capture_output: bool = True,
) -> Dict[str, Any]:
    """执行 Docker 命令"""
    logger.info(f"Executing: {command}")
    
    start_time = datetime.now()
    
    try:
        # 使用 asyncio.create_subprocess_shell 异步执行
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE if capture_output else None,
            stderr=asyncio.subprocess.PIPE if capture_output else None,
        )
        
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout
        )
        
        duration = (datetime.now() - start_time).total_seconds()
        
        result = {
            "success": process.returncode == 0,
            "exit_code": process.returncode,
            "duration_seconds": duration,
            "command": command,
        }
        
        if capture_output:
            result["stdout"] = stdout.decode("utf-8", errors="replace") if stdout else ""
            result["stderr"] = stderr.decode("utf-8", errors="replace") if stderr else ""
        
        if process.returncode != 0:
            logger.warning(f"Command failed with exit code {process.returncode}: {result.get('stderr', '')[:500]}")
        
        return result
        
    except asyncio.TimeoutError:
        logger.error(f"Command timed out after {timeout}s: {command}")
        return {
            "success": False,
            "error": f"Command timed out after {timeout} seconds",
            "command": command,
        }
    except Exception as e:
        logger.exception(f"Command execution failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "command": command,
        }


async def bio_tools_handler(
    tool_name: str,
    operation: str = "help",
    input_file: Optional[str] = None,
    output_file: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    生物信息学工具处理器
    
    Args:
        tool_name: 工具名称 (如 "seqkit", "blast", "prodigal")
        operation: 操作类型 (如 "stats", "blastn", "predict")
        input_file: 输入文件路径
        output_file: 输出文件名（保存在工具目录中）
        params: 额外参数
        timeout: 超时时间（秒）
    
    Returns:
        执行结果字典
    """
    logger.info(f"Bio tools handler called: tool={tool_name}, operation={operation}")
    
    # 特殊操作：列出可用工具
    if tool_name == "list" or operation == "list":
        tools = get_available_bio_tools()
        return {
            "success": True,
            "operation": "list",
            "tools": tools,
            "count": len(tools),
        }
    
    # 特殊操作：获取工具帮助
    if operation == "help":
        config = get_tools_config()
        if tool_name not in config:
            return {
                "success": False,
                "error": f"Unknown tool: {tool_name}",
                "available_tools": list(config.keys()),
            }
        
        tool_config = config[tool_name]
        # 返回更详细的操作信息，包括 notes 和 extra_params
        operations_detail = {}
        for op, info in tool_config.get("operations", {}).items():
            operations_detail[op] = {
                "description": info.get("description", ""),
                "extra_params": info.get("extra_params", []),
                "notes": info.get("notes", ""),
            }
        
        return {
            "success": True,
            "tool": tool_name,
            "description": tool_config.get("description", ""),
            "notes": tool_config.get("notes", ""),
            "image": tool_config.get("image", ""),
            "operations": operations_detail,
        }
    
    # 验证工具和操作
    config = get_tools_config()
    if tool_name not in config:
        return {
            "success": False,
            "error": f"Unknown tool: {tool_name}",
            "available_tools": list(config.keys()),
        }
    
    try:
        # 构建 Docker 命令
        docker_cmd = build_docker_command(
            tool_name=tool_name,
            operation=operation,
            input_file=input_file,
            output_file=output_file,
            extra_params=params,
        )
        
        # 执行命令
        result = await execute_docker_command(docker_cmd, timeout=timeout)
        
        # 添加工具信息
        result["tool"] = tool_name
        result["operation"] = operation
        
        # 如果有输出文件，添加输出路径
        if output_file and result.get("success"):
            tool_dir = ensure_tool_directory(tool_name)
            result["output_path"] = str(tool_dir / output_file)
        
        return result
        
    except ValueError as e:
        return {
            "success": False,
            "error": str(e),
            "tool": tool_name,
            "operation": operation,
        }
    except Exception as e:
        logger.exception(f"Bio tools execution failed: {e}")
        return {
            "success": False,
            "error": f"Execution failed: {str(e)}",
            "tool": tool_name,
            "operation": operation,
        }


# 工具定义（用于注册到 tool_box）
bio_tools_tool = {
    "name": "bio_tools",
    "description": """Execute bioinformatics tools in Docker containers.
    
Supports 35+ tools including:
- SeqKit: FASTA/Q sequence manipulation
- BLAST: Sequence alignment
- Prodigal: Prokaryotic gene prediction  
- HMMER: HMM-based sequence analysis
- CheckV: Viral genome quality assessment
- And many more...

Use operation="list" to see all available tools.
Use operation="help" with a tool_name to see available operations.""",
    "category": "bioinformatics",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Name of the bioinformatics tool (e.g., seqkit, blast, prodigal)"
            },
            "operation": {
                "type": "string",
                "description": "Operation to perform (e.g., stats, blastn, predict). Use 'help' to see available operations."
            },
            "input_file": {
                "type": "string",
                "description": "Path to input file (FASTA, FASTQ, etc.)"
            },
            "output_file": {
                "type": "string",
                "description": "Name for output file (saved in tool's runtime directory)"
            },
            "params": {
                "type": "object",
                "description": "Additional parameters (e.g., database, pattern)"
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 3600)"
            }
        },
        "required": ["tool_name"]
    },
    "handler": bio_tools_handler,
    "tags": ["bioinformatics", "docker", "sequence", "genomics"],
    "examples": [
        {
            "description": "List available tools",
            "params": {"tool_name": "list"}
        },
        {
            "description": "Get SeqKit stats for a FASTA file",
            "params": {
                "tool_name": "seqkit",
                "operation": "stats",
                "input_file": "/data/sequences.fasta"
            }
        },
        {
            "description": "Run Prodigal gene prediction",
            "params": {
                "tool_name": "prodigal",
                "operation": "predict",
                "input_file": "/data/genome.fasta",
                "output_file": "genes.gff",
                "params": {"protein_output": "proteins.faa"}
            }
        }
    ]
}
