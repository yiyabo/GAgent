from __future__ import annotations

from pathlib import Path

import pytest

from tool_box.tools_impl.code_executor import (
    _reconcile_deliverables,
    _build_search_and_generate_prompt,
)


def _make_criteria(*file_names: str) -> dict:
    checks = [{"type": "file_exists", "path": name} for name in file_names]
    return {"category": "file_data", "blocking": True, "checks": checks}


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    results = tmp_path / "results"
    results.mkdir()
    return tmp_path


def test_reconcile_noop_when_no_criteria(task_dir: Path) -> None:
    report = _reconcile_deliverables(
        execution_spec={},
        task_work_dir=task_dir,
    )
    assert report == {"aligned": [], "missing": [], "already_ok": []}


def test_reconcile_already_ok(task_dir: Path) -> None:
    (task_dir / "results" / "foo.csv").write_text("data")
    report = _reconcile_deliverables(
        execution_spec={"acceptance_criteria": _make_criteria("foo.csv")},
        task_work_dir=task_dir,
    )
    assert report["already_ok"] == ["foo.csv"]
    assert report["aligned"] == []
    assert report["missing"] == []


def test_reconcile_strips_run_prefix(task_dir: Path) -> None:
    (task_dir / "results" / "run_20260531_124434_177941_0e92f7f4_tmb_tam_correlation.csv").write_text("data")
    report = _reconcile_deliverables(
        execution_spec={"acceptance_criteria": _make_criteria("tmb_tam_correlation.csv")},
        task_work_dir=task_dir,
    )
    assert len(report["aligned"]) == 1
    assert report["aligned"][0]["expected"] == "tmb_tam_correlation.csv"
    assert (task_dir / "results" / "tmb_tam_correlation.csv").is_symlink()


def test_reconcile_multiple_files(task_dir: Path) -> None:
    prefix = "run_20260531_124434_177941_0e92f7f4_"
    (task_dir / "results" / f"{prefix}tmb_tam_correlation.csv").write_text("a")
    (task_dir / "results" / f"{prefix}mutation_tam_association.csv").write_text("b")
    (task_dir / "results" / f"{prefix}hrd_tam_analysis.csv").write_text("c")
    (task_dir / "results" / f"{prefix}oncoplot_tam_annotated.png").write_bytes(b"\x89PNG")

    report = _reconcile_deliverables(
        execution_spec={"acceptance_criteria": _make_criteria(
            "tmb_tam_correlation.csv",
            "mutation_tam_association.csv",
            "hrd_tam_analysis.csv",
            "oncoplot_tam_annotated.png",
        )},
        task_work_dir=task_dir,
    )
    assert len(report["aligned"]) == 4
    assert report["missing"] == []
    for name in ("tmb_tam_correlation.csv", "mutation_tam_association.csv",
                 "hrd_tam_analysis.csv", "oncoplot_tam_annotated.png"):
        assert (task_dir / "results" / name).is_symlink()


def test_reconcile_reports_missing(task_dir: Path) -> None:
    (task_dir / "results" / "other_file.csv").write_text("data")
    report = _reconcile_deliverables(
        execution_spec={"acceptance_criteria": _make_criteria("expected_file.csv")},
        task_work_dir=task_dir,
    )
    assert report["missing"] == ["expected_file.csv"]
    assert report["aligned"] == []


def test_reconcile_creates_symlinks_in_unified_dir(task_dir: Path, tmp_path: Path) -> None:
    unified = tmp_path / "unified"
    unified.mkdir()
    (task_dir / "results" / "run_20260531_124434_177941_0e92f7f4_depmap_scores.csv").write_text("data")

    _reconcile_deliverables(
        execution_spec={"acceptance_criteria": _make_criteria("depmap_scores.csv")},
        task_work_dir=task_dir,
        unified_output_dir=unified,
    )
    assert (task_dir / "results" / "depmap_scores.csv").is_symlink()
    assert (unified / "depmap_scores.csv").is_symlink()


def test_reconcile_skips_glob_criteria(task_dir: Path) -> None:
    (task_dir / "results" / "run_20260531_124434_177941_0e92f7f4_data.csv").write_text("data")
    criteria = {"category": "file_data", "blocking": True, "checks": [
        {"type": "glob_nonempty", "glob": "*.csv"},
    ]}
    report = _reconcile_deliverables(
        execution_spec={"acceptance_criteria": criteria},
        task_work_dir=task_dir,
    )
    assert report["aligned"] == []
    assert report["missing"] == []


def test_reconcile_no_results_dir(tmp_path: Path) -> None:
    report = _reconcile_deliverables(
        execution_spec={"acceptance_criteria": _make_criteria("foo.csv")},
        task_work_dir=tmp_path,
    )
    assert report == {"aligned": [], "missing": [], "already_ok": []}


def test_build_search_and_generate_prompt(tmp_path: Path) -> None:
    prompt = _build_search_and_generate_prompt(
        missing_files=["foo.csv", "bar.json"],
        session_dir=tmp_path,
        execution_spec={
            "task_name": "Test Task",
            "task_instruction": "Do some analysis",
        },
    )
    assert "DELIVERABLE SEARCH TASK" in prompt
    assert "foo.csv" in prompt
    assert "bar.json" in prompt
    assert "Test Task" in prompt
    assert "Do some analysis" in prompt
    assert str(tmp_path) in prompt
    assert "SEARCH" in prompt
    assert "COPY" in prompt
    assert "Do NOT attempt to generate" in prompt


def test_build_search_and_generate_prompt_no_spec(tmp_path: Path) -> None:
    prompt = _build_search_and_generate_prompt(
        missing_files=["foo.csv"],
        session_dir=tmp_path,
        execution_spec=None,
    )
    assert "DELIVERABLE SEARCH TASK" in prompt
    assert "foo.csv" in prompt


def test_build_search_and_generate_prompt_timeout(tmp_path: Path) -> None:
    prompt = _build_search_and_generate_prompt(
        missing_files=["foo.csv"],
        session_dir=tmp_path,
        execution_spec={
            "task_name": "Test Task",
            "task_instruction": "Do some analysis",
        },
        is_timeout=True,
    )
    assert "DELIVERABLE RECOVERY TASK" in prompt
    assert "PREVIOUS EXECUTION TIMED OUT" in prompt
    assert "foo.csv" in prompt
    assert "RE-EXECUTE" in prompt
    assert "re-run the analysis" in prompt
