import os
from pathlib import Path

import pytest

from app.services.interpreter import docker_interpreter as docker_interpreter_module
from app.services.interpreter.docker_interpreter import DockerCodeInterpreter


class _FakeContainer:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", exit_code: int = 0):
        self.status = "exited"
        self._stdout = stdout
        self._stderr = stderr
        self._exit_code = exit_code
        self.removed = False

    def reload(self):
        return None

    def wait(self):
        return {"StatusCode": self._exit_code}

    def logs(self, *, stdout: bool = True, stderr: bool = False):
        if stdout and not stderr:
            return self._stdout
        if stderr and not stdout:
            return self._stderr
        return self._stdout + self._stderr

    def remove(self, force: bool = False):
        self.removed = force


class _FakeImages:
    def __init__(self, exc: Exception | None = None):
        self.exc = exc
        self.requested = None

    def get(self, image: str):
        self.requested = image
        if self.exc is not None:
            raise self.exc
        return {"image": image}


class _FakeContainers:
    def __init__(self, container: _FakeContainer):
        self.container = container
        self.last_kwargs = None

    def run(self, **kwargs):
        self.last_kwargs = kwargs
        return self.container


class _FakeClient:
    def __init__(self, container: _FakeContainer, image_exc: Exception | None = None):
        self.images = _FakeImages(exc=image_exc)
        self.containers = _FakeContainers(container)


def test_build_volume_mounts_use_same_host_paths_and_dedupe(tmp_path: Path) -> None:
    work_dir = tmp_path / "session" / "run"
    data_dir = tmp_path / "data"
    nested_data = data_dir / "nested"
    shared_dir = tmp_path / "shared"
    nested_shared = shared_dir / "nested"
    work_child = work_dir / "results"
    for path in (work_dir, data_dir, nested_data, shared_dir, nested_shared, work_child):
        path.mkdir(parents=True, exist_ok=True)

    interpreter = DockerCodeInterpreter(
        work_dir=str(work_dir),
        data_dir=str(data_dir),
        extra_read_dirs=[
            str(data_dir),
            str(nested_data),
            str(shared_dir),
            str(nested_shared),
            str(work_child),
        ],
    )

    mounts = interpreter._build_volume_mounts()

    assert mounts[str(data_dir)] == {"bind": str(data_dir), "mode": "ro"}
    assert mounts[str(shared_dir)] == {"bind": str(shared_dir), "mode": "ro"}
    assert mounts[str(work_dir)] == {"bind": str(work_dir), "mode": "rw"}
    assert str(nested_data) not in mounts
    assert str(nested_shared) not in mounts
    assert str(work_child) not in mounts


def test_build_volume_mounts_promotes_parent_write_mounts_over_readonly_parent(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    work_dir = session_dir / "task" / "run"
    data_dir = tmp_path / "data"
    for path in (session_dir, work_dir, data_dir):
        path.mkdir(parents=True, exist_ok=True)

    interpreter = DockerCodeInterpreter(
        work_dir=str(work_dir),
        data_dir=str(data_dir),
        extra_read_dirs=[str(session_dir)],
        extra_write_dirs=[str(session_dir)],
    )

    mounts = interpreter._build_volume_mounts()

    assert mounts[str(session_dir)] == {"bind": str(session_dir), "mode": "rw"}
    assert str(work_dir) not in mounts
    assert mounts[str(data_dir)] == {"bind": str(data_dir), "mode": "ro"}


def test_run_file_passes_same_path_mounts_env_and_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)

    work_dir = tmp_path / "session" / "run"
    data_dir = tmp_path / "data"
    shared_dir = tmp_path / "shared"
    for path in (work_dir, data_dir, shared_dir):
        path.mkdir(parents=True, exist_ok=True)
    code_file = work_dir / "task_code.py"
    code_file.write_text("print('ok')", encoding="utf-8")

    container = _FakeContainer(stdout=b"ok\n", stderr=b"", exit_code=0)
    client = _FakeClient(container)
    interpreter = DockerCodeInterpreter(
        image="custom:image",
        work_dir=str(work_dir),
        data_dir=str(data_dir),
        extra_read_dirs=[str(shared_dir)],
    )
    interpreter.client = client

    result = interpreter.run_file(str(code_file))

    assert result.status == "success"
    assert client.images.requested == "custom:image"
    kwargs = client.containers.last_kwargs
    assert kwargs is not None
    cache_root = work_dir / ".code_executor_cache"
    assert kwargs["command"] == ["python", str(code_file.resolve())]
    assert kwargs["working_dir"] == str(work_dir)
    assert kwargs["environment"]["WORKSPACE"] == str(work_dir)
    assert kwargs["environment"]["DATA_DIR"] == str(data_dir)
    assert kwargs["environment"]["HOME"] == str(cache_root)
    assert kwargs["environment"]["XDG_CACHE_HOME"] == str(cache_root / "xdg")
    assert kwargs["environment"]["MPLCONFIGDIR"] == str(cache_root / "matplotlib")
    assert kwargs["environment"]["NUMBA_CACHE_DIR"] == str(cache_root / "numba")
    assert kwargs["volumes"][str(work_dir)] == {"bind": str(work_dir), "mode": "rw"}
    assert kwargs["volumes"][str(data_dir)] == {"bind": str(data_dir), "mode": "ro"}
    assert kwargs["volumes"][str(shared_dir)] == {"bind": str(shared_dir), "mode": "ro"}
    assert kwargs["network_disabled"] is False
    assert kwargs["environment"] == {
        "WORKSPACE": str(work_dir),
        "DATA_DIR": str(data_dir),
        "DATA": str(data_dir),
        "HOME": str(cache_root),
        "XDG_CACHE_HOME": str(cache_root / "xdg"),
        "MPLCONFIGDIR": str(cache_root / "matplotlib"),
        "NUMBA_CACHE_DIR": str(cache_root / "numba"),
    }
    assert kwargs["user"] == f"{os.getuid()}:{os.getgid()}"
    assert container.removed is True


def test_run_file_fails_fast_when_image_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class MissingImageError(Exception):
        pass

    monkeypatch.setattr(docker_interpreter_module, "ImageNotFound", MissingImageError)

    work_dir = tmp_path / "session" / "run"
    work_dir.mkdir(parents=True, exist_ok=True)
    code_file = work_dir / "task_code.py"
    code_file.write_text("print('ok')", encoding="utf-8")

    client = _FakeClient(_FakeContainer(), image_exc=MissingImageError("missing"))
    interpreter = DockerCodeInterpreter(image="missing:image", work_dir=str(work_dir))
    interpreter.client = client

    result = interpreter.run_file(str(code_file))

    assert result.status == "error"
    assert result.runtime_failure is True
    assert "Docker image not found: missing:image" in result.error
    assert client.containers.last_kwargs is None


def test_run_file_uses_host_network_for_loopback_proxy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(docker_interpreter_module.sys, "platform", "linux")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://localhost:7890")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")

    work_dir = tmp_path / "session" / "run"
    work_dir.mkdir(parents=True, exist_ok=True)
    code_file = work_dir / "task_code.py"
    code_file.write_text("print('ok')", encoding="utf-8")

    container = _FakeContainer(stdout=b"ok\n", stderr=b"", exit_code=0)
    client = _FakeClient(container)
    interpreter = DockerCodeInterpreter(work_dir=str(work_dir))
    interpreter.client = client

    result = interpreter.run_file(str(code_file))

    assert result.status == "success"
    kwargs = client.containers.last_kwargs
    assert kwargs is not None
    assert kwargs["environment"]["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert kwargs["environment"]["HTTPS_PROXY"] == "http://localhost:7890"
    assert kwargs["environment"]["NO_PROXY"] == "localhost,127.0.0.1"
    assert kwargs["network_mode"] == "host"
    assert "network_disabled" not in kwargs
