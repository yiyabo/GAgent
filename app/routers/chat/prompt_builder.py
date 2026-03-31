"""Prompt building utilities for structured chat responses."""

import json
import logging
import re
from typing import Any, Dict, List

from app.config.tool_policy import get_tool_policy, is_tool_allowed
from app.prompts import prompt_manager
from app.services.foundation.settings import CHAT_HISTORY_ABS_MAX, get_settings
from app.services.response_style import (
    PROFESSIONAL_STYLE_INSTRUCTION,
    sanitize_professional_response_text,
)
from .request_routing import resolve_request_routing

logger = logging.getLogger(__name__)
_PLAIN_CHAT_EXECUTION_PROMISE_RE = re.compile(
    r"^(?:我现在开始|我这就开始|我开始执行|我来执行|我去执行|我去跑|马上开始|正在生成|稍等我去跑一下|"
    r"starting now\b|i will\b.*\b(run|execute|start|generate|process)\b|"
    r"i'?m (?:starting|running|generating) now\b)",
    flags=re.IGNORECASE,
)
_PLAIN_CHAT_EXECUTION_PREFIX_RE = re.compile(
    r"^(?:sure|ok(?:ay)?|alright|got it|好的|好|行|没问题)[,，!！.。\s]+",
    flags=re.IGNORECASE,
)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def agent_history_limit(agent: Any) -> int:
    """How many recent chat messages to inject into prompts (instance, legacy, or settings)."""
    lim = getattr(agent, "max_history_messages", None)
    if isinstance(lim, int) and lim > 0:
        return max(1, min(CHAT_HISTORY_ABS_MAX, lim))
    legacy = getattr(agent, "MAX_HISTORY", None)
    if isinstance(legacy, int) and legacy > 0:
        return max(1, min(CHAT_HISTORY_ABS_MAX, legacy))
    try:
        raw = int(getattr(get_settings(), "chat_history_max_messages", 80))
    except Exception:
        raw = 80
    return max(1, min(CHAT_HISTORY_ABS_MAX, raw))


def should_use_deep_think(agent: Any, message: str) -> bool:
    """Check if the router would select a DeepThink engine path."""
    decision = resolve_request_routing(
        message=message,
        history=getattr(agent, "history", None),
        context=getattr(agent, "extra_context", None),
        plan_id=getattr(getattr(agent, "plan_session", None), "plan_id", None),
        current_task_id=(getattr(agent, "extra_context", {}) or {}).get("current_task_id"),
    )
    return decision.use_deep_think


# Rough char-to-token ratio ~3.5; cap at ~28k tokens.
_MAX_PROMPT_CHARS = 100_000


def build_prompt(agent: Any, user_message: str) -> str:
    plan_bound = agent.plan_session.plan_id is not None
    history_text = format_history(agent)

    # Extract memories from extra_context and format separately.
    memories = agent.extra_context.pop("memories", None)
    memory_section = format_memories(memories) if memories else ""

    context_text = json.dumps(agent.extra_context, ensure_ascii=False, indent=2)
    plan_outline = agent.plan_session.outline(max_depth=4, max_nodes=100)
    plan_status = compose_plan_status(agent, plan_bound)
    plan_catalog = compose_plan_catalog(agent, plan_bound)
    actions_catalog = compose_action_catalog(agent, plan_bound)
    guidelines = compose_guidelines(agent, plan_bound)

    prompt_parts = [
        "You are an AI assistant that manages research plans represented as task trees.",
        f"Current mode: {agent.mode}",
        f"Conversation ID: {agent.conversation_id or 'N/A'}",
        f"Session binding: {plan_status}",
        f"Extra context:\n{context_text}",
    ]

    # Add memory section if relevant memories exist.
    if memory_section:
        prompt_parts.append(memory_section)

    prompt_parts.extend([
        f"History (latest {agent_history_limit(agent)} messages):\n{history_text}",
        "\n=== Plan Overview ===",
        plan_outline,
    ])
    if plan_catalog:
        prompt_parts.append(plan_catalog)
    prompt_parts.extend([
        "\nReturn a JSON object that matches the following schema exactly:",
        agent.schema_json,
        "\nAction catalog:",
        actions_catalog,
        "\nGuidelines:",
        guidelines,
        f"\nUser message: {user_message}",
        "Respond with the JSON object now.",
    ])
    prompt = "\n".join(prompt_parts)
    if len(prompt) > _MAX_PROMPT_CHARS:
        logger.warning(
            "Chat prompt too long (%d chars), truncating plan outline.",
            len(prompt),
        )
        # Truncate plan outline first (usually the largest section).
        outline_marker = "\n=== Plan Overview ==="
        idx = prompt.find(outline_marker)
        if idx != -1:
            end_idx = prompt.find("\nReturn a JSON", idx)
            if end_idx != -1:
                prompt = prompt[:idx] + "\n=== Plan Overview ===\n[Omitted due to prompt size limit]\n" + prompt[end_idx:]
        if len(prompt) > _MAX_PROMPT_CHARS:
            prompt = prompt[:_MAX_PROMPT_CHARS] + "\n... [TRUNCATED]\nRespond with the JSON object now."
    return prompt


def build_simple_stream_chat_prompt(agent: Any, user_message: str) -> str:
    """
    Prompt for plan-unbound *stream_simple_chat*: plain-language replies only.

    Must not ask for structured JSON; the stream path does not parse tool actions.
    """
    plan_bound = agent.plan_session.plan_id is not None
    history_text = format_history(agent)

    memories = agent.extra_context.pop("memories", None)
    memory_section = format_memories(memories) if memories else ""

    ctx_copy = dict(agent.extra_context)
    context_text = json.dumps(ctx_copy, ensure_ascii=False, indent=2)

    plan_outline = agent.plan_session.outline(max_depth=4, max_nodes=100)
    plan_status = compose_plan_status(agent, plan_bound)
    plan_catalog = compose_plan_catalog(agent, plan_bound)

    prompt_parts = [
        "You are a helpful AI assistant for research planning and bioinformatics workflows.",
        f"Current mode: {agent.mode}",
        f"Conversation ID: {agent.conversation_id or 'N/A'}",
        f"Session binding: {plan_status}",
        "",
        "=== OUTPUT FORMAT (STRICT) ===",
        "This channel does NOT execute tools (no web_search, no file access, no APIs).",
        "Reply in plain natural language only. Markdown is allowed.",
        "Match the user's language by default, unless they explicitly ask you to switch languages.",
        PROFESSIONAL_STYLE_INSTRUCTION,
        "Do NOT output JSON, YAML, or XML. Do NOT wrap the reply in code fences unless showing a short code sample.",
        "Do NOT emit tool call payloads or {\"llm_reply\": ...} schemas — they will be shown raw to the user and will break the UI.",
        "If the user needs live web data, local file inspection, or any tool-backed verification, say clearly that this plain chat channel cannot verify those facts directly.",
        "Never claim that you have started executing, running, or generating anything in this channel.",
        "If the user asks you to start or continue execution, say plainly that execution has not started in plain chat and that a separate execution flow is required.",
        "",
        f"Extra context:\n{context_text}",
    ]
    if memory_section:
        prompt_parts.append(memory_section)

    prompt_parts.extend(
        [
            f"History (latest {agent_history_limit(agent)} messages):\n{history_text}",
            "\n=== Plan Overview ===",
            plan_outline,
        ]
    )
    if plan_catalog:
        prompt_parts.append(plan_catalog)

    prompt_parts.extend(
        [
            f"\nUser message: {user_message}",
            "\nRespond in plain language now.",
        ]
    )
    prompt = "\n".join(prompt_parts)
    if len(prompt) > _MAX_PROMPT_CHARS:
        logger.warning(
            "Simple stream chat prompt too long (%d chars), truncating plan outline.",
            len(prompt),
        )
        outline_marker = "\n=== Plan Overview ==="
        idx = prompt.find(outline_marker)
        if idx != -1:
            end_idx = prompt.find("\n\nUser message:", idx)
            if end_idx != -1:
                prompt = (
                    prompt[:idx]
                    + "\n=== Plan Overview ===\n[Omitted due to prompt size limit]\n"
                    + prompt[end_idx:]
                )
        if len(prompt) > _MAX_PROMPT_CHARS:
            prompt = prompt[:_MAX_PROMPT_CHARS] + "\n... [TRUNCATED]\nRespond in plain language now."
    return prompt


def coerce_plain_text_chat_response(raw: str) -> str:
    """
    If the model ignored instructions and returned structured-agent JSON, extract
    llm_reply.message so we do not persist or display raw JSON to users.
    """
    if not raw:
        return raw
    stripped = raw.strip()
    if not stripped:
        return stripped
    cleaned = strip_code_fence(stripped)
    if not cleaned.startswith("{"):
        return sanitize_professional_response_text(stripped)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        return sanitize_professional_response_text(stripped)
    if not isinstance(obj, dict):
        return sanitize_professional_response_text(stripped)
    lr = obj.get("llm_reply")
    if isinstance(lr, dict):
        msg = lr.get("message")
        if isinstance(msg, str) and msg.strip():
            return sanitize_professional_response_text(msg.strip())
    return sanitize_professional_response_text(stripped)


def rewrite_plain_chat_execution_claims(raw: str) -> str:
    text = sanitize_professional_response_text(str(raw or "").strip())
    if not text:
        return text
    candidate = text
    while True:
        updated = _PLAIN_CHAT_EXECUTION_PREFIX_RE.sub("", candidate, count=1).strip()
        if updated == candidate:
            break
        candidate = updated
    if not _PLAIN_CHAT_EXECUTION_PROMISE_RE.match(candidate):
        return text
    if _CJK_RE.search(text):
        return "我可以继续执行这个任务；如果你要我现在开工，我会进入执行流程。"
    return "I can continue with this task; if you want me to start now, I'll switch into the execution flow."


def format_memories(memories: List[Dict[str, Any]]) -> str:
    """Format memory list into prompt text."""
    if not memories:
        return ""

    lines = ["\n=== Relevant Memories (from previous conversations) ==="]
    for mem in memories:
        content = mem.get("content", "")
        similarity = mem.get("similarity", 0)
        mem_type = mem.get("memory_type", "unknown")
        similarity_pct = int(similarity * 100) if similarity else 0
        lines.append(f"- [{similarity_pct}% match, type: {mem_type}] {content}")

    lines.append("(Use these memories as context to provide more relevant responses)")
    return "\n".join(lines)


def compose_plan_status(agent: Any, plan_bound: bool) -> str:
    if plan_bound:
        assert agent.plan_session.plan_id is not None
        return f"Currently bound Plan ID: {agent.plan_session.plan_id}"
    return (
        "This session is not bound to any plan. For complex or multi-step requests, "
        "automatically create and manage a plan. For simple single-step requests, "
        "respond directly without creating a plan."
    )


def compose_plan_catalog(agent: Any, plan_bound: bool) -> str:
    if plan_bound:
        return ""
    summaries = agent.plan_session.summaries_for_prompt(limit=10)
    return (
        "Available plans (up to 10, for reference):\n"
        f"{summaries}\n"
        "If the user wants to work with one of them, ask for the specific plan ID; otherwise keep clarifying needs."
    )


def compose_action_catalog(agent: Any, plan_bound: bool) -> str:
    prompts = get_structured_agent_prompts()
    base_actions = list(prompts["action_catalog"]["base_actions"])
    plan_actions = prompts["action_catalog"]["plan_actions"]
    selected_plan_actions = plan_actions["bound" if plan_bound else "unbound"]
    policy = get_tool_policy()
    filtered_base_actions: List[str] = []
    for line in base_actions:
        tool_name = agent._extract_tool_name(line)
        if tool_name and not is_tool_allowed(tool_name, policy):
            continue
        filtered_base_actions.append(line)
    return "\n".join(filtered_base_actions + list(selected_plan_actions))


def compose_guidelines(agent: Any, plan_bound: bool) -> str:
    prompts = get_structured_agent_prompts()
    common_rules = list(prompts["guidelines"]["common_rules"])
    scenario_rules = prompts["guidelines"]["scenario_rules"]
    selected_rules = scenario_rules["bound" if plan_bound else "unbound"]
    all_rules = common_rules + list(selected_rules)
    return "\n".join(
        f"{idx}. {rule}" for idx, rule in enumerate(all_rules, start=1)
    )


def get_structured_agent_prompts() -> Dict[str, Any]:
    prompts = prompt_manager.get_category("structured_agent")
    if not isinstance(prompts, dict):
        raise ValueError("structured_agent prompts must be a dictionary.")
    return prompts


def format_history(agent: Any) -> str:
    if not agent.history:
        return "<empty>"
    truncated = agent.history[-agent_history_limit(agent) :]
    return "\n".join(
        f"{item.get('role', 'user')}: {item.get('content', '')}"
        for item in truncated
    )


def strip_code_fence(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    # Fix incomplete Unicode escapes (e.g. "\u" not followed by 4 hex digits)
    # that some LLMs emit, which cause json.loads to fail with
    # "incomplete escape \u at position N".
    cleaned = re.sub(
        r'\\u(?![0-9a-fA-F]{4})',
        r'\\\\u',
        cleaned,
    )
    return cleaned
