from __future__ import annotations

import asyncio
import importlib
import shlex
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import tool_routes
import tool_box
from tool_box.bio_tools import remote_executor

bio_handler_module = importlib.import_module("tool_box.bio_tools.bio_tools_handler")


def test_remote_mode_dispatches_to_remote_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: List[str] = []

    async def _fake_remote(**kwargs):
        calls.append("remote")
        return {
            "success": True,
            "tool": kwargs["tool_name"],
            "operation": kwargs["operation"],
            "execution_mode": "remote",
            "execution_host": "remote-host",
        }

    async def _fake_local(**kwargs):
        calls.append("local")
        return {"success": True, "execution_mode": "local", "execution_host": "local"}

    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "remote")
    monkeypatch.setattr(bio_handler_module, "_execute_remote_bio_tool", _fake_remote)
    monkeypatch.setattr(bio_handler_module, "_execute_local_bio_tool", _fake_local)

    result = asyncio.run(
        bio_handler_module.bio_tools_handler(
            tool_name="seqkit",
            operation="stats",
            sequence_text=">seq1\nACGT\n",
            session_id="session_remote_dispatch",
        )
    )

    assert result["success"] is True
    assert result["execution_mode"] == "remote"
    assert calls == ["remote"]


def test_auto_mode_falls_back_to_local_when_remote_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_remote(**kwargs):
        _ = kwargs
        return {"success": False, "error": "remote failed"}

    async def _fake_local(**kwargs):
        _ = kwargs
        return {"success": True, "execution_mode": "local", "execution_host": "local"}

    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "auto")
    monkeypatch.setenv("BIO_TOOLS_REMOTE_PASSWORD", "dummy-password")
    monkeypatch.setattr(bio_handler_module, "_execute_remote_bio_tool", _fake_remote)
    monkeypatch.setattr(bio_handler_module, "_execute_local_bio_tool", _fake_local)

    result = asyncio.run(
        bio_handler_module.bio_tools_handler(
            tool_name="seqkit",
            operation="stats",
            sequence_text=">seq1\nACGT\n",
            session_id="session_auto_fallback",
        )
    )

    assert result["success"] is True
    assert result["execution_mode"] == "local"
    assert result["remote_fallback"] is True
    assert "remote failed" in result["remote_fallback_error"]


def test_list_and_help_do_not_trigger_remote_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _unexpected_remote(**kwargs):
        _ = kwargs
        raise AssertionError("remote execution should not be called for list/help")

    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "remote")
    monkeypatch.setattr(bio_handler_module, "_execute_remote_bio_tool", _unexpected_remote)

    list_result = asyncio.run(
        bio_handler_module.bio_tools_handler(
            tool_name="list",
            operation="list",
        )
    )
    assert list_result["success"] is True
    assert list_result["operation"] == "list"

    help_result = asyncio.run(
        bio_handler_module.bio_tools_handler(
            tool_name="seqkit",
            operation="help",
        )
    )
    assert help_result["success"] is True
    assert "operations" in help_result


def test_prepare_remote_io_rewrites_inputs_outputs_and_uploads(tmp_path: Path) -> None:
    fasta = tmp_path / "input.fa"
    bam = tmp_path / "reads.bam"
    bam_bai = tmp_path / "reads.bam.bai"
    bam_csi = tmp_path / "reads.bam.csi"
    hmm = tmp_path / "model.hmm"
    fasta.write_text(">a\nACGT\n", encoding="utf-8")
    bam.write_bytes(b"bam")
    bam_bai.write_bytes(b"bai")
    bam_csi.write_bytes(b"csi")
    hmm.write_text("HMMER3/f\n", encoding="utf-8")

    rewritten_input, rewritten_output_file, rewritten_params, uploads = bio_handler_module._prepare_remote_io(
        input_file=str(fasta),
        output_file="result.tsv",
        params={
            "bam_files": str(bam),
            "db": str(hmm),
            "depth": str(bam),
            "output": "summary.txt",
            "output_dir": "/work/custom_out",
        },
        remote_run_dir="/remote/runtime/_runs/seqkit/abc123",
    )

    assert rewritten_input and rewritten_input.startswith("/work/input/")
    assert rewritten_output_file == "output/result.tsv"
    assert rewritten_params["bam_files"].startswith("/work/input/")
    assert rewritten_params["db"].startswith("/work/input/")
    assert rewritten_params["depth"].startswith("/work/input/")
    assert rewritten_params["output"] == "output/summary.txt"
    assert rewritten_params["output_dir"] == "output/custom_out"
    assert len(uploads) == 5
    assert uploads[0][1].startswith("/remote/runtime/_runs/seqkit/abc123/input/")
    uploaded_remote_targets = {item[1] for item in uploads}
    assert any(target.endswith("reads.bam.bai") for target in uploaded_remote_targets)
    assert any(target.endswith("reads.bam.csi") for target in uploaded_remote_targets)


def test_prepare_remote_io_errors_on_missing_local_input() -> None:
    with pytest.raises(ValueError):
        bio_handler_module._prepare_remote_io(
            input_file="/Users/nonexistent/missing.fa",
            output_file=None,
            params=None,
            remote_run_dir="/remote/runtime/_runs/seqkit/abc123",
        )


def test_resolve_auth_falls_back_to_password_on_key_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "id_rsa"
    key_path.write_text("dummy", encoding="utf-8")

    config = remote_executor.RemoteExecutionConfig(
        host="example.com",
        user="user",
        port=22,
        runtime_dir="/remote/runtime",
        local_artifact_root="/tmp/local",
        ssh_key_path=str(key_path),
        password="pwd",
        sudo_policy="on_demand",
        connect_timeout=5,
    )

    async def _fake_run_ssh_command(config_obj, auth, command, timeout, display_command=None):
        _ = (config_obj, command, timeout, display_command)
        if auth.mode == "key":
            return {"success": False, "stderr": "Permission denied (publickey)"}
        return {"success": True, "stdout": "__BIO_TOOLS_REMOTE_AUTH_OK__"}

    monkeypatch.setattr(remote_executor, "_run_ssh_command", _fake_run_ssh_command)

    resolved = asyncio.run(remote_executor.resolve_auth(config))
    assert resolved.mode == "password"


def test_resolve_remote_uid_gid_parses_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = remote_executor.RemoteExecutionConfig(
        host="example.com",
        user="user",
        port=22,
        runtime_dir="/remote/runtime",
        local_artifact_root="/tmp/local",
        ssh_key_path=None,
        password="pwd",
        sudo_policy="on_demand",
        connect_timeout=5,
    )
    auth = remote_executor.ResolvedAuth(mode="password")

    async def _fake_run_ssh_command(config_obj, auth_obj, command, timeout, display_command=None):
        _ = (config_obj, auth_obj, command, timeout, display_command)
        return {"success": True, "stdout": "1000\n1001\n", "stderr": ""}

    monkeypatch.setattr(remote_executor, "_run_ssh_command", _fake_run_ssh_command)

    uid, gid = asyncio.run(remote_executor.resolve_remote_uid_gid(config, auth))
    assert uid == 1000
    assert gid == 1001


def test_execute_remote_command_retries_with_sudo_on_permission_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = remote_executor.RemoteExecutionConfig(
        host="example.com",
        user="user",
        port=22,
        runtime_dir="/remote/runtime",
        local_artifact_root="/tmp/local",
        ssh_key_path=None,
        password="pwd",
        sudo_policy="on_demand",
        connect_timeout=5,
    )
    auth = remote_executor.ResolvedAuth(mode="password")
    calls: List[str] = []

    async def _fake_run_ssh_command(config_obj, auth_obj, command, timeout, display_command=None):
        _ = (config_obj, auth_obj, timeout, display_command)
        calls.append(command)
        if len(calls) == 1:
            return {
                "success": False,
                "stderr": "Got permission denied while trying to connect to the Docker daemon socket",
            }
        return {"success": True, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(remote_executor, "_run_ssh_command", _fake_run_ssh_command)

    result = asyncio.run(
        remote_executor.execute_remote_command(
            config,
            auth,
            command="docker run --rm hello-world",
            timeout=30,
        )
    )

    assert result["success"] is True
    assert result["sudo_retry"] is True
    assert result["sudo_used"] is True
    assert len(calls) == 2


def test_execute_remote_command_joins_list_command_with_safe_quoting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = remote_executor.RemoteExecutionConfig(
        host="example.com",
        user="user",
        port=22,
        runtime_dir="/remote/runtime",
        local_artifact_root="/tmp/local",
        ssh_key_path=None,
        password=None,
        sudo_policy="never",
        connect_timeout=5,
    )
    auth = remote_executor.ResolvedAuth(mode="password")
    captured: Dict[str, Any] = {}

    async def _fake_run_ssh_command(config_obj, auth_obj, command, timeout, display_command=None):
        _ = (config_obj, auth_obj, timeout, display_command)
        captured["command"] = command
        return {"success": True, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(remote_executor, "_run_ssh_command", _fake_run_ssh_command)

    result = asyncio.run(
        remote_executor.execute_remote_command(
            config,
            auth,
            command=[
                "docker",
                "run",
                "--rm",
                "seqkit:latest",
                "seqkit",
                "grep",
                "-p",
                "x;touch /tmp/pwn",
                "/work/input.fa",
            ],
            timeout=30,
        )
    )

    assert result["success"] is True
    rendered = captured["command"]
    assert "'x;touch /tmp/pwn'" in rendered
    assert " -p x;touch /tmp/pwn " not in rendered


def test_download_remote_run_dir_returns_error_when_local_dir_unwritable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = remote_executor.RemoteExecutionConfig(
        host="example.com",
        user="user",
        port=22,
        runtime_dir="/remote/runtime",
        local_artifact_root="/tmp/local",
        ssh_key_path="/tmp/id_rsa",
        password=None,
        sudo_policy="on_demand",
        connect_timeout=5,
    )
    auth = remote_executor.ResolvedAuth(mode="key", key_path="/tmp/id_rsa")

    def _fail_mkdir(self, parents=False, exist_ok=False):  # type: ignore[no-untyped-def]
        _ = (self, parents, exist_ok)
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "mkdir", _fail_mkdir)

    result = asyncio.run(
        remote_executor.download_remote_run_dir(
            config,
            auth,
            remote_run_dir="/remote/run",
            local_target_dir="/tmp/unwritable",
        )
    )

    assert result["success"] is False
    assert "Failed to prepare local artifact directory" in result["error"]


def test_upload_files_retries_on_transient_scp_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_file = tmp_path / "input.fa"
    local_file.write_text(">a\nACGT\n", encoding="utf-8")

    config = remote_executor.RemoteExecutionConfig(
        host="example.com",
        user="user",
        port=22,
        runtime_dir="/remote/runtime",
        local_artifact_root=str(tmp_path / "artifacts"),
        ssh_key_path="/tmp/id_rsa",
        password=None,
        sudo_policy="on_demand",
        connect_timeout=5,
        scp_retries=2,
        scp_retry_delay=0.0,
    )
    auth = remote_executor.ResolvedAuth(mode="key", key_path="/tmp/id_rsa")

    monkeypatch.setattr(remote_executor, "_build_scp_base", lambda *_args, **_kwargs: ["scp"])
    attempts = {"count": 0}

    async def _fake_run_subprocess(args, timeout, display_command=None):  # type: ignore[no-untyped-def]
        _ = (args, timeout, display_command)
        attempts["count"] += 1
        if attempts["count"] == 1:
            return {"success": False, "stderr": "scp: Connection closed\r\n", "exit_code": 255}
        return {"success": True, "stderr": "", "stdout": "", "exit_code": 0}

    monkeypatch.setattr(remote_executor, "_run_subprocess", _fake_run_subprocess)

    results = asyncio.run(
        remote_executor.upload_files(
            config,
            auth,
            [(str(local_file), "/remote/runtime/input/input.fa")],
        )
    )

    assert len(results) == 1
    assert results[0]["success"] is True
    assert results[0]["attempt"] == 2
    assert results[0]["retries_used"] == 1
    assert attempts["count"] == 2


def test_download_remote_run_dir_retries_on_transient_scp_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = remote_executor.RemoteExecutionConfig(
        host="example.com",
        user="user",
        port=22,
        runtime_dir="/remote/runtime",
        local_artifact_root=str(tmp_path / "artifacts"),
        ssh_key_path="/tmp/id_rsa",
        password=None,
        sudo_policy="on_demand",
        connect_timeout=5,
        scp_retries=2,
        scp_retry_delay=0.0,
    )
    auth = remote_executor.ResolvedAuth(mode="key", key_path="/tmp/id_rsa")

    monkeypatch.setattr(remote_executor, "_build_scp_base", lambda *_args, **_kwargs: ["scp"])
    attempts = {"count": 0}

    async def _fake_run_subprocess(args, timeout, display_command=None):  # type: ignore[no-untyped-def]
        _ = (args, timeout, display_command)
        attempts["count"] += 1
        if attempts["count"] == 1:
            return {"success": False, "stderr": "lost connection", "exit_code": 255}
        return {"success": True, "stderr": "", "stdout": "", "exit_code": 0}

    monkeypatch.setattr(remote_executor, "_run_subprocess", _fake_run_subprocess)

    result = asyncio.run(
        remote_executor.download_remote_run_dir(
            config,
            auth,
            remote_run_dir="/remote/runtime/_runs/tool/abc",
            local_target_dir=str(tmp_path / "download"),
        )
    )

    assert result["success"] is True
    assert result["attempt"] == 2
    assert result["retries_used"] == 1
    assert attempts["count"] == 2


def test_execute_remote_bio_tool_uses_remote_uid_gid_for_docker_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_config = remote_executor.RemoteExecutionConfig(
        host="example.com",
        user="user",
        port=22,
        runtime_dir="/remote/runtime",
        local_artifact_root="/tmp/local",
        ssh_key_path=None,
        password="pwd",
        sudo_policy="on_demand",
        connect_timeout=5,
    )
    captured: Dict[str, Any] = {}

    async def _fake_resolve_auth(config_obj):
        _ = config_obj
        return remote_executor.ResolvedAuth(mode="password")

    async def _fake_resolve_uid_gid(config_obj, auth_obj):
        _ = (config_obj, auth_obj)
        return (1000, 1001)

    async def _fake_create_dirs(config_obj, auth_obj, remote_run_dir):
        _ = (config_obj, auth_obj, remote_run_dir)
        return {"success": True}

    async def _fake_upload(config_obj, auth_obj, uploads):
        _ = (config_obj, auth_obj, uploads)
        return []

    def _fake_build_docker_command_args(**kwargs):
        captured["run_as_user"] = kwargs.get("run_as_user")
        return ["docker", "run", "--rm", "fake-image", "true"]

    async def _fake_execute_remote_command(config_obj, auth_obj, command, timeout):
        _ = (config_obj, auth_obj, command, timeout)
        return {"success": True, "stdout": "ok", "stderr": "", "exit_code": 0, "duration_seconds": 0.1}

    async def _fake_download(config_obj, auth_obj, remote_run_dir, local_target_dir):
        _ = (config_obj, auth_obj, remote_run_dir, local_target_dir)
        return {"success": True}

    monkeypatch.setattr(bio_handler_module, "resolve_auth", _fake_resolve_auth)
    monkeypatch.setattr(bio_handler_module, "resolve_remote_uid_gid", _fake_resolve_uid_gid)
    monkeypatch.setattr(bio_handler_module, "create_remote_run_dirs", _fake_create_dirs)
    monkeypatch.setattr(bio_handler_module, "upload_files", _fake_upload)
    monkeypatch.setattr(bio_handler_module, "_build_docker_command_args", _fake_build_docker_command_args)
    monkeypatch.setattr(bio_handler_module, "execute_remote_command", _fake_execute_remote_command)
    monkeypatch.setattr(bio_handler_module, "download_remote_run_dir", _fake_download)

    result = asyncio.run(
        bio_handler_module._execute_remote_bio_tool(
            tool_name="seqkit",
            operation="stats",
            input_file=None,
            output_file=None,
            params=None,
            timeout=30,
            remote_config=remote_config,
        )
    )

    assert result["success"] is True
    assert captured["run_as_user"] == (1000, 1001)
    assert result["remote_uid"] == 1000
    assert result["remote_gid"] == 1001


def test_execute_remote_bio_tool_marks_sync_warning_without_failing_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_config = remote_executor.RemoteExecutionConfig(
        host="example.com",
        user="user",
        port=22,
        runtime_dir="/remote/runtime",
        local_artifact_root="/tmp/local",
        ssh_key_path=None,
        password="pwd",
        sudo_policy="on_demand",
        connect_timeout=5,
    )

    async def _fake_resolve_auth(config_obj):
        _ = config_obj
        return remote_executor.ResolvedAuth(mode="password")

    async def _fake_resolve_uid_gid(config_obj, auth_obj):
        _ = (config_obj, auth_obj)
        return (1000, 1001)

    async def _fake_create_dirs(config_obj, auth_obj, remote_run_dir):
        _ = (config_obj, auth_obj, remote_run_dir)
        return {"success": True}

    async def _fake_upload(config_obj, auth_obj, uploads):
        _ = (config_obj, auth_obj, uploads)
        return []

    def _fake_build_docker_command_args(**kwargs):
        _ = kwargs
        return ["docker", "run", "--rm", "fake-image", "true"]

    async def _fake_execute_remote_command(config_obj, auth_obj, command, timeout):
        _ = (config_obj, auth_obj, command, timeout)
        return {"success": True, "stdout": "ok", "stderr": "", "exit_code": 0, "duration_seconds": 0.1}

    async def _fake_download(config_obj, auth_obj, remote_run_dir, local_target_dir):
        _ = (config_obj, auth_obj, remote_run_dir, local_target_dir)
        return {"success": False, "error": "artifact sync unavailable"}

    monkeypatch.setattr(bio_handler_module, "resolve_auth", _fake_resolve_auth)
    monkeypatch.setattr(bio_handler_module, "resolve_remote_uid_gid", _fake_resolve_uid_gid)
    monkeypatch.setattr(bio_handler_module, "create_remote_run_dirs", _fake_create_dirs)
    monkeypatch.setattr(bio_handler_module, "upload_files", _fake_upload)
    monkeypatch.setattr(bio_handler_module, "_build_docker_command_args", _fake_build_docker_command_args)
    monkeypatch.setattr(bio_handler_module, "execute_remote_command", _fake_execute_remote_command)
    monkeypatch.setattr(bio_handler_module, "download_remote_run_dir", _fake_download)

    result = asyncio.run(
        bio_handler_module._execute_remote_bio_tool(
            tool_name="seqkit",
            operation="stats",
            input_file=None,
            output_file=None,
            params=None,
            timeout=30,
            remote_config=remote_config,
        )
    )

    assert result["success"] is True
    assert "sync_warning" in result
    assert "artifact sync unavailable" in result["sync_warning"]


def test_execute_docker_command_prefers_subprocess_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: Dict[str, Any] = {}

    class _FakeProcess:
        returncode = 0

        async def communicate(self):
            return (b"ok\n", b"")

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProcess()

    async def _fake_shell(*args, **kwargs):  # pragma: no cover - guard rail
        _ = (args, kwargs)
        raise AssertionError("Shell execution path should not be used")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_shell)

    result = asyncio.run(
        bio_handler_module.execute_docker_command(
            ["docker", "run", "--rm", "hello-world"],
            timeout=5,
        )
    )

    assert result["success"] is True
    assert captured["args"][:2] == ("docker", "run")


@pytest.mark.parametrize(
    "pattern",
    [
        "x;touch /tmp/pwn",
        "$(id)",
        "`id`",
    ],
)
def test_build_docker_command_safely_quotes_free_text_parameter(pattern: str) -> None:
    cmd = bio_handler_module.build_docker_command(
        tool_name="seqkit",
        operation="grep",
        input_file="/work/input/test.fa",
        output_file=None,
        extra_params={
            "pattern": pattern,
            "output": "result.fa",
        },
        work_dir_override="/tmp/remote-run",
        validate_input_exists=False,
        run_as_user=(1000, 1001),
    )

    assert " sh -lc " not in cmd
    assert f"-p {shlex.quote(pattern)}" in cmd
    assert f" -p {pattern} " not in cmd


@pytest.mark.parametrize(
    "bad_db",
    [
        "/tmp/db;touch /tmp/pwn",
        "/tmp/db|cat /etc/passwd",
        "/tmp/db&&id",
        "/tmp/db\nid",
        "$(id)",
        "`id`",
    ],
)
def test_build_docker_command_rejects_unsafe_path_parameters(bad_db: str) -> None:
    with pytest.raises(ValueError, match="Invalid/unsafe parameter: db"):
        bio_handler_module.build_docker_command(
            tool_name="blast",
            operation="blastn",
            input_file="/work/input/query.fa",
            output_file=None,
            extra_params={"db": bad_db, "output": "hits.tsv"},
            work_dir_override="/tmp/remote-run",
            validate_input_exists=False,
            run_as_user=(1000, 1001),
        )


def test_build_docker_command_wraps_shell_metacharacters_inside_container() -> None:
    cmd = bio_handler_module.build_docker_command(
        tool_name="minimap2",
        operation="map",
        input_file=None,
        output_file=None,
        extra_params={
            "preset": "map-ont",
            "reference": "/work/input/ref.fa",
            "query": "/work/input/query.fa",
            "output": "output/aln.sam",
        },
        work_dir_override="/tmp/remote-run",
        validate_input_exists=False,
        run_as_user=(1000, 1001),
    )
    assert " sh -lc " in cmd
    assert " > /work/output/aln.sam" in cmd


def test_build_docker_command_sets_tool_specific_env_flags() -> None:
    nextflow_cmd = bio_handler_module.build_docker_command(
        tool_name="nextflow",
        operation="clean",
        input_file=None,
        output_file=None,
        extra_params=None,
        work_dir_override="/tmp/remote-run",
        validate_input_exists=False,
        run_as_user=(1000, 1001),
    )
    assert "-e NXF_HOME=/tmp/.nextflow" in nextflow_cmd
    assert "-e NXF_ASSETS=/tmp/.nextflow/assets" in nextflow_cmd

    virsorter_cmd = bio_handler_module.build_docker_command(
        tool_name="virsorter2",
        operation="run",
        input_file="/work/input/test.fa",
        output_file=None,
        extra_params={"output": "vs2_out", "min_score": 0.5, "threads": 1},
        work_dir_override="/tmp/remote-run",
        validate_input_exists=False,
        run_as_user=(1000, 1001),
    )
    assert "-e SNAKEMAKE_CONDA_PREFIX=/work/output/conda_envs" in virsorter_cmd
    assert "-e CONDA_PKGS_DIRS=/work/output/.conda/pkgs" in virsorter_cmd


def test_build_docker_command_output_file_is_not_double_prefixed() -> None:
    cmd = bio_handler_module.build_docker_command(
        tool_name="seqtk",
        operation="sample",
        input_file="/work/input/test.fasta",
        output_file="sampled.fasta",
        extra_params={"fraction": 1.0},
        work_dir_override="/tmp/remote-run",
        validate_input_exists=False,
        run_as_user=(1000, 1001),
    )
    assert "/work//work/" not in cmd
    assert " > /work/sampled.fasta" in cmd


def test_build_docker_command_iphop_uses_rw_database_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BIO_TOOLS_IPHOP_DB_PATH", raising=False)
    default_cmd = bio_handler_module.build_docker_command(
        tool_name="iphop",
        operation="predict",
        input_file="/work/input/test.fa",
        output_file=None,
        extra_params={"output": "iphop_out", "threads": 1},
        work_dir_override="/tmp/remote-run",
        validate_input_exists=False,
        run_as_user=(1000, 1001),
    )
    assert "/home/zczhao/GAgent/data/databases/bio_tools/iphop/Aug_2023_pub_rw:/work/database" in default_cmd

    monkeypatch.setenv("BIO_TOOLS_IPHOP_DB_PATH", "/tmp/custom_iphop_rw")
    override_cmd = bio_handler_module.build_docker_command(
        tool_name="iphop",
        operation="predict",
        input_file="/work/input/test.fa",
        output_file=None,
        extra_params={"output": "iphop_out", "threads": 1},
        work_dir_override="/tmp/remote-run",
        validate_input_exists=False,
        run_as_user=(1000, 1001),
    )
    assert "/tmp/custom_iphop_rw:/work/database" in override_cmd


def test_build_docker_command_query_falls_back_to_input_when_missing() -> None:
    cmd = bio_handler_module.build_docker_command(
        tool_name="fastani",
        operation="compare",
        input_file="/work/input/query.fa",
        output_file=None,
        extra_params={
            "reference": "/work/input/reference.fa",
            "output": "fastani.out",
            "threads": 1,
        },
        work_dir_override="/tmp/remote-run",
        validate_input_exists=False,
        run_as_user=(1000, 1001),
    )
    assert "fastANI -q /work/input/query.fa -r /work/input/reference.fa" in cmd


def test_build_docker_command_raises_for_missing_required_placeholders() -> None:
    with pytest.raises(ValueError, match="Missing required parameters"):
        bio_handler_module.build_docker_command(
            tool_name="fastani",
            operation="compare",
            input_file=None,
            output_file=None,
            extra_params={"output": "fastani.out", "threads": 1},
            work_dir_override="/tmp/remote-run",
            validate_input_exists=False,
            run_as_user=(1000, 1001),
        )


def test_bio_tools_handler_returns_unified_error_for_unsafe_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "local")
    result = asyncio.run(
        bio_handler_module.bio_tools_handler(
            tool_name="blast",
            operation="blastn",
            input_file="/work/input/query.fa",
            params={
                "db": "/tmp/db;touch /tmp/pwn",
                "output": "hits.tsv",
            },
        )
    )

    assert result["success"] is False
    assert result["error"] == "Invalid/unsafe parameter: db"


def test_bio_tools_handler_converts_fasta_sequence_text_to_input_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: Dict[str, Any] = {}

    async def _fake_local(**kwargs: Any) -> Dict[str, Any]:
        captured.update(kwargs)
        return {"success": True, "tool": "seqkit", "operation": "stats"}

    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "local")
    monkeypatch.setattr(bio_handler_module, "_execute_local_bio_tool", _fake_local)

    result = asyncio.run(
        bio_handler_module.bio_tools_handler(
            tool_name="seqkit",
            operation="stats",
            sequence_text=">seq1\nACGTNN\n",
            session_id="session_inline_fasta",
        )
    )

    assert result["success"] is True
    assert result["input_origin"] == "sequence_text_fasta"
    generated = result.get("generated_input_file")
    assert isinstance(generated, str) and generated.endswith(".fasta")
    assert captured.get("input_file") == generated
    assert Path(generated).exists()
    assert ">seq1" in Path(generated).read_text(encoding="utf-8")


def test_bio_tools_handler_converts_raw_sequence_text_to_fasta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: Dict[str, Any] = {}

    async def _fake_local(**kwargs: Any) -> Dict[str, Any]:
        captured.update(kwargs)
        return {"success": True, "tool": "seqkit", "operation": "stats"}

    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "local")
    monkeypatch.setattr(bio_handler_module, "_execute_local_bio_tool", _fake_local)

    result = asyncio.run(
        bio_handler_module.bio_tools_handler(
            tool_name="seqkit",
            operation="stats",
            sequence_text=" acgt nnnn ",
            session_id="session_inline_raw",
        )
    )

    assert result["success"] is True
    assert result["input_origin"] == "sequence_text_raw"
    generated = result.get("generated_input_file")
    assert isinstance(generated, str)
    content = Path(generated).read_text(encoding="utf-8")
    assert content.startswith(">seq_1\n")
    assert "acgtnnnn" in content.lower()
    assert captured.get("input_file") == generated


def test_bio_tools_handler_rejects_invalid_sequence_text_and_sets_no_claude_fallback() -> None:
    result = asyncio.run(
        bio_handler_module.bio_tools_handler(
            tool_name="seqkit",
            operation="stats",
            sequence_text="ACGT;touch /tmp/pwn",
            session_id="session_inline_invalid",
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "invalid_sequence_text"
    assert result["error_stage"] == "input_preparation"
    assert result["no_claude_fallback"] is True


def test_bio_tools_handler_rejects_ambiguous_input_file_and_sequence_text() -> None:
    result = asyncio.run(
        bio_handler_module.bio_tools_handler(
            tool_name="seqkit",
            operation="stats",
            input_file="/tmp/a.fa",
            sequence_text=">x\nACGT\n",
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "sequence_input_ambiguous"
    assert result["error_stage"] == "input_preparation"
    assert result["no_claude_fallback"] is True


def test_tools_bio_tools_endpoint_returns_backward_compatible_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_execute_tool(name: str, **kwargs: Any) -> Dict[str, Any]:
        assert name == "bio_tools"
        _ = kwargs
        return {
            "success": True,
            "tool": "seqkit",
            "operation": "stats",
            "execution_mode": "remote",
            "execution_host": "119.147.24.196",
            "run_id": "abc123",
            "remote_run_dir": "/remote/run/abc123",
            "local_artifact_dir": "/Volumes/BIOINFO2/docker/remote_bio_tools/seqkit/abc123",
        }

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)

    app = FastAPI()
    app.include_router(tool_routes.router)
    client = TestClient(app)
    try:
        response = client.post(
            "/tools/bio-tools",
            json={
                "tool_name": "seqkit",
                "operation": "stats",
                "input_file": "/tmp/input.fasta",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["tool"] == "seqkit"
        assert payload["operation"] == "stats"
        assert payload["result"]["execution_mode"] == "remote"
        assert payload["result"]["run_id"] == "abc123"
    finally:
        client.close()


def test_tools_bio_tools_endpoint_forwards_sequence_text_and_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_execute_tool(name: str, **kwargs: Any) -> Dict[str, Any]:
        assert name == "bio_tools"
        assert kwargs["tool_name"] == "seqkit"
        assert kwargs["operation"] == "stats"
        assert kwargs["sequence_text"].startswith(">seq1")
        assert kwargs["session_id"] == "session_api_inline"
        return {
            "success": True,
            "tool": "seqkit",
            "operation": "stats",
            "input_origin": "sequence_text_fasta",
            "generated_input_file": "/tmp/generated.fasta",
        }

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)

    app = FastAPI()
    app.include_router(tool_routes.router)
    client = TestClient(app)
    try:
        response = client.post(
            "/tools/bio-tools",
            json={
                "tool_name": "seqkit",
                "operation": "stats",
                "sequence_text": ">seq1\nACGT\n",
                "session_id": "session_api_inline",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["result"]["input_origin"] == "sequence_text_fasta"
    finally:
        client.close()


def test_bio_tools_handler_background_mode_submits_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: Dict[str, Any] = {}

    def _fake_submit_background(**kwargs: Any) -> Dict[str, Any]:
        captured.update(kwargs)
        return {
            "success": True,
            "background": True,
            "job_id": "bio_test_job",
            "status": "queued",
        }

    monkeypatch.setattr(
        bio_handler_module,
        "_submit_background_bio_tools_job",
        _fake_submit_background,
    )

    result = asyncio.run(
        bio_handler_module.bio_tools_handler(
            tool_name="seqkit",
            operation="head",
            sequence_text=">seq1\nACGT\n",
            session_id="session_bg_submit",
            params={"background": True, "count": 1},
            timeout=0,
        )
    )

    assert result["success"] is True
    assert result["background"] is True
    assert result["job_id"] == "bio_test_job"
    assert captured["timeout"] is None
    assert captured["params"] == {"count": "1"}


def test_bio_tools_handler_job_status_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeManager:
        def get_job_payload(self, job_id: str) -> Dict[str, Any]:
            assert job_id == "bio_job_123"
            return {
                "job_id": "bio_job_123",
                "job_type": "bio_tools_run",
                "status": "running",
                "result": None,
            }

    monkeypatch.setattr(
        bio_handler_module,
        "_get_plan_job_manager",
        lambda: _FakeManager(),
    )

    result = asyncio.run(
        bio_handler_module.bio_tools_handler(
            tool_name="job",
            operation="job_status",
            params={"job_id": "bio_job_123"},
        )
    )

    assert result["success"] is True
    assert result["job_id"] == "bio_job_123"
    assert result["status"] == "running"


def test_tools_bio_tools_endpoint_supports_zero_timeout_and_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_execute_tool(name: str, **kwargs: Any) -> Dict[str, Any]:
        assert name == "bio_tools"
        assert kwargs["timeout"] == 0
        assert kwargs["background"] is True
        return {
            "success": True,
            "background": True,
            "job_id": "bio_test_job",
            "status": "queued",
        }

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)

    app = FastAPI()
    app.include_router(tool_routes.router)
    client = TestClient(app)
    try:
        response = client.post(
            "/tools/bio-tools",
            json={
                "tool_name": "seqkit",
                "operation": "stats",
                "params": {"threads": 1},
                "timeout": 0,
                "background": True,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["result"]["background"] is True
        assert payload["result"]["job_id"] == "bio_test_job"
    finally:
        client.close()


def test_tools_bio_tools_job_status_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_execute_tool(name: str, **kwargs: Any) -> Dict[str, Any]:
        assert name == "bio_tools"
        assert kwargs["operation"] == "job_status"
        assert kwargs["params"]["job_id"] == "bio_test_job"
        return {
            "success": True,
            "operation": "job_status",
            "job_id": "bio_test_job",
            "status": "running",
            "job": {"job_id": "bio_test_job", "job_type": "bio_tools_run"},
        }

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)

    app = FastAPI()
    app.include_router(tool_routes.router)
    client = TestClient(app)
    try:
        response = client.get("/tools/bio-tools/jobs/bio_test_job")
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["job_id"] == "bio_test_job"
        assert payload["status"] == "running"
    finally:
        client.close()
