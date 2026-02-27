"""Prompt building utilities for structured chat responses."""

import json
import logging
import re
from typing import Any, Dict, List

from app.config.tool_policy import get_tool_policy, is_tool_allowed
from app.prompts import prompt_manager

logger = logging.getLogger(__name__)


def should_use_deep_think(agent: Any, message: str) -> bool:
    """Check if deep think mode should be activated."""
    # Check explicit trigger
    if message.startswith("/think ") or message.startswith("/deep"):
        return True
    # Check context flag
    if agent.extra_context.get("deep_think_enabled", False):
        return True
    # Default: force DeepThink for plan creation / research planning.
    # This prevents shallow plans and enables rubric-based self-optimization.
    msg = (message or "").strip().lower()
    if not msg:
        return False
    plan_keywords = (
        "create plan",
        "make a plan",
        "plan for",
        "research plan",
        "project plan",
        "roadmap",
        "decompose",
        "break down",
        "task tree",
    )
    if any(k in msg for k in plan_keywords):
        return True
    return False


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
        f"History (latest {agent.MAX_HISTORY} messages):\n{history_text}",
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
    return "\n".join(prompt_parts)


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
    truncated = agent.history[-agent.MAX_HISTORY :]
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
