"""Request accounting and deterministic anchors; estimates are not billing."""
from __future__ import annotations

import json
from .context_manager import estimate_tokens,estimate_messages_tokens


class ContextBudgetExceeded(RuntimeError):pass


def breakdown(messages,schemas,output,components=None):
    groups={'system':0,'skills':0,'recall':0,'history_tool_results':0,'tool_schemas':estimate_tokens(json.dumps(schemas,ensure_ascii=False)) if schemas else 0,'output_reserve':max(0,output),'provider_framing':128 if schemas or output else 0}
    for m in messages:
        key='system' if m.get('role')=='system' else 'history_tool_results'
        groups[key]+=estimate_messages_tokens([m])
    for name,fragment in (components or {}).items():
        if not fragment:continue
        for m in messages:
            if fragment in str(m.get('content') or ''):
                source='system' if m.get('role')=='system' else 'history_tool_results'
                amount=min(groups[source],estimate_tokens(fragment));groups[source]-=amount;groups[name]=groups.get(name,0)+amount
                break
    return groups


ANCHOR_HEADER = "[Deterministic execution anchors]"
MAX_ANCHOR_PRODUCED_PATHS = 25
MAX_ANCHOR_ASSUMPTIONS = 12


def _anchor_list(value, limit):
    """Ordered, de-duplicated, non-empty strings; capped at ``limit``."""
    items = value if isinstance(value, (list, tuple)) else ([value] if value else [])
    out = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def anchor_text(anchors):
    """Render anchors deterministically: same input, byte-identical text.

    ``produced_paths`` and ``assumptions`` get their own readable sections so a
    compacted model still sees which deliverables already exist and which plan
    defaults are unconfirmed; every other anchor is a sorted-key JSON object.
    """
    if not anchors:
        return ""
    rest = dict(anchors)
    produced = _anchor_list(rest.pop("produced_paths", None), MAX_ANCHOR_PRODUCED_PATHS)
    assumptions = _anchor_list(rest.pop("assumptions", None), MAX_ANCHOR_ASSUMPTIONS)
    lines = [ANCHOR_HEADER]
    if produced:
        lines.append("Produced deliverables (already written; reuse or edit, do not recreate):")
        lines.extend(f"- {path}" for path in produced)
    if assumptions:
        lines.append("Active plan assumptions (unconfirmed defaults; keep consistent):")
        lines.extend(f"- {item}" for item in assumptions)
    if rest:
        lines.append(json.dumps(rest, ensure_ascii=False, sort_keys=True, default=str))
    return "\n".join(lines) if len(lines) > 1 else ""
