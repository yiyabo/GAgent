"""Phase 1: phage-intent first-iteration schema disclosure.

A phage task used to pay one or two load_tool_schema round-trips before any
real work: iteration 1 sent the full payload, iteration 2+ trimmed to core
tools only, and phagescope_data/bio_tools were behind the meta loader. When
the request clearly concerns phages and phagescope_data is deployed, both
tools must be disclosed from iteration 1.
"""

from __future__ import annotations

import pytest

from app.services.deep_think import schema_disclosure as sd


def _schemas(names: list[str]) -> list[dict]:
    return [
        {"type": "function", "function": {"name": name, "description": f"{name} desc", "parameters": {"type": "object", "properties": {}}}}
        for name in names
    ]


_TOOLS = ["execute_code", "file_operations", "web_search", "deliverable_submit", "phagescope_data", "bio_tools", "load_skill"]


@pytest.fixture(autouse=True)
def _v2_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.deep_think.runtime_policy.configured_policy",
        lambda: {"version": 1, "arguments": False, "schemas": 2, "receipts": False},
    )
    monkeypatch.setattr(sd, "skills_index_active", lambda: False)


def _names(payload: list[dict]) -> set[str]:
    return {schema["function"]["name"] for schema in payload}


class TestPhageIntent:
    def test_phage_hint_discloses_data_and_bio_tools_at_iteration_1(self) -> None:
        disclosure = sd.SchemaDisclosure(_schemas(_TOOLS), _TOOLS, task_hint="用 PhageScope 筛选 Pseudomonas 噬菌体并跑 seqkit")
        payload = disclosure.effective(iteration=1, tools_used=[])
        names = _names(payload)
        assert "phagescope_data" in names
        assert "bio_tools" in names
        assert "execute_code" in names
        assert sd.META_TOOL_NAME in names

    def test_chinese_phage_keyword_also_triggers(self) -> None:
        disclosure = sd.SchemaDisclosure(_schemas(_TOOLS), _TOOLS, task_hint="帮我挖掘裂解酶候选")
        names = _names(disclosure.effective(iteration=1, tools_used=[]))
        assert "phagescope_data" in names

    def test_non_phage_hint_does_not_disclose(self) -> None:
        disclosure = sd.SchemaDisclosure(_schemas(_TOOLS), _TOOLS, task_hint="总结这篇肺癌免疫治疗论文")
        names = _names(disclosure.effective(iteration=1, tools_used=[]))
        assert "phagescope_data" not in names
        assert "bio_tools" not in names

    def test_intent_requires_tool_to_be_available(self) -> None:
        available = [name for name in _TOOLS if name != "phagescope_data"]
        disclosure = sd.SchemaDisclosure(_schemas(available), available, task_hint="分析噬菌体")
        assert disclosure._phage_intent is False

    def test_empty_hint_does_not_disclose(self) -> None:
        disclosure = sd.SchemaDisclosure(_schemas(_TOOLS), _TOOLS, task_hint="")
        assert disclosure._phage_intent is False

    def test_meta_description_mentions_current_tool_names(self) -> None:
        assert "phagescope_data" in sd._META_DESCRIPTION
        assert "bio_tools" in sd._META_DESCRIPTION
