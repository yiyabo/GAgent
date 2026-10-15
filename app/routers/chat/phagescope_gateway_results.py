"""Gateway receipts kept usable across structured chat result compaction."""
from tool_box.phagescope_gateway_contract import summarize_gateway_result


def compact_gateway_result(raw):
    out = {"tool": "phagescope", **raw}
    cropped = False

    def compact(value):
        nonlocal cropped
        if isinstance(value, str) and len(value) > 8000:
            cropped = True
            return value[:8000]
        if isinstance(value, list):
            cropped = cropped or len(value) > 50
            return [compact(item) for item in value[:50]]
        if isinstance(value, dict):
            return {key: compact(item) for key, item in value.items()}
        return value

    if "data" in out:
        out["data"] = compact(out["data"])
    if cropped:
        out["display_truncated"] = True
    return out


