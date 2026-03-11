"""Tests for skills deep integration into the PlanExecutor execution chain.

Covers:
- SkillsLoader build/injection behaviour
- TaskExecutionContext.skill_context field propagation
- DeepThinkAgent system prompt skill injection
- ExecutionConfig new skill parameters
"""
from __future__ import annotations

import asyncio
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
        assert "[Reference: references/verified_ops.md]" in result

    def test_budget_degrades_to_summary_without_truncating_markdown(self):
        loader = _make_loader()
        result = loader.load_skills_within_budget(
            ["visualization-generator"], max_chars=500
        )
        assert len(result) <= 500
        assert "[Skill: visualization-generator]" in result
        assert "Summary:" in result
        assert "... (truncated)" not in result

    def test_budget_fallback_to_summary(self):
        """When remaining budget is too small, summary mode remains available."""
        loader = _make_loader()
        all_skills = [s["name"] for s in loader.list_skills()]
        result = loader.load_skills_within_budget(all_skills, max_chars=2000)
        assert len(result) > 0
        assert "[Skill:" in result
        assert "Summary:" in result
        assert len(result) <= 2000

    def test_all_skills_fit_with_large_budget(self):
        loader = _make_loader()
        all_skills = [s["name"] for s in loader.list_skills()]
        result = loader.load_skills_within_budget(all_skills, max_chars=500000)
        for name in all_skills:
            assert f"[Skill: {name}]" in result

    def test_injection_order_follows_priority(self):
        loader = _make_loader()
        ordered = ["bio-data-interpreter", "xlsx"]
        result = loader.load_skills_within_budget(ordered, max_chars=50000)
        idx_xlsx = result.index("[Skill: xlsx]")
        idx_bio = result.index("[Skill: bio-data-interpreter]")
        assert idx_xlsx < idx_bio


class TestSkillSelectionRuntime:
    def test_fasta_task_prefers_bio_router(self):
        loader = _make_loader()
        llm = MagicMock()
        result = asyncio.run(
            loader.select_skills(
                task_title="Compute FASTA sequence stats",
                task_description="Compute sequence statistics for the phage genome FASTA",
                llm_service=llm,
                dependency_paths=["/tmp/sample.fasta"],
                tool_hints=[],
                selection_mode="hybrid",
                max_skills=3,
                scope="task",
            )
        )
        assert result.selection_source == "deterministic"
        assert result.selected_skill_ids[0] == "bio-tools-router"
        assert "bio-tools-router" in result.selected_skill_ids

    def test_visualization_task_prefers_visualization_generator(self):
        loader = _make_loader()
        llm = MagicMock()
        result = asyncio.run(
            loader.select_skills(
                task_title="Build publication figure",
                task_description="Create a heatmap and scatter plot from the CSV data",
                llm_service=llm,
                dependency_paths=["/tmp/metrics.csv"],
                tool_hints=["claude_code"],
                selection_mode="hybrid",
                max_skills=3,
                scope="task",
            )
        )
        assert "visualization-generator" in result.selected_skill_ids

    def test_report_task_prefers_scientific_writer(self):
        loader = _make_loader()
        llm = MagicMock()
        result = asyncio.run(
            loader.select_skills(
                task_title="Write report",
                task_description="Draft the methods and results sections for the paper",
                llm_service=llm,
                dependency_paths=[],
                tool_hints=["manuscript_writer"],
                selection_mode="hybrid",
                max_skills=3,
                scope="task",
            )
        )
        assert "scientific-report-writer" in result.selected_skill_ids

    def test_task_selection_is_not_hard_limited_by_plan_candidates(self):
        loader = _make_loader()
        llm = MagicMock()
        result = asyncio.run(
            loader.select_skills(
                task_title="Generate chart",
                task_description="Create a chart from CSV output",
                llm_service=llm,
                dependency_paths=["/tmp/results.csv"],
                tool_hints=["claude_code"],
                preferred_skills=["bio-tools-router"],
                selection_mode="hybrid",
                max_skills=3,
                scope="task",
            )
        )
        assert "visualization-generator" in result.selected_skill_ids

    def test_llm_failure_without_deterministic_match_returns_empty(self):
        loader = _make_loader()
        llm = MagicMock()
        llm.chat.return_value = "not-json"
        result = asyncio.run(
            loader.select_skills(
                task_title="Do a vague thing",
                task_description="General task with no skill-specific cues",
                llm_service=llm,
                dependency_paths=[],
                tool_hints=[],
                selection_mode="hybrid",
                max_skills=3,
                scope="task",
            )
        )
        assert result.selection_source == "llm_fallback"
        assert result.selected_skill_ids == []


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

    def test_task_execution_result_accepts_skill_trace(self):
        from app.services.interpreter.task_executer import TaskExecutionResult, TaskType

        result = TaskExecutionResult(
            task_type=TaskType.TEXT_ONLY,
            success=True,
            skill_trace={"selection_source": "deterministic"},
        )
        assert result.skill_trace == {"selection_source": "deterministic"}


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
        assert cfg.skill_selection_mode == "hybrid"
        assert cfg.skill_max_per_task == 3
        assert cfg.skill_trace_enabled is True

    def test_custom_values(self):
        from app.services.plans.plan_executor import ExecutionConfig

        cfg = ExecutionConfig(
            enable_skills=False,
            skill_budget_chars=3000,
            skill_selection_mode="llm_only",
            skill_max_per_task=2,
            skill_trace_enabled=False,
        )
        assert cfg.enable_skills is False
        assert cfg.skill_budget_chars == 3000
        assert cfg.skill_selection_mode == "llm_only"
        assert cfg.skill_max_per_task == 2
        assert cfg.skill_trace_enabled is False

    def test_from_settings_defaults(self):
        from app.services.plans.plan_executor import ExecutionConfig

        mock_settings = MagicMock(spec=[
            "model", "max_retries", "timeout", "use_context",
            "include_plan_outline", "dependency_throttle", "max_tasks",
            "enable_skills", "skill_budget_chars", "skill_selection_mode",
            "skill_max_per_task", "skill_trace_enabled",
        ])
        mock_settings.model = "test-model"
        mock_settings.max_retries = 2
        mock_settings.timeout = None
        mock_settings.use_context = True
        mock_settings.include_plan_outline = True
        mock_settings.dependency_throttle = True
        mock_settings.max_tasks = None
        mock_settings.enable_skills = True
        mock_settings.skill_budget_chars = 6000
        mock_settings.skill_selection_mode = "hybrid"
        mock_settings.skill_max_per_task = 3
        mock_settings.skill_trace_enabled = True

        cfg = ExecutionConfig.from_settings(mock_settings)
        assert cfg.enable_skills is True
        assert cfg.skill_budget_chars == 6000
        assert cfg.skill_selection_mode == "hybrid"
        assert cfg.skill_max_per_task == 3
        assert cfg.skill_trace_enabled is True
