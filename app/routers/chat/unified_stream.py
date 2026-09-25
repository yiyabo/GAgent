"""Shared helpers for ``StructuredChatAgent.process_unified_stream`` (W5c).

The 1856-line unified stream is cut into phases by W5c.  This module holds the
pieces that are pure or parameter-light, so the method (and its closures) keep
only what really needs ``self`` and the stream's mutable local state.

Currently extracted: the progress/tool-callback helper family that the
``_emit_progress_status`` / ``on_thinking`` / ``on_tool_start`` closures call
(they were inner functions of the method and never module-level names of
``agent``, so nothing is re-exported by the facade).

Deviation: ``_progress_label_from_phase`` closed over the method's
``reasoning_language`` local; it now takes ``language`` explicitly (the three
call sites pass ``language=reasoning_language``), which is a signature-only
change — the mapping tables and every returned string are byte-identical.

Patch surface: none of these helpers reads a patched ``agent`` binding, and none
of them is patched by any test (they were never importable before).  No logger is
used here.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

from app.services.deep_think_agent import ThinkingStep


def _progress_label_from_phase(phase: str, *, language: str) -> str:
    if language == "zh":
        mapping = {
            "planning": "分析请求中",
            "gathering": "检索资料中",
            "analyzing": "整理候选方向中",
            "synthesizing": "汇总结论中",
            "finalizing": "生成最终答复中",
        }
    else:
        mapping = {
            "planning": "Planning the response",
            "gathering": "Gathering evidence",
            "analyzing": "Analyzing findings",
            "synthesizing": "Synthesizing conclusions",
            "finalizing": "Preparing the final answer",
        }
    return mapping.get(phase, mapping["analyzing"])


def _normalize_progress_text(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _truncate_progress_text(text: Optional[str], max_chars: int = 72) -> str:
    normalized = _normalize_progress_text(text)
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 1].rstrip()}…"


def _tool_progress_details(
    tool_name: str, params: Optional[Dict[str, Any]]
) -> Optional[str]:
    params = params if isinstance(params, dict) else {}
    lowered = (tool_name or "").strip().lower()
    if lowered == "web_search":
        query = _normalize_progress_text(params.get("query"))
        return query or None
    if lowered == "literature_pipeline":
        topic = _normalize_progress_text(
            params.get("topic") or params.get("query") or params.get("question")
        )
        return topic or None
    if lowered == "document_reader":
        path = _normalize_progress_text(
            params.get("path") or params.get("file_path")
        )
        return path or None
    if lowered == "file_operations":
        target = _normalize_progress_text(
            params.get("path")
            or params.get("target")
            or params.get("file_path")
        )
        return target or None
    return None


def _extract_tool_context(action_raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not action_raw:
        return None, None
    try:
        parsed = json.loads(action_raw)
    except Exception:
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    tool_name = str(parsed.get("tool") or "").strip() or None
    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    return tool_name, _tool_progress_details(tool_name or "", params)


def _progress_phase_from_step(step: ThinkingStep) -> str:
    if step.status == "calling_tool" or step.action:
        return "gathering"
    if step.status == "done":
        return "finalizing"
    if step.status == "analyzing":
        return "synthesizing"
    if step.iteration <= 1:
        return "planning"
    return "analyzing"
