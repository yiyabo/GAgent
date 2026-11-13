"""Phase 1: cohort_report composite operation (service + handler).

One round-trip must return the exact cohort count, length stats, the four
facet tables and sample rows; save_tsv=true additionally persists the cohort
TSV into the session workspace so the model never re-pages just to export.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.services.phagescope_data_service import db_queries as dq
from tool_box.tools_impl.phagescope_data import client as ps_client
from tool_box.tools_impl.phagescope_data.handler import phagescope_data_handler


class _FakeCursor:
    def __init__(self, stats: tuple) -> None:
        self._stats = stats
        self.queries: list[str] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        return False

    def execute(self, stmt: str, params: list | None = None) -> None:
        self.queries.append(stmt)

    def fetchone(self) -> tuple:
        return self._stats


class _FakeConn:
    def __init__(self, stats: tuple = (512, 40000, 90000, 460105)) -> None:
        self._stats = stats

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._stats)


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHAGESCOPE_DATA_ENABLED", "1")


class TestCohortReportService:
    def test_assembles_count_stats_facets_and_samples(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dq, "_dataset_maps", lambda conn: ({1: "RefSeq"}, {"refseq": 1}))
        monkeypatch.setattr(dq, "_build_phage_where", lambda filters, ds: ([], ["Pseudomonas"], ["p.is_mapped = FALSE"]))
        facet_calls: list[str] = []

        def _fake_aggregate(conn: Any, *, group_by: str, **kwargs: Any) -> dict:
            facet_calls.append(group_by)
            return {"group_by": group_by, "groups": [{"key": "x", "count": 7}]}

        def _fake_query(conn: Any, filters: dict, **kwargs: Any) -> dict:
            return {"results": [{"id": 1, "accession": "NC_001416.1"}]}

        monkeypatch.setattr(dq, "aggregate_phages", _fake_aggregate)
        monkeypatch.setattr(dq, "query_phages", _fake_query)

        report = dq.cohort_report(_FakeConn(), {"host": "Pseudomonas"}, sample_limit=5)
        assert report["count"] == 512
        assert report["length"] == {"min": 40000, "avg": 90000, "max": 460105}
        assert facet_calls == ["dataset", "completeness", "lifestyle", "host_genus"]
        assert set(report["facets"]) == {"by_dataset", "by_completeness", "by_lifestyle", "top_host_genera"}
        assert report["facets"]["by_dataset"] == [{"key": "x", "count": 7}]
        assert report["sample_rows"] == [{"id": 1, "accession": "NC_001416.1"}]


class TestCohortReportHandler:
    async def test_handler_returns_service_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        async def _fake_call_service(path: str, *, method: str = "GET", body: dict | None = None, **kwargs: Any) -> dict:
            seen["path"] = path
            seen["body"] = body
            return {
                "count": 512,
                "length": {"min": 40000, "avg": 90000, "max": 460105},
                "facets": {"by_dataset": [{"key": "RefSeq", "count": 512}]},
                "sample_rows": [{"id": 1}],
            }

        monkeypatch.setattr(ps_client, "call_service", _fake_call_service)
        out = await phagescope_data_handler(
            operation="cohort_report", host="Pseudomonas", sample_size=3, sort_by="length_desc"
        )
        assert out["success"] is True
        assert out["operation"] == "cohort_report"
        assert out["count"] == 512
        assert out["filters"] == {"host": "Pseudomonas"}
        assert seen["path"] == "v2/phages/cohort_report/"
        assert seen["body"]["sample_limit"] == 3
        assert seen["body"]["sort_by"] == "length_desc"
        assert seen["body"]["filters"] == {"host": "Pseudomonas"}

    async def test_save_tsv_writes_cohort_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from tool_box.context import ToolContext

        monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))

        async def _fake_call_service(path: str, *, method: str = "GET", body: dict | None = None, **kwargs: Any) -> dict:
            if path == "v2/phages/cohort_report/":
                return {"count": 2, "length": {}, "facets": {}, "sample_rows": []}
            if path == "v2/phages/query/":
                assert body is not None
                assert body["export_mode"] is True
                return {
                    "results": [
                        {"id": 1, "Acession_ID": "NC_001416.1", "dataset": "RefSeq", "length": 48502},
                        {"id": 2, "Acession_ID": "NC_002000.1", "dataset": "RefSeq", "length": 40000},
                    ]
                }
            raise AssertionError(f"unexpected path {path}")

        monkeypatch.setattr(ps_client, "call_service", _fake_call_service)
        work_dir = tmp_path / "runtime" / "session_cohort"
        work_dir.mkdir(parents=True)
        ctx = ToolContext(session_id="session_cohort", work_dir=str(work_dir))
        out = await phagescope_data_handler(
            operation="cohort_report", host="Pseudomonas", save_tsv=True,
            output_subdir="cohort_out", tool_context=ctx,
        )
        assert out["success"] is True
        assert out["saved_rows"] == 2
        tsv = Path(out["output_directory"]) / "phages_cohort.tsv"
        assert tsv.is_file()
        content = tsv.read_text()
        assert "NC_001416.1" in content and "NC_002000.1" in content

    def test_cohort_report_is_in_operations(self) -> None:
        from tool_box.tools_impl.phagescope_data.schema import OPERATIONS

        assert "cohort_report" in OPERATIONS
