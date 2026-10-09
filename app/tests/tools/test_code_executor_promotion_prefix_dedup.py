"""Prefix-overlap de-duplication for artifact promotion.

Production symptom (pi delegation): promoted artifacts landed under
``<session>/raw_files/tmp/<run>/raw_files/tmp/<run>/x.png`` because the
promotion target directory already carried the leading segments of the
relative path, and the legacy de-duplication only worked when the output dir
and the session dir resolved to the same root.  Test data lives under a
dedicated repo ``runtime/`` sandbox (never ``/tmp``) and is removed by the
fixture.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from tool_box.tools_impl.code_executor import (
    _collapse_rooted_rel_path,
    _dedupe_output_dir_suffix_overlap,
    _promote_project_level_strays,
    _promote_results_to_unified_dir,
)

_SANDBOX = (
    Path(__file__).resolve().parents[3] / "runtime" / "test_promotion_prefix_dedup_sandbox"
)
_RUN_A = "20261001_101010_000001_aaaaaaaa"
_TASK_SUBDIRS = ["results", "code", "data", "docs"]


@pytest.fixture
def promote_layout() -> SimpleNamespace:
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    # Session root that actually owns the output directory.
    session_dir = _SANDBOX / "runtime_root" / "session_pi"
    tmp_output_dir = session_dir / "raw_files" / "tmp"
    output_dir = tmp_output_dir / _RUN_A
    scratch_dir = session_dir / "_scratch" / "adhoc_task" / f"run_{_RUN_A}"
    # Second root that merely spells the same session id (the /app vs /data
    # bind-mount shape that makes ``relative_to`` fail).
    other_session_dir = _SANDBOX / "other_runtime_root" / "session_pi"
    project_root = _SANDBOX / "project"
    for path in (output_dir, scratch_dir, other_session_dir, project_root / "results", project_root / "output"):
        path.mkdir(parents=True, exist_ok=True)
    yield SimpleNamespace(
        sandbox=_SANDBOX,
        session_dir=session_dir,
        other_session_dir=other_session_dir,
        tmp_output_dir=tmp_output_dir,
        output_dir=output_dir,
        scratch_dir=scratch_dir,
        project_root=project_root,
        run_id=_RUN_A,
    )
    shutil.rmtree(_SANDBOX, ignore_errors=True)


def _artifact(path: Path) -> dict:
    return {"path": str(path), "exists": True}


# --- _collapse_rooted_rel_path: production shapes -------------------------


def test_collapse_tmp_output_dir_production_shape(promote_layout: SimpleNamespace) -> None:
    collapsed = _collapse_rooted_rel_path(
        rel=Path("raw_files/tmp/x.png"),
        output_dir=promote_layout.tmp_output_dir,
        session_dir=promote_layout.session_dir,
    )
    assert collapsed == Path("x.png")


def test_collapse_run_output_dir_production_shape(promote_layout: SimpleNamespace) -> None:
    collapsed = _collapse_rooted_rel_path(
        rel=Path(f"raw_files/tmp/{promote_layout.run_id}/x.png"),
        output_dir=promote_layout.output_dir,
        session_dir=promote_layout.session_dir,
    )
    assert collapsed == Path("x.png")


def test_collapse_dedupes_when_session_roots_differ(promote_layout: SimpleNamespace) -> None:
    # output_dir and session_dir resolve to different roots -> the legacy
    # whole-prefix ``relative_to`` raises and used to return rel untouched.
    collapsed = _collapse_rooted_rel_path(
        rel=Path(f"raw_files/tmp/{promote_layout.run_id}/x.png"),
        output_dir=promote_layout.output_dir,
        session_dir=promote_layout.other_session_dir,
    )
    assert collapsed == Path("x.png")


def test_collapse_dedupes_partial_mirror_when_session_roots_differ(
    promote_layout: SimpleNamespace,
) -> None:
    collapsed = _collapse_rooted_rel_path(
        rel=Path("raw_files/tmp/x.png"),
        output_dir=promote_layout.tmp_output_dir,
        session_dir=promote_layout.other_session_dir,
    )
    assert collapsed == Path("x.png")


def test_collapse_keeps_foreign_run_segment(promote_layout: SimpleNamespace) -> None:
    # Pins existing block-search semantics: a run id that is not the output
    # dir's own run id stays in the relative path; the new backstop must not
    # strip it further.
    collapsed = _collapse_rooted_rel_path(
        rel=Path("raw_files/tmp/run_B/x.png"),
        output_dir=promote_layout.output_dir,
        session_dir=promote_layout.session_dir,
    )
    assert collapsed == Path("run_B/x.png")


def test_collapse_returns_unrelated_rel_verbatim(promote_layout: SimpleNamespace) -> None:
    for session_dir in (promote_layout.session_dir, promote_layout.other_session_dir):
        assert _collapse_rooted_rel_path(
            rel=Path("results/y.csv"),
            output_dir=promote_layout.tmp_output_dir,
            session_dir=session_dir,
        ) == Path("results/y.csv")


def test_collapse_is_idempotent_for_already_correct_rel(promote_layout: SimpleNamespace) -> None:
    for session_dir in (promote_layout.session_dir, promote_layout.other_session_dir):
        assert _collapse_rooted_rel_path(
            rel=Path("x.png"),
            output_dir=promote_layout.output_dir,
            session_dir=session_dir,
        ) == Path("x.png")


def test_dedupe_helper_is_idempotent_for_mirrored_rel(promote_layout: SimpleNamespace) -> None:
    once = _dedupe_output_dir_suffix_overlap(
        rel=Path(f"raw_files/tmp/{promote_layout.run_id}/x.png"),
        output_dir=promote_layout.output_dir,
    )
    assert once == Path("x.png")
    assert _dedupe_output_dir_suffix_overlap(rel=once, output_dir=promote_layout.output_dir) == once


def test_dedupe_helper_leaves_interior_segments_alone(promote_layout: SimpleNamespace) -> None:
    # Only the leading overlap is removed; a nested mirror later in the path is
    # not rewritten by this backstop.
    assert _dedupe_output_dir_suffix_overlap(
        rel=Path(f"raw_files/tmp/{promote_layout.run_id}/raw_files/tmp/{promote_layout.run_id}/x.png"),
        output_dir=promote_layout.output_dir,
    ) == Path(f"raw_files/tmp/{promote_layout.run_id}/x.png")


# --- _promote_project_level_strays ---------------------------------------


def test_promote_strays_does_not_double_root_mirrored_output_dir(
    promote_layout: SimpleNamespace,
) -> None:
    mirrored = promote_layout.project_root / "results" / "raw_files" / "tmp"
    mirrored.mkdir(parents=True, exist_ok=True)
    source = mirrored / "HIV_report.md"
    source.write_text("# Report\n", encoding="utf-8")

    promoted = _promote_project_level_strays(
        contract_artifacts=[_artifact(source)],
        unified_output_dir=promote_layout.tmp_output_dir,
        project_root=promote_layout.project_root,
    )

    dest = promote_layout.tmp_output_dir / "HIV_report.md"
    assert promoted == [str(dest)]
    assert dest.read_text(encoding="utf-8") == "# Report\n"
    assert not (promote_layout.tmp_output_dir / "raw_files").exists()


def test_promote_strays_keeps_non_overlapping_subdirectory(
    promote_layout: SimpleNamespace,
) -> None:
    subdir = promote_layout.project_root / "results" / "gvhd_model"
    subdir.mkdir(parents=True, exist_ok=True)
    source = subdir / "metrics.json"
    source.write_text("{}", encoding="utf-8")

    _promote_project_level_strays(
        contract_artifacts=[_artifact(source)],
        unified_output_dir=promote_layout.tmp_output_dir,
        project_root=promote_layout.project_root,
    )

    assert (promote_layout.tmp_output_dir / "gvhd_model" / "metrics.json").exists()


# --- _promote_results_to_unified_dir end to end ---------------------------


def _mirror_file(layout: SimpleNamespace, rel: str) -> Path:
    mirrored = layout.scratch_dir / rel
    mirrored.parent.mkdir(parents=True, exist_ok=True)
    mirrored.write_bytes(b"\x89PNG")
    return mirrored


def test_promote_results_end_to_end_same_root(promote_layout: SimpleNamespace) -> None:
    _mirror_file(promote_layout, f"raw_files/tmp/{promote_layout.run_id}/chart.png")

    promoted = _promote_results_to_unified_dir(
        scratch_dir=promote_layout.scratch_dir,
        output_dir=promote_layout.output_dir,
        subdirs=_TASK_SUBDIRS,
        session_dir=promote_layout.session_dir,
    )

    assert promoted == [f"raw_files/tmp/{promote_layout.run_id}/chart.png"]
    assert (promote_layout.output_dir / "chart.png").exists()
    assert not (promote_layout.output_dir / "raw_files").exists()


def test_promote_results_end_to_end_when_session_roots_differ(
    promote_layout: SimpleNamespace,
) -> None:
    _mirror_file(promote_layout, f"raw_files/tmp/{promote_layout.run_id}/chart.png")

    promoted = _promote_results_to_unified_dir(
        scratch_dir=promote_layout.scratch_dir,
        output_dir=promote_layout.output_dir,
        subdirs=_TASK_SUBDIRS,
        session_dir=promote_layout.other_session_dir,
    )

    dest = promote_layout.output_dir / "chart.png"
    assert promoted == [str(dest)]
    assert dest.exists()
    assert not (promote_layout.output_dir / "raw_files").exists()
