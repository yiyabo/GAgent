from __future__ import annotations

import asyncio
from pathlib import Path

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
