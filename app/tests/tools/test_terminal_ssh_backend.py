from __future__ import annotations

import asyncio

from app.services.terminal import ssh_backend as ssh_backend_module
from app.services.terminal.ssh_backend import SSHBackend, SSHConfig


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, _size: int):
        if not self._chunks:
            await asyncio.sleep(0)
            return ""
        return self._chunks.pop(0)


class _FakeChannel:
    def __init__(self):
        self.size = None

    def change_terminal_size(self, cols: int, rows: int):
        self.size = (cols, rows)


class _FakeStdin:
    def __init__(self):
        self.writes = []
        self.channel = _FakeChannel()

    def write(self, text: str):
        self.writes.append(text)

    async def drain(self):
        return None


class _FakeProcess:
    def __init__(self):
        self.stdout = _FakeStream(["ssh-hello", ""])
        self.stderr = _FakeStream(["", ""])
        self.stdin = _FakeStdin()
        self.closed = False
        self.resize = None

    def change_terminal_size(self, cols: int, rows: int):
        self.resize = (cols, rows)

    def terminate(self):
        self.closed = True

    async def wait_closed(self):
        return None


class _FakeConnection:
    def __init__(self):
        self.process = _FakeProcess()
        self.closed = False

    async def create_process(self, **_kwargs):
        return self.process

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


class _FakeAsyncSSH:
    def __init__(self):
        self.last_kwargs = None
        self.conn = _FakeConnection()

    async def connect(self, **kwargs):
        self.last_kwargs = kwargs
        return self.conn


async def _run_case(monkeypatch):
    fake_asyncssh = _FakeAsyncSSH()
    monkeypatch.setattr(ssh_backend_module, "_get_asyncssh", lambda: fake_asyncssh)

    backend = SSHBackend()
    cfg = SSHConfig(host="127.0.0.1", user="demo", port=22, password="secret")

    await backend.connect(cfg)

    chunk = await asyncio.wait_for(backend.read(), timeout=2)
    assert b"ssh-hello" in chunk

    await backend.write(b"ls\\n")
    assert "ls" in "".join(fake_asyncssh.conn.process.stdin.writes)

    await backend.resize(120, 40)
    assert fake_asyncssh.conn.process.resize == (120, 40)

    await backend.disconnect()
    assert fake_asyncssh.conn.closed is True


def test_ssh_backend_connect_read_write_disconnect(monkeypatch):
    asyncio.run(_run_case(monkeypatch))
