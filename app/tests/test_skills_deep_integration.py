"""Tests for skills deep integration into the PlanExecutor execution chain.

Covers:
- SkillsLoader.load_skills_within_budget (budget truncation)
- TaskExecutionContext.skill_context field propagation
- DeepThinkAgent system prompt skill injection
- ExecutionConfig new skill parameters
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from app.services.skills import SkillsLoader
from app.services.deep_think_agent import TaskExecutionContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = PROJECT_ROOT / "skills"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_loader() -> SkillsLoader:
    return SkillsLoader(
        skills_dir=str(SKILLS_ROOT),
        project_skills_dir=str(SKILLS_ROOT),
        auto_sync=False,
    )


# ---------------------------------------------------------------------------
# load_skills_within_budget
# ---------------------------------------------------------------------------


class TestLoadSkillsWithinBudget:
    def test_empty_list_returns_empty(self):
        loader = _make_loader()
        assert loader.load_skills_within_budget([]) == ""

    def test_unknown_skill_is_skipped(self):
        loader = _make_loader()
        result = loader.load_skills_within_budget(["nonexistent-skill"])
        assert result == ""

    def test_single_small_skill_within_budget(self):
        loader = _make_loader()
        result = loader.load_skills_within_budget(
            ["bio-tools-router"], max_chars=50000
        )
        assert "[Skill: bio-tools-router]" in result
        assert "Routing rules" in result

    def test_budget_truncates_large_content(self):
        loader = _make_loader()
        result = loader.load_skills_within_budget(
            ["visualization-generator"], max_chars=500
        )
        assert len(result) <= 600  # some overhead from join
        assert "[Skill: visualization-generator]" in result

    def test_budget_fallback_to_summary(self):
        """When remaining budget is too small for content, fall back to summary."""
        loader = _make_loader()
        all_skills = [s["name"] for s in loader.list_skills()]
        result = loader.load_skills_within_budget(all_skills, max_chars=2000)
        assert len(result) > 0
        parts = result.split("\n\n")
        has_full = any("[Skill:" in p for p in parts)
        has_summary = any(p.startswith("- ") for p in parts)
        assert has_full, "At least one skill should be loaded in full"
        if len(all_skills) > 1:
            assert has_summary or len(result) <= 2000

    def test_all_skills_fit_with_large_budget(self):
        loader = _make_loader()
        all_skills = [s["name"] for s in loader.list_skills()]
        result = loader.load_skills_within_budget(all_skills, max_chars=500000)
        for name in all_skills:
            assert f"[Skill: {name}]" in result

    def test_order_is_preserved(self):
        loader = _make_loader()
        ordered = ["bio-tools-router", "bio-tools-troubleshooting"]
        result = loader.load_skills_within_budget(ordered, max_chars=50000)
        idx_router = result.index("[Skill: bio-tools-router]")
        idx_trouble = result.index("[Skill: bio-tools-troubleshooting]")
        assert idx_router < idx_trouble


# ---------------------------------------------------------------------------
# TaskExecutionContext.skill_context
# ---------------------------------------------------------------------------


class TestTaskExecutionContextSkillField:
    def test_default_is_none(self):
        ctx = TaskExecutionContext()
        assert ctx.skill_context is None

    def test_accepts_string(self):
        ctx = TaskExecutionContext(skill_context="[Skill: test]\nSome content")
        assert ctx.skill_context == "[Skill: test]\nSome content"

    def test_other_fields_unaffected(self):
        ctx = TaskExecutionContext(
            task_id=1,
            task_name="Test",
            skill_context="guidance",
        )
        assert ctx.task_id == 1
        assert ctx.task_name == "Test"
        assert ctx.skill_context == "guidance"


# ---------------------------------------------------------------------------
# DeepThinkAgent system prompt injection
# ---------------------------------------------------------------------------


class TestSystemPromptSkillInjection:
    """Verify that skill_context appears in both native and legacy prompts."""

    def _make_agent(self):
        from app.services.deep_think_agent import DeepThinkAgent

        mock_llm = MagicMock()
        mock_llm.stream_chat_with_tools_async = None
        return DeepThinkAgent(
            llm_client=mock_llm,
            available_tools=["web_search"],
            tool_executor=AsyncMock(),
        )

    def test_native_prompt_includes_skill_context(self):
        agent = self._make_agent()
        ctx = TaskExecutionContext(
            task_id=1,
            task_instruction="Analyze phage genome",
            skill_context="[Skill: bio-tools-router]\nPrefer Tier-1 ops.",
        )
        prompt = agent._build_native_system_prompt(context={}, task_context=ctx)
        assert "=== SKILL GUIDANCE ===" in prompt
        assert "[Skill: bio-tools-router]" in prompt
        assert "Prefer Tier-1 ops." in prompt

    def test_native_prompt_without_skill_context(self):
        agent = self._make_agent()
        ctx = TaskExecutionContext(
            task_id=1,
            task_instruction="Analyze data",
        )
        prompt = agent._build_native_system_prompt(context={}, task_context=ctx)
        assert "SKILL GUIDANCE" not in prompt

    def test_legacy_prompt_includes_skill_context(self):
        agent = self._make_agent()
        ctx = TaskExecutionContext(
            task_id=2,
            task_instruction="Generate visualization",
            skill_context="[Skill: visualization-generator]\nUse COLORS palette.",
        )
        prompt = agent._build_system_prompt(context={}, task_context=ctx)
        assert "=== SKILL GUIDANCE ===" in prompt
        assert "[Skill: visualization-generator]" in prompt
        assert "Use COLORS palette." in prompt

    def test_legacy_prompt_without_skill_context(self):
        agent = self._make_agent()
        ctx = TaskExecutionContext(
            task_id=2,
            task_instruction="Simple task",
        )
        prompt = agent._build_system_prompt(context={}, task_context=ctx)
        assert "SKILL GUIDANCE" not in prompt


# ---------------------------------------------------------------------------
# ExecutionConfig new fields
# ---------------------------------------------------------------------------


class TestExecutionConfigSkillParams:
    def test_default_values(self):
        from app.services.plans.plan_executor import ExecutionConfig

        cfg = ExecutionConfig()
        assert cfg.enable_skills is True
        assert cfg.skill_budget_chars == 6000

    def test_custom_values(self):
        from app.services.plans.plan_executor import ExecutionConfig

        cfg = ExecutionConfig(enable_skills=False, skill_budget_chars=3000)
        assert cfg.enable_skills is False
        assert cfg.skill_budget_chars == 3000

    def test_from_settings_defaults(self):
        from app.services.plans.plan_executor import ExecutionConfig

        mock_settings = MagicMock(spec=[
            "model", "max_retries", "timeout", "use_context",
            "include_plan_outline", "dependency_throttle", "max_tasks",
        ])
        mock_settings.model = "test-model"
        mock_settings.max_retries = 2
        mock_settings.timeout = None
        mock_settings.use_context = True
        mock_settings.include_plan_outline = True
        mock_settings.dependency_throttle = True
        mock_settings.max_tasks = None

        cfg = ExecutionConfig.from_settings(mock_settings)
        assert cfg.enable_skills is True
        assert cfg.skill_budget_chars == 6000
