"""Progress receipts for useful observations and successful computations.

These receipts affect stagnation detection only, never artifact publication or
completion claims. Replayed output/call IDs, timings and kernel counters do not
create progress. Existing time, iteration and repeated-failure limits still apply.
"""
import hashlib
import json

_VOLATILE = frozenset({"duration", "duration_seconds", "duration_ms", "elapsed", "elapsed_seconds",
    "timestamp", "checked_at", "kernel", "execution_count", "request_id", "tool_call_id", "call_id", "usage"})


def _stable(value):
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items() if k not in _VOLATILE}
    if isinstance(value, list):
        return [_stable(item) for item in value]
    return value


def observe(tool_results, guard):
    seen = guard.setdefault("observation_fingerprints", [])
    changed = False
    for item in tool_results:
        payload = item.get("tool_result")
        if not isinstance(payload, (dict, str)):
            payload = item.get("tool_result_text")
        success = False
        for _ in range(6):
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (ValueError, TypeError):
                    break
            if not isinstance(payload, dict):
                break
            status = str(payload.get("status") or "").lower()
            if (payload.get("success") is False or payload.get("exit_code") not in (None, 0)
                    or status in {"failed", "failure", "error", "timeout", "cancelled"}):
                success = False
                break
            success = success or payload.get("success") is True or status in {"success", "ok", "completed"}
            if "result" not in payload:
                break
            payload = payload["result"]
        if not success or payload in (None, "", {}, []):
            continue
        tool = item.get("tool_name") or item.get("tool")
        receipt = {"tool": tool, "result": _stable(payload)}
        if tool == "execute_code":
            params = item.get("tool_params") or item.get("parameters") or {}
            receipt["code"] = params.get("code")
        fingerprint = hashlib.sha256(json.dumps(receipt, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        if fingerprint not in seen:
            seen.append(fingerprint)
            changed = True
    # A run's iteration cap is <=200; keep all normal receipts across resume.
    guard["observation_fingerprints"] = seen[-512:]
    return changed
