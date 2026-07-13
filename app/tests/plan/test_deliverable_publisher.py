import json
from pathlib import Path

from app.config.deliverable_config import (
    DeliverableConflictStrategy,
    DeliverableSettings,
    RESEARCH_MODULES,
)
from app.services.deliverables.publisher import DeliverablePublisher


def _build_publisher(
    tmp_path: Path,
    *,
    conflict_strategy: DeliverableConflictStrategy = "error",
) -> DeliverablePublisher:
    settings = DeliverableSettings(
        enabled=True,
        default_template="research",
        show_draft=False,
        history_max=1,
        single_version_only=True,
        modules=RESEARCH_MODULES,
        basename_conflict_strategy=conflict_strategy,
    )
    return DeliverablePublisher(
        settings=settings,
        project_root=tmp_path,
        runtime_dir=tmp_path / "runtime",
    )


def test_code_executor_does_not_auto_publish(tmp_path: Path) -> None:
    publisher = _build_publisher(tmp_path)
    png = tmp_path / "workspace" / "submission" / "plot.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b"img")

    report = publisher.publish_from_tool_result(
        session_id="exp_skip001",
        tool_name="code_executor",
        raw_result={"output_path": str(png), "success": True},
        summary="Generated plot.",
    )

    assert report is None


def test_explicit_ingest_applies_deliverable_submit_artifacts(tmp_path: Path) -> None:
    publisher = _build_publisher(tmp_path)
    png = tmp_path / "workspace" / "submission" / "plot.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b"img")

    report = publisher.publish_from_tool_result(
        session_id="exp_submit001",
        tool_name="deliverable_submit",
        raw_result={
            "success": True,
            "deliverable_submit": {
                "publish": True,
                "artifacts": [{"path": str(png), "module": "image_tabular"}],
            },
        },
        summary="User-approved deliverables.",
    )

    assert report is not None
    assert report.submit_artifacts_requested == 1
    assert report.submit_artifacts_published == 1
    assert report.submit_artifacts_skipped == 0
    assert report.warnings == []
    latest_root = tmp_path / "runtime" / "session_exp_submit001" / "deliverables" / "latest"
    assert (latest_root / "image_tabular" / "plot.png").read_bytes() == b"img"


def test_explicit_ingest_deliverable_submit_reports_partial_warnings(tmp_path: Path) -> None:
    publisher = _build_publisher(tmp_path)
    png = tmp_path / "workspace" / "submission" / "plot.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b"img")

    report = publisher.publish_from_tool_result(
        session_id="exp_submit_warn001",
        tool_name="deliverable_submit",
        raw_result={
            "success": True,
            "deliverable_submit": {
                "publish": True,
                "artifacts": [
                    {"path": str(png), "module": "image_tabular"},
                    {"path": str(tmp_path / "workspace" / "missing.png"), "module": "image_tabular"},
                    {"path": str(png), "module": "unknown"},
                ],
            },
        },
        summary="User-approved deliverables.",
    )

    assert report is not None
    assert report.submit_artifacts_requested == 3
    assert report.submit_artifacts_published == 1
    assert report.submit_artifacts_skipped == 2
    assert len(report.warnings) == 2
    assert "does not exist" in report.warnings[0]
    assert "unsupported module 'unknown'" in report.warnings[1]
    latest_root = tmp_path / "runtime" / "session_exp_submit_warn001" / "deliverables" / "latest"
    assert (latest_root / "image_tabular" / "plot.png").read_bytes() == b"img"


def test_explicit_ingest_deliverable_submit_returns_report_when_all_artifacts_skipped(tmp_path: Path) -> None:
    publisher = _build_publisher(tmp_path)

    report = publisher.publish_from_tool_result(
        session_id="exp_submit_skip001",
        tool_name="deliverable_submit",
        raw_result={
            "success": True,
            "deliverable_submit": {
                "publish": True,
                "artifacts": [
                    {"path": str(tmp_path / "workspace" / "missing.png"), "module": "image_tabular"},
                    {"path": str(tmp_path / "workspace" / "missing.py"), "module": "code"},
                ],
            },
        },
        summary="User-approved deliverables.",
    )

    assert report is not None
    assert report.submit_artifacts_requested == 2
    assert report.submit_artifacts_published == 0
    assert report.submit_artifacts_skipped == 2
    assert len(report.warnings) == 2
    manifest_path = tmp_path / "runtime" / "session_exp_submit_skip001" / "deliverables" / "manifest_latest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["published_files_count"] == 0
    assert report.submit_summary().startswith(
        "Deliverable submit published 0 artifact(s); skipped 2 with warnings"
    )


def test_explicit_ingest_manuscript_writer_still_publishes_sections(tmp_path: Path) -> None:
    publisher = _build_publisher(tmp_path)
    section_file = tmp_path / "workspace" / "sections" / "01_introduction.md"
    section_file.parent.mkdir(parents=True, exist_ok=True)
    section_file.write_text("## Intro\nHello.\n", encoding="utf-8")

    report = publisher.publish_from_tool_result(
        session_id="exp_ms001",
        tool_name="manuscript_writer",
        raw_result={
            "tool": "manuscript_writer",
            "sections": [{"section": "introduction", "path": str(section_file)}],
        },
        summary="intro",
        task_name="Write introduction",
    )

    assert report is not None
    latest_root = tmp_path / "runtime" / "session_exp_ms001" / "deliverables" / "latest"
    assert (latest_root / "paper" / "sections" / "introduction.tex").exists()


def test_publish_maps_manuscript_writer_outputs_to_paper_and_docs(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    section_file = tmp_path / "workspace" / "sections" / "01_introduction.md"
    section_file.parent.mkdir(parents=True, exist_ok=True)
    section_file.write_text("## Introduction\\nManuscript section text.\\n", encoding="utf-8")
    output_file = tmp_path / "workspace" / "final.md"
    output_file.write_text("## Final Manuscript\\nBody text.\\n", encoding="utf-8")
    analysis_file = tmp_path / "workspace" / "final.md.analysis.md"
    analysis_file.write_text("# Analysis\\nAudit notes.\\n", encoding="utf-8")

    report = publisher.publish_from_tool_result(
        session_id="paper_map001",
        tool_name="manuscript_writer",
        raw_result={
            "tool": "manuscript_writer",
            "success": True,
            "public_release_ready": True,
            "release_state": "final",
            "release_summary": "Ready for publication.",
            "hidden_artifact_prefixes": ["tool_outputs/review_pack_writer/review_pack_20260311_000000"],
            "sections": [
                {
                    "section": "introduction",
                    "path": str(section_file),
                }
            ],
            "output_path": str(output_file),
            "analysis_path": str(analysis_file),
        },
        summary="manuscript finished",
        task_name="Write introduction",
    )

    assert report is not None
    latest_root = tmp_path / "runtime" / "session_paper_map001" / "deliverables" / "latest"
    intro_tex = latest_root / "paper" / "sections" / "introduction.tex"
    assert intro_tex.exists()
    intro_tex_text = intro_tex.read_text(encoding="utf-8")
    assert "Manuscript section text." in intro_tex_text
    assert "manuscript finished" not in intro_tex_text
    assert (latest_root / "docs" / "introduction.md").exists()
    assert (latest_root / "docs" / "analysis.md").exists()
    assert (latest_root / "docs" / "report.md").exists()
    manifest = json.loads((tmp_path / "runtime" / "session_paper_map001" / "deliverables" / "manifest_latest.json").read_text(encoding="utf-8"))
    assert manifest["release_state"] == "final"
    assert manifest["public_release_ready"] is True
    assert manifest["hidden_artifact_prefixes"] == ["tool_outputs/review_pack_writer/review_pack_20260311_000000"]


def test_publish_manuscript_discussion_creates_docs(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    section_file = tmp_path / "workspace" / "sections" / "06_discussion.md"
    section_file.parent.mkdir(parents=True, exist_ok=True)
    section_file.write_text("## Discussion\nInterpretation of the findings.\n", encoding="utf-8")

    report = publisher.publish_from_tool_result(
        session_id="discussion001",
        tool_name="manuscript_writer",
        raw_result={
            "tool": "manuscript_writer",
            "sections": [{"section": "discussion", "path": str(section_file)}],
        },
        summary="discussion ready",
        task_name="Write discussion",
    )

    assert report is not None
    latest_root = tmp_path / "runtime" / "session_discussion001" / "deliverables" / "latest"
    assert (latest_root / "paper" / "sections" / "discussion.tex").exists()
    discussion_doc = latest_root / "docs" / "discussion.md"
    assert discussion_doc.exists()
    assert "Interpretation of the findings." in discussion_doc.read_text(encoding="utf-8")


def test_publish_review_pack_writer_maps_nested_manuscript_outputs(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    section_file = tmp_path / "workspace" / "sections" / "01_abstract.md"
    output_file = tmp_path / "workspace" / "review_draft.md"
    analysis_file = tmp_path / "workspace" / "review_draft.md.analysis.md"
    refs_file = tmp_path / "workspace" / "references.bib"

    section_file.parent.mkdir(parents=True, exist_ok=True)
    section_file.write_text("## Abstract\nReview abstract grounded in evidence.\n", encoding="utf-8")
    output_file.write_text(
        "## Abstract\nReview abstract grounded in evidence ([@known2026]).\n\n"
        "## References\n\n[@known2026]\n",
        encoding="utf-8",
    )
    analysis_file.write_text("# Analysis\nReview audit notes.\n", encoding="utf-8")
    refs_file.write_text(
        "@article{known2026,\n"
        "  title={Known Review Reference},\n"
        "  author={Doe, Jane and Smith, Alex},\n"
        "  journal={BioAI},\n"
        "  year={2026},\n"
        "  doi={10.1/example}\n"
        "}\n",
        encoding="utf-8",
    )

    report = publisher.publish_from_tool_result(
        session_id="review_pack001",
        tool_name="review_pack_writer",
        raw_result={
            "tool": "review_pack_writer",
            "success": True,
            "public_release_ready": True,
            "release_state": "final",
            "release_summary": "Ready for publication.",
            "hidden_artifact_prefixes": ["tool_outputs/review_pack_writer/review_pack_20260311_000000"],
            "pack": {
                "outputs": {
                    "references_bib": str(refs_file),
                }
            },
            "draft": {
                "tool": "manuscript_writer",
                "success": True,
                "public_release_ready": True,
                "release_state": "final",
                "sections": [
                    {
                        "section": "abstract",
                        "path": str(section_file),
                    }
                ],
                "output_path": str(output_file),
                "analysis_path": str(analysis_file),
            },
        },
        summary="review pack finished",
        task_name="Write review abstract",
    )

    assert report is not None
    latest_root = tmp_path / "runtime" / "session_review_pack001" / "deliverables" / "latest"
    abstract_tex = latest_root / "paper" / "sections" / "abstract.tex"
    abstract_doc = latest_root / "docs" / "abstract.md"
    report_doc = latest_root / "docs" / "report.md"
    analysis_doc = latest_root / "docs" / "analysis.md"
    refs_bib = latest_root / "refs" / "references.bib"

    assert abstract_tex.exists()
    assert "AUTO_PLACEHOLDER" not in abstract_tex.read_text(encoding="utf-8")
    assert "Review abstract grounded in evidence." in abstract_tex.read_text(encoding="utf-8")
    assert abstract_doc.exists()
    assert report_doc.exists()
    report_text = report_doc.read_text(encoding="utf-8")
    assert "Doe and Smith, 2026" in report_text
    assert "Known Review Reference. BioAI. DOI: 10.1/example" in report_text
    assert "[@known2026]" not in report_text
    assert analysis_doc.exists()
    assert refs_bib.exists()
    assert report.paper_status["completed_count"] >= 1
    assert "abstract" in report.paper_status["completed_sections"]
    assert report.release_state == "final"
    assert report.public_release_ready is True


def test_publish_blocked_review_pack_writer_only_exposes_release_summary(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    section_file = tmp_path / "workspace" / "sections" / "01_abstract.md"
    partial_output = tmp_path / "workspace" / "review_draft.partial.md"
    analysis_file = tmp_path / "workspace" / "review_draft.md.analysis.md"
    refs_file = tmp_path / "workspace" / "references.bib"

    section_file.parent.mkdir(parents=True, exist_ok=True)
    section_file.write_text("## Abstract\nPartial review abstract.\n", encoding="utf-8")
    partial_output.write_text("## Abstract\nPartial review abstract.\n", encoding="utf-8")
    analysis_file.write_text("# Analysis\nPartial draft notes.\n", encoding="utf-8")
    refs_file.write_text(
        "@article{partial2026,\n"
        "  title={Partial Review Reference},\n"
        "  author={Doe, Jane},\n"
        "  year={2026}\n"
        "}\n",
        encoding="utf-8",
    )

    report = publisher.publish_from_tool_result(
        session_id="review_pack_partial001",
        tool_name="review_pack_writer",
        raw_result={
            "tool": "review_pack_writer",
            "success": False,
            "public_release_ready": False,
            "release_state": "blocked",
            "release_summary": "Publication blocked: section quality gate failed for abstract.",
            "hidden_artifact_prefixes": [
                "tool_outputs/review_pack_writer/review_pack_20260311_000000",
                ".manuscript_writer_20260311_000000",
            ],
            "pack": {
                "outputs": {
                    "references_bib": str(refs_file),
                }
            },
            "draft": {
                "tool": "manuscript_writer",
                "success": False,
                "error_code": "section_evaluation_failed",
                "public_release_ready": False,
                "release_state": "blocked",
                "sections": [
                    {
                        "section": "abstract",
                        "path": str(section_file),
                    }
                ],
                "partial_output_path": str(partial_output),
                "analysis_path": str(analysis_file),
            },
        },
        summary="review pack partial draft",
        task_name="Write review abstract",
    )

    assert report is not None
    latest_root = tmp_path / "runtime" / "session_review_pack_partial001" / "deliverables" / "latest"
    summary_doc = latest_root / "docs" / "release_summary.md"

    assert summary_doc.exists()
    assert "Publication blocked" in summary_doc.read_text(encoding="utf-8")
    assert not (latest_root / "paper" / "sections" / "abstract.tex").exists()
    assert not (latest_root / "docs" / "analysis.md").exists()
    assert not (latest_root / "refs" / "references.bib").exists()
    manifest = json.loads(Path(report.manifest_path).read_text(encoding="utf-8"))
    assert manifest["release_state"] == "blocked"
    assert manifest["public_release_ready"] is False
    assert manifest["items"] == [
        {
            "module": "docs",
            "path": "docs/release_summary.md",
            "status": "final",
            "size": summary_doc.stat().st_size,
            "updated_at": manifest["items"][0]["updated_at"],
            "source_path": "task:unknown",
        }
    ]
    assert report.paper_status["completed_count"] == 0
    assert report.release_state == "blocked"
    assert report.public_release_ready is False


def test_publish_draft_manuscript_uses_bio_profile_and_filters_placeholder_sections(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    section_dir = tmp_path / "workspace" / ".manuscript_draft_sections"
    output_file = tmp_path / "workspace" / "manuscript_draft.md"
    analysis_file = tmp_path / "workspace" / "manuscript_draft.md.analysis.md"
    method_file = section_dir / "03_method.md"
    result_file = section_dir / "04_result.md"

    section_dir.mkdir(parents=True, exist_ok=True)
    method_file.write_text("Methods text grounded in completed task outputs.\n", encoding="utf-8")
    result_file.write_text("Results text grounded in completed task outputs.\n", encoding="utf-8")
    output_file.write_text("# Draft\n\nMethods and results.\n", encoding="utf-8")
    analysis_file.write_text("# Analysis\n\nDraft assembly notes.\n", encoding="utf-8")

    report = publisher.publish_from_tool_result(
        session_id="session_draft_profile001",
        tool_name="manuscript_writer",
        raw_result={
            "tool": "manuscript_writer",
            "success": True,
            "draft_only": True,
            "public_release_ready": False,
            "release_state": "draft",
            "release_summary": "Local manuscript draft assembled from completed outputs.",
            "section_profile": "bio_manuscript",
            "applicable_sections": [
                "abstract",
                "introduction",
                "method",
                "result",
                "discussion",
                "conclusion",
            ],
            "completed_sections": ["method", "result"],
            "missing_sections": ["abstract", "introduction", "discussion", "conclusion"],
            "sections": [
                {"section": "method", "path": str(method_file), "substantive": True},
                {"section": "result", "path": str(result_file), "substantive": True},
            ],
            "output_path": str(output_file),
            "analysis_path": str(analysis_file),
        },
        summary="draft manuscript",
        task_name="Assemble ovarian cancer manuscript draft",
    )

    assert report is not None
    latest_root = tmp_path / "runtime" / "session_draft_profile001" / "deliverables" / "latest"
    manifest = json.loads(Path(report.manifest_path).read_text(encoding="utf-8"))
    item_paths = {str(item["path"]) for item in manifest["items"]}

    assert report.release_state == "draft"
    assert report.public_release_ready is False
    assert report.paper_status["section_profile"] == "bio_manuscript"
    assert report.paper_status["applicable_sections"] == [
        "abstract",
        "introduction",
        "method",
        "result",
        "discussion",
        "conclusion",
    ]
    assert report.paper_status["completed_count"] == 2
    assert report.paper_status["completed_sections"] == ["method", "result"]
    assert "abstract" in report.paper_status["missing_sections"]
    assert "paper/sections/method.tex" in item_paths
    assert "paper/sections/result.tex" in item_paths
    assert "paper/sections/abstract.tex" not in item_paths
    assert "paper/sections/experiment.tex" not in item_paths
    assert "refs/references.bib" not in item_paths
    assert "docs/report.md" in item_paths
    assert "docs/analysis.md" in item_paths
    assert (latest_root / "paper" / "sections" / "method.tex").exists()
    assert (latest_root / "paper" / "sections" / "result.tex").exists()


def test_publish_blocked_manuscript_run_clears_previous_public_paper_outputs(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    section_file = tmp_path / "workspace" / "sections" / "01_abstract.md"
    output_file = tmp_path / "workspace" / "review_draft.md"
    analysis_file = tmp_path / "workspace" / "review_draft.md.analysis.md"
    section_file.parent.mkdir(parents=True, exist_ok=True)
    section_file.write_text("## Abstract\nVisible abstract.\n", encoding="utf-8")
    output_file.write_text("## Abstract\nVisible abstract.\n", encoding="utf-8")
    analysis_file.write_text("# Analysis\nVisible analysis.\n", encoding="utf-8")

    first = publisher.publish_from_tool_result(
        session_id="session_reset001",
        tool_name="manuscript_writer",
        raw_result={
            "tool": "manuscript_writer",
            "success": True,
            "public_release_ready": True,
            "release_state": "final",
            "sections": [{"section": "abstract", "path": str(section_file)}],
            "output_path": str(output_file),
            "analysis_path": str(analysis_file),
        },
        summary="ready",
    )
    assert first is not None

    second = publisher.publish_from_tool_result(
        session_id="session_reset001",
        tool_name="manuscript_writer",
        raw_result={
            "tool": "manuscript_writer",
            "success": False,
            "public_release_ready": False,
            "release_state": "blocked",
            "release_summary": "Publication blocked: release gate failed.",
            "hidden_artifact_prefixes": [".manuscript_writer_20260311_000000"],
        },
        summary="blocked",
    )
    assert second is not None

    latest_root = tmp_path / "runtime" / "session_reset001" / "deliverables" / "latest"
    assert not (latest_root / "paper" / "sections" / "abstract.tex").exists()
    assert not (latest_root / "docs" / "report.md").exists()
    assert (latest_root / "docs" / "release_summary.md").exists()


def test_publish_manuscript_section_stages_markdown_images(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    image = tmp_path / "workspace" / "assets" / "roc.png"
    section_file = tmp_path / "workspace" / "sections" / "03_result.md"
    image.parent.mkdir(parents=True, exist_ok=True)
    section_file.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"roc-image")
    section_file.write_text(
        "## Results\n![ROC](../assets/roc.png)\n",
        encoding="utf-8",
    )

    report = publisher.publish_from_tool_result(
        session_id="section_figure001",
        tool_name="manuscript_writer",
        raw_result={
            "tool": "manuscript_writer",
            "sections": [{"section": "result", "path": str(section_file)}],
        },
        summary="result ready",
        task_name="Write results",
    )

    assert report is not None
    staged = (
        tmp_path
        / "runtime"
        / "session_section_figure001"
        / "deliverables"
        / "latest"
        / "image_tabular"
        / "roc.png"
    )
    assert staged.exists()
    assert staged.read_bytes() == b"roc-image"


def test_publish_rejects_conflicting_doc_basenames_from_different_sources(tmp_path: Path) -> None:
    publisher = _build_publisher(tmp_path)
    doc_a = tmp_path / "workspace" / "draft_a" / "report.md"
    doc_b = tmp_path / "workspace" / "draft_b" / "report.md"
    doc_a.parent.mkdir(parents=True, exist_ok=True)
    doc_b.parent.mkdir(parents=True, exist_ok=True)
    doc_a.write_text("# Draft A\n", encoding="utf-8")
    doc_b.write_text("# Draft B\n", encoding="utf-8")

    first = publisher.publish_from_tool_result(
        session_id="doc_conflict001",
        tool_name="deliverable_submit",
        raw_result={
            "success": True,
            "deliverable_submit": {
                "publish": True,
                "artifacts": [{"path": str(doc_a), "module": "docs"}],
            },
        },
        summary="Published first draft.",
    )
    assert first is not None

    second = publisher.publish_from_tool_result(
        session_id="doc_conflict001",
        tool_name="deliverable_submit",
        raw_result={
            "success": True,
            "deliverable_submit": {
                "publish": True,
                "artifacts": [{"path": str(doc_b), "module": "docs"}],
            },
        },
        summary="Published conflicting draft.",
    )

    assert second is not None
    assert second.submit_artifacts_requested == 1
    assert second.submit_artifacts_published == 0
    assert second.submit_artifacts_skipped == 1
    assert "Conflicting deliverable basename 'report.md'" in second.warnings[0]


def test_publish_renames_conflicting_doc_basenames_when_strategy_is_rename(tmp_path: Path) -> None:
    publisher = _build_publisher(tmp_path, conflict_strategy="rename")
    doc_a = tmp_path / "workspace" / "draft_a" / "report.md"
    doc_b = tmp_path / "workspace" / "draft_b" / "report.md"
    doc_a.parent.mkdir(parents=True, exist_ok=True)
    doc_b.parent.mkdir(parents=True, exist_ok=True)
    doc_a.write_text("# Draft A\n", encoding="utf-8")
    doc_b.write_text("# Draft B\n", encoding="utf-8")

    first = publisher.publish_from_tool_result(
        session_id="doc_rename001",
        tool_name="deliverable_submit",
        raw_result={
            "success": True,
            "deliverable_submit": {
                "publish": True,
                "artifacts": [{"path": str(doc_a), "module": "docs"}],
            },
        },
        summary="Published first draft.",
    )
    assert first is not None

    second = publisher.publish_from_tool_result(
        session_id="doc_rename001",
        tool_name="deliverable_submit",
        raw_result={
            "success": True,
            "deliverable_submit": {
                "publish": True,
                "artifacts": [{"path": str(doc_b), "module": "docs"}],
            },
        },
        summary="Published renamed draft.",
    )

    assert second is not None
    latest_root = tmp_path / "runtime" / "session_doc_rename001" / "deliverables" / "latest" / "docs"
    assert (latest_root / "report.md").read_text(encoding="utf-8").startswith("# Draft A\n")
    assert (latest_root / "report__2.md").read_text(encoding="utf-8").startswith("# Draft B\n")


def test_publish_keep_first_skips_conflicting_doc_basename_with_warning(tmp_path: Path) -> None:
    publisher = _build_publisher(tmp_path, conflict_strategy="keep_first")
    doc_a = tmp_path / "workspace" / "draft_a" / "report.md"
    doc_b = tmp_path / "workspace" / "draft_b" / "report.md"
    doc_a.parent.mkdir(parents=True, exist_ok=True)
    doc_b.parent.mkdir(parents=True, exist_ok=True)
    doc_a.write_text("# Draft A\n", encoding="utf-8")
    doc_b.write_text("# Draft B\n", encoding="utf-8")

    first = publisher.publish_from_tool_result(
        session_id="doc_keep001",
        tool_name="deliverable_submit",
        raw_result={
            "success": True,
            "deliverable_submit": {
                "publish": True,
                "artifacts": [{"path": str(doc_a), "module": "docs"}],
            },
        },
        summary="Published first draft.",
    )
    assert first is not None

    second = publisher.publish_from_tool_result(
        session_id="doc_keep001",
        tool_name="deliverable_submit",
        raw_result={
            "success": True,
            "deliverable_submit": {
                "publish": True,
                "artifacts": [{"path": str(doc_b), "module": "docs"}],
            },
        },
        summary="Attempted conflicting draft.",
    )

    assert second is not None
    assert second.submit_artifacts_requested == 1
    assert second.submit_artifacts_published == 0
    assert second.submit_artifacts_skipped == 1
    assert second.warnings
    assert "kept existing deliverable per conflict strategy" in second.warnings[0]
    latest_root = tmp_path / "runtime" / "session_doc_keep001" / "deliverables" / "latest" / "docs"
    assert (latest_root / "report.md").read_text(encoding="utf-8").startswith("# Draft A\n")
    assert not (latest_root / "report__2.md").exists()


def test_explicit_submit_can_override_global_conflict_strategy_with_rename(tmp_path: Path) -> None:
    publisher = _build_publisher(tmp_path, conflict_strategy="error")
    doc_a = tmp_path / "workspace" / "draft_a" / "report.md"
    doc_b = tmp_path / "workspace" / "draft_b" / "report.md"
    doc_a.parent.mkdir(parents=True, exist_ok=True)
    doc_b.parent.mkdir(parents=True, exist_ok=True)
    doc_a.write_text("# Draft A\n", encoding="utf-8")
    doc_b.write_text("# Draft B\n", encoding="utf-8")

    first = publisher.publish_from_tool_result(
        session_id="doc_per_submit_rename001",
        tool_name="deliverable_submit",
        raw_result={
            "success": True,
            "deliverable_submit": {
                "publish": True,
                "artifacts": [{"path": str(doc_a), "module": "docs"}],
            },
        },
        summary="Published first draft.",
    )
    assert first is not None

    second = publisher.publish_from_tool_result(
        session_id="doc_per_submit_rename001",
        tool_name="deliverable_submit",
        raw_result={
            "success": True,
            "deliverable_submit": {
                "publish": True,
                "conflict_strategy": "rename",
                "artifacts": [{"path": str(doc_b), "module": "docs"}],
            },
        },
        summary="Published renamed draft.",
    )

    assert second is not None
    latest_root = tmp_path / "runtime" / "session_doc_per_submit_rename001" / "deliverables" / "latest" / "docs"
    assert (latest_root / "report.md").read_text(encoding="utf-8").startswith("# Draft A\n")
    assert (latest_root / "report__2.md").read_text(encoding="utf-8").startswith("# Draft B\n")


def test_publish_docs_md_rewrites_relative_image_paths(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    viz_dir = tmp_path / "workspace" / "visualizations"
    viz_dir.mkdir(parents=True, exist_ok=True)
    roc = viz_dir / "roc_curve.png"
    cal = viz_dir / "calibration_curve.png"
    roc.write_bytes(b"roc-image-data")
    cal.write_bytes(b"cal-image-data")
    report_dir = tmp_path / "workspace" / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_md = report_dir / "final_report.md"
    report_md.write_text(
        "# Report\n\n![ROC](../visualizations/roc_curve.png)\n\n![Cal](../visualizations/calibration_curve.png)\n",
        encoding="utf-8",
    )

    report = publisher.publish_from_tool_result(
        session_id="rewrite_md_paths001",
        tool_name="deliverable_submit",
        raw_result={
            "deliverable_submit": {
                "artifacts": [
                    {"path": str(report_md), "module": "docs"},
                    {"path": str(roc), "module": "image_tabular"},
                    {"path": str(cal), "module": "image_tabular"},
                ],
                "publish": True,
            },
        },
        summary="Report with images",
    )
    assert report is not None

    latest_root = (
        tmp_path
        / "runtime"
        / "session_rewrite_md_paths001"
        / "deliverables"
        / "latest"
    )
    doc_file = latest_root / "docs" / "final_report.md"
    assert doc_file.exists()
    content = doc_file.read_text(encoding="utf-8")
    assert "../visualizations/" not in content
    assert "image_tabular/roc_curve.png" in content
    assert "image_tabular/calibration_curve.png" in content
    assert not (latest_root / "paper" / "Hu2025Naturecommunica.pdf").exists()
