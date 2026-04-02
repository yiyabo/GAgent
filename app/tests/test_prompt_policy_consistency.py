from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.prompts import prompt_manager
from app.routers.chat.prompt_builder import (
    agent_history_limit,
    build_simple_stream_chat_prompt,
    coerce_plain_text_chat_response,
    compose_plan_status,
    rewrite_plain_chat_execution_claims,
)
from app.services.foundation.settings import CHAT_HISTORY_ABS_MAX
from app.services.response_style import sanitize_professional_response_text
from app.services.deep_think_agent import DeepThinkAgent
from app.services import tool_schemas
from app.routers.chat.agent import _build_brief_execute_continuation_summary


async def _noop_tool_executor(_name: str, _params: dict):
    return {"success": True}


def _build_deep_think_agent(request_profile: dict | None = None) -> DeepThinkAgent:
    return DeepThinkAgent(
        llm_client=SimpleNamespace(),
        available_tools=[
            "sequence_fetch",
            "bio_tools",
            "deeppl",
            "web_search",
            "code_executor",
            "plan_operation",
            "deliverable_submit",
        ],
        tool_executor=_noop_tool_executor,
        request_profile=request_profile,
    )


def test_structured_action_catalog_includes_bio_tools() -> None:
    prompts = prompt_manager.get_category("structured_agent")
    base_actions = prompts["action_catalog"]["base_actions"]
    assert any("tool_operation: bio_tools" in line for line in base_actions)
    assert any("tool_operation: sequence_fetch" in line for line in base_actions)
    assert any("tool_operation: deeppl" in line for line in base_actions)
    bio_line = next(line for line in base_actions if "tool_operation: bio_tools" in line)
    assert "sequence_text" in bio_line


def test_structured_action_catalog_includes_deliverable_submit() -> None:
    prompts = prompt_manager.get_category("structured_agent")
    base_actions = prompts["action_catalog"]["base_actions"]
    line = next(line for line in base_actions if "tool_operation: deliverable_submit" in line)
    assert "artifacts" in line
    assert "DELIVERABLES_INGEST_MODE" in line


def test_structured_action_catalog_forbids_plan_status_mutation_via_plan_operation() -> None:
    prompts = prompt_manager.get_category("structured_agent")
    base_actions = prompts["action_catalog"]["base_actions"]
    note = next(line for line in base_actions if line.strip().startswith("NOTE:"))
    assert "Do not call plan_operation/task_operation just to mark the current task completed or failed" in note


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
        assert "ALWAYS try bio_tools first before code_executor" in prompt
        assert "Never use code_executor as fallback for sequence_fetch failures." in prompt
        assert "Never use code_executor as fallback for bio_tools input-conversion/parsing failures." in prompt
        assert "do NOT use plan_operation or task_operation just to mark that task completed/failed" in prompt

    assert "PROTOCOL BOUNDARY (NATIVE TOOL CALLING)" in native_prompt
    assert "PROTOCOL BOUNDARY (LEGACY JSON)" in legacy_prompt
    assert "Promote specific files into the session Deliverables bundle." in legacy_prompt


def test_deep_think_prompts_require_structured_plan_tool_for_explicit_plan_requests() -> None:
    agent = _build_deep_think_agent()
    native_prompt = agent._build_native_system_prompt()
    legacy_prompt = agent._build_system_prompt()

    for prompt in (native_prompt, legacy_prompt):
        assert "plan or task breakdown" in prompt
        assert "use plan_operation to create or update a structured plan" in prompt
        assert "prose-only pseudo-plan" in prompt


def test_deep_think_prompts_pin_real_structured_plan_actions_when_required() -> None:
    agent = _build_deep_think_agent(
        {
            "requires_structured_plan": True,
            "plan_request_mode": "update_bound",
            "current_plan_id": 42,
        }
    )
    native_prompt = agent._build_native_system_prompt()
    legacy_prompt = agent._build_system_prompt()

    for prompt in (native_prompt, legacy_prompt):
        assert "STRUCTURED PLAN REQUIREMENT" in prompt
        assert "prose-only answer does NOT satisfy this request" in prompt
        assert "bound plan_id is 42" in prompt


def test_plan_operation_guidance_makes_research_optional() -> None:
    agent = _build_deep_think_agent()
    native_prompt = agent._build_native_system_prompt()
    legacy_prompt = agent._build_system_prompt()

    assert "research is optional" in native_prompt
    assert "Research before planning only when current external best practices" in legacy_prompt


def test_agent_history_limit_clamps_overrides() -> None:
    """Manual max_history_messages / MAX_HISTORY must not exceed CHAT_HISTORY_ABS_MAX."""
    over = SimpleNamespace(max_history_messages=CHAT_HISTORY_ABS_MAX + 50)
    assert agent_history_limit(over) == CHAT_HISTORY_ABS_MAX
    legacy_over = SimpleNamespace(MAX_HISTORY=CHAT_HISTORY_ABS_MAX + 10)
    assert agent_history_limit(legacy_over) == CHAT_HISTORY_ABS_MAX
    small = SimpleNamespace(MAX_HISTORY=10)
    assert agent_history_limit(small) == 10


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


def test_response_style_sanitizer_is_passthrough() -> None:
    raw = "🎉 太好了！\n## ✅ 目前你们已经覆盖的核心模块\n- 🚀 下一步建议"
    cleaned = sanitize_professional_response_text(raw)
    assert cleaned == raw.strip()


def test_plain_text_chat_response_coercion_preserves_emoji() -> None:
    raw = '{"llm_reply":{"message":"🎉 太好了！\\n## ✅ 结论\\n- 🚀 下一步"}}'
    cleaned = coerce_plain_text_chat_response(raw)
    assert "🎉" in cleaned
    assert "✅" in cleaned


def test_deep_think_prompt_boundaries_prevent_cross_protocol_confusion() -> None:
    agent = _build_deep_think_agent()
    native_prompt = agent._build_native_system_prompt()
    legacy_prompt = agent._build_system_prompt()

    assert "Do NOT output legacy JSON keys like thinking/action/final_answer" in native_prompt
    assert "Do NOT output structured-agent JSON keys like llm_reply/actions" in native_prompt
    assert "Respond with valid JSON only using keys: thinking, action, final_answer" in legacy_prompt
    assert "Do NOT output structured-agent JSON keys like llm_reply/actions" in legacy_prompt
    assert "Respond with valid JSON only using keys: thinking, action, final_answer" not in native_prompt


def test_deep_think_slim_evidence_keeps_json_when_storage_paths_in_single_line() -> None:
    """Regression: single-line tool JSON must not become empty after redaction (fallback synthesis)."""
    agent = _build_deep_think_agent()
    blob = (
        '[file_operations] {"tool": "file_operations", "success": true, '
        '"result": {"count": 11, "items": [], '
        '"storage": {"result_path": "/runtime/x/tool_outputs/job/step/result.json"}}}'
    )
    slim = agent._slim_evidence_text_for_synthesis(blob)
    assert "11" in slim or "count" in slim
    assert "tool_outputs" not in slim.lower()


def test_brief_execute_followup_prompt_skips_recent_chat_history() -> None:
    agent = DeepThinkAgent(
        llm_client=SimpleNamespace(),
        available_tools=["result_interpreter", "file_operations"],
        tool_executor=_noop_tool_executor,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "brevity_hint": True,
        },
    )
    context = {
        "request_tier": "execute",
        "intent_type": "execute_task",
        "brevity_hint": True,
        "user_message": "继续执行一下",
        "chat_history": [
            {"role": "user", "content": "更早的旧请求 A"},
            {"role": "assistant", "content": "更早的旧总结 B"},
            {"role": "user", "content": "中间的旧请求 C"},
            {"role": "assistant", "content": "中间的旧总结 D"},
            {"role": "user", "content": "再往后的旧请求 E"},
            {"role": "assistant", "content": "再往后的旧总结 F"},
            {"role": "user", "content": "上一轮失败的是 result_interpreter 和 code_executor"},
            {"role": "assistant", "content": "上一次已经确认 code_executor 400 修好了，下一步继续重跑宿主筛选"},
            {"role": "assistant", "content": ""},
        ],
        "recent_tool_results": [
            {"tool": "phagescope", "summary": "旧的批量上传测试完成"},
            {"tool": "result_interpreter", "summary": "当前筛选已产出 10071 条结果"},
        ],
        "continuation_summary": {
            "previous_user_request": "重新跑宿主筛选",
            "previous_assistant_summary": "上一次已经确认 code_executor 400 修好了，下一步继续重跑宿主筛选",
            "known_paths": ["/Users/apple/LLM/agent/phagescope/gvd_phage_meta_data.tsv"],
            "known_filenames": ["gvd_phage_meta_data.tsv"],
            "latest_tool_result": "result_interpreter: 当前筛选已产出 10071 条结果",
            "recent_image_artifacts": ["figure.png (code_executor)"],
        },
    }

    native_prompt = agent._build_native_system_prompt(context=context)
    legacy_prompt = agent._build_system_prompt(context=context)

    for prompt in (native_prompt, legacy_prompt):
        assert "=== RECENT CONVERSATION ===" not in prompt
        assert "=== EXECUTION CONTINUATION SUMMARY ===" in prompt
        assert "=== RECENT CONTINUATION CONTEXT ===" in prompt
        assert "Focus on the current execution result or current task outcome." in prompt
        assert "Known path anchor: /Users/apple/LLM/agent/phagescope/gvd_phage_meta_data.tsv" in prompt
        assert "Known filename anchors: gvd_phage_meta_data.tsv" in prompt
        assert "Recent image artifacts: figure.png (code_executor)" in prompt
        assert "当前筛选已产出 10071 条结果" in prompt
        assert "旧的批量上传测试完成" not in prompt
        assert "上一轮失败的是 result_interpreter 和 code_executor" in prompt
        assert "上一次已经确认 code_executor 400 修好了" in prompt
        assert "更早的旧请求 A" not in prompt
        assert "更早的旧总结 B" not in prompt
        assert "再往后的旧请求 E" in prompt
        assert "再往后的旧总结 F" in prompt


def test_execute_tier_prompt_warns_against_progress_recaps_for_brief_followups() -> None:
    agent = DeepThinkAgent(
        llm_client=SimpleNamespace(),
        available_tools=["result_interpreter"],
        tool_executor=_noop_tool_executor,
        request_profile={
            "request_tier": "execute",
            "intent_type": "execute_task",
            "brevity_hint": True,
        },
    )

    native_prompt = agent._build_native_system_prompt()
    legacy_prompt = agent._build_system_prompt()

    for prompt in (native_prompt, legacy_prompt):
        assert "Do not recap prior project milestones" in prompt
        assert "Do not append next-step menus" in prompt
        assert "continue from that anchor instead of restarting broad workspace discovery" in prompt


def test_brief_execute_continuation_summary_extracts_path_anchors_from_older_history() -> None:
    agent = SimpleNamespace(
        history=[
            {
                "role": "assistant",
                "content": "较早说明里提到源文件在 /Users/apple/LLM/agent/phagescope/gvd_phage_meta_data.tsv",
            },
            {"role": "user", "content": "再跑一轮完整的吧"},
            {
                "role": "assistant",
                "content": "code_executor 400 已修复，下一步直接继续宿主筛选，不要回到路径排查。",
            },
        ],
        extra_context={
            "recent_tool_results": [
                {
                    "tool": "result_interpreter",
                    "summary": "当前筛选已产出 10071 条结果",
                    "result": {"work_dir": "/Users/apple/LLM/agent/runtime/session_demo"},
                }
            ]
        },
    )
    routing_decision = SimpleNamespace(
        request_tier="execute",
        intent_type="execute_task",
        brevity_hint=True,
    )

    summary = _build_brief_execute_continuation_summary(agent, routing_decision)

    assert summary is not None
    assert summary["previous_user_request"] == "再跑一轮完整的吧"
    assert "宿主筛选" in summary["previous_assistant_summary"]
    assert "/Users/apple/LLM/agent/phagescope/gvd_phage_meta_data.tsv" in summary["known_paths"]
    assert "gvd_phage_meta_data.tsv" in summary["known_filenames"]
    assert summary["latest_tool_result"].startswith("result_interpreter:")


def test_brief_execute_continuation_summary_includes_recent_image_anchors() -> None:
    agent = SimpleNamespace(
        history=[
            {"role": "user", "content": "把图重新整理一下"},
            {"role": "assistant", "content": "上一轮已经产出封面图和摘要图。"},
        ],
        extra_context={
            "recent_image_artifacts": [
                {
                    "path": "tool_outputs/run_2/cover.png",
                    "display_name": "cover.png",
                    "source_tool": "code_executor",
                },
                {
                    "path": "tool_outputs/run_2/summary.png",
                    "display_name": "summary.png",
                    "source_tool": "code_executor",
                },
            ]
        },
    )
    routing_decision = SimpleNamespace(
        request_tier="execute",
        intent_type="execute_task",
        brevity_hint=True,
    )

    summary = _build_brief_execute_continuation_summary(agent, routing_decision)

    assert summary is not None
    assert summary["recent_image_artifacts"] == [
        "cover.png (code_executor)",
        "summary.png (code_executor)",
    ]


def test_plain_chat_execution_claims_are_rewritten_to_non_committal_text() -> None:
    rewritten = rewrite_plain_chat_execution_claims("我现在开始执行并给你生成结果。")

    assert rewritten == "我可以继续执行这个任务；如果你要我现在开工，我会进入执行流程。"


def test_plain_chat_execution_claims_keep_quoted_phrase_explanations() -> None:
    rewritten = rewrite_plain_chat_execution_claims(
        'The phrase "starting now" means 立即开始。'
    )

    assert rewritten == 'The phrase "starting now" means 立即开始。'


def test_plain_chat_execution_claims_preserve_english_reply_language() -> None:
    rewritten = rewrite_plain_chat_execution_claims(
        "Sure, starting now. I'll generate the report."
    )

    assert (
        rewritten
        == "I can continue with this task; if you want me to start now, I'll switch into the execution flow."
    )


def test_deep_think_filters_internal_storage_artifacts_from_evidence() -> None:
    agent = _build_deep_think_agent()
    payload = {
        "stdout": (
            "Generated file at /Users/apple/LLM/agent/runtime/session_demo/tool_outputs/job_dt_x/"
            "step_1_file_operations_abc123/result.json\n"
            "Generated file at /Users/apple/LLM/agent/runtime/session_demo/tool_outputs/job_dt_x/"
            "step_1_file_operations_abc123/manifest.json\n"
            "Generated file at /Users/apple/LLM/agent/runtime/session_demo/tool_outputs/job_dt_x/"
            "step_1_file_operations_abc123/preview.json\n"
            "Generated file at /Users/apple/LLM/agent/runtime/session_demo/deliverables/manifest_latest.json\n"
            "Generated file at /Users/apple/LLM/agent/results/report.csv"
        ),
    }

    evidence = agent._extract_evidence("file_operations", {}, payload)
    refs = [item["ref"] for item in evidence if item.get("type") == "file"]

    assert "/Users/apple/LLM/agent/results/report.csv" in refs
    assert not any(ref.endswith("/result.json") for ref in refs)
    assert not any(ref.endswith("/manifest.json") for ref in refs)
    assert not any(ref.endswith("/preview.json") for ref in refs)
    assert not any(ref.endswith("/deliverables/manifest_latest.json") for ref in refs)


def test_deep_think_emit_artifacts_skips_internal_storage_files() -> None:
    seen: list[dict[str, str]] = []

    async def _on_artifact(meta: dict[str, str]) -> None:
        seen.append(meta)

    agent = DeepThinkAgent(
        llm_client=SimpleNamespace(),
        available_tools=["file_operations"],
        tool_executor=_noop_tool_executor,
        on_artifact=_on_artifact,
    )

    payload = {
        "summary": "Generated file at /Users/apple/LLM/agent/runtime/session_demo/tool_outputs/job_dt_x/step_1_file_operations_abc123/result.json",
        "report": "saved to /Users/apple/LLM/agent/results/report.csv",
    }

    asyncio.run(agent._emit_artifacts("file_operations", payload, 1))

    assert len(seen) == 1
    assert seen[0]["path"].endswith("/report.csv")


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
    actions = schema["function"]["parameters"]["properties"]["action"]["enum"]
    assert "ping" in actions, "DeepThink schema must expose ping for API/connectivity checks"
    module_desc = schema["function"]["parameters"]["properties"]["modulelist"]["description"].lower()
    assert "submit modules only" in module_desc
    assert "proteins" in module_desc
    assert "not use result/output names" in module_desc

    ping_rule = next(
        rule for rule in common_rules if "ping" in rule and "PhageScope" in rule
    )
    assert "file_operations" in ping_rule.lower()
