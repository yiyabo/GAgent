from __future__ import annotations

from pathlib import Path

import pytest

from tool_box.tools_impl.code_executor import _promote_project_level_strays


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    results = tmp_path / "results"
    results.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    return tmp_path


@pytest.fixture
def unified_output_dir(tmp_path: Path) -> Path:
    out = tmp_path / "raw_files" / "task_1"
    out.mkdir(parents=True)
    return out


def _make_contract_artifact(path: str, exists: bool = True) -> dict:
    return {"path": path, "exists": exists}


def test_promote_strays_noop_when_no_artifacts(
    project_root: Path, unified_output_dir: Path
) -> None:
    promoted = _promote_project_level_strays(
        contract_artifacts=[],
        unified_output_dir=unified_output_dir,
        project_root=project_root,
    )
    assert promoted == []


def test_promote_strays_noop_when_no_unified_dir(project_root: Path) -> None:
    promoted = _promote_project_level_strays(
        contract_artifacts=[_make_contract_artifact(str(project_root / "results" / "foo.csv"))],
        unified_output_dir=None,
        project_root=project_root,
    )
    assert promoted == []


def test_promote_strays_copies_from_results(
    project_root: Path, unified_output_dir: Path
) -> None:
    source = project_root / "results" / "report.csv"
    source.write_text("data")

    promoted = _promote_project_level_strays(
        contract_artifacts=[_make_contract_artifact(str(source))],
        unified_output_dir=unified_output_dir,
        project_root=project_root,
    )

    assert len(promoted) == 1
    assert (unified_output_dir / "report.csv").exists()
    assert (unified_output_dir / "report.csv").read_text() == "data"


def test_promote_strays_copies_from_output(
    project_root: Path, unified_output_dir: Path
) -> None:
    source = project_root / "output" / "analysis.csv"
    source.write_text("analysis data")

    promoted = _promote_project_level_strays(
        contract_artifacts=[_make_contract_artifact(str(source))],
        unified_output_dir=unified_output_dir,
        project_root=project_root,
    )

    assert len(promoted) == 1
    assert (unified_output_dir / "analysis.csv").exists()


def test_promote_strays_preserves_subdirectory(
    project_root: Path, unified_output_dir: Path
) -> None:
    subdir = project_root / "results" / "gvhd_model"
    subdir.mkdir()
    source = subdir / "metrics.json"
    source.write_text("{}")

    promoted = _promote_project_level_strays(
        contract_artifacts=[_make_contract_artifact(str(source))],
        unified_output_dir=unified_output_dir,
        project_root=project_root,
    )

    assert len(promoted) == 1
    assert (unified_output_dir / "gvhd_model" / "metrics.json").exists()


def test_promote_strays_skips_non_results_output(
    project_root: Path, unified_output_dir: Path
) -> None:
    other = project_root / "data" / "input.csv"
    other.parent.mkdir(exist_ok=True)
    other.write_text("data")

    promoted = _promote_project_level_strays(
        contract_artifacts=[_make_contract_artifact(str(other))],
        unified_output_dir=unified_output_dir,
        project_root=project_root,
    )

    assert promoted == []


def test_promote_strays_skips_nonexistent_files(
    project_root: Path, unified_output_dir: Path
) -> None:
    promoted = _promote_project_level_strays(
        contract_artifacts=[_make_contract_artifact(str(project_root / "results" / "missing.csv"))],
        unified_output_dir=unified_output_dir,
        project_root=project_root,
    )

    assert promoted == []


def test_promote_strays_skips_already_in_unified(
    project_root: Path, unified_output_dir: Path
) -> None:
    already_there = unified_output_dir / "already.csv"
    already_there.write_text("data")

    promoted = _promote_project_level_strays(
        contract_artifacts=[_make_contract_artifact(str(already_there))],
        unified_output_dir=unified_output_dir,
        project_root=project_root,
    )

    assert promoted == []


def test_promote_strays_skips_non_existent_artifact(
    project_root: Path, unified_output_dir: Path
) -> None:
    promoted = _promote_project_level_strays(
        contract_artifacts=[_make_contract_artifact(str(project_root / "results" / "foo.csv"), exists=False)],
        unified_output_dir=unified_output_dir,
        project_root=project_root,
    )

    assert promoted == []


def test_promote_strays_multiple_files(
    project_root: Path, unified_output_dir: Path
) -> None:
    f1 = project_root / "results" / "table1.csv"
    f1.write_text("table1")
    f2 = project_root / "results" / "km_curve.png"
    f2.write_bytes(b"\x89PNG")
    f3 = project_root / "output" / "report.md"
    f3.write_text("# Report")

    promoted = _promote_project_level_strays(
        contract_artifacts=[
            _make_contract_artifact(str(f1)),
            _make_contract_artifact(str(f2)),
            _make_contract_artifact(str(f3)),
        ],
        unified_output_dir=unified_output_dir,
        project_root=project_root,
    )

    assert len(promoted) == 3
    assert (unified_output_dir / "table1.csv").exists()
    assert (unified_output_dir / "km_curve.png").exists()
    assert (unified_output_dir / "report.md").exists()
