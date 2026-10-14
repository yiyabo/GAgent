"""Compact execution facts for read-only Plan tools, without model/tool transcripts."""

import json


def execution_summary(node):
    raw = getattr(node, "execution_result", None)
    if not raw:
        return None
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {"last_status": "unknown"}
    if not isinstance(payload, dict):
        return {"last_status": "unknown"}
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    diff = metadata.get("contract_diff") or {}
    if not isinstance(diff, dict):
        diff = {}
    missing = diff.get("missing_required_outputs") or []
    missing = [p for p in missing if isinstance(p, str)] if isinstance(missing, list) else []
    verification = metadata.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    failures = verification.get("failures")
    failures = failures if isinstance(failures, list) else []
    checks = [
        {key: item[key] for key in ("type", "path", "message", "hard") if isinstance(item.get(key), (str, bool))}
        for item in failures[:10]
        if isinstance(item, dict)
    ]
    return {
        "last_status": payload.get("status"),
        "verification_status": metadata.get("verification_status"),
        "failure_kind": metadata.get("failure_kind"),
        "missing_outputs": missing[:20],
        "missing_output_count": len(missing),
        "repair_attempts": metadata.get("repair_attempts", 0),
        "failed_checks": checks,
        "failed_check_count": len(failures),
    }
