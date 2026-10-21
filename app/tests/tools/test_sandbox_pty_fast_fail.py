"""A terminal sandbox that cannot reach the app fails fast and does not make every retry wait again.

On the evaluation instance every terminal_session call failed after 34-85 s ("sandbox did not connect its cell
channel in time"): the sandbox containers joined another stack's network, dialled the wrong app, and the app waited
out a 30 s handshake. Two retries (ensure, then write) cost a minute each time.
"""

from __future__ import annotations

import asyncio
import subprocess
import time

import pytest

from app.services.terminal import sandbox_pty_backend as module
from app.services.terminal.sandbox_pty_backend import SandboxPTYBackend


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    module._BREAKER.reset()
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.delenv("HOST_RUNTIME_ROOT", raising=False)
    monkeypatch.setenv("CODE_MODE_RPC_BIND", "127.0.0.1")
    monkeypatch.setenv("CODE_MODE_RPC_ADVERTISE", "127.0.0.1")
    monkeypatch.delenv("SANDBOX_NETWORK", raising=False)
    monkeypatch.setenv("TERMINAL_SANDBOX_CONNECT_TIMEOUT_SECONDS", "2")
    yield
    module._BREAKER.reset()


class _NeverConnects:
    """Stands in for CellChannelServer when the sandbox never dials back."""

    def __init__(self, token):
        self.stopped = False

    def start(self):
        return "tcp://127.0.0.1:1"

    def wait_for_streams(self, timeout):
        time.sleep(min(timeout, 0.2))
        raise TimeoutError("sandbox did not connect its cell channel in time")

    def stop(self):
        self.stopped = True


class _Docker:
    """Fake `docker` CLI: records calls, answers inspect from the fields."""

    def __init__(self, own_networks="gagent-front gagent-sandbox", container="running 0", run_rc=0):
        self.calls = []
        self.own_networks = own_networks
        self.container = container
        self.run_rc = run_rc

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[1] == "inspect" and "--format" in argv and "State.Status" in argv[-1]:
            if self.container is None:
                return subprocess.CompletedProcess(argv, 1, "", "forbidden")
            return subprocess.CompletedProcess(argv, 0, self.container + "\n", "")
        if argv[1] == "inspect":
            if self.own_networks is None:
                return subprocess.CompletedProcess(argv, 1, "", "forbidden")
            return subprocess.CompletedProcess(argv, 0, self.own_networks + " \n", "")
        if argv[1] == "run":
            return subprocess.CompletedProcess(argv, self.run_rc, "id", "boom" if self.run_rc else "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def ran(self, verb):
        return [c for c in self.calls if c[1] == verb]


def _spawn(tmp_path, docker, monkeypatch, channel=_NeverConnects):
    monkeypatch.setattr(module.subprocess, "run", docker)
    monkeypatch.setattr(module, "CellChannelServer", channel)
    backend = SandboxPTYBackend()
    backend.session_id = "session_demo"
    workspace = tmp_path / "runtime" / "session_demo" / "raw_files"
    workspace.mkdir(parents=True, exist_ok=True)
    return backend, asyncio.run(_try(backend, workspace))


async def _try(backend, workspace):
    started = time.monotonic()
    try:
        await backend.spawn(cwd=str(workspace), exec_args=["/bin/bash"])
        return None, time.monotonic() - started
    except Exception as exc:  # noqa: BLE001 - the tests look at which one
        return exc, time.monotonic() - started


def test_a_network_this_app_is_not_on_is_refused_before_anything_starts(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_NETWORK", "gagent-sandbox")
    docker = _Docker(own_networks="gagent-eval-front gagent-eval-sandbox")
    _, (error, elapsed) = _spawn(tmp_path, docker, monkeypatch)
    assert isinstance(error, RuntimeError)
    assert "SANDBOX_NETWORK is 'gagent-sandbox'" in str(error) and "gagent-eval-sandbox" in str(error)
    assert docker.ran("run") == []
    assert elapsed < 1


def test_the_right_network_lets_the_start_proceed(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_NETWORK", "gagent-sandbox")
    docker = _Docker(own_networks="gagent-front gagent-sandbox gagent-dockerapi", container="running 0")
    _, (error, _) = _spawn(tmp_path, docker, monkeypatch)
    assert docker.ran("run")  # went on to create the container; it then never connected
    assert isinstance(error, TimeoutError)


@pytest.mark.parametrize("own", [None, ""])
def test_when_the_app_cannot_inspect_itself_the_check_is_skipped(tmp_path, monkeypatch, own):
    docker = _Docker(own_networks=own)
    _, (error, _) = _spawn(tmp_path, docker, monkeypatch)
    assert docker.ran("run")
    assert not (isinstance(error, RuntimeError) and "SANDBOX_NETWORK" in str(error))


def test_network_none_is_not_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_NETWORK", "none")
    docker = _Docker(own_networks="gagent-front")
    _spawn(tmp_path, docker, monkeypatch)
    assert docker.ran("run")


def test_an_exited_container_fails_at_once_with_its_exit_code(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_SANDBOX_CONNECT_TIMEOUT_SECONDS", "30")
    docker = _Docker(container="exited 3")
    backend, (error, elapsed) = _spawn(tmp_path, docker, monkeypatch)
    assert isinstance(error, RuntimeError) and "exited with code 3" in str(error)
    assert elapsed < 5
    assert docker.calls[-1][:3] == ["docker", "rm", "-f"]
    assert backend.channel is None


def test_a_sandbox_that_never_connects_gives_up_at_the_configured_time(tmp_path, monkeypatch):
    docker = _Docker(container="running 0")
    backend, (error, elapsed) = _spawn(tmp_path, docker, monkeypatch)
    assert isinstance(error, TimeoutError) and "within 2 s" in str(error)
    assert 1.5 <= elapsed < 8
    assert docker.calls[-1][:3] == ["docker", "rm", "-f"]


def test_a_retry_after_a_failed_start_fails_at_once(tmp_path, monkeypatch):
    docker = _Docker(container="exited 1")
    _spawn(tmp_path, docker, monkeypatch)
    runs_before = len(docker.ran("run"))
    _, (error, elapsed) = _spawn(tmp_path, docker, monkeypatch)
    assert isinstance(error, RuntimeError) and "unavailable" in str(error) and "execute_code" in str(error)
    assert elapsed < 0.5
    assert len(docker.ran("run")) == runs_before


def test_the_cooldown_ends(tmp_path, monkeypatch):
    docker = _Docker(container="exited 1")
    _spawn(tmp_path, docker, monkeypatch)
    monkeypatch.setenv("TERMINAL_SANDBOX_COOLDOWN_SECONDS", "0")
    runs_before = len(docker.ran("run"))
    _spawn(tmp_path, docker, monkeypatch)
    assert len(docker.ran("run")) == runs_before + 1


def test_a_failed_docker_run_also_starts_the_cooldown(tmp_path, monkeypatch):
    _, (error, _) = _spawn(tmp_path, _Docker(run_rc=1), monkeypatch)
    assert isinstance(error, RuntimeError) and "creation failed" in str(error)
    _, (second, _) = _spawn(tmp_path, _Docker(), monkeypatch)
    assert "unavailable" in str(second)


def test_the_runner_dials_for_less_time_than_the_app_waits():
    source = (module.Path(module.__file__).with_name("sandbox_pty_runner.py")).read_text(encoding="utf-8")
    assert "timeout=30" not in source and "timeout=8" in source
