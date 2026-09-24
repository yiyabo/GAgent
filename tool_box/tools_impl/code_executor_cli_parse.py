"""CLI output parsing for code_executor: JSONL transcripts, stderr, errors.

Extracted from ``code_executor.py`` (cluster C4) per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.1. This sibling owns
the task-subdirectory formatting helpers, the qwen truncated-tool-failure
detectors, the stderr line partitioning (debug-log path extraction), the
unbounded stream-line iterator, the JSONL result/deliverables/summary
extractors, and the readable-error builders.

Compatibility contract (gating.py pattern): the ``code_executor`` facade
re-exports every name defined here; test and production import sites keep
working unchanged. No cross-cluster calls into facade-resident names exist
in this module.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

from app.services.plans.acceptance_criteria import derive_relative_output_dirs

_DEFAULT_TASK_SUBDIRECTORIES = ("results", "code", "data", "docs")

_QWEN_DEBUG_ENABLED_LINE_RE = re.compile(
    r"^(?:\[[^\]]+\]\s*)?Debug mode enabled(?:\s+Logging to:\s*(?P<path>\S+))?\s*$",
    re.IGNORECASE,
)
_QWEN_LOGGING_TO_LINE_RE = re.compile(
    r"^(?:\[[^\]]+\]\s*)?Logging to:\s*(?P<path>\S+)\s*$",
    re.IGNORECASE,
)


def _derive_task_subdirectories(
    execution_spec: Optional[Dict[str, Any]],
) -> List[str]:
    criteria = execution_spec.get("acceptance_criteria") if isinstance(execution_spec, dict) else None
    return derive_relative_output_dirs(
        criteria,
        default_dirs=_DEFAULT_TASK_SUBDIRECTORIES,
    )


def _format_task_subdirectories(subdirs: Sequence[str]) -> str:
    return " ".join(f"{name}/" for name in subdirs)


def _format_directory_choices(subdirs: Sequence[str]) -> str:
    items = [f"{name}/" for name in subdirs if name]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return f"{', '.join(items[:-1])}, or {items[-1]}"


def _compact_cli_text(value: Optional[str], *, limit: int = 320) -> str:
    text = " ".join((value or "").split()).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _is_qwen_truncated_tool_failure_text(text: Any) -> bool:
    normalized = str(text or "").lower()
    if not normalized:
        return False
    fatal_terms = (
        "previous response was truncated due to max_tokens limit",
        "tool call has been rejected to prevent writing truncated content",
        "must split the content into smaller parts",
    )
    if any(term in normalized for term in fatal_terms):
        return True
    return "error executing tool write_file" in normalized and "truncated" in normalized


def _qwen_truncated_tool_failure_note(source: str = "debug log") -> str:
    return (
        "[QWEN_TOOL_CALL_TRUNCATED] qwen_tool_call_truncated: "
        f"Qwen Code reported a truncated/rejected tool call in {source}; "
        "the current attempt cannot complete without recovery"
    )


def _partition_cli_stderr_lines(stderr: str) -> tuple[List[str], str]:
    actionable_lines: List[str] = []
    debug_log_path = ""

    for raw_line in stderr.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = _QWEN_DEBUG_ENABLED_LINE_RE.match(line)
        if match:
            maybe_path = str(match.group("path") or "").strip()
            if maybe_path:
                debug_log_path = maybe_path
            continue

        match = _QWEN_LOGGING_TO_LINE_RE.match(line)
        if match:
            maybe_path = str(match.group("path") or "").strip()
            if maybe_path:
                debug_log_path = maybe_path
            continue

        actionable_lines.append(line)

    return actionable_lines, debug_log_path


async def _iter_stream_lines_unbounded(
    stream: asyncio.StreamReader,
    *,
    chunk_size: int = 65536,
) -> AsyncIterator[str]:
    """Yield decoded lines without relying on StreamReader.readline limits."""

    pending = ""
    while True:
        chunk = await stream.read(chunk_size)
        if not chunk:
            break
        pending += chunk.decode(errors="replace")
        while True:
            newline_index = pending.find("\n")
            if newline_index < 0:
                break
            line = pending[:newline_index]
            if line.endswith("\r"):
                line = line[:-1]
            yield line
            pending = pending[newline_index + 1 :]

    if pending:
        if pending.endswith("\r"):
            pending = pending[:-1]
        yield pending


def _extract_result_from_jsonl(stdout: str) -> Optional[str]:
    """Extract the clean JSON response from qwen_code JSONL session transcript.

    The qwen CLI outputs JSONL (one JSON event per line). The final assistant
    message typically contains a fenced ```json block with the task result.
    This function parses the JSONL, finds the last assistant message, and
    extracts that JSON block.
    """
    if not stdout or not stdout.strip():
        return None
    lines = stdout.strip().split("\n")
    last_assistant_text = None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").lower()
        if event_type != "assistant":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = str(part.get("text") or "")
                    if text.strip():
                        last_assistant_text = text
                        break
        elif isinstance(content, str) and content.strip():
            last_assistant_text = content
        if last_assistant_text:
            break
    if not last_assistant_text:
        return None
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", last_assistant_text, re.DOTALL)
    if fence_match:
        json_text = fence_match.group(1).strip()
        try:
            parsed = json.loads(json_text)
            if isinstance(parsed, dict):
                return _build_summary_from_parsed_json(parsed)
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _extract_deliverables_from_jsonl(stdout: str) -> List[Dict[str, Any]]:
    """Extract files marked as deliverables from qwen_code JSONL session transcript.

    Parses the JSONL, finds the last assistant message with a fenced JSON block,
    and extracts produced_files entries where deliverable=true.

    Returns:
        List of dicts with keys: path, module, description
    """
    if not stdout or not stdout.strip():
        return []

    lines = stdout.strip().split("\n")
    last_assistant_text = None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").lower()
        if event_type != "assistant":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = str(part.get("text") or "")
                    if text.strip():
                        last_assistant_text = text
                        break
        elif isinstance(content, str) and content.strip():
            last_assistant_text = content
        if last_assistant_text:
            break

    if not last_assistant_text:
        return []

    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", last_assistant_text, re.DOTALL)
    if not fence_match:
        return []

    json_text = fence_match.group(1).strip()
    try:
        parsed = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return []

    if not isinstance(parsed, dict):
        return []

    produced_files = parsed.get("produced_files") or []
    if not isinstance(produced_files, list):
        return []

    deliverables = []
    for item in produced_files:
        if not isinstance(item, dict):
            continue
        if not item.get("deliverable"):
            continue

        path = str(item.get("path") or "").strip()
        module = str(item.get("module") or "").strip().lower()
        description = str(item.get("description") or "").strip()

        if not path or not module:
            continue

        deliverables.append({
            "path": path,
            "module": module,
            "description": description,
        })

    return deliverables


def _build_summary_from_parsed_json(parsed: dict) -> Optional[str]:
    """Build a meaningful summary from the qwen agent's parsed JSON response.

    When the agent's own summary field is too short (e.g. just "completed"),
    enrich it with produced_files and acceptance_check details.
    """
    summary = str(parsed.get("summary") or "").strip()
    produced = parsed.get("produced_files") or []
    acceptance = parsed.get("acceptance_check") or {}
    notes = str(acceptance.get("notes") or "").strip()

    if len(summary) >= 30:
        return summary

    parts = []
    status = str(parsed.get("status") or "").strip().lower()
    if status and status not in ("completed", "success"):
        parts.append(f"Status: {status}")

    file_names = []
    for f in produced[:5]:
        if isinstance(f, dict):
            path = str(f.get("path") or "")
        elif isinstance(f, str):
            path = f
        else:
            continue
        name = path.rsplit("/", 1)[-1] if "/" in path else path
        if name:
            file_names.append(name)
    if file_names:
        parts.append(f"Produced: {', '.join(file_names)}")

    if notes and len(notes) <= 200:
        parts.append(notes)

    if parts:
        return " | ".join(parts)
    return summary or None


def _extract_readable_error(stderr: str) -> str:
    """Extract a human-readable error from CLI stderr.

    When the CLI crashes, stderr may contain a minified JS stack trace that is
    useless for debugging.  This function detects that pattern and produces a
    concise summary instead.
    """
    if not stderr or not stderr.strip():
        return ""

    lines, _debug_log_path = _partition_cli_stderr_lines(stderr)
    if not lines:
        return ""

    # 1. Detect known structured error messages first.
    for line in lines:
        lower = line.lower()
        if "cannot be launched inside another claude code session" in lower:
            return "Nested Claude Code session detected. Unset the CLAUDECODE env var."
        if "error:" in lower and len(line) < 300:
            return line

    # 2. Detect minified JavaScript dump (CLI crash).
    joined = " ".join(lines)
    is_minified_js = (
        "cli.js:" in joined
        and any(kw in joined for kw in (
            "function(", "var ", "Object.defineProperty",
            "exports.", "DefaultTransporter", "status>=400",
        ))
    )
    if is_minified_js:
        # Try to extract HTTP status hint from the minified code context.
        status_match = re.search(r'status[>=]+\s*(\d{3})', joined)
        if status_match:
            status_code = status_match.group(1)
            if status_code in {"401", "403"}:
                return (
                    f"Claude CLI crashed (HTTP {status_code} from upstream Anthropic-compatible API). "
                    "Check provider credentials and authorization settings."
                )
            if status_code == "429":
                return (
                    "Claude CLI crashed (HTTP 429 from upstream Anthropic-compatible API). "
                    "The provider likely rate-limited the request."
                )
            if status_code == "400":
                return (
                    "Claude CLI crashed (HTTP 400 from upstream Anthropic-compatible API). "
                    "The upstream rejected the request; this is not necessarily a local API-key/base-URL problem."
                )
            return (
                f"Claude CLI crashed (HTTP {status_code} from upstream Anthropic-compatible API). "
                "Check provider debug logs and request compatibility."
            )
        return (
            "Claude CLI crashed with an unhandled JS exception. "
            "This usually indicates an API connectivity or authentication error."
        )

    # 3. Fallback: truncate to a readable length.
    return _compact_cli_text(joined, limit=360)


def _extract_qwen_debug_log_path(stderr: str) -> str:
    if not stderr or not stderr.strip():
        return ""
    _lines, debug_log_path = _partition_cli_stderr_lines(stderr)
    return debug_log_path
