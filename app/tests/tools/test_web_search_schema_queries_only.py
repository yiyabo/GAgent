"""web_search: a `queries`-only call is valid under argument validation (LOCAL_INFRA §111).

Replay of 571 production checkpoints (2026-10-09): 18 of 21 web_search calls sent
only `queries` — the batched form the schema description asks for and the handler
accepts — yet both schemas still listed `query` as required, so enabling
AGENT_ARGUMENT_VALIDATION_ENABLED would have rejected 86% of them.
"""

from __future__ import annotations

import pytest

from app.services.deep_think.native_validation import validate
from tool_box.native_tool_schemas import NATIVE_TOOL_CONTENT
from tool_box.tools_impl.web_search import web_search_handler, web_search_tool


def _schemas() -> dict:
    return {
        "native": NATIVE_TOOL_CONTENT["web_search"]["parameters"],
        "registry": web_search_tool["parameters_schema"],
    }


@pytest.mark.parametrize("source", ["native", "registry"])
def test_queries_only_call_passes_argument_validation(source: str) -> None:
    schema = _schemas()[source]
    assert validate({"queries": ["phage therapy review", "phage resistance"]}, schema) is None


@pytest.mark.parametrize("source", ["native", "registry"])
def test_single_query_call_still_passes(source: str) -> None:
    assert validate({"query": "phage therapy"}, _schemas()[source]) is None


@pytest.mark.parametrize("source", ["native", "registry"])
def test_schema_no_longer_requires_query(source: str) -> None:
    assert _schemas()[source]["required"] == []


@pytest.mark.asyncio()
async def test_handler_still_rejects_an_empty_call() -> None:
    # Schema-level `required` is empty on purpose: the handler owns the
    # "at least one of query/queries" rule and answers with an actionable payload.
    result = await web_search_handler()
    assert result["success"] is False
    assert result["error"] == "missing_query"
