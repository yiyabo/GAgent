"""Path/text/blocked-dependency helpers for the plan executor.

Moved verbatim out of ``plan_executor.py`` per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.6 (cluster ②):
the sync coroutine bridge, job logging shim, the ``BLOCKED_DEPENDENCY``
text protocol, recoverable-output evidence detection, path-like extraction
and its runtime-path regexes.  ``plan_executor.py`` re-exports every name,
so the facade namespace external callers and tests use is unchanged.

Only sanctioned deviation: ``logger`` is this module's own logger (the
``deep_think``/``deliverables`` split precedent) — log messages and levels
are byte-identical, only the record's ``name`` field differs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...llm import close_current_loop_async_client

logger = logging.getLogger(__name__)


def _run_coroutine_sync(coro: Any) -> Any:
    """Run an async coroutine synchronously, handling nested event loops.

    When called from a thread that already has a running event loop (e.g. via
    asyncio.to_thread), this spawns a new thread with its own event loop to
    avoid "cannot run nested event loop" errors.  Any async LLM HTTP client
    created inside that temporary loop is closed before ``asyncio.run`` closes
    the loop, avoiding cross-loop reuse of httpx/anyio primitives.
    """
    import contextvars

    async def _run_and_cleanup() -> Any:
        try:
            return await coro
        finally:
            try:
                await close_current_loop_async_client()
            except Exception as exc:
                logger.warning("Failed to close temporary loop LLM client: %s", exc)

    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop and running_loop.is_running():
        ctx = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: ctx.run(asyncio.run, _run_and_cleanup())).result()
    return asyncio.run(_run_and_cleanup())


def _log_job(level: str, message: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    try:
        from .decomposition_jobs import log_job_event
    except Exception:  # pragma: no cover - defensive
        return
    log_job_event(level, message, metadata)


_BLOCKED_DEPENDENCY_MARKER_RE = re.compile(
    r"(?:^|\b)(?:STATUS\s*:\s*)?BLOCKED_DEPENDENCY(?:\b|$)",
    re.IGNORECASE,
)


_PRIMARY_EXECUTION_TOOLS = {
    "code_executor",
    "bio_tools",
    "literature_pipeline",
    "manuscript_writer",
    "review_pack_writer",
}


def _extract_blocked_dependency_detail(text: str) -> Optional[str]:
    raw = str(text or "")
    if not _BLOCKED_DEPENDENCY_MARKER_RE.search(raw):
        return None

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("detail:"):
            detail = stripped.split(":", 1)[1].strip()
            if detail:
                return detail

    compact = " ".join(part.strip() for part in raw.splitlines() if part.strip())
    return compact[:500] if compact else "Blocked by missing prerequisite data or upstream artifacts."


def _deep_think_has_failed_primary_execution_tool(result: Any) -> bool:
    failures = getattr(result, "tool_failures", None)
    if not isinstance(failures, list):
        return False
    for failure in failures:
        if not isinstance(failure, dict):
            continue
        tool = str(failure.get("tool") or failure.get("tool_name") or "").strip().lower()
        if tool in _PRIMARY_EXECUTION_TOOLS and failure.get("success") is False:
            return True
    return False


def _path_points_to_recoverable_output(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    try:
        candidate = Path(text).expanduser()
        if candidate.is_file():
            return candidate.stat().st_size >= 0
        if candidate.is_dir():
            return any(candidate.iterdir())
    except OSError:
        return False
    return False


def _has_recoverable_output_evidence(*values: Any) -> bool:
    output_keys = {
        "artifact_paths",
        "session_artifact_paths",
        "produced_files",
        "contract_artifacts",
        "deliverables",
        "output_data",
        "run_directory",
        "working_directory",
        "task_directory_full",
        "task_root_directory",
        "results_directory",
        "work_dir",
        "run_dir",
    }

    def _visit(value: Any, *, key: Optional[str] = None) -> bool:
        if value is None:
            return False
        lowered_key = str(key or "").strip().lower()
        if isinstance(value, str):
            if lowered_key in output_keys or lowered_key.endswith("_path") or lowered_key.endswith("_file") or lowered_key.endswith("_dir"):
                return _path_points_to_recoverable_output(value)
            return False
        if isinstance(value, dict):
            if lowered_key == "deliverables" and value:
                manifest_path = value.get("manifest_path")
                if _path_points_to_recoverable_output(manifest_path):
                    return True
                modules = value.get("published_modules")
                if isinstance(modules, list) and modules:
                    return True
            if lowered_key == "contract_artifacts" or value.get("exists") is True:
                path = value.get("path")
                if _path_points_to_recoverable_output(path):
                    return True
            for item_key, item_value in value.items():
                if _visit(item_value, key=str(item_key)):
                    return True
            return False
        if isinstance(value, (list, tuple, set)):
            return any(_visit(item, key=key) for item in value)
        return False

    return any(_visit(value) for value in values)


def _coerce_blocked_dependency_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    text_parts: List[str] = []
    for key in ("content", "stdout", "stderr", "error", "error_summary"):
        value = payload.get(key)
        if value:
            text_parts.append(str(value))

    notes = payload.get("notes")
    if isinstance(notes, list):
        text_parts.extend(str(item) for item in notes if item is not None)
    elif notes:
        text_parts.append(str(notes))

    output_data = payload.get("output_data")
    if isinstance(output_data, list):
        for item in output_data:
            if isinstance(item, dict):
                for key in ("result", "content", "text"):
                    value = item.get(key)
                    if value:
                        text_parts.append(str(value))
            elif item is not None:
                text_parts.append(str(item))

    detail = _extract_blocked_dependency_detail("\n".join(text_parts))
    if not detail:
        return payload, "completed"
    coerced = dict(payload)
    raw_metadata = coerced.get("metadata")
    metadata: Dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    metadata["blocked_by_dependencies"] = True
    metadata["blocked_dependency_reported_by_task"] = True
    metadata["blocked_dependency_detail"] = detail
    coerced["metadata"] = metadata
    coerced["status"] = "skipped"

    existing_notes = coerced.get("notes")
    normalized_notes = list(existing_notes) if isinstance(existing_notes, list) else []
    note = "Task reported BLOCKED_DEPENDENCY; execution was not treated as a contract failure."
    if note not in normalized_notes:
        normalized_notes.append(note)
    coerced["notes"] = normalized_notes
    return coerced, "skipped"


def _summarize_tool_params(params: Optional[Dict[str, Any]], max_length: int = 200) -> str:
    """Create a brief summary of tool parameters for logging."""
    if not params:
        return "(no parameters)"
    summary = json.dumps(params, ensure_ascii=False)
    if len(summary) > max_length:
        return summary[:max_length] + "..."
    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATH_LIKE_RE = re.compile(r"(/[a-zA-Z0-9_./-]{8,})")
_NON_DELIVERABLE_WORKSPACE_RE = re.compile(
    r"/runtime/session_[^/]+/(?:_scratch/)?plan\d+_task\d+/run_[^/]+(?:/(?:results|code|data|docs))?$",
    re.IGNORECASE,
)
_LEGACY_SESSION_WORKSPACE_RE = re.compile(
    r"(?:^|/)runtime/session_current/workspace(?:/.*)?$",
    re.IGNORECASE,
)
_USELESS_RUNTIME_ROOT_RE = re.compile(r"(?:^|/)runtime/?$", re.IGNORECASE)


def _is_non_canonical_runtime_path(path: str) -> bool:
    normalized = "/" + str(path or "").strip().replace("\\", "/").lstrip("/")
    if not normalized or normalized == "/":
        return False
    return bool(
        _LEGACY_SESSION_WORKSPACE_RE.search(normalized)
        or _USELESS_RUNTIME_ROOT_RE.search(normalized)
    )


def _extract_paths_from_execution_result(raw: str, max_paths: int = 40) -> List[str]:
    """Extract absolute file paths from an execution result string."""
    if not raw:
        return []
    try:
        payload = json.loads(raw) if raw.strip().startswith("{") else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}

    paths: List[str] = []
    seen: set[str] = set()

    # Structured fields first
    for key in ("artifact_paths", "produced_files", "session_artifact_paths"):
        items = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(items, list):
            for p in items:
                text = str(p).strip()
                if (
                    text.startswith("/")
                    and not _is_non_canonical_runtime_path(text)
                    and text not in seen
                ):
                    seen.add(text)
                    paths.append(text)

    # Regex fallback on raw text
    if len(paths) < max_paths:
        for m in _PATH_LIKE_RE.finditer(raw):
            p = m.group(1)
            if _is_non_canonical_runtime_path(p):
                continue
            if p not in seen:
                seen.add(p)
                paths.append(p)
            if len(paths) >= max_paths:
                break

    return paths[:max_paths]
