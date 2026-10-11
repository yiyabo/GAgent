"""Regressions for the sandbox hardening profile (app/ops/sandbox.py).

P3 moved model-written code out of the app process and into sibling
containers. That only holds if every creation site emits the same profile, so
these tests pin the profile itself: the flags, the host-path translation, and
the refusal of anything that would put a sandbox back on the host or on the
app's own filesystem.
"""

from __future__ import annotations

import pytest

from app.ops import sandbox as sb


def _spec(**overrides) -> sb.SandboxRunSpec:
    base = dict(
        image="gagent-sandbox-python:latest",
        kind="python",
        command=["python", "-c", "print(1)"],
        workdir="/app/runtime/session_x",
        name="gagent-kernel-abc",
        session_id="session_x",
        user="1001:1001",
        mounts=[("/app/runtime/session_x", "rw")],
        env={"HOME": "/tmp"},
    )
    base.update(overrides)
    return sb.SandboxRunSpec(**base)


def test_argv_carries_the_full_hardening_profile() -> None:
    argv = sb.build_sandbox_run_args(_spec())
    assert argv[0] == "docker" and argv[1] == "run"
    for flag in (
        "--rm",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--pids-limit=512",
    ):
        assert flag in argv, flag
    assert "--user" in argv and "1001:1001" in argv
    assert argv[argv.index("--tmpfs") + 1] == "/tmp:rw,size=2g"
    assert not any(item.startswith("--memory") for item in argv)


def test_argv_orders_flags_then_image_then_command() -> None:
    argv = sb.build_sandbox_run_args(_spec())
    image_at = argv.index("gagent-sandbox-python:latest")
    assert argv[image_at + 1:] == ["python", "-c", "print(1)"]


def test_network_defaults_to_the_sandbox_network(monkeypatch) -> None:
    monkeypatch.delenv("SANDBOX_NETWORK", raising=False)
    assert sb.sandbox_network() == "gagent-sandbox"
    argv = sb.build_sandbox_run_args(_spec())
    assert argv[argv.index("--network") + 1] == "gagent-sandbox"


@pytest.mark.parametrize("bad", ["host", "container", "container:gagent-app-1"])
def test_forbidden_networks_fall_back(monkeypatch, bad: str) -> None:
    monkeypatch.setenv("SANDBOX_NETWORK", bad)
    assert sb.sandbox_network() == "gagent-sandbox"


def test_mounts_translate_to_host_paths(monkeypatch) -> None:
    monkeypatch.setenv("APP_RUNTIME_ROOT", "/app/runtime")
    monkeypatch.setenv("HOST_RUNTIME_ROOT", "/data/phage-agent/runtime")
    assert (
        sb.translate_mount("/app/runtime/session_x")
        == "/data/phage-agent/runtime/session_x:/app/runtime/session_x:rw"
    )
    argv = sb.build_sandbox_run_args(_spec())
    assert "/data/phage-agent/runtime/session_x:/app/runtime/session_x:rw" in argv


def test_mounts_are_same_path_without_a_host_root(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RUNTIME_ROOT", raising=False)
    assert (
        sb.translate_mount("/app/runtime/session_x")
        == "/app/runtime/session_x:/app/runtime/session_x:rw"
    )


def test_require_translatable_refuses_paths_outside_the_runtime_root(monkeypatch) -> None:
    monkeypatch.setenv("APP_RUNTIME_ROOT", "/app/runtime")
    monkeypatch.setenv("HOST_RUNTIME_ROOT", "/data/phage-agent/runtime")
    with pytest.raises(ValueError):
        sb.require_translatable("/tmp/scratch")
    assert (
        sb.require_translatable("/app/runtime/session_x")
        == "/data/phage-agent/runtime/session_x"
    )


def test_require_translatable_is_a_noop_without_a_host_root(monkeypatch) -> None:
    monkeypatch.delenv("HOST_RUNTIME_ROOT", raising=False)
    assert sb.require_translatable("/tmp/scratch") == "/tmp/scratch"


def test_sandbox_env_never_inherits_host_secrets(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "super-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = sb.build_sandbox_env(workdir="/w")
    assert "QWEN_API_KEY" not in env
    assert "PATH" not in env  # the image's own PATH is the correct one
    assert env["HOME"] == "/tmp"
    assert env["WORKSPACE"] == "/w"
    assert env["PYTHONUTF8"] == "1"


def test_labels_carry_session_and_kind() -> None:
    labels = _spec().labels_with_defaults()
    assert labels["gagent.session"] == "session_x"
    assert labels["gagent.kind"] == "python"
    argv = sb.build_sandbox_run_args(_spec())
    assert "gagent.session=session_x" in argv
    assert "gagent.kind=python" in argv


def test_sdk_kwargs_match_the_cli_profile() -> None:
    kwargs = sb.as_run_kwargs(_spec())
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges"]
    assert kwargs["read_only"] is True
    assert kwargs["remove"] is True
    assert kwargs["network"] == "gagent-sandbox"
    assert kwargs["extra_host_config"]["pids_limit"] == 512
    assert kwargs["extra_host_config"]["tmpfs"] == {"/tmp": "rw,size=2g"}
    assert kwargs["volumes"]["/app/runtime/session_x"] == {
        "bind": "/app/runtime/session_x",
        "mode": "rw",
    }


def test_sdk_kwargs_translate_volumes(monkeypatch) -> None:
    monkeypatch.setenv("APP_RUNTIME_ROOT", "/app/runtime")
    monkeypatch.setenv("HOST_RUNTIME_ROOT", "/data/phage-agent/runtime")
    kwargs = sb.as_run_kwargs(_spec())
    assert "/data/phage-agent/runtime/session_x" in kwargs["volumes"]


def test_memory_and_cpus_come_from_env(monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_MEM", "4g")
    monkeypatch.setenv("SANDBOX_CPUS", "2")
    spec = _spec(memory=sb.sandbox_memory(), cpus=sb.sandbox_cpus())
    argv = sb.build_sandbox_run_args(spec)
    assert "--memory=4g" in argv
    assert "--cpus=2" in argv
    kwargs = sb.as_run_kwargs(spec)
    assert kwargs["mem_limit"] == "4g"
    assert kwargs["nano_cpus"] == 2_000_000_000


def test_container_names_are_sanitised() -> None:
    assert sb.sandbox_name("gagent-kernel", "a1b2c3d4e5f6") == "gagent-kernel-a1b2c3d4e5f6"
    assert sb.sandbox_name("gagent-kernel", "bad/../name!") == "gagent-kernel-badname"
    assert sb.sandbox_name("gagent-kernel", "") == "gagent-kernel"
