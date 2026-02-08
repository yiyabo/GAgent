import json
from pathlib import Path

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
