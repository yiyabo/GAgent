from __future__ import annotations

import json
from pathlib import Path

from app.services.skills import SkillsLoader, validate_skills
from app.services import tool_schemas


PROJECT_ROOT = Path(__file__).resolve().parents[3]
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
    assert "sequence_text" in description


def test_bio_tools_schema_exposes_sequence_text_parameter() -> None:
    schema = tool_schemas.TOOL_REGISTRY["bio_tools"]
    props = schema["function"]["parameters"]["properties"]
    assert "sequence_text" in props


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


def test_skills_loader_exposes_manifest_fields() -> None:
    loader = SkillsLoader(
        skills_dir=str(SKILLS_ROOT),
        project_skills_dir=str(SKILLS_ROOT),
        auto_sync=False,
    )
    skills = {item["name"]: item for item in loader.list_skills()}

    router = skills["bio-tools-router"]
    assert router["has_config"] is True
    assert router["category"] == "router"
    assert router["scope"] == "both"
    assert router["injection"]["mode"] == "summary_with_references"
    assert "references/verified_ops.md" in router["references"]


def test_validate_skills_reports_invalid_manifest_without_blocking_valid_skills(tmp_path: Path) -> None:
    valid_dir = tmp_path / "valid-skill"
    valid_dir.mkdir()
    (valid_dir / "SKILL.md").write_text(
        "---\nname: valid-skill\ndescription: Valid skill.\n---\n\n# Valid Skill\n",
        encoding="utf-8",
    )

    invalid_dir = tmp_path / "invalid-skill"
    invalid_dir.mkdir()
    (invalid_dir / "SKILL.md").write_text(
        "---\nname: invalid-skill\ndescription: Invalid skill.\n---\n\n# Invalid Skill\n",
        encoding="utf-8",
    )
    (invalid_dir / "config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "category": "router",
                "scope": "task",
                "priority": 1,
                "selection": {"keywords": []},
                "injection": {"mode": "summary", "max_chars": 500},
                "references": ["../escape.md"],
                "scripts": [],
            }
        ),
        encoding="utf-8",
    )

    loader = SkillsLoader(
        skills_dir=str(tmp_path),
        project_skills_dir=str(tmp_path),
        auto_sync=False,
    )
    listed_names = {item["name"] for item in loader.list_skills()}
    assert listed_names == {"valid-skill"}

    validation = validate_skills(
        skills_dir=str(tmp_path),
        project_skills_dir=str(tmp_path),
        auto_sync=False,
    )
    assert validation["valid_skills"] == ["valid-skill"]
    assert "invalid-skill" in validation["invalid_skills"]
