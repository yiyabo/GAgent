from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.routers.chat.agent import StructuredChatAgent
from app.routers.chat.request_routing import (
    build_request_tier_profile,
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
