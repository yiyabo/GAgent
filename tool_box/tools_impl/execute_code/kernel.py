"""Persistent session kernels for execute_code.

One child process per ``(session_id, sorted(allowlist), cwd)`` key; one code
cell per execute_code call; a single global namespace survives across cells.
Contract copied from Hermes: a timed-out or interrupted cell kills the whole
process group and the state is LOST (a cell cannot be interrupted in place
safely) — the result says so explicitly and the next call starts fresh.

Wire protocol: one JSON request per stdin line ``{"id", "code"}``; replies
framed on stdout as ``<SENTINEL> <byte-length>\\n<json>`` with a per-kernel
random sentinel from the env. Bytes outside frames are raw fd output
attributed to the running cell. Tool RPC rides a separate loopback socket
(rpc.py); per-cell authority (CellBinding) is bound at cell start and retired
at settle, so late RPC from a leaked cell thread is refused.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import config
from .env_scrub import build_child_env
from .output import failure_hint, strip_ansi, truncate_stderr, truncate_stdout
from .rpc import KernelRPCServer
from .stub_gen import generate_stub_module

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Kernel runner template (written into the kernel dir via Path.write_text)
# ---------------------------------------------------------------------------

RUNNER_CELL_SOURCE = '''\
GLOBALS = {"__name__": "__main__", "__builtins__": __builtins__}


def _clip(text):
    return (text, False) if len(text) <= _CAPTURE_LIMIT else (text[:_CAPTURE_LIMIT], True)


def run_cell(request, execution_count):
    """Exec one cell in the persistent namespace; returns (payload, FULL stdout)."""
    out, err = io.StringIO(), io.StringIO()
    status, trace = "ok", ""
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(compile(request["code"], "<cell>", "exec"), GLOBALS)
    except SystemExit as exc:
        status, trace = "exit", "SystemExit: " + repr(exc.code)
    except BaseException:
        status, trace = "error", traceback.format_exc()
    stdout_text, stdout_clipped = _clip(out.getvalue())
    stderr_text, stderr_clipped = _clip(err.getvalue())
    return {
        "id": request.get("id", ""), "status": status,
        "stdout": stdout_text, "stderr": stderr_text,
        "stdout_clipped": stdout_clipped, "stderr_clipped": stderr_clipped,
        "traceback": trace, "execution_count": execution_count,
    }, out.getvalue()
'''

KERNEL_RUNNER_SOURCE = '''\
"""Auto-generated GAgent code-mode kernel runner. One exec cell per request."""
import contextlib
import io
import json
import os
import sys
import threading
import traceback

_BLOCKED_TOP_LEVEL_PACKAGES = ("app", "tool_box")


def _start_parent_watchdog():
    """Exit the kernel when the host process goes away, however it dies.

    The kernel is spawned with start_new_session=True, so it is not in the
    host's process group and never receives its signals: if the host is
    SIGKILLed or crashes, the kernel would keep running as an orphan. The host
    holds the write end of an inherited pipe, so EOF on our read end is proof
    the host is gone.
    """
    fd_raw = os.environ.get("GAGENT_KERNEL_PARENT_FD", "")
    if not fd_raw.isdigit():
        return
    fd = int(fd_raw)
    try:
        # Do not hand this fd to processes the cell spawns: a grandchild
        # holding the write end would delay the EOF past the host's death.
        os.set_inheritable(fd, False)
    except OSError:
        pass

    def _watch():
        try:
            while True:
                if not os.read(fd, 1):
                    break
        except OSError:
            pass
        sys.stderr.flush()
        os._exit(0)

    threading.Thread(target=_watch, daemon=True).start()


class _BlockedBackendPackageFinder:
    """Refuse imports of the host backend packages inside the kernel.

    Model-written cells must reach tools through the gagent_tools RPC stubs,
    not by importing app/tool_box internals (raw DB, settings, live provider
    keys). Third-party scientific packages (numpy/pandas/matplotlib) and the
    stdlib are unaffected — the match is on the top-level package name only.
    """

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in _BLOCKED_TOP_LEVEL_PACKAGES:
            raise ImportError(
                "importing the backend package %r is not allowed inside "
                "execute_code. Call tools via `from gagent_tools import ...` "
                "instead of reaching into backend internals." % fullname
            )
        return None


sys.meta_path.insert(0, _BlockedBackendPackageFinder())

_start_parent_watchdog()

_SENTINEL = os.environ["GAGENT_KERNEL_SENTINEL"]
_CAPTURE_LIMIT = {capture_limit}
_SPILL_DIR = os.environ.get("GAGENT_KERNEL_SPILL_DIR", "")
_SPILL_CAP = {spill_cap}

_real_stdout = sys.stdout

{cell_source}


def _spill(text, spill_name):
    """Best-effort: write the FULL clipped stdout into the kernel dir."""
    if not _SPILL_DIR:
        return ""
    try:
        spill_path = os.path.join(_SPILL_DIR, spill_name)
        with open(spill_path, "w", encoding="utf-8", errors="replace") as handle:
            handle.write(text[:_SPILL_CAP])
            if len(text) > _SPILL_CAP:
                handle.write("\\n\\n[... spill capped ...]")
        return spill_path
    except Exception:
        return ""


def _reply(payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _real_stdout.buffer.write(("\\n" + _SENTINEL + " " + str(len(body)) + "\\n").encode("utf-8"))
    _real_stdout.buffer.write(body)
    _real_stdout.buffer.flush()


def main():
    execution_count = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        execution_count += 1
        payload, full_stdout = run_cell(request, execution_count)
        payload["stdout_spill_path"] = (
            _spill(full_stdout, "cell_%06d_stdout.txt" % execution_count)
            if payload["stdout_clipped"] else ""
        )
        _reply(payload)
        if payload["status"] == "exit":
            break


if __name__ == "__main__":
    main()
'''.format(
    cell_source=RUNNER_CELL_SOURCE,
    capture_limit=config.RUNNER_CAPTURE_BYTES,
    spill_cap=config.MAX_SPILLED_STDOUT_BYTES,
)


# ---------------------------------------------------------------------------
# Per-cell authority
# ---------------------------------------------------------------------------


class CellBinding:
    """The ToolContext identity of exactly one execute_code cell.

    Interpreter state persists across cells; tool authority must not. Bound at
    cell start, retired at settle: a late RPC (leaked background thread in the
    kernel) is refused instead of running under a stale identity.
    """

    def __init__(self, tool_context: Optional[object]):
        self.tool_context = tool_context
        self.active = True

    def retire(self) -> None:
        self.active = False


class _BoundedBuffer:
    """Byte chunks capped at a total size; ``drain`` returns text and resets."""

    def __init__(self) -> None:
        self.chunks: List[bytes] = []
        self.total = 0

    def append(self, data: bytes, cap: int) -> None:
        keep = data[: max(0, cap - self.total)]
        if keep:
            self.chunks.append(keep)
            self.total += len(keep)

    def drain(self) -> str:
        chunks, self.chunks, self.total = self.chunks, [], 0
        return b"".join(chunks).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Kernel lifecycle
# ---------------------------------------------------------------------------


class SessionKernel:
    """One live kernel process plus its RPC server and reader threads."""

    def __init__(self, key: Tuple):
        self.key = key
        self.cell_lock = threading.Lock()  # one cell at a time per kernel
        self.proc: Optional[subprocess.Popen] = None
        self.kernel_dir: Optional[Path] = None
        self.rpc_token = ""
        self.sentinel = ""
        self.rpc_server: Optional[KernelRPCServer] = None
        self.allowlist: frozenset = frozenset()
        self.max_tool_calls = config.DEFAULT_MAX_TOOL_CALLS_PER_CELL
        self.tool_call_counter: List[int] = [0]
        self.authority: Optional[CellBinding] = None
        self.attached = 0
        self.response_q: "queue.Queue[dict]" = queue.Queue()
        self.raw, self.stderr = _BoundedBuffer(), _BoundedBuffer()
        self.execution_count = 0
        self.last_used = time.monotonic()
        # Write end of the parent-liveness pipe handed to the kernel.
        self.parent_fd_w: Optional[int] = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def dead(self) -> bool:
        """True only once a spawned process exited (proc=None is mid-spawn)."""
        return self.proc is not None and self.proc.poll() is not None

    def teardown(self) -> None:
        if self.rpc_server is not None:
            self.rpc_server.stop()
            self.rpc_server = None
        if self.alive():
            _kill_process_group(self.proc, escalate=True)
        self.proc = None
        if self.parent_fd_w is not None:
            # Closing the write end is what tells a *surviving* kernel the host
            # is done with it; the kill above already covers the normal path.
            try:
                os.close(self.parent_fd_w)
            except OSError:
                pass
            self.parent_fd_w = None
        if self.kernel_dir is not None:
            shutil.rmtree(self.kernel_dir, ignore_errors=True)
            self.kernel_dir = None


def _kill_process_group(proc: subprocess.Popen, escalate: bool = True) -> None:
    """SIGTERM the whole process group, then SIGKILL after the grace window."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    if not escalate:
        return
    deadline = time.monotonic() + config.kill_grace_seconds()
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("execute_code kernel pid %s unkillable", proc.pid)


_KERNELS: Dict[Tuple, SessionKernel] = {}
_REGISTRY_LOCK = threading.Lock()

# Keys whose kernel is gone for good: a timeout kill, an LRU/idle retirement, or
# an explicit reset. The next acquire for such a key starts a fresh kernel, and
# the result has to say so — `state_reset` used to come back False there, which
# contradicted the description's promise that kernel metadata always tells the
# truth (`tool.py`: "the result's kernel metadata ... always tells the truth").
# Consumed on the next acquire, so this stays bounded by the sessions that have
# lost state and not yet called back.
_STALE_KEYS: "OrderedDict[Tuple, None]" = OrderedDict()
_STALE_KEYS_MAX = 256


def _mark_state_lost(keys: "Iterable[Tuple]") -> None:
    """Caller holds _REGISTRY_LOCK."""
    for key in keys:
        _STALE_KEYS[key] = None
    while len(_STALE_KEYS) > _STALE_KEYS_MAX:
        _STALE_KEYS.popitem(last=False)


def _take_state_lost(key: Tuple) -> bool:
    """Caller holds _REGISTRY_LOCK."""
    if key in _STALE_KEYS:
        del _STALE_KEYS[key]
        return True
    return False


def _pop_idle_expired(now: float, idle_timeout: float) -> List[SessionKernel]:
    """Pop (caller holds _REGISTRY_LOCK) every idle-expired unattached kernel."""
    expired = [
        (key, _KERNELS.pop(key))
        for key in list(_KERNELS)
        if _KERNELS[key].attached == 0 and now - _KERNELS[key].last_used > idle_timeout
    ]
    _mark_state_lost(key for key, _kernel in expired)
    return [kernel for _key, kernel in expired]


def _acquire_kernel(key: Tuple, reset: bool) -> Tuple[SessionKernel, bool]:
    """Look up or register the kernel for *key*; returns (kernel, state_reset).

    Every acquire also sweeps idle-expired kernels and enforces the LRU cap;
    doomed kernels are popped under the lock and torn down outside it.
    """
    cap = config.max_kernels()
    idle_timeout = config.kernel_idle_seconds()
    with _REGISTRY_LOCK:
        expired = _pop_idle_expired(time.monotonic(), idle_timeout)
        kernel = _KERNELS.get(key)
        state_reset = kernel is not None and (reset or kernel.dead())
        if state_reset:
            dropped = _KERNELS.pop(key)
            if dropped.attached == 0:
                expired.append(dropped)
            kernel = None
        if kernel is None:
            if _take_state_lost(key):
                state_reset = True
            kernel = _KERNELS[key] = SessionKernel(key)
        kernel.last_used = time.monotonic()
        kernel.attached += 1
        by_age = sorted(
            (other for other in _KERNELS if other != key and _KERNELS[other].attached == 0),
            key=lambda other: _KERNELS[other].last_used,
        )
        evicted_keys = by_age[: max(0, len(_KERNELS) - cap)]
        _mark_state_lost(evicted_keys)
        expired.extend(_KERNELS.pop(other) for other in evicted_keys)
    for doomed in expired:
        doomed.teardown()
    return kernel, state_reset


def shutdown_all_kernels() -> None:
    """Kill every session kernel (atexit; also used by tests)."""
    with _REGISTRY_LOCK:
        doomed = [_KERNELS.pop(key) for key in list(_KERNELS)]
    for kernel in doomed:
        kernel.teardown()


def shutdown_code_mode_kernels() -> None:
    """Explicit app-shutdown hook: stop RPC threads, kill process groups, clear
    the registry. Idempotent and never raises — a single wedged kernel must not
    block the rest of the lifespan teardown (or mask later cleanup steps).
    """
    try:
        with _REGISTRY_LOCK:
            doomed = [_KERNELS.pop(key) for key in list(_KERNELS)]
    except Exception:  # noqa: BLE001 - shutdown must not raise
        logger.warning("code-mode kernel registry sweep failed", exc_info=True)
        return
    for kernel in doomed:
        try:
            kernel.teardown()
        except Exception:  # noqa: BLE001 - keep disposing the remaining kernels
            logger.warning("code-mode kernel teardown failed", exc_info=True)


def shutdown_kernels_for_session(session_id: str) -> None:
    """Dispose every kernel a session owns (key[0] is the session identity)."""
    with _REGISTRY_LOCK:
        doomed = [_KERNELS.pop(key) for key in list(_KERNELS) if key[0] == session_id]
    for kernel in doomed:
        kernel.teardown()


atexit.register(shutdown_all_kernels)


# --- background idle reaper -------------------------------------------------

_REAPER_STARTED = False
_REAPER_INTERVAL_FLOOR, _REAPER_INTERVAL_CEIL = 30.0, 300.0


def _reap_once() -> None:
    with _REGISTRY_LOCK:
        expired = _pop_idle_expired(time.monotonic(), config.kernel_idle_seconds())
    for doomed in expired:
        doomed.teardown()


def _ensure_background_reaper() -> None:
    global _REAPER_STARTED
    with _REGISTRY_LOCK:
        if _REAPER_STARTED:
            return
        _REAPER_STARTED = True
    threading.Thread(
        target=_background_reaper, daemon=True, name="gagent-code-mode-reaper"
    ).start()


def _background_reaper() -> None:
    while True:
        idle_timeout = config.kernel_idle_seconds()
        time.sleep(min(_REAPER_INTERVAL_CEIL, max(_REAPER_INTERVAL_FLOOR, idle_timeout / 6.0)))
        try:
            _reap_once()
        except Exception:
            logger.exception("code-mode kernel idle reaper pass failed")


# --- spawn ------------------------------------------------------------------


def _stdout_reader(kernel: SessionKernel) -> None:
    """Split the child's stdout into protocol frames and raw passthrough."""
    assert kernel.proc is not None and kernel.proc.stdout is not None
    stream = kernel.proc.stdout
    marker = ("\n" + kernel.sentinel + " ").encode("utf-8")

    def raw(data: bytes) -> None:
        kernel.raw.append(data, config.MAX_STDOUT_BYTES)

    buf = b""
    while True:
        # read1 returns as soon as any bytes arrive; a plain read(n) blocks
        # until n bytes or EOF and would sit on a complete small frame forever.
        chunk = stream.read1(4096)
        if not chunk:
            if buf:
                raw(buf)
            kernel.response_q.put({"status": "kernel-eof"})
            return
        buf += chunk
        while True:
            index = buf.find(marker)
            if index < 0:
                spill = buf[: -len(marker)] if len(buf) > len(marker) else b""
                if spill:
                    raw(spill)
                    buf = buf[len(spill):]
                break
            if index:
                raw(buf[:index])
            rest = buf[index + len(marker):]
            newline = rest.find(b"\n")
            if newline < 0:
                buf = buf[index:]
                break
            try:
                length = int(rest[:newline])
            except ValueError:
                raw(marker)
                buf = rest
                continue
            body = rest[newline + 1:]
            while len(body) < length:
                more = stream.read1(length - len(body))
                if not more:
                    kernel.response_q.put({"status": "kernel-eof"})
                    return
                body += more
            try:
                kernel.response_q.put(json.loads(body[:length].decode("utf-8", errors="replace")))
            except ValueError:
                kernel.response_q.put({"status": "protocol-error"})
            buf = body[length:]


def _stderr_reader(kernel: SessionKernel) -> None:
    assert kernel.proc is not None and kernel.proc.stderr is not None
    while chunk := kernel.proc.stderr.read1(4096):
        kernel.stderr.append(chunk, config.MAX_STDERR_BYTES)


def _spawn(
    kernel: SessionKernel,
    *,
    scratch_dir: Path,
    child_cwd: Path,
    allowlist: frozenset,
    max_tool_calls: int,
) -> None:
    kernel.kernel_dir = Path(scratch_dir) / "kernels" / uuid.uuid4().hex
    kernel.kernel_dir.mkdir(parents=True, exist_ok=True)
    kernel.rpc_token = secrets.token_urlsafe(32)
    kernel.sentinel = "@@GAGENT-KERNEL-" + secrets.token_urlsafe(16) + "@@"
    kernel.allowlist = frozenset(allowlist)
    kernel.max_tool_calls = max_tool_calls
    kernel.rpc_server = KernelRPCServer(kernel)
    rpc_endpoint = kernel.rpc_server.start()

    # Parent-liveness pipe: the child watches the read end, we hold the write
    # end. Closing it (or dying) tells the kernel the host is gone.
    parent_read, parent_write = os.pipe()
    kernel.parent_fd_w = parent_write

    (kernel.kernel_dir / "gagent_tools.py").write_text(
        generate_stub_module(sorted(allowlist)), encoding="utf-8"
    )
    runner_path = kernel.kernel_dir / "gagent_kernel_runner.py"
    runner_path.write_text(KERNEL_RUNNER_SOURCE, encoding="utf-8")

    child_env = build_child_env(
        rpc_endpoint=rpc_endpoint,
        rpc_token=kernel.rpc_token,
        kernel_dir=kernel.kernel_dir,
        sentinel=kernel.sentinel,
        parent_fd=parent_read,
    )
    try:
        kernel.proc = subprocess.Popen(
            [sys.executable, str(runner_path)],
            cwd=str(child_cwd),
            env=child_env,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            close_fds=True,
            pass_fds=(parent_read,),
        )
    finally:
        # Only the child needs the read end; the parent keeps the write end
        # open for the kernel's lifetime so EOF means "host is gone".
        os.close(parent_read)
    for target in (_stdout_reader, _stderr_reader):
        threading.Thread(target=target, args=(kernel,), daemon=True).start()
    _ensure_background_reaper()


# --- cell execution ---------------------------------------------------------


def _await_cell(
    kernel: SessionKernel,
    timeout: int,
    abort_check: Optional[Callable[[], bool]],
) -> Tuple[str, Dict[str, Any]]:
    """Wait for the cell's reply; returns (host status, payload)."""
    deadline = time.monotonic() + timeout if timeout else None
    while True:
        if abort_check is not None and abort_check():
            return "interrupted", {}
        if deadline is not None and time.monotonic() > deadline:
            return "timeout", {}
        try:
            payload = kernel.response_q.get(timeout=0.05)
        except queue.Empty:
            continue
        if payload.get("status") in ("kernel-eof", "protocol-error"):
            return "error", payload
        return "success", payload


def _with_stderr(stdout_text: str, stderr_text: str) -> str:
    return stdout_text + "\n--- stderr ---\n" + stderr_text


def _cell_result(
    kernel: SessionKernel,
    key: Tuple,
    status: str,
    payload: Dict[str, Any],
    *,
    timeout: int,
    reused: bool,
    state_reset: bool,
    exec_start: float,
    scratch_dir: Path,
) -> Dict[str, Any]:
    """Assemble the tool result for one settled cell (disposing per contract)."""
    if status in ("timeout", "interrupted"):
        # No safe way to interrupt one cell in place: kill the kernel, report
        # the loss, respawn on the next call.
        _discard_kernel(key, kernel)
    duration = round(time.monotonic() - exec_start, 2)
    kernel.execution_count = int(payload.get("execution_count", kernel.execution_count + 1))
    stderr_raw = kernel.stderr.drain()

    def clean(text: str) -> str:
        return strip_ansi(text)

    stdout_text, stdout_metadata = truncate_stdout(
        clean(str(payload.get("stdout", "")) + kernel.raw.drain()), scratch_dir
    )
    cell_stderr = truncate_stderr(clean(str(payload.get("stderr", "")) + stderr_raw))
    cell_status = payload.get("status", "")
    result: Dict[str, Any] = {
        "status": status,
        "output": stdout_text,
        "exit_code": 0,
        "tool_calls_made": kernel.tool_call_counter[0],
        "duration_seconds": duration,
        "kernel": {
            "reused": reused,
            "execution_count": kernel.execution_count,
            "state_reset": state_reset,
        },
    }
    result.update(stdout_metadata)
    cell_spill = str(payload.get("stdout_spill_path", "") or "")
    if cell_spill and payload.get("stdout_clipped"):
        result["stdout_spill_path"] = cell_spill
        result["warning"] = (
            f"Cell stdout exceeded the inline cap; head shown. FULL output saved to {cell_spill} "
            f'— page it with the file/document tools (e.g. file_operations operation="read") '
            "instead of re-running. Kernel state persists: printing a narrower slice next "
            "cell is often cheaper."
        )

    if status == "timeout":
        message = (
            f"Cell timed out after {timeout}s; the session kernel was killed and its "
            "state was lost. The next execute_code call starts a fresh kernel."
        )
        result.update(
            success=False,
            exit_code=-1,
            error=message,
            output=(stdout_text + "\n\n[timeout] " + message) if stdout_text else message,
        )
    elif status == "interrupted":
        message = (
            "Execution interrupted; the session kernel was killed and its state was lost."
        )
        result.update(
            success=False,
            exit_code=-1,
            error=message,
            output=(stdout_text + "\n\n[interrupted] " + message) if stdout_text else message,
        )
    elif cell_status == "error":
        trace = clean(str(payload.get("traceback", "")))
        result.update(
            success=False,
            status="error",
            exit_code=1,
            error=trace or "Cell raised an exception.",
            output=_with_stderr(stdout_text, cell_stderr + trace)
            if (cell_stderr or trace)
            else stdout_text,
        )
        hint = failure_hint(trace, sorted(kernel.allowlist))
        if hint:
            result["hint"] = hint
    elif cell_status == "exit":
        # The cell called sys.exit(): honor it as end-of-kernel.
        _discard_kernel(key, kernel)
        result["kernel"]["ended"] = True
        result["success"] = True
        if cell_stderr:
            result["output"] = _with_stderr(stdout_text, cell_stderr)
    elif status == "error":
        _discard_kernel(key, kernel)
        result.update(
            success=False,
            exit_code=-1,
            error="The session kernel died while running the cell"
            + (": " + stderr_raw.strip() if stderr_raw.strip() else "."),
        )
    else:
        result["success"] = True
        if cell_stderr:
            result["output"] = _with_stderr(stdout_text, cell_stderr)
    return result


def _discard_kernel(key: Tuple, kernel: SessionKernel) -> None:
    """Drop *kernel*'s registry entry (only if still registered) and tear it down."""
    with _REGISTRY_LOCK:
        if _KERNELS.get(key) is kernel:
            _KERNELS.pop(key, None)
            # The next call for this key respawns: its state is gone, and the
            # result must not claim otherwise.
            _mark_state_lost([key])
    kernel.teardown()


def run_cell(
    code: str,
    *,
    session_id: str,
    work_dir: str,
    reset: bool,
    tool_context: Optional[object],
    abort_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Run one cell in the (session_id, sorted(allowlist), cwd) kernel."""
    scratch_dir = config.resolve_scratch_dir(work_dir)
    child_cwd = config.resolve_child_cwd(work_dir, scratch_dir)
    allowlist = tuple(sorted(set(config.allowed_tools())))
    key = (session_id, allowlist, str(child_cwd))
    timeout = config.cell_timeout_seconds()
    exec_start = time.monotonic()

    kernel, state_reset = _acquire_kernel(key, reset)
    try:
        return _run_cell(
            kernel,
            key,
            code,
            scratch_dir=scratch_dir,
            child_cwd=child_cwd,
            allowlist=allowlist,
            timeout=timeout,
            abort_check=abort_check,
            exec_start=exec_start,
            state_reset=state_reset,
            tool_context=tool_context,
        )
    finally:
        with _REGISTRY_LOCK:
            kernel.attached -= 1
            kernel.last_used = time.monotonic()
            # Popped from the registry (reset/dead/reaped) while this cell was
            # still attached: the last cell out owns the teardown.
            orphaned = kernel.attached == 0 and _KERNELS.get(key) is not kernel
        if orphaned:
            kernel.teardown()


def _run_cell(
    kernel: SessionKernel,
    key: Tuple,
    code: str,
    *,
    scratch_dir: Path,
    child_cwd: Path,
    allowlist: Tuple[str, ...],
    timeout: int,
    abort_check: Optional[Callable[[], bool]],
    exec_start: float,
    state_reset: bool,
    tool_context: Optional[object],
) -> Dict[str, Any]:
    reused = kernel.proc is not None and kernel.alive()
    with kernel.cell_lock:
        binding = CellBinding(tool_context)
        try:
            if kernel.dead():
                kernel.teardown()
            if kernel.proc is None:
                reused = False
                _spawn(
                    kernel,
                    scratch_dir=scratch_dir,
                    child_cwd=child_cwd,
                    allowlist=frozenset(allowlist),
                    max_tool_calls=config.max_tool_calls_per_cell(),
                )
            assert kernel.proc is not None and kernel.proc.stdin is not None
            kernel.tool_call_counter[0] = 0
            kernel.raw.drain()
            kernel.stderr.drain()
            kernel.authority = binding
            kernel.proc.stdin.write(
                (json.dumps({"id": uuid.uuid4().hex, "code": code}) + "\n").encode("utf-8")
            )
            kernel.proc.stdin.flush()
            status, payload = _await_cell(kernel, timeout, abort_check)
            return _cell_result(
                kernel,
                key,
                status,
                payload,
                timeout=timeout,
                reused=reused,
                state_reset=state_reset,
                exec_start=exec_start,
                scratch_dir=scratch_dir,
            )
        except (BrokenPipeError, OSError) as exc:
            logger.error("execute_code kernel pipe failed: %s", exc)
            _discard_kernel(key, kernel)
            return {
                "success": False,
                "status": "error",
                "exit_code": -1,
                "error": f"The session kernel died: {exc}",
                "output": "",
                "tool_calls_made": kernel.tool_call_counter[0],
                "duration_seconds": round(time.monotonic() - exec_start, 2),
                "kernel": {
                    "reused": reused,
                    "execution_count": kernel.execution_count,
                    "state_reset": state_reset,
                },
            }
        finally:
            # The cell has settled on every path: its tool authority retires
            # with it, so nothing the cell left running can dispatch under it.
            binding.retire()
            kernel.authority = None
