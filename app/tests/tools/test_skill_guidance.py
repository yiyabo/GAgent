"""_get_skill_guidance: deterministic, budgeted skill injection for delegation prompts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.services.skills import SkillsLoader
from app.services.skills import skills_loader as skills_loader_module
from tool_box.tools_impl.code_executor import _build_claude_code_prompt, _get_skill_guidance

_SANDBOX = Path("runtime/test_skill_guidance_sandbox")


@pytest.fixture()
def sandbox_loader(monkeypatch):
    project_root = (_SANDBOX / "project_skills").resolve()
    runtime_root = (_SANDBOX / "runtime_skills").resolve()
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    skill_dir = project_root / "demo-viz"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-viz\ndescription: demo chart drawing rules\n---\n\n"
        "# Demo Viz\n\n## When to use\n\n- Use the DEMO palette for every chart.\n",
        encoding="utf-8",
    )
    (skill_dir / "config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "category": "generic",
                "scope": "both",
                "priority": 50,
                "selection": {"keywords": ["柱状图", "bar chart"]},
                "injection": {"mode": "full", "max_chars": 1500},
            }
        ),
        encoding="utf-8",
    )
    runtime_root.mkdir(parents=True, exist_ok=True)
    loader = SkillsLoader(
        skills_dir=str(runtime_root),
        project_skills_dir=str(project_root),
        auto_sync=False,
    )
    monkeypatch.setattr(skills_loader_module, "_global_skills_loader", loader)
    yield loader
    monkeypatch.setattr(skills_loader_module, "_global_skills_loader", None)
    shutil.rmtree(_SANDBOX, ignore_errors=True)


def test_guidance_injected_for_matching_task(sandbox_loader) -> None:
    guidance = _get_skill_guidance("画一张柱状图对比三组数据")
    assert "[Skill: demo-viz]" in guidance
    assert "DEMO palette" in guidance


def test_guidance_empty_for_non_matching_task(sandbox_loader) -> None:
    assert _get_skill_guidance("读取 CSV 并输出统计摘要") == ""


def test_delegation_prompt_carries_guidance(sandbox_loader) -> None:
    prompt = _build_claude_code_prompt(
        task="画一张柱状图对比三组数据（A组=12，B组=19，C组=7）",
        task_work_dir=_SANDBOX / "run_1",
        file_prefix="run_1_",
        task_subdirs=["code", "results", "data"],
        execution_spec=None,
        resolved_resources=None,
        allowed_dirs_info="",
    )
    assert "Skill guidance (apply when relevant to the task):" in prompt
    assert "[Skill: demo-viz]" in prompt


def test_guidance_failure_is_silent(monkeypatch) -> None:
    class _Broken:
        def _eligible_skills(self, scope):
            raise RuntimeError("boom")

    # _get_skill_guidance imports get_skills_loader from the package namespace
    # (app.services.skills), so patch it there, not on the submodule.
    monkeypatch.setattr(
        "app.services.skills.get_skills_loader", lambda **kwargs: _Broken()
    )
    assert _get_skill_guidance("画一张柱状图") == ""
