#!/usr/bin/env python3
"""Remote execution helpers for bio_tools.

This module executes commands on a remote host via SSH, supports key-first
authentication with password fallback, and handles file transfer through scp.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

_AUTH_FAILURE_MARKERS = (
    "permission denied",
    "publickey",
    "authentication failed",
)

_DOCKER_PERMISSION_MARKERS = (
    "got permission denied while trying to connect to the docker daemon socket",
    "permission denied",
)

_SCP_TRANSIENT_FAILURE_MARKERS = (
    "connection closed",
    "connection reset",
    "broken pipe",
    "lost connection",
    "connection timed out",
    "operation timed out",
)


@dataclass
class RemoteExecutionConfig:
    host: str
    user: str
    port: int
    runtime_dir: str
    local_artifact_root: str
    ssh_key_path: Optional[str]
    password: Optional[str]
    sudo_policy: str
    connect_timeout: int
    scp_retries: int = 2
    scp_retry_delay: float = 1.5

    @classmethod
    def from_env(cls) -> "RemoteExecutionConfig":
        host = os.getenv("BIO_TOOLS_REMOTE_HOST", "119.147.24.196").strip()
        user = os.getenv("BIO_TOOLS_REMOTE_USER", "zczhao").strip()
        port_raw = os.getenv("BIO_TOOLS_REMOTE_PORT", "22").strip() or "22"
        runtime_dir = os.getenv("BIO_TOOLS_REMOTE_RUNTIME_DIR", "/home/zczhao/GAgent/runtime/bio_tools").strip()
        local_artifact_root = os.getenv(
            "BIO_TOOLS_REMOTE_LOCAL_ARTIFACT_ROOT", "/Volumes/BIOINFO2/docker/remote_bio_tools"
        ).strip()
        ssh_key_path = (os.getenv("BIO_TOOLS_REMOTE_SSH_KEY_PATH") or "").strip() or None
        password = (os.getenv("BIO_TOOLS_REMOTE_PASSWORD") or "").strip() or None
        sudo_policy = (os.getenv("BIO_TOOLS_REMOTE_SUDO_POLICY", "on_demand") or "on_demand").strip().lower()
        timeout_raw = os.getenv("BIO_TOOLS_REMOTE_CONNECT_TIMEOUT", "15").strip() or "15"
        scp_retries_raw = os.getenv("BIO_TOOLS_REMOTE_SCP_RETRIES", "2").strip() or "2"
        scp_retry_delay_raw = os.getenv("BIO_TOOLS_REMOTE_SCP_RETRY_DELAY", "1.5").strip() or "1.5"

        try:
            port = int(port_raw)
        except ValueError:
            port = 22

        try:
            connect_timeout = int(timeout_raw)
        except ValueError:
            connect_timeout = 15

        try:
            scp_retries = int(scp_retries_raw)
        except ValueError:
            scp_retries = 2

        try:
            scp_retry_delay = float(scp_retry_delay_raw)
        except ValueError:
            scp_retry_delay = 1.5

        if sudo_policy not in {"on_demand", "always", "never"}:
            sudo_policy = "on_demand"

        return cls(
            host=host,
            user=user,
            port=port,
            runtime_dir=runtime_dir,
            local_artifact_root=local_artifact_root,
            ssh_key_path=ssh_key_path,
            password=password,
            sudo_policy=sudo_policy,
            connect_timeout=max(1, connect_timeout),
            scp_retries=max(0, scp_retries),
            scp_retry_delay=max(0.0, scp_retry_delay),
        )

    def missing_required(self) -> List[str]:
        missing: List[str] = []
        if not self.host:
            missing.append("BIO_TOOLS_REMOTE_HOST")
        if not self.user:
            missing.append("BIO_TOOLS_REMOTE_USER")
        if not self.runtime_dir:
            missing.append("BIO_TOOLS_REMOTE_RUNTIME_DIR")
        has_key = bool(self.ssh_key_path and Path(self.ssh_key_path).expanduser().exists())
        has_password = bool(self.password)
        if not has_key and not has_password:
            missing.append("BIO_TOOLS_REMOTE_SSH_KEY_PATH or BIO_TOOLS_REMOTE_PASSWORD")
        return missing

    def has_key(self) -> bool:
        if not self.ssh_key_path:
            return False
        return Path(self.ssh_key_path).expanduser().exists()

    def has_password(self) -> bool:
        return bool(self.password)


@dataclass
class ResolvedAuth:
    mode: str  # key | password
    key_path: Optional[str] = None


def _is_auth_failure(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _AUTH_FAILURE_MARKERS)


def _needs_sudo_retry(text: str) -> bool:
    low = (text or "").lower()
    return (
        "docker" in low
        and any(marker in low for marker in _DOCKER_PERMISSION_MARKERS)
    )


def _redact_args(args: Sequence[str]) -> str:
    redacted: List[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token == "-p" and i > 0 and args[i - 1] == "sshpass":
            redacted.extend([token, "<redacted>"])
            i += 2
            continue
        redacted.append(token)
        i += 1
    return " ".join(shlex.quote(x) for x in redacted)


async def _run_subprocess(
    args: Sequence[str],
    timeout: Optional[int],
    *,
    display_command: Optional[str] = None,
) -> dict:
    start_time = datetime.now()
    command_display = display_command or _redact_args(args)
    effective_timeout: Optional[int] = None
    if timeout is not None:
        try:
            parsed_timeout = int(timeout)
        except (TypeError, ValueError):
            parsed_timeout = 0
        if parsed_timeout > 0:
            effective_timeout = parsed_timeout
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if effective_timeout is None:
            stdout, stderr = await process.communicate()
        else:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=effective_timeout,
            )
        duration = (datetime.now() - start_time).total_seconds()
        return {
            "success": process.returncode == 0,
            "exit_code": process.returncode,
            "stdout": stdout.decode("utf-8", errors="replace") if stdout else "",
            "stderr": stderr.decode("utf-8", errors="replace") if stderr else "",
            "duration_seconds": duration,
            "command": command_display,
        }
    except asyncio.TimeoutError:
        return {
            "success": False,
            "error": f"Command timed out after {effective_timeout} seconds",
            "command": command_display,
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "success": False,
            "error": str(exc),
            "command": command_display,
        }


def _build_ssh_base(config: RemoteExecutionConfig, auth: ResolvedAuth) -> List[str]:
    args: List[str] = []
    if auth.mode == "password":
        if not shutil.which("sshpass"):
            raise RuntimeError("sshpass is required for password-based remote execution")
        if not config.password:
            raise RuntimeError("BIO_TOOLS_REMOTE_PASSWORD is required for password auth")
        args.extend(["sshpass", "-p", config.password])
    args.append("ssh")
    args.extend(
        [
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            f"ConnectTimeout={config.connect_timeout}",
            "-p",
            str(config.port),
        ]
    )
    if auth.mode == "key":
        if not auth.key_path:
            raise RuntimeError("SSH key auth selected but no key path provided")
        args.extend(["-i", auth.key_path, "-o", "BatchMode=yes"])
    return args


def _build_scp_base(config: RemoteExecutionConfig, auth: ResolvedAuth) -> List[str]:
    args: List[str] = []
    if auth.mode == "password":
        if not shutil.which("sshpass"):
            raise RuntimeError("sshpass is required for password-based remote execution")
        if not config.password:
            raise RuntimeError("BIO_TOOLS_REMOTE_PASSWORD is required for password auth")
        args.extend(["sshpass", "-p", config.password])
    args.append("scp")
    args.extend(
        [
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            f"ConnectTimeout={config.connect_timeout}",
            "-P",
            str(config.port),
        ]
    )
    if auth.mode == "key":
        if not auth.key_path:
            raise RuntimeError("SSH key auth selected but no key path provided")
        args.extend(["-i", auth.key_path])
    return args


def _remote_shell_arg(command: str) -> str:
    return "bash -lc " + shlex.quote(command)


def _is_transient_scp_failure(result: dict) -> bool:
    text = "\n".join(
        str(result.get(key, "")) for key in ("stderr", "stdout", "error")
    ).lower()
    return any(marker in text for marker in _SCP_TRANSIENT_FAILURE_MARKERS)


async def _run_scp_with_retries(
    args: Sequence[str],
    timeout: Optional[int],
    *,
    display_command: str,
    retries: int,
    retry_delay: float,
) -> dict:
    attempt = 0
    last_result: dict = {}
    while True:
        attempt += 1
        result = await _run_subprocess(args, timeout=timeout, display_command=display_command)
        result["attempt"] = attempt
        if result.get("success"):
            result["retries_used"] = attempt - 1
            return result
        last_result = result
        if attempt > retries or not _is_transient_scp_failure(result):
            result["retries_used"] = attempt - 1
            return result
        if retry_delay > 0:
            await asyncio.sleep(retry_delay)
    return last_result


async def _run_ssh_command(
    config: RemoteExecutionConfig,
    auth: ResolvedAuth,
    command: str,
    timeout: Optional[int],
    *,
    display_command: Optional[str] = None,
) -> dict:
    base = _build_ssh_base(config, auth)
    target = f"{config.user}@{config.host}"
    args = base + [target, _remote_shell_arg(command)]
    return await _run_subprocess(args, timeout, display_command=display_command)


async def resolve_auth(config: RemoteExecutionConfig) -> ResolvedAuth:
    # Key first
    if config.has_key():
        key_path = str(Path(config.ssh_key_path or "").expanduser())
        key_auth = ResolvedAuth(mode="key", key_path=key_path)
        probe = await _run_ssh_command(
            config,
            key_auth,
            "echo __BIO_TOOLS_REMOTE_AUTH_OK__",
            timeout=config.connect_timeout,
            display_command=f"ssh(key) {config.user}@{config.host} <auth probe>",
        )
        if probe.get("success"):
            return key_auth
        if not _is_auth_failure(probe.get("stderr", "")):
            raise RuntimeError(
                "SSH key authentication failed before password fallback: "
                + (probe.get("stderr") or probe.get("error") or "unknown error")
            )

    # Password fallback
    if config.has_password():
        pwd_auth = ResolvedAuth(mode="password")
        probe = await _run_ssh_command(
            config,
            pwd_auth,
            "echo __BIO_TOOLS_REMOTE_AUTH_OK__",
            timeout=config.connect_timeout,
            display_command=f"ssh(password) {config.user}@{config.host} <auth probe>",
        )
        if probe.get("success"):
            return pwd_auth
        raise RuntimeError(
            "Password authentication failed: "
            + (probe.get("stderr") or probe.get("error") or "unknown error")
        )

    raise RuntimeError("No valid remote auth available (SSH key or password required)")


async def create_remote_run_dirs(
    config: RemoteExecutionConfig,
    auth: ResolvedAuth,
    remote_run_dir: str,
) -> dict:
    cmd = (
        f"mkdir -p {shlex.quote(remote_run_dir)} "
        f"{shlex.quote(remote_run_dir + '/input')} "
        f"{shlex.quote(remote_run_dir + '/output')}"
    )
    return await _run_ssh_command(
        config,
        auth,
        cmd,
        timeout=config.connect_timeout + 10,
        display_command=f"ssh {config.user}@{config.host} <mkdir run dirs>",
    )


async def resolve_remote_uid_gid(
    config: RemoteExecutionConfig,
    auth: ResolvedAuth,
) -> Tuple[int, int]:
    result = await _run_ssh_command(
        config,
        auth,
        "id -u; id -g",
        timeout=config.connect_timeout + 10,
        display_command=f"ssh {config.user}@{config.host} <resolve uid gid>",
    )
    if not result.get("success"):
        raise RuntimeError(
            "Failed to resolve remote uid/gid: "
            + (result.get("stderr") or result.get("error") or "unknown error")
        )

    values: List[int] = []
    for line in (result.get("stdout") or "").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            values.append(int(text))
        except ValueError:
            continue

    if len(values) < 2:
        raise RuntimeError(
            "Failed to parse remote uid/gid from output: "
            + repr(result.get("stdout", ""))
        )
    return values[0], values[1]


async def upload_files(
    config: RemoteExecutionConfig,
    auth: ResolvedAuth,
    uploads: Iterable[Tuple[str, str]],
) -> List[dict]:
    results: List[dict] = []
    for local_path, remote_path in uploads:
        local_abs = str(Path(local_path).expanduser().resolve())
        scp_base = _build_scp_base(config, auth)
        target = f"{config.user}@{config.host}:{remote_path}"
        args = scp_base + [local_abs, target]
        result = await _run_scp_with_retries(
            args,
            timeout=max(config.connect_timeout + 120, 180),
            display_command=f"scp {local_abs} -> {config.user}@{config.host}:{remote_path}",
            retries=config.scp_retries,
            retry_delay=config.scp_retry_delay,
        )
        result["local_path"] = local_abs
        result["remote_path"] = remote_path
        results.append(result)
        if not result.get("success"):
            break
    return results


def _wrap_with_sudo(config: RemoteExecutionConfig, command: str) -> str:
    if not config.password:
        raise RuntimeError(
            "BIO_TOOLS_REMOTE_PASSWORD is required for sudo execution on remote host"
        )
    password_expr = shlex.quote(config.password)
    return (
        f"printf '%s\\n' {password_expr} | "
        "sudo -S -p '' bash -lc "
        + shlex.quote(command)
    )


async def execute_remote_command(
    config: RemoteExecutionConfig,
    auth: ResolvedAuth,
    command: str,
    timeout: Optional[int],
) -> dict:
    policy = config.sudo_policy

    effective_command = command
    used_sudo = False

    if policy == "always":
        effective_command = _wrap_with_sudo(config, command)
        used_sudo = True

    result = await _run_ssh_command(
        config,
        auth,
        effective_command,
        timeout=timeout,
        display_command=f"ssh {config.user}@{config.host} <remote command>",
    )

    if (
        not result.get("success")
        and policy == "on_demand"
        and _needs_sudo_retry((result.get("stderr") or "") + "\n" + (result.get("stdout") or ""))
        and config.password
    ):
        retry_cmd = _wrap_with_sudo(config, command)
        retry_result = await _run_ssh_command(
            config,
            auth,
            retry_cmd,
            timeout=timeout,
            display_command=f"ssh {config.user}@{config.host} <remote command with sudo>",
        )
        retry_result["sudo_retry"] = True
        retry_result["sudo_used"] = True
        return retry_result

    result["sudo_used"] = used_sudo
    return result


async def download_remote_run_dir(
    config: RemoteExecutionConfig,
    auth: ResolvedAuth,
    remote_run_dir: str,
    local_target_dir: str,
) -> dict:
    target_dir = Path(local_target_dir).expanduser()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {
            "success": False,
            "error": f"Failed to prepare local artifact directory '{target_dir}': {exc}",
        }

    scp_base = _build_scp_base(config, auth)
    remote_spec = f"{config.user}@{config.host}:{remote_run_dir}/."
    args = scp_base + ["-r", remote_spec, str(target_dir)]
    return await _run_scp_with_retries(
        args,
        timeout=max(config.connect_timeout + 300, 600),
        display_command=f"scp -r {config.user}@{config.host}:{remote_run_dir}/. -> {target_dir}",
        retries=config.scp_retries,
        retry_delay=config.scp_retry_delay,
    )
