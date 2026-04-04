"""Tests for partial completion detection in code_executor and deep_think_agent."""
from __future__ import annotations

import pytest


class TestDetectPartialCompletion:
    """Unit tests for _detect_partial_completion in code_executor."""

    @staticmethod
    def _detect(stdout="", stderr="", produced_files=None, success=True):
        from tool_box.tools_impl.code_executor import _detect_partial_completion

        return _detect_partial_completion(
            stdout, stderr, produced_files or [], success=success,
        )

    def test_clean_output_returns_empty(self):
        result = self._detect(stdout="All done\nProcessing complete", produced_files=["/a.csv"])
        assert not result.get("partial_completion_suspected")
        assert not result.get("output_warnings")

    def test_warning_lines_detected_in_stderr(self):
        result = self._detect(stderr="Warning: file not found\nContinuing...")
        assert "output_warnings" in result
        assert any("file not found" in w for w in result["output_warnings"])

    def test_error_lines_detected_in_stdout(self):
        result = self._detect(stdout="Processing...\nError: permission denied\nDone")
        assert "output_warnings" in result
        assert any("permission denied" in w for w in result["output_warnings"])

    def test_skipping_pattern_detected(self):
        result = self._detect(stdout="Skipping Fibroblast due to low cell count")
        assert "output_warnings" in result
        assert any("Skipping" in w for w in result["output_warnings"])

    def test_failed_to_process_detected(self):
        result = self._detect(stdout="Failed to process Myeloid cells")
        assert "output_warnings" in result

    def test_traceback_detected(self):
        result = self._detect(stdout="Traceback (most recent call last):\n  File ...\nKeyError: 'x'")
        assert "output_warnings" in result
        assert any("Traceback" in w for w in result["output_warnings"])

    def test_partial_ratio_detected_of_pattern(self):
        result = self._detect(stdout="Processed 2 of 6 cell types successfully")
        assert result.get("partial_completion_suspected") is True
        assert result.get("partial_ratio") == "2/6"

    def test_partial_ratio_detected_slash_pattern(self):
        result = self._detect(stdout="Completed 3/8 samples")
        assert result.get("partial_completion_suspected") is True
        assert result.get("partial_ratio") == "3/8"

    def test_full_ratio_not_flagged(self):
        """6/6 should not be flagged as partial."""
        result = self._detect(
            stdout="Processed 6 of 6 cell types successfully",
            produced_files=["/a.csv"],
        )
        assert not result.get("partial_completion_suspected")

    def test_no_files_produced_on_success_is_suspicious(self):
        result = self._detect(stdout="All done", produced_files=[], success=True)
        assert result.get("partial_completion_suspected") is True

    def test_no_files_on_failure_not_flagged_as_partial(self):
        """When success=False, missing files is expected — don't double-flag."""
        result = self._detect(stdout="", produced_files=[], success=False)
        assert not result.get("partial_completion_suspected")

    def test_partial_ratio_only_on_success(self):
        """Partial ratio detection should only fire when success=True."""
        result = self._detect(
            stdout="Processed 2 of 6 cell types", produced_files=[], success=False,
        )
        assert not result.get("partial_ratio")

    def test_warnings_capped_at_20(self):
        lines = "\n".join(f"Warning: issue {i}" for i in range(30))
        result = self._detect(stderr=lines)
        assert len(result.get("output_warnings", [])) <= 20

    def test_combined_stdout_and_stderr(self):
        result = self._detect(
            stdout="Processed 2 of 6 items",
            stderr="Warning: memory low",
            produced_files=["/a.csv", "/b.csv"],
            success=True,
        )
        assert result.get("partial_completion_suspected") is True
        assert result.get("partial_ratio") == "2/6"
        assert any("memory low" in w for w in result.get("output_warnings", []))


class TestBuildToolCallbackSummaryPartial:
    """Tests for _build_tool_callback_summary surfacing partial completion signals."""

    @staticmethod
    def _build_summary(result):
        from app.services.deep_think_agent import DeepThinkAgent

        return DeepThinkAgent._build_tool_callback_summary(result)

    def test_normal_result_uses_summary(self):
        result = {"summary": "Task completed successfully", "success": True}
        summary = self._build_summary(result)
        assert "Task completed" in summary

    def test_partial_completion_surfaced(self):
        result = {
            "summary": "Execution done",
            "success": True,
            "partial_completion_suspected": True,
            "partial_ratio": "2/6",
        }
        summary = self._build_summary(result)
        assert "PARTIAL COMPLETION" in summary
        assert "2/6" in summary

    def test_output_warnings_surfaced(self):
        result = {
            "summary": "Done",
            "success": True,
            "output_warnings": ["Warning: skipped 3 items due to errors"],
        }
        summary = self._build_summary(result)
        assert "warning" in summary.lower()

    def test_no_warnings_no_extra_text(self):
        result = {"summary": "All good", "success": True}
        summary = self._build_summary(result)
        assert "PARTIAL" not in summary
        assert "warning" not in summary.lower()

    def test_error_result_still_works(self):
        result = {"error": "Connection timeout", "success": False}
        summary = self._build_summary(result)
        assert "Connection timeout" in summary
