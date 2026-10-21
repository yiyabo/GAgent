"""Container PTY over the existing dial-back channel; no Docker exec/attach."""
import asyncio
import base64
import json
import os
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import threading
import time
import uuid

from app.ops import sandbox
from app.ops.workspace_mounts import session_mounts
from tool_box.tools_impl.execute_code.cell_channel import CellChannelServer
from .docker_pty_backend import DockerPTYBackend


def _env_seconds(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name) or default))
    except ValueError:
        return default


def connect_timeout_seconds() -> float:
    """How long a sandbox gets to dial back. It normally takes under a second."""
    return _env_seconds("TERMINAL_SANDBOX_CONNECT_TIMEOUT_SECONDS", 10.0) or 10.0


def cooldown_seconds() -> float:
    """After a failed start, how long further starts fail at once instead of waiting out the timeout again."""
    return _env_seconds("TERMINAL_SANDBOX_COOLDOWN_SECONDS", 120.0)


class _StartBreaker:
    """Remembers a recent failed start so a retry (ensure, then write, then write) costs nothing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failed_at = None
        self._reason = ""

    def reset(self) -> None:
        with self._lock:
            self._failed_at, self._reason = None, ""

    def record(self, reason: str) -> None:
        with self._lock:
            self._failed_at, self._reason = time.monotonic(), reason

    def check(self) -> None:
        window = cooldown_seconds()
        with self._lock:
            if self._failed_at is None or window <= 0:
                return
            age = time.monotonic() - self._failed_at
            if age >= window:
                self._failed_at = None
                return
            raise RuntimeError(
                "Sandbox terminal is unavailable: it failed to start %ds ago (%s). Use execute_code instead; "
                "a new start is allowed in %ds." % (age, self._reason, window - age)
            )


_BREAKER = _StartBreaker()


def _docker(argv, timeout: float = 8):
    return subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=timeout)


def _own_networks():
    """Names of the networks this app container is attached to, or None when that cannot be told."""
    try:
        result = _docker(["inspect", socket.gethostname(), "--format",
                          "{{range $name, $net := .NetworkSettings.Networks}}{{$name}} {{end}}"])
    except (OSError, subprocess.SubprocessError):
        return None
    names = result.stdout.split() if result.returncode == 0 else []
    return names or None


def check_sandbox_network() -> None:
    """A sandbox dials back to this app by name, so it must share a network with it.

    With ``SANDBOX_NETWORK`` naming another stack's network the sandbox reached that stack's app (or nothing)
    and every start waited out its handshake timeout. Skipped when the app cannot inspect itself.
    """
    wanted = sandbox.sandbox_network()
    if wanted == "none":
        return
    networks = _own_networks()
    if networks is not None and wanted not in networks:
        raise RuntimeError(
            "SANDBOX_NETWORK is %r but this app is attached to %s; a sandbox started on %r cannot reach it. "
            "Set SANDBOX_NETWORK to one of them." % (wanted, ", ".join(sorted(networks)), wanted)
        )


def _container_state(name: str):
    """``(status, exit_code)`` of the sandbox container, or None when unknown."""
    try:
        result = _docker(["inspect", name, "--format", "{{.State.Status}} {{.State.ExitCode}}"], timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    parts = result.stdout.split() if result.returncode == 0 else []
    if len(parts) != 2:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


class SandboxPTYBackend(DockerPTYBackend):
    def __init__(self):
        super().__init__()
        self.session_id = ""
        self.channel = None
        self.streams = None
        self.output = queue.Queue()
        self.send_lock = threading.Lock()

    async def spawn(self, *, cwd=None, env=None, exec_args=None, cols=120, rows=36, image=None, **kwargs):
        if self.channel is not None:
            raise RuntimeError("Sandbox terminal is already running")
        _BREAKER.check()
        await asyncio.to_thread(check_sandbox_network)
        workspace = Path(cwd).resolve()
        mounts = session_mounts(str(workspace), self.session_id)
        directory = workspace / "scratch" / "terminal" / uuid.uuid4().hex
        directory.mkdir(parents=True, exist_ok=True)
        runner = directory / "runner.py"
        runner.write_bytes(Path(__file__).with_name("sandbox_pty_runner.py").read_bytes())
        token = secrets.token_urlsafe(32)
        self.channel = CellChannelServer(token)
        endpoint = self.channel.start()
        self._container_name = sandbox.sandbox_name("gagent-terminal", uuid.uuid4().hex[:12])
        spec = sandbox.SandboxRunSpec(
            image=image or sandbox.sandbox_python_image(), kind="terminal",
            name=self._container_name, session_id=self.session_id,
            command=["python", str(runner)], workdir=str(workspace),
            user=f"{os.getuid()}:{os.getgid()}", mounts=mounts,
            env=sandbox.build_sandbox_env(workdir=str(workspace), extra={
                **(env or {}), "TERM": "xterm-256color", "GAGENT_CELL_ENDPOINT": endpoint,
                "GAGENT_CELL_TOKEN": token, "GAGENT_TERMINAL_COMMAND": json.dumps(list(exec_args or ["/bin/bash"])),
                "GAGENT_TERMINAL_SIZE": json.dumps([cols, rows]),
            }), memory=sandbox.sandbox_memory(), cpus=sandbox.sandbox_cpus(),
        )
        try:
            result = await asyncio.to_thread(subprocess.run, sandbox.build_sandbox_run_args(spec),
                                             capture_output=True, text=True, timeout=90)
            if result.returncode:
                raise RuntimeError("Sandbox terminal creation failed: " + result.stderr[-1000:])
            self.streams = await self._wait_connected(connect_timeout_seconds())
            self._closed = False
            threading.Thread(target=self._read_output, daemon=True).start()
            _BREAKER.reset()
        except BaseException as exc:
            if isinstance(exc, (RuntimeError, TimeoutError)):
                _BREAKER.record(str(exc)[:200])
            await self.terminate()
            raise

    async def _wait_connected(self, timeout: float):
        """Wait for the sandbox to dial back, and give up at once if its container has already exited."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            try:
                return await asyncio.to_thread(self.channel.wait_for_streams, max(0.2, min(2.0, remaining)))
            except TimeoutError:
                pass
            state = await asyncio.to_thread(_container_state, self._container_name)
            if state is not None and state[0] in ("exited", "dead"):
                raise RuntimeError("Sandbox terminal container exited with code %d before it connected" % state[1])
            if time.monotonic() >= deadline:
                raise TimeoutError("sandbox did not connect its cell channel within %.0f s" % timeout)

    def _read_output(self):
        try:
            while data := self.streams[0].read1(65536):
                self.output.put(data)
        except OSError:
            pass
        finally:
            self._closed = True
            self.output.put(b"")

    def _send(self, message):
        if self._closed or not self.streams:
            raise RuntimeError("Sandbox terminal is closed")
        with self.send_lock:
            self.streams[1].write((json.dumps(message) + "\n").encode())
            self.streams[1].flush()

    async def read(self):
        if self._closed and self.output.empty():
            return b""
        return await asyncio.to_thread(self.output.get)

    async def write(self, data):
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("write() expects bytes")
        await asyncio.to_thread(self._send, {"op": "write", "data": base64.b64encode(data).decode()})

    async def resize(self, cols, rows):
        if not self._closed:
            await asyncio.to_thread(self._send, {"op": "resize", "cols": cols, "rows": rows})

    async def terminate(self):
        if not self._closed and self.streams:
            try:
                await asyncio.to_thread(self._send, {"op": "close"})
            except (OSError, RuntimeError):
                pass
        self._closed = True
        if self.channel:
            await asyncio.to_thread(self.channel.stop)
            self.channel = None
        if self._container_name:
            try:
                await asyncio.to_thread(subprocess.run, ["docker", "rm", "-f", self._container_name],
                                        capture_output=True, timeout=15)
            finally:
                self._container_name = None
        if self.streams:
            for stream in self.streams:
                if hasattr(stream, "close"):
                    stream.close()
            self.streams = None
        self.output.put(b"")
