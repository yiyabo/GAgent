"""Recent-image display selection cluster of ``agent`` (W5a cluster ⑧).

Moved out of ``agent.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.8.  The plan's ⑧ was
``full_plan_runner.py``; that cluster (§4.8 ⑧) is a *class-body* method
(``_run_full_plan_via_executor``, 191 lines) and is therefore out of W5a scope,
so ⑧ is realised as this module instead — the remaining self-contained
module-level cluster in the file (the "previous / latest image" phrase tables and
the two helpers that answer a "show me that image again" turn).  ``agent.py``
re-exports every name, so the class call sites
(``_build_recent_image_display_response``) are unchanged.

Patch surface: **zero body deviations**.  None of these names is patched in
``app/`` or ``app/tests/``; the facade aliases the cluster read
(``requests_image_regeneration`` / ``requests_existing_image_display`` from
``request_routing`` and ``merge_artifact_gallery`` /
``update_recent_image_artifacts`` from ``artifact_gallery``) are imported here
directly from those source modules, so every call expression stays verbatim.
``Sequence`` is imported here for the ``Sequence[Dict[str, Any]]`` annotation
that the facade left unresolved under ``from __future__ import annotations``.

No logger is used in this cluster; every user-visible response string, metadata
key and selection mode literal is byte-identical.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

from .artifact_gallery import merge_artifact_gallery, update_recent_image_artifacts
from .request_routing import (
    RequestRoutingDecision,
    requests_existing_image_display,
    requests_image_regeneration,
)

_IMAGE_PREVIOUS_SELECTION_PHRASES = ("上一张", "前一张", "previous image", "prior image")
_IMAGE_LATEST_SELECTION_PHRASES = (
    "刚才那张",
    "刚刚那张",
    "最新那张",
    "最后那张",
    "latest image",
    "last image",
)


def _select_recent_image_artifacts(
    user_message: str,
    recent_items: Sequence[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], str]:
    normalized_items = [dict(item) for item in recent_items if isinstance(item, dict)]
    if not normalized_items:
        return [], "none"
    if len(normalized_items) == 1:
        return [normalized_items[0]], "single"

    lowered = str(user_message or "").strip().lower()
    if any(token in lowered for token in _IMAGE_PREVIOUS_SELECTION_PHRASES):
        return [normalized_items[1]], "previous"
    if any(token in lowered for token in _IMAGE_LATEST_SELECTION_PHRASES):
        return [normalized_items[0]], "latest"

    for item in normalized_items:
        display_name = str(item.get("display_name") or "").strip().lower()
        path = str(item.get("path") or "").strip().lower()
        basename = os.path.basename(path).strip().lower()
        if display_name and display_name in lowered:
            return [item], "named"
        if basename and basename in lowered:
            return [item], "named"

    return [], "ambiguous"


def _build_recent_image_display_response(
    agent: Any,
    *,
    user_message: str,
    routing_decision: RequestRoutingDecision,
) -> Optional[tuple[str, Dict[str, Any]]]:
    extra_context = getattr(agent, "extra_context", {}) or {}
    if requests_image_regeneration(user_message):
        return None
    if not requests_existing_image_display(user_message, extra_context):
        return None

    recent_items = extra_context.get("recent_image_artifacts")
    if not isinstance(recent_items, list) or not recent_items:
        return None

    selected_items, selection_mode = _select_recent_image_artifacts(user_message, recent_items)
    metadata: Dict[str, Any] = {
        "status": "completed",
        **routing_decision.metadata(),
    }
    metadata["thinking_display_mode"] = "final_answer"

    if not selected_items:
        if selection_mode != "ambiguous":
            return None
        response_text = (
            "当前会话里有多张图片。我先不重新生成。你要看哪一张？"
            "可以说“最新那张”“上一张”，或直接说文件名。"
        )
        metadata["analysis_text"] = response_text
        metadata["final_summary"] = response_text
        return response_text, metadata

    merged_gallery = merge_artifact_gallery(None, selected_items, limit=4)
    update_recent_image_artifacts(extra_context, merged_gallery)
    metadata["artifact_gallery"] = merged_gallery

    if selection_mode == "previous":
        response_text = "这里是上一张图片。"
    elif selection_mode in {"latest", "single"}:
        response_text = "这里是刚才那张图片。"
    else:
        response_text = "这里是你要看的那张图片。"
    metadata["analysis_text"] = response_text
    metadata["final_summary"] = response_text
    return response_text, metadata
