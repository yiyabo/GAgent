"""Durable ownership evidence for interrupted Hermes evaluation processes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time


def write_process_state(root, state):
    path = Path(root) / "hermes-process-state.json"
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def identity(process):
    return {"pid": process.pid, "create_time": process.create_time()}


def owned_process(record):
    """A recycled PID is no longer ours; never signal it."""
    import psutil
    if not isinstance(record, dict) or not isinstance(record.get("pid"), int) or not isinstance(record.get("create_time"), (int, float)):
        raise ValueError("invalid process identity")
    try:
        process = psutil.Process(record["pid"])
        if process.create_time() != record["create_time"]:
            return None
        return process if process.is_running() and process.status() != psutil.STATUS_ZOMBIE else None
    except psutil.NoSuchProcess:
        return None


def recover_hermes_processes(root):
    """Recover this trial only. Missing ownership evidence is explicitly unknown."""
    import psutil
    root = Path(root)
    path = root / "hermes-process-state.json"
    unknown = {"cleanup_verified": False, "status": "cleanup_unverified", "remaining_pids": None}
    try:
        state = json.loads(path.read_text())
        if not isinstance(state, dict) or state.get("schema_version") != 1 or not isinstance(state.get("children"), list) or "terminal" not in state:
            raise ValueError("invalid process state")
        worker_record = state.get("worker")
        if worker_record is None and state.get("phase") != "launch_failed":
            raise ValueError("launch ownership was not persisted")
        records = list(state["children"])
        if worker_record:
            records.append(worker_record)
        live = {}
        for record in records:
            process = owned_process(record)
            if process:
                live[(process.pid, process.create_time())] = process
        worker = owned_process(worker_record) if worker_record else None
        if worker:
            for child in worker.children(recursive=True):
                live[(child.pid, child.create_time())] = child
        # Persist any newly discovered children before signalling; another recovery
        # can continue if this one is itself interrupted.
        recorded = {(r["pid"], r["create_time"]): r for r in state["children"]}
        recorded.update({key: identity(proc) for key, proc in live.items() if not worker or proc.pid != worker.pid})
        state.update(children=list(recorded.values()), terminal=False, phase="recovering", recovery_at=time.time())
        write_process_state(root, state)
        for process in live.values():
            try:
                process.kill()  # psutil rechecks cached creation time before the signal.
            except psutil.NoSuchProcess:
                pass
        _, waiting = psutil.wait_procs(list(live.values()), timeout=2)
        remaining = []
        for process in waiting:
            current = owned_process(identity(process))
            if current:
                remaining.append(process.pid)
        result = {"cleanup_verified": not remaining, "status": "cleanup_verified" if not remaining else "cleanup_unverified",
                  "remaining_pids": remaining, "recovered_processes": len(live),
                  "elapsed_seconds": state.get("elapsed_seconds"), "result_present": (root / "result.json").is_file(),
                  "tracking_scope": "persisted_and_recovered_descendants_with_pid_creation_time"}
        state.update(terminal=not remaining, phase="terminal" if not remaining else "recovery_incomplete", cleanup_status=result)
        write_process_state(root, state)
        return result
    except (OSError, ValueError, TypeError, KeyError, psutil.Error) as exc:
        return {**unknown, "reason": type(exc).__name__, "state_present": path.exists()}
