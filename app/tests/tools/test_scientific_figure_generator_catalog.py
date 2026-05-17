from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from app.prompts.en_US import PROMPTS_EN_US


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, dict)
    return cast(Mapping[str, object], value)


def _string_list(value: object) -> list[str]:
    assert isinstance(value, list)
    items = cast(list[object], value)
    assert all(isinstance(item, str) for item in items)
    return cast(list[str], items)


def test_scientific_figure_generator_visible_in_structured_action_catalog() -> None:
    prompt_root = _mapping(PROMPTS_EN_US)
    structured_agent_prompt = _mapping(prompt_root["structured_agent"])
    action_catalog = _mapping(structured_agent_prompt["action_catalog"])
    guidelines = _mapping(structured_agent_prompt["guidelines"])

    catalog_text = "\n".join(_string_list(action_catalog["base_actions"]))
    common_rules_text = "\n".join(_string_list(guidelines["common_rules"]))

    assert "tool_operation: scientific_figure_generator" in catalog_text
    assert "PREFERRED for scientific composite figures" in catalog_text
    assert "use `scientific_figure_generator` first" in common_rules_text
    assert "code_executor" in common_rules_text
