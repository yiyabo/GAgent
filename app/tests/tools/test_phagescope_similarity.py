"""Offline similarity-search contracts: fake MMseqs2 runs, never the live index."""
from __future__ import annotations

import contextlib
import json
import sqlite3
import subprocess
import threading
import time
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
    def __init__(self, returncode: int = 0, m8: dict[str, list[str]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.m8 = M8 if m8 is None else m8

    def __call__(self, argv: list[str], timeout: float) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        job_dir = Path(argv[-1]).parent
        for name in se.DATABASES:
            lines = self.m8.get(name, [])
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


def _state(tmp_path: Path, runner, mmseqs: Path, *, runner_kind: str = "local") -> svc.DataServiceState:
    state = svc.DataServiceState(api_key="k", conn_provider=_provider, overview={})
    state.similarity_configured = True
    state.similarity = se.SimilarityEngine(
        _index(tmp_path), mmseqs=str(mmseqs), runner=runner_kind, run_command=runner, family_lookup=state._family_ids
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


def test_done_search_reports_outcome_and_timings(tmp_path, mmseqs):
    body = _call(_state(tmp_path, FakeRunner(), mmseqs), "POST", "search/", {"sequences": PROTEIN})[1]
    assert body["status"] == "done" and body["outcome"] == "hits" and body["count"] == 3
    timings = body["timings"]
    assert timings["final"] and timings["run_seconds"] is not None and timings["queued_seconds"] >= 0
    assert body["elapsed_seconds"] == timings["run_seconds"] and body["started_at"]


def test_zero_hit_search_is_distinct_from_pending_and_failed(tmp_path, mmseqs):
    body = _call(_state(tmp_path, FakeRunner(m8={}), mmseqs), "POST", "search/", {"sequences": PROTEIN})[1]
    assert body["status"] == "done" and body["outcome"] == "no_hits" and body["count"] == 0
    assert body["queries"][0]["hits"] == 0


def test_failed_search_has_no_count(tmp_path, mmseqs):
    body = _call(_state(tmp_path, FakeRunner(returncode=1), mmseqs), "POST", "search/", {"sequences": PROTEIN})[1]
    assert body["outcome"] == "failed" and body["count"] is None and body["queries"][0]["hits"] is None
    assert body["timings"]["final"]


class GatedSlurm(FakeRunner):
    """Holds the fake sbatch until released; ``start`` writes the job script's start marker."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.dispatched = threading.Event()
        self.release = threading.Event()
        self.job_dir: Path | None = None

    def start(self) -> None:
        assert self.job_dir is not None
        (self.job_dir / se.STARTED_MARKER).write_text(f"{time.time():.3f}\n", encoding="ascii")

    def __call__(self, argv, timeout):
        self.job_dir = Path(argv[-1]).parent
        self.dispatched.set()
        assert self.release.wait(10)
        return super().__call__(argv, timeout)


def test_slurm_pending_then_running_then_done(tmp_path, mmseqs):
    runner = GatedSlurm()
    state = _state(tmp_path, runner, mmseqs, runner_kind="slurm")
    body = _call(state, "POST", "search/", {"sequences": PROTEIN, "wait_seconds": 0})[1]
    assert runner.dispatched.wait(10)
    sid = body["search_id"]
    pending = _call(state, "GET", f"search/{sid}/")[1]
    assert pending["status"] == "queued" and pending["outcome"] == "pending"
    assert pending["count"] is None and pending["queries"][0]["hits"] is None and pending["poll_after_seconds"] == 5
    assert pending["timings"]["run_seconds"] is None and not pending["timings"]["final"]
    assert _call(state, "GET", "status/")[1]["queue"]["queued"] == 1
    runner.start()
    running = _call(state, "GET", f"search/{sid}/")[1]
    assert running["status"] == "running" and running["count"] is None and running["started_at"]
    assert running["timings"]["run_seconds"] is not None
    runner.release.set()
    job = state.similarity.get(sid)
    assert job.done.wait(10)
    done = state.similarity.view(job)
    assert done["outcome"] == "hits" and done["count"] == 3 and done["timings"]["final"]
    assert "date +%s.%N > started" in (runner.job_dir / "search.sh").read_text()


def test_slurm_time_limit_reports_search_timeout(tmp_path, mmseqs):
    class TimedOut(FakeRunner):
        def __call__(self, argv, timeout):
            job_dir = Path(argv[-1]).parent
            (job_dir / se.STARTED_MARKER).write_text(f"{time.time():.3f}", encoding="ascii")
            (job_dir / "slurm.log").write_text("slurmstepd: error: *** JOB 1 CANCELLED DUE TO TIME LIMIT ***\n")
            return subprocess.CompletedProcess(argv, 1, "", "")

    state = _state(tmp_path, TimedOut(), mmseqs, runner_kind="slurm")
    body = _call(state, "POST", "search/", {"sequences": PROTEIN})[1]
    assert body["status"] == "failed" and body["error"]["code"] == "search_timeout" and body["count"] is None
    assert body["timings"]["run_seconds"] is not None


def test_service_timeout_reports_search_timeout(tmp_path, mmseqs):
    def expire(argv, timeout):
        raise subprocess.TimeoutExpired(argv, timeout)

    body = _call(_state(tmp_path, expire, mmseqs), "POST", "search/", {"sequences": PROTEIN})[1]
    assert body["outcome"] == "failed" and body["error"]["code"] == "search_timeout"


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
    assert notes[0].startswith("Search not finished (status=running)") and "n1" in notes and "notes" not in data
    await tools.TOOLS_BY_NAME["get_similarity_result"].run({"search_id": "sim_" + "a" * 32, "pagesize": 500})
    assert calls[1] == ("GET", f"v2/similarity/search/sim_{'a' * 32}/", {"page": 1, "pagesize": 200})
    with pytest.raises(tools.ToolInputError):
        await tools.TOOLS_BY_NAME["get_similarity_result"].run({"search_id": "../x"})


def test_mcp_similarity_notes_separate_failed_and_zero_hits():
    failed = tools._similarity_notes({"status": "failed", "count": None, "error": {"code": "search_timeout"}})
    assert failed[0].startswith("Search failed (search_timeout)")
    assert tools._similarity_notes({"status": "done", "count": 0})[0].startswith("Search finished with zero hits")
    assert tools._similarity_notes({"status": "done", "count": 2})[0] == tools.SIMILARITY_NOTE
