"""deliverable_submit refuses CSV/TSV tables that default readers would miscount."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.deliverable_config import RESEARCH_MODULES, DeliverableSettings
from app.services.deliverables.publisher import DeliverablePublisher

_ROWS = (
    "requested_accession\tmatched_accession\tstatus\tgenome_length_bp\tcompleteness\n"
    "NC_001416.1\tNC_001416.1\tfound\t48502\tHigh-quality\n"
    "NC_001604.1\tNC_001604.1\tfound\t39937\tComplete\n"
    "NC_999999999.1\t\tnot_found\t\t\n"
)


def _publish(tmp_path: Path, files: dict[str, str], *, trusted: bool = False):
    base = tmp_path / "runtime" / "session_s1" / "raw_files"
    base.mkdir(parents=True)
    artifacts = []
    for name, text in files.items():
        path = base / name
        path.write_text(text)
        artifacts.append({"path": str(path), "module": "image_tabular", "trusted": trusted})
    settings = DeliverableSettings(
        enabled=True,
        default_template="research",
        show_draft=False,
        history_max=1,
        single_version_only=True,
        modules=RESEARCH_MODULES,
        basename_conflict_strategy="error",
    )
    publisher = DeliverablePublisher(settings=settings, project_root=tmp_path, runtime_dir=tmp_path / "runtime")
    report = publisher.publish_from_tool_result(
        session_id="s1",
        tool_name="deliverable_submit",
        raw_result={"success": True, "deliverable_submit": {"publish": True, "artifacts": artifacts}},
        summary="audit",
    )
    assert report is not None
    return report, tmp_path / "runtime" / "session_s1" / "deliverables" / "latest" / "image_tabular"


def test_trailing_comment_lines_are_not_published(tmp_path: Path) -> None:
    text = _ROWS + "# data_snapshot\t2026-09-11\n# lookup_method\tread-only catalog"
    report, out_dir = _publish(tmp_path, {"accession_audit.tsv": text})

    assert report.submit_artifacts_published == 0
    assert report.submit_artifacts_skipped == 1
    assert "accession_audit.tsv does not parse as plain rows" in report.warnings[0]
    assert "line 5, 6" in report.warnings[0]
    assert "accession_audit.metadata.json" in report.warnings[0]
    assert "skipped" in report.submit_summary()
    assert not (out_dir / "accession_audit.tsv").exists()


def test_ragged_csv_is_not_published(tmp_path: Path) -> None:
    report, _ = _publish(tmp_path, {"t.csv": "a,b,c\n1,2,3\nsnapshot,2026-09-11\n"})

    assert report.submit_artifacts_published == 0
    assert "3-column header at line 3" in report.warnings[0]


def test_plain_table_publishes_and_parses_to_its_rows(tmp_path: Path) -> None:
    pd = pytest.importorskip("pandas")
    text = _ROWS.replace("\tcompleteness\n", "\tcompleteness\tdata_snapshot\n", 1)
    text = "\n".join(line + "\t2026-09-11" if i else line for i, line in enumerate(text.splitlines(), 0)) + "\n"
    report, out_dir = _publish(tmp_path, {"accession_audit.tsv": text, "q.tsv": "#query\ttarget\na\tb\n\n"})

    assert report.submit_artifacts_published == 2, report.warnings
    frame = pd.read_csv(out_dir / "accession_audit.tsv", sep="\t")
    assert list(frame["status"]) == ["found", "found", "not_found"]
    assert frame["genome_length_bp"].isna().tolist() == [False, False, True]
    assert set(frame["data_snapshot"]) == {"2026-09-11"}


def test_trusted_plan_outputs_are_not_checked(tmp_path: Path) -> None:
    report, _ = _publish(tmp_path, {"hits.tsv": "# BLASTP\nq\ts\n"}, trusted=True)

    assert report.submit_artifacts_published == 1
