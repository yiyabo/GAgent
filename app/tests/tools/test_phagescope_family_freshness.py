"""Offline tests for the daily, read-only protein-family freshness check."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from app.routers.chat.tool_results import sanitize_tool_result, summarize_tool_result
from app.services.phagescope_data_service import family_freshness as ff
from app.services.phagescope_data_service import server as svc
from app.services.phagescope_data_service.family_fts_engine import FamilyFTSEngine
from scripts import build_phagescope_family_fts as build
from tool_box.tools_impl.phagescope_data import client as ps_client
from tool_box.tools_impl.phagescope_data import phagescope_data_handler


@pytest.fixture()
def snapshot(tmp_path: Path) -> tuple[Path, list[tuple[Any, ...]]]:
    root = tmp_path / "family-index"
    staging = root / "staging"
    staging.mkdir(parents=True)
    rows = [
        ("PF_ONE", 12, 12, "lysis;", "endolysin", 1, "P1", 2, False),
        ("PF_TWO", 4, 4, "assembly;", "tail fiber", 1, "P2", 1, False),
        ("PF_THREE", 1, 1, "", "portal protein", 0, "P3", 1, True),
    ]
    build.create_index(staging / "families.sqlite", [rows], expected_indexed=3)
    shutil.copyfile(build.DEFAULT_SYNONYMS, staging / "family_synonyms.json")
    files = {
        name: {
            "bytes": (staging / name).stat().st_size,
            "sha256": build.sha256_of(staging / name),
        }
        for name in ("families.sqlite", "family_synonyms.json")
    }
    (staging / "manifest.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "version": "v1",
                "built_at": "2026-11-14T00:00:00Z",
                "source_role": "phage_ro",
                "source_rows": 4,
                "indexed_rows": 3,
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    build.publish(staging, root / "v1", root / "current")
    return root / "current", rows


class SourceCursor:
    def __init__(self, source: SourceDatabase) -> None:
        self.source = source
        self.query_kind = ""
        self.selected: list[tuple[Any, ...]] = []

    def __enter__(self) -> SourceCursor:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        _ = (exc_type, exc_value, traceback)

    def execute(self, query: str, params: Any = None) -> None:
        self.query_kind = "counts" if "COUNT(*)" in query else "samples"
        if self.query_kind == "samples":
            self.selected = [
                self.source.rows[fid] for fid in params[0] if fid in self.source.rows
            ]

    def fetchone(self) -> tuple[int, int]:
        assert self.query_kind == "counts"
        return self.source.counts

    def fetchall(self) -> list[tuple[Any, ...]]:
        assert self.query_kind == "samples"
        return self.selected


class SourceDatabase:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = {row[0]: row for row in rows}
        self.counts: tuple[int, int] = (4, 3)

    def cursor(self) -> SourceCursor:
        return SourceCursor(self)


def test_daily_sample_rotates_across_source_rows() -> None:
    first = ff._sample_rowids(1000, 10, seed="2026-11-14")
    next_day = ff._sample_rowids(1000, 10, seed="2026-11-15")
    assert first != next_day
    assert all(1 + i * 100 <= rowid <= (i + 1) * 100 for i, rowid in enumerate(first))
    assert len(set(first)) == 10


def test_counts_and_samples_detect_drift_without_claiming_full_parity(
    snapshot: tuple[Path, list[tuple[Any, ...]]],
) -> None:
    root, rows = snapshot
    engine = FamilyFTSEngine(root)
    assert engine.available, engine.last_error
    source = SourceDatabase(rows)
    clean = ff.check_freshness(source, engine, sample_size=3)
    assert clean["status"] == "matched_counts_and_samples"
    assert clean["samples_checked"] == 3 and clean["sample_mismatch_count"] == 0
    assert "do not prove" in clean["limits"]

    source.rows["PF_TWO"] = (*rows[1][:4], "changed annotation", *rows[1][5:])
    changed = ff.check_freshness(source, engine, sample_size=3)
    assert changed["status"] == "drift_detected"
    assert changed["sample_mismatch_count"] == 1
    assert changed["sample_mismatch_examples"] == ["PF_TWO"]
    assert "changed annotation" not in json.dumps(changed)

    source.rows["PF_TWO"] = rows[1]
    source.counts = (5, 3)
    counts_changed = ff.check_freshness(source, engine, sample_size=3)
    assert (
        counts_changed["status"] == "drift_detected"
        and counts_changed["sample_mismatch_count"] == 0
    )


def test_status_endpoint_reads_cached_report_and_marks_old_checks_overdue(
    snapshot: tuple[Path, list[tuple[Any, ...]]],
) -> None:
    root, rows = snapshot

    def no_db() -> Any:
        raise AssertionError("The status endpoint must not query PostgreSQL")

    state = svc.DataServiceState(
        api_key="test-key", conn_provider=no_db, overview={}, family_index_root=root
    )
    endpoint = "/agent-api/v1/data/v2/proteins/family-index/status/"

    def status() -> dict[str, Any]:
        http, response = svc.dispatch_request(
            state, "GET", endpoint, {"X-API-Key": "test-key"}
        )
        assert http == 200
        return response

    assert status()["status"] == "not_checked"
    assert state.family_index is not None
    report = ff.check_freshness(SourceDatabase(rows), state.family_index, sample_size=3)
    ff.write_status(ff.status_path(root), report)
    assert status()["status"] == "matched_counts_and_samples"
    assert ff.status_path(root).stat().st_mode & 0o777 == 0o600

    old = {**report, "checked_at": "2020-01-01T00:00:00Z"}
    ff.write_status(ff.status_path(root), old)
    overdue = status()
    assert overdue["status"] == "check_overdue"
    assert overdue["last_check_status"] == "matched_counts_and_samples"
    ff.status_path(root).write_text("not-json", encoding="utf-8")
    assert status()["status"] == "invalid_report"


async def test_tool_exposes_last_check_and_chat_preserves_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHAGESCOPE_DATA_ENABLED", "1")
    report = {
        "status": "drift_detected",
        "checked_at": "2026-11-14T01:00:00Z",
        "samples_checked": 256,
    }

    async def fake_call(path: str, *, method: str, **_: Any) -> dict[str, Any]:
        assert path == "v2/proteins/family-index/status/" and method == "GET"
        return report

    monkeypatch.setattr(ps_client, "call_service", fake_call)
    result = await phagescope_data_handler(operation="family_index_status")
    compact = sanitize_tool_result("phagescope_data", result)
    assert compact["index_status"] == report
    assert "drift_detected" in summarize_tool_result("phagescope_data", compact)
