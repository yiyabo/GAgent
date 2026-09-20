from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from tool_box.tools_impl.code_executor import (
    _build_local_backend_result_payload,
    _promote_project_level_strays,
    _promote_results_to_unified_dir,
    _resolve_promoted_output_files,
)


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


def test_promote_results_to_unified_dir_falls_back_to_session_results(
    tmp_path: Path,
) -> None:
    from tool_box.tools_impl.code_executor import _promote_results_to_unified_dir

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    scratch_dir = session_dir / "_scratch" / "plan143_task7" / "run_xxx"
    scratch_dir.mkdir(parents=True)
    output_dir = session_dir / "raw_files" / "task_1" / "task_7"
    output_dir.mkdir(parents=True)

    # run workspace results/ is empty
    (scratch_dir / "results").mkdir()

    # session-level results/ has the actual file
    session_results = session_dir / "results"
    session_results.mkdir()
    report = session_results / "research_report.md"
    report.write_text("# Report\n", encoding="utf-8")

    promoted = _promote_results_to_unified_dir(
        scratch_dir=scratch_dir,
        output_dir=output_dir,
        subdirs=["results", "code", "data", "docs"],
        session_dir=session_dir,
    )

    assert len(promoted) == 1
    assert (output_dir / "research_report.md").exists()
    assert (output_dir / "research_report.md").read_text(encoding="utf-8") == "# Report\n"


def test_promote_results_prefers_run_workspace_over_session_fallback(
    tmp_path: Path,
) -> None:
    from tool_box.tools_impl.code_executor import _promote_results_to_unified_dir

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    scratch_dir = session_dir / "_scratch" / "plan143_task7" / "run_xxx"
    scratch_dir.mkdir(parents=True)
    output_dir = session_dir / "raw_files" / "task_1" / "task_7"
    output_dir.mkdir(parents=True)

    # run workspace has a file
    run_results = scratch_dir / "results"
    run_results.mkdir()
    (run_results / "run_report.md").write_text("from run", encoding="utf-8")

    # session-level also has a file (should be ignored because run has content)
    session_results = session_dir / "results"
    session_results.mkdir()
    (session_results / "session_report.md").write_text("from session", encoding="utf-8")

    promoted = _promote_results_to_unified_dir(
        scratch_dir=scratch_dir,
        output_dir=output_dir,
        subdirs=["results", "code", "data", "docs"],
        session_dir=session_dir,
    )

    # Only run workspace files promoted; session fallback not triggered
    assert len(promoted) == 1
    assert (output_dir / "run_report.md").exists()
    assert not (output_dir / "session_report.md").exists()


# --- Regression: double-rooted promotion of pi delegation artifacts ---
# Production symptom: artifact URLs shaped like
# ``raw_files/tmp/<run>/raw_files/tmp/<run>/x.png`` (prefix twice) → 404.
# Test data lives under a dedicated repo runtime/ sandbox (never /tmp, which
# production guard code treats as scratch) and is removed by the fixture.

_PROMOTE_SANDBOX = (
    Path(__file__).resolve().parents[3] / "runtime" / "test_promote_double_prefix_sandbox"
)


@pytest.fixture
def tmp_run_layout():
    run_id = "20260921_010203_000001_abcd1234"
    session_dir = _PROMOTE_SANDBOX / "session_pi"
    scratch_dir = session_dir / "_scratch" / "adhoc_task" / f"run_{run_id}"
    output_dir = session_dir / "raw_files" / "tmp" / run_id
    shutil.rmtree(_PROMOTE_SANDBOX, ignore_errors=True)
    scratch_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    yield SimpleNamespace(
        session_dir=session_dir,
        scratch_dir=scratch_dir,
        output_dir=output_dir,
        run_id=run_id,
    )
    shutil.rmtree(_PROMOTE_SANDBOX, ignore_errors=True)


def test_promote_results_does_not_double_root_mirrored_tmp_layout(
    tmp_run_layout: SimpleNamespace,
) -> None:
    # Delegated agent mirrored the session layout inside its scratch cwd:
    # <scratch>/raw_files/tmp/<run>/chart.png
    mirrored = (
        tmp_run_layout.scratch_dir / "raw_files" / "tmp" / tmp_run_layout.run_id
    )
    mirrored.mkdir(parents=True)
    (mirrored / "chart.png").write_bytes(b"\x89PNG")

    promoted = _promote_results_to_unified_dir(
        scratch_dir=tmp_run_layout.scratch_dir,
        output_dir=tmp_run_layout.output_dir,
        subdirs=["results", "code", "data", "docs"],
        session_dir=tmp_run_layout.session_dir,
    )

    expected_rel = f"raw_files/tmp/{tmp_run_layout.run_id}/chart.png"
    assert promoted == [expected_rel]
    assert (tmp_run_layout.output_dir / "chart.png").exists()
    assert not (tmp_run_layout.output_dir / "raw_files").exists()


def test_promote_results_single_layer_promotion_unchanged(
    tmp_run_layout: SimpleNamespace,
) -> None:
    run_results = tmp_run_layout.scratch_dir / "results"
    run_results.mkdir()
    (run_results / "chart.png").write_bytes(b"\x89PNG")
    run_code = tmp_run_layout.scratch_dir / "code"
    run_code.mkdir()
    (run_code / "script.py").write_text("print('hi')\n", encoding="utf-8")

    promoted = _promote_results_to_unified_dir(
        scratch_dir=tmp_run_layout.scratch_dir,
        output_dir=tmp_run_layout.output_dir,
        subdirs=["results", "code", "data", "docs"],
        session_dir=tmp_run_layout.session_dir,
    )

    prefix = f"raw_files/tmp/{tmp_run_layout.run_id}"
    assert sorted(promoted) == sorted(
        [f"{prefix}/chart.png", f"{prefix}/code/script.py"]
    )
    assert (tmp_run_layout.output_dir / "chart.png").exists()
    assert (tmp_run_layout.output_dir / "code" / "script.py").exists()
    assert not (tmp_run_layout.output_dir / "raw_files").exists()


def test_resolve_promoted_output_files_roots_against_session_dir(
    tmp_run_layout: SimpleNamespace,
) -> None:
    session_dir = tmp_run_layout.session_dir
    entries = [
        f"raw_files/tmp/{tmp_run_layout.run_id}/chart.png",
        "/abs/already.png",
    ]

    resolved = _resolve_promoted_output_files(entries, session_dir=session_dir)

    assert resolved[0] == str((session_dir / entries[0]).resolve())
    assert resolved[0].count("raw_files/tmp") == 1
    assert resolved[1] == str(Path("/abs/already.png").resolve())


def test_local_backend_payload_output_files_not_double_rooted(
    tmp_run_layout: SimpleNamespace,
) -> None:
    session_rel = f"raw_files/tmp/{tmp_run_layout.run_id}/chart.png"

    payload = _build_local_backend_result_payload(
        task="draw chart",
        local_result={"success": True, "stdout": "", "stderr": "", "exit_code": 0},
        resolved_plan_id=None,
        resolved_task_id=None,
        require_task_context=False,
        task_dir_base="adhoc_task",
        task_work_dir=tmp_run_layout.scratch_dir,
        task_root_dir=tmp_run_layout.scratch_dir.parent,
        run_id=tmp_run_layout.run_id,
        file_prefix=f"run_{tmp_run_layout.run_id}",
        task_subdirs=["results", "code", "data", "docs"],
        session_dir=tmp_run_layout.session_dir,
        execution_lane="cli",
        execution_lane_reason="test",
        log_path=None,
        normalized_allowed_tools=["Bash"],
        code_directory=None,
        primary_code_file=None,
        produced_files=[],
        verification_artifact_paths=[],
        contract_artifacts=[],
        session_artifact_paths=[],
        unified_output_dir=tmp_run_layout.output_dir,
        unified_promoted_files=[session_rel],
        effective_session_id="pi",
        ancestor_chain=None,
        execution_spec=None,
    )

    output_files = payload["output_files"]
    assert output_files == [
        str((tmp_run_layout.session_dir / session_rel).resolve())
    ]
    assert output_files[0].count("raw_files/tmp") == 1
    assert payload["output_location"]["files"] == [session_rel]
