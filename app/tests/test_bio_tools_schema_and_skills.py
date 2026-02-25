from __future__ import annotations

import json
from pathlib import Path

from app.services.skills import SkillsLoader
from app.services import tool_schemas


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_CONFIG_PATH = PROJECT_ROOT / "tool_box" / "bio_tools" / "tools_config.json"
SKILLS_ROOT = PROJECT_ROOT / "skills"


def test_bio_tools_schema_enum_matches_tools_config() -> None:
    config = json.loads(TOOLS_CONFIG_PATH.read_text(encoding="utf-8"))
    expected_tools = sorted(config.keys())

    schema = tool_schemas.TOOL_REGISTRY["bio_tools"]
    enum_tools = schema["function"]["parameters"]["properties"]["tool_name"]["enum"]

    assert sorted(enum_tools) == expected_tools


def test_bio_tools_schema_description_mentions_dynamic_sync() -> None:
    schema = tool_schemas.TOOL_REGISTRY["bio_tools"]
    description = schema["function"]["description"]

    assert "tools_config.json" in description
    assert "Use operation='help'" in description


def test_skills_loader_discovers_bio_tools_skills() -> None:
    loader = SkillsLoader(
        skills_dir=str(SKILLS_ROOT),
        project_skills_dir=str(SKILLS_ROOT),
        auto_sync=False,
    )
    names = {item.get("name") for item in loader.list_skills()}

    required = {
        "bio-tools-router",
        "bio-tools-execution-playbook",
        "bio-tools-troubleshooting",
    }
    assert required.issubset(names)
