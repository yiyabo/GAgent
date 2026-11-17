"""p70: deliverable_submit resolves task-relative paths and fixes wrong modules."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.config.deliverable_config import RESEARCH_MODULES, DeliverableSettings
from app.services.deliverables.publisher import DeliverablePublisher
from tool_box.context import ToolContext
from tool_box.tools_impl.deliverable_submit import deliverable_submit_handler


def _publisher(tmp_path: Path) -> DeliverablePublisher:
    settings = DeliverableSettings(
        enabled=True,
        default_template="research",
        show_draft=False,
        history_max=1,
        single_version_only=True,
        modules=RESEARCH_MODULES,
        basename_conflict_strategy="error",
    )
    return DeliverablePublisher(settings=settings, project_root=tmp_path, runtime_dir=tmp_path / "runtime")


def _submit(artifacts, tool_context=None):
    return asyncio.run(deliverable_submit_handler(artifacts=artifacts, tool_context=tool_context))


def test_task_relative_path_becomes_absolute(tmp_path: Path) -> None:
    work_dir = tmp_path / "runtime" / "session_s1" / "raw_files" / "task_1" / "task_17"
    tree = work_dir / "pg_data" / "04_trees" / "domain_trees.newick"
    tree.parent.mkdir(parents=True)
    tree.write_text("(a,b);\n")
    ctx = ToolContext(session_id="s1", plan_id=9, task_id=17, work_dir=str(work_dir))

    result = _submit([{"path": "pg_data/04_trees/domain_trees.newick", "module": "image_tabular"}], ctx)

    assert result["deliverable_submit"]["artifacts"][0]["path"] == str(tree.resolve())


def test_missing_relative_path_is_left_for_the_publisher(tmp_path: Path) -> None:
    ctx = ToolContext(session_id="s1", plan_id=9, task_id=17, work_dir=str(tmp_path / "w"))
    result = _submit([{"path": "nope/x.tsv", "module": "image_tabular"}], ctx)
    assert result["deliverable_submit"]["artifacts"][0]["path"] == "nope/x.tsv"


def test_handler_without_context_is_unchanged() -> None:
    result = _submit([{"path": "raw_files/x.png", "module": "IMAGE_TABULAR"}])
    assert result["deliverable_submit"]["artifacts"] == [{"path": "raw_files/x.png", "module": "image_tabular"}]


def test_wrong_module_is_rerouted_and_readme_is_a_doc(tmp_path: Path) -> None:
    base = tmp_path / "runtime" / "session_s2" / "raw_files" / "task_17"
    base.mkdir(parents=True)
    tree = base / "domain_trees.newick"
    tree.write_text("(a,b);\n")
    readme = base / "README.txt"
    readme.write_text("method notes\n")
    publisher = _publisher(tmp_path)

    report = publisher.publish_from_tool_result(
        session_id="s2",
        tool_name="deliverable_submit",
        raw_result={
            "success": True,
            "deliverable_submit": {
                "publish": True,
                "artifacts": [
                    {"path": str(tree), "module": "code"},
                    {"path": str(readme), "module": "docs"},
                ],
            },
        },
        summary="trees",
    )

    assert report is not None
    assert report.submit_artifacts_published == 2
    assert report.submit_artifacts_skipped == 0
    latest = tmp_path / "runtime" / "session_s2" / "deliverables" / "latest"
    assert (latest / "image_tabular" / "domain_trees.newick").exists()
    assert (latest / "docs" / "README.txt").exists()
    assert any("instead of 'code'" in w for w in report.warnings)


def test_unclassifiable_file_is_still_skipped(tmp_path: Path) -> None:
    base = tmp_path / "runtime" / "session_s3" / "raw_files"
    base.mkdir(parents=True)
    blob = base / "notes.xyz"
    blob.write_text("x")
    report = _publisher(tmp_path).publish_from_tool_result(
        session_id="s3",
        tool_name="deliverable_submit",
        raw_result={"success": True, "deliverable_submit": {"publish": True, "artifacts": [{"path": str(blob), "module": "code"}]}},
        summary="x",
    )
    assert report is not None
    assert report.submit_artifacts_published == 0
    assert "not an allowed code file" in report.warnings[0]
