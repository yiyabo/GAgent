from __future__ import annotations

import asyncio

from app.routers.chat import action_execution


def test_repair_distribution_summary_math_corrects_derived_percentage() -> None:
    raw = """饼图已成功生成并保存至 `results/completeness_pie_chart.png`。

### Completeness 分布统计（共 31,402 条记录）

| 类别 | 数量 | 占比 |
|---|---|---|
| Low-quality | 14,235 | 45.3% |
| Not-determined | 9,373 | 29.8% |
| Medium-quality | 2,980 | 9.5% |
| High-quality | 2,695 | 8.6% |
| Complete | 2,119 | 6.7% |

### 关键观察
- 只有约 **15.3%** 的噬菌体达到了 Medium-quality 及以上标准（Medium + High + Complete 合计 7,794 条）。
- **Complete（完整基因组）仅占 6.7%**，共 2,119 条。
"""

    repaired = action_execution._repair_distribution_summary_math(raw)

    assert repaired is not None
    assert "**24.8%**" in repaired
    assert "15.3%" not in repaired
    assert "| Complete | 2,119 | 6.7% |" in repaired


def test_repair_distribution_summary_math_leaves_unstructured_text_unchanged() -> None:
    raw = "结果已生成，详情见 results/plot.png。"

    repaired = action_execution._repair_distribution_summary_math(raw)

    assert repaired == raw


def test_generate_tool_analysis_applies_distribution_math_repair(monkeypatch) -> None:
    buggy_analysis = """### Completeness 分布统计（共 31,402 条记录）

| 类别 | 数量 | 占比 |
|---|---|---|
| Low-quality | 14,235 | 45.3% |
| Not-determined | 9,373 | 29.8% |
| Medium-quality | 2,980 | 9.5% |
| High-quality | 2,695 | 8.6% |
| Complete | 2,119 | 6.7% |

- 只有约 **15.3%** 的噬菌体达到了 Medium-quality 及以上标准（Medium + High + Complete 合计 7,794 条）。
"""

    class _FakeLLMService:
        async def chat_async(self, _prompt: str) -> str:
            return buggy_analysis

    monkeypatch.setattr(
        action_execution,
        "_get_llm_service_for_provider",
        lambda _provider: _FakeLLMService(),
    )

    result = asyncio.run(
        action_execution._generate_tool_analysis(
            user_message="分析 Completeness 分布",
            tool_results=[
                {
                    "name": "code_executor",
                    "summary": "generated pie chart",
                    "result": {
                        "success": True,
                        "stdout": "Low-quality 14235\nNot-determined 9373\nMedium-quality 2980\nHigh-quality 2695\nComplete 2119\n",
                    },
                }
            ],
            session_id="session_test",
            llm_provider="qwen",
        )
    )

    assert result is not None
    assert "**24.8%**" in result
    assert "15.3%" not in result


def test_build_contract_verification_analysis_uses_grounded_artifacts() -> None:
    result = action_execution._build_contract_verification_analysis(
        "请检查任务结果",
        [
            {
                "name": "code_executor",
                "result": {
                    "verification_status": "failed",
                    "contract_diff": {
                        "missing_required_outputs": ["subset_manifest.tsv"],
                        "wrong_format_outputs": ["results/subset_manifest.tsv"],
                        "unexpected_outputs": ["results/terminal_code_stats.csv"],
                    },
                    "produced_files": [
                        "/tmp/results/terminal_code_stats.csv",
                        "/tmp/results/terminal_code_summary.md",
                    ],
                },
            }
        ],
    )

    assert result is not None
    assert "确定性产物校验未通过" in result
    assert "subset_manifest.tsv" in result
    assert "terminal_code_stats.csv" in result


def test_build_contract_verification_analysis_ignores_superseded_earlier_failure() -> None:
    result = action_execution._build_contract_verification_analysis(
        "请检查任务结果",
        [
            {
                "name": "code_executor",
                "result": {
                    "verification_status": "failed",
                    "contract_diff": {
                        "missing_required_outputs": ["subset_manifest.csv"],
                        "wrong_format_outputs": ["results/subset_manifest.tsv"],
                        "unexpected_outputs": ["results/subset_manifest.tsv"],
                    },
                    "produced_files": ["/tmp/results/subset_manifest.tsv"],
                },
            },
            {
                "name": "code_executor",
                "result": {
                    "verification_status": "passed",
                    "artifact_verification": {
                        "status": "passed",
                        "actual_outputs": ["results/subset_manifest.csv"],
                    },
                    "produced_files": ["/tmp/results/subset_manifest.csv"],
                },
            },
        ],
    )

    assert result is None


def test_build_contract_verification_success_analysis_uses_canonical_run_paths(
    tmp_path,
) -> None:
    run_dir = tmp_path / "phage_qc_pipeline_952025" / "run_001"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True)
    subset = results_dir / "subset_manifest.csv"
    summary = results_dir / "qc_summary.md"
    report = results_dir / "qc_report.pdf"
    subset.write_text("phage_id\nPhage_001\n", encoding="utf-8")
    summary.write_text("# summary\n", encoding="utf-8")
    report.write_bytes(b"%PDF-1.4\n%stub\n")

    session_promoted = (
        tmp_path
        / "results"
        / "phage_qc_pipeline_952025"
        / "run_001"
        / "subset_manifest.csv"
    )
    session_promoted.parent.mkdir(parents=True)
    session_promoted.write_text("shadow\n", encoding="utf-8")

    result = action_execution._build_contract_verification_success_analysis(
        "请检查任务结果",
        [
            {
                "name": "code_executor",
                "result": {
                    "verification_status": "passed",
                    "task_directory_full": str(run_dir),
                    "session_artifact_paths": [
                        str(session_promoted),
                    ],
                    "artifact_verification": {
                        "status": "passed",
                        "verified_outputs": [
                            "results/subset_manifest.csv",
                            "results/qc_summary.md",
                            "results/qc_report.pdf",
                        ],
                    },
                },
            }
        ],
    )

    assert result is not None
    assert str(subset) in result
    assert str(summary) in result
    assert str(report) in result
    assert f"{tmp_path}/results/phage_qc_pipeline_952025/run_001/results/subset_manifest.csv" not in result
