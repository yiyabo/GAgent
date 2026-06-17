"""
File Operations Tool Implementation

This module provides file system operations for AI agents.
"""

import asyncio
import logging
import os
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from tool_box.context import ToolContext
from tool_box.path_resolution import resolve_tool_path_str

logger = logging.getLogger(__name__)

# Security configuration
MAX_READ_CHARS = 100_000   # 100K character cap (~25K-50K tokens)
MAX_READ_LINES = 2_000     # 2K line cap (covers most source files)
MAX_READ_FILE_BYTES = 50 * 1024 * 1024
MAX_COPY_MOVE_FILE_BYTES = 500 * 1024 * 1024
MAX_PROFILE_SCAN_ENTRIES = 50_000
MAX_PROFILE_STATUS_FILES = 40
MAX_PROFILE_STATUS_FILE_BYTES = 2 * 1024 * 1024
MAX_PROFILE_STATUS_LINES = 100_000
MAX_PROFILE_CHILD_SAMPLE = 80
MAX_RECONCILIATION_EXAMPLES = 20
MAX_RECONCILIATION_STRUCTURE_SCAN_DIRS = 5_000
MAX_RECONCILIATION_FILE_PATTERNS = 30
DEFAULT_EXTERNAL_ALLOWED_BASE_PATHS = (Path("/mnt/sdm/zczhao"),)

_STATUS_SUCCESS_TOKENS = {"complete", "completed", "success", "succeeded", "done", "passed", "ok"}
_STATUS_FAILURE_TOKENS = {"fail", "failed", "failure", "error", "errors", "crash", "crashed", "aborted"}
_STATUS_NEUTRAL_TOKENS = {"progress", "status", "manifest", "summary", "report", "reports", "log", "logs"}
_STATUS_FILE_EXTENSIONS = {".txt", ".tsv", ".csv", ".json", ".jsonl", ".log", ".out", ".err"}


def _redirect_session_workspace_path(
    raw_path: Optional[str],
    *,
    tool_context: Optional[ToolContext],
    require_existing: bool = False,
) -> Optional[str]:
    text = str(raw_path or "").strip()
    if not text or tool_context is None:
        return raw_path

    session_id = str(tool_context.session_id or "").strip()
    work_dir = str(tool_context.work_dir or "").strip()
    if not session_id or not work_dir:
        return raw_path

    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        return raw_path

    try:
        from app.services.session_paths import get_runtime_session_dir

        session_dir = get_runtime_session_dir(session_id, create=True).resolve()
    except Exception:
        return raw_path

    workspace_dir = (session_dir / "workspace").resolve()
    resolved_candidate = candidate.resolve(strict=False)
    try:
        rel_workspace_path = resolved_candidate.relative_to(workspace_dir)
    except ValueError:
        return raw_path

    task_output_dir = Path(work_dir).expanduser().resolve(strict=False)
    redirected = (task_output_dir / rel_workspace_path).resolve(strict=False)
    try:
        redirected.relative_to(task_output_dir)
    except ValueError:
        return raw_path

    if require_existing and not redirected.exists():
        return raw_path

    logger.info(
        "Redirecting session workspace path into task work_dir: %s -> %s",
        resolved_candidate,
        redirected,
    )
    return str(redirected)

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
        *DEFAULT_EXTERNAL_ALLOWED_BASE_PATHS,
    ]

    extra_paths: List[Path] = []
    raw_extra = os.getenv("FILE_OPERATIONS_ALLOWED_BASE_PATHS", "")
    for part in raw_extra.split(os.pathsep):
        value = part.strip()
        if value:
            extra_paths.append(Path(os.path.expanduser(value)))

    normalized: List[str] = []
    seen: set[str] = set()
    for path in [*defaults, *extra_paths]:
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


def _enforce_session_scope(
    candidate_resolved: Path,
    candidate_lexical: Path,
    tool_context: Optional[ToolContext],
) -> Tuple[Optional[bool], Optional[str]]:
    """
    Session isolation layer.

    Returns:
        (True, None)   - path is session-private; allow immediately.
        (False, error) - path sits inside a cross-session / global artifact
                         directory but is NOT within the current session;
                         reject.
        (None, None)   - path is not session-scoped; fall through to the
                         existing ALLOWED_BASE_PATHS whitelist.
    """
    if tool_context is None:
        return (None, None)

    session_id = str(tool_context.session_id or "").strip()
    if not session_id:
        return (None, None)

    session_dir: Optional[Path] = None
    try:
        from app.services.session_paths import get_runtime_session_dir

        session_dir = get_runtime_session_dir(session_id, create=True).resolve()
    except Exception:
        pass

    # Membership is judged on candidate_resolved ONLY (not candidate_lexical),
    # because resolve() follows symlinks. Using lexical here would let an
    # agent create `session_dir/escape -> /project_root/output/secret.csv`
    # and read global artifacts through the symlink (lexical passes the
    # session_dir check before the risk-dir check runs).

    # Risk directories are anchored to the project root reported by
    # session_paths (NOT os.getcwd()) so that risk-dir detection stays
    # consistent with where sessions actually live, even if the process
    # working directory differs.
    try:
        from app.services.session_paths import get_runtime_root

        project_root = get_runtime_root().parent.resolve()
    except Exception:
        project_root = Path(os.getcwd()).resolve()
    risk_dirs: List[Path] = []
    for name in ("runtime", "output", "results"):
        try:
            risk_dirs.append((project_root / name).resolve(strict=False))
        except Exception:
            continue

    def _resolved_within(base: Path) -> bool:
        try:
            candidate_resolved.relative_to(base)
            return True
        except ValueError:
            return False

    # 1. session-private: resolved path is inside the current session dir.
    if session_dir is not None and _resolved_within(session_dir):
        return (True, None)

    # 2. cross-session / global artifact: resolved path is inside a risk dir
    #    but NOT inside the current session (checked above). This also
    #    catches symlinks whose lexical form is inside session_dir but whose
    #    resolved target lands in a risk dir.
    for risk_resolved in risk_dirs:
        if _resolved_within(risk_resolved):
            return (
                False,
                f"Access denied: path is inside a cross-session or global "
                f"artifact directory ({risk_resolved}). Scope file access to "
                f"the current session directory.",
            )

    # 3. work_dir: legitimate task output dirs that live outside session_dir
    #    (e.g. ad-hoc interpreter runs, tmpdirs). Runs after the risk check
    #    so a work_dir that fell back to the project root cannot smuggle
    #    access to output/ or results/.
    work_dir = str(tool_context.work_dir or "").strip()
    if work_dir:
        try:
            work_root = Path(work_dir).expanduser().resolve(strict=False)
            if _resolved_within(work_root):
                return (True, None)
        except Exception:
            pass

    return (None, None)


def _validate_path_security(
    file_path: str,
    *,
    enforce_file_size_limit: bool = False,
    tool_context: Optional[ToolContext] = None,
) -> Tuple[bool, str]:
    """
    Validate file path for security.

    When a ``tool_context`` carrying a ``session_id`` is supplied, an extra
    session-isolation layer is enforced before the legacy ALLOWED_BASE_PATHS
    whitelist: paths inside the current session directory are always allowed,
    while paths inside cross-session / global artifact directories
    (``runtime/``, ``output/``, ``results/`` under the project root) that do
    NOT belong to the current session are rejected outright.

    Returns:
        (is_safe, error_message)
    """
    try:
        input_path = Path(file_path).expanduser()
        lexical_abs = Path(os.path.abspath(str(input_path)))
        resolved_abs = input_path.resolve(strict=False)

        # Session isolation layer (takes precedence over the legacy whitelist
        # so cross-session access cannot be sneaked in via allowed base paths).
        scope_verdict, scope_error = _enforce_session_scope(resolved_abs, lexical_abs, tool_context)
        if scope_verdict is True:
            # Session-private path; allow without further whitelist checks.
            if enforce_file_size_limit and resolved_abs.exists() and resolved_abs.is_file():
                if resolved_abs.stat().st_size > MAX_READ_FILE_BYTES:
                    return False, "File too large (>50MB)"
            return True, ""
        if scope_verdict is False:
            return False, scope_error or "Access denied by session isolation"
        # scope_verdict is None -> not session-scoped, fall through to whitelist.

        # Global guard: resolved paths landing in dangerous system dirs are
        # rejected REGARDLESS of the allowed-base-paths check below, so a
        # symlink whose lexical form sits inside an allowed dir (e.g. an
        # agent-created symlink under session_dir pointing to /etc) cannot
        # bypass the dangerous-paths guard.
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
            # Size limits are only for read-like operations. Copy/move of large
            # binary artifacts must remain possible inside allowed workspaces.
            if enforce_file_size_limit and resolved_abs.exists() and resolved_abs.is_file():
                if resolved_abs.stat().st_size > MAX_READ_FILE_BYTES:
                    return False, "File too large (>50MB)"
            return True, ""

        # Check for path traversal attempts
        if ".." in Path(file_path).parts or Path(file_path).is_absolute():
            return False, "Path outside allowed directories"

        # Check file size only for read-like operations (prevent dumping huge files).
        if enforce_file_size_limit and resolved_abs.exists() and resolved_abs.is_file():
            if resolved_abs.stat().st_size > 50 * 1024 * 1024:  # 50MB limit
                return False, "File too large (>50MB)"

        return True, ""

    except Exception as e:
        return False, f"Path validation error: {str(e)}"


def _validate_copy_move_size(file_path: str) -> Tuple[bool, str]:
    try:
        path = Path(file_path).expanduser().resolve(strict=False)
        if path.exists() and path.is_file() and path.stat().st_size > MAX_COPY_MOVE_FILE_BYTES:
            return False, "File too large for copy/move (>500MB)"
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
        operation: Operation type ("read", "write", "list", "profile", "census", "delete", "copy", "move")
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
        rewritten_path = path
        rewritten_destination = destination
        if operation == "write":
            rewritten_path = _redirect_session_workspace_path(
                path,
                tool_context=tool_context,
            )
        elif operation in {"copy", "move", "delete"}:
            rewritten_path = _redirect_session_workspace_path(
                path,
                tool_context=tool_context,
                require_existing=True,
            )
        if operation in {"copy", "move"}:
            rewritten_destination = _redirect_session_workspace_path(
                destination,
                tool_context=tool_context,
            )

        resolved_path = resolve_tool_path_str(
            rewritten_path or "",
            tool_context=tool_context,
            treat_bare_as_results_output=operation == "write",
        )
        resolved_destination = (
            resolve_tool_path_str(
                rewritten_destination or "",
                tool_context=tool_context,
                treat_bare_as_results_output=True,
            )
            if destination
            else None
        )
        if operation == "read":
            return await _read_file(resolved_path, tool_context=tool_context)
        elif operation == "write":
            return await _write_file(resolved_path, content or "", tool_context=tool_context)
        elif operation == "list":
            return await _list_directory(resolved_path, pattern, tool_context=tool_context)
        elif operation in {"profile", "census"}:
            return await _profile_directory(resolved_path, pattern, operation=operation, tool_context=tool_context)
        elif operation == "delete":
            return await _delete_path(resolved_path, tool_context=tool_context)
        elif operation == "copy":
            if resolved_destination is None:
                return {"operation": "copy", "path": resolved_path, "success": False, "error": "destination is required"}
            return await _copy_path(resolved_path, resolved_destination, tool_context=tool_context)
        elif operation == "move":
            if resolved_destination is None:
                return {"operation": "move", "path": resolved_path, "success": False, "error": "destination is required"}
            return await _move_path(resolved_path, resolved_destination, tool_context=tool_context)
        elif operation == "exists":
            return await _check_exists(resolved_path, tool_context=tool_context)
        elif operation == "info":
            return await _get_file_info(resolved_path, tool_context=tool_context)
        else:
            return {"operation": operation, "success": False, "error": f"Unsupported operation: {operation}"}

    except Exception as e:
        logger.error(f"File operation failed: {e}")
        return {"operation": operation, "path": path, "success": False, "error": str(e)}


async def _read_file(file_path: str, max_chars: Optional[int] = None, max_lines: Optional[int] = None, tool_context: Optional[ToolContext] = None) -> Dict[str, Any]:
    """Read file content with security validation and output limits

    Args:
        file_path: Path to the file to read
        max_chars: Maximum characters to read (default: MAX_READ_CHARS)
        max_lines: Maximum lines to read (default: MAX_READ_LINES)
        tool_context: Optional execution context for session isolation.
    """
    if max_chars is None:
        max_chars = MAX_READ_CHARS
    if max_lines is None:
        max_lines = MAX_READ_LINES

    try:
        is_safe, error_msg = _validate_path_security(file_path, tool_context=tool_context)
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


async def _write_file(file_path: str, content: str, tool_context: Optional[ToolContext] = None) -> Dict[str, Any]:
    """Write content to file with security validation"""
    try:
        is_safe, error_msg = _validate_path_security(file_path, tool_context=tool_context)
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


def _tokenize_status_name(name: str) -> Set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", name.lower()) if token}


def _looks_like_status_file(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in _STATUS_FILE_EXTENSIONS:
        return False
    tokens = _tokenize_status_name(path.stem)
    if not tokens:
        return False
    signal_tokens = _STATUS_SUCCESS_TOKENS | _STATUS_FAILURE_TOKENS | _STATUS_NEUTRAL_TOKENS
    return bool(tokens & signal_tokens)


def _classify_status_file(path: Path) -> str:
    tokens = _tokenize_status_name(path.stem)
    if tokens & _STATUS_FAILURE_TOKENS:
        return "failure"
    if tokens & _STATUS_SUCCESS_TOKENS:
        return "success"
    return "status"


def _looks_like_status_directory(path: Path) -> bool:
    if not path.is_dir():
        return False
    tokens = _tokenize_status_name(path.name)
    # Directory names often encode sample state (e.g. sample_failed), so only
    # neutral status/progress/report names are sufficient by themselves.
    if tokens & _STATUS_NEUTRAL_TOKENS:
        return True
    try:
        return any(_looks_like_status_file(child) for child in path.iterdir())
    except Exception:
        return False


def _read_status_file_summary(path: Path) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "name": path.name,
        "path": str(path),
        "kind": _classify_status_file(path),
    }
    try:
        stat = path.stat()
        info["size"] = stat.st_size
        if stat.st_size > MAX_PROFILE_STATUS_FILE_BYTES:
            info["truncated"] = True
            info["line_count"] = None
            info["sample_values"] = []
            return info

        sample_values: List[str] = []
        line_count = 0
        entry_count = 0
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line_count += 1
                text = raw_line.strip()
                if text:
                    entry_count += 1
                    if len(sample_values) < 10:
                        sample_values.append(text[:240])
                if line_count >= MAX_PROFILE_STATUS_LINES:
                    info["truncated"] = True
                    break
        info["line_count"] = line_count
        info["entry_count"] = entry_count
        info["sample_values"] = sample_values
        if _looks_like_manifest_status_file(path, sample_values, entry_count):
            info["count_source"] = "manifest_like"
            info["count_confidence"] = "high"
        else:
            info["count_source"] = "filename_heuristic"
            info["count_confidence"] = "low"
    except Exception as exc:
        info["read_error"] = str(exc)
    return info


def _looks_like_manifest_status_file(path: Path, sample_values: List[str], entry_count: int) -> bool:
    if entry_count <= 0:
        return False
    if path.suffix.lower() in {".log", ".err", ".out"}:
        return False
    if not sample_values:
        return False
    for value in sample_values[:10]:
        text = str(value or "").strip()
        if not text:
            continue
        if len(text) > 300:
            return False
        if any(ch.isspace() for ch in text):
            return False
        if "," in text or "\t" in text:
            return False
        if ":" in text and not re.match(r"^[A-Za-z]:[/\\]", text):
            return False
    return True


def _collect_status_files(path: Path) -> List[Dict[str, Any]]:
    status_files: List[Dict[str, Any]] = []
    try:
        direct_children = list(path.iterdir())
    except Exception:
        return status_files

    for child in direct_children:
        if _looks_like_status_file(child):
            status_files.append(_read_status_file_summary(child))
            if len(status_files) >= MAX_PROFILE_STATUS_FILES:
                return status_files

    for child in direct_children:
        if not child.is_dir():
            continue
        try:
            for nested in child.iterdir():
                if _looks_like_status_file(nested):
                    status_files.append(_read_status_file_summary(nested))
                    if len(status_files) >= MAX_PROFILE_STATUS_FILES:
                        return status_files
        except Exception:
            continue
    return status_files


def _infer_status_counts(status_files: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, Any] = {}
    for info in status_files:
        if info.get("count_confidence") != "high":
            continue
        kind = str(info.get("kind") or "status")
        entry_count = info.get("entry_count")
        if not isinstance(entry_count, int):
            entry_count = info.get("line_count")
        if not isinstance(entry_count, int):
            continue
        if kind == "success":
            counts["completed"] = counts.get("completed", 0) + entry_count
        elif kind == "failure":
            counts["failed"] = counts.get("failed", 0) + entry_count
    if "completed" in counts or "failed" in counts:
        counts["status_file_total"] = counts.get("completed", 0) + counts.get("failed", 0)
    return counts


def _status_count_sources(status_files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    for info in status_files:
        kind = str(info.get("kind") or "status")
        if kind not in {"success", "failure"}:
            continue
        entry_count = info.get("entry_count")
        if not isinstance(entry_count, int):
            entry_count = info.get("line_count")
        if not isinstance(entry_count, int):
            continue
        sources.append(
            {
                "name": info.get("name"),
                "path": info.get("path"),
                "kind": kind,
                "entry_count": entry_count,
                "count_source": info.get("count_source") or "unknown",
                "count_confidence": info.get("count_confidence") or "unknown",
            }
        )
    return sources


def _read_manifest_status_entries(info: Dict[str, Any]) -> List[str]:
    if info.get("count_confidence") != "high":
        return []
    raw_path = str(info.get("path") or "").strip()
    if not raw_path:
        return []
    path = Path(raw_path)
    try:
        if not path.is_file() or path.stat().st_size > MAX_PROFILE_STATUS_FILE_BYTES:
            return []
        entries: List[str] = []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                text = raw_line.strip()
                if text:
                    entries.append(text)
                if len(entries) >= MAX_PROFILE_STATUS_LINES:
                    break
        return entries
    except Exception:
        return []


def _sample_suffix_counts(names: Set[str]) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for name in names:
        if "-" not in name:
            counts["[no-hyphen]"] += 1
            continue
        suffix = name.split("-", 1)[1].strip() or "[empty]"
        counts[suffix] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:MAX_RECONCILIATION_FILE_PATTERNS])


def _normalize_child_file_pattern(sample_name: str, file_name: str) -> str:
    if sample_name and file_name.startswith(sample_name):
        return "<sample>" + file_name[len(sample_name):]
    return file_name


def _summarize_status_directory_structure(root: Path, names: Set[str]) -> Dict[str, Any]:
    sorted_names = sorted(names)
    scan_names = sorted_names[:MAX_RECONCILIATION_STRUCTURE_SCAN_DIRS]
    file_count_distribution: Counter[str] = Counter()
    child_dir_count_distribution: Counter[str] = Counter()
    file_patterns: Counter[str] = Counter()
    scan_errors: List[Dict[str, str]] = []

    for sample_name in scan_names:
        sample_dir = root / sample_name
        try:
            children = list(sample_dir.iterdir())
        except Exception as exc:
            if len(scan_errors) < MAX_RECONCILIATION_EXAMPLES:
                scan_errors.append({"name": sample_name, "error": str(exc)})
            continue
        files = sorted(child.name for child in children if child.is_file())
        child_dirs = [child.name for child in children if child.is_dir()]
        file_count_distribution[str(len(files))] += 1
        child_dir_count_distribution[str(len(child_dirs))] += 1
        for file_name in files:
            file_patterns[_normalize_child_file_pattern(sample_name, file_name)] += 1

    common_patterns = [
        {"pattern": pattern, "count": count}
        for pattern, count in file_patterns.most_common(MAX_RECONCILIATION_FILE_PATTERNS)
    ]
    return {
        "entries_considered": len(sorted_names),
        "directories_scanned": len(scan_names),
        "complete_scan": len(scan_names) == len(sorted_names),
        "scan_limit": MAX_RECONCILIATION_STRUCTURE_SCAN_DIRS,
        "file_count_distribution": dict(sorted(file_count_distribution.items(), key=lambda item: int(item[0]))),
        "child_dir_count_distribution": dict(sorted(child_dir_count_distribution.items(), key=lambda item: int(item[0]))),
        "all_scanned_have_same_file_count": len(file_count_distribution) == 1 and bool(file_count_distribution),
        "common_file_patterns": common_patterns,
        "scan_errors": scan_errors,
    }


def _build_status_reconciliation(
    *,
    root: Path,
    sample_candidate_names: List[str],
    status_files: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    high_confidence_status_files = [
        info
        for info in status_files
        if info.get("count_confidence") == "high" and str(info.get("kind") or "") in {"success", "failure"}
    ]
    if not high_confidence_status_files:
        return None

    sample_set = set(sample_candidate_names)
    entries_by_kind: Dict[str, List[str]] = {"success": [], "failure": []}
    source_summaries: List[Dict[str, Any]] = []
    for info in high_confidence_status_files:
        kind = str(info.get("kind") or "")
        entries = _read_manifest_status_entries(info)
        if kind not in entries_by_kind or not entries:
            continue
        entries_by_kind[kind].extend(entries)
        source_summaries.append(
            {
                "name": info.get("name"),
                "path": info.get("path"),
                "kind": kind,
                "entries": len(entries),
                "unique_entries": len(set(entries)),
            }
        )

    success_counter = Counter(entries_by_kind["success"])
    failure_counter = Counter(entries_by_kind["failure"])
    success_set = set(success_counter)
    failure_set = set(failure_counter)
    status_union = success_set | failure_set
    success_failure_overlap = success_set & failure_set
    duplicate_success = sorted(name for name, count in success_counter.items() if count > 1)
    duplicate_failure = sorted(name for name, count in failure_counter.items() if count > 1)
    status_entries_with_directories = status_union & sample_set
    status_entries_missing_directories = status_union - sample_set
    sample_dirs_without_status = sample_set - status_union

    counts = {
        "sample_candidate_directories": len(sample_set),
        "status_entries_total": len(entries_by_kind["success"]) + len(entries_by_kind["failure"]),
        "status_unique_total": len(status_union),
        "success_entries": len(entries_by_kind["success"]),
        "success_unique": len(success_set),
        "failure_entries": len(entries_by_kind["failure"]),
        "failure_unique": len(failure_set),
        "success_with_directories": len(success_set & sample_set),
        "success_missing_directories": len(success_set - sample_set),
        "failure_with_directories": len(failure_set & sample_set),
        "failure_missing_directories": len(failure_set - sample_set),
        "status_entries_with_directories": len(status_entries_with_directories),
        "status_entries_missing_directories": len(status_entries_missing_directories),
        "sample_dirs_without_status": len(sample_dirs_without_status),
        "success_failure_overlap": len(success_failure_overlap),
        "duplicate_success_entries": len(duplicate_success),
        "duplicate_failure_entries": len(duplicate_failure),
    }

    evidence_relations: List[str] = []
    guidance: List[str] = []
    if counts["status_entries_missing_directories"]:
        evidence_relations.append("status_entries_without_matching_directories")
        guidance.append("Explain status/directory count mismatches using missing matching directories, not inferred retries/reruns unless duplicates or overlaps support that.")
    if counts["sample_dirs_without_status"]:
        evidence_relations.append("directories_without_status_entries")
        guidance.append("Some sample-candidate directories are absent from high-confidence status manifests.")
    if counts["success_failure_overlap"]:
        evidence_relations.append("success_failure_status_overlap")
        guidance.append("Some IDs appear in both success and failure manifests; do not treat status counts as disjoint without noting the overlap.")
    if counts["duplicate_success_entries"] or counts["duplicate_failure_entries"]:
        evidence_relations.append("duplicate_status_entries")
        guidance.append("Duplicate status entries were observed; distinguish line counts from unique IDs.")
    if counts["status_unique_total"] != counts["sample_candidate_directories"]:
        evidence_relations.append("status_unique_total_differs_from_sample_candidate_directories")
    if not counts["success_failure_overlap"] and not counts["duplicate_success_entries"] and not counts["duplicate_failure_entries"]:
        evidence_relations.append("no_duplicate_or_success_failure_overlap_detected")

    reconciliation: Dict[str, Any] = {
        "schema": "file_operations.reconciliation.v1",
        "basis": "high_confidence_manifest_status_files_vs_sample_candidate_directories",
        "counts": counts,
        "sources": source_summaries[:MAX_PROFILE_STATUS_FILES],
        "evidence_relations": evidence_relations,
        "claim_guidance": guidance,
        "examples": {
            "success_missing_directories": sorted(success_set - sample_set)[:MAX_RECONCILIATION_EXAMPLES],
            "failure_missing_directories": sorted(failure_set - sample_set)[:MAX_RECONCILIATION_EXAMPLES],
            "sample_dirs_without_status": sorted(sample_dirs_without_status)[:MAX_RECONCILIATION_EXAMPLES],
            "success_failure_overlap": sorted(success_failure_overlap)[:MAX_RECONCILIATION_EXAMPLES],
            "duplicate_success_entries": duplicate_success[:MAX_RECONCILIATION_EXAMPLES],
            "duplicate_failure_entries": duplicate_failure[:MAX_RECONCILIATION_EXAMPLES],
        },
        "sample_directory_name_profile": {
            "hyphen_suffix_counts": _sample_suffix_counts(sample_set),
        },
    }
    if success_set & sample_set:
        reconciliation["success_directory_structure"] = _summarize_status_directory_structure(
            root,
            success_set & sample_set,
        )
    if failure_set & sample_set:
        reconciliation["failure_directory_structure"] = _summarize_status_directory_structure(
            root,
            failure_set & sample_set,
        )
    return reconciliation


def _build_evidence_envelope(
    *,
    operation: str,
    path: str,
    total_children: int,
    scanned_children: int,
    sample_children: List[Dict[str, Any]],
    status_files: Optional[List[Dict[str, Any]]] = None,
    directory_classification: Optional[Dict[str, Any]] = None,
    reconciliation: Optional[Dict[str, Any]] = None,
    completeness_status: str = "unknown",
    notes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    status_files = status_files or []
    notes = notes or []
    sample_names = [str(item.get("name") or "") for item in sample_children if isinstance(item, dict)]
    envelope: Dict[str, Any] = {
        "schema": "file_operations.evidence.v1",
        "operation": operation,
        "path": path,
        "scope": "direct_children",
        "completeness_status": completeness_status,
        "enumeration": {
            "total_children": total_children,
            "scanned_children": scanned_children,
            "sampled_children": len(sample_children),
            "omitted_children": max(0, total_children - len(sample_children)),
            "complete_enumeration": scanned_children >= total_children,
        },
        "claim_guidance": [],
    }
    if sample_names:
        envelope["sample_names"] = sample_names[:MAX_PROFILE_CHILD_SAMPLE]
    if status_files:
        envelope["status_files"] = [
            {
                "name": item.get("name"),
                "path": item.get("path"),
                "kind": item.get("kind"),
                "line_count": item.get("line_count"),
                "entry_count": item.get("entry_count"),
                "count_source": item.get("count_source"),
                "count_confidence": item.get("count_confidence"),
                "sample_values": item.get("sample_values", [])[:5]
                if isinstance(item.get("sample_values"), list)
                else [],
            }
            for item in status_files[:MAX_PROFILE_STATUS_FILES]
        ]
        sources = _status_count_sources(status_files)
        if sources:
            envelope["status_count_sources"] = sources[:MAX_PROFILE_STATUS_FILES]
        inferred_counts = _infer_status_counts(status_files)
        if inferred_counts:
            envelope["status_counts"] = inferred_counts
            envelope["status_counts_confidence"] = "high"
        else:
            envelope["status_counts_confidence"] = "none"
    if directory_classification:
        envelope["directory_classification"] = directory_classification
    if reconciliation:
        envelope["reconciliation"] = reconciliation
    if notes:
        envelope["notes"] = notes[:8]

    guidance = envelope.get("claim_guidance")
    if not isinstance(guidance, list):
        guidance = []
        envelope["claim_guidance"] = guidance
    if completeness_status in {"partial", "unknown"}:
        guidance.append("Do not claim every/all items succeeded unless a complete manifest or status file supports it.")
    if envelope["enumeration"]["omitted_children"]:
        guidance.append("Listing evidence is sampled/compacted; qualify global claims unless counts/status files support them.")
    if any(str(item.get("kind") or "") == "failure" for item in status_files):
        guidance.append("Failure status files were observed; report them explicitly and avoid all-success claims.")
    if directory_classification and directory_classification.get("status_directories"):
        guidance.append("Direct child count includes status/progress directories; do not report it as verified sample-directory count.")
    raw_status_counts = envelope.get("status_counts")
    status_counts = raw_status_counts if isinstance(raw_status_counts, dict) else {}
    status_total = status_counts.get("status_file_total")
    sample_candidates = directory_classification.get("sample_candidate_directories") if directory_classification else None
    if isinstance(status_total, int) and isinstance(sample_candidates, int) and status_total != sample_candidates:
        guidance.append("Status-file total differs from sample-candidate directory count; reconciliation is required before claiming total samples.")
    if reconciliation:
        for item in reconciliation.get("claim_guidance") or []:
            text = str(item or "").strip()
            if text and text not in guidance:
                guidance.append(text)
    return envelope


async def _profile_file(file_path: str, *, operation: str = "profile", tool_context: Optional[ToolContext] = None) -> Dict[str, Any]:
    """Return a compact profile for a single file when profile/census receives a file path."""
    try:
        is_safe, error_msg = _validate_path_security(file_path, enforce_file_size_limit=True, tool_context=tool_context)
        if not is_safe:
            return {
                "operation": operation,
                "path": file_path,
                "success": False,
                "error": f"Security violation: {error_msg}",
            }
        path = Path(file_path)
        if not path.exists():
            return {"operation": operation, "path": file_path, "success": False, "error": "File not found"}
        if not path.is_file():
            return {"operation": operation, "path": file_path, "success": False, "error": "Path is not a file"}
        stat = path.stat()
        suffix = path.suffix.lower() or "[no extension]"
        evidence_scope = {
            "schema": "file_operations.evidence.v1",
            "operation": operation,
            "path": file_path,
            "scope": "single_file",
            "completeness_status": "file_metadata_only",
            "enumeration": {
                "total_children": 0,
                "scanned_children": 0,
                "sampled_children": 0,
                "omitted_children": 0,
                "complete_enumeration": True,
            },
            "claim_guidance": [
                "This is a file profile, not a directory census; do not infer sibling files or directory completion from it."
            ],
        }
        return {
            "operation": operation,
            "path": file_path,
            "success": True,
            "type": "file",
            "name": path.name,
            "size": stat.st_size,
            "suffix": suffix,
            "modified": stat.st_mtime,
            "evidence_scope": evidence_scope,
            "summary": f"File profile for {file_path}: {stat.st_size} bytes, suffix={suffix}.",
        }
    except Exception as e:
        return {"operation": operation, "path": file_path, "success": False, "error": str(e)}


async def _list_directory(dir_path: str, pattern: Optional[str] = None, tool_context: Optional[ToolContext] = None) -> Dict[str, Any]:
    """List directory contents"""
    try:
        is_safe, error_msg = _validate_path_security(dir_path, tool_context=tool_context)
        if not is_safe:
            return {
                "operation": "list",
                "path": dir_path,
                "success": False,
                "error": f"Security violation: {error_msg}",
            }

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

        evidence_scope = _build_evidence_envelope(
            operation="list",
            path=dir_path,
            total_children=len(items),
            scanned_children=len(items),
            sample_children=items[:MAX_PROFILE_CHILD_SAMPLE],
            completeness_status="complete",
        )
        return {
            "operation": "list",
            "path": dir_path,
            "success": True,
            "items": items,
            "count": len(items),
            "evidence_scope": evidence_scope,
        }

    except Exception as e:
        return {"operation": "list", "path": dir_path, "success": False, "error": str(e)}


async def _profile_directory(
    dir_path: str,
    pattern: Optional[str] = None,
    *,
    operation: str = "profile",
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Build a compact, read-only directory census for evidence-scoped answers."""
    try:
        is_safe, error_msg = _validate_path_security(dir_path, enforce_file_size_limit=True, tool_context=tool_context)
        if not is_safe:
            return {
                "operation": operation,
                "path": dir_path,
                "success": False,
                "error": f"Security violation: {error_msg}",
            }
        path = Path(dir_path)

        if not path.exists():
            return {"operation": operation, "path": dir_path, "success": False, "error": "Directory not found"}

        if not path.is_dir():
            if path.is_file():
                return await _profile_file(dir_path, operation=operation)
            return {"operation": operation, "path": dir_path, "success": False, "error": "Path is not a directory"}

        total_children = 0
        scanned_children = 0
        file_count = 0
        directory_count = 0
        other_count = 0
        status_directory_count = 0
        status_directory_names: List[str] = []
        extension_counts: Counter[str] = Counter()
        sample_children: List[Dict[str, Any]] = []
        incomplete_examples: List[Dict[str, Any]] = []
        notes: List[str] = []

        for item in path.iterdir():
            if pattern and not item.match(pattern):
                continue
            total_children += 1
            if scanned_children >= MAX_PROFILE_SCAN_ENTRIES:
                continue
            scanned_children += 1

            item_type = "directory" if item.is_dir() else "file" if item.is_file() else "other"
            if item_type == "directory":
                directory_count += 1
                if _looks_like_status_directory(item):
                    status_directory_count += 1
                    if len(status_directory_names) < 20:
                        status_directory_names.append(item.name)
            elif item_type == "file":
                file_count += 1
                extension_counts[item.suffix.lower() or "[no extension]"] += 1
            else:
                other_count += 1

            child_info: Dict[str, Any] = {
                "name": item.name,
                "path": str(item),
                "type": item_type,
            }
            if item_type == "file":
                try:
                    child_info["size"] = item.stat().st_size
                except Exception:
                    pass
            elif item_type == "directory":
                is_status_dir = _looks_like_status_directory(item)
                child_info["directory_role"] = "status_or_progress" if is_status_dir else "sample_candidate"
                try:
                    child_names = [child.name for child in item.iterdir()]
                    child_info["child_count"] = len(child_names)
                    lower_names = [name.lower() for name in child_names]
                    if not child_names:
                        child_info["weak_signal"] = "empty_directory"
                    elif any("fail" in name or "error" in name for name in lower_names):
                        child_info["weak_signal"] = "contains_failure_named_child"
                except Exception as exc:
                    child_info["child_count_error"] = str(exc)
            if len(sample_children) < MAX_PROFILE_CHILD_SAMPLE:
                sample_children.append(child_info)

        if total_children > scanned_children:
            notes.append(f"Scanned first {scanned_children} of {total_children} matching direct children.")

        status_files = _collect_status_files(path)
        status_counts = _infer_status_counts(status_files)
        failed_count = status_counts.get("failed") if isinstance(status_counts.get("failed"), int) else 0
        completed_count = status_counts.get("completed") if isinstance(status_counts.get("completed"), int) else 0

        if total_children == scanned_children and status_files:
            completeness_status = "complete_with_status_files"
        elif total_children == scanned_children:
            completeness_status = "complete_enumeration_only"
        else:
            completeness_status = "partial"

        sample_candidate_directories = max(0, directory_count - status_directory_count)
        status_dir_names_set = set(status_directory_names)
        sample_candidate_names = [
            item.get("name")
            for item in sample_children
            if isinstance(item, dict)
            and item.get("type") == "directory"
            and item.get("name") not in status_dir_names_set
        ]
        if scanned_children == total_children:
            sample_candidate_names = []
            try:
                for child in path.iterdir():
                    if pattern and not child.match(pattern):
                        continue
                    if child.is_dir() and child.name not in status_dir_names_set:
                        sample_candidate_names.append(child.name)
            except Exception:
                pass
        directory_classification = {
            "directories": directory_count,
            "sample_candidate_directories": sample_candidate_directories,
            "status_directories": status_directory_count,
            "status_directory_names": status_directory_names,
            "sample_candidate_names_sample": sorted(str(name) for name in sample_candidate_names if name)[:MAX_PROFILE_CHILD_SAMPLE],
            "classification_basis": "name/status-file heuristic",
        }
        reconciliation = _build_status_reconciliation(
            root=path,
            sample_candidate_names=[str(name) for name in sample_candidate_names if name],
            status_files=status_files,
        )

        evidence_scope = _build_evidence_envelope(
            operation=operation,
            path=dir_path,
            total_children=total_children,
            scanned_children=scanned_children,
            sample_children=sample_children,
            status_files=status_files,
            directory_classification=directory_classification,
            reconciliation=reconciliation,
            completeness_status=completeness_status,
            notes=notes,
        )

        counts = {
            "direct_children": total_children,
            "scanned_children": scanned_children,
            "files": file_count,
            "directories": directory_count,
            "sample_candidate_directories": sample_candidate_directories,
            "status_directories": status_directory_count,
            "other": other_count,
        }
        if status_counts:
            counts.update(status_counts)

        summary_parts = [
            f"Directory profile for {dir_path}: {total_children} direct child item(s)",
            f"{directory_count} directories, {file_count} files",
        ]
        if status_directory_count:
            summary_parts.append(
                f"sample-candidate directories={sample_candidate_directories}, status/progress directories={status_directory_count}"
            )
        if completed_count or failed_count:
            summary_parts.append(f"status files indicate completed={completed_count}, failed={failed_count}")
        if reconciliation:
            raw_rec_counts = reconciliation.get("counts")
            rec_counts = raw_rec_counts if isinstance(raw_rec_counts, dict) else {}
            missing = rec_counts.get("status_entries_missing_directories")
            if isinstance(missing, int) and missing:
                summary_parts.append(f"reconciliation shows {missing} status ID(s) without matching sample-candidate directories")
        return {
            "operation": operation,
            "path": dir_path,
            "success": True,
            "counts": counts,
            "extension_counts": dict(extension_counts.most_common(20)),
            "status_files": status_files,
            "status_count_sources": _status_count_sources(status_files),
            "reconciliation": reconciliation,
            "status_counts_confidence": "high" if status_counts else "none",
            "incomplete_examples": incomplete_examples[:20],
            "sample_items": sample_children,
            "evidence_scope": evidence_scope,
            "completeness_status": completeness_status,
            "summary": "; ".join(summary_parts) + ".",
        }

    except Exception as e:
        return {"operation": operation, "path": dir_path, "success": False, "error": str(e)}


async def _delete_path(target_path: str, tool_context: Optional[ToolContext] = None) -> Dict[str, Any]:
    """Delete file or directory with security validation"""
    try:
        is_safe, error_msg = _validate_path_security(target_path, tool_context=tool_context)
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


async def _copy_path(source: str, destination: str, tool_context: Optional[ToolContext] = None) -> Dict[str, Any]:
    """Copy file or directory with security validation"""
    try:
        # Security validation for both paths
        is_safe_src, error_msg_src = _validate_path_security(source, tool_context=tool_context)
        if not is_safe_src:
            return {
                "operation": "copy",
                "source": source,
                "destination": destination,
                "success": False,
                "error": f"Source security violation: {error_msg_src}",
            }
        is_size_safe, size_error = _validate_copy_move_size(source)
        if not is_size_safe:
            return {
                "operation": "copy",
                "source": source,
                "destination": destination,
                "success": False,
                "error": f"Source security violation: {size_error}",
            }

        is_safe_dest, error_msg_dest = _validate_path_security(destination, tool_context=tool_context)
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


async def _move_path(source: str, destination: str, tool_context: Optional[ToolContext] = None) -> Dict[str, Any]:
    """Move file or directory with security validation"""
    try:
        # Security validation for both paths
        is_safe_src, error_msg_src = _validate_path_security(source, tool_context=tool_context)
        if not is_safe_src:
            return {
                "operation": "move",
                "source": source,
                "destination": destination,
                "success": False,
                "error": f"Source security violation: {error_msg_src}",
            }
        is_size_safe, size_error = _validate_copy_move_size(source)
        if not is_size_safe:
            return {
                "operation": "move",
                "source": source,
                "destination": destination,
                "success": False,
                "error": f"Source security violation: {size_error}",
            }

        is_safe_dest, error_msg_dest = _validate_path_security(destination, tool_context=tool_context)
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


async def _check_exists(target_path: str, tool_context: Optional[ToolContext] = None) -> Dict[str, Any]:
    """Check if path exists"""
    is_safe, error_msg = _validate_path_security(target_path, tool_context=tool_context)
    if not is_safe:
        return {
            "operation": "exists",
            "path": target_path,
            "success": False,
            "error": f"Security violation: {error_msg}",
        }
    path = Path(target_path)
    return {
        "operation": "exists",
        "path": target_path,
        "exists": path.exists(),
        "type": "directory" if path.is_dir() else "file" if path.is_file() else "unknown",
    }


async def _get_file_info(target_path: str, tool_context: Optional[ToolContext] = None) -> Dict[str, Any]:
    """Get file/directory information"""
    try:
        is_safe, error_msg = _validate_path_security(target_path, tool_context=tool_context)
        if not is_safe:
            return {
                "operation": "info",
                "path": target_path,
                "success": False,
                "error": f"Security violation: {error_msg}",
            }

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
    "description": "Perform file system operations (read, write, list, profile/census, delete, copy, move, etc.). Use profile/census for compact directory-wide evidence before making all/every/completed claims. In task execution, prefer relative output paths so writes land in the current task output directory.",
    "category": "file_management",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": "Operation type; profile/census accept directories and return metadata for single files.",
                "enum": ["read", "write", "list", "profile", "census", "delete", "copy", "move", "exists", "info"],
            },
            "path": {"type": "string", "description": "Target file or directory path. Prefer relative paths or bare filenames for task outputs; avoid absolute session workspace paths for final deliverables."},
            "content": {"type": "string", "description": "Content to write (required for write operation)"},
            "destination": {"type": "string", "description": "Destination path (required for copy/move operations). Prefer task-relative destinations for final outputs."},
            "pattern": {"type": "string", "description": "File matching pattern (optional for list/profile/census operation)"},
        },
        "required": ["operation", "path"],
    },
    "handler": file_operations_handler,
    "tags": ["file", "filesystem", "read", "write", "management"],
    "examples": ["Read configuration file", "Create new document", "List directory contents", "Profile a directory before global completion claims", "Copy file to backup directory"],
}
