"""Skill progressive disclosure, level 1: the skill index in the chat system prompt.

The model sees one line per enabled skill and opens what it needs with
``load_skill`` (levels 2 and 3 are covered in ``tools/test_load_skill_tool.py``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.deep_think import skills_index
from app.services.deep_think.schema_disclosure import SchemaDisclosure
from app.services.deep_think.skills_index import (
    MAX_DESCRIPTION_CHARS,
    MAX_INDEX_CHARS,
    build_skills_index_block,
    skills_index_active,
)
from app.services.deep_think_agent import DeepThinkAgent, TaskExecutionContext
from app.services.skills import SkillsLoader


def _row(name: str, description: str = "", category: str = "analysis") -> dict:
    return {"name": name, "description": description, "category": category}


@pytest.fixture()
def two_skills(monkeypatch):
    rows = [
        _row("zeta-writer", "Writes   reports\nwith\tfigures.", "writer"),
        _row("alpha-router", "Routes tools.", "router"),
        _row("beta-router", "Routes more tools.", "router"),
    ]
    monkeypatch.setattr(skills_index, "_enabled_skills", lambda: rows)
    return rows


def test_index_groups_by_category_and_sorts(two_skills):
    block = build_skills_index_block()

    assert block.startswith("=== SKILLS ===")
    assert "<available_skills>" in block and "</available_skills>" in block
    body = block.split("<available_skills>\n", 1)[1].split("</available_skills>", 1)[0]
    assert body == (
        "  router:\n"
        "    - alpha-router: Routes tools.\n"
        "    - beta-router: Routes more tools.\n"
        "  writer:\n"
        "    - zeta-writer: Writes reports with figures.\n"
    )


def test_index_tells_the_model_how_to_open_a_skill(two_skills):
    block = build_skills_index_block()

    assert "load_skill(name)" in block
    assert "file_path='references/x.md'" in block
    assert "linked_files" in block
    assert "MUST load it" in block
    assert "does not make that tool available" in block


def test_long_descriptions_are_cut_to_one_short_line():
    block = build_skills_index_block([_row("verbose", "word " * 200)])

    line = next(item for item in block.splitlines() if item.strip().startswith("- verbose:"))
    description = line.split(": ", 1)[1]
    assert len(description) <= MAX_DESCRIPTION_CHARS
    assert description.endswith("…")


def test_nothing_to_offer_means_no_block():
    assert build_skills_index_block([]) == ""
    assert build_skills_index_block([_row("")]) == ""


def test_duplicate_names_are_listed_once():
    block = build_skills_index_block([_row("same", "first"), _row("same", "second")])

    assert block.count("- same:") == 1 and "first" in block and "second" not in block


def test_oversized_index_drops_descriptions_but_never_entries():
    rows = [_row(f"skill-{index:03d}", "d" * MAX_DESCRIPTION_CHARS) for index in range(60)]

    block = build_skills_index_block(rows)

    body = block.split("<available_skills>\n", 1)[1].split("</available_skills>", 1)[0]
    assert len(body) <= MAX_INDEX_CHARS
    assert all(f"- skill-{index:03d}\n" in body for index in range(60))


def test_switch_turns_the_index_off(two_skills, monkeypatch):
    monkeypatch.setenv("CHAT_SKILLS_INDEX_ENABLED", "0")

    assert build_skills_index_block() == ""
    assert skills_index_active() is False


def test_loader_failure_never_breaks_the_prompt(monkeypatch):
    def boom():
        raise RuntimeError("skills directory unreadable")

    monkeypatch.setattr(skills_index, "_enabled_skills", boom)

    assert build_skills_index_block() == ""
    assert skills_index_active() is False


def test_active_follows_what_is_enabled(monkeypatch):
    monkeypatch.setattr(skills_index, "_enabled_skills", lambda: [])
    assert skills_index_active() is False
    monkeypatch.setattr(skills_index, "_enabled_skills", lambda: [_row("x")])
    assert skills_index_active() is True


def test_admin_disabled_skill_is_not_offered(tmp_path, monkeypatch):
    """The index reads the loader's enabled set, so an admin switch removes the line."""
    root = tmp_path / "skills"
    for name in ("on-skill", "off-skill"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} desc\n---\n# {name}\n", encoding="utf-8")
    monkeypatch.setattr("app.services.skills.skills_loader._admin_disabled_skills", lambda: {"off-skill"})
    loader = SkillsLoader(skills_dir=str(tmp_path / "rt"), project_skills_dir=str(root), auto_sync=False)
    monkeypatch.setattr(skills_index, "_enabled_skills", loader.list_skills)

    block = build_skills_index_block()

    assert "on-skill" in block and "off-skill" not in block


# ----------------------------------------------------------------- prompt placement


def _agent(tools):
    return DeepThinkAgent(llm_client=MagicMock(), available_tools=list(tools), tool_executor=AsyncMock())


def test_chat_prompt_carries_the_index_when_load_skill_is_offered(two_skills):
    prompt = _agent(["web_search", "load_skill"])._build_native_system_prompt(context={})

    assert "=== SKILLS ===" in prompt
    assert "- alpha-router: Routes tools." in prompt


def test_chat_prompt_has_no_index_without_load_skill(two_skills):
    prompt = _agent(["web_search"])._build_native_system_prompt(context={})

    assert "=== SKILLS ===" not in prompt


def test_plan_task_prompt_keeps_its_own_skill_guidance_and_no_index(two_skills):
    ctx = TaskExecutionContext(task_id=1, task_instruction="Analyze", skill_context="[Skill: x]\nDo x.")

    prompt = _agent(["web_search", "load_skill"])._build_native_system_prompt(context={}, task_context=ctx)

    assert "=== SKILL GUIDANCE ===" in prompt
    assert "=== SKILLS ===" not in prompt


def test_index_sits_before_the_volatile_context(two_skills):
    """Prompt-cache friendliness: the index is part of the stable head, not the per-turn tail."""
    prompt = _agent(["web_search", "load_skill"])._build_native_system_prompt(context={})

    assert prompt.index("=== SKILLS ===") < prompt.index("=== WORKFLOW ===")


# ------------------------------------------------------------ tool stays offered


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}}


_NAMES = ["execute_code", "file_operations", "web_search", "deliverable_submit", "load_skill", "vision_reader"]


def _offered(monkeypatch, *, active: bool, iteration: int = 2):
    monkeypatch.setattr("app.services.deep_think.schema_disclosure.skills_index_active", lambda: active)
    disclosure = SchemaDisclosure([_schema(n) for n in _NAMES], _NAMES)
    return [s["function"]["name"] for s in disclosure.effective(iteration=iteration, tools_used=[])]


def test_load_skill_stays_in_the_trimmed_payload_while_an_index_is_shown(monkeypatch):
    assert "load_skill" in _offered(monkeypatch, active=True)


def test_load_skill_waits_behind_load_tool_schema_without_an_index(monkeypatch):
    offered = _offered(monkeypatch, active=False)

    assert "load_skill" not in offered and "vision_reader" not in offered


def test_first_iteration_still_sends_everything(monkeypatch):
    assert "vision_reader" in _offered(monkeypatch, active=True, iteration=1)


# ----------------------------------------------- YAML block scalars in frontmatter


@pytest.fixture()
def frontmatter_loader(tmp_path):
    return SkillsLoader(skills_dir=str(tmp_path / "rt"), project_skills_dir=str(tmp_path / "p"), auto_sync=False)


def test_folded_block_scalar_description(frontmatter_loader):
    text = "---\nname: paper-writer\ndescription: >-\n  Drafts paper prose from\n  the author's materials.\n  Cites sources.\nlicense: MIT\n---\n# Body\n"

    meta = frontmatter_loader._extract_frontmatter(text)

    assert meta["description"] == "Drafts paper prose from the author's materials. Cites sources."
    assert meta["license"] == "MIT" and meta["name"] == "paper-writer"
    assert frontmatter_loader._extract_description(text, meta).startswith("Drafts paper prose")


def test_literal_block_scalar_keeps_line_breaks(frontmatter_loader):
    text = "---\nname: x\ndescription: |\n  line one\n  line two\n---\n"

    assert frontmatter_loader._extract_frontmatter(text)["description"] == "line one\nline two"


def test_plain_and_quoted_values_are_unchanged(frontmatter_loader):
    text = '---\nname: a\ndescription: "Quoted: with colon"\ncategory: > 5 chars is not a block\n---\n'

    meta = frontmatter_loader._extract_frontmatter(text)

    assert meta["description"] == "Quoted: with colon"
    assert meta["category"] == "> 5 chars is not a block"
