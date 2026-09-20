"""Strict-protocol JSON parsers for the DeepThink agent.

God-class split (behaviour zero-change): the bodies of the like-named
DeepThinkAgent methods with `self` renamed to `agent`; the class keeps
thin wrappers (same decorators). Cross-calls between the parsers go
through `agent._x(...)` so subclass overrides keep working.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.services.deep_think.models import DeepThinkProtocolError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.deep_think_agent import DeepThinkAgent


def _parse_llm_response_safe(agent: "DeepThinkAgent", response: str) -> tuple[Dict[str, Any], Optional[str]]:
    try:
        return agent._parse_llm_response(response), None
    except Exception as exc:
        return {}, str(exc)


def _parse_llm_response(agent: "DeepThinkAgent", response: str) -> Dict[str, Any]:
    json_str = agent._extract_json(response)
    parsed: Optional[Dict[str, Any]] = None
    parse_errors: List[str] = []

    for candidate in (
        json_str,
        agent._repair_json_text(json_str),
    ):
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            parse_errors.append(str(exc))
            continue
        if isinstance(payload, dict):
            parsed = payload
            break

    if parsed is None:
        parsed = agent._regex_parse_fallback(json_str)
        if parsed is None:
            raise DeepThinkProtocolError(
                "LLM output is not valid JSON; parse errors: "
                + "; ".join(parse_errors[:2])
            )

    thinking = parsed.get("thinking", "")
    if thinking is None:
        thinking = ""
    if not isinstance(thinking, str):
        thinking = str(thinking)

    result = {
        "thought": thinking,
        "is_final": False,
        "final_answer": "",
        "confidence": 0.0,
        "tool_name": None,
        "tool_params": None,
        "action_str": None,
    }

    final_ans = parsed.get("final_answer")
    if final_ans is not None:
        if isinstance(final_ans, str):
            final_ans = {"answer": final_ans, "confidence": 0.7}
        if not isinstance(final_ans, dict):
            raise DeepThinkProtocolError("final_answer must be an object or null.")
        answer = final_ans.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise DeepThinkProtocolError("final_answer.answer must be a non-empty string.")
        confidence_raw = final_ans.get("confidence", 0.8)
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = 0.8
        result["is_final"] = True
        result["final_answer"] = answer
        result["confidence"] = min(max(confidence, 0.0), 1.0)
        return result

    action = parsed.get("action")
    if action is not None:
        if isinstance(action, str):
            action = {"tool": action, "params": {}}
        if not isinstance(action, dict):
            raise DeepThinkProtocolError("action must be an object or null.")
        tool_name = action.get("tool")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise DeepThinkProtocolError("action.tool must be a non-empty string.")
        tool_params = action.get("params", {})
        if tool_params is None:
            tool_params = {}
        if not isinstance(tool_params, dict):
            tool_params = {}
        result["tool_name"] = tool_name.strip()
        result["tool_params"] = tool_params
        result["action_str"] = json.dumps(
            {"tool": result["tool_name"], "params": tool_params},
            ensure_ascii=False,
        )

    return result


def _repair_json_text(agent: "DeepThinkAgent", text: str) -> str:
    repaired = (text or "").strip()
    if not repaired:
        return repaired
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = repaired.replace("None", "null")
    if "'" in repaired and '"' not in repaired:
        repaired = repaired.replace("'", '"')
    return repaired


def _regex_parse_fallback(agent: "DeepThinkAgent", text: str) -> Optional[Dict[str, Any]]:
    body = (text or "").strip()
    if not body:
        return None
    thinking_match = re.search(r'"thinking"\s*:\s*"([^"]*)"', body, re.DOTALL)
    final_match = re.search(r'"answer"\s*:\s*"([^"]+)"', body, re.DOTALL)
    tool_match = re.search(r'"tool"\s*:\s*"([^"]+)"', body)

    payload: Dict[str, Any] = {}
    if thinking_match:
        payload["thinking"] = thinking_match.group(1)
    if final_match:
        payload["final_answer"] = {"answer": final_match.group(1), "confidence": 0.6}
    if tool_match:
        payload["action"] = {"tool": tool_match.group(1), "params": {}}
    return payload or None


def _try_parse_structured_actions(content: str) -> List[Dict[str, Any]]:
    """Try to parse LLMStructuredResponse-style JSON actions from text.

    Returns a list of action dicts [{name, parameters}] or empty list
    if the content is not a valid structured response.
    """
    text = (content or "").strip()
    if not text or not text.startswith("{"):
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return []
        try:
            payload = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []

    if not isinstance(payload, dict):
        return []
    actions_raw = payload.get("actions")
    if not isinstance(actions_raw, list) or not actions_raw:
        return []

    result: List[Dict[str, Any]] = []
    for action in actions_raw:
        if not isinstance(action, dict):
            continue
        name = action.get("name") or ""
        if not name:
            continue
        result.append({
            "name": str(name),
            "parameters": action.get("parameters") or {},
            "kind": action.get("kind", "tool_operation"),
        })
    return result


def _extract_json(agent: "DeepThinkAgent", text: str) -> str:
    """Extract the first complete top-level JSON object."""
    text = (text or "").strip()
    if not text:
        raise DeepThinkProtocolError("LLM output is empty.")

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    brace_count = 0
    start_idx = -1
    for i, char in enumerate(text):
        if char == '{':
            if brace_count == 0:
                start_idx = i
            brace_count += 1
        elif char == '}':
            if brace_count == 0:
                continue
            brace_count -= 1
            if brace_count == 0 and start_idx >= 0:
                return text[start_idx:i+1]

    raise DeepThinkProtocolError("No complete JSON object found in LLM output.")
