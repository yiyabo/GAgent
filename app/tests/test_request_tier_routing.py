from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.routers.chat.agent import StructuredChatAgent
from app.routers.chat.request_routing import (
    build_request_tier_profile,
    resolve_intent_type,
    resolve_request_routing,
)


def test_request_tier_routes_greeting_to_light_auto_simple() -> None:
    decision = resolve_request_routing(message="你好呀")

    assert decision.request_tier == "light"
    assert decision.request_route_mode == "auto_simple"
    assert decision.thinking_visibility == "visible"
    assert decision.manual_deep_think is False


def test_request_tier_routes_latest_sources_request_to_research_auto_deepthink() -> None:
    decision = resolve_request_routing(
        message="给我 2025-2026 最新文献和来源，最好附上引用",
    )

    assert decision.request_tier == "research"
    assert decision.request_route_mode == "auto_deepthink"
    assert decision.thinking_visibility == "progress"
    assert decision.metadata()["progress_mode"] == "compact"
    assert "research_cue" in decision.route_reason_codes
    assert "time_sensitive_cue" in decision.route_reason_codes


def test_request_tier_routes_attachment_request_to_execute_auto_deepthink() -> None:
    decision = resolve_request_routing(
        message="帮我分析这个文件",
        context={"attachments": [{"type": "document", "path": "/tmp/a.pdf"}]},
    )

    assert decision.request_tier == "execute"
    assert decision.request_route_mode == "auto_deepthink"
    assert decision.thinking_visibility == "progress"
    assert "has_attachments" in decision.route_reason_codes

    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )
    assert profile.max_iterations == 6


def test_request_tier_keeps_manual_deepthink_visible_for_light_request() -> None:
    decision = resolve_request_routing(
        message="你好",
        context={"deep_think_enabled": True},
    )

    assert decision.request_tier == "light"
    assert decision.request_route_mode == "manual_deepthink"
    assert decision.thinking_visibility == "visible"
    assert decision.manual_deep_think is True

    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )
    assert profile.available_tools == []
    assert profile.max_iterations == 2
    assert profile.thinking_budget <= 400


def test_request_tier_promotes_depth_cue_out_of_light_bucket() -> None:
    decision = resolve_request_routing(message="请详细说说这个方向")

    assert decision.request_tier == "standard"
    assert decision.request_route_mode == "auto_simple"


def test_manual_deepthink_search_request_routes_to_research_with_tools() -> None:
    decision = resolve_request_routing(message="/think 你得仔细搜索一下！不要随便回复给我")

    assert decision.request_tier == "research"
    assert decision.request_route_mode == "manual_deepthink"
    assert decision.thinking_visibility == "visible"

    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )
    assert "web_search" in profile.available_tools
    assert "deliverable_submit" in profile.available_tools


def test_manual_deepthink_mixed_plan_request_routes_to_execute_with_plan_tool() -> None:
    decision = resolve_request_routing(message="/think 很好啊，那你针对于这个，制作一个plan来看看吧")

    assert decision.intent_type == "execute_task"
    assert decision.capability_floor == "tools"
    assert decision.request_tier == "execute"
    assert decision.request_route_mode == "manual_deepthink"
    assert "intent_plan_request" in decision.route_reason_codes
    assert decision.requires_structured_plan is True
    assert decision.plan_request_mode == "create"

    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )
    assert "plan_operation" in profile.available_tools
    assert profile.requires_structured_plan is True
    assert profile.plan_request_mode == "create"


def test_manual_deepthink_research_then_plan_request_still_routes_to_execute() -> None:
    decision = resolve_request_routing(
        message="/think 我的意思是，根据你上面说的进行的调研，制作一个plan给我，我们来完成你说的那些"
    )

    assert decision.intent_type == "execute_task"
    assert decision.capability_floor == "tools"
    assert decision.request_tier == "execute"
    assert decision.request_route_mode == "manual_deepthink"
    assert "intent_plan_request" in decision.route_reason_codes
    assert decision.requires_structured_plan is True
    assert decision.plan_request_mode == "create"

    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )
    assert "plan_operation" in profile.available_tools
    assert profile.plan_request_mode == "create"


def test_bound_plan_request_defaults_to_updating_current_plan() -> None:
    decision = resolve_request_routing(
        message="/think 基于上面的内容做一个plan",
        plan_id=42,
    )

    assert decision.intent_type == "execute_task"
    assert decision.capability_floor == "tools"
    assert decision.requires_structured_plan is True
    assert decision.plan_request_mode == "update_bound"


def test_bound_plan_request_with_explicit_new_language_creates_new_plan() -> None:
    decision = resolve_request_routing(
        message="/think 新建一个plan，和刚才那个分开",
        plan_id=42,
    )

    assert decision.intent_type == "execute_task"
    assert decision.capability_floor == "tools"
    assert decision.requires_structured_plan is True
    assert decision.plan_request_mode == "create_new"


def test_bound_plan_review_request_requires_review_operation() -> None:
    decision = resolve_request_routing(
        message="/think 你审核一下这个任务看看如何",
        plan_id=67,
        current_task_id=2,
    )

    assert decision.intent_type == "execute_task"
    assert decision.capability_floor == "tools"
    assert decision.requires_structured_plan is True
    assert decision.plan_request_mode == "update_bound"
    assert decision.requires_plan_review is True
    assert decision.requires_plan_optimize is False
    assert "intent_plan_review_request" in decision.route_reason_codes

    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )
    assert profile.requires_plan_review is True
    assert profile.requires_plan_optimize is False
    assert "plan_operation" in profile.available_tools


def test_bound_plan_review_and_optimize_request_requires_both_operations() -> None:
    decision = resolve_request_routing(
        message="/think 你先审核一下这个计划，然后帮我优化一下",
        plan_id=67,
    )

    assert decision.intent_type == "execute_task"
    assert decision.capability_floor == "tools"
    assert decision.requires_structured_plan is True
    assert decision.plan_request_mode == "update_bound"
    assert decision.requires_plan_review is True
    assert decision.requires_plan_optimize is True
    assert "intent_plan_review_request" in decision.route_reason_codes
    assert "intent_plan_optimize_request" in decision.route_reason_codes


def test_bound_plan_update_language_requires_optimize_operation() -> None:
    decision = resolve_request_routing(
        message="/think 感觉还不错，但是你貌似没有去更新plan，更新一下吧",
        plan_id=68,
    )

    assert decision.intent_type == "execute_task"
    assert decision.capability_floor == "tools"
    assert decision.requires_structured_plan is True
    assert decision.plan_request_mode == "update_bound"
    assert decision.requires_plan_review is False
    assert decision.requires_plan_optimize is True
    assert "intent_plan_optimize_request" in decision.route_reason_codes


def test_bound_plan_status_update_language_does_not_require_optimize_operation() -> None:
    decision = resolve_request_routing(
        message="/think 请给我更新一下这个计划的状态",
        plan_id=68,
    )

    assert decision.requires_plan_optimize is False
    assert "intent_plan_optimize_request" not in decision.route_reason_codes


def test_bound_plan_progress_followup_does_not_require_optimize_operation() -> None:
    decision = resolve_request_routing(
        message="update me on the plan",
        plan_id=68,
    )

    assert decision.requires_plan_optimize is False
    assert "intent_plan_optimize_request" not in decision.route_reason_codes


def test_english_followup_another_one_does_not_trigger_plan_mode() -> None:
    decision = resolve_request_routing(message="show me another one")

    assert decision.requires_structured_plan is False
    assert decision.plan_request_mode is None
    assert "intent_plan_request" not in decision.route_reason_codes


def test_file_followup_inherits_active_subject_and_keeps_local_inspect_tools() -> None:
    decision = resolve_request_routing(
        message="都有哪些数据在里面哇",
        context={
            "active_subject": {
                "kind": "workspace",
                "canonical_ref": "data/张老师卵巢癌单细胞数据",
                "display_ref": "data/张老师卵巢癌单细胞数据",
                "verification_state": "not_found",
                "salience": 5,
                "last_referenced_turn": 2,
            }
        },
        history=[
            {"role": "user", "content": "你阅读一下这个文件：data/张老师卵巢癌单细胞数据"},
            {"role": "assistant", "content": "我去看一下。"},
        ],
    )

    assert decision.request_tier == "light"
    assert decision.request_route_mode == "auto_deepthink"
    assert decision.capability_floor == "tools"
    assert decision.subject_resolution["source"] == "inherited"
    assert decision.simple_channel_allowed is False

    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )
    assert "file_operations" in profile.available_tools
    assert "result_interpreter" in profile.available_tools
    assert "code_executor" in profile.available_tools
    assert "deliverable_submit" in profile.available_tools
    assert profile.max_iterations == 2


def test_manual_deepthink_followup_keeps_local_inspect_floor() -> None:
    decision = resolve_request_routing(
        message="/think 都有哪些数据哇",
        context={
            "active_subject": {
                "kind": "workspace",
                "canonical_ref": "data/demo",
                "display_ref": "data/demo",
                "verification_state": "verified",
                "salience": 5,
                "last_referenced_turn": 3,
            }
        },
        history=[
            {"role": "user", "content": "看一下 data/demo"},
            {"role": "assistant", "content": "好的"},
        ],
    )

    assert decision.request_tier == "light"
    assert decision.request_route_mode == "manual_deepthink"
    assert decision.capability_floor == "tools"
    assert decision.simple_channel_allowed is False

    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )
    assert "file_operations" in profile.available_tools
    assert "result_interpreter" in profile.available_tools
    assert "code_executor" in profile.available_tools
    assert profile.max_iterations == 3


def test_phagescope_remote_verify_elevates_to_research_with_phagescope_tool() -> None:
    decision = resolve_request_routing(
        message="确认 PhageScope 访问权限，你测试一下",
        context={
            "active_subject": {
                "kind": "workspace",
                "canonical_ref": "data/张老师卵巢癌单细胞数据",
                "display_ref": "data/张老师卵巢癌单细胞数据",
                "verification_state": "not_found",
                "salience": 5,
                "last_referenced_turn": 2,
            }
        },
        history=[
            {"role": "user", "content": "你阅读一下这个文件：data/张老师卵巢癌单细胞数据"},
            {"role": "assistant", "content": "我去看一下。"},
        ],
    )

    assert decision.intent_type == "research"
    assert decision.capability_floor == "tools"
    assert "intent_phagescope_remote_verify" in decision.route_reason_codes
    assert decision.request_tier == "research"
    assert decision.request_route_mode == "auto_deepthink"

    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )
    assert "phagescope" in profile.available_tools
    assert profile.max_iterations == 8


def test_phagescope_task_download_outputs_elevates_to_research_without_anchor() -> None:
    """Log regression: English 'task' + 下载/输出/验证 + module names must not fall through to plain_chat."""
    decision = resolve_request_routing(
        message="/think 下载 task 38619 的 quality 和 annotation 输出进行验证",
        context={},
        history=[],
    )
    assert decision.intent_type == "research"
    assert "intent_phagescope_task_result" in decision.route_reason_codes
    assert decision.capability_floor == "tools"
    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )
    assert "phagescope" in profile.available_tools


def test_phagescope_task_status_followup_elevates_to_research_without_saying_phagescope() -> None:
    """Numeric task id + status wording + subject under phagescope/ must keep phagescope tool available."""
    intent, reasons = resolve_intent_type(
        message="你再咨询一下：38619，这个任务，看看是不是真的在跑",
        context={},
        subject_resolution={
            "kind": "file",
            "canonical_ref": "/Users/apple/LLM/agent/phagescope/gvd_phage_meta_data.tsv",
            "aliases": ["/Users/apple/LLM/agent/phagescope/gvd_phage_meta_data.tsv"],
            "source": "inherited",
            "continuity": "continued",
        },
    )
    assert intent == "research"
    assert "intent_phagescope_task_status" in reasons

    decision = resolve_request_routing(
        message="/think 你再咨询一下：38619，这个任务，看看是不是真的在跑",
        context={
            "active_subject": {
                "kind": "file",
                "canonical_ref": "/Users/apple/LLM/agent/phagescope/gvd_phage_meta_data.tsv",
                "display_ref": "phagescope/gvd_phage_meta_data.tsv",
                "aliases": ["/Users/apple/LLM/agent/phagescope/gvd_phage_meta_data.tsv"],
                "source": "inherited",
                "continuity": "continued",
            }
        },
        history=[
            {"role": "user", "content": "测试 phagescope 提交"},
            {"role": "assistant", "content": "已提交任务 38619。"},
        ],
    )
    assert decision.intent_type == "research"
    assert "intent_phagescope_task_status" in decision.route_reason_codes
    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )
    assert "phagescope" in profile.available_tools


def test_inherited_subject_data_inquiry_does_not_include_phagescope_without_remote_cue() -> None:
    decision = resolve_request_routing(
        message="都有哪些数据在里面哇",
        context={
            "active_subject": {
                "kind": "workspace",
                "canonical_ref": "data/张老师卵巢癌单细胞数据",
                "display_ref": "data/张老师卵巢癌单细胞数据",
                "verification_state": "not_found",
                "salience": 5,
                "last_referenced_turn": 2,
            }
        },
        history=[
            {"role": "user", "content": "你阅读一下这个文件：data/张老师卵巢癌单细胞数据"},
            {"role": "assistant", "content": "我去看一下。"},
        ],
    )

    assert decision.capability_floor == "tools"
    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )
    assert "phagescope" in profile.available_tools


def test_explicit_absolute_path_matches_existing_relative_subject_identity() -> None:
    workspace_path = str((Path.cwd() / "data/张老师卵巢癌单细胞数据").resolve())

    decision = resolve_request_routing(
        message=f"{workspace_path}\n这个路径，你再看看",
        context={
            "active_subject": {
                "kind": "workspace",
                "canonical_ref": "data/张老师卵巢癌单细胞数据",
                "display_ref": "data/张老师卵巢癌单细胞数据",
                "verification_state": "not_found",
                "salience": 5,
                "last_referenced_turn": 2,
            }
        },
        history=[
            {"role": "user", "content": "你阅读一下这个文件：data/张老师卵巢癌单细胞数据"},
            {"role": "assistant", "content": "我去看一下。"},
        ],
    )

    assert decision.subject_resolution["canonical_ref"] == workspace_path
    assert decision.subject_resolution["continuity"] == "continued"
    assert decision.subject_resolution["source"] == "explicit"


def test_local_mutation_followup_routes_to_execute_with_inherited_subject() -> None:
    decision = resolve_request_routing(
        message="你帮我直接解压吧，就放在对应的zip包那里即可",
        context={
            "active_subject": {
                "kind": "directory",
                "canonical_ref": "data/张老师卵巢癌单细胞数据/张老师卵巢癌单细胞数据",
                "display_ref": "data/张老师卵巢癌单细胞数据/张老师卵巢癌单细胞数据",
                "verification_state": "verified",
                "salience": 5,
                "last_referenced_turn": 4,
            },
            "last_subject_action_class": "inspect",
        },
        history=[
            {"role": "user", "content": "都有哪些数据在里面哇"},
            {"role": "assistant", "content": "我已经看到了里面的 zip 文件。"},
        ],
    )

    assert decision.intent_type == "local_mutation"
    assert decision.request_tier == "execute"
    assert decision.request_route_mode == "auto_deepthink"
    assert decision.capability_floor == "tools"
    assert decision.simple_channel_allowed is False
    assert decision.subject_resolution["source"] == "inherited"

    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )
    assert "terminal_session" in profile.available_tools


def test_reunzip_short_followup_routes_without_active_subject_in_context() -> None:
    """Regression: brief follow-ups like '重新解压' must not fall through to plain_chat."""
    decision = resolve_request_routing(
        message="OK的，你重新解压一下吧",
        context={"last_subject_action_class": "inspect"},
        history=[
            {"role": "user", "content": "看看目录里有什么"},
            {"role": "assistant", "content": "目录里有很多 zip 文件。"},
        ],
    )
    assert decision.intent_type == "local_mutation"
    assert decision.capability_floor == "tools"
    assert decision.simple_channel_allowed is False


def test_archive_followups_promote_to_local_mutation() -> None:
    messages = [
        "解压这些 zip",
        "把这些压缩包展开",
        "就在原目录解压",
    ]

    for message in messages:
        decision = resolve_request_routing(
            message=message,
            context={
                "active_subject": {
                    "kind": "directory",
                    "canonical_ref": "data/demo",
                    "display_ref": "data/demo",
                    "verification_state": "verified",
                    "salience": 5,
                    "last_referenced_turn": 3,
                },
                "last_subject_action_class": "inspect",
            },
            history=[
                {"role": "user", "content": "看看 data/demo 里有什么"},
                {"role": "assistant", "content": "我看到了几个 zip 压缩包。"},
            ],
        )
        assert decision.intent_type == "local_mutation"
        assert decision.capability_floor == "tools"
        assert decision.simple_channel_allowed is False


def test_unrelated_abstract_question_does_not_inherit_local_subject() -> None:
    decision = resolve_request_routing(
        message="解释一下 transformer 和 rnn 的区别",
        context={
            "active_subject": {
                "kind": "directory",
                "canonical_ref": "data/demo",
                "display_ref": "data/demo",
                "verification_state": "verified",
                "salience": 5,
                "last_referenced_turn": 3,
            },
            "last_subject_action_class": "inspect",
        },
        history=[
            {"role": "user", "content": "看看 data/demo 里有什么"},
            {"role": "assistant", "content": "我已经看到了里面的数据文件。"},
        ],
    )

    assert decision.intent_type == "chat"
    assert decision.subject_resolution["kind"] == "none"
    assert decision.capability_floor == "plain_chat"


def test_execute_tier_profile_includes_deliverable_submit() -> None:
    decision = resolve_request_routing(
        message="帮我分析这个文件并整理成可提交的结果",
        context={"attachments": [{"type": "document", "path": "/tmp/a.pdf"}]},
    )

    profile = build_request_tier_profile(
        decision,
        default_thinking_budget=10000,
        simple_thinking_budget=2000,
        default_max_iterations=64,
    )

    assert decision.request_tier == "execute"
    assert "deliverable_submit" in profile.available_tools


def test_process_unified_stream_delegates_light_request_to_simple_chat() -> None:
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=None)
    agent.extra_context = {}
    agent.history = []
    agent.session_id = None
    agent.llm_service = object()

    captured: dict[str, object] = {}

    async def _fake_stream_simple_chat(
        self,
        user_message: str,
        *,
        routing_decision=None,
        route_profile=None,
        event_sink=None,
    ):
        captured["user_message"] = user_message
        captured["route_mode"] = routing_decision.request_route_mode
        captured["request_tier"] = routing_decision.request_tier
        captured["thinking_visibility"] = routing_decision.thinking_visibility
        payload = {
            "type": "final",
            "payload": {
                "llm_reply": {"message": "hello"},
                "metadata": routing_decision.metadata(),
            },
        }
        if event_sink is not None:
            await event_sink(payload)
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    agent.stream_simple_chat = _fake_stream_simple_chat.__get__(
        agent, StructuredChatAgent
    )

    async def _collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in agent.process_unified_stream("你好"):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())

    assert captured == {
        "user_message": "你好",
        "route_mode": "auto_simple",
        "request_tier": "light",
        "thinking_visibility": "visible",
    }
    assert len(chunks) == 1
    assert '"request_route_mode": "auto_simple"' in chunks[0]


def test_start_task_followup_routes_to_execute_instead_of_plain_chat() -> None:
    decision = resolve_request_routing(
        message="你开始完成任务吧",
        history=[
            {"role": "user", "content": "已经没有 Claude Code了，我修改成其他的了。"},
            {"role": "assistant", "content": "我可以继续执行这个任务；如果要我现在开工，我会进入执行流程。"},
        ],
    )

    assert decision.intent_type == "execute_task"
    assert decision.capability_floor == "tools"
    assert decision.request_route_mode == "auto_deepthink"
    assert decision.request_tier == "execute"


def test_existing_image_display_routes_to_local_read_when_recent_images_exist() -> None:
    decision = resolve_request_routing(
        message="把刚才那张图片给我看",
        context={
            "recent_image_artifacts": [
                {
                    "path": "tool_outputs/run_1/cover.png",
                    "display_name": "cover.png",
                    "source_tool": "code_executor",
                }
            ]
        },
    )

    assert decision.intent_type == "local_read"
    assert decision.capability_floor == "tools"
    assert decision.request_route_mode == "auto_deepthink"
    assert "intent_show_existing_image" in decision.route_reason_codes


def test_existing_image_display_with_repair_context_still_routes_to_local_read() -> None:
    decision = resolve_request_routing(
        message="给我看看图片，我修复了刚刚的bug",
        context={
            "recent_image_artifacts": [
                {
                    "path": "/Users/apple/LLM/agent/phagescope/results/host_family_donut.png",
                    "display_name": "host_family_donut.png",
                    "source_tool": "file_operations",
                }
            ]
        },
    )

    assert decision.intent_type == "local_read"
    assert decision.capability_floor == "tools"
    assert decision.request_tier == "light"
    assert "intent_show_existing_image" in decision.route_reason_codes


def test_explicit_execution_still_overrides_existing_image_display() -> None:
    decision = resolve_request_routing(
        message="开始执行，然后把刚才那张图片给我看",
        context={
            "recent_image_artifacts": [
                {
                    "path": "tool_outputs/run_1/cover.png",
                    "display_name": "cover.png",
                    "source_tool": "code_executor",
                }
            ]
        },
    )

    assert decision.intent_type == "execute_task"
    assert decision.capability_floor == "tools"


def test_image_regeneration_followup_routes_to_execute() -> None:
    decision = resolve_request_routing(
        message="重新生成那张图，换个风格",
        context={
            "recent_image_artifacts": [
                {
                    "path": "tool_outputs/run_1/cover.png",
                    "display_name": "cover.png",
                    "source_tool": "code_executor",
                }
            ]
        },
    )

    assert decision.intent_type == "execute_task"
    assert decision.capability_floor == "tools"
    assert decision.request_route_mode == "auto_deepthink"


def test_process_unified_stream_reuses_recent_image_without_starting_deep_think() -> None:
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=None)
    agent.extra_context = {
        "recent_image_artifacts": [
            {
                "path": "tool_outputs/run_2/figure.png",
                "display_name": "figure.png",
                "source_tool": "code_executor",
                "mime_family": "image",
                "origin": "artifact",
                "created_at": "2026-03-31T00:00:00Z",
                "tracking_id": "dt_demo",
            }
        ]
    }
    agent.history = []
    agent.session_id = None
    agent.llm_service = object()

    async def _collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in agent.process_unified_stream("把刚才那张图片给我看"):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())

    assert len(chunks) == 1
    payload = json.loads(chunks[0].removeprefix("data: ").strip())
    assert payload["type"] == "final"
    assert payload["payload"]["response"] == "这里是刚才那张图片。"
    assert payload["payload"]["metadata"]["artifact_gallery"][0]["path"] == "tool_outputs/run_2/figure.png"


def test_process_unified_stream_asks_for_clarification_when_multiple_recent_images_match() -> None:
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=None)
    agent.extra_context = {
        "recent_image_artifacts": [
            {
                "path": "tool_outputs/run_2/figure_a.png",
                "display_name": "figure_a.png",
                "source_tool": "code_executor",
            },
            {
                "path": "tool_outputs/run_2/figure_b.png",
                "display_name": "figure_b.png",
                "source_tool": "code_executor",
            },
        ]
    }
    agent.history = []
    agent.session_id = None
    agent.llm_service = object()

    async def _collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in agent.process_unified_stream("展示图片"):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())
    payload = json.loads(chunks[0].removeprefix("data: ").strip())

    assert "我先不重新生成" in payload["payload"]["response"]
    assert "artifact_gallery" not in payload["payload"]["metadata"]


# ---------------------------------------------------------------------------
# Followthrough + action verb compound detection
# ---------------------------------------------------------------------------

def test_followthrough_plus_action_verb_routes_to_execute() -> None:
    """'继续' + action verb '尝试' → execute_task, not chat."""
    intent, reasons = resolve_intent_type(message="继续用这五个进行尝试")
    assert intent == "execute_task"
    assert "intent_execute_task" in reasons


def test_followthrough_with_code_context() -> None:
    intent, reasons = resolve_intent_type(
        message="继续用这五个进行尝试，我又修改了一下代码",
    )
    assert intent == "execute_task"


def test_retry_patterns_route_to_execute() -> None:
    for msg in ["再试一下", "重试", "再提交", "再跑一次", "试试看"]:
        intent, _ = resolve_intent_type(message=msg)
        assert intent == "execute_task", f"Expected execute_task for '{msg}', got {intent}"


def test_chat_continuation_stays_chat() -> None:
    """'继续说' / '继续讲' should remain chat."""
    for msg in ["继续说", "继续讲", "继续解释一下"]:
        intent, _ = resolve_intent_type(message=msg)
        assert intent == "chat", f"Expected chat for '{msg}', got {intent}"


def test_english_followthrough_plus_action_routes_to_execute() -> None:
    intent, _ = resolve_intent_type(message="continue trying with those five")
    assert intent == "execute_task"


def test_english_chat_continuation_stays_chat() -> None:
    intent, _ = resolve_intent_type(message="keep explaining the concept")
    assert intent == "chat"
