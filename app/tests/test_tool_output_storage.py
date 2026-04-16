from __future__ import annotations

import json
from pathlib import Path

from app.services import tool_output_storage as tool_output_storage_module


def test_store_tool_output_without_session_uses_runtime_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tool_output_storage_module, "get_runtime_root", lambda: tmp_path / "runtime")

    stored = tool_output_storage_module.store_tool_output(
        session_id=None,
        job_id="123",
        action={"order": 1},
        tool_name="literature_pipeline",
        raw_result={"ok": True, "artifacts": ["runtime/lit_reviews/example/evidence.md"]},
        summary="stored",
    )

    assert stored is not None
    assert stored.output_dir.startswith("tool_outputs/job_123/step_1_literature_pipeline_")
    result_path = tmp_path / "runtime" / stored.result_path
    manifest_path = tmp_path / "runtime" / stored.manifest_path
    assert result_path.exists()
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["result"]["path"].startswith("tool_outputs/job_123/step_1_literature_pipeline_")