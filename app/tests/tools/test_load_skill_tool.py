"""load_skill tool tests: handler paths, registry legality, offer surfaces."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.routers.chat.request_routing import get_all_tools
from app.services.tool_schemas import build_executor_tool_schemas, build_tool_schemas
from tool_box.tool_registry import get_tool_orchestration_metadata, register_all_tools
from tool_box.tools import get_tool_registry
from tool_box.tools_impl import load_skill as load_skill_module
from tool_box.tools_impl.load_skill import (
    MAX_CONTENT_CHARS,
    load_skill_handler,
)


@pytest.fixture()
def skill_loader(tmp_path, monkeypatch):
    """A SkillsLoader rooted at a tmp project dir with two test skills."""
    from app.services.skills.skills_loader import SkillsLoader

    skills_root = tmp_path / "skills"
    alpha = skills_root / "alpha-skill"
    alpha.mkdir(parents=True)
    (alpha / "SKILL.md").write_text(
        "---\nname: alpha-skill\ndescription: Alpha test skill.\n---\n"
        "# Alpha\n\nIntro text.\n\n## Setup\n\nDo the setup thing.\n\n## Usage\n\nUse it well.\n",
        encoding="utf-8",
    )
    big = skills_root / "big-skill"
    big.mkdir(parents=True)
    (big / "SKILL.md").write_text(
        "---\nname: big-skill\ndescription: Big test skill.\n---\n"
        "# Big\n\n" + ("x" * (MAX_CONTENT_CHARS + 5000)) + "\n",
        encoding="utf-8",
    )
    loader = SkillsLoader(
        skills_dir=str(tmp_path / "runtime-skills"),
        project_skills_dir=str(skills_root),
        auto_sync=False,
    )
    monkeypatch.setattr(load_skill_module, "_resolve_loader", lambda: loader)
    return loader


@pytest.mark.asyncio()
async def test_load_skill_returns_full_body(skill_loader):
    result = await load_skill_handler(name="alpha-skill")
    assert result["success"] is True
    assert result["name"] == "alpha-skill"
    assert result["description"] == "Alpha test skill."
    assert "## Setup" in result["content"] and "## Usage" in result["content"]
    assert result["truncated"] is False
    assert result["total_chars"] > 0


@pytest.mark.asyncio()
async def test_load_skill_unknown_name_lists_available(skill_loader):
    result = await load_skill_handler(name="no-such-skill")
    assert result["success"] is False
    assert "skill_not_found" in result["error"]
    assert result["available_skills"] == ["alpha-skill", "big-skill"]
    assert "alpha-skill" in result["summary"]
    assert len(result["available_skills"]) <= 20


@pytest.mark.asyncio()
async def test_load_skill_truncates_oversized_body(skill_loader):
    result = await load_skill_handler(name="big-skill")
    assert result["success"] is True
    assert result["truncated"] is True
    assert result["content_chars"] <= MAX_CONTENT_CHARS + 200
    assert "section" in result["content"]  # truncation notice points at the section param


@pytest.mark.asyncio()
async def test_load_skill_section_extracts_one_heading(skill_loader):
    result = await load_skill_handler(name="alpha-skill", section="setup")
    assert result["success"] is True
    assert "Setup" in result["content"]
    assert "Do the setup thing." in result["content"]
    assert "Use it well." not in result["content"]
    assert result["section"] == "setup"


@pytest.mark.asyncio()
async def test_load_skill_unknown_section_lists_headings(skill_loader):
    result = await load_skill_handler(name="alpha-skill", section="nope")
    assert result["success"] is False
    assert "section_not_found" in result["error"]
    assert "Setup" in result["available_sections"]


@pytest.mark.asyncio()
async def test_load_skill_empty_name_rejected(skill_loader):
    result = await load_skill_handler(name="  ")
    assert result["success"] is False
    assert result["error"] == "missing_name"


# --- registration / offer surfaces -----------------------------------------------


def test_load_skill_registered_with_read_only_metadata():
    register_all_tools()
    tool_def = get_tool_registry().get_tool("load_skill")
    assert tool_def is not None
    assert tool_def.is_read_only is True
    assert tool_def.is_concurrent_safe is True
    assert not tool_def.is_destructive
    metadata = get_tool_orchestration_metadata("load_skill")
    assert metadata.get("is_read_only") is True


def test_load_skill_on_offer_surfaces():
    assert "load_skill" in get_all_tools()
    native_names = [s["function"]["name"] for s in build_tool_schemas(["load_skill"])]
    assert "load_skill" in native_names
    executor_names = [s["function"]["name"] for s in build_executor_tool_schemas()]
    assert "load_skill" in executor_names


@pytest.mark.asyncio()
async def test_load_skill_dispatch_through_registry(skill_loader):
    """Canonical dispatch path: registry lookup + prepare_handler_kwargs."""
    from tool_box import execute_tool

    register_all_tools()
    result = await execute_tool("load_skill", name="alpha-skill")
    assert result["success"] is True
    assert "## Usage" in result["content"]
