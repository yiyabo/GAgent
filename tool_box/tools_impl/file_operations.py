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

from tool_box.context import ToolContext
from tool_box.path_resolution import resolve_tool_path_str

logger = logging.getLogger(__name__)

# Security configuration
MAX_READ_CHARS = 100_000   # 100K character cap (~25K-50K tokens)
MAX_READ_LINES = 2_000     # 2K line cap (covers most source files)

def _normalize_allowed_base_paths() -> List[str]:
    cwd = Path(os.getcwd()).resolve()
    defaults = [
        Path("/tmp"),
        Path("/var/tmp"),
        Path("/data"),
        Path("/home/zczhao/GAgent"),  # Project directory on server
        Path(os.path.expanduser("~/Documents")),
        Path(os.path.expanduser("~/Downloads")),
        cwd,  # Current working directory
        cwd / "data",  # Project data directory
        cwd / "results",  # Project results directory
        cwd / "runtime",  # Runtime directory
    ]

    normalized: List[str] = []
    seen: set[str] = set()
    for path in defaults:
        variants = [path]
        try:
            variants.append(path.resolve())
        except Exception:
            pass
        for item in variants:
            key = str(item)
            if key and key not in seen:
                seen.add(key)
                normalized.append(key)
    return normalized


ALLOWED_BASE_PATHS = _normalize_allowed_base_paths()

def _path_is_within(candidate: Path, base: Path) -> bool:
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def _validate_path_security(file_path: str) -> tuple[bool, str]:
    """
    Validate file path for security

    Returns:
        (is_safe, error_message)
    """
    try:
        input_path = Path(file_path).expanduser()
        lexical_abs = Path(os.path.abspath(str(input_path)))
        resolved_abs = input_path.resolve(strict=False)

        # First check if path is in explicitly allowed directories.
        # We check both lexical and resolved absolute paths so symlinked
        # project directories (e.g. data -> /Volumes/...) can still pass.
        is_in_allowed = False
        for allowed in ALLOWED_BASE_PATHS:
            allowed_path = Path(allowed).expanduser()
            try:
                allowed_resolved = allowed_path.resolve(strict=False)
            except Exception:
                allowed_resolved = allowed_path
            if _path_is_within(lexical_abs, allowed_path) or _path_is_within(resolved_abs, allowed_resolved):
                is_in_allowed = True
                break
        
        if is_in_allowed:
            # Path is explicitly allowed, only check file size
            if resolved_abs.exists() and resolved_abs.is_file():
                if resolved_abs.stat().st_size > 50 * 1024 * 1024:  # 50MB limit
                    return False, "File too large (>50MB)"
            return True, ""

        # Check for path traversal attempts
        if ".." in Path(file_path).parts or Path(file_path).is_absolute():
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
            if _path_is_within(resolved_abs, Path(dangerous)):
                return False, f"Access to {dangerous} is not allowed"

        # Check file size for read operations (prevent reading huge files)
        if resolved_abs.exists() and resolved_abs.is_file():
            if resolved_abs.stat().st_size > 50 * 1024 * 1024:  # 50MB limit
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
    session_id: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """
    File operations tool handler

    Args:
        operation: Operation type ("read", "write", "list", "delete", "copy", "move")
        path: Target file/directory path
        content: Content for write operations
        destination: Destination path for copy/move operations
        pattern: File pattern for list operations
        session_id: Optional execution session identifier. Accepted for
            executor compatibility and intentionally ignored by this tool.
        tool_context: Optional structured execution context. Used for
            work_dir-aware relative path resolution.

    Returns:
        Dict containing operation results
    """
    try:
        resolved_path = resolve_tool_path_str(
            path,
            tool_context=tool_context,
            treat_bare_as_results_output=operation == "write",
        )
        resolved_destination = (
            resolve_tool_path_str(
                destination,
                tool_context=tool_context,
                treat_bare_as_results_output=True,
            )
            if destination
            else None
        )
        if operation == "read":
            return await _read_file(resolved_path)
        elif operation == "write":
            return await _write_file(resolved_path, content or "")
        elif operation == "list":
            return await _list_directory(resolved_path, pattern)
        elif operation == "delete":
            return await _delete_path(resolved_path)
        elif operation == "copy":
            return await _copy_path(resolved_path, resolved_destination)
        elif operation == "move":
            return await _move_path(resolved_path, resolved_destination)
        elif operation == "exists":
            return await _check_exists(resolved_path)
        elif operation == "info":
            return await _get_file_info(resolved_path)
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
            result["truncated_message"] = (
                f"Content truncated (read {len(lines)} lines / {len(content)} chars; "
                f"total file size: {file_size} bytes)"
            )

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
