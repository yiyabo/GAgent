"""Tests for billing attribution: keys, headers, and ledger columns."""
from __future__ import annotations

import pytest

from app.billing_keys import (
    REGISTERED_BILLING_KEYS,
    billing_key_for_purpose,
    normalize_billing_key,
)
from app.llm import (
    _billing_request_headers,
    _new_logical_call_id,
    set_usage_context,
    clear_usage_context,
    update_usage_context,
)


class TestBillingKeys:
    def test_registry_contains_expected_keys(self) -> None:
        expected = {
            "chat.main",
            "chat.routing",
            "deep_think.iteration",
            "plan.task_execution",
            "plan.decompose",
            "plan.review",
            "plan.optimize",
            "tool.execution",
            "tool.code_executor",
            "tool.web_search",
            "coding_agent.qwen_code_cli",
            "internal.conversation_quality_evaluation",
            "internal.memory_embedding",
            "internal.uncategorized",
        }
        assert expected <= REGISTERED_BILLING_KEYS

    def test_unknown_key_normalizes_to_uncategorized(self) -> None:
        assert normalize_billing_key("no.such_key") == "internal.uncategorized"
        assert normalize_billing_key("") == "internal.uncategorized"
        assert normalize_billing_key(None) == "internal.uncategorized"

    def test_purpose_to_key_mapping(self) -> None:
        assert billing_key_for_purpose("chat_main") == "chat.main"
        assert billing_key_for_purpose("deep_think") == "deep_think.iteration"
        assert billing_key_for_purpose("plan_task_execution") == "plan.task_execution"
        assert billing_key_for_purpose("qwen_code_cli_execution") == "coding_agent.qwen_code_cli"
        assert billing_key_for_purpose(None, "code_executor") == "tool.code_executor"
        assert billing_key_for_purpose(None, "web_search") == "tool.web_search"
        assert billing_key_for_purpose("conversation_quality_evaluation") == (
            "internal.conversation_quality_evaluation"
        )
        assert billing_key_for_purpose("memory_embedding") == "internal.memory_embedding"


class TestBillingHeaders:
    def test_headers_carry_key_and_call_id(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_BILLING_HEADERS_ENABLED", "true")
        token = set_usage_context(session_id="s1", plan_id=7, call_purpose="chat_main")
        try:
            cid = _new_logical_call_id()
            headers = _billing_request_headers(cid, 2)
            assert headers["X-Agent-Tool-Key"] == "chat.main"
            assert headers["X-Agent-Call-ID"] == cid
            assert headers["X-Agent-Attempt"] == "2"
            assert headers["X-Agent-Session"] == "s1"
            assert headers["X-Agent-Plan"] == "7"
        finally:
            clear_usage_context(token)

    def test_headers_disabled_via_env(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_BILLING_HEADERS_ENABLED", "false")
        token = set_usage_context(call_purpose="chat_main")
        try:
            assert _billing_request_headers(_new_logical_call_id(), 1) == {}
        finally:
            clear_usage_context(token)

    def test_headers_without_context_fall_back_to_uncategorized(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_BILLING_HEADERS_ENABLED", "true")
        token = set_usage_context(call_purpose=None)
        try:
            headers = _billing_request_headers(_new_logical_call_id(), 1)
            assert headers["X-Agent-Tool-Key"] == "internal.uncategorized"
        finally:
            clear_usage_context(token)

    def test_header_names_env_configurable(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_BILLING_HEADERS_ENABLED", "true")
        monkeypatch.setenv("LLM_BILLING_KEY_HEADER", "X-Biz-Key")
        token = set_usage_context(call_purpose="chat_main")
        try:
            headers = _billing_request_headers(_new_logical_call_id(), 1)
            assert headers["X-Biz-Key"] == "chat.main"
            assert "X-Agent-Tool-Key" not in headers
        finally:
            clear_usage_context(token)


class TestUsageContextBilling:
    def test_set_usage_context_resolves_billing_key(self) -> None:
        token = set_usage_context(call_purpose="plan_task_execution")
        try:
            import app.llm as llm_mod

            ctx = llm_mod._usage_context.get()
            assert ctx["billing_key"] == "plan.task_execution"
        finally:
            clear_usage_context(token)

    def test_update_usage_context_reroutes_key_on_tool_change(self) -> None:
        token = set_usage_context(session_id="s1", call_purpose="chat_main")
        try:
            update_usage_context(tool_name="web_search", phase="tool")
            import app.llm as llm_mod

            ctx = llm_mod._usage_context.get()
            assert ctx["billing_key"] == "tool.web_search"
            assert ctx["tool_name"] == "web_search"
        finally:
            clear_usage_context(token)

    def test_explicit_billing_key_wins(self) -> None:
        token = set_usage_context(call_purpose="chat_main", billing_key="tool.vision_reader")
        try:
            import app.llm as llm_mod

            ctx = llm_mod._usage_context.get()
            # unregistered keys normalize to the explicit unclassified bucket
            assert ctx["billing_key"] == "internal.uncategorized"
        finally:
            clear_usage_context(token)
