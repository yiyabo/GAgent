"""Tests for DataProcessor.get_metadata JSON handling."""

from __future__ import annotations

import json
from pathlib import Path

from app.services.interpreter.metadata import DataProcessor


def test_scalar_valued_json_object_becomes_single_row(tmp_path: Path) -> None:
    """Tool result previews are JSON objects of scalars; pd.read_json raises
    'If using all scalar values, you must pass an index' on them. Metadata
    extraction must fall back to a single-row frame instead of failing."""
    path = tmp_path / "preview.json"
    path.write_text(
        json.dumps({"success": True, "tool": "phagescope_research", "count": 3}),
        encoding="utf-8",
    )

    meta = DataProcessor.get_metadata(str(path))

    assert meta.total_rows == 1
    assert meta.total_columns == 3
    assert {c.name for c in meta.columns} == {"success", "tool", "count"}


def test_records_json_still_reads_normally(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    path.write_text(
        json.dumps([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]),
        encoding="utf-8",
    )

    meta = DataProcessor.get_metadata(str(path))

    assert meta.total_rows == 2
    assert {c.name for c in meta.columns} == {"a", "b"}


def test_scalar_root_json_is_wrapped(tmp_path: Path) -> None:
    path = tmp_path / "scalar.json"
    path.write_text("42", encoding="utf-8")

    meta = DataProcessor.get_metadata(str(path))

    assert meta.total_rows == 1
    assert [c.name for c in meta.columns] == ["value"]


class _ExploitPayload:
    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def __reduce__(self):
        return (exec, (f"open({str(self.marker)!r}, 'w').write('pwned')",))


def test_pickle_files_are_rejected_without_unpickling(tmp_path: Path) -> None:
    import pickle

    import pytest

    marker = tmp_path / "pwned.txt"
    for suffix in (".pkl", ".pickle", ".PKL"):
        path = tmp_path / f"payload{suffix}"
        path.write_bytes(pickle.dumps(_ExploitPayload(marker)))

        with pytest.raises(ValueError, match="Pickle files"):
            DataProcessor.get_metadata(str(path))

    assert not marker.exists()


def test_npy_object_arrays_are_not_unpickled(tmp_path: Path) -> None:
    import numpy as np
    import pytest

    marker = tmp_path / "pwned.txt"
    path = tmp_path / "payload.npy"
    np.save(path, np.array([_ExploitPayload(marker)], dtype=object), allow_pickle=True)

    with pytest.raises(ValueError, match="without unpickling"):
        DataProcessor.get_metadata(str(path))

    assert not marker.exists()


def test_numeric_npy_still_reads(tmp_path: Path) -> None:
    import numpy as np

    path = tmp_path / "matrix.npy"
    np.save(path, np.arange(6, dtype=float).reshape(3, 2))

    meta = DataProcessor.get_metadata(str(path))

    assert meta.total_rows == 3
    assert meta.total_columns == 2
