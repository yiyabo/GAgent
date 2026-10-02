"""Smaller model-facing JSON; raw results, steps and verification remain intact.

Only duplicate protocol envelope fields are removed. Result data, stdout,
artifact paths, errors and truncation markers are never summarized or clipped.
"""
import json
from .runtime_policy import policy_for, configured_policy


def project_text(agent, tool_name, text):
    if not (policy_for(agent) if agent is not None else configured_policy())['receipts']:
        return text
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return text
    if isinstance(payload, dict) and payload.get('tool') == tool_name:
        inner = payload.get('result')
        # The dispatch envelope repeats the handler's exact bool status. Keep
        # the handler payload untouched: it may contain user JSON of any shape.
        if isinstance(inner, dict):
            for key in ('success', 'error'):
                if key in payload and key in inner and type(payload[key]) is type(inner[key]) and payload[key] == inner[key]:
                    del payload[key]
    rendered = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    if len(rendered) >= len(text):
        return text
    from app.services.execution.llm_observation import emit
    emit('receipt_projection', policy_version=1, tool=tool_name,
         original_chars=len(text), projected_chars=len(rendered))
    return rendered
