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


# ---------------------------------------------------------------------------
# Progressive disclosure, levels 2 and 3: linked_files, then one linked file
# ---------------------------------------------------------------------------


@pytest.fixture()
def linked_loader(tmp_path, monkeypatch):
    """A skill with references, a script, a loose helper, and files that must stay hidden."""
    from app.services.skills.skills_loader import SkillsLoader

    root = tmp_path / "skills"
    skill = root / "router-skill"
    (skill / "references").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "assets").mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: router-skill\ndescription: Routes things.\n---\n# Router\n\nSee references/guide.md.\n",
        encoding="utf-8",
    )
    (skill / "config.json").write_text('{"category": "router"}', encoding="utf-8")
    (skill / "LICENSE.txt").write_text("MIT", encoding="utf-8")
    (skill / "helper.py").write_text("print('helper')\n", encoding="utf-8")
    (skill / ".secret.md").write_text("hidden", encoding="utf-8")
    (skill / "references" / "guide.md").write_text(
        "# Guide\n\nIntro.\n\n## Taxonomy\n\nICTV notes.\n\n## Markers\n\nGene markers.\n", encoding="utf-8"
    )
    (skill / "references" / "big.md").write_text("y" * (MAX_CONTENT_CHARS + 500), encoding="utf-8")
    (skill / "scripts" / "run.sh").write_text("echo run\n", encoding="utf-8")
    (skill / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n")

    plain = root / "plain-skill"
    plain.mkdir()
    (plain / "SKILL.md").write_text("---\nname: plain-skill\ndescription: No extras.\n---\n# Plain\n", encoding="utf-8")

    outside = tmp_path / "outside.md"
    outside.write_text("TOP SECRET", encoding="utf-8")
    (skill / "references" / "leak.md").symlink_to(outside)

    loader = SkillsLoader(skills_dir=str(tmp_path / "rt"), project_skills_dir=str(root), auto_sync=False)
    monkeypatch.setattr(load_skill_module, "_resolve_loader", lambda: loader)
    return skill


@pytest.mark.asyncio()
async def test_first_load_lists_the_files_it_can_open(linked_loader):
    result = await load_skill_handler(name="router-skill")

    assert result["success"] is True
    assert result["linked_files"] == {
        "references": ["references/big.md", "references/guide.md"],
        "scripts": ["scripts/run.sh"],
        "files": ["helper.py"],
    }
    assert "file_path=" in result["usage_hint"]


@pytest.mark.asyncio()
async def test_listing_hides_config_license_hidden_binary_and_escaping_links(linked_loader):
    listed = (await load_skill_handler(name="router-skill"))["linked_files"]
    flat = [item for group in listed.values() for item in group]

    for hidden in ("config.json", "LICENSE.txt", ".secret.md", "assets/logo.png", "references/leak.md"):
        assert hidden not in flat


@pytest.mark.asyncio()
async def test_skill_without_extras_has_no_linked_files_key(linked_loader):
    result = await load_skill_handler(name="plain-skill")

    assert result["success"] is True
    assert "linked_files" not in result and "usage_hint" not in result


@pytest.mark.asyncio()
async def test_file_path_returns_one_linked_file(linked_loader):
    result = await load_skill_handler(name="router-skill", file_path="references/guide.md")

    assert result["success"] is True
    assert result["name"] == "router-skill"
    assert result["file_path"] == "references/guide.md"
    assert "ICTV notes." in result["content"]
    assert "linked_files" not in result


@pytest.mark.asyncio()
@pytest.mark.parametrize("spelling", ["./references/guide.md", "references\\guide.md"])
async def test_file_path_spellings_are_normalised(linked_loader, spelling):
    result = await load_skill_handler(name="router-skill", file_path=spelling)

    assert result["success"] is True and result["file_path"] == "references/guide.md"


@pytest.mark.asyncio()
async def test_scripts_and_loose_files_can_be_read(linked_loader):
    script = await load_skill_handler(name="router-skill", file_path="scripts/run.sh")
    loose = await load_skill_handler(name="router-skill", file_path="helper.py")

    assert script["success"] is True and "echo run" in script["content"]
    assert loose["success"] is True and "print('helper')" in loose["content"]


@pytest.mark.asyncio()
async def test_section_applies_inside_a_linked_markdown_file(linked_loader):
    result = await load_skill_handler(name="router-skill", file_path="references/guide.md", section="markers")

    assert result["success"] is True
    assert "Gene markers." in result["content"] and "ICTV notes." not in result["content"]
    assert result["section"] == "markers"


@pytest.mark.asyncio()
async def test_unknown_section_in_a_linked_file_lists_headings(linked_loader):
    result = await load_skill_handler(name="router-skill", file_path="references/guide.md", section="nope")

    assert result["success"] is False
    assert result["available_sections"] == ["Guide", "Taxonomy", "Markers"]


@pytest.mark.asyncio()
async def test_missing_linked_file_points_back_to_the_list(linked_loader):
    result = await load_skill_handler(name="router-skill", file_path="references/missing.md")

    assert result["success"] is False
    assert result["error"].startswith("file_not_found")
    assert "references/guide.md" in result["linked_files"]["references"]


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "bad_path",
    [
        "../plain-skill/SKILL.md",
        "references/../../plain-skill/SKILL.md",
        "/etc/passwd",
        "C:/Windows/win.ini",
        ".secret.md",
        "references/.hidden.md",
    ],
)
async def test_paths_outside_the_skill_are_refused(linked_loader, bad_path):
    result = await load_skill_handler(name="router-skill", file_path=bad_path)

    assert result["success"] is False
    assert result["error"].startswith(("path_not_allowed", "file_not_found"))
    assert "content" not in result


@pytest.mark.asyncio()
async def test_a_symlink_out_of_the_skill_is_refused(linked_loader):
    result = await load_skill_handler(name="router-skill", file_path="references/leak.md")

    assert result["success"] is False
    assert result["error"].startswith("path_not_allowed")
    assert "TOP SECRET" not in str(result)


@pytest.mark.asyncio()
@pytest.mark.parametrize("target", ["assets/logo.png", "config.json", "LICENSE.txt"])
async def test_binary_config_and_license_files_are_not_readable(linked_loader, target):
    result = await load_skill_handler(name="router-skill", file_path=target)

    assert result["success"] is False
    assert result["error"].startswith("file_type_not_allowed")


@pytest.mark.asyncio()
async def test_skill_md_as_file_path_is_the_main_load(linked_loader):
    result = await load_skill_handler(name="router-skill", file_path="SKILL.md")

    assert result["success"] is True and "See references/guide.md." in result["content"]
    assert "linked_files" in result


@pytest.mark.asyncio()
async def test_big_linked_file_is_truncated_with_a_hint(linked_loader):
    result = await load_skill_handler(name="router-skill", file_path="references/big.md")

    assert result["success"] is True and result["truncated"] is True
    assert "re-call load_skill with 'section'" in result["content"]


@pytest.mark.asyncio()
async def test_file_over_the_size_cap_is_refused(linked_loader, monkeypatch):
    monkeypatch.setattr(load_skill_module, "_MAX_FILE_BYTES", 10)

    result = await load_skill_handler(name="router-skill", file_path="references/guide.md")

    assert result["success"] is False and result["error"].startswith("file_too_large")


@pytest.mark.asyncio()
async def test_file_path_on_an_unknown_skill_is_still_skill_not_found(linked_loader):
    result = await load_skill_handler(name="ghost", file_path="references/guide.md")

    assert result["success"] is False and result["error"].startswith("skill_not_found")


def test_native_schema_exposes_file_path():
    schema = next(s for s in build_tool_schemas(["load_skill"]) if s["function"]["name"] == "load_skill")

    assert "file_path" in schema["function"]["parameters"]["properties"]
    assert schema["function"]["parameters"]["required"] == ["name"]


def test_chat_result_keeps_linked_files_and_file_path():
    """What the model reads back is the sanitized result; it must keep the new fields."""
    from app.routers.chat.tool_results import sanitize_tool_result, summarize_tool_result

    first = sanitize_tool_result(
        "load_skill",
        {
            "success": True,
            "name": "router-skill",
            "content": "# Router",
            "content_chars": 8,
            "linked_files": {"references": ["references/guide.md"], "scripts": []},
            "usage_hint": "call load_skill(name, file_path=...)",
        },
    )
    assert first["linked_files"] == {"references": ["references/guide.md"]}
    assert first["usage_hint"].startswith("call load_skill")

    second = sanitize_tool_result(
        "load_skill",
        {"success": True, "name": "router-skill", "file_path": "references/guide.md", "content": "x", "content_chars": 1},
    )
    assert second["file_path"] == "references/guide.md"
    assert summarize_tool_result("load_skill", second) == "load_skill loaded 'router-skill' file references/guide.md (1 chars)."
