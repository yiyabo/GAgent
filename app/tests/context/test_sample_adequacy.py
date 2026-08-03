"""Unit tests for clinical sample-size adequacy audit."""

from __future__ import annotations

import pandas as pd
import pytest

from app.services.context.sample_adequacy import (
    audit_from_dataset_metadata,
    compute_sample_adequacy,
    detect_outcome_column,
)


def _cols(names_uniques):
    out = []
    for name, uniq, dtype in names_uniques:
        out.append(
            {
                "name": name,
                "dtype": dtype,
                "unique_count": uniq,
                "null_count": 0,
                "sample_values": [],
            }
        )
    return out


def test_detect_outcome_prefers_named_binary():
    cols = _cols(
        [
            ("patient_id", 100, "int64"),
            ("age", 40, "float64"),
            ("sex", 2, "object"),
            ("术后胰腺炎", 2, "int64"),
        ]
    )
    assert detect_outcome_column(cols) == "术后胰腺炎"


def test_red_tier_small_n(tmp_path):
    path = tmp_path / "small.csv"
    df = pd.DataFrame(
        {
            "patient_id": list(range(30)),
            "age": list(range(30)),
            "bmi": list(range(30)),
            "outcome": [1] * 5 + [0] * 25,
        }
    )
    df.to_csv(path, index=False)
    meta = {
        "total_rows": 30,
        "total_columns": 4,
        "columns": [
            {"name": "patient_id", "dtype": "int64", "unique_count": 30, "null_count": 0},
            {"name": "age", "dtype": "int64", "unique_count": 30, "null_count": 0},
            {"name": "bmi", "dtype": "int64", "unique_count": 30, "null_count": 0},
            {"name": "outcome", "dtype": "int64", "unique_count": 2, "null_count": 0},
        ],
    }
    audit = audit_from_dataset_metadata(meta, file_path=str(path))
    assert audit.tier == "red"
    assert audit.n_positive == 5
    assert audit.outcome_column == "outcome"
    assert "multi-model" in audit.gate.lower() or "AUC" in audit.gate
    block = audit.to_prompt_block(language="zh")
    assert "SAMPLE ADEQUACY AUDIT" in block
    assert "tier: red" in block


def test_green_tier_large_n(tmp_path):
    path = tmp_path / "big.csv"
    n = 400
    n_pos = 80
    df = pd.DataFrame(
        {
            "id": list(range(n)),
            "x1": list(range(n)),
            "x2": list(range(n)),
            "label": [1] * n_pos + [0] * (n - n_pos),
        }
    )
    df.to_csv(path, index=False)
    meta = {
        "total_rows": n,
        "total_columns": 4,
        "columns": [
            {"name": "id", "dtype": "int64", "unique_count": n, "null_count": 0},
            {"name": "x1", "dtype": "int64", "unique_count": n, "null_count": 0},
            {"name": "x2", "dtype": "int64", "unique_count": n, "null_count": 0},
            {"name": "label", "dtype": "int64", "unique_count": 2, "null_count": 0},
        ],
    }
    audit = audit_from_dataset_metadata(meta, file_path=str(path), target_predictors=5)
    assert audit.tier == "green"
    assert audit.n_positive == n_pos
    assert audit.epv is not None and audit.epv >= 10
    assert audit.recommended_events_standard == 50


def test_recommended_n_scales_with_event_rate(tmp_path):
    path = tmp_path / "rate.csv"
    df = pd.DataFrame(
        {
            "a": range(100),
            "b": range(100),
            "c": range(100),
            "event": [1] * 10 + [0] * 90,
        }
    )
    df.to_csv(path, index=False)
    meta = {
        "total_rows": 100,
        "total_columns": 4,
        "columns": [
            {"name": "a", "dtype": "int64", "unique_count": 100, "null_count": 0},
            {"name": "b", "dtype": "int64", "unique_count": 100, "null_count": 0},
            {"name": "c", "dtype": "int64", "unique_count": 100, "null_count": 0},
            {"name": "event", "dtype": "int64", "unique_count": 2, "null_count": 0},
        ],
    }
    audit = audit_from_dataset_metadata(meta, file_path=str(path), target_predictors=5)
    assert audit.event_rate == pytest.approx(0.1)
    assert audit.recommended_n_at_observed_rate == 500
    assert audit.tier in {"red", "yellow"}


def test_no_outcome_uses_n_over_p():
    audit = compute_sample_adequacy(
        n_rows=20,
        n_columns=15,
        columns=[
            {"name": f"f{i}", "dtype": "float64", "unique_count": 20, "null_count": 0}
            for i in range(15)
        ],
    )
    assert audit.tier == "red"
    assert audit.outcome_column is None
