"""Protein similarity search (MMseqs2) over a validated GAgent-owned index version.

The index is built offline from PhageScope's protein FASTA files (see
``deploy/phagescope_data_service/README.md``). Hits expand to PhageScope proteins
through the FASTA-derived member table shipped with the index
(``db/members.sqlite``), so results follow the FASTA files' sequence version.
Query sequences are written only into the job's FASTA file, never onto a command
line, and are not logged. Runs as a loose module on the host (Python 3.9).
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import mmap
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from . import db_queries as dq
except ImportError:  # host data service runs these modules as loose files
    import db_queries as dq

logger = logging.getLogger("phagescope_data_service.similarity")

MIN_LEN = 30
MAX_LEN = 10_000
MAX_QUERIES = 50
MAX_TOTAL_AA = 50_000
MAX_HITS = 500
DEFAULT_HITS = 50
MAX_SEQS = 500
THREADS = 16
SENSITIVITY = {"fast": 4.0, "default": 5.7, "sensitive": 7.5}
MAX_RUNNING = 2
MAX_PENDING = 20
MAX_WAIT_SECONDS = 20.0
RESULT_TTL_SECONDS = 7 * 24 * 3600
JOB_TIMEOUT_SECONDS = 20 * 60
SLURM_TIME_LIMIT = "00:15:00"
STARTED_MARKER = "started"
PENDING = ("queued", "running")
SAMPLE_MEMBERS = 5
DATABASES = ("base", "long")
FORMAT = "query,target,fident,alnlen,qcov,tcov,evalue,bits,tlen"
# A search mmaps the whole precomputed index (``--db-load-mode 2``). A Slurm job's memory cgroup is charged for
# every index page it faults in, so a cold index thrashes against the job's memory cap on rotating disk; pages
# already cached by the data service are not charged to the job. Keep them resident and hold searches until
# they are.
WARM_FILES = tuple(f"db/{name}.idx" for name in DATABASES)
WARM_CHECK_SECONDS = 300
WARM_REFRESH_BELOW = 0.99
WARM_READY_AT = 0.9
WARM_WAIT_SECONDS = 40 * 60
WARM_CHUNK = 16 << 20

_AA = re.compile(r"^[ACDEFGHIKLMNPQRSTVWYBJOUXZ]+$")
_NUCLEOTIDE = frozenset("ACGTUN")
_QUERY_ID = re.compile(r"^[A-Za-z0-9_.:|-]{1,64}$")
SEARCH_ID = re.compile(r"^sim_[0-9a-f]{32}$")

NOTES = (
    "E-values depend on the index size, so they change between index versions.",
    "No hit is not proof that PhageScope holds no remote homolog; retry with sensitivity=sensitive.",
    "Hits follow the sequence version of PhageScope's protein FASTA files (the sequences get_sequence and "
    "protein downloads serve); for about 1.3% of proteins the database's own unique_seq table holds a "
    "different gene call.",
    f"Proteins shorter than {MIN_LEN} aa are not indexed; proteins longer than 2700 aa are searched in a "
    "supplementary index (target_db=long).",
)


def similarity_root_from_env() -> Path | None:
    raw = os.environ.get("PHAGE_SIMILARITY_ROOT", "").strip()
    return Path(raw) if raw else None


_MINCORE_WINDOW = 1 << 30


def _libc() -> Any:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mmap.restype = ctypes.c_void_p
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
    libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_ubyte)]
    return libc


def resident_bytes(path: Path) -> tuple[int, int] | None:
    """``(cached, total)`` bytes of ``path`` in the page cache via mincore(2); None where unsupported."""
    try:
        libc = _libc()
        page = mmap.PAGESIZE
        with path.open("rb") as fh:
            total = os.fstat(fh.fileno()).st_size
            cached = 0
            for offset in range(0, total, _MINCORE_WINDOW):
                length = min(_MINCORE_WINDOW, total - offset)
                addr = libc.mmap(None, length, mmap.PROT_READ, mmap.MAP_SHARED, fh.fileno(), offset)
                if addr in (None, ctypes.c_void_p(-1).value):
                    return None
                try:
                    vec = (ctypes.c_ubyte * ((length + page - 1) // page))()
                    if libc.mincore(addr, length, vec) != 0:
                        return None
                    flags = bytes(vec)
                    cached += (len(flags) - flags.count(0)) * page
                finally:
                    libc.munmap(addr, length)
        return min(cached, total), total
    except (OSError, AttributeError, ValueError):
        return None


def _now_iso(ts: float | None = None) -> str:
    moment = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return moment.replace(microsecond=0).isoformat()


def _read_marker(path: Path) -> float | None:
    try:
        return float(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _timings(job: "_Job") -> dict[str, Any]:
    """Queue wait (data-service slot plus Slurm pending) and execution time, in seconds."""
    end = job.finished_at or time.time()
    started = job.started_at
    queued = (started if started else end) - job.submitted_at
    return {
        "queued_seconds": round(max(0.0, queued), 2),
        "run_seconds": round(max(0.0, end - started), 2) if started else None,
        "total_seconds": round(max(0.0, end - job.submitted_at), 2),
        "final": job.status not in PENDING,
    }


def _bad(message: str, code: str = "invalid_parameter") -> dq.ServiceQueryError:
    return dq.ServiceQueryError(code, message, 400)


def parse_sequences(raw: Any) -> list[tuple[str, str]]:
    """Return ``[(query_id, sequence)]`` from FASTA text, a bare sequence, or ``[{id, sequence}]``."""
    entries: list[tuple[str | None, str]] = []
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith(">"):
            current: str | None = None
            chunks: list[str] = []
            for line in text.splitlines():
                if line.startswith(">"):
                    if current is not None:
                        entries.append((current, "".join(chunks)))
                    header = line[1:].strip().split()
                    current, chunks = (header[0] if header else ""), []
                else:
                    chunks.append(line)
            if current is not None:
                entries.append((current, "".join(chunks)))
        elif text:
            entries.append((None, text))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                entries.append((None, item))
            elif isinstance(item, dict):
                seq = item.get("sequence", item.get("seq"))
                if not isinstance(seq, str):
                    raise _bad("Each sequence object needs a string 'sequence'.")
                entries.append((str(item["id"]) if item.get("id") is not None else None, seq))
            else:
                raise _bad("sequences must be FASTA text or a list of {id, sequence} objects.")
    else:
        raise _bad("sequences must be FASTA text or a list of {id, sequence} objects.")
    if not entries:
        raise _bad("Provide at least one protein sequence.")
    if len(entries) > MAX_QUERIES:
        raise _bad(f"At most {MAX_QUERIES} sequences per search.")

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    total = 0
    for index, (qid, seq) in enumerate(entries, start=1):
        clean = "".join(seq.split()).upper().rstrip("*")
        label = qid if qid and _QUERY_ID.match(qid) and qid not in seen else f"q{index}"
        if not clean:
            raise _bad(f"Sequence {label} is empty.", "invalid_sequence")
        if set(clean) <= _NUCLEOTIDE and len(clean) >= 20:
            raise _bad(
                f"Sequence {label} looks like nucleotides; this search takes protein sequences only.",
                "invalid_sequence",
            )
        if not _AA.match(clean):
            raise _bad(f"Sequence {label} has characters outside the amino-acid alphabet.", "invalid_sequence")
        if len(clean) < MIN_LEN:
            raise _bad(
                f"Sequence {label} is {len(clean)} aa; proteins shorter than {MIN_LEN} aa are not indexed.",
                "sequence_too_short",
            )
        if len(clean) > MAX_LEN:
            raise _bad(f"Sequence {label} is longer than {MAX_LEN} aa.", "sequence_too_long")
        total += len(clean)
        seen.add(label)
        out.append((label, clean))
    if total > MAX_TOTAL_AA:
        raise _bad(f"Total query length {total} aa exceeds {MAX_TOTAL_AA} aa.")
    return out


def _fraction(payload: dict[str, Any], name: str) -> float:
    value = payload.get(name)
    if value is None or value == "":
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _bad(f"{name} must be a number between 0 and 1.") from exc
    if not 0.0 <= number <= 1.0:
        raise _bad(f"{name} must be between 0 and 1.")
    return number


def parse_params(payload: dict[str, Any]) -> dict[str, Any]:
    sensitivity = str(payload.get("sensitivity") or "default").strip().lower()
    if sensitivity not in SENSITIVITY:
        raise _bad(f"sensitivity must be one of: {', '.join(SENSITIVITY)}.")
    try:
        max_hits = int(payload.get("max_hits", DEFAULT_HITS))
        evalue = float(payload.get("evalue", 1e-3))
    except (TypeError, ValueError) as exc:
        raise _bad("max_hits must be an integer and evalue a number.") from exc
    if not 1 <= max_hits <= MAX_HITS:
        raise _bad(f"max_hits must be between 1 and {MAX_HITS}.")
    if not 0.0 < evalue <= 10.0:
        raise _bad("evalue must be greater than 0 and at most 10.")
    datasets = payload.get("datasets") or []
    if not isinstance(datasets, list) or len(datasets) > 40 or not all(isinstance(d, str) for d in datasets):
        raise _bad("datasets must be a list of up to 40 dataset names.")
    return {
        "sensitivity": sensitivity,
        "max_hits": max_hits,
        "evalue": evalue,
        "min_identity": _fraction(payload, "min_identity"),
        "min_coverage": _fraction(payload, "min_coverage"),
        "exclude_low_quality": bool(payload.get("exclude_low_quality", False)),
        "datasets": sorted({d.strip().casefold() for d in datasets if d.strip()}),
    }


class _Job:
    def __init__(self, search_id: str, queries: list[tuple[str, str]], params: dict[str, Any], job_dir: Path) -> None:
        self.search_id = search_id
        self.queries = queries
        self.params = params
        self.job_dir = job_dir
        self.status = "queued"
        self.submitted_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.error: dict[str, str] | None = None
        self.dispatched = False
        self.results: list[dict[str, Any]] = []
        self.query_summary: list[dict[str, Any]] = [
            {"query_id": qid, "length": len(seq), "hits": None} for qid, seq in queries
        ]
        self.done = threading.Event()


class SimilarityEngine:
    def __init__(
        self,
        root: Path,
        *,
        work_dir: Path | None = None,
        mmseqs: str | None = None,
        runner: str | None = None,
        run_command: Callable[[list[str], float], subprocess.CompletedProcess] | None = None,
        family_lookup: Callable[[list[str]], dict[str, str]] | None = None,
    ) -> None:
        self.available = False
        self.last_error = "index_unavailable"
        self.manifest: dict[str, Any] = {}
        self.mmseqs_version = ""
        self.family_lookup = family_lookup
        self._run_command = run_command or self._subprocess
        self.runner = (runner or os.environ.get("PHAGE_SIMILARITY_RUNNER", "slurm")).strip().lower()
        self.partition = os.environ.get("PHAGE_SIMILARITY_PARTITION", "work").strip() or "work"
        self.slurm_mem = os.environ.get("PHAGE_SIMILARITY_SLURM_MEM", "32G").strip() or "32G"
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(MAX_RUNNING)
        self._jobs: dict[str, _Job] = {}
        self._warm_ready = threading.Event()
        self._warm_ready.set()
        self.cache: dict[str, Any] = {"keep_warm": False, "warming": False, "resident_fraction": None, "last_warm_seconds": None}
        try:
            supplied = Path(root)
            resolved = supplied.resolve(strict=True)
            if not resolved.is_dir() or resolved.parent != supplied.parent.resolve(strict=True):
                raise ValueError("root must be a version directory or a link to a sibling version directory")
            manifest = json.loads((resolved / "manifest.json").read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("index_version") != resolved.name:
                raise ValueError("similarity manifest version does not match its directory")
            sizes = {entry["path"]: entry["bytes"] for entry in manifest.get("files", [])}
            required = [f"db/{name}{ext}" for name in DATABASES for ext in ("", ".index", ".idx", ".idx.index", ".lookup")]
            required.append("db/members.sqlite")
            for rel in required:
                path = resolved / rel
                if path.is_symlink() or not path.is_file() or rel not in sizes or path.stat().st_size != sizes[rel]:
                    raise ValueError(f"missing or changed index file: {rel}")
            binary = mmseqs or os.environ.get("PHAGE_SIMILARITY_MMSEQS", "").strip() or shutil.which("mmseqs") or ""
            if not binary or not os.access(binary, os.X_OK):
                raise ValueError("mmseqs binary not found")
            self.root = resolved
            self.mmseqs = binary
            self.members_path = resolved / "db" / "members.sqlite"
            with closing(self._members()) as conn:
                conn.execute("SELECT md5 FROM members LIMIT 1").fetchall()
            self.mmseqs_version = str(manifest.get("mmseqs_version") or "")
            self.work_dir = Path(work_dir or os.environ.get("PHAGE_SIMILARITY_WORK_DIR", "") or resolved.parent / "jobs")
            self.work_dir.mkdir(parents=True, exist_ok=True)
            self.manifest = manifest
            self.available = True
            self.last_error = ""
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            self.last_error = f"index_unavailable: {exc}"

    # ------------------------------------------------------------ public API

    @property
    def index_version(self) -> str:
        return str(self.manifest.get("index_version") or "")

    def status(self) -> dict[str, Any]:
        with self._lock:
            for job in self._jobs.values():
                self._refresh(job)
            running = sum(1 for job in self._jobs.values() if job.status == "running")
            queued = sum(1 for job in self._jobs.values() if job.status == "queued")
        dbs = self.manifest.get("databases") or {}
        return {
            "index_version": self.index_version,
            "built_at": self.manifest.get("built_at"),
            "mmseqs_version": self.mmseqs_version,
            "sequence_source": self.manifest.get("sequence_source"),
            "databases": dbs,
            "excluded": self.manifest.get("excluded"),
            "member_proteins": (self.manifest.get("members") or {}).get("proteins"),
            "index_cache": {**self.cache, "ready": self._warm_ready.is_set()},
            "queue": {"running": running, "queued": queued, "max_running": MAX_RUNNING, "max_pending": MAX_PENDING},
            "limits": {
                "max_queries": MAX_QUERIES,
                "max_total_aa": MAX_TOTAL_AA,
                "min_length": MIN_LEN,
                "max_length": MAX_LEN,
                "max_hits": MAX_HITS,
            },
        }

    def start_keep_warm(self, interval: float = WARM_CHECK_SECONDS) -> None:
        """Keep the index files in the page cache from this (uncapped) process; searches wait until they are."""
        if not self.available or self.cache["keep_warm"]:
            return
        self.cache["keep_warm"] = True
        self._warm_ready.clear()

        def loop() -> None:
            while True:
                try:
                    self.warm_once()
                except Exception:  # never let the keeper die; searches fall back to dispatching cold
                    logger.exception("Similarity index warm-up failed")
                    self._warm_ready.set()
                time.sleep(interval)

        threading.Thread(target=loop, name="simsearch-keep-warm", daemon=True).start()

    def warm_once(self) -> None:
        fraction = self._resident_fraction()
        self.cache["resident_fraction"] = fraction
        if fraction is not None and fraction >= WARM_REFRESH_BELOW:
            self._warm_ready.set()
            return
        if fraction is None or fraction < WARM_READY_AT:
            self._warm_ready.clear()
        self.cache["warming"] = True
        began = time.time()
        try:
            for rel in WARM_FILES:
                self._read_through(self.root / rel)
        finally:
            self.cache["warming"] = False
        self.cache["last_warm_seconds"] = round(time.time() - began, 1)
        self.cache["resident_fraction"] = after = self._resident_fraction()
        logger.info(
            "Similarity index warmed in %ss (resident %s -> %s)", self.cache["last_warm_seconds"], fraction, after
        )
        self._warm_ready.set()

    def _resident_fraction(self) -> float | None:
        cached = total = 0
        for rel in WARM_FILES:
            counts = resident_bytes(self.root / rel)
            if counts is None:
                return None
            cached, total = cached + counts[0], total + counts[1]
        return round(cached / total, 4) if total else 1.0

    @staticmethod
    def _read_through(path: Path) -> None:
        buf = bytearray(WARM_CHUNK)
        with path.open("rb", buffering=0) as fh:
            if hasattr(os, "posix_fadvise"):
                os.posix_fadvise(fh.fileno(), 0, 0, os.POSIX_FADV_SEQUENTIAL)
            while fh.readinto(buf):
                pass

    def submit(self, payload: dict[str, Any]) -> _Job:
        queries = parse_sequences(payload.get("sequences"))
        params = parse_params(payload)
        key = json.dumps({"v": self.index_version, "q": queries, "p": params}, sort_keys=True)
        search_id = "sim_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        with self._lock:
            self._purge_locked()
            job = self._jobs.get(search_id) or self._load_locked(search_id)
            if job is not None and job.status != "failed":
                return job
            pending = sum(1 for j in self._jobs.values() if j.status in PENDING)
            if pending >= MAX_PENDING:
                raise dq.ServiceQueryError(
                    "too_busy", "The similarity search queue is full; retry in about a minute.", 429
                )
            job = _Job(search_id, queries, params, self.work_dir / search_id)
            self._jobs[search_id] = job
        threading.Thread(target=self._run, args=(job,), name=f"simsearch-{search_id[:12]}", daemon=True).start()
        return job

    def get(self, search_id: str) -> _Job:
        if not SEARCH_ID.match(search_id or ""):
            raise _bad("search_id is malformed.")
        with self._lock:
            self._purge_locked()
            job = self._jobs.get(search_id) or self._load_locked(search_id)
        if job is None:
            raise dq.ServiceQueryError("not_found", "Unknown or expired search_id; submit the search again.", 404)
        return job

    def view(self, job: _Job, *, page: int = 1, pagesize: int = 50) -> dict[str, Any]:
        self._refresh(job)
        page = max(1, page)
        pagesize = max(1, min(pagesize, 200))
        start = (page - 1) * pagesize
        done = job.status == "done"
        rows = job.results[start : start + pagesize] if done else []
        timings = _timings(job)
        body: dict[str, Any] = {
            "search_id": job.search_id,
            "status": job.status,
            "outcome": ("hits" if job.results else "no_hits") if done else "failed" if job.status == "failed" else "pending",
            "submitted_at": _now_iso(job.submitted_at),
            "started_at": _now_iso(job.started_at) if job.started_at else None,
            "finished_at": _now_iso(job.finished_at) if job.finished_at else None,
            "elapsed_seconds": timings["run_seconds"],
            "timings": timings,
            "index_version": self.index_version,
            "mmseqs_version": self.mmseqs_version,
            "params": {**job.params, "max_seqs": MAX_SEQS, "sensitivity_value": SENSITIVITY[job.params["sensitivity"]]},
            "queries": job.query_summary,
            "count": len(job.results) if done else None,
            "page": page,
            "pagesize": pagesize,
            "has_more": done and start + pagesize < len(job.results),
            "results": rows,
            "notes": list(NOTES),
        }
        if job.status in PENDING:
            body["poll_after_seconds"] = 5
            if not job.dispatched and not self._warm_ready.is_set():
                body["waiting_for"] = "index_loading"
        if job.error:
            body["error"] = job.error
        return body

    # ------------------------------------------------------------ internals

    def _refresh(self, job: _Job) -> None:
        """Move a dispatched job to running once its script has actually started (Slurm may hold it pending)."""
        if job.status != "queued" or not job.dispatched:
            return
        started = _read_marker(job.job_dir / STARTED_MARKER)
        if started is not None:
            job.started_at, job.status = started, "running"

    def _members(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.members_path}?mode=ro&immutable=1", uri=True, check_same_thread=False)

    def _purge_locked(self) -> None:
        cutoff = time.time() - RESULT_TTL_SECONDS
        for sid in [sid for sid, job in self._jobs.items() if job.finished_at and job.finished_at < cutoff]:
            self._jobs.pop(sid, None)
        try:
            for child in self.work_dir.iterdir():
                if SEARCH_ID.match(child.name) and child.is_dir() and child.stat().st_mtime < cutoff:
                    if child.name not in self._jobs:
                        shutil.rmtree(child, ignore_errors=True)
        except OSError:
            logger.warning("Could not scan similarity job directory %s", self.work_dir)

    def _load_locked(self, search_id: str) -> _Job | None:
        path = self.work_dir / search_id / "result.json"
        try:
            if time.time() - path.stat().st_mtime > RESULT_TTL_SECONDS:
                return None
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("index_version") != self.index_version:
                return None
        except (OSError, ValueError):
            return None
        job = _Job(search_id, [], saved["params"], path.parent)
        job.status = "done"
        job.submitted_at, job.started_at, job.finished_at = saved["submitted"], saved["started"], saved["finished"]
        job.query_summary = saved["queries"]
        job.results = saved["results"]
        job.done.set()
        self._jobs[search_id] = job
        return job

    @staticmethod
    def _subprocess(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)

    def _script(self, job: _Job) -> str:
        q = shlex.quote
        sens = SENSITIVITY[job.params["sensitivity"]]
        lines = ["#!/bin/bash", "set -euo pipefail", f"cd {q(str(job.job_dir))}", f"date +%s.%N > {STARTED_MARKER}"]
        for name in DATABASES:
            lines.append(
                " ".join(
                    [
                        q(self.mmseqs), "easy-search", "query.faa", q(str(self.root / "db" / name)),
                        f"{name}.m8", f"tmp_{name}", "--threads", str(THREADS), "-s", f"{sens:g}",
                        "-e", f"{job.params['evalue']:g}", "--max-seqs", str(MAX_SEQS), "--db-load-mode", "2",
                        "--format-output", FORMAT, f"> {name}.log 2>&1",
                    ]
                )
            )
        lines.append("rm -rf tmp_base tmp_long")
        return "\n".join(lines) + "\n"

    def _argv(self, job: _Job) -> list[str]:
        script = str(job.job_dir / "search.sh")
        if self.runner == "local":
            return ["bash", script]
        return [
            "sbatch", "--wait", "--parsable", "--job-name=gagent-simsearch", f"--partition={self.partition}",
            f"--cpus-per-task={THREADS}", f"--mem={self.slurm_mem}", f"--time={SLURM_TIME_LIMIT}",
            f"--output={job.job_dir / 'slurm.log'}", script,
        ]

    def _run(self, job: _Job) -> None:
        with self._slots:
            try:
                if job.job_dir.exists():
                    shutil.rmtree(job.job_dir)
                job.job_dir.mkdir(parents=True)
                with (job.job_dir / "query.faa").open("w", encoding="ascii") as fasta:
                    for index, (_, seq) in enumerate(job.queries):
                        fasta.write(f">q{index}\n{seq}\n")
                (job.job_dir / "search.sh").write_text(self._script(job), encoding="utf-8")
                if not self._warm_ready.wait(WARM_WAIT_SECONDS):
                    raise RuntimeError("index_loading")
                launched = time.time()
                if self.runner == "local":
                    job.started_at, job.status = launched, "running"
                job.dispatched = True
                try:
                    proc = self._run_command(self._argv(job), JOB_TIMEOUT_SECONDS)
                finally:
                    self._refresh(job)
                if proc.returncode != 0:
                    logger.error("Similarity search %s failed (rc=%s): %s", job.search_id, proc.returncode, (proc.stderr or "")[-500:])
                    raise RuntimeError("timeout" if self._hit_time_limit(job) else "mmseqs run failed")
                hits = []
                for name in DATABASES:
                    hits += self._read_m8(job, job.job_dir / f"{name}.m8", name)
                job.results = self._expand(job, hits)
                job.finished_at, job.status = time.time(), "done"
                self._save(job)
            except Exception as exc:  # report a failed search, never crash the server thread
                timed_out = isinstance(exc, subprocess.TimeoutExpired) or str(exc) == "timeout"
                if not isinstance(exc, (RuntimeError, subprocess.TimeoutExpired)):
                    logger.exception("Similarity search %s failed", job.search_id)
                job.finished_at, job.status = time.time(), "failed"
                if str(exc) == "index_loading":
                    job.error = {"code": "index_loading", "message": "The similarity index is still loading into memory; retry in about 15 minutes."}
                elif timed_out:
                    job.error = {"code": "search_timeout", "message": "The similarity search hit its run time limit; retry later or with fewer sequences."}
                else:
                    job.error = {"code": "search_failed", "message": "The similarity search did not complete; retry later."}
            finally:
                job.queries = []
                job.done.set()
                t = _timings(job)
                logger.info(
                    "Similarity search %s %s queued=%ss run=%ss total=%ss",
                    job.search_id, job.status, t["queued_seconds"], t["run_seconds"], t["total_seconds"],
                )

    @staticmethod
    def _hit_time_limit(job: _Job) -> bool:
        try:
            return "DUE TO TIME LIMIT" in (job.job_dir / "slurm.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

    def _read_m8(self, job: _Job, path: Path, target_db: str) -> list[dict[str, Any]]:
        ids = [qid for qid, _ in job.queries]
        rows = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 9 or not parts[0].startswith("q"):
                    continue
                rows.append(
                    {
                        "query_id": ids[int(parts[0][1:])],
                        "seq_md5": parts[1],
                        "identity": float(parts[2]),
                        "aln_len": int(parts[3]),
                        "qcov": float(parts[4]),
                        "tcov": float(parts[5]),
                        "evalue": float(parts[6]),
                        "bits": float(parts[7]),
                        "target_length": int(parts[8]),
                        "target_db": target_db,
                    }
                )
        return rows

    def _expand(self, job: _Job, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        params = job.params
        wanted = set(params["datasets"])
        by_query: dict[str, list[dict[str, Any]]] = {}
        member_cache: dict[str, list[tuple[str, str]]] = {}
        with closing(self._members()) as conn:
            for hit in sorted(hits, key=lambda h: (h["evalue"], -h["bits"])):
                if hit["identity"] < params["min_identity"]:
                    continue
                if min(hit["qcov"], hit["tcov"]) < params["min_coverage"]:
                    continue
                bucket = by_query.setdefault(hit["query_id"], [])
                if len(bucket) >= params["max_hits"]:
                    continue
                md5 = hit["seq_md5"]
                if md5 not in member_cache:
                    member_cache[md5] = conn.execute(
                        "SELECT src, pid FROM members WHERE md5 = ? ORDER BY (src LIKE 'LQ\\_%' ESCAPE '\\'), src, pid",
                        (md5,),
                    ).fetchall()
                members = member_cache[md5]
                if params["exclude_low_quality"]:
                    members = [m for m in members if not m[0].startswith("LQ_")]
                if wanted:
                    members = [m for m in members if m[0].casefold() in wanted]
                if not members:
                    continue
                datasets = sorted({m[0] for m in members})
                bucket.append(
                    {
                        **hit,
                        "rep_protein_id": members[0][1],
                        "member_count": len(members),
                        "datasets": datasets,
                        "low_quality": all(d.startswith("LQ_") for d in datasets),
                        "sample_protein_ids": [m[1] for m in members[:SAMPLE_MEMBERS]],
                    }
                )
        flat: list[dict[str, Any]] = []
        for summary in job.query_summary:
            bucket = by_query.get(summary["query_id"], [])
            summary["hits"] = len(bucket)
            flat.extend(bucket)
        if self.family_lookup is not None and flat:
            try:
                families = self.family_lookup(sorted({row["rep_protein_id"] for row in flat}))
            except Exception:
                logger.exception("Family lookup for similarity hits failed")
                families = {}
            for row in flat:
                row["family_id"] = families.get(row["rep_protein_id"])
        return flat

    def _save(self, job: _Job) -> None:
        saved = {
            "index_version": self.index_version,
            "params": job.params,
            "submitted": job.submitted_at,
            "started": job.started_at,
            "finished": job.finished_at,
            "queries": job.query_summary,
            "results": job.results,
        }
        tmp = job.job_dir / "result.json.tmp"
        tmp.write_text(json.dumps(saved, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, job.job_dir / "result.json")
        (job.job_dir / "query.faa").unlink(missing_ok=True)
