from __future__ import annotations

import asyncio
from pathlib import Path

from app.services import path_router as path_router_module
from app.services.path_router import get_path_router
from tool_box.tools_impl import manuscript_writer as manuscript_writer_module
from tool_box.tools_impl import review_pack_writer as review_pack_writer_module


def test_manuscript_writer_draft_only_sets_task_output_location(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    runtime_root = repo_root / "runtime"
    repo_root.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_root.resolve()))
    monkeypatch.setattr(manuscript_writer_module, "_PROJECT_ROOT", repo_root.resolve())
    monkeypatch.setattr(manuscript_writer_module, "_RUNTIME_DIR", runtime_root.resolve())
    monkeypatch.setattr(path_router_module, "_default_router", None)

    context_file = repo_root / "context.md"
    context_file.write_text("Key result: accepted evidence.", encoding="utf-8")

    router = get_path_router()
    task_dir = router.get_task_output_dir("demo", 12, [5], create=True)
    output_file = task_dir / "draft.md"

    result = asyncio.run(
        manuscript_writer_module.manuscript_writer_handler(
            task="Write a concise draft based on the accepted evidence.",
            output_path=str(output_file),
            context_paths=["context.md"],
            draft_only=True,
            keep_workspace=True,
            session_id="demo",
            task_id=12,
            ancestor_chain=[5],
        )
    )

    assert result["success"] is True
    assert result["output_location"]["task_id"] == 12
    assert result["output_location"]["ancestor_chain"] == [5]
    assert any(path.endswith("draft.md") for path in result["output_location"]["files"])


def test_manuscript_writer_promotes_workspace_output_into_task_output_location(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    runtime_root = repo_root / "runtime"
    repo_root.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_root.resolve()))
    monkeypatch.setattr(manuscript_writer_module, "_PROJECT_ROOT", repo_root.resolve())
    monkeypatch.setattr(manuscript_writer_module, "_RUNTIME_DIR", runtime_root.resolve())
    monkeypatch.setattr(path_router_module, "_default_router", None)

    context_file = repo_root / "context.md"
    context_file.write_text("Key result: accepted evidence.", encoding="utf-8")

    router = get_path_router()
    task_dir = router.get_task_output_dir("demo", 12, [5], create=True)
    workspace_output = runtime_root / "session_demo" / "workspace" / "report.md"

    result = asyncio.run(
        manuscript_writer_module.manuscript_writer_handler(
            task="Write a concise draft based on the accepted evidence.",
            output_path="workspace/report.md",
            context_paths=["context.md"],
            draft_only=True,
            keep_workspace=True,
            session_id="demo",
            task_id=12,
            ancestor_chain=[5],
        )
    )

    promoted_report = task_dir / "report.md"
    assert result["success"] is True
    assert workspace_output.exists()
    assert promoted_report.exists()
    assert result["output_path"] == "runtime/session_demo/workspace/report.md"
    assert result["effective_output_path"] == "raw_files/task_5/task_12/report.md"
    assert any(path.endswith("raw_files/task_5/task_12/report.md") for path in result["output_location"]["files"])


def test_review_pack_writer_sets_task_output_location(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    runtime_root = repo_root / "runtime"
    repo_root.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_root.resolve()))
    monkeypatch.setattr(review_pack_writer_module, "_PROJECT_ROOT", repo_root.resolve())
    monkeypatch.setattr(path_router_module, "_default_router", None)

    async def _fake_literature_pipeline_handler(*args, **kwargs):
        output_dir = Path(kwargs["out_dir"]).resolve()
        (output_dir / "docs").mkdir(parents=True, exist_ok=True)
        (output_dir / "library.jsonl").write_text("{}\n", encoding="utf-8")
        (output_dir / "study_cards.jsonl").write_text("{}\n", encoding="utf-8")
        (output_dir / "coverage_report.json").write_text('{"pass": true}', encoding="utf-8")
        (output_dir / "references.bib").write_text("@article{a}\n", encoding="utf-8")
        (output_dir / "evidence.md").write_text("evidence", encoding="utf-8")
        (output_dir / "docs" / "evidence_coverage.md").write_text("coverage", encoding="utf-8")
        (output_dir / "docs" / "study_matrix.md").write_text("matrix", encoding="utf-8")
        return {
            "success": True,
            "evidence_coverage_passed": True,
            "coverage_summary": "ok",
            "coverage_report_path": str(output_dir / "coverage_report.json"),
            "outputs": {
                "library_jsonl": str(output_dir / "library.jsonl"),
                "study_cards_jsonl": str(output_dir / "study_cards.jsonl"),
                "coverage_report_json": str(output_dir / "coverage_report.json"),
                "references_bib": str(output_dir / "references.bib"),
                "evidence_md": str(output_dir / "evidence.md"),
                "evidence_coverage_md": str(output_dir / "docs" / "evidence_coverage.md"),
                "study_matrix_md": str(output_dir / "docs" / "study_matrix.md"),
                "pdf_dir": str(output_dir / "pdfs"),
            },
        }

    async def _fake_manuscript_writer_handler(*args, **kwargs):
        output_path = Path(kwargs["output_path"]).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("review draft", encoding="utf-8")
        return {
            "success": True,
            "quality_gate_passed": True,
            "polish_gate_passed": True,
            "public_release_ready": True,
            "release_state": "final",
            "release_summary": "ready",
            "output_path": str(output_path),
            "temp_workspace": str(output_path.parent),
        }

    monkeypatch.setattr(
        review_pack_writer_module,
        "literature_pipeline_handler",
        _fake_literature_pipeline_handler,
    )
    monkeypatch.setattr(
        review_pack_writer_module,
        "manuscript_writer_handler",
        _fake_manuscript_writer_handler,
    )

    result = asyncio.run(
        review_pack_writer_module.review_pack_writer_handler(
            topic="Accepted evidence review",
            session_id="demo",
            task_id=42,
            ancestor_chain=[7],
            keep_workspace=True,
        )
    )

    assert result["success"] is True
    assert result["output_location"]["task_id"] == 42
    assert result["output_location"]["ancestor_chain"] == [7]
    assert any(path.endswith("review_draft.md") for path in result["output_location"]["files"])
    assert any(path.endswith("docs/study_matrix.md") for path in result["output_location"]["files"])