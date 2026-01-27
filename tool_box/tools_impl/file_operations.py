"""
File Operations Tool Implementation

This module provides file system operations for AI agents.
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Security configuration
MAX_READ_CHARS = 100_000   # 100K 字符限制，约 25-50K tokens
MAX_READ_LINES = 2_000     # 2000 行限制，覆盖绝大多数源文件

ALLOWED_BASE_PATHS = [
    "/tmp",
    "/var/tmp",
    "/data",  # Data directory
    "/home/zczhao/GAgent",  # Project directory on server
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Downloads"),
    os.getcwd(),  # Current working directory
    os.path.join(os.getcwd(), "data"),  # Project data directory
    os.path.join(os.getcwd(), "results"),  # Project results directory
    os.path.join(os.getcwd(), "runtime"),  # Runtime directory
]

# Default work directory - avoid creating files in root directory
DEFAULT_WORK_DIR = "results"


def _normalize_file_path(file_path: str) -> str:
    """Normalize file path, avoid creating files in root directory"""
    # If it's a simple filename (no directory), put it in the results directory
    if not os.path.dirname(file_path) and not file_path.startswith('/'):
        os.makedirs(DEFAULT_WORK_DIR, exist_ok=True)
        return os.path.join(DEFAULT_WORK_DIR, file_path)
    
    return file_path


def _validate_path_security(file_path: str) -> tuple[bool, str]:
    """
    Validate file path for security

    Returns:
        (is_safe, error_message)
    """
    try:
        # Resolve absolute path to handle .. and symlinks
        abs_path = Path(file_path).resolve()
        abs_path_str = str(abs_path)

        # First check if path is in explicitly allowed directories
        # If so, skip dangerous paths check
        is_in_allowed = any(abs_path_str.startswith(allowed) for allowed in ALLOWED_BASE_PATHS)
        
        if is_in_allowed:
            # Path is explicitly allowed, only check file size
            if abs_path.exists() and abs_path.is_file():
                if abs_path.stat().st_size > 50 * 1024 * 1024:  # 50MB limit
                    return False, "File too large (>50MB)"
            return True, ""

        # Check for path traversal attempts
        if ".." in file_path or file_path.startswith("/"):
            return False, "Path outside allowed directories"

        # Check for dangerous paths (only for non-allowed paths)
        dangerous_paths = [
            "/etc",
            "/bin",
            "/sbin",
            "/usr/bin",
            "/usr/sbin",
            "/root",
            "/var/log",
            "/proc",
            "/sys",
        ]

        for dangerous in dangerous_paths:
            if abs_path_str.startswith(dangerous):
                return False, f"Access to {dangerous} is not allowed"

        # Check file size for read operations (prevent reading huge files)
        if abs_path.exists() and abs_path.is_file():
            if abs_path.stat().st_size > 50 * 1024 * 1024:  # 50MB limit
                return False, "File too large (>50MB)"

        return True, ""

    except Exception as e:
        return False, f"Path validation error: {str(e)}"


async def file_operations_handler(
    operation: str,
    path: str,
    content: Optional[str] = None,
    destination: Optional[str] = None,
    pattern: Optional[str] = None,
) -> Dict[str, Any]:
    # Normalize file path
    path = _normalize_file_path(path)
    if destination:
        destination = _normalize_file_path(destination)
    """
    File operations tool handler

    Args:
        operation: Operation type ("read", "write", "list", "delete", "copy", "move")
        path: Target file/directory path
        content: Content for write operations
        destination: Destination path for copy/move operations
        pattern: File pattern for list operations

    Returns:
        Dict containing operation results
    """
    try:
        if operation == "read":
            return await _read_file(path)
        elif operation == "write":
            return await _write_file(path, content or "")
        elif operation == "list":
            return await _list_directory(path, pattern)
        elif operation == "delete":
            return await _delete_path(path)
        elif operation == "copy":
            return await _copy_path(path, destination)
        elif operation == "move":
            return await _move_path(path, destination)
        elif operation == "exists":
            return await _check_exists(path)
        elif operation == "info":
            return await _get_file_info(path)
        else:
            return {"operation": operation, "success": False, "error": f"Unsupported operation: {operation}"}

    except Exception as e:
        logger.error(f"File operation failed: {e}")
        return {"operation": operation, "path": path, "success": False, "error": str(e)}


async def _read_file(file_path: str, max_chars: int = None, max_lines: int = None) -> Dict[str, Any]:
    """Read file content with security validation and output limits

    Args:
        file_path: Path to the file to read
        max_chars: Maximum characters to read (default: MAX_READ_CHARS)
        max_lines: Maximum lines to read (default: MAX_READ_LINES)
    """
    if max_chars is None:
        max_chars = MAX_READ_CHARS
    if max_lines is None:
        max_lines = MAX_READ_LINES

    try:
        # Security validation
        is_safe, error_msg = _validate_path_security(file_path)
        if not is_safe:
            return {
                "operation": "read",
                "path": file_path,
                "success": False,
                "error": f"Security violation: {error_msg}",
            }

        path = Path(file_path)

        if not path.exists():
            return {"operation": "read", "path": file_path, "success": False, "error": "File not found"}

        if not path.is_file():
            return {"operation": "read", "path": file_path, "success": False, "error": "Path is not a file"}

        # Read file with limits to prevent context overflow
        file_size = path.stat().st_size
        with open(path, "r", encoding="utf-8") as f:
            lines = []
            total_chars = 0
            truncated = False
            line_count = 0

            for line in f:
                line_count += 1
                if line_count > max_lines or total_chars + len(line) > max_chars:
                    truncated = True
                    break
                lines.append(line)
                total_chars += len(line)

            content = ''.join(lines)

        result = {
            "operation": "read",
            "path": file_path,
            "success": True,
            "content": content,
            "size": len(content),
            "file_size": file_size,
            "lines_read": len(lines),
            "encoding": "utf-8",
            "truncated": truncated,
        }

        if truncated:
            result["truncated_message"] = f"内容已截断（已读取 {len(lines)} 行/{len(content)} 字符，文件总大小 {file_size} 字节）"

        return result

    except UnicodeDecodeError:
        return {
            "operation": "read",
            "path": file_path,
            "success": False,
            "error": "File encoding not supported (try binary read)",
        }
    except Exception as e:
        return {"operation": "read", "path": file_path, "success": False, "error": str(e)}


async def _write_file(file_path: str, content: str) -> Dict[str, Any]:
    """Write content to file with security validation"""
    try:
        # Security validation
        is_safe, error_msg = _validate_path_security(file_path)
        if not is_safe:
            return {
                "operation": "write",
                "path": file_path,
                "success": False,
                "error": f"Security violation: {error_msg}",
            }

        path = Path(file_path)

        # Create parent directories if they don't exist
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        return {"operation": "write", "path": file_path, "success": True, "size": len(content)}

    except Exception as e:
        return {"operation": "write", "path": file_path, "success": False, "error": str(e)}


async def _list_directory(dir_path: str, pattern: Optional[str] = None) -> Dict[str, Any]:
    """List directory contents"""
    try:
        path = Path(dir_path)

        if not path.exists():
            return {"operation": "list", "path": dir_path, "success": False, "error": "Directory not found"}

        if not path.is_dir():
            return {"operation": "list", "path": dir_path, "success": False, "error": "Path is not a directory"}

        items = []
        for item in path.iterdir():
            if pattern and not item.match(pattern):
                continue

            items.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else 0,
                }
            )

        return {"operation": "list", "path": dir_path, "success": True, "items": items, "count": len(items)}

    except Exception as e:
        return {"operation": "list", "path": dir_path, "success": False, "error": str(e)}


async def _delete_path(target_path: str) -> Dict[str, Any]:
    """Delete file or directory with security validation"""
    try:
        # Security validation
        is_safe, error_msg = _validate_path_security(target_path)
        if not is_safe:
            return {
                "operation": "delete",
                "path": target_path,
                "success": False,
                "error": f"Security violation: {error_msg}",
            }

        path = Path(target_path)

        if not path.exists():
            return {"operation": "delete", "path": target_path, "success": False, "error": "Path not found"}

        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

        return {"operation": "delete", "path": target_path, "success": True}

    except Exception as e:
        return {"operation": "delete", "path": target_path, "success": False, "error": str(e)}


async def _copy_path(source: str, destination: str) -> Dict[str, Any]:
    """Copy file or directory with security validation"""
    try:
        # Security validation for both paths
        is_safe_src, error_msg_src = _validate_path_security(source)
        if not is_safe_src:
            return {
                "operation": "copy",
                "source": source,
                "destination": destination,
                "success": False,
                "error": f"Source security violation: {error_msg_src}",
            }

        is_safe_dest, error_msg_dest = _validate_path_security(destination)
        if not is_safe_dest:
            return {
                "operation": "copy",
                "source": source,
                "destination": destination,
                "success": False,
                "error": f"Destination security violation: {error_msg_dest}",
            }

        src_path = Path(source)
        dest_path = Path(destination)

        if not src_path.exists():
            return {
                "operation": "copy",
                "source": source,
                "destination": destination,
                "success": False,
                "error": "Source path not found",
            }

        # Create destination parent directories
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        if src_path.is_file():
            shutil.copy2(src_path, dest_path)
        elif src_path.is_dir():
            shutil.copytree(src_path, dest_path)

        return {"operation": "copy", "source": source, "destination": destination, "success": True}

    except Exception as e:
        return {"operation": "copy", "source": source, "destination": destination, "success": False, "error": str(e)}


async def _move_path(source: str, destination: str) -> Dict[str, Any]:
    """Move file or directory with security validation"""
    try:
        # Security validation for both paths
        is_safe_src, error_msg_src = _validate_path_security(source)
        if not is_safe_src:
            return {
                "operation": "move",
                "source": source,
                "destination": destination,
                "success": False,
                "error": f"Source security violation: {error_msg_src}",
            }

        is_safe_dest, error_msg_dest = _validate_path_security(destination)
        if not is_safe_dest:
            return {
                "operation": "move",
                "source": source,
                "destination": destination,
                "success": False,
                "error": f"Destination security violation: {error_msg_dest}",
            }

        src_path = Path(source)
        dest_path = Path(destination)

        if not src_path.exists():
            return {
                "operation": "move",
                "source": source,
                "destination": destination,
                "success": False,
                "error": "Source path not found",
            }

        # Create destination parent directories
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        shutil.move(str(src_path), str(dest_path))

        return {"operation": "move", "source": source, "destination": destination, "success": True}

    except Exception as e:
        return {"operation": "move", "source": source, "destination": destination, "success": False, "error": str(e)}


async def _check_exists(target_path: str) -> Dict[str, Any]:
    """Check if path exists"""
    path = Path(target_path)
    return {
        "operation": "exists",
        "path": target_path,
        "exists": path.exists(),
        "type": "directory" if path.is_dir() else "file" if path.is_file() else "unknown",
    }


async def _get_file_info(target_path: str) -> Dict[str, Any]:
    """Get file/directory information"""
    try:
        path = Path(target_path)

        if not path.exists():
            return {"operation": "info", "path": target_path, "success": False, "error": "Path not found"}

        stat = path.stat()

        return {
            "operation": "info",
            "path": target_path,
            "success": True,
            "type": "directory" if path.is_dir() else "file",
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "created": stat.st_ctime,
            "permissions": oct(stat.st_mode)[-3:],
        }

    except Exception as e:
        return {"operation": "info", "path": target_path, "success": False, "error": str(e)}


# Tool definition for file operations
file_operations_tool = {
    "name": "file_operations",
    "description": "Perform file system operations (read, write, list, delete, copy, move, etc.)",
    "category": "file_management",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": "Operation type",
                "enum": ["read", "write", "list", "delete", "copy", "move", "exists", "info"],
            },
            "path": {"type": "string", "description": "Target file/directory path"},
            "content": {"type": "string", "description": "Content to write (required for write operation)"},
            "destination": {"type": "string", "description": "Destination path (required for copy/move operations)"},
            "pattern": {"type": "string", "description": "File matching pattern (optional for list operation)"},
        },
        "required": ["operation", "path"],
    },
    "handler": file_operations_handler,
    "tags": ["file", "filesystem", "read", "write", "management"],
    "examples": ["Read configuration file", "Create new document", "List directory contents", "Copy file to backup directory"],
}
