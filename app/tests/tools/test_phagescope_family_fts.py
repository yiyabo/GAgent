"""Offline FTS5 family-search contracts; never contact the live PhageScope API."""
from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from app.routers.chat.tool_results import sanitize_tool_result
from app.services.phagescope_data_service import db_queries as dq
from app.services.phagescope_data_service import server as svc
from app.services.phagescope_data_service.family_fts_engine import FamilyFTSEngine
from scripts import build_phagescope_family_fts as build
from tool_box.tools_impl.phagescope_data import client as ps_client
from tool_box.tools_impl.phagescope_data import phagescope_data_handler
from tool_box.tools_impl.phagescope_data.schema import PARAMETERS_SCHEMA


@pytest.fixture()
def family_snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "family-index"
    stage = root / "staging"
    stage.mkdir(parents=True)
    rows = [
        ("PF_FIBER", 100, 100, "assembly;", "tail fiber protein", 2, "P1", 2, False),
        ("PF_FIBRE", 80, 80, "assembly;", "tail fibre protein", 1, "P2", 1, False),
        ("PF_FIBER_SINGLE", 1, 1, "assembly;", "tail fiber protein", 0, "P3", 1, True),
        ("PF_LYSIN", 40, 40, "lysis;", "endolysin", 1, "P4", 2, False),
        ("PF_PHAGE_LYSIN", 50, 50, "lysis;", "phage lysin", 1, "P5", 1, False),
        ("PF_PORTAL", 200, 200, "assembly;", "portal protein", 1, "P6", 1, False),
    ]
    stats = build.create_index(stage / "families.sqlite", [rows], len(rows))
    assert stats == {"indexed_rows": 6, "indexed_non_singleton_rows": 5}
    build.validate_index(stage / "families.sqlite", len(rows))
    shutil.copyfile(build.DEFAULT_SYNONYMS, stage / "family_synonyms.json")
    files = {
        name: {"bytes": (stage / name).stat().st_size, "sha256": build.sha256_of(stage / name)}
        for name in ("families.sqlite", "family_synonyms.json")
    }
    (stage / "manifest.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "version": "v1",
                "built_at": "2026-11-14T00:00:00Z",
                "source_role": "phage_ro",
                "source_rows": 8,
                "indexed_rows": 6,
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    build.publish(stage, root / "v1", root / "current")
    return root / "current"


def _post(state: svc.DataServiceState, **body: Any) -> tuple[int, dict[str, Any]]:
    return svc.dispatch_request(
        state,
        "POST",
        "/agent-api/v1/data/v2/proteins/query/",
        {"X-API-Key": "test-secret"},
        json.dumps({"mode": "search_families", **body}).encode(),
    )


def test_family_fts_synonyms_paging_filters_and_exact_mode(family_snapshot: Path) -> None:
    engine = FamilyFTSEngine(family_snapshot)
    assert engine.available, engine.last_error
    page1 = engine.search("tail fibre", mode="synonyms", pagesize=1)
    page2 = engine.search("tail fibre", mode="synonyms", page=2, pagesize=1)
    assert page1["source"] == "family_fts5"
    assert page1["expanded_terms"] == ["tail fiber", "tail fibre"]
    assert page1["results"][0]["family_id"] == "PF_FIBER"
    assert page1["results"][0]["match_kind"] == "alias"
    assert page1["has_more"] is True
    assert page2["results"][0]["family_id"] == "PF_FIBRE"
    assert page2["results"][0]["match_kind"] == "literal"
    assert page2["has_more"] is False
    assert [row["family_id"] for row in engine.search("tail fibre", mode="fts")["results"]] == ["PF_FIBRE"]
    assert [row["family_id"] for row in engine.search("tail fibre", mode="synonyms", min_members=90)["results"]] == ["PF_FIBER"]
    assert [row["family_id"] for row in engine.search("tail fibre", mode="synonyms", min_datasets=2)["results"]] == ["PF_FIBER"]
    assert engine.search("tail fibre", mode="synonyms", dominant_class="lysis")["results"] == []
    all_rows = engine.search("tail fibre", mode="synonyms", non_singleton_only=False)["results"]
    assert all_rows[-1]["family_id"] == "PF_FIBER_SINGLE"
    lysins = engine.search("endolysin", mode="synonyms")["results"]
    assert [(row["family_id"], row["match_kind"]) for row in lysins] == [
        ("PF_PHAGE_LYSIN", "alias"),
        ("PF_LYSIN", "literal"),
    ]
    assert engine.search('tail fibre" OR "endolysin', mode="fts")["results"] == []
    assert engine.search("anti-holin", mode="synonyms")["expanded_terms"] == ["antiholin", "anti holin"]


def test_chat_preserves_family_match_provenance(family_snapshot: Path) -> None:
    engine = FamilyFTSEngine(family_snapshot)
    raw = {
        "tool": "phagescope_data",
        "operation": "search_proteins",
        "mode": "search_families",
        "success": True,
        **engine.search("tail fibre", mode="synonyms", pagesize=2),
    }
    compact = sanitize_tool_result("phagescope_data", raw)
    assert compact["source"] == "family_fts5"
    assert compact["search_mode"] == "synonyms"
    assert compact["expanded_terms"] == ["tail fiber", "tail fibre"]
    assert compact["index_built_at"] == "2026-11-14T00:00:00Z"
    assert [row["match_kind"] for row in compact["results"]] == ["alias", "literal"]


def test_indexed_route_skips_postgres_and_literal_still_uses_it(
    family_snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    @contextlib.contextmanager
    def postgres():
        calls.append({"opened": True})
        yield object()

    def literal_search(_conn: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"results": [{"family_id": "FROM_SQL"}], "page": 1, "pagesize": 20, "has_more": False}

    monkeypatch.setattr(dq, "search_protein_families", literal_search)
    state = svc.DataServiceState(api_key="test-secret", conn_provider=postgres, overview={}, family_index_root=family_snapshot)
    status, indexed = _post(state, keyword="tail fibre", family_search_mode="synonyms", pagesize=2)
    assert status == 200
    assert [row["family_id"] for row in indexed["results"]] == ["PF_FIBER", "PF_FIBRE"]
    assert calls == []
    status, auto = _post(state, keyword="endolysin")
    assert status == 200 and auto["search_mode"] == "fts"
    assert calls == []
    status, literal = _post(state, keyword="tail fib", family_search_mode="literal")
    assert status == 200 and literal["results"][0]["family_id"] == "FROM_SQL"
    assert calls[1]["keyword"] == "tail fib"
    status, direct = _post(state, family_id="PF_FIBER")
    assert status == 200 and direct["results"][0]["family_id"] == "FROM_SQL"
    assert calls[3]["family_id"] == "PF_FIBER"


def test_invalid_index_fails_closed_and_unconfigured_auto_uses_sql(
    family_snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (family_snapshot / "family_synonyms.json").write_text("{}", encoding="utf-8")
    calls: list[str] = []

    @contextlib.contextmanager
    def postgres():
        calls.append("sql")
        yield object()

    monkeypatch.setattr(dq, "search_protein_families", lambda _conn, **_: {"results": [], "page": 1})
    invalid = svc.DataServiceState(api_key="test-secret", conn_provider=postgres, overview={}, family_index_root=family_snapshot)
    assert invalid.family_index is None
    status, response = _post(invalid, keyword="endolysin")
    assert status == 503 and response["error"]["code"] == "family_index_unavailable"
    assert calls == []
    status, response = _post(invalid, keyword="endolysin", family_search_mode="literal")
    assert status == 200 and calls == ["sql"]
    unconfigured = svc.DataServiceState(api_key="test-secret", conn_provider=postgres, overview={})
    status, response = _post(unconfigured, keyword="endolysin")
    assert status == 200 and response["results"] == []
    status, response = _post(unconfigured, keyword="endolysin", family_search_mode="synonyms")
    assert status == 503 and response["error"]["code"] == "family_index_unavailable"
    status, response = _post(unconfigured, family_search_mode="fts")
    assert status == 400 and response["error"]["code"] == "invalid_parameter"
    assert calls == ["sql", "sql"]


def test_builder_rejects_count_mismatch_and_existing_version(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    staging = root / "staging"
    staging.mkdir()
    row = ("PF_ONE", 1, 1, "lysis;", "endolysin", 0, "P1", 1, True)
    with pytest.raises(ValueError, match="row mismatch"):
        build.create_index(staging / "families.sqlite", [[row]], 2)
    (root / "v1").mkdir()
    (root / "current").symlink_to("v1")
    with pytest.raises(FileExistsError):
        build.publish(staging, root / "v1", root / "current")
    assert (root / "current").resolve().name == "v1"


async def test_tool_passes_explicit_family_search_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHAGESCOPE_DATA_ENABLED", "1")
    captured: list[dict[str, Any]] = []

    async def fake_call(path: str, *, body: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        assert path == "v2/proteins/query/"
        captured.append(body or {})
        return {"results": [], "page": 1, "pagesize": 25, "has_more": False}

    monkeypatch.setattr(ps_client, "call_service", fake_call)
    result = await phagescope_data_handler(operation="search_proteins", family_query="tail fibre", family_search_mode="synonyms")
    assert result["success"] is True
    assert captured[0]["family_search_mode"] == "synonyms"
    assert PARAMETERS_SCHEMA["properties"]["family_search_mode"]["enum"] == ["auto", "literal", "fts", "synonyms"]
