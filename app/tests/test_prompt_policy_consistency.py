from __future__ import annotations

from types import SimpleNamespace

from app.prompts import prompt_manager
from app.routers.chat.prompt_builder import compose_plan_status
from app.services.deep_think_agent import DeepThinkAgent


async def _noop_tool_executor(_name: str, _params: dict):
    return {"success": True}


def _build_deep_think_agent() -> DeepThinkAgent:
    return DeepThinkAgent(
        llm_client=SimpleNamespace(),
        available_tools=[
            "sequence_fetch",
            "bio_tools",
            "web_search",
            "claude_code",
            "plan_operation",
        ],
        tool_executor=_noop_tool_executor,
    )


def test_structured_action_catalog_includes_bio_tools() -> None:
    prompts = prompt_manager.get_category("structured_agent")
    base_actions = prompts["action_catalog"]["base_actions"]
    assert any("tool_operation: bio_tools" in line for line in base_actions)
    assert any("tool_operation: sequence_fetch" in line for line in base_actions)
    bio_line = next(line for line in base_actions if "tool_operation: bio_tools" in line)
    assert "sequence_text" in bio_line


def test_unbound_rules_use_auto_create_plan_without_confirmation_language() -> None:
    prompts = prompt_manager.get_category("structured_agent")
    unbound_actions = prompts["action_catalog"]["plan_actions"]["unbound"]
    create_line = next(line for line in unbound_actions if "create_plan" in line)
    lower_line = create_line.lower()
    assert "automatically create" in lower_line
    assert "do not ask for confirmation first" in lower_line
    assert "agree" not in lower_line


def test_structured_info_rule_is_text_first_with_conditional_tool_use() -> None:
    prompts = prompt_manager.get_category("structured_agent")
    common_rules = prompts["guidelines"]["common_rules"]
    info_rule = next(
        rule for rule in common_rules if "for informational questions" in rule.lower()
    )
    lowered = info_rule.lower()
    assert "default to a direct text answer" in lowered
    assert "minimally necessary" in lowered
    assert "do not invoke any tools" not in lowered


def test_compose_plan_status_unbound_matches_auto_plan_policy() -> None:
    dummy_agent = SimpleNamespace(plan_session=SimpleNamespace(plan_id=None))
    status = compose_plan_status(dummy_agent, plan_bound=False).lower()
    assert "automatically create and manage a plan" in status
    assert "simple single-step requests" in status
    assert "only trigger plan-related actions when the user explicitly requests" not in status


def test_deep_think_native_and_legacy_prompts_share_aggressive_and_bio_priority_rules() -> None:
    agent = _build_deep_think_agent()
    native_prompt = agent._build_native_system_prompt()
    legacy_prompt = agent._build_system_prompt()

    for prompt in (native_prompt, legacy_prompt):
        assert "UNLIMITED resources" in prompt
        assert "better to call 5 tools" in prompt
        assert "For accession-based FASTA downloads, call sequence_fetch first." in prompt
        assert "ALWAYS try bio_tools first before claude_code" in prompt
        assert "Never use claude_code as fallback for sequence_fetch failures." in prompt
        assert "Never use claude_code as fallback for bio_tools input-conversion/parsing failures." in prompt

    assert "PROTOCOL BOUNDARY (NATIVE TOOL CALLING)" in native_prompt
    assert "PROTOCOL BOUNDARY (LEGACY JSON)" in legacy_prompt


def test_deep_think_prompt_boundaries_prevent_cross_protocol_confusion() -> None:
    agent = _build_deep_think_agent()
    native_prompt = agent._build_native_system_prompt()
    legacy_prompt = agent._build_system_prompt()

    assert "Do NOT output legacy JSON keys like thinking/action/final_answer" in native_prompt
    assert "Do NOT output structured-agent JSON keys like llm_reply/actions" in native_prompt
    assert "Respond with valid JSON only using keys: thinking, action, final_answer" in legacy_prompt
    assert "Do NOT output structured-agent JSON keys like llm_reply/actions" in legacy_prompt
    assert "Respond with valid JSON only using keys: thinking, action, final_answer" not in native_prompt
