"""Build compact, fact-only snapshots from durable chat runs."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.repository.chat_runs import fetch_events_after, get_chat_run

_TEXT_EVENT_TYPES = {"delta", "thinking_delta", "reasoning_delta", "tool_output"}


def _trim(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if limit <= 0:
        return ""
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _parse_request(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, str):
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _safe_tool_results(value: Any, *, item_limit: int = 6) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in value[:item_limit]:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        result_summary = ""
        if isinstance(result, dict):
            result_summary = _trim(
                result.get("summary") or result.get("message") or result.get("error") or result.get("status"),
                500,
            )
        out.append(
            {
                "name": _trim(item.get("name"), 100),
                "summary": _trim(item.get("summary"), 500),
                "success": result.get("success") if isinstance(result, dict) else None,
                "result_summary": result_summary,
            }
        )
    return out


def _extract_final_payload(events: List[Tuple[int, Dict[str, Any]]]) -> Dict[str, Any]:
    for _, event in reversed(events):
        if event.get("type") != "final":
            continue
        payload = event.get("payload")
        return payload if isinstance(payload, dict) else {}
    return {}


def _extract_event_summary(events: List[Tuple[int, Dict[str, Any]]]) -> Dict[str, Any]:
    errors: List[str] = []
    tool_outputs: List[Dict[str, str]] = []
    event_counts: Dict[str, int] = {}
    for _, event in events:
        event_type = str(event.get("type") or "unknown")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        if event_type == "error":
            errors.append(_trim(event.get("message") or event.get("error"), 500))
        elif event_type == "tool_output" and len(tool_outputs) < 6:
            tool_outputs.append(
                {
                    "tool": _trim(event.get("tool") or event.get("name"), 100),
                    "summary": _trim(event.get("summary") or event.get("content"), 500),
                }
            )
    return {
        "counts": event_counts,
        "errors": [item for item in errors if item][:4],
        "tool_outputs": tool_outputs,
    }


def build_run_snapshot(run_id: str, *, max_chars: int) -> Optional[Dict[str, Any]]:
    """Return a bounded snapshot or None when the durable run is unavailable."""
    run = get_chat_run(run_id)
    if not run:
        return None
    request = _parse_request(run.get("request_json"))
    events = fetch_events_after(run_id, -1)
    final_payload = _extract_final_payload(events)
    metadata = final_payload.get("metadata") if isinstance(final_payload.get("metadata"), dict) else {}
    context = request.get("context") if isinstance(request.get("context"), dict) else {}
    response = final_payload.get("response") or (
        final_payload.get("llm_reply", {}).get("message")
        if isinstance(final_payload.get("llm_reply"), dict)
        else ""
    )
    routing_keys = ("request_tier", "intent_type", "route_reason_codes", "request_route_mode")
    routing = {
        key: metadata.get(key, context.get(key))
        for key in routing_keys
        if metadata.get(key, context.get(key)) is not None
    }
    tools_used = metadata.get("tools_used")
    if not isinstance(tools_used, list):
        tools_used = []
    snapshot: Dict[str, Any] = {
        "snapshot_version": "conversation_quality_v1",
        "run": {
            "run_id": run_id,
            "status": run.get("status"),
            "created_at": run.get("created_at"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "error": _trim(run.get("error"), 1000),
        },
        "user_goal": _trim(request.get("message"), 5000),
        "assistant_response": _trim(response, 7000),
        "routing": routing,
        "tools_used": [_trim(tool, 100) for tool in tools_used[:12] if str(tool).strip()],
        "tool_failures": [_trim(item, 500) for item in (metadata.get("tool_failures") or [])[:6]],
        "tool_results": _safe_tool_results(metadata.get("tool_results")),
        "event_summary": _extract_event_summary(events),
    }
    encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= max_chars:
        return snapshot

    text_budget = max(160, (max_chars - 500) // 2)
    snapshot["assistant_response"] = _trim(snapshot["assistant_response"], text_budget)
    snapshot["user_goal"] = _trim(snapshot["user_goal"], text_budget)
    snapshot["tool_results"] = snapshot["tool_results"][:2]
    snapshot["event_summary"]["tool_outputs"] = snapshot["event_summary"]["tool_outputs"][:1]
    encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= max_chars:
        return snapshot

    minimal = {
        "snapshot_version": snapshot["snapshot_version"],
        "run": {
            "run_id": run_id,
            "status": snapshot["run"].get("status"),
            "error": "",
        },
        "user_goal": "",
        "assistant_response": "",
        "routing": {
            "request_tier": snapshot["routing"].get("request_tier"),
            "intent_type": snapshot["routing"].get("intent_type"),
        },
        "tools_used": snapshot["tools_used"][:4],
        "tool_failures": snapshot["tool_failures"][:2],
        "event_summary": {"errors": []},
    }
    candidates = [
        ("run_error", _trim(snapshot["run"].get("error"), 500)),
        ("user_goal", snapshot["user_goal"]),
        ("assistant_response", snapshot["assistant_response"]),
        ("event_error", _trim((snapshot["event_summary"].get("errors") or [""])[0], 500)),
    ]
    nonempty = [(key, value) for key, value in candidates if value]
    fixed_size = len(json.dumps(minimal, ensure_ascii=False, separators=(",", ":")))
    available = max(0, max_chars - fixed_size)
    per_value = available // len(nonempty) if nonempty else 0
    for key, value in nonempty:
        fitted = _trim(value, per_value) if per_value else ""
        if key == "run_error":
            minimal["run"]["error"] = fitted
        elif key == "user_goal":
            minimal["user_goal"] = fitted
        elif key == "assistant_response":
            minimal["assistant_response"] = fitted
        else:
            minimal["event_summary"]["errors"] = [fitted] if fitted else []

    while len(json.dumps(minimal, ensure_ascii=False, separators=(",", ":"))) > max_chars:
        current = minimal["user_goal"] or minimal["assistant_response"] or minimal["run"]["error"]
        if not current:
            errors = minimal["event_summary"]["errors"]
            if not errors:
                break
            minimal["event_summary"]["errors"] = [_trim(errors[0], len(errors[0]) - 1)]
            continue
        shortened = _trim(current, len(current) - 1)
        if minimal["user_goal"]:
            minimal["user_goal"] = shortened
        elif minimal["assistant_response"]:
            minimal["assistant_response"] = shortened
        else:
            minimal["run"]["error"] = shortened
    return minimal
