#!/usr/bin/env python3
"""Host-side runner for native bio_tools runs (Slurm + environment modules).

Runs as an ordinary user on the machine that has the bioinformatics software, outside the app
container. It watches ``<runtime-dir>/_native/runs/<run_id>/request.json``, validates each request
with ``native_exec.build_job``, submits the rendered command with ``sbatch`` and keeps
``status.json`` up to date. It never runs anything a request supplies as a command.

Install: copy ``native_runner.py``, ``native_exec.py`` and ``tools_config.json`` into one directory
and run ``python3 native_runner.py --runtime-dir ... --read-root ...``. Python 3.8, standard library
only.
"""

import argparse
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

try:  # installed layout: next to this file
    import native_exec
except ImportError:  # imported from the repository
    from tool_box.bio_tools import native_exec  # type: ignore

logger = logging.getLogger("gagent.bio_native_runner")

RUNNER_VERSION = 1
MAX_REQUEST_BYTES = 256 * 1024
HEARTBEAT_SECONDS = 5.0
SLURM_TIMEOUT_SECONDS = 30
VANISHED_GRACE_SECONDS = 90

_SLURM_ACTIVE = {"PENDING": "submitted", "CONFIGURING": "submitted", "REQUEUED": "submitted",
                 "RUNNING": "running", "COMPLETING": "running", "SUSPENDED": "running", "RESIZING": "running"}
_SLURM_FINAL = {"COMPLETED": "succeeded", "FAILED": "failed", "CANCELLED": "cancelled", "TIMEOUT": "timeout",
                "OUT_OF_MEMORY": "failed", "NODE_FAIL": "failed", "BOOT_FAIL": "failed", "DEADLINE": "timeout",
                "PREEMPTED": "failed"}


def _now() -> float:
    return time.time()


def _mkdir_open(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    os.chmod(path, 0o777)


class Runner:
    def __init__(
        self,
        runtime_dir: str,
        config_path: str,
        read_roots: List[str],
        *,
        db_root: Optional[str] = None,
        conda_base: Optional[str] = None,
        max_active: int = 8,
        partition: Optional[str] = None,
        sbatch: str = "sbatch",
        squeue: str = "squeue",
        sacct: str = "sacct",
        scancel: str = "scancel",
    ) -> None:
        self.runtime_dir = os.path.realpath(runtime_dir)
        self.config_path = config_path
        self.read_roots = [os.path.realpath(r) for r in read_roots] + [self.runtime_dir]
        self.db_root = os.path.realpath(db_root) if db_root else None
        self.conda_base = conda_base
        self.max_active = max_active
        self.partition = partition
        self.bins = {"sbatch": sbatch, "squeue": squeue, "sacct": sacct, "scancel": scancel}
        self.host = socket.gethostname()
        self.started_at = _now()
        self._last_beat = 0.0
        self._config = {}  # type: Dict[str, Any]
        self._config_mtime = -1.0
        self.active = {}  # type: Dict[str, Dict[str, Any]]
        self._runs = native_exec.runs_root(self.runtime_dir)

    # ------------------------------------------------------------------ setup

    def ensure_dirs(self) -> None:
        _mkdir_open(native_exec.native_root(self.runtime_dir))
        _mkdir_open(self._runs)

    def config(self) -> Dict[str, Any]:
        try:
            mtime = os.path.getmtime(self.config_path)
        except OSError:
            return self._config
        if mtime != self._config_mtime:
            self._config = native_exec.load_tools_config(self.config_path)
            self._config_mtime = mtime
        return self._config

    def recover(self) -> None:
        """Pick up runs that were submitted before this process started."""
        for run_id, run_dir in self._run_dirs():
            status = native_exec.read_json(os.path.join(run_dir, native_exec.STATUS_FILENAME))
            if status and status.get("state") not in native_exec.STATES_TERMINAL and status.get("slurm_job_id"):
                self.active[run_id] = status
        logger.info("recovered %d active run(s)", len(self.active))

    # ------------------------------------------------------------------ loop

    def tick(self) -> None:
        self.heartbeat()
        self.claim_new()
        self.poll_active()

    def serve(self, interval: float, stop: "Optional[List[bool]]" = None) -> None:
        self.ensure_dirs()
        self.recover()
        while not (stop and stop[0]):
            try:
                self.tick()
            except Exception:  # keep serving; the next tick retries
                logger.exception("tick failed")
            time.sleep(interval)

    def heartbeat(self, force: bool = False) -> None:
        if not force and _now() - self._last_beat < HEARTBEAT_SECONDS:
            return
        self._last_beat = _now()
        native_exec.write_json_atomic(
            native_exec.heartbeat_path(self.runtime_dir),
            {"version": RUNNER_VERSION, "protocol": native_exec.PROTOCOL_VERSION, "pid": os.getpid(),
             "host": self.host, "ts": self._last_beat, "started_at": self.started_at,
             "active": len(self.active), "max_active": self.max_active},
        )

    # ------------------------------------------------------------------ intake

    def _run_dirs(self):
        found = []
        try:
            with os.scandir(self._runs) as entries:
                for entry in entries:
                    try:
                        if native_exec.RUN_ID_RE.match(entry.name) and entry.is_dir(follow_symlinks=False):
                            found.append((entry.stat().st_mtime, entry.name, entry.path))
                    except OSError:
                        continue  # removed while we looked
        except OSError:
            return
        for _, name, path in sorted(found):
            yield name, path

    def claim_new(self) -> None:
        for run_id, run_dir in self._run_dirs():
            if len(self.active) >= self.max_active:
                return
            if run_id in self.active:
                continue
            if os.path.exists(os.path.join(run_dir, native_exec.STATUS_FILENAME)):
                continue
            if not os.path.isfile(os.path.join(run_dir, native_exec.REQUEST_FILENAME)):
                continue
            self._claim(run_id, run_dir)

    def _reject(self, run_dir: str, error: str, code: str) -> None:
        logger.warning("rejected %s: %s", os.path.basename(run_dir), error)
        native_exec.write_json_atomic(
            os.path.join(run_dir, native_exec.STATUS_FILENAME),
            {"state": "rejected", "error": error, "error_code": code, "host": self.host, "ended_at": _now()},
        )

    def _database_path(self, tool: str) -> Optional[str]:
        spec = native_exec.native_spec(self.config(), tool) or {}
        db = spec.get("database")
        if not isinstance(db, dict):
            return None
        path = os.environ.get(str(db.get("env") or "")) if db.get("env") else None
        if not path and self.db_root and db.get("subdir"):
            path = os.path.join(self.db_root, str(db["subdir"]))
        if not path:
            return None
        real = os.path.realpath(path)
        try:
            if os.path.isdir(real) and any(os.scandir(real)):
                return real
        except OSError:
            pass
        return None

    def _claim(self, run_id: str, run_dir: str) -> None:
        request_path = os.path.join(run_dir, native_exec.REQUEST_FILENAME)
        try:
            if os.path.getsize(request_path) > MAX_REQUEST_BYTES:
                return self._reject(run_dir, "request is too large", "invalid_request")
        except OSError:
            return
        request = native_exec.read_json(request_path)
        if request is None:
            try:
                age = _now() - os.path.getmtime(request_path)
            except OSError:
                return
            if age > 60:
                self._reject(run_dir, "the request is not valid JSON", "invalid_request")
            return  # may still be being written
        if request.get("version") != native_exec.PROTOCOL_VERSION or request.get("run_id") != run_id:
            return self._reject(run_dir, "unsupported request", "invalid_request")
        tool = str(request.get("tool") or "")
        operation = str(request.get("operation") or "")
        input_file, output_file, params = native_exec.split_request_paths(request)
        work_dir = os.path.join(run_dir, native_exec.WORK_DIRNAME)
        try:
            _mkdir_open(work_dir)
            job = native_exec.build_job(
                self.config(), tool=tool, operation=operation, input_file=input_file, output_file=output_file,
                params=params, timeout=request.get("timeout"), work_dir=work_dir, read_roots=self.read_roots,
                database_path=self._database_path(tool),
            )
            script = native_exec.render_script(job, run_dir=run_dir, conda_base=self.conda_base)
        except native_exec.NativeError as exc:
            return self._reject(run_dir, str(exc), exc.code)
        except (OSError, TypeError, ValueError) as exc:
            return self._reject(run_dir, "could not prepare the run: %s" % exc, "invalid_request")

        script_path = os.path.join(run_dir, native_exec.SCRIPT_FILENAME)
        with open(script_path, "w", encoding="utf-8") as handle:
            handle.write(script)
        os.chmod(script_path, 0o644)

        args = [
            self.bins["sbatch"], "--parsable", "--job-name=gagent-%s-%s" % (tool, run_id),
            "--chdir=%s" % job["work_dir"],
            "--output=%s" % os.path.join(run_dir, native_exec.STDOUT_FILENAME),
            "--error=%s" % os.path.join(run_dir, native_exec.STDERR_FILENAME),
            "--cpus-per-task=%d" % job["cpus"], "--mem=%dM" % job["mem_mb"],
            "--time=%s" % native_exec.slurm_time(job["time_limit_seconds"]), "--export=NONE",
        ]
        partition = job.get("partition") or self.partition
        if partition:
            args.append("--partition=%s" % partition)
        args.append(script_path)  # sbatch reads the file; its #! line picks the interpreter
        try:
            done = subprocess.run(args, capture_output=True, text=True, timeout=SLURM_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._reject(run_dir, "sbatch failed: %s" % exc, "scheduler_error")
        job_id = (done.stdout or "").strip().split(";")[0]
        if done.returncode != 0 or not re.fullmatch(r"\d+", job_id):
            return self._reject(run_dir, "sbatch failed: %s" % (done.stderr or done.stdout).strip()[:500],
                                "scheduler_error")
        status = {
            "state": "submitted", "slurm_job_id": job_id, "host": self.host, "command": job["command"],
            "modules": job["modules"], "cpus": job["cpus"], "mem_mb": job["mem_mb"],
            "time_limit_seconds": job["time_limit_seconds"], "work_dir": job["work_dir"],
            "output_path": job["output_path"], "submitted_at": _now(),
        }
        native_exec.write_json_atomic(os.path.join(run_dir, native_exec.STATUS_FILENAME), status)
        self.active[run_id] = status
        logger.info("submitted %s %s.%s as slurm job %s", run_id, tool, operation, job_id)

    # ------------------------------------------------------------------ progress

    def _run(self, name: str, *args: str) -> Optional[subprocess.CompletedProcess]:
        try:
            return subprocess.run([self.bins[name]] + list(args), capture_output=True, text=True,
                                  timeout=SLURM_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("%s failed: %s", name, exc)
            return None

    def poll_active(self) -> None:
        if not self.active:
            return
        for run_id, status in list(self.active.items()):
            run_dir = os.path.join(self._runs, run_id)
            if os.path.exists(os.path.join(run_dir, native_exec.CANCEL_FILENAME)) and not status.get("cancel_sent"):
                self._run("scancel", str(status["slurm_job_id"]))
                status["cancel_sent"] = True
        ids = ",".join(str(s["slurm_job_id"]) for s in self.active.values())
        listed = {}  # type: Dict[str, str]
        done = self._run("squeue", "-h", "-j", ids, "-o", "%i|%T")
        if done is not None and done.returncode == 0:
            for line in (done.stdout or "").splitlines():
                job_id, _, state = line.partition("|")
                listed[job_id.strip()] = state.strip()
        elif done is None:
            return  # the scheduler did not answer; keep what we know
        for run_id, status in list(self.active.items()):
            job_id = str(status["slurm_job_id"])
            run_dir = os.path.join(self._runs, run_id)
            state = listed.get(job_id) or self._accounting_state(job_id)
            if state is None:
                if _now() - float(status.get("submitted_at") or 0) > VANISHED_GRACE_SECONDS:
                    self._finish(run_id, run_dir, status, "failed", error="the scheduler lost the job")
                continue
            if state in _SLURM_ACTIVE:
                mapped = _SLURM_ACTIVE[state]
                if mapped != status.get("state"):
                    status["state"] = mapped
                    if mapped == "running":
                        status.setdefault("started_at", _now())
                    native_exec.write_json_atomic(os.path.join(run_dir, native_exec.STATUS_FILENAME), status)
                continue
            base = state.split()[0]
            final = _SLURM_FINAL.get(base)
            if final is None:
                continue
            self._finish(run_id, run_dir, status, final)

    def _accounting_state(self, job_id: str) -> Optional[str]:
        done = self._run("sacct", "-n", "-P", "-X", "-j", job_id, "-o", "State")
        if done is None or done.returncode != 0:
            return None
        lines = [line.strip() for line in (done.stdout or "").splitlines() if line.strip()]
        return lines[0] if lines else None

    def _finish(self, run_id: str, run_dir: str, status: Dict[str, Any], state: str,
                error: Optional[str] = None) -> None:
        exit_code = None  # type: Optional[int]
        try:
            with open(os.path.join(run_dir, native_exec.EXIT_CODE_FILENAME), "r", encoding="utf-8") as handle:
                exit_code = int(handle.read().strip())
        except (OSError, ValueError):
            pass
        if state == "succeeded" and exit_code != 0:
            # The script records its exit code as its last step; a success without it cannot be trusted.
            state = "failed"
            error = error or "the run ended without recording its exit code"
        status.update({"state": state, "ended_at": _now()})
        if exit_code is not None:
            status["exit_code"] = exit_code
        if error:
            status["error"] = error
        elif state == "timeout":
            status["error"] = "the run reached its time limit"
        elif state == "failed" and exit_code in (96, 97):
            status["error"] = "the software environment could not be loaded on the server"
        native_exec.write_json_atomic(os.path.join(run_dir, native_exec.STATUS_FILENAME), status)
        self.active.pop(run_id, None)
        logger.info("run %s finished: %s (exit %s)", run_id, state, exit_code)


def main(argv: Optional[List[str]] = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", required=True, help="host path of the bio_tools runtime directory")
    parser.add_argument("--config", default=os.path.join(here, "tools_config.json"))
    parser.add_argument("--read-root", action="append", default=[], help="host directory inputs may be read from")
    parser.add_argument("--db-root", default=None, help="host directory holding reference databases")
    parser.add_argument("--conda-base", default=None)
    parser.add_argument("--max-active", type=int, default=8)
    parser.add_argument("--partition", default=None)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--once", action="store_true", help="run a single iteration and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    os.umask(0)
    runner = Runner(args.runtime_dir, args.config, args.read_root, db_root=args.db_root,
                    conda_base=args.conda_base, max_active=args.max_active, partition=args.partition)
    runner.ensure_dirs()
    if args.once:
        runner.recover()
        runner.heartbeat(force=True)
        runner.tick()
        return 0
    stop = [False]

    def _stop(signum: int, frame: Any) -> None:
        stop[0] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    runner.heartbeat(force=True)
    runner.serve(args.interval, stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
