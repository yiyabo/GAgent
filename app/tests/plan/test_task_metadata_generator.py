"""
Tests for task_metadata_generator module.
"""
import pytest
from app.services.plans.task_metadata_generator import (
    ensure_task_metadata,
    generate_acceptance_criteria,
    generate_artifact_contract,
    _extract_explicit_output_paths,
)


class TestExtractExplicitOutputPaths:
    """Test extraction of explicit output paths from instruction text."""

    def test_chinese_save_pattern(self):
        text = "将结果保存到 output/report.md"
        paths = _extract_explicit_output_paths(text)
        assert paths == ["output/report.md"]

    def test_chinese_output_pattern(self):
        text = "输出到 results/data.csv"
        paths = _extract_explicit_output_paths(text)
        assert paths == ["results/data.csv"]

    def test_chinese_write_pattern(self):
        text = "写入到 /tmp/output.txt"
        paths = _extract_explicit_output_paths(text)
        assert paths == ["/tmp/output.txt"]

    def test_english_save_pattern(self):
        text = "Save to output/report.md"
        paths = _extract_explicit_output_paths(text)
        assert paths == ["output/report.md"]

    def test_english_output_pattern(self):
        text = "Output to results/data.csv"
        paths = _extract_explicit_output_paths(text)
        assert paths == ["results/data.csv"]

    def test_english_write_pattern(self):
        text = "Write to /tmp/output.txt"
        paths = _extract_explicit_output_paths(text)
        assert paths == ["/tmp/output.txt"]

    def test_no_explicit_pattern(self):
        text = "分析数据并生成报告"
        paths = _extract_explicit_output_paths(text)
        assert paths == []

    def test_multiple_patterns(self):
        text = "保存到 output/a.csv，然后输出到 results/b.csv"
        paths = _extract_explicit_output_paths(text)
        assert paths == ["output/a.csv", "results/b.csv"]

    def test_deduplication(self):
        text = "保存到 output/report.md，再次保存到 output/report.md"
        paths = _extract_explicit_output_paths(text)
        assert paths == ["output/report.md"]


class TestGenerateAcceptanceCriteria:
    """Test generation of acceptance_criteria based on task type."""

    def test_output_task_with_explicit_path(self):
        ac = generate_acceptance_criteria(
            task_name="生成数据报告",
            instruction="分析数据并保存到 output/report.md",
        )
        assert ac is not None
        assert ac["category"] == "file_data"
        assert ac["blocking"] is True
        assert len(ac["checks"]) == 1
        assert ac["checks"][0]["type"] == "file_nonempty"
        assert ac["checks"][0]["path"] == "output/report.md"

    def test_output_task_without_explicit_path(self):
        ac = generate_acceptance_criteria(
            task_name="生成数据报告",
            instruction="分析数据并生成报告",
        )
        assert ac is None

    def test_analysis_task(self):
        ac = generate_acceptance_criteria(
            task_name="数据分析",
            instruction="分析数据并生成统计结果",
        )
        assert ac is None

    def test_fetch_task_with_explicit_path(self):
        ac = generate_acceptance_criteria(
            task_name="下载数据集",
            instruction="从服务器下载到 data/dataset.csv",
        )
        assert ac is not None
        assert ac["category"] == "file_data"
        assert ac["blocking"] is True
        assert len(ac["checks"]) == 1
        assert ac["checks"][0]["type"] == "file_exists"
        assert ac["checks"][0]["path"] == "data/dataset.csv"

    def test_fetch_task_without_explicit_path(self):
        ac = generate_acceptance_criteria(
            task_name="下载数据集",
            instruction="从服务器下载数据集",
        )
        assert ac is None

    def test_unknown_task_type(self):
        ac = generate_acceptance_criteria(
            task_name="执行任务",
            instruction="执行某些操作",
        )
        assert ac is None


class TestGenerateArtifactContract:
    """Test generation of artifact_contract based on acceptance_criteria."""

    def test_with_acceptance_criteria(self):
        ac = {
            "category": "file_data",
            "blocking": True,
            "checks": [
                {"type": "file_nonempty", "path": "output/report.md"},
                {"type": "file_exists", "path": "results/data.csv"},
            ],
        }
        contract = generate_artifact_contract(
            task_name="生成报告",
            instruction="生成报告",
            acceptance_criteria=ac,
        )
        assert contract is not None
        assert "publishes" in contract
        assert len(contract["publishes"]) == 2
        assert "output.report.md" in contract["publishes"]
        assert "output.data.csv" in contract["publishes"]

    def test_without_acceptance_criteria(self):
        contract = generate_artifact_contract(
            task_name="生成报告",
            instruction="生成报告",
            acceptance_criteria=None,
        )
        assert contract is None

    def test_with_empty_acceptance_criteria(self):
        contract = generate_artifact_contract(
            task_name="生成报告",
            instruction="生成报告",
            acceptance_criteria={"checks": []},
        )
        assert contract is None


class TestEnsureTaskMetadata:
    """Test the main ensure_task_metadata function."""

    def test_preserves_valid_acceptance_criteria(self):
        metadata = {
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "file_exists", "path": "output.txt"}],
            }
        }
        result = ensure_task_metadata(
            metadata=metadata,
            task_name="生成报告",
            instruction="保存到 output.txt",
        )
        assert "acceptance_criteria" in result
        assert result["acceptance_criteria"]["checks"][0]["path"] == "output.txt"

    def test_removes_invalid_acceptance_criteria(self):
        metadata = {
            "acceptance_criteria": "invalid"
        }
        result = ensure_task_metadata(
            metadata=metadata,
            task_name="生成报告",
            instruction="保存到 output.txt",
        )
        assert "acceptance_criteria" in result
        assert isinstance(result["acceptance_criteria"], dict)

    def test_generates_missing_acceptance_criteria(self):
        metadata = {}
        result = ensure_task_metadata(
            metadata=metadata,
            task_name="生成报告",
            instruction="保存到 output/report.md",
        )
        assert "acceptance_criteria" in result
        assert result["acceptance_criteria"]["checks"][0]["path"] == "output/report.md"

    def test_preserves_valid_artifact_contract(self):
        metadata = {
            "artifact_contract": {
                "publishes": ["output.report_md"],
            }
        }
        result = ensure_task_metadata(
            metadata=metadata,
            task_name="生成报告",
            instruction="保存到 output/report.md",
        )
        assert "artifact_contract" in result
        assert result["artifact_contract"]["publishes"] == ["output.report_md"]

    def test_removes_invalid_artifact_contract(self):
        metadata = {
            "artifact_contract": "invalid"
        }
        result = ensure_task_metadata(
            metadata=metadata,
            task_name="生成报告",
            instruction="保存到 output/report.md",
        )
        assert "artifact_contract" in result
        assert isinstance(result["artifact_contract"], dict)

    def test_generates_missing_artifact_contract(self):
        metadata = {}
        result = ensure_task_metadata(
            metadata=metadata,
            task_name="生成报告",
            instruction="保存到 output/report.md",
        )
        assert "artifact_contract" in result
        assert "output.report.md" in result["artifact_contract"]["publishes"]

    def test_handles_none_metadata(self):
        result = ensure_task_metadata(
            metadata=None,
            task_name="生成报告",
            instruction="保存到 output/report.md",
        )
        assert isinstance(result, dict)
        assert "acceptance_criteria" in result
        assert "artifact_contract" in result

    def test_does_not_mutate_input(self):
        metadata = {"existing_key": "value"}
        result = ensure_task_metadata(
            metadata=metadata,
            task_name="生成报告",
            instruction="保存到 output/report.md",
        )
        assert "existing_key" in metadata
        assert "acceptance_criteria" not in metadata
        assert "existing_key" in result
        assert "acceptance_criteria" in result


class TestTask18Scenario:
    """Test the specific scenario that caused Task 18 to fail."""

    def test_task_18_does_not_generate_false_positive(self):
        """
        Task 18: "论文格式研究报告撰写与输出"
        Instruction mentions input files (数据源文件路径) but no explicit output path.
        Should NOT generate acceptance_criteria to avoid false positives.
        """
        task_name = "论文格式研究报告撰写与输出"
        instruction = """
        撰写一份详细的分析报告，包含以下内容：
        
        数据源文件路径：
        - /home/zczhao/Phage-Agent/results/table1_baseline_characteristics.csv
        - /home/zczhao/Phage-Agent/results/km_overall_survival.png
        - /home/zczhao/Phage-Agent/results/model_performance.csv
        
        输出为单个Markdown文件，保存至 results/ 目录。
        """
        
        ac = generate_acceptance_criteria(task_name, instruction)
        assert ac is None

    def test_task_18_with_explicit_output_path(self):
        """
        If Task 18 had an explicit output path, it should generate criteria.
        """
        task_name = "论文格式研究报告撰写与输出"
        instruction = """
        撰写一份详细的分析报告。
        
        数据源文件路径：
        - /home/zczhao/Phage-Agent/results/table1_baseline_characteristics.csv
        
        保存到 results/final_report.md
        """
        
        ac = generate_acceptance_criteria(task_name, instruction)
        assert ac is not None
        assert ac["checks"][0]["path"] == "results/final_report.md"
        assert not any(
            "table1_baseline_characteristics.csv" in check.get("path", "")
            for check in ac["checks"]
        )
