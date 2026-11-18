"""Project stored chat-run events into a read-only Run Inspector payload.

Pure functions only: callers pass rows from ``app.repository.run_inspection``.
The projection never re-executes anything. It deduplicates the many
``thinking_step`` status updates per iteration into one step, extracts tool
calls with redacted, truncated parameters and results, and summarizes usage.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

INSPECTION_VERSION = 1
MAX_STEPS = 400
MAX_ARTIFACTS = 100
MAX_TEXT = 2000
MAX_PARAM_TEXT = 400
MAX_PARAMS_JSON = 2000

_SECRET_KEY = re.compile(
    r"api[_-]?key|token|secret|password|passwd|authorization|cookie|credential",
    re.IGNORECASE,
)
_SUCCESS_FLAG = re.compile(r'"success"\s*:\s*(true|false)', re.IGNORECASE)
_CONTEXT_FLAGS = (
    "plan_mode",
    "plan_offer_accepted",
    "plan_create_required",
    "plan_contract_enforced",
)
_FINAL_KEYS = (
    "status",
    "termination_reason",
    "plan_id",
    "plan_title",
    "plan_creation_state",
    "plan_offer_gate",
    "iterations",
    "intent_type",
    "request_tier",
    "request_route_mode",
    "route_reason_codes",
    "fallback_used",
    "tools_used",
)


def _truncate(value: Any, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, default=str)
    )
    return (
        text if len(text) <= limit else text[:limit] + f"… [+{len(text) - limit} chars]"
    )


def redact(value: Any, depth: int = 0) -> Any:
    """Mask secret-looking keys and shorten long strings, recursively."""
    if depth > 6:
        return "[depth limit]"
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if _SECRET_KEY.search(str(key))
            else redact(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        items = [redact(item, depth + 1) for item in value[:50]]
        if len(value) > 50:
            items.append(f"[+{len(value) - 50} items]")
        return items
    if isinstance(value, str):
        return _truncate(value, MAX_PARAM_TEXT)
    return value


def _parse_time(value: Any) -> datetime | None:
    if not value or value == "None":
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for candidate in (text, text.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed.replace(tzinfo=None)
    return None


def _duration_ms(start: Any, end: Any) -> int | None:
    begin, finish = _parse_time(start), _parse_time(end)
    if begin is None or finish is None or finish < begin:
        return None
    return int((finish - begin).total_seconds() * 1000)


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _tool_calls(action: Any) -> list[dict[str, Any]]:
    data = _as_dict(action)
    if not data:
        return []
    raw = data.get("tools")
    calls = raw if isinstance(raw, list) else [data] if data.get("tool") else []
    out: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, dict) or not call.get("tool"):
            continue
        params = redact(call.get("params") or {})
        out.append(
            {
                "tool": str(call["tool"]),
                "tool_call_id": call.get("tool_call_id"),
                "params": _truncate(params, MAX_PARAMS_JSON),
            }
        )
    return out


def result_success(result: Any) -> bool | None:
    """Best-effort success flag from a tool result; None when unknown."""
    data = _as_dict(result)
    if data is not None and isinstance(data.get("success"), bool):
        return data["success"]
    if isinstance(result, str):
        match = _SUCCESS_FLAG.search(result[:4000])
        if match:
            return match.group(1).lower() == "true"
    return None


def _step_key(step: dict[str, Any], tools: list[dict[str, Any]]) -> tuple[Any, ...]:
    signature = tuple(
        sorted(str(call.get("tool_call_id") or call["tool"]) for call in tools)
    )
    kind = "tool" if tools else str(step.get("kind") or "reasoning")
    return (step.get("iteration"), kind, signature)


def project_steps(
    step_events: list[tuple[int, str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], bool]:
    """Collapse repeated status updates into one entry per iteration step."""
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for seq, _created, payload in step_events:
        step = payload.get("step")
        if not isinstance(step, dict):
            continue
        tools = _tool_calls(step.get("action"))
        key = _step_key(step, tools)
        if key not in merged:
            if len(order) >= MAX_STEPS:
                continue
            order.append(key)
            merged[key] = {
                "first_seq": seq,
                "started_at": step.get("started_at") or step.get("timestamp"),
            }
        entry = merged[key]
        entry.update(
            {
                "last_seq": seq,
                "iteration": step.get("iteration"),
                "kind": "tool" if tools else (step.get("kind") or "reasoning"),
                "status": step.get("status"),
                "label": _truncate(step.get("display_text"), 300),
                "tools": tools or entry.get("tools") or [],
            }
        )
        if step.get("finished_at") and step.get("finished_at") != "None":
            entry["finished_at"] = step.get("finished_at")
        result = step.get("action_result")
        if result is not None and result != "None":
            entry["result"] = _truncate(result)
            entry["success"] = result_success(result)
        if step.get("self_correction") not in (None, "None", ""):
            entry["self_correction"] = _truncate(step.get("self_correction"), 500)
    steps: list[dict[str, Any]] = []
    for key in order:
        entry = merged[key]
        if entry.get("status") == "error":
            entry["success"] = False
        entry["duration_ms"] = _duration_ms(
            entry.get("started_at"), entry.get("finished_at")
        )
        steps.append(entry)
    total_keys = len(
        {
            _step_key(
                p.get("step") or {}, _tool_calls((p.get("step") or {}).get("action"))
            )
            for _seq, _c, p in step_events
            if isinstance(p.get("step"), dict)
        }
    )
    return steps, total_keys > len(steps)


def summarize_usage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def num(row: dict[str, Any], key: str) -> float:
        value = row.get(key)
        return float(value) if isinstance(value, (int, float)) else 0.0

    totals = {
        "calls": len(rows),
        "failed_calls": sum(
            1 for row in rows if str(row.get("call_status") or "ok") != "ok"
        ),
        "prompt_tokens": int(sum(num(row, "prompt_tokens") for row in rows)),
        "completion_tokens": int(sum(num(row, "completion_tokens") for row in rows)),
        "total_tokens": int(sum(num(row, "total_tokens") for row in rows)),
        "cache_read_tokens": int(sum(num(row, "cache_read_tokens") for row in rows)),
        "duration_ms": int(sum(num(row, "duration_ms") for row in rows)),
        "estimated_cost": round(sum(num(row, "estimated_cost") for row in rows), 6),
        "cost_currency": next(
            (row.get("cost_currency") for row in rows if row.get("cost_currency")), None
        ),
        "models": sorted({str(row["model"]) for row in rows if row.get("model")}),
    }
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("phase") or "unknown"),
            str(row.get("call_purpose") or "unknown"),
        )
        group = groups.setdefault(
            key,
            {
                "phase": key[0],
                "purpose": key[1],
                "calls": 0,
                "total_tokens": 0,
                "duration_ms": 0,
                "failed": 0,
            },
        )
        group["calls"] += 1
        group["total_tokens"] += int(num(row, "total_tokens"))
        group["duration_ms"] += int(num(row, "duration_ms"))
        group["failed"] += 1 if str(row.get("call_status") or "ok") != "ok" else 0
    totals["by_purpose"] = sorted(
        groups.values(), key=lambda item: -item["total_tokens"]
    )
    return totals


def _dict_field(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    return value if isinstance(value, dict) else {}


def _request_summary(raw: Any) -> dict[str, Any]:
    data = _as_dict(raw) or {}
    context = _dict_field(data, "context")
    return {
        "message": _truncate(data.get("message")),
        "context_flags": {
            key: context[key]
            for key in _CONTEXT_FLAGS
            if isinstance(context.get(key), bool)
        },
    }


def _timeline_event(
    seq: int, created: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    kind = payload.get("type")
    if kind == "artifact":
        return {
            "seq": seq,
            "at": created,
            "type": "artifact",
            "path": payload.get("path"),
            "display_name": payload.get("display_name"),
            "source_tool": payload.get("source_tool"),
            "iteration": payload.get("iteration"),
        }
    if kind == "error":
        return {
            "seq": seq,
            "at": created,
            "type": "error",
            "message": _truncate(payload.get("message"), 1000),
            "error_type": payload.get("error_type"),
        }
    if kind == "steer_ack":
        return {
            "seq": seq,
            "at": created,
            "type": "steer",
            "message": _truncate(payload.get("message"), 500),
        }
    if kind == "job_update":
        body = _dict_field(payload, "payload")
        return {
            "seq": seq,
            "at": created,
            "type": "job",
            "status": body.get("status"),
            "job_id": body.get("job_id"),
        }
    return None


def build_run_inspection(
    run: dict[str, Any],
    events: list[tuple[int, str, dict[str, Any]]],
    event_counts: dict[str, int],
    usage_rows: list[dict[str, Any]],
    child_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    step_events = [item for item in events if item[2].get("type") == "thinking_step"]
    steps, steps_truncated = project_steps(step_events)

    timeline_events: list[dict[str, Any]] = []
    final_payload: dict[str, Any] = {}
    for seq, created, payload in events:
        if payload.get("type") == "final" and isinstance(payload.get("payload"), dict):
            final_payload = payload["payload"]
            continue
        item = _timeline_event(seq, created, payload)
        if item is not None:
            timeline_events.append(item)
    artifacts = [item for item in timeline_events if item["type"] == "artifact"]
    artifacts_truncated = len(artifacts) > MAX_ARTIFACTS
    if artifacts_truncated:
        keep = {id(item) for item in artifacts[:MAX_ARTIFACTS]}
        timeline_events = [
            item
            for item in timeline_events
            if item["type"] != "artifact" or id(item) in keep
        ]

    metadata = _dict_field(final_payload, "metadata")
    final: dict[str, Any] = {
        key: redact(metadata[key]) for key in _FINAL_KEYS if key in metadata
    }
    tool_failures = metadata.get("tool_failures")
    if isinstance(tool_failures, list):
        final["tool_failures"] = redact(tool_failures[:20])
    if final_payload:
        final["response_preview"] = _truncate(final_payload.get("response"), 1500)

    tool_steps = [step for step in steps if step["kind"] == "tool"]
    tool_names = sorted({call["tool"] for step in tool_steps for call in step["tools"]})
    failures = sum(1 for step in tool_steps if step.get("success") is False)
    streaming = {
        key: event_counts.get(key, 0)
        for key in ("delta", "thinking_delta", "reasoning_delta", "tool_output")
    }

    return {
        "version": INSPECTION_VERSION,
        "run": {
            "run_id": run.get("run_id"),
            "session_id": run.get("session_id"),
            "status": run.get("status"),
            "created_at": run.get("created_at"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "duration_ms": _duration_ms(
                run.get("started_at") or run.get("created_at"), run.get("finished_at")
            ),
            "error": _truncate(run.get("error"), 1000),
            "last_event_seq": run.get("last_event_seq"),
            "user_message_id": run.get("user_message_id"),
            "assistant_message_id": run.get("assistant_message_id"),
            **_request_summary(run.get("request_json")),
        },
        "summary": {
            "steps": len(steps),
            "tool_steps": len(tool_steps),
            "tool_failures": failures,
            "tools": tool_names,
            "artifacts": len(artifacts),
            "errors": sum(1 for item in timeline_events if item["type"] == "error"),
            "event_counts": dict(sorted(event_counts.items())),
            "streaming_events": streaming,
        },
        "steps": steps,
        "events": timeline_events,
        "final": final,
        "usage": summarize_usage(usage_rows),
        "child_runs": list(child_runs or []),
        "truncated": {"steps": steps_truncated, "artifacts": artifacts_truncated},
    }


def load_child_runs(run_id: str) -> list[dict[str, Any]]:
    """Per-child usage rows for sub-agents delegated by ``run_id`` (``[]`` when none)."""
    from app.repository.llm_usage import get_child_run_usage_summary

    try:
        summary = get_child_run_usage_summary(run_id)
    except Exception:  # usage ledger is best-effort observability
        logger.warning("run inspector: child run usage unavailable for %s", run_id, exc_info=True)
        return []
    children = summary.get("children") if isinstance(summary, dict) else None
    return [dict(item) for item in children] if isinstance(children, list) else []
