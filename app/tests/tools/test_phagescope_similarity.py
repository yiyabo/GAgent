"""Offline similarity-search contracts: fake MMseqs2 runs, never the live index."""
from __future__ import annotations

import contextlib
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.services.phagescope_data_service import server as svc
from app.services.phagescope_data_service import similarity_engine as se
from app.services.phagescope_mcp import tools

PROTEIN = "M" + "ACDEFGHIKLMNPQRSTVWY" * 3
MEMBERS = [
    ("md5_a", "RefSeq", "NP_1.1", 61),
    ("md5_a", "LQ_UHGV", "uhgv_9", 61),
    ("md5_b", "LQ_UHGV", "uhgv_1", 70),
    ("md5_c", "GPD", "gpd_7", 3001),
]
M8 = {
    "base": ["q0\tmd5_b\t0.40\t50\t0.80\t0.70\t1e-10\t60\t70", "q0\tmd5_a\t1.000\t61\t1.0\t1.0\t1e-30\t120\t61"],
    "long": ["q0\tmd5_c\t0.30\t40\t0.60\t0.02\t1e-5\t40\t3001"],
}


def _index(tmp_path: Path, *, corrupt: bool = False) -> Path:
    root = tmp_path / "similarity" / "v1"
    db = root / "db"
    db.mkdir(parents=True)
    for name in se.DATABASES:
        for ext in ("", ".index", ".idx", ".idx.index", ".lookup"):
            (db / f"{name}{ext}").write_bytes(b"x" * 8)
    with contextlib.closing(sqlite3.connect(db / "members.sqlite")) as conn:
        conn.execute("CREATE TABLE members(md5 TEXT, src TEXT, pid TEXT, len INTEGER, PRIMARY KEY (md5, src, pid)) WITHOUT ROWID")
        conn.executemany("INSERT INTO members VALUES (?,?,?,?)", MEMBERS)
        conn.commit()
    files = [{"path": f"db/{p.name}", "bytes": p.stat().st_size + (1 if corrupt else 0)} for p in db.iterdir()]
    manifest = {
        "index_version": "v1",
        "built_at": "2026-11-20T00:00:00Z",
        "mmseqs_version": "17.b804f",
        "databases": {"base": {"sequences": 2}, "long": {"sequences": 1}},
        "members": {"proteins": 4},
        "files": files,
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


class FakeRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, argv: list[str], timeout: float) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        job_dir = Path(argv[-1]).parent
        for name, lines in M8.items():
            (job_dir / f"{name}.m8").write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        return subprocess.CompletedProcess(argv, self.returncode, "", "boom" if self.returncode else "")


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.ids = params[0]

    def fetchall(self):
        return [(pid, "PF_1") for pid in self.ids if pid == "NP_1.1"]


class _Conn:
    def cursor(self):
        return _Cursor()


@contextlib.contextmanager
def _provider():
    yield _Conn()


def _state(tmp_path: Path, runner: FakeRunner, mmseqs: Path) -> svc.DataServiceState:
    state = svc.DataServiceState(api_key="k", conn_provider=_provider, overview={})
    state.similarity_configured = True
    state.similarity = se.SimilarityEngine(
        _index(tmp_path), mmseqs=str(mmseqs), runner="local", run_command=runner, family_lookup=state._family_ids
    )
    assert state.similarity.available, state.similarity.last_error
    return state


@pytest.fixture()
def mmseqs(tmp_path: Path) -> Path:
    binary = tmp_path / "mmseqs"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    return binary


def _call(state, method: str, path: str, body: dict[str, Any] | None = None):
    raw = json.dumps(body).encode() if body is not None else b""
    return svc.dispatch_request(state, method, f"/agent-api/v1/data/v2/similarity/{path}", {"X-API-Key": "k"}, raw)


def test_parse_sequences_accepts_fasta_and_objects_and_rejects_bad_input():
    assert se.parse_sequences(f">sp|P1 tail\n{PROTEIN[:30]}\n{PROTEIN[30:]}*\n") == [("sp|P1", PROTEIN)]
    assert se.parse_sequences([{"id": "bad id!", "sequence": PROTEIN.lower()}, PROTEIN]) == [("q1", PROTEIN), ("q2", PROTEIN)]
    for raw, code in (
        ("ACGT" * 20, "invalid_sequence"),
        (PROTEIN[:20], "sequence_too_short"),
        (PROTEIN + "1", "invalid_sequence"),
        ([PROTEIN] * 51, "invalid_parameter"),
        (None, "invalid_parameter"),
    ):
        with pytest.raises(se.dq.ServiceQueryError) as err:
            se.parse_sequences(raw)
        assert err.value.code == code


def test_search_expands_hits_through_fasta_members(tmp_path, mmseqs):
    runner = FakeRunner()
    state = _state(tmp_path, runner, mmseqs)
    status, body = _call(state, "POST", "search/", {"sequences": [{"id": "lysin", "sequence": PROTEIN}]})
    assert status == 200, body
    assert body["status"] == "done" and body["count"] == 3
    assert [r["seq_md5"] for r in body["results"]] == ["md5_a", "md5_b", "md5_c"]
    top = body["results"][0]
    assert top["query_id"] == "lysin" and top["rep_protein_id"] == "NP_1.1" and top["family_id"] == "PF_1"
    assert top["member_count"] == 2 and top["datasets"] == ["LQ_UHGV", "RefSeq"] and not top["low_quality"]
    assert body["results"][1]["low_quality"] and body["results"][2]["target_db"] == "long"
    assert body["index_version"] == "v1" and body["params"]["sensitivity_value"] == 5.7
    script = (Path(runner.calls[0][-1])).read_text()
    assert PROTEIN not in script and "-s 5.7" in script and "--db-load-mode 2" in script
    assert not (Path(runner.calls[0][-1]).parent / "query.faa").exists()

    again = _call(state, "POST", "search/", {"sequences": [{"id": "lysin", "sequence": PROTEIN}]})[1]
    assert again["search_id"] == body["search_id"] and len(runner.calls) == 1
    paged = _call(state, "GET", f"search/{body['search_id']}/?page=2&pagesize=2")[1]
    assert [r["seq_md5"] for r in paged["results"]] == ["md5_c"] and not paged["has_more"]
    status_body = _call(state, "GET", "status/")[1]
    assert status_body["index_version"] == "v1" and status_body["queue"]["max_running"] == se.MAX_RUNNING


def test_search_filters_low_quality_datasets_and_coverage(tmp_path, mmseqs):
    state = _state(tmp_path, FakeRunner(), mmseqs)
    body = _call(state, "POST", "search/", {"sequences": PROTEIN, "exclude_low_quality": True, "min_coverage": 0.5})[1]
    assert [(r["seq_md5"], r["member_count"]) for r in body["results"]] == [("md5_a", 1)]
    only_gpd = _call(state, "POST", "search/", {"sequences": PROTEIN, "datasets": ["gpd"]})[1]
    assert [r["seq_md5"] for r in only_gpd["results"]] == ["md5_c"]


def test_failed_run_reports_error_and_unknown_id_is_404(tmp_path, mmseqs):
    state = _state(tmp_path, FakeRunner(returncode=1), mmseqs)
    body = _call(state, "POST", "search/", {"sequences": PROTEIN})[1]
    assert body["status"] == "failed" and body["error"]["code"] == "search_failed" and body["results"] == []
    status, missing = _call(state, "GET", f"search/sim_{'0' * 32}/")
    assert status == 404 and missing["error"]["code"] == "not_found"
    assert _call(state, "GET", "search/../")[0] in (400, 404)


def test_saved_results_survive_a_restart(tmp_path, mmseqs):
    state = _state(tmp_path, FakeRunner(), mmseqs)
    body = _call(state, "POST", "search/", {"sequences": PROTEIN})[1]
    fresh = se.SimilarityEngine(tmp_path / "similarity" / "v1", mmseqs=str(mmseqs), run_command=FakeRunner())
    assert fresh.view(fresh.get(body["search_id"]))["count"] == 3


def test_unconfigured_or_changed_index_is_unavailable(tmp_path, mmseqs):
    state = svc.DataServiceState(api_key="k", conn_provider=_provider, overview={})
    status, body = _call(state, "GET", "status/")
    assert status == 503 and body["error"]["code"] == "similarity_unavailable"
    broken = se.SimilarityEngine(_index(tmp_path, corrupt=True), mmseqs=str(mmseqs))
    assert not broken.available and "changed index file" in broken.last_error


async def test_mcp_similarity_tools_forward_to_data_service(monkeypatch):
    calls = []

    async def fake(path, *, method="GET", query=None, body=None, timeout=45.0):
        calls.append((method, path, body or query))
        return {"search_id": "sim_" + "a" * 32, "status": "running", "results": [], "notes": ["n1"]}

    monkeypatch.setattr(tools.client, "call_service", fake)
    data, notes = await tools.TOOLS_BY_NAME["similarity_search"].run({"sequences": PROTEIN, "wait_seconds": 99})
    assert calls[0][:2] == ("POST", "v2/similarity/search/") and calls[0][2]["wait_seconds"] == 20
    assert notes[0].startswith("Search still running") and "n1" in notes and "notes" not in data
    await tools.TOOLS_BY_NAME["get_similarity_result"].run({"search_id": "sim_" + "a" * 32, "pagesize": 500})
    assert calls[1] == ("GET", f"v2/similarity/search/sim_{'a' * 32}/", {"page": 1, "pagesize": 200})
    with pytest.raises(tools.ToolInputError):
        await tools.TOOLS_BY_NAME["get_similarity_result"].run({"search_id": "../x"})
