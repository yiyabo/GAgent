from __future__ import annotations

from types import SimpleNamespace

from app.prompts import prompt_manager
from app.routers.chat.prompt_builder import (
    build_simple_stream_chat_prompt,
    coerce_plain_text_chat_response,
    compose_plan_status,
)
from app.services.response_style import sanitize_professional_response_text
from app.services.deep_think_agent import DeepThinkAgent
from app.services import tool_schemas


async def _noop_tool_executor(_name: str, _params: dict):
    return {"success": True}


def _build_deep_think_agent() -> DeepThinkAgent:
    return DeepThinkAgent(
        llm_client=SimpleNamespace(),
        available_tools=[
            "sequence_fetch",
            "bio_tools",
            "deeppl",
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
    assert any("tool_operation: deeppl" in line for line in base_actions)
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


def test_deep_think_native_and_legacy_prompts_share_effort_matching_and_bio_priority_rules() -> None:
    agent = _build_deep_think_agent()
    native_prompt = agent._build_native_system_prompt()
    legacy_prompt = agent._build_system_prompt()

    for prompt in (native_prompt, legacy_prompt):
        assert "First classify the request" in prompt
        assert "Default to the lightest path that fully satisfies the user." in prompt
        assert "Do NOT start broad web/literature research" in prompt
        assert "For accession-based FASTA downloads, call sequence_fetch first." in prompt
        assert "ALWAYS try bio_tools first before claude_code" in prompt
        assert "Never use claude_code as fallback for sequence_fetch failures." in prompt
        assert "Never use claude_code as fallback for bio_tools input-conversion/parsing failures." in prompt

    assert "PROTOCOL BOUNDARY (NATIVE TOOL CALLING)" in native_prompt
    assert "PROTOCOL BOUNDARY (LEGACY JSON)" in legacy_prompt


def test_chat_prompts_default_to_professional_non_emoji_style() -> None:
    agent = SimpleNamespace(
        plan_session=SimpleNamespace(plan_id=None, outline=lambda **_: "[no plan]", summaries_for_prompt=lambda limit=10: ""),
        extra_context={},
        history=[],
        mode="chat",
        conversation_id="conv_1",
        MAX_HISTORY=10,
    )
    simple_prompt = build_simple_stream_chat_prompt(agent, "请介绍一下你自己")
    deep_agent = _build_deep_think_agent()
    native_prompt = deep_agent._build_native_system_prompt()

    for prompt in (simple_prompt, native_prompt):
        lowered = prompt.lower()
        assert "do not use celebratory, decorative, or playful emojis by default" in lowered
        assert "professional" in lowered


def test_response_style_sanitizer_removes_decorative_heading_emoji() -> None:
    raw = "🎉 太好了！\n## ✅ 目前你们已经覆盖的核心模块\n- 🚀 下一步建议"
    cleaned = sanitize_professional_response_text(raw)
    assert cleaned == "太好了！\n## 目前你们已经覆盖的核心模块\n- 下一步建议"


def test_plain_text_chat_response_coercion_sanitizes_emoji_heavy_reply() -> None:
    raw = '{"llm_reply":{"message":"🎉 太好了！\\n## ✅ 结论\\n- 🚀 下一步"}}'
    cleaned = coerce_plain_text_chat_response(raw)
    assert cleaned == "太好了！\n## 结论\n- 下一步"


def test_deep_think_prompt_boundaries_prevent_cross_protocol_confusion() -> None:
    agent = _build_deep_think_agent()
    native_prompt = agent._build_native_system_prompt()
    legacy_prompt = agent._build_system_prompt()

    assert "Do NOT output legacy JSON keys like thinking/action/final_answer" in native_prompt
    assert "Do NOT output structured-agent JSON keys like llm_reply/actions" in native_prompt
    assert "Respond with valid JSON only using keys: thinking, action, final_answer" in legacy_prompt
    assert "Do NOT output structured-agent JSON keys like llm_reply/actions" in legacy_prompt
    assert "Respond with valid JSON only using keys: thinking, action, final_answer" not in native_prompt


def test_phagescope_prompt_and_schema_mark_proteins_as_result_not_submit_module() -> None:
    prompts = prompt_manager.get_category("structured_agent")
    base_actions = prompts["action_catalog"]["base_actions"]
    phagescope_line = next(line for line in base_actions if "tool_operation: phagescope" in line)
    assert "proteins" in phagescope_line
    assert "not a submit module" in phagescope_line

    common_rules = prompts["guidelines"]["common_rules"]
    phagescope_rule = next(
        rule for rule in common_rules if "modulelist" in rule and "proteins" in rule
    )
    lowered_rule = phagescope_rule.lower()
    assert "submit modules" in lowered_rule
    assert "result/output names" in lowered_rule

    schema = tool_schemas.TOOL_REGISTRY["phagescope"]
    module_desc = schema["function"]["parameters"]["properties"]["modulelist"]["description"].lower()
    assert "submit modules only" in module_desc
    assert "proteins" in module_desc
    assert "not use result/output names" in module_desc
