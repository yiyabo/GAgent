"""Regressions for the sandbox kernel transport (P3).

A sandbox kernel is a sibling container that dials back to the app. Three
things must hold for the local pipe path to be reused unchanged:

  * the cell channel accepts only a correct token, and hands back the
    reader/writer pair the frame parser already understands;
  * ``SandboxProcess`` quacks like ``subprocess.Popen`` (stdin/stdout/stderr,
    poll/wait) and its kill goes to docker, never to ``os.killpg``;
  * ``_spawn_sandbox`` wires the container's mounts and env correctly — in
    particular the host-path translation, without which the daemon would be
    asked to mount a path that only exists inside the app container.
"""

from __future__ import annotations

import io
import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from tool_box.tools_impl.execute_code import cell_channel, kernel as kernel_module


class _FakeChannel:
    """Stands in for CellChannelServer: no sockets, just the handoff."""

    instances: list = []

    def __init__(self, token: str) -> None:
        self.token = token
        self.stopped = False
        _FakeChannel.instances.append(self)

    def start(self) -> str:
        return "tcp://app:12345"

    def wait_for_streams(self, timeout: float):
        return (io.BytesIO(), io.BytesIO(), io.BytesIO())

    def stop(self) -> None:
        self.stopped = True


def _dial(host: str, port: int, token: str, role: str) -> socket.socket:
    conn = socket.create_connection((host, int(port)), timeout=5)
    conn.sendall((json.dumps({"token": token, "role": role}) + "\n").encode("utf-8"))
    return conn


# --- the channel -----------------------------------------------------------


def test_channel_round_trips_and_publishes_eof() -> None:
    server = cell_channel.CellChannelServer("tok")
    endpoint = server.start()
    host, port = endpoint.split("://")[1].split(":")

    def client() -> None:
        conn = _dial(host, port, "tok", "cell")
        _dial(host, port, "tok", "stderr")
        time.sleep(0.2)
        conn.sendall(b"REPLY-1\n")
        time.sleep(0.2)
        conn.close()

    threading.Thread(target=client, daemon=True).start()
    out, inp, errs = server.wait_for_streams(5)
    inp.write(b"CELL-1\n")
    inp.flush()
    assert out.read1(4096) == b"REPLY-1\n"
    # A closed peer may surface as EOF or as a reset; the kernel's reader
    # treats both as "gone" (_safe_read1), so the channel may raise here.
    try:
        tail = out.read1(4096)
    except OSError:
        tail = b""
    assert tail == b""
    assert hasattr(errs, "read1")
    server.stop()


def test_channel_rejects_a_bad_token() -> None:
    server = cell_channel.CellChannelServer("good")
    endpoint = server.start()
    host, port = endpoint.split("://")[1].split(":")
    conn = _dial(host, port, "bad", "cell")
    assert conn.recv(16) == b""  # refused and closed, no stream handed over
    with pytest.raises(TimeoutError):
        server.wait_for_streams(0.5)
    server.stop()


def test_channel_times_out_when_nobody_dials() -> None:
    server = cell_channel.CellChannelServer("tok")
    server.start()
    with pytest.raises(TimeoutError):
        server.wait_for_streams(0.3)
    server.stop()


# --- SandboxProcess --------------------------------------------------------


def test_sandbox_process_eof_comes_from_the_stream() -> None:
    proc = kernel_module.SandboxProcess(
        name="gagent-kernel-x",
        container_id="id",
        stdin=io.BytesIO(),
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
        channel=_FakeChannel("t"),
    )
    assert proc.poll() is None
    assert proc.stdout.read1(16) == b""  # the peer is gone
    assert proc.poll() == 0
    assert proc.wait(0.1) == 0


def test_kill_process_group_dispatches_to_sandbox_kill() -> None:
    seen: list = []

    class FakeSandbox:
        def sandbox_kill(self, escalate: bool = True) -> None:
            seen.append(escalate)

    kernel_module._kill_process_group(FakeSandbox(), escalate=False)
    assert seen == [False]


def test_sandbox_kill_stops_the_channel_and_removes_the_container(monkeypatch) -> None:
    argv_seen: list = []

    def fake_run(argv, **kwargs):
        argv_seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(kernel_module.subprocess, "run", fake_run)
    channel = _FakeChannel("t")
    proc = kernel_module.SandboxProcess(
        name="gagent-kernel-x",
        container_id="id",
        stdin=io.BytesIO(),
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
        channel=channel,
    )
    proc.sandbox_kill(escalate=False)
    assert channel.stopped is True
    assert ["docker", "kill", "gagent-kernel-x"] in argv_seen
    assert ["docker", "rm", "-f", "gagent-kernel-x"] in argv_seen


# --- _spawn_sandbox wiring -------------------------------------------------


def _kernel_for_spawn(tmp_path: Path) -> kernel_module.SessionKernel:
    kernel = kernel_module.SessionKernel(
        ("session_x", ("web_search",), "/app/runtime/session_x")
    )
    kernel.kernel_dir = tmp_path
    kernel.sentinel = "@@SENTINEL@@"
    kernel.rpc_token = "rpc-token"
    return kernel


def _spawn_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_RUNTIME_ROOT", "/app/runtime")
    monkeypatch.setenv("HOST_RUNTIME_ROOT", "/data/phage-agent/runtime")
    monkeypatch.setenv("CODE_MODE_SANDBOX", "1")
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "gagent-sandbox-python:p1")
    monkeypatch.delenv("SANDBOX_NETWORK", raising=False)
    monkeypatch.delenv("SANDBOX_MEM", raising=False)
    monkeypatch.delenv("SANDBOX_CPUS", raising=False)


def test_spawn_sandbox_wires_a_hardened_container(monkeypatch, tmp_path) -> None:
    _spawn_env(monkeypatch)
    monkeypatch.setattr(cell_channel, "CellChannelServer", _FakeChannel)
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="deadbeefcafe\n", stderr="")

    monkeypatch.setattr(kernel_module.subprocess, "run", fake_run)
    kernel = _kernel_for_spawn(tmp_path)
    runner = tmp_path / "gagent_kernel_runner.py"
    runner.write_text("print('x')", encoding="utf-8")

    kernel_module._spawn_sandbox(
        kernel,
        runner_path=runner,
        child_cwd=Path("/app/runtime/session_x"),
        rpc_endpoint="tcp://app:9999",
    )

    argv = captured["argv"]
    assert argv[:2] == ["docker", "run"]
    for flag in ("--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only"):
        assert flag in argv
    assert argv[argv.index("--network") + 1] == "gagent-sandbox"
    # The mount SOURCE must be the host path: the daemon resolves it, not us.
    assert "/data/phage-agent/runtime/session_x:/app/runtime/session_x:rw" in argv
    joined = " ".join(argv)
    assert "GAGENT_CELL_ENDPOINT=tcp://app:12345" in joined
    assert "GAGENT_CELL_TOKEN=" in joined
    assert "GAGENT_RPC_ENDPOINT=tcp://app:9999" in joined
    assert "GAGENT_RPC_TOKEN=rpc-token" in joined
    assert "GAGENT_KERNEL_SENTINEL=@@SENTINEL@@" in joined
    assert f"PYTHONPATH={tmp_path}" in joined
    assert argv[-2:] == ["python", str(runner)]
    assert isinstance(kernel.proc, kernel_module.SandboxProcess)
    assert kernel.proc.poll() is None
    assert kernel.cell_channel is not None


def test_spawn_sandbox_refuses_a_workspace_outside_the_runtime_root(monkeypatch, tmp_path) -> None:
    _spawn_env(monkeypatch)
    monkeypatch.setattr(cell_channel, "CellChannelServer", _FakeChannel)
    monkeypatch.setattr(
        kernel_module.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    kernel = _kernel_for_spawn(tmp_path)
    with pytest.raises(ValueError):
        kernel_module._spawn_sandbox(
            kernel,
            runner_path=tmp_path / "gagent_kernel_runner.py",
            child_cwd=Path("/tmp/not-the-runtime"),
            rpc_endpoint="tcp://app:9999",
        )
    assert kernel.cell_channel is None  # cleaned up, not left half-open


def test_spawn_sandbox_cleans_up_when_docker_fails(monkeypatch, tmp_path) -> None:
    _spawn_env(monkeypatch)
    monkeypatch.setattr(cell_channel, "CellChannelServer", _FakeChannel)
    monkeypatch.setattr(
        kernel_module.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 125, stdout="", stderr="no such image"
        ),
    )
    kernel = _kernel_for_spawn(tmp_path)
    with pytest.raises(RuntimeError):
        kernel_module._spawn_sandbox(
            kernel,
            runner_path=tmp_path / "gagent_kernel_runner.py",
            child_cwd=Path("/app/runtime/session_x"),
            rpc_endpoint="tcp://app:9999",
        )
    assert kernel.cell_channel is None
    assert kernel.proc is None
    assert _FakeChannel.instances[-1].stopped is True
