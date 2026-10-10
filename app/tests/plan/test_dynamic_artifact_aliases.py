"""Dynamic artifact aliases map to real filenames (LOCAL_INFRA §119).

Plan #183 (2026-10-10) finished every task with zero registered products: the
decomposer declared no ``publishes``, the text fallback produced aliases such
as ``output.report.md`` that never passed the alias grammar, and a dynamic
alias like ``ns.search_corpus_jsonl`` only matched a file literally named
``search_corpus_jsonl``. These tests pin the repaired contract plumbing.
"""

from __future__ import annotations

from pathlib import Path

from app.services.plans.artifact_contracts import (
    DYNAMIC_FILE_EXTENSIONS,
    _dynamic_artifact_spec,
    _extract_explicit_aliases,
    candidate_filenames_for_alias,
    canonical_artifact_path,
    dynamic_artifact_alias,
    find_candidate_source_for_alias,
    is_artifact_alias,
)


def test_dynamic_file_slot_maps_to_a_real_extension() -> None:
    assert _dynamic_artifact_spec("lit_review.search_corpus_jsonl") == ("lit_review", "search_corpus.jsonl")
    assert canonical_artifact_path(7, "lit_review.search_corpus_jsonl").name == "search_corpus.jsonl"
    # Directory slots keep their canonical directory names.
    assert _dynamic_artifact_spec("lit_review.evidence_tables") == ("lit_review", "evidence_tables")


def test_candidate_filenames_cover_both_the_file_and_the_raw_slot() -> None:
    names = candidate_filenames_for_alias("lit_review.search_corpus_jsonl")
    assert "search_corpus.jsonl" in names
    assert "search_corpus_jsonl" in names


def test_naturally_named_file_matches_its_dynamic_alias(tmp_path: Path) -> None:
    produced = tmp_path / "results" / "search_corpus.jsonl"
    produced.parent.mkdir(parents=True)
    produced.write_text('{"pmid": "1"}\n', encoding="utf-8")

    assert find_candidate_source_for_alias(
        alias="lit_review.search_corpus_jsonl",
        candidate_paths=[str(produced)],
    ) == str(produced)
    # A reported parent directory is searched as well.
    assert find_candidate_source_for_alias(
        alias="lit_review.search_corpus_jsonl",
        candidate_paths=[str(tmp_path / "results")],
    ) == str(produced)


def test_figure_and_table_extensions_are_registrable() -> None:
    for ext in ("png", "svg", "pdf", "xlsx", "tsv"):
        assert ext in DYNAMIC_FILE_EXTENSIONS
    assert is_artifact_alias("lit_review.fig1_schematic_png")
    assert is_artifact_alias("lit_review.extraction_matrix_xlsx")


def test_dynamic_artifact_alias_builds_registrable_aliases_only() -> None:
    assert dynamic_artifact_alias("output", "results/Extraction Matrix.csv") == "output.extraction_matrix_csv"
    assert dynamic_artifact_alias("output", "figures/Fig 1-Schematic.PNG") == "output.fig_1_schematic_png"
    assert dynamic_artifact_alias("output", "1st_pass.md") == "output.f_1st_pass_md"
    assert dynamic_artifact_alias("output", "bin/tool.exe") is None
    assert dynamic_artifact_alias("output", "README") is None
    assert dynamic_artifact_alias("", "a.md") is None


def test_legacy_double_dot_aliases_are_still_dropped_but_dynamic_ones_survive() -> None:
    assert _extract_explicit_aliases(["lit_review.search_corpus_jsonl", "output.report.md", "general.evidence_md"]) == [
        "lit_review.search_corpus_jsonl",
        "general.evidence_md",
    ]
