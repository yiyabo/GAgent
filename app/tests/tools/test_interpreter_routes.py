from __future__ import annotations

import asyncio
from pathlib import Path

from app.routers import interpreter_routes as interpreter_routes_module


def test_interpreter_execute_passes_docker_image_override_to_code_executor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "work"
    data_dir = tmp_path / "data"
    task_dir = tmp_path / "task_dir"
    results_dir = task_dir / "results"
    results_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    (results_dir / "artifact.txt").write_text("done", encoding="utf-8")

    captured: dict[str, str] = {}

    async def _fake_code_executor_handler(**kwargs):
        captured["docker_image"] = kwargs["docker_image"]
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

    response = asyncio.run(
        interpreter_routes_module.execute_code(
            interpreter_routes_module.CodeExecuteRequest(
                code="print('ok')",
                work_dir=str(work_dir),
                data_dir=str(data_dir),
                docker_image="custom:image",
            )
        )
    )

    assert response.success is True
    assert response.output == "ok\n"
    assert captured["docker_image"] == "custom:image"
    assert captured["add_dirs"] == f"{work_dir},{data_dir}"
    assert (work_dir / "results" / "artifact.txt").read_text(encoding="utf-8") == "done"
