import json
from pathlib import Path

import pytest

from app.config.deliverable_config import DeliverableSettings, RESEARCH_MODULES
from app.services.deliverables.publisher import DeliverablePublisher


def _build_publisher(tmp_path: Path) -> DeliverablePublisher:
    settings = DeliverableSettings(
        enabled=True,
        default_template="research",
        show_draft=False,
        history_max=1,
        single_version_only=True,
        modules=RESEARCH_MODULES,
    )
    return DeliverablePublisher(
        settings=settings,
        project_root=tmp_path,
        runtime_dir=tmp_path / "runtime",
    )


def test_publish_copies_code_into_latest_manifest(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    source = tmp_path / "workspace" / "analysis.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("print('hello deliverables')\n", encoding="utf-8")

    report = publisher.publish_from_tool_result(
        session_id="abc123",
        tool_name="claude_code",
        raw_result={"output_path": str(source)},
        summary="Generated implementation script.",
        task_name="Implement method",
        task_instruction="Write the core method implementation.",
    )

    assert report is not None
    latest_root = tmp_path / "runtime" / "session_abc123" / "deliverables" / "latest"
    assert (latest_root / "code" / "analysis.py").exists()

    manifest_path = tmp_path / "runtime" / "session_abc123" / "deliverables" / "manifest_latest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["published_files_count"] >= 1
    assert "code" in manifest["published_modules"]


def test_publish_creates_incremental_paper_docs_and_refs(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    bib = (
        "@article{smith2026,\n"
        "  title={Large Models for Biology},\n"
        "  author={Smith, Alex},\n"
        "  journal={BioAI},\n"
        "  year={2026}\n"
        "}"
    )

    report = publisher.publish_from_tool_result(
        session_id="paper001",
        tool_name="manuscript_writer",
        raw_result={"content": "We introduce a practical methodology.", "bibtex": bib},
        summary="Introduction section draft with motivation and scope.",
        task_name="Write introduction",
        task_instruction="Draft the Introduction section for the paper.",
    )

    assert report is not None
    latest_root = tmp_path / "runtime" / "session_paper001" / "deliverables" / "latest"
    main_tex = latest_root / "paper" / "main.tex"
    intro_tex = latest_root / "paper" / "sections" / "introduction.tex"
    intro_doc = latest_root / "docs" / "introduction.md"
    refs_bib = latest_root / "refs" / "references.bib"

    assert main_tex.exists()
    assert intro_tex.exists()
    assert intro_doc.exists()
    assert refs_bib.exists()
    assert "AUTO_PLACEHOLDER" not in intro_tex.read_text(encoding="utf-8")
    assert "smith2026" in refs_bib.read_text(encoding="utf-8")


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


def test_publish_keeps_single_latest_snapshot_without_history(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    source_dir = tmp_path / "workspace"
    source_dir.mkdir(parents=True, exist_ok=True)

    first = source_dir / "abstract.md"
    first.write_text("abstract content", encoding="utf-8")
    second = source_dir / "introduction.md"
    second.write_text("introduction content", encoding="utf-8")

    report_1 = publisher.publish_from_tool_result(
        session_id="single001",
        tool_name="file_operations",
        raw_result={"save_path": str(first)},
        summary="Draft abstract",
    )
    report_2 = publisher.publish_from_tool_result(
        session_id="single001",
        tool_name="file_operations",
        raw_result={"save_path": str(second)},
        summary="Draft introduction",
    )

    assert report_1 is not None
    assert report_2 is not None

    deliverables_root = tmp_path / "runtime" / "session_single001" / "deliverables"
    history_root = deliverables_root / "history"
    latest_manifest_path = deliverables_root / "manifest_latest.json"
    latest_manifest = json.loads(latest_manifest_path.read_text(encoding="utf-8"))

    assert not history_root.exists()
    assert latest_manifest["published_files_count"] >= 2
    assert any(item.get("path") == "docs/abstract.md" for item in latest_manifest.get("items", []))
    assert any(item.get("path") == "docs/introduction.md" for item in latest_manifest.get("items", []))


def test_publish_ignores_non_whitelisted_docs(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    source = tmp_path / "workspace" / "random_notes.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# report\n", encoding="utf-8")

    report = publisher.publish_from_tool_result(
        session_id="noise001",
        tool_name="claude_code",
        raw_result={"output_path": str(source)},
        summary="Generated report.",
    )

    assert report is None
    latest_root = tmp_path / "runtime" / "session_noise001" / "deliverables" / "latest"
    assert not (latest_root / "docs" / "random_notes.md").exists()


def test_publish_ignores_path_like_strings_in_non_path_fields(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    source = tmp_path / "workspace" / "code" / "train.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("print('train')\n", encoding="utf-8")

    report = publisher.publish_from_tool_result(
        session_id="noise_paths",
        tool_name="claude_code",
        raw_result={
            "stdout": f"Generated file at {source}",
            "message": f"See {source}",
        },
        summary="Execution finished.",
    )

    assert report is None
    latest_root = tmp_path / "runtime" / "session_noise_paths" / "deliverables" / "latest"
    assert not (latest_root / "code" / "train.py").exists()


def test_publish_scans_task_directory_not_session_root(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    runtime_session = tmp_path / "runtime" / "session_session_alpha"
    task_dir = runtime_session / "task_intro"
    other_task_dir = runtime_session / "task_noise"
    (task_dir / "code").mkdir(parents=True, exist_ok=True)
    (other_task_dir / "code").mkdir(parents=True, exist_ok=True)
    (task_dir / "code" / "intro.py").write_text("print('intro')\n", encoding="utf-8")
    (other_task_dir / "code" / "noise.py").write_text("print('noise')\n", encoding="utf-8")

    report = publisher.publish_from_tool_result(
        session_id="session_alpha",
        tool_name="claude_code",
        raw_result={
            "session_directory": str(runtime_session),
            "task_directory_full": str(task_dir),
        },
        summary="Generated code in task workspace.",
    )

    assert report is not None
    latest_root = tmp_path / "runtime" / "session_alpha" / "deliverables" / "latest"
    assert (latest_root / "code" / "intro.py").exists()
    assert not (latest_root / "code" / "noise.py").exists()


def test_publish_prefers_produced_files_over_directory_scan(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    runtime_session = tmp_path / "runtime" / "session_session_alpha"
    task_root = runtime_session / "plan7_task11"
    old_file = task_root / "run_older" / "code" / "noise.py"
    new_file = task_root / "run_new" / "code" / "intro.py"
    old_file.parent.mkdir(parents=True, exist_ok=True)
    new_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_text("print('old')\n", encoding="utf-8")
    new_file.write_text("print('new')\n", encoding="utf-8")

    report = publisher.publish_from_tool_result(
        session_id="session_alpha",
        tool_name="claude_code",
        raw_result={
            "task_directory_full": str(task_root),
            "produced_files": [str(new_file)],
        },
        summary="Generated code in this run.",
    )

    assert report is not None
    latest_root = tmp_path / "runtime" / "session_alpha" / "deliverables" / "latest"
    assert (latest_root / "code" / "intro.py").exists()
    assert not (latest_root / "code" / "noise.py").exists()


def test_publish_skips_failed_tool_results(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    source = tmp_path / "workspace" / "analysis.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("print('hello')\n", encoding="utf-8")

    report = publisher.publish_from_tool_result(
        session_id="failed001",
        tool_name="claude_code",
        raw_result={"success": False, "output_path": str(source), "error": "boom"},
        summary="Execution failed.",
    )

    assert report is None
    manifest_path = tmp_path / "runtime" / "session_failed001" / "deliverables" / "manifest_latest.json"
    assert not manifest_path.exists()


def test_publish_ignores_raw_tool_output_metadata_files(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    noise_root = (
        tmp_path
        / "data"
        / "information_sessions"
        / "session-session_beta"
        / "tool_outputs"
        / "job_unknown"
        / "step_22_file_operations_6eaa21"
    )
    noise_root.mkdir(parents=True, exist_ok=True)
    manifest = noise_root / "manifest.json"
    preview = noise_root / "preview.json"
    result = noise_root / "result.json"
    manifest.write_text("{}", encoding="utf-8")
    preview.write_text("{}", encoding="utf-8")
    result.write_text("{}", encoding="utf-8")

    report = publisher.publish_from_tool_result(
        session_id="noise_meta001",
        tool_name="file_operations",
        raw_result={
            "operation": "list",
            "success": True,
            "manifest_path": str(manifest),
            "preview_path": str(preview),
            "result_path": str(result),
        },
        summary="list finished",
    )

    assert report is None
    latest_root = tmp_path / "runtime" / "session_noise_meta001" / "deliverables" / "latest"
    assert not (latest_root / "code" / "manifest.json").exists()
    assert not (latest_root / "code" / "preview.json").exists()
    assert not (latest_root / "code" / "result.json").exists()


def test_publish_does_not_create_docs_for_web_search_text_only(tmp_path: Path):
    publisher = _build_publisher(tmp_path)

    report = publisher.publish_from_tool_result(
        session_id="web001",
        tool_name="web_search",
        raw_result={
            "success": True,
            "summary": "Web search finished.",
            "response": "Some fetched context text about SIAMCAT.",
        },
        summary="Web search finished.",
        task_name="Write results section",
        task_instruction="Draft result section",
    )

    assert report is None
    latest_root = tmp_path / "runtime" / "session_web001" / "deliverables" / "latest"
    assert not (latest_root / "docs" / "result.md").exists()
    assert not (latest_root / "paper" / "sections" / "result.tex").exists()


def test_publish_accepts_paths_under_project_symlink(tmp_path: Path):
    publisher = _build_publisher(tmp_path)

    external_root = tmp_path / "external_data"
    external_root.mkdir(parents=True, exist_ok=True)
    project_data_link = tmp_path / "data"
    project_data_link.symlink_to(external_root, target_is_directory=True)

    source = project_data_link / "experiment_ML" / "1" / "code" / "siamcat_repro" / "train.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("print('symlink-source')\n", encoding="utf-8")

    report = publisher.publish_from_tool_result(
        session_id="symlink001",
        tool_name="file_operations",
        raw_result={
            "operation": "write",
            "success": True,
            "path": str(source),
        },
        summary="Wrote train.py",
    )

    assert report is not None
    latest_root = tmp_path / "runtime" / "session_symlink001" / "deliverables" / "latest"
    copied = latest_root / "code" / "train.py"
    assert copied.exists()
    assert copied.read_text(encoding="utf-8") == "print('symlink-source')\n"


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
        / "paper"
        / "figures"
        / "roc.png"
    )
    assert staged.exists()
    assert staged.read_bytes() == b"roc-image"


def test_publish_updates_staged_figure_for_same_source_path(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    figure = tmp_path / "workspace" / "plots" / "summary.png"
    figure.parent.mkdir(parents=True, exist_ok=True)
    figure.write_bytes(b"old-image")

    first = publisher.publish_from_tool_result(
        session_id="figure_update001",
        tool_name="claude_code",
        raw_result={"output_path": str(figure)},
        summary="Generated figure.",
    )
    assert first is not None

    figure.write_bytes(b"new-image")
    second = publisher.publish_from_tool_result(
        session_id="figure_update001",
        tool_name="claude_code",
        raw_result={"output_path": str(figure)},
        summary="Updated figure.",
    )

    assert second is not None
    staged = (
        tmp_path
        / "runtime"
        / "session_figure_update001"
        / "deliverables"
        / "latest"
        / "paper"
        / "figures"
        / "summary.png"
    )
    assert staged.exists()
    assert staged.read_bytes() == b"new-image"


def test_publish_rejects_conflicting_figure_basenames_from_different_sources(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    figure_a = tmp_path / "workspace" / "run_a" / "plot.png"
    figure_b = tmp_path / "workspace" / "run_b" / "plot.png"
    figure_a.parent.mkdir(parents=True, exist_ok=True)
    figure_b.parent.mkdir(parents=True, exist_ok=True)
    figure_a.write_bytes(b"plot-a")
    figure_b.write_bytes(b"plot-b")

    first = publisher.publish_from_tool_result(
        session_id="figure_conflict001",
        tool_name="claude_code",
        raw_result={"output_path": str(figure_a)},
        summary="Generated first figure.",
    )
    assert first is not None

    with pytest.raises(ValueError, match="Conflicting deliverable basename 'plot.png'"):
        publisher.publish_from_tool_result(
            session_id="figure_conflict001",
            tool_name="claude_code",
            raw_result={"output_path": str(figure_b)},
            summary="Generated conflicting figure.",
        )


def test_publish_uses_previous_manifest_source_for_legacy_figure_updates(tmp_path: Path):
    publisher = _build_publisher(tmp_path)
    figure = tmp_path / "workspace" / "legacy" / "plot.png"
    figure.parent.mkdir(parents=True, exist_ok=True)
    figure.write_bytes(b"old-bytes")

    session_root = tmp_path / "runtime" / "session_legacy_figure001" / "deliverables"
    latest_root = session_root / "latest"
    image_dir = latest_root / "image_tabular"
    figure_dir = latest_root / "paper" / "figures"
    image_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "plot.png").write_bytes(b"old-bytes")
    (figure_dir / "plot.png").write_bytes(b"old-bytes")
    (session_root / "manifest_latest.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "module": "image_tabular",
                        "path": "image_tabular/plot.png",
                        "source_path": str(figure),
                    },
                    {
                        "module": "paper",
                        "path": "paper/figures/plot.png",
                        "source_path": str(figure),
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    figure.write_bytes(b"new-bytes")
    report = publisher.publish_from_tool_result(
        session_id="legacy_figure001",
        tool_name="claude_code",
        raw_result={"output_path": str(figure)},
        summary="Updated legacy figure.",
    )

    assert report is not None
    assert (image_dir / "plot.png").read_bytes() == b"new-bytes"
    assert (figure_dir / "plot.png").read_bytes() == b"new-bytes"
