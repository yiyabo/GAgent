from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import asyncio

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_deeppl_phagescope_paper_assets.py"
SPEC = importlib.util.spec_from_file_location("paper_assets_pipeline", SCRIPT_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_normalize_lifecycle_label_maps_supported_values() -> None:
    assert module.normalize_lifecycle_label("Lysogenic") == "temperate"
    assert module.normalize_lifecycle_label("Temperate phage") == "temperate"
    assert module.normalize_lifecycle_label("virulent") == "virulent"
    assert module.normalize_lifecycle_label("lytic lifecycle") == "virulent"


def test_normalize_lifecycle_label_rejects_unknown_values() -> None:
    with pytest.raises(module.PipelineError):
        module.normalize_lifecycle_label("unknown")


def test_compute_metrics_returns_expected_percentages() -> None:
    rows = [
        {"true_label": "temperate", "deeppl_label": "temperate"},
        {"true_label": "temperate", "deeppl_label": "virulent"},
        {"true_label": "virulent", "deeppl_label": "virulent"},
        {"true_label": "virulent", "deeppl_label": "temperate"},
    ]

    metrics = module.compute_metrics(rows)

    assert metrics == {
        "n_total": 4,
        "tp": 1,
        "tn": 1,
        "fp": 1,
        "fn": 1,
        "accuracy": 50.0,
        "sensitivity": 50.0,
        "specificity": 50.0,
        "precision": 50.0,
        "f1": 0.5,
        "mcc": 0.0,
    }


def test_prepare_deeppl_sequence_replaces_small_ambiguous_set_deterministically() -> None:
    cleaned_a, raw_len_a, removed_n_a, replaced_a = module.prepare_deeppl_sequence(
        ("A" * 110) + "RYSWKMNN",
        accession="ACC001",
    )
    cleaned_b, raw_len_b, removed_n_b, replaced_b = module.prepare_deeppl_sequence(
        ("A" * 110) + "RYSWKMNN",
        accession="ACC001",
    )

    assert cleaned_a == cleaned_b
    assert raw_len_a == raw_len_b == 118
    assert removed_n_a == removed_n_b == 2
    assert replaced_a == replaced_b == 6
    assert set(cleaned_a) <= set("ACTG")


def test_prepare_deeppl_sequence_rejects_too_many_ambiguous_bases() -> None:
    with pytest.raises(module.PipelineError):
        module.prepare_deeppl_sequence(("A" * 120) + ("R" * 11), accession="ACC002")


def test_select_validation_subset_balances_and_sorts_by_accession() -> None:
    benchmark_rows = [
        {"accession": "T003", "true_label": "temperate", "deeppl_label": "temperate", "positive_window_fraction": 0.9},
        {"accession": "T001", "true_label": "temperate", "deeppl_label": "temperate", "positive_window_fraction": 0.8},
        {"accession": "T002", "true_label": "temperate", "deeppl_label": "virulent", "positive_window_fraction": 0.1},
        {"accession": "V003", "true_label": "virulent", "deeppl_label": "virulent", "positive_window_fraction": 0.2},
        {"accession": "V001", "true_label": "virulent", "deeppl_label": "virulent", "positive_window_fraction": 0.3},
        {"accession": "V002", "true_label": "virulent", "deeppl_label": "temperate", "positive_window_fraction": 0.7},
    ]

    subset = module.select_validation_subset(benchmark_rows, per_class=2)

    assert [row["sample_id"] for row in subset] == ["T001", "T002", "V001", "V002"]
    assert all(Path(row["input_fasta"]).name == f"{row['accession']}.fasta" for row in subset)


def test_select_validation_subset_requires_enough_samples_per_class() -> None:
    benchmark_rows = [
        {"accession": "T001", "true_label": "temperate", "deeppl_label": "temperate", "positive_window_fraction": 0.8},
        {"accession": "V001", "true_label": "virulent", "deeppl_label": "virulent", "positive_window_fraction": 0.3},
    ]

    with pytest.raises(module.PipelineError):
        module.select_validation_subset(benchmark_rows, per_class=2)


def test_count_keyword_hits_reads_annotation_artifacts(tmp_path: Path) -> None:
    annotation_dir = tmp_path / "annotation"
    annotation_dir.mkdir()
    (annotation_dir / "proteins.tsv").write_text(
        "integrase\trepressor\nlysogenic prophage\nXis excisionase\n",
        encoding="utf-8",
    )

    counts = module.count_keyword_hits(tmp_path)

    assert counts["integrase"] == 1
    assert counts["repressor"] == 1
    assert counts["excisionase"] >= 1
    assert counts["lysogeny"] >= 2


def test_parse_deeppl_raw_rows_reports_missing_accessions() -> None:
    truth_by_accession = {
        "ACC001": {"lifecycle_normalized": "temperate"},
        "ACC002": {"lifecycle_normalized": "virulent"},
    }
    raw_rows = [
        {"FASTA File": "ACC001.fasta", "Probability": "0.42", "Prediction": "Lysogenic"},
    ]

    benchmark_rows, missing = module.parse_deeppl_raw_rows(raw_rows, truth_by_accession)

    assert [row["accession"] for row in benchmark_rows] == ["ACC001"]
    assert benchmark_rows[0]["deeppl_label"] == "temperate"
    assert missing == ["ACC002"]


def test_merge_deeppl_raw_rows_overwrites_and_sorts() -> None:
    merged = module.merge_deeppl_raw_rows(
        [
            {"FASTA File": "B.fasta", "Probability": "0.1", "Prediction": "Lytic"},
            {"FASTA File": "A.fasta", "Probability": "0.2", "Prediction": "Lytic"},
        ],
        [
            {"FASTA File": "B.fasta", "Probability": "0.9", "Prediction": "Lysogenic"},
            {"FASTA File": "C.fasta", "Probability": "0.3", "Prediction": "Lytic"},
        ],
    )

    assert [row["FASTA File"] for row in merged] == ["A.fasta", "B.fasta", "C.fasta"]
    assert next(row for row in merged if row["FASTA File"] == "B.fasta")["Prediction"] == "Lysogenic"


def test_should_accept_partial_deeppl_benchmark_uses_370_threshold() -> None:
    assert module.should_accept_partial_deeppl_benchmark(370) is True
    assert module.should_accept_partial_deeppl_benchmark(369) is False


def test_run_phagescope_validation_creates_empty_completed_samples_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = module.build_paths(tmp_path / "paper_assets")
    module.init_layout(paths)
    manifest = {}
    subset_rows = [
        {
            "sample_id": "ACC001",
            "accession": "ACC001",
            "true_label": "temperate",
            "deeppl_label": "temperate",
            "input_fasta": str(paths.test_set_fasta_dir / "ACC001.fasta"),
        }
    ]

    async def fake_submit_phagescope_sample(sample: dict) -> dict:
        return {"success": True, "data": {"data": {"taskid": 12345}}}

    async def fake_task_detail_phagescope(taskid: str) -> dict:
        return {"success": True, "data": {"status": "Running", "data": []}}

    monkeypatch.setattr(module, "submit_phagescope_sample", fake_submit_phagescope_sample)
    monkeypatch.setattr(module, "task_detail_phagescope", fake_task_detail_phagescope)

    completed, registry = asyncio.run(
        module.run_phagescope_validation(
            paths,
            manifest,
            subset_rows,
            poll_interval=1.0,
            poll_timeout=0.0,
        )
    )

    completed_path = paths.phagescope_dir / "completed_samples.tsv"
    assert completed == []
    assert "ACC001" in registry
    assert completed_path.exists() is True
    assert completed_path.read_text(encoding="utf-8").strip() == (
        "sample_id\taccession\ttaskid\tstatus\tphagescope_lifestyle_raw\tphagescope_lifestyle\tquality_summary\thost\tartifact_root"
    )


def test_extract_completed_sample_falls_back_to_module_outputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "save_all" / "ACC001"
    (output_dir / "metadata").mkdir(parents=True)
    (output_dir / "annotation").mkdir(parents=True)
    (output_dir / "metadata" / "phage_info.json").write_text(
        '{"results": [{"Acession_ID": "ACC001"}]}',
        encoding="utf-8",
    )
    (output_dir / "annotation" / "module_lifestyle.json").write_text(
        '{"results": [{"phageid": "ACC001", "lifestyle": "virulent"}]}',
        encoding="utf-8",
    )
    (output_dir / "annotation" / "module_host.json").write_text(
        '{"results": [{"accesion_id": "ACC001", "host": "Escherichia coli"}]}',
        encoding="utf-8",
    )

    row = module.extract_completed_sample(
        {"sample_id": "ACC001", "accession": "ACC001"},
        {"taskid": "123", "output_directory": str(output_dir)},
    )

    assert row["phagescope_lifestyle_raw"] == "virulent"
    assert row["phagescope_lifestyle"] == "virulent"
    assert row["host"] == "Escherichia coli"


def test_build_integration_outputs_tracks_omitted_samples(tmp_path: Path) -> None:
    paths = module.build_paths(tmp_path / "paper_assets")
    module.init_layout(paths)
    manifest = {}
    benchmark_rows = [
        {
            "accession": "ACC001",
            "deeppl_label": "temperate",
            "positive_window_fraction": 0.8,
            "window_score_threshold": 0.9,
            "positive_window_fraction_threshold": 0.016,
            "prediction_source": "test",
        },
        {
            "accession": "ACC002",
            "deeppl_label": "virulent",
            "positive_window_fraction": 0.1,
            "window_score_threshold": 0.9,
            "positive_window_fraction_threshold": 0.016,
            "prediction_source": "test",
        },
    ]
    completed_samples = [
        {
            "sample_id": "ACC001",
            "accession": "ACC001",
            "phagescope_lifestyle": "temperate",
            "artifact_root": str(paths.root),
        },
        {
            "sample_id": "ACC002",
            "accession": "ACC002",
            "phagescope_lifestyle": "",
            "artifact_root": str(paths.root),
        },
    ]

    comparison_rows = module.build_integration_outputs(paths, manifest, benchmark_rows, completed_samples)

    assert len(comparison_rows) == 1
    omitted = module.read_tsv(paths.integration_dir / "omitted_samples.tsv")
    assert omitted == [{"sample_id": "ACC002", "accession": "ACC002", "reason": "missing_phagescope_lifestyle"}]
