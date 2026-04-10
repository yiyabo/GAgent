"""Agent-driven Qwen Code execution inside Docker containers.

Provides a high-level API for running ``qwen`` CLI commands inside a long-lived
container, giving:

- Filesystem persistence (installed packages, generated files survive across calls)
- Environment isolation (no host pollution)
- Qwen session continuity via ``--session-id``
- Structured JSON output (same contract as the host subprocess path)

Architecture::

    code_executor_handler()
      ↓ use_qwen_code_backend=True + Docker available
    QwenSessionDriver.ensure_container()   ← manages per-session containers
    build qwen CLI args with stable --session-id
    docker exec <container> qwen ...

``exec_qwen_task()`` remains available as a lower-level helper for future
callers that want to bypass the generic subprocess wrapper.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Re-use image/limits from docker_pty_backend
_DEFAULT_IMAGE = os.getenv("QWEN_CODE_DOCKER_IMAGE", "gagent-qwen-code-runtime:latest")
_MEMORY_LIMIT = os.getenv("QWEN_CODE_CONTAINER_MEMORY", "4g")
_PIDS_LIMIT = int(os.getenv("QWEN_CODE_CONTAINER_PIDS", "512"))
_CONTAINER_LABEL = "gagent.component=qwen-code-agent"


def _sanitise_container_suffix(session_id: str) -> str:
    """Derive a DNS-safe suffix from a session_id."""
    slug = re.sub(r"[^a-zA-Z0-9]", "-", session_id)[:40].strip("-") or "default"
    return slug


class QwenSessionDriver:
    """Manages long-lived Docker containers for agent-driven qwen execution.

    Each logical session (keyed by *session_id*) gets at most one running
    container.  Successive calls to :meth:`ensure_container` reuse it.
    """

    def __init__(self) -> None:
        self._containers: Dict[str, str] = {}  # session_id → container_name
        self._session_ids: Dict[str, str] = {}  # session_id → qwen --session-id value
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Container lifecycle
    # ------------------------------------------------------------------

    async def ensure_container(
        self,
        session_id: str,
        *,
        host_work_dir: str,
        extra_mounts: Optional[List[Tuple[str, str]]] = None,
        image: Optional[str] = None,
    ) -> str:
        """Return a running container name, creating one if necessary.

        Parameters
        ----------
        session_id:
            Logical identifier (e.g. chat session id).
        host_work_dir:
            The *host* directory where task outputs live.  Bind-mounted at
            the **same absolute path** inside the container so that file
            references in the qwen prompt are valid.
        extra_mounts:
            Additional ``(host_path, container_path)`` pairs.  Paths are
            only mounted if the host path exists.
        image:
            Docker image override.
        """
        async with self._lock:
            name = self._containers.get(session_id)
            if name and await self._container_running(name):
                return name

            # Container doesn't exist or is dead — create a new one
            image = image or _DEFAULT_IMAGE
            await self._check_image(image)

            name = f"gagent-qc-agent-{_sanitise_container_suffix(session_id)}"
            # Remove stale container with the same name (idempotent)
            await self._force_remove(name)

            env_map = self._build_env()
            env_file_path: Optional[str] = None
            try:
                if env_map:
                    fd, env_file_path = tempfile.mkstemp(suffix=".env", prefix="gagent_qcd_")
                    with os.fdopen(fd, "w") as f:
                        for k, v in env_map.items():
                            f.write(f"{k}={v}\n")

                docker_cmd: List[str] = [
                    "docker", "run", "-d",
                    "--name", name,
                    "--label", _CONTAINER_LABEL,
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    f"--memory={_MEMORY_LIMIT}",
                    f"--pids-limit={_PIDS_LIMIT}",
                    "--user", f"{os.getuid()}:{os.getgid()}",
                ]
                if env_file_path:
                    docker_cmd.extend(["--env-file", env_file_path])

                # Mount work_dir at the same host path inside the container
                real_work = os.path.realpath(host_work_dir)
                os.makedirs(real_work, exist_ok=True)
                docker_cmd.extend(["-v", f"{real_work}:{real_work}"])

                if extra_mounts:
                    seen = {real_work}
                    for host_p, cont_p in extra_mounts:
                        rp = os.path.realpath(host_p)
                        if rp in seen or not os.path.exists(rp):
                            continue
                        seen.add(rp)
                        docker_cmd.extend(["-v", f"{rp}:{cont_p}"])

                docker_cmd.extend([
                    "-w", real_work,
                    image,
                    "sleep", "infinity",
                ])

                logger.info("Creating qwen-code agent container %s (image=%s)", name, image)
                proc = await asyncio.create_subprocess_exec(
                    *docker_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                if proc.returncode != 0:
                    raise RuntimeError(f"Failed to create container: {stderr.decode().strip()}")
                logger.info("Agent container %s started", name)
            finally:
                if env_file_path:
                    try:
                        os.unlink(env_file_path)
                    except OSError:
                        pass

            self._containers[session_id] = name
            # Stable qwen session-id for context continuity
            self._session_ids.setdefault(session_id, f"agent-{_sanitise_container_suffix(session_id)}")
            return name

    async def exec_qwen_task(
        self,
        container_name: str,
        qwen_args: List[str],
        *,
        cwd: Optional[str] = None,
        timeout: float = 600.0,
        on_stdout: Optional[Callable[[str], Awaitable[None]]] = None,
        on_stderr: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> Tuple[int, str, str]:
        """Run ``docker exec <container> qwen <args>`` and collect output.

        Returns (exit_code, stdout_text, stderr_text).
        """
        cmd: List[str] = ["docker", "exec"]
        if cwd:
            cmd.extend(["-w", cwd])
        cmd.extend([container_name, "qwen"] + qwen_args)

        logger.info(
            "[QWEN_SESSION_DRIVER] exec in %s: qwen %s (cwd=%s)",
            container_name, " ".join(qwen_args[:4]) + ("..." if len(qwen_args) > 4 else ""),
            cwd or "(default)",
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_lines: List[str] = []
        stderr_lines: List[str] = []

        async def _drain(stream: asyncio.StreamReader, lines: List[str],
                         callback: Optional[Callable[[str], Awaitable[None]]]) -> None:
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                lines.append(line)
                if callback:
                    try:
                        capped = line[:4000] + "..." if len(line) > 4000 else line
                        await callback(capped)
                    except Exception:
                        pass

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _drain(proc.stdout, stdout_lines, on_stdout),  # type: ignore[arg-type]
                    _drain(proc.stderr, stderr_lines, on_stderr),  # type: ignore[arg-type]
                ),
                timeout=timeout,
            )
            exit_code = await proc.wait()
        except asyncio.TimeoutError:
            logger.warning("[QWEN_SESSION_DRIVER] Timeout after %.0fs, killing", timeout)
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            exit_code = -1
            stderr_lines.append(f"[TIMEOUT] docker exec killed after {timeout:.0f}s")
        except asyncio.CancelledError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            raise

        return exit_code, "\n".join(stdout_lines), "\n".join(stderr_lines)

    def get_qwen_session_id(self, session_id: str) -> Optional[str]:
        """Return the stable qwen ``--session-id`` for context continuity."""
        return self._session_ids.get(session_id)

    async def cleanup(self, session_id: str) -> None:
        """Stop and remove the container for a session."""
        async with self._lock:
            name = self._containers.pop(session_id, None)
            self._session_ids.pop(session_id, None)
        if name:
            await self._force_remove(name)

    async def cleanup_all(self) -> None:
        """Remove all managed containers."""
        async with self._lock:
            names = list(self._containers.values())
            self._containers.clear()
            self._session_ids.clear()
        for name in names:
            await self._force_remove(name)

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_env() -> Dict[str, str]:
        """Build container env (Qwen API creds, writable HOME)."""
        env: Dict[str, str] = {}
        qwen_key = os.getenv("QWEN_API_KEY", "").strip()
        if qwen_key:
            env["OPENAI_API_KEY"] = qwen_key
        env["OPENAI_BASE_URL"] = (
            os.getenv("QWEN_CODE_BASE_URL", "").strip()
            or os.getenv("OPENAI_BASE_URL", "").strip()
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        env["TERM"] = "xterm-256color"
        env["HOME"] = "/tmp/gagent_home"
        model = os.getenv("QWEN_CODE_MODEL", "").strip()
        if model:
            env["QWEN_CODE_MODEL"] = model
        return env

    @staticmethod
    async def _check_image(image: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", image,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Docker image '{image}' not found. "
                f"Build with: ./scripts/build_qwen_code_runtime_image.sh"
            )

    @staticmethod
    async def _container_running(name: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "inspect", "-f", "{{.State.Running}}", name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode().strip().lower() == "true"
        except Exception:
            return False

    @staticmethod
    async def _force_remove(name: str) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
        except Exception:
            pass


# Module-level singleton
_driver: Optional[QwenSessionDriver] = None
_driver_lock = threading.Lock()


def get_qwen_session_driver() -> QwenSessionDriver:
    """Return the module-level QwenSessionDriver singleton (thread-safe)."""
    global _driver
    if _driver is None:
        with _driver_lock:
            if _driver is None:
                _driver = QwenSessionDriver()
    return _driver
