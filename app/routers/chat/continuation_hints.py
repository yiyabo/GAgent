"""Continuation-hint cluster of ``agent`` (W5a cluster ①).

Moved out of ``agent.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.8 (module-level cluster
① ``continuation_hints.py``): the filename/absolute-path recognizers and the
helpers that build the brief "where were we" continuation summary for a
follow-up ``execute`` turn (previous request/answer excerpts, known paths and
filenames, image anchors, latest tool result, last failure).  ``agent.py``
re-exports every name, so the class call sites
(``_build_brief_execute_continuation_summary``,
``_current_user_turn_index_from_history``) and
app/tests/chat/test_prompt_policy_consistency.py's direct import are unchanged.

Patch surface: **zero body deviations**.  None of these names is patched in
``app/`` or ``app/tests/``, and the one facade alias the cluster read
(``_extract_declared_absolute_paths_fn``, the facade's alias of
``guardrails.extract_declared_absolute_paths``) is imported here directly from
that source module under the same alias name, so every call expression stays
verbatim.  No patched binding (`execute_tool`, `plan_decomposition_jobs`, job
triple, ...) is touched.

No logger is used in this cluster; every summary key, limit and regular
expression is byte-identical.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from .guardrails import extract_declared_absolute_paths as _extract_declared_absolute_paths_fn

_CONTINUATION_FILENAME_RE = re.compile(
    r"(?<![A-Za-z0-9_/.-])([A-Za-z0-9][A-Za-z0-9_.-]{1,120}\.(?:tsv|csv|txt|json|ya?ml|gff3?|fa|fasta|faa|fna|fastq|fq|md|pdf|png|jpe?g|svg|xlsx?|zip|gz|tar))",
    flags=re.IGNORECASE,
)
_REAL_ABSOLUTE_PATH_PREFIXES = (
    "/Users/",
    "/home/",
    "/tmp/",
    "/var/",
    "/opt/",
    "/private/",
    "/Volumes/",
    "/etc/",
    "/dev/",
    "/mnt/",
    "/srv/",
    "/root/",
    "/workspace/",
    "/workspaces/",
    "/data/",
)
_LOW_SIGNAL_CONTINUATION_FILENAMES = {
    "result.json",
    "manifest.json",
    "preview.json",
}


def _current_user_turn_index_from_history(
    history: Optional[List[Dict[str, Any]]],
) -> int:
    if not history:
        return 1
    return 1 + sum(
        1
        for item in history
        if str(item.get("role") or "").strip().lower() == "user"
    )


def _is_brief_execute_followup_request(
    routing_decision: Any,
) -> bool:
    if routing_decision is None:
        return False
    request_tier = str(getattr(routing_decision, "request_tier", "") or "").strip().lower()
    brevity_hint = bool(getattr(routing_decision, "brevity_hint", False))
    return request_tier == "execute" and brevity_hint


def _clip_continuation_text(value: Any, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _append_unique_hint(target: List[str], seen: set[str], value: Any, *, limit: int = 6) -> None:
    text = str(value or "").strip()
    if not text:
        return
    normalized = text.lower()
    if normalized in seen:
        return
    seen.add(normalized)
    target.append(text)
    if len(target) > limit:
        del target[limit:]


def _looks_like_real_absolute_path(value: Any) -> bool:
    path = str(value or "").strip()
    if not path.startswith("/"):
        return False
    return any(path.startswith(prefix) for prefix in _REAL_ABSOLUTE_PATH_PREFIXES)


def _path_hint_priority(path: str) -> int:
    normalized = str(path or "").strip().lower()
    basename = os.path.basename(normalized)
    score = 0
    if "/runtime/" not in normalized:
        score += 4
    else:
        score -= 3
    if any(marker in normalized for marker in ("/phagescope/", "/data/", "/paper/", "/results/")):
        score += 4
    if "." in basename:
        score += 2
    if basename in _LOW_SIGNAL_CONTINUATION_FILENAMES:
        score -= 6
    return score


def _extract_recent_path_and_filename_hints(
    history: Optional[List[Dict[str, Any]]],
    recent_tool_results: Any,
    active_subject: Any,
) -> Dict[str, List[str]]:
    paths: List[str] = []
    filenames: List[str] = []
    seen_paths: set[str] = set()
    seen_filenames: set[str] = set()

    def _collect_text_hints(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        for path in _extract_declared_absolute_paths_fn(text):
            if not _looks_like_real_absolute_path(path):
                continue
            _append_unique_hint(paths, seen_paths, path, limit=6)
            basename = os.path.basename(path)
            if basename and "." in basename and basename.lower() not in _LOW_SIGNAL_CONTINUATION_FILENAMES:
                _append_unique_hint(filenames, seen_filenames, basename, limit=6)
        for match in _CONTINUATION_FILENAME_RE.findall(text):
            filename = str(match or "").strip()
            if not filename:
                continue
            if "/" in filename:
                continue
            if filename.lower() in _LOW_SIGNAL_CONTINUATION_FILENAMES:
                continue
            _append_unique_hint(filenames, seen_filenames, filename, limit=6)

    if isinstance(active_subject, dict):
        for key in ("canonical_ref", "display_ref"):
            value = str(active_subject.get(key) or "").strip()
            if not value:
                continue
            if _looks_like_real_absolute_path(value):
                _append_unique_hint(paths, seen_paths, value, limit=6)
            else:
                _collect_text_hints(value)

    if isinstance(history, list):
        for item in reversed(history):
            if not isinstance(item, dict):
                continue
            _collect_text_hints(item.get("content"))

    if isinstance(recent_tool_results, list):
        for item in reversed(recent_tool_results):
            if not isinstance(item, dict):
                continue
            _collect_text_hints(item.get("summary"))
            result_payload = item.get("result")
            if result_payload is None:
                continue
            try:
                serialized = json.dumps(result_payload, ensure_ascii=False, default=str)
            except Exception:
                serialized = str(result_payload)
            _collect_text_hints(serialized)

    ranked_paths = sorted(paths, key=_path_hint_priority, reverse=True)

    return {
        "known_paths": ranked_paths[:4],
        "known_filenames": filenames[:4],
    }


def _build_brief_execute_continuation_summary(
    agent: Any,
    routing_decision: Any,
) -> Optional[Dict[str, Any]]:
    if not _is_brief_execute_followup_request(routing_decision):
        return None

    history = getattr(agent, "history", None) or []
    extra_context = getattr(agent, "extra_context", {}) or {}
    previous_user_request = ""
    previous_assistant_summary = ""
    if isinstance(history, list):
        for item in reversed(history):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            if role == "assistant" and not previous_assistant_summary:
                previous_assistant_summary = _clip_continuation_text(content, limit=260)
            elif role == "user" and not previous_user_request:
                previous_user_request = _clip_continuation_text(content, limit=220)
            if previous_user_request and previous_assistant_summary:
                break

    summary: Dict[str, Any] = {}
    if previous_user_request:
        summary["previous_user_request"] = previous_user_request
    if previous_assistant_summary:
        summary["previous_assistant_summary"] = previous_assistant_summary

    active_subject = extra_context.get("active_subject")
    if isinstance(active_subject, dict):
        active_ref = str(
            active_subject.get("display_ref") or active_subject.get("canonical_ref") or ""
        ).strip()
        if active_ref:
            summary["active_subject"] = _clip_continuation_text(active_ref, limit=240)

    hints = _extract_recent_path_and_filename_hints(
        history,
        extra_context.get("recent_tool_results", []),
        active_subject,
    )
    if hints["known_paths"]:
        summary["known_paths"] = hints["known_paths"]
    if hints["known_filenames"]:
        summary["known_filenames"] = hints["known_filenames"]

    recent_image_artifacts = extra_context.get("recent_image_artifacts")
    if isinstance(recent_image_artifacts, list) and recent_image_artifacts:
        image_anchors: List[str] = []
        for item in recent_image_artifacts[:4]:
            if not isinstance(item, dict):
                continue
            display_name = str(item.get("display_name") or "").strip()
            path = str(item.get("path") or "").strip()
            source_tool = str(item.get("source_tool") or "").strip()
            anchor = display_name or path
            if not anchor:
                continue
            if source_tool:
                anchor = f"{anchor} ({source_tool})"
            image_anchors.append(anchor)
        if image_anchors:
            summary["recent_image_artifacts"] = image_anchors

    recent_tool_results = extra_context.get("recent_tool_results", [])
    if isinstance(recent_tool_results, list) and recent_tool_results:
        latest = recent_tool_results[-1]
        if isinstance(latest, dict):
            tool_name = str(latest.get("tool") or latest.get("name") or "").strip()
            tool_summary = _clip_continuation_text(latest.get("summary"), limit=260)
            if tool_summary:
                summary["latest_tool_result"] = (
                    f"{tool_name}: {tool_summary}" if tool_name else tool_summary
                )

    failure_state = extra_context.get("last_failure_state")
    if isinstance(failure_state, dict):
        tool_name = str(failure_state.get("tool_name") or "").strip()
        operation = str(failure_state.get("operation") or "").strip()
        error_message = _clip_continuation_text(
            failure_state.get("error_message"),
            limit=220,
        )
        if error_message:
            prefix = " ".join(part for part in (tool_name, operation) if part).strip()
            summary["last_failure"] = (
                f"{prefix}: {error_message}" if prefix else error_message
            )

    return summary or None
