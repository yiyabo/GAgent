from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from tool_box.tools_impl import result_interpreter as result_interpreter_module


def test_result_interpreter_execute_uses_code_executor(monkeypatch, tmp_path) -> None:
    work_dir = tmp_path / "work"
    task_dir = tmp_path / "task_dir"
    results_dir = task_dir / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "artifact.txt").write_text("done", encoding="utf-8")

    captured: dict[str, str] = {}

    async def _fake_code_executor_handler(**kwargs):
        captured["task"] = kwargs["task"]
        captured["add_dirs"] = kwargs["add_dirs"]
        return {
            "success": True,
            "stdout": "ok\n",
            "stderr": "",
            "exit_code": 0,
            "task_directory_full": str(task_dir),
        }

    monkeypatch.setattr(
        "tool_box.tools_impl.code_executor.code_executor_handler",
        _fake_code_executor_handler,
    )

    result = asyncio.run(
        result_interpreter_module.result_interpreter_handler(
            operation="execute",
            code="print('ok')",
            work_dir=str(work_dir),
            data_dir=str(tmp_path / "data"),
        )
    )

    assert result["success"] is True
    assert result["output"] == "ok\n"
    assert "print('ok')" in captured["task"]
    assert str(work_dir) in captured["add_dirs"]
    assert (work_dir / "results" / "artifact.txt").exists()


def test_result_interpreter_execute_skips_copy_when_executor_uses_same_work_dir(
    monkeypatch,
    tmp_path,
) -> None:
    work_dir = tmp_path / "work"
    results_dir = work_dir / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "artifact.txt").write_text("done", encoding="utf-8")

    async def _fake_code_executor_handler(**_kwargs):
        return {
            "success": True,
            "stdout": "ok\n",
            "stderr": "",
            "exit_code": 0,
            "task_directory_full": str(work_dir),
        }

    monkeypatch.setattr(
        "tool_box.tools_impl.code_executor.code_executor_handler",
        _fake_code_executor_handler,
    )

    result = asyncio.run(
        result_interpreter_module.result_interpreter_handler(
            operation="execute",
            code="print('ok')",
            work_dir=str(work_dir),
        )
    )

    assert result["success"] is True
    assert result["output"] == "ok\n"
    assert (work_dir / "results" / "artifact.txt").read_text(encoding="utf-8") == "done"


def test_result_interpreter_analyze_uses_text_response_and_skips_visual_mode_for_overview(
    monkeypatch,
    tmp_path,
) -> None:
    data_file = tmp_path / "gvd.tsv"
    data_file.write_text("gene\tvalue\nA\t1\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class _FakeMetadata:
        def model_dump(self):
            return {
                "filename": "gvd.tsv",
                "file_format": "tsv",
                "total_rows": 1,
                "total_columns": 2,
            }

    class _FakeTaskExecutor:
        def __init__(self, *args, **kwargs):
            _ = args, kwargs

        async def execute(self, **kwargs):
            captured["is_visualization"] = kwargs.get("is_visualization")
            return SimpleNamespace(
                task_type=SimpleNamespace(value="text_only"),
                success=True,
                final_code=None,
                code_description=None,
                code_output=None,
                text_response="Dataset overview based on extracted metadata:",
                code_error=None,
                error_message=None,
                total_attempts=1,
                has_visualization=False,
                visualization_purpose=None,
                visualization_analysis=None,
            )

    monkeypatch.setattr(
        "app.services.interpreter.DataProcessor.get_metadata",
        lambda _fp: _FakeMetadata(),
    )
    monkeypatch.setattr("app.services.interpreter.TaskExecutor", _FakeTaskExecutor)

    result = asyncio.run(
        result_interpreter_module.result_interpreter_handler(
            operation="analyze",
            file_paths=[str(data_file)],
            task_title="GVD Phage Dataset Overview",
            task_description="Provide a quick overview of rows, columns, and schema only.",
            work_dir=str(tmp_path / "work"),
        )
    )

    assert result["success"] is True
    assert result["task_type"] == "text_only"
    assert result["execution_output"] == "Dataset overview based on extracted metadata:"
    assert result["code_description"] == "Direct metadata overview generated without code execution"
    assert captured["is_visualization"] is False


def test_result_interpreter_profile_returns_deterministic_summary_and_id_matches(
    tmp_path,
) -> None:
    data_file = tmp_path / "gvd.tsv"
    data_file.write_text(
        "Phage_ID\tLength\tHost\nphage_a\t12000\tHostA\nphage_b\t15000\tHostB\n",
        encoding="utf-8",
    )
    lookup_file = tmp_path / "batch_test_phageids.txt"
    lookup_file.write_text("phage_a\nphage_missing\n", encoding="utf-8")

    result = asyncio.run(
        result_interpreter_module.result_interpreter_handler(
            operation="profile",
            file_paths=[str(data_file), str(lookup_file)],
        )
    )

    assert result["success"] is True
    assert result["operation"] == "profile"
    assert result["task_type"] == "text_only"
    assert result["profile_mode"] == "deterministic"
    assert result["metadata"][0]["filename"] == "gvd.tsv"
    assert result["metadata"][0]["total_rows"] == 2
    assert result["profile"]["structured_datasets"][0]["column_names"][:2] == [
        "Phage_ID",
        "Length",
    ]
    assert result["profile"]["lookup_files"][0]["entry_count"] == 2
    assert result["profile"]["identifier_matches"][0]["identifier_column"] == "Phage_ID"
    assert result["profile"]["identifier_matches"][0]["matched_count"] == 1
    assert result["profile"]["identifier_matches"][0]["missing_count"] == 1
    assert "Deterministic dataset profile" in result["execution_output"]
