"""Delegation prompt carries the publication figure style only for figure tasks."""

from pathlib import Path

from tool_box.tools_impl.code_executor import (
    _build_claude_code_prompt,
    _figure_style_prompt,
)

_SANDBOX = Path("runtime/test_figure_style_prompt_sandbox")


def _prompt_for(task: str) -> str:
    return _build_claude_code_prompt(
        task=task,
        task_work_dir=_SANDBOX / "run_1",
        file_prefix="run_1_",
        task_subdirs=["code", "results", "data"],
        execution_spec=None,
        resolved_resources=None,
        allowed_dirs_info="",
    )


def test_figure_intent_triggers_style_block() -> None:
    for task in (
        "画一张柱状图对比三组数据",
        "绘制 eGFR 随时间变化轨迹图",
        "Create a bar chart of group means",
        "Generate a volcano plot for differential expression",
        "Visualize the correlation heatmap",
    ):
        style = _figure_style_prompt(task)
        assert "#E64B35" in style, task
        assert "savefig.dpi" in style, task


def test_non_figure_task_has_no_style_block() -> None:
    for task in ("读取 cohort.csv 并做字段标准化", "Run BLAST for the sequence", "清洗缺失值并输出 CSV"):
        assert _figure_style_prompt(task) == "", task


def test_delegation_prompt_inlines_style_for_figure_task() -> None:
    prompt = _prompt_for("画一张柱状图对比三组数据（A组=12，B组=19，C组=7）")
    assert "Figure style (MANDATORY" in prompt
    assert "PALETTE" in prompt
    assert "'#3C5488'" in prompt


def test_delegation_prompt_omits_style_for_plain_task() -> None:
    prompt = _prompt_for("读取上传的 CSV 并完成宽表转长表")
    assert "Figure style (MANDATORY" not in prompt
    assert "PALETTE" not in prompt
