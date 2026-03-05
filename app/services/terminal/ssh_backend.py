"""SSH terminal backend powered by asyncssh."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Dict, Optional


def _get_asyncssh():
    """Lazily import asyncssh to avoid slow startup cost (heavy crypto deps)."""
    try:
        import asyncssh  # type: ignore
        return asyncssh
    except Exception:  # pragma: no cover
        return None


@dataclass
class SSHConfig:
    host: str
    user: str
    port: int = 22
    ssh_key_path: Optional[str] = None
    password: Optional[str] = None
    connect_timeout: int = 15
    known_hosts_path: Optional[str] = None  # None = use ~/.ssh/known_hosts

    @classmethod
    def from_remote_execution_config(cls, config: object) -> "SSHConfig":
        return cls(
            host=str(getattr(config, "host", "")),
            user=str(getattr(config, "user", "")),
            port=int(getattr(config, "port", 22)),
            ssh_key_path=getattr(config, "ssh_key_path", None),
            password=getattr(config, "password", None),
            connect_timeout=int(getattr(config, "connect_timeout", 15)),
        )


class SSHBackend:
    """Interactive SSH backend with a PTY channel."""

    def __init__(self) -> None:
        self._conn = None
        self._process = None
        self._output_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4096)
        self._stdout_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._closed = True

    async def connect(
        self,
        config: SSHConfig,
        *,
        cols: int = 120,
        rows: int = 36,
        env: Optional[Dict[str, str]] = None,
    ) -> None:
        asyncssh = _get_asyncssh()
        if asyncssh is None:
            raise RuntimeError("asyncssh is required for SSH terminal mode")

        # Resolve known_hosts: default to ~/.ssh/known_hosts for host key
        # verification.  Fail-close: if no known_hosts file exists and no
        # explicit path is configured, refuse to connect.
        if config.known_hosts_path == "":
            resolved_known_hosts: object = ()  # explicitly disabled
        elif config.known_hosts_path:
            resolved_known_hosts = config.known_hosts_path
        else:
            default_kh = os.path.expanduser("~/.ssh/known_hosts")
            if not os.path.isfile(default_kh):
                raise RuntimeError(
                    f"SSH host key verification failed: {default_kh} not found. "
                    "Create the file or set known_hosts_path='' to explicitly disable verification."
                )
            resolved_known_hosts = default_kh

        connect_kwargs: Dict[str, object] = {
            "host": config.host,
            "port": int(config.port),
            "username": config.user,
            "known_hosts": resolved_known_hosts,
            "connect_timeout": int(config.connect_timeout),
        }
        if config.ssh_key_path:
            connect_kwargs["client_keys"] = [config.ssh_key_path]
            if config.password:
                connect_kwargs["passphrase"] = config.password
        elif config.password:
            connect_kwargs["password"] = config.password

        self._conn = await asyncssh.connect(**connect_kwargs)
        self._process = await self._conn.create_process(
            term_type="xterm-256color",
            term_size=(max(1, cols), max(1, rows)),
            env=env,
            request_pty=True,
        )

        self._closed = False
        self._stdout_task = asyncio.create_task(self._pump_stream(self._process.stdout))
        self._stderr_task = asyncio.create_task(self._pump_stream(self._process.stderr))

    async def _pump_stream(self, stream: object) -> None:
        while not self._closed:
            try:
                data = await stream.read(65536)
            except Exception:
                break
            if not data:
                break
            if isinstance(data, str):
                chunk = data.encode("utf-8", errors="replace")
            elif isinstance(data, (bytes, bytearray)):
                chunk = bytes(data)
            else:
                chunk = str(data).encode("utf-8", errors="replace")
            if self._output_queue.full():
                try:
                    self._output_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            self._output_queue.put_nowait(chunk)

    async def read(self) -> bytes:
        return await self._output_queue.get()

    async def write(self, data: bytes) -> None:
        if self._process is None:
            raise RuntimeError("SSH process is not connected")
        text = data.decode("utf-8", errors="replace")
        self._process.stdin.write(text)
        await self._process.stdin.drain()

    async def resize(self, cols: int, rows: int) -> None:
        if self._process is None:
            return
        try:
            self._process.change_terminal_size(max(1, cols), max(1, rows))
        except Exception:
            try:
                self._process.stdin.channel.change_terminal_size(max(1, cols), max(1, rows))
            except Exception:
                pass

    async def disconnect(self) -> None:
        self._closed = True
        for task in (self._stdout_task, self._stderr_task):
            if task:
                task.cancel()
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass
            try:
                await self._process.wait_closed()
            except Exception:
                pass
            self._process = None
        if self._conn is not None:
            try:
                self._conn.close()
                await self._conn.wait_closed()
            except Exception:
                pass
            self._conn = None

    @property
    def is_closed(self) -> bool:
        return self._closed
