"""A search query must survive whatever shape the tool call arrives in.

Production 2026-09-26: a user asked for actionable directions "基于近期热点" and
`web_search` came back `missing_query` three times in one run — the model's own
narration was "query 参数连续丢失，我改用代码内核方式调用搜索". The chat lane read
only `params["query"]`, so the plural form the native schema advertises
(`queries`), an unparsed argument string (`_raw`), or a renamed key all ended in
the same hard dead end, and the final answer had to disclose "本轮外部检索未成功
完成". The last test here locks the failure message so the next occurrence says
what actually arrived.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.routers.chat.action_tool_params import (
    _normalize_web_search_params,
    _web_search_query_from_params,
)
from app.services.llm.structured_response import LLMAction


def _agent() -> SimpleNamespace:
    return SimpleNamespace(extra_context={})


def _action() -> LLMAction:
    return LLMAction(kind="tool_operation", name="web_search")


def test_explicit_query_wins() -> None:
    params = _normalize_web_search_params(
        _agent(), _action(), "web_search", {"query": "cervical cancer screening 2025"}
    )

    assert params["query"] == "cervical cancer screening 2025"


def test_plural_queries_is_accepted() -> None:
    """The native schema invites `queries` for broad comparison questions."""
    params = _normalize_web_search_params(
        _agent(),
        _action(),
        "web_search",
        {"queries": ["HPV vaccine single dose 2025", "self-sampling HPV screening"]},
    )

    assert "HPV vaccine single dose 2025" in params["query"]
    assert "self-sampling HPV screening" in params["query"]


def test_unparsed_argument_string_is_recovered() -> None:
    """`_raw` is what llm.py stores when the argument JSON never parsed."""
    params = _normalize_web_search_params(
        _agent(),
        _action(),
        "web_search",
        {"_raw": '{"query": "recovered from the raw argument string"}'},
    )

    assert params["query"] == "recovered from the raw argument string"


def test_renamed_key_still_finds_a_query() -> None:
    """A slightly-off search beats a dead end, so take the longest string."""
    params = _normalize_web_search_params(
        _agent(),
        _action(),
        "web_search",
        {"search_text": "cervical cancer risk factors review 2025", "provider": "builtin"},
    )

    assert params["query"] == "cervical cancer risk factors review 2025"


def test_missing_query_names_what_arrived() -> None:
    step = _normalize_web_search_params(
        _agent(), _action(), "web_search", {"provider": "builtin"}
    )

    assert step.success is False
    assert step.details["error"] == "missing_query"
    assert "Received parameters: ['provider']" in step.message


def test_empty_params_say_none_arrived() -> None:
    step = _normalize_web_search_params(_agent(), _action(), "web_search", {})

    assert "Received parameters: none" in step.message


@pytest.mark.parametrize(
    "params",
    [
        {"query": "   "},
        {"queries": []},
        {"queries": ["", "   "]},
        {"_raw": "not json at all"},
    ],
)
def test_unusable_shapes_still_report_missing_query(params: dict) -> None:
    assert _web_search_query_from_params(params) == ""
    step = _normalize_web_search_params(_agent(), _action(), "web_search", params)
    assert step.success is False
    assert step.details["error"] == "missing_query"
