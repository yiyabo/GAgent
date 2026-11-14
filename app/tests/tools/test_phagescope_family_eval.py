"""The family search review must expose timeouts and leave relevance unlabeled."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from scripts import evaluate_phagescope_family_search as ev


def test_review_reports_timeout_without_calling_it_zero(
    tmp_path: Path, monkeypatch: Any
) -> None:
    queries = ev.load_queries(ev.DEFAULT_QUERIES)
    assert len(queries) == 20

    def timed_out(*_: Any, **__: Any) -> dict[str, Any]:
        raise TimeoutError("literal search exceeded the limit")

    monkeypatch.setattr(ev.dq, "search_protein_families", timed_out)

    class FakeEngine:
        def search(self, _term: str, *, mode: str, pagesize: int) -> dict[str, Any]:
            assert pagesize == 5
            return {
                "has_more": False,
                "results": [
                    {
                        "family_id": "PF_TEST",
                        "consensus_product": "tail fiber",
                        "dominant_class": "assembly;",
                        "member_count": 3,
                        "match_kind": "alias" if mode == "synonyms" else None,
                    }
                ],
                "expanded_terms": ["tail fibre"] if mode == "synonyms" else [],
            }

    records = ev.evaluate(object(), FakeEngine(), [queries[3]], top_k=5)
    assert records[0]["modes"]["literal"]["error"] == "TimeoutError"
    assert "results" not in records[0]["modes"]["literal"]
    directory = tmp_path / "review"
    ev.save_artifacts(
        directory,
        records,
        metadata={
            "checked_at": "2026-11-14T00:00:00Z",
            "snapshot_version": "v2",
            "snapshot_built_at": "2026-11-14T00:00:00Z",
            "query_sha256": "test",
            "top_k": 5,
        },
    )
    assert "error: TimeoutError" in (directory / "summary.md").read_text(
        encoding="utf-8"
    )
    result = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    assert result["queries"][0]["modes"]["literal"]["error"] == "TimeoutError"
    with (directory / "review_candidates.tsv").open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert len(rows) == 1 and rows[0]["family_id"] == "PF_TEST"
    assert rows[0]["expert_label"] == "" and rows[0]["expert_note"] == ""
