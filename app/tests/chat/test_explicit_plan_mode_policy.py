"""Explicit Plan mode: read-only research plus Plan-tree edits, never execution."""

from __future__ import annotations

import pytest

from app.routers.chat.models import AgentStep
from app.routers.chat.request_routing import resolve_request_routing
from app.services.deep_think.plan_mode import (
    defer_plan_mode_execution,
    plan_mode_tool_allowed,
    plan_mode_tool_names,
)
from app.services.llm.structured_response import LLMAction


@pytest.mark.parametrize(
    ("tool", "params", "allowed"),
    [
        ("web_search", {"query": "phage"}, True),
        ("load_skill", {"name": "deep-research"}, True),
        ("file_operations", {"operation": "read", "path": "notes.md"}, True),
        ("file_operations", {"operation": "list", "path": "."}, True),
        ("file_operations", {"operation": "write", "path": "notes.md"}, False),
        ("file_operations", {"operation": "copy", "path": "a"}, False),
        ("plan_operation", {"operation": "create"}, True),
        ("plan_operation", {"operation": "optimize", "plan_id": 1}, True),
        ("plan_operation", {"operation": "update_task", "plan_id": 1}, True),
        ("plan_operation", {"operation": "get", "plan_id": 1}, True),
        ("plan_operation", {"operation": "execute_all", "plan_id": 1}, False),
        ("plan_operation", {"operation": "execute_task", "plan_id": 1}, False),
        ("plan_operation", {"operation": "recompute", "plan_id": 1}, False),
        ("phagescope_data", {"operation": "search_phages"}, True),
        ("phagescope_data", {"operation": "materialize"}, False),
        ("phagescope_data", {"operation": "search_phages", "save_tsv": True}, False),
        (
            "phagescope_data",
            {"operation": "structures", "materialize_pdb": True},
            False,
        ),
        ("phagescope", {"action": "query"}, True),
        ("phagescope", {"action": "task_detail"}, True),
        ("phagescope", {"action": "submit"}, False),
        ("phagescope", {"action": "download"}, False),
        ("phagescope_research", {"action": "audit"}, True),
        ("phagescope_research", {"action": "prepare_metadata_table"}, False),
        ("execute_code", {"code": "print(1)"}, False),
        ("code_executor", {"code": "print(1)"}, False),
        ("bio_tools", {"operation": "seqkit stats"}, False),
        ("delegate_task", {"goal": "run everything"}, False),
        ("terminal_session", {"operation": "input", "data": "ls"}, False),
        ("deliverable_submit", {"paths": ["report.md"]}, False),
        ("unknown_future_tool", {}, False),
    ],
)
def test_plan_mode_tool_policy(tool: str, params: dict, allowed: bool) -> None:
    assert plan_mode_tool_allowed(tool, params) is allowed


def test_plan_mode_offers_only_tools_with_a_read_or_planning_path() -> None:
    names = [
        "web_search",
        "file_operations",
        "plan_operation",
        "phagescope_data",
        "phagescope",
        "phagescope_research",
        "load_skill",
        "load_tool_schema",
        "execute_code",
        "code_executor",
        "bio_tools",
        "delegate_task",
        "deliverable_submit",
        "unknown_future_tool",
    ]
    assert plan_mode_tool_names(names) == [
        "web_search",
        "file_operations",
        "plan_operation",
        "phagescope_data",
        "phagescope",
        "phagescope_research",
        "load_skill",
        "load_tool_schema",
    ]


def test_plan_mode_defers_execution_even_when_language_requests_it() -> None:
    decision = defer_plan_mode_execution(
        resolve_request_routing(
            message="立即执行全部任务",
            context={"plan_mode": True},
            plan_id=34,
        )
    )
    assert decision.plan_contract_enforced is True
    assert decision.plan_execute_required is False
    assert decision.plan_execute_after_create_required is False
    assert decision.full_plan_execution is False
    assert decision.intent_type == "chat"
    assert "plan_mode_execution_deferred" in decision.route_reason_codes


def test_explicit_plan_mode_still_hardens_plan_creation() -> None:
    decision = resolve_request_routing(
        message="把能纳入的都纳入，然后制定完整的研究计划吧",
        context={"plan_mode": True},
    )
    assert decision.plan_create_required is True
    assert decision.plan_contract_enforced is True


async def test_structured_action_guard_blocks_before_handler(monkeypatch) -> None:
    from app.tests.chat.test_unified_stream_event_golden import _build_agent

    async def fail_if_called(*args, **kwargs):
        pytest.fail("mutating handler ran in Plan mode")

    monkeypatch.setattr("app.routers.chat.agent._handle_tool_action_fn", fail_if_called)
    agent = _build_agent(extra_context={"plan_contract_enforced": True})
    action = LLMAction(
        kind="tool_operation",
        name="file_operations",
        parameters={"operation": "write", "path": "x.md", "content": "x"},
    )
    step = await agent._handle_tool_action(action)
    assert isinstance(step, AgentStep)
    assert step.success is False
    assert step.details["error"] == "plan_mode_read_only"
    assert step.details["operation"] == "write"
