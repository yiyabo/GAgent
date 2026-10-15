"""Container PTY over the existing dial-back channel; no Docker exec/attach."""
import asyncio
import base64
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import threading
import uuid

from app.ops import sandbox
from app.ops.workspace_mounts import session_mounts
from tool_box.tools_impl.execute_code.cell_channel import CellChannelServer
from .docker_pty_backend import DockerPTYBackend


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
            self.streams = await asyncio.to_thread(self.channel.wait_for_streams, 30)
            self._closed = False
            threading.Thread(target=self._read_output, daemon=True).start()
        except BaseException:
            await self.terminate()
            raise

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
