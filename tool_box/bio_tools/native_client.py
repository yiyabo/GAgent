"""App-side client for native bio_tools runs.

The app container hands a request to the host runner through the shared runtime directory and waits
for ``status.json``. Nothing here needs SSH, a Docker socket or credentials: the only channel is the
directory both sides already mount (``/app/runtime`` in the container is ``$HOST_RUNTIME_ROOT`` on the
host).
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import native_exec

LogCallback = Callable[[str, str], None]
ProgressReport = Callable[[str, str], Any]

_TAIL_BYTES = 200_000


class PathMap:
    """Container path -> host path for the directories the runner can read."""

    def __init__(self, pairs: List[Tuple[str, str]]) -> None:
        self.pairs = sorted(
            ((c.rstrip("/") or "/", h.rstrip("/") or "/") for c, h in pairs if c and h),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )

    @classmethod
    def from_env(cls, container_runtime_root: str) -> "PathMap":
        pairs: List[Tuple[str, str]] = []
        host_runtime = (os.getenv("BIO_TOOLS_NATIVE_HOST_RUNTIME_ROOT") or os.getenv("HOST_RUNTIME_ROOT") or "").strip()
        pairs.append((container_runtime_root, host_runtime or container_runtime_root))
        refdata = (os.getenv("GAGENT_REFDATA_DIR") or "").strip()
        if refdata:
            pairs.append(("/data", refdata))
        for item in (os.getenv("BIO_TOOLS_NATIVE_PATH_MAP") or "").split(";"):
            container, sep, host = item.partition("=")
            if sep and container.strip() and host.strip():
                pairs.append((container.strip(), host.strip()))
        return cls(pairs)

    def to_host(self, path: str) -> str:
        text = str(path)
        for container, host in self.pairs:
            if text == container or text.startswith(container + "/"):
                return host + text[len(container):]
        for _, host in self.pairs:  # already a host path
            if text == host or text.startswith(host + "/"):
                return text
        raise native_exec.NativeError(
            "%s is not in a directory the host runner can read" % text, "path_not_allowed"
        )

    def to_container(self, path: str) -> str:
        text = str(path)
        for container, host in self.pairs:
            if text == host or text.startswith(host + "/"):
                return container + text[len(host):]
        return text


def _heartbeat_max_age() -> float:
    try:
        return float(os.getenv("BIO_TOOLS_NATIVE_HEARTBEAT_MAX_AGE", "30"))
    except ValueError:
        return 30.0


def runner_unavailable_reason(runtime_dir: str) -> Optional[str]:
    """None when a runner has reported in recently, otherwise why not."""
    beat = native_exec.read_json(native_exec.heartbeat_path(runtime_dir))
    if beat is None:
        return "the native runner has not started on this server"
    age = time.time() - float(beat.get("ts") or 0)
    if age > _heartbeat_max_age():
        return "the native runner has not reported for %d seconds" % int(age)
    return None


def _translate_params(params: Dict[str, Any], path_map: PathMap) -> Dict[str, Any]:
    """Container paths in path-like parameters become host paths; everything else is untouched."""
    translated: Dict[str, Any] = {}
    path_keys = native_exec.INPUT_PATH_PARAM_KEYS | native_exec.OUTPUT_PATH_PARAM_KEYS
    for key, value in params.items():
        if key in path_keys and isinstance(value, str) and value.startswith("/") and not value.startswith("/work"):
            if key in native_exec.MULTI_PATH_PARAM_KEYS:
                translated[key] = ",".join(path_map.to_host(p.strip()) for p in value.split(",") if p.strip())
            else:
                translated[key] = path_map.to_host(value)
        else:
            translated[key] = value
    return translated


def _read_tail(path: str, limit: int = _TAIL_BYTES) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


class _LineTail:
    """Forward lines that appeared in a growing file since the last call."""

    def __init__(self, path: str, stream: str, callback: Optional[LogCallback]) -> None:
        self.path, self.stream, self.callback, self.offset = path, stream, callback, 0
        self.partial = b""

    def poll(self) -> None:
        if self.callback is None:
            return
        try:
            with open(self.path, "rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read(1_000_000)
        except OSError:
            return
        if not chunk:
            return
        self.offset += len(chunk)
        data = self.partial + chunk
        *lines, self.partial = data.split(b"\n")
        for raw in lines:
            line = raw.decode("utf-8", errors="replace").rstrip("\r")
            if line.strip():
                try:
                    self.callback(line, self.stream)
                except Exception:
                    pass


def _failure(tool: str, operation: str, error: str, code: str, **extra: Any) -> Dict[str, Any]:
    result = {
        "success": False, "tool": tool, "operation": operation, "error": error, "error_code": code,
        "execution_mode": "native", "execution_host": "native",
    }
    result.update(extra)
    return result


async def run_native_bio_tool(
    *,
    config: Dict[str, Any],
    runtime_dir: str,
    container_runtime_root: str,
    tool_name: str,
    operation: str,
    input_file: Optional[str],
    output_file: Optional[str],
    params: Optional[Dict[str, Any]],
    timeout: Optional[int],
    report: Optional[ProgressReport] = None,
    log_callback: Optional[LogCallback] = None,
    path_map: Optional[PathMap] = None,
) -> Dict[str, Any]:
    """Hand one run to the host runner and wait for it. ``runtime_dir`` is the bio_tools runtime
    directory as this process sees it."""

    async def progress(stage: str, message: str) -> None:
        if report is not None:
            await report(stage, message)

    if not native_exec.supports_native(config, tool_name, operation):
        return _failure(tool_name, operation, "%s.%s has no native implementation" % (tool_name, operation),
                        "not_native")
    reason = runner_unavailable_reason(runtime_dir)
    if reason:
        return _failure(tool_name, operation, "Native execution is unavailable: %s." % reason,
                        "native_runner_unavailable", fallback_ok=True)

    mapping = path_map or PathMap.from_env(container_runtime_root)
    run_id = uuid.uuid4().hex[:12]
    run_dir = os.path.join(native_exec.runs_root(runtime_dir), run_id)
    try:
        host_input = mapping.to_host(input_file) if input_file else None
        host_params = _translate_params(dict(params or {}), mapping)
    except native_exec.NativeError as exc:
        return _failure(tool_name, operation, str(exc), exc.code, run_id=run_id)

    try:
        os.makedirs(native_exec.runs_root(runtime_dir), exist_ok=True)
        os.mkdir(run_dir)
        os.chmod(run_dir, 0o777)  # the runner is a different user and writes status and outputs here
        native_exec.write_json_atomic(
            os.path.join(run_dir, native_exec.REQUEST_FILENAME),
            {
                "version": native_exec.PROTOCOL_VERSION, "run_id": run_id, "tool": tool_name,
                "operation": operation, "input_file": host_input, "output_file": output_file,
                "params": host_params, "timeout": timeout, "submitted_at": time.time(),
            },
            mode=0o644,
        )
    except OSError as exc:
        return _failure(tool_name, operation, "Could not hand the run to the native runner: %s" % exc,
                        "native_handoff_failed", run_id=run_id)

    await progress("started", "Queued %s %s for the server's native tools" % (tool_name, operation))
    status_path = os.path.join(run_dir, native_exec.STATUS_FILENAME)
    cancel_path = os.path.join(run_dir, native_exec.CANCEL_FILENAME)
    out_tail = _LineTail(os.path.join(run_dir, native_exec.STDOUT_FILENAME), "stdout", log_callback)
    err_tail = _LineTail(os.path.join(run_dir, native_exec.STDERR_FILENAME), "stderr", log_callback)
    poll = float(os.getenv("BIO_TOOLS_NATIVE_POLL_INTERVAL", "1.5"))
    queue_timeout = float(os.getenv("BIO_TOOLS_NATIVE_QUEUE_TIMEOUT", "3600"))
    started = time.monotonic()
    last_state: Optional[str] = None
    cancelled_for_queue = False

    def request_cancel() -> None:
        try:
            Path(cancel_path).touch(mode=0o666)
        except OSError:
            pass

    try:
        while True:
            status = native_exec.read_json(status_path)
            state = status.get("state") if status else None
            out_tail.poll()
            err_tail.poll()
            if state != last_state:
                last_state = state
                if state == "submitted":
                    await progress("running", "Waiting for a slot on the server's scheduler")
                elif state == "running":
                    await progress("running", "Running %s %s on %s" % (tool_name, operation, status.get("host", "the server")))
            if state in native_exec.STATES_TERMINAL:
                break
            if state in (None, "submitted") and time.monotonic() - started > queue_timeout and not cancelled_for_queue:
                cancelled_for_queue = True
                request_cancel()
            await asyncio.sleep(poll)
    except asyncio.CancelledError:
        request_cancel()
        raise

    out_tail.poll()
    err_tail.poll()
    duration = time.monotonic() - started
    work_dir = status.get("work_dir") or os.path.join(run_dir, native_exec.WORK_DIRNAME)
    result: Dict[str, Any] = {
        "success": state == "succeeded",
        "tool": tool_name,
        "operation": operation,
        "run_id": run_id,
        "state": state,
        "command": status.get("command"),
        "exit_code": status.get("exit_code"),
        "duration_seconds": duration,
        "stdout": _read_tail(os.path.join(run_dir, native_exec.STDOUT_FILENAME)),
        "stderr": _read_tail(os.path.join(run_dir, native_exec.STDERR_FILENAME)),
        "execution_mode": "native",
        "execution_host": status.get("host") or "native",
        "slurm_job_id": status.get("slurm_job_id"),
        "work_dir": mapping.to_container(work_dir),
    }
    output_path = status.get("output_path")
    if output_path and state == "succeeded":
        result["output_path"] = mapping.to_container(output_path)
    if state != "succeeded":
        result["error"] = status.get("error") or (
            "cancelled while waiting for a slot" if cancelled_for_queue else "%s.%s failed" % (tool_name, operation)
        )
        if status.get("error_code"):
            result["error_code"] = status["error_code"]
    await progress("completed" if state == "succeeded" else "failed", "%s %s %s" % (tool_name, operation, state))
    return result
