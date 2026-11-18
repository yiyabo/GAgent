"""Project stored chat-run events into a read-only Run Inspector payload.

Pure functions only: callers pass rows from ``app.repository.run_inspection``.
The projection never re-executes anything. It deduplicates the many
``thinking_step`` status updates per iteration into one step, extracts tool
calls with redacted, truncated parameters and results, and summarizes usage.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

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
_BEARER_SECRET = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_INLINE_SECRET = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{12,}|\bgh[pousr]_[A-Za-z0-9_]{16,}|\bxox[baprs]-[A-Za-z0-9-]{12,})"
)
_KEY_VALUE_SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|passwd|authorization|cookie|credential)\b\s*[:=]\s*['\"]?)([^'\"\s,;}{\]]{6,})"
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
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"… [+{len(text) - limit} chars]"


def _mask_inline_secrets(text: str) -> str:
    masked = _BEARER_SECRET.sub(r"\1[redacted]", text)
    masked = _INLINE_SECRET.sub("[redacted]", masked)
    masked = _KEY_VALUE_SECRET.sub(r"\1[redacted]", masked)
    return masked


def redact(value: Any, depth: int = 0, *, text_limit: int = MAX_PARAM_TEXT) -> Any:
    """Mask secret-looking keys/values (including JSON strings) and shorten strings."""
    if depth > 6:
        return "[depth limit]"
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SECRET_KEY.search(str(key)) else redact(item, depth + 1, text_limit=text_limit)
            for key, item in value.items()
        }
    if isinstance(value, list):
        items = [redact(item, depth + 1, text_limit=text_limit) for item in value[:50]]
        if len(value) > 50:
            items.append(f"[+{len(value) - 50} items]")
        return items
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) <= 200_000 and (
            (stripped.startswith("{") and stripped.endswith("}"))
            or (stripped.startswith("[") and stripped.endswith("]"))
        ):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                parsed = None
            if isinstance(parsed, (dict, list)):
                return redact(parsed, depth + 1, text_limit=text_limit)
        return _truncate(_mask_inline_secrets(value), text_limit)
    return value


def redact_text(value: Any, limit: int = MAX_TEXT) -> str | None:
    """Redact structured or free-text payloads and format as a bounded string."""
    if value is None:
        return None
    return _truncate(redact(value, text_limit=limit), limit)


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


def _action_calls(action: Any) -> list[Any]:
    """Raw call dicts from a step action: ``{"tools": [...]}``, one call, or a list."""
    if isinstance(action, str) and action.strip().startswith("["):
        try:
            action = json.loads(action)
        except ValueError:
            return []
    if isinstance(action, list):
        return action
    data = _as_dict(action)
    if not data:
        return []
    raw = data.get("tools")
    return raw if isinstance(raw, list) else [data] if data.get("tool") else []


def _tool_calls(action: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for call in _action_calls(action):
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


def _step_kind(step: dict[str, Any], tools: list[dict[str, Any]]) -> str:
    if tools:
        return "tool"
    kind = str(step.get("kind") or "reasoning")
    # build_user_visible_step labels any truthy action "tool"; without a
    # parsable call there is nothing to show as a tool step.
    return "reasoning" if kind == "tool" else kind


def _step_key(step: dict[str, Any], tools: list[dict[str, Any]]) -> tuple[Any, ...]:
    signature = tuple(sorted(str(call.get("tool_call_id") or call["tool"]) for call in tools))
    return (step.get("iteration"), _step_kind(step, tools), signature)


def _is_priority_step(entry: dict[str, Any]) -> bool:
    """Tool-bearing and failed steps survive the step cap before reasoning-only ones."""
    return bool(entry.get("tools")) or entry.get("status") == "error" or entry.get("success") is False


def project_steps(
    step_events: list[tuple[int, str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], int]:
    """Collapse repeated status updates into one entry per iteration step.

    A toolless update (``thinking`` → ``done``/``error`` with no parsable
    action) is merged into the step already open for its iteration instead of
    opening an empty ``tools: []`` row. Returns the projected steps and the
    number of steps dropped by the ``MAX_STEPS`` cap; when capping, tool and
    failed steps are kept ahead of reasoning-only ones, in original order.
    """
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    open_by_iteration: dict[Any, tuple[Any, ...]] = {}
    for seq, _created, payload in step_events:
        step = payload.get("step")
        if not isinstance(step, dict):
            continue
        tools = _tool_calls(step.get("action"))
        iteration = step.get("iteration")
        trackable = isinstance(iteration, (int, float, str))
        if not tools and trackable and iteration in open_by_iteration:
            key = open_by_iteration[iteration]
        else:
            key = _step_key(step, tools)
        if key not in merged:
            order.append(key)
            merged[key] = {
                "first_seq": seq,
                "started_at": step.get("started_at") or step.get("timestamp"),
            }
        if trackable:
            open_by_iteration[iteration] = key
        entry = merged[key]
        entry.update(
            {
                "last_seq": seq,
                "iteration": iteration,
                "kind": key[1],
                "status": step.get("status"),
                "label": _truncate(step.get("display_text"), 300) or entry.get("label"),
                "tools": tools or entry.get("tools") or [],
            }
        )
        if step.get("finished_at") and step.get("finished_at") != "None":
            entry["finished_at"] = step.get("finished_at")
        result = step.get("action_result")
        if result is not None and result != "None":
            entry["success"] = result_success(result)
            entry["result"] = redact_text(result, MAX_TEXT)
        if step.get("self_correction") not in (None, "None", ""):
            entry["self_correction"] = redact_text(step.get("self_correction"), 500)
    for entry in merged.values():
        if entry.get("status") == "error":
            entry["success"] = False
        entry["duration_ms"] = _duration_ms(entry.get("started_at"), entry.get("finished_at"))
    kept = order
    if len(order) > MAX_STEPS:
        chosen = {key for key in order if _is_priority_step(merged[key])}
        if len(chosen) > MAX_STEPS:
            chosen = set([key for key in order if key in chosen][:MAX_STEPS])
        for key in order:
            if len(chosen) >= MAX_STEPS:
                break
            chosen.add(key)
        kept = [key for key in order if key in chosen]
    return [merged[key] for key in kept], len(order) - len(kept)


def summarize_usage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def num(row: dict[str, Any], key: str) -> float:
        value = row.get(key)
        return float(value) if isinstance(value, (int, float)) else 0.0

    totals = {
        "calls": len(rows),
        "failed_calls": sum(1 for row in rows if str(row.get("call_status") or "ok") != "ok"),
        "prompt_tokens": int(sum(num(row, "prompt_tokens") for row in rows)),
        "completion_tokens": int(sum(num(row, "completion_tokens") for row in rows)),
        "total_tokens": int(sum(num(row, "total_tokens") for row in rows)),
        "cache_read_tokens": int(sum(num(row, "cache_read_tokens") for row in rows)),
        "duration_ms": int(sum(num(row, "duration_ms") for row in rows)),
        "estimated_cost": round(sum(num(row, "estimated_cost") for row in rows), 6),
        "cost_currency": next((row.get("cost_currency") for row in rows if row.get("cost_currency")), None),
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
    totals["by_purpose"] = sorted(groups.values(), key=lambda item: -item["total_tokens"])
    return totals


def _dict_field(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    return value if isinstance(value, dict) else {}


def _request_summary(raw: Any) -> dict[str, Any]:
    data = _as_dict(raw) or {}
    context = _dict_field(data, "context")
    return {
        "message": _truncate(data.get("message")),
        "context_flags": {key: context[key] for key in _CONTEXT_FLAGS if isinstance(context.get(key), bool)},
    }


def _timeline_event(seq: int, created: str, payload: dict[str, Any]) -> dict[str, Any] | None:
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
            "message": redact_text(payload.get("message"), 1000),
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
) -> dict[str, Any]:
    step_events = [item for item in events if item[2].get("type") == "thinking_step"]
    steps, dropped_steps = project_steps(step_events)

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
        timeline_events = [item for item in timeline_events if item["type"] != "artifact" or id(item) in keep]

    metadata = _dict_field(final_payload, "metadata")
    final: dict[str, Any] = {key: redact(metadata[key]) for key in _FINAL_KEYS if key in metadata}
    tool_failures = metadata.get("tool_failures")
    if isinstance(tool_failures, list):
        final["tool_failures"] = redact(tool_failures[:20])
    if final_payload:
        final["response_preview"] = redact_text(final_payload.get("response"), 1500)

    tool_steps = [step for step in steps if step["kind"] == "tool"]
    tool_names = sorted({call["tool"] for step in tool_steps for call in step["tools"]})
    failures = sum(1 for step in tool_steps if step.get("success") is False)
    streaming = {key: event_counts.get(key, 0) for key in ("delta", "thinking_delta", "reasoning_delta", "tool_output")}

    return {
        "version": INSPECTION_VERSION,
        "run": {
            "run_id": run.get("run_id"),
            "session_id": run.get("session_id"),
            "status": run.get("status"),
            "created_at": run.get("created_at"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "duration_ms": _duration_ms(run.get("started_at") or run.get("created_at"), run.get("finished_at")),
            "error": redact_text(run.get("error"), 1000),
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
        "truncated": {
            "steps": dropped_steps > 0,
            "artifacts": artifacts_truncated,
            "dropped_steps": dropped_steps,
        },
    }


_ACTIVE_TASK_STATUSES = frozenset({"running", "in_progress", "executing"})


def plan_task_run_id(plan_id: int, task_id: int) -> str:
    """Usage-ledger run id for a plan task (see ``plan_executor._task_usage_id``)."""
    return f"plan_{plan_id}_task_{task_id}"


def _stored_result_steps(result: dict[str, Any], first_seq: int, at: str) -> list[tuple[int, str, dict[str, Any]]]:
    thinking = _dict_field(_dict_field(result, "metadata"), "thinking_process")
    raw_steps = thinking.get("steps")
    if not isinstance(raw_steps, list):
        return []
    return [
        (first_seq + index, str(step.get("timestamp") or at), {"type": "thinking_step", "step": step})
        for index, step in enumerate(raw_steps)
        if isinstance(step, dict)
    ]


def _stored_artifact_paths(result: dict[str, Any]) -> list[str]:
    metadata = _dict_field(result, "metadata")
    paths: list[str] = []
    for source in (result, metadata):
        for key in ("artifact_paths", "session_artifact_paths"):
            values = source.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, str) and value.strip() and value.strip() not in paths:
                    paths.append(value.strip())
    return paths


def _plan_log_events(
    logs: list[tuple[int, str, dict[str, Any]]],
) -> list[tuple[int, str, dict[str, Any]]]:
    events: list[tuple[int, str, dict[str, Any]]] = []
    for seq, at, row in logs:
        metadata = row.get("metadata")
        meta = metadata if isinstance(metadata, dict) else {}
        sub_type = meta.get("sub_type")
        if sub_type == "thinking_step" and isinstance(meta.get("step"), dict):
            events.append((seq, at, {"type": "thinking_step", "step": meta["step"]}))
        elif str(row.get("level") or "").lower() == "error" and sub_type != "tool_call_result":
            # Failed tool results already surface on their step.
            events.append(
                (
                    seq,
                    at,
                    {
                        "type": "error",
                        "message": redact_text(row.get("message"), 1000),
                        "error_type": sub_type or meta.get("error_type"),
                    },
                )
            )
    return events


def build_plan_task_inspection(
    *,
    plan_id: int,
    task: dict[str, Any],
    job: dict[str, Any] | None,
    logs: list[tuple[int, str, dict[str, Any]]],
    log_counts: dict[str, int],
    usage_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Project one plan task's execution into the Run Inspector payload.

    Steps come from the task's ``thinking_step`` job logs; when the job logs
    are absent (pruned, or executed before logs carried ``task_id``) they are
    rebuilt from ``tasks.execution_result`` (only when inspecting the latest
    job attempt so historical ``job_id`` pins never mix newer results).
    """
    task_id = int(task["id"])
    is_latest_job = bool(job.get("is_latest_job", True)) if isinstance(job, dict) else True
    result = (_as_dict(task.get("execution_result")) or {}) if is_latest_job else {}
    events = _plan_log_events(logs)
    last_seq = logs[-1][0] if logs else 0
    fallback_at = str(task.get("updated_at") or "")
    if not any(item[2]["type"] == "thinking_step" for item in events):
        events.extend(_stored_result_steps(result, last_seq + 1, fallback_at))
    next_seq = max((item[0] for item in events), default=last_seq) + 1
    for path in _stored_artifact_paths(result):
        artifact = {"type": "artifact", "path": path, "display_name": path.rsplit("/", 1)[-1]}
        events.append((next_seq, fallback_at, artifact))
        next_seq += 1
    if result:
        events.append((next_seq, fallback_at, _stored_final_event(result)))

    payload = build_run_inspection(
        _plan_task_run(plan_id, task, job, logs, result, is_latest_job=is_latest_job),
        events,
        log_counts,
        usage_rows,
    )
    payload["run"].update(
        {
            "plan_id": plan_id,
            "task_id": task_id,
            "task_name": _truncate(task.get("name"), 300),
            "job_id": (job or {}).get("job_id"),
        }
    )
    payload["summary"]["streaming_chunks"] = int(log_counts.get("thinking_delta", 0))
    # The usage ledger keys plan tasks by task, not job: totals cover every
    # attempt of the task even when ``job_id`` pins one run.
    payload["usage"]["scope"] = "plan_task_all_attempts"
    logged = sum(n for kind, n in log_counts.items() if kind != "thinking_delta")
    payload["truncated"]["logs"] = len(logs) < logged
    payload["artifacts"] = [item for item in payload["events"] if item["type"] == "artifact"]
    return payload


def _stored_final_event(result: dict[str, Any]) -> dict[str, Any]:
    """Shape ``tasks.execution_result`` like a chat ``final`` event."""
    candidates = {"status": result.get("status"), **_dict_field(result, "metadata")}
    return {
        "type": "final",
        "payload": {
            "response": result.get("content"),
            "metadata": {
                key: value for key, value in candidates.items() if key in _FINAL_KEYS or key == "tool_failures"
            },
        },
    }


def _plan_task_run(
    plan_id: int,
    task: dict[str, Any],
    job: dict[str, Any] | None,
    logs: list[tuple[int, str, dict[str, Any]]],
    result: dict[str, Any],
    *,
    is_latest_job: bool = True,
) -> dict[str, Any]:
    """Run-row equivalent for a plan task, timed by its own job logs.

    A full-plan job spans many tasks, so the job's own window and error only
    stand in for the task when the job targeted this task.
    """
    task_id = int(task["id"])
    job_row = job or {}
    targeted = job is not None and job_row.get("target_task_id") == task_id
    log_times = [at for _seq, at, _row in logs if at]
    started = log_times[0] if log_times else None
    finished = log_times[-1] if log_times else None
    if targeted:
        started = started or job_row.get("started_at")
        finished = finished or job_row.get("finished_at")
    status = task.get("status") if is_latest_job else (job_row.get("status") or task.get("status"))
    if str(status or "").strip().lower() in _ACTIVE_TASK_STATUSES:
        finished = None
    return {
        "run_id": plan_task_run_id(plan_id, task_id),
        "session_id": job_row.get("session_id"),
        "status": status,
        "created_at": job_row.get("created_at") or task.get("created_at"),
        "started_at": started,
        "finished_at": finished,
        "error": result.get("error") or (job_row.get("error") if targeted else None),
        "last_event_seq": logs[-1][0] if logs else None,
        "user_message_id": None,
        "assistant_message_id": None,
        "request_json": {"message": task.get("instruction") or task.get("name")},
    }
