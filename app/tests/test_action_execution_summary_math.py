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
