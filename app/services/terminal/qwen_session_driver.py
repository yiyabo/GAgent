"""Agent-driven Qwen Code execution inside Docker containers.

Provides a high-level API for running ``qwen`` CLI commands inside a long-lived
container, giving:

- Filesystem persistence (installed packages, generated files survive across calls)
- Environment isolation (no host pollution)
- Stable Qwen session identity for ``--session-id`` / ``--resume`` handoff
- Structured JSON output (same contract as the host subprocess path)

Architecture::

    code_executor_handler()
      ↓ use_qwen_code_backend=True + Docker available
    QwenSessionDriver.ensure_container()   ← manages per-session containers
        build qwen CLI args with a stable Qwen session id
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

from .docker_pty_backend import (
    CONTAINER_EXEC_PATH,
    QWEN_EXECUTABLE,
    DockerPTYBackend,
    _sanitise_qwen_session_id,
)
from .session_manager import terminal_session_manager

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
        self._locks: Dict[str, asyncio.Lock] = {}
        self._shared_terminal_ids: Dict[str, str] = {}  # session_id → terminal_id
        self._identity_file_paths: Dict[str, Tuple[str, str]] = {}
        self._lock = asyncio.Lock()
        # Idle container TTL tracking
        self._last_used: Dict[str, float] = {}  # session_id → monotonic timestamp
        self._ttl_task: Optional[asyncio.Task] = None
        self._ttl_seconds: float = float(
            os.getenv("QWEN_CONTAINER_IDLE_TTL", "600")
        )  # default 10 minutes
        self._ttl_check_interval: float = 60.0  # check every 60 seconds

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
            if name:
                if session_id in self._shared_terminal_ids:
                    terminal_id = self._shared_terminal_ids.get(session_id)
                    if terminal_id:
                        try:
                            session = await terminal_session_manager.get_session(terminal_id)
                        except KeyError:
                            self._shared_terminal_ids.pop(session_id, None)
                        else:
                            backend = session.backend
                            if (
                                isinstance(backend, DockerPTYBackend)
                                and backend.container_name == name
                                and await self._container_running(name)
                            ):
                                self._touch(session_id)
                                return name
                elif await self._container_running(name):
                    self._touch(session_id)
                    return name
                self._containers.pop(session_id, None)
                self._session_ids.pop(session_id, None)
                self._locks.pop(session_id, None)
                self._shared_terminal_ids.pop(session_id, None)

            requires_alias_mounts = any(
                str(host_path or "").strip()
                and str(container_path or "").strip()
                and os.path.realpath(str(host_path)) != os.path.normpath(str(container_path))
                for host_path, container_path in (extra_mounts or [])
            )

            required_paths = [os.path.realpath(host_work_dir)]
            for host_path, _container_path in extra_mounts or []:
                token = str(host_path or "").strip()
                if token:
                    required_paths.append(os.path.realpath(token))

            if not requires_alias_mounts:
                shared_session = await terminal_session_manager.ensure_qwen_code_session(
                    session_id,
                    required_paths=required_paths,
                )
                shared_backend = shared_session.backend
                if isinstance(shared_backend, DockerPTYBackend):
                    shared_container = shared_backend.container_name
                    if shared_container and all(
                        shared_backend.covers_path(path) for path in required_paths
                    ):
                        self._containers[session_id] = shared_container
                        self._session_ids[session_id] = (
                            shared_backend.qwen_session_id or shared_session.session_id
                        )
                        self._locks[session_id] = shared_session.command_lock
                        self._shared_terminal_ids[session_id] = shared_session.terminal_id
                        logger.info(
                            "Reusing shared qwen_code terminal session %s for agent execution",
                            shared_session.terminal_id,
                        )
                        self._touch(session_id)
                        return shared_container
            else:
                logger.info(
                    "Creating dedicated qwen agent container for %s because alias mounts are required",
                    session_id,
                )

            # Container doesn't exist or is dead — create a new one
            image = image or _DEFAULT_IMAGE
            await self._check_image(image)

            name = f"gagent-qc-agent-{_sanitise_container_suffix(session_id)}"
            # Remove stale container with the same name (idempotent)
            await self._force_remove(name)

            env_map = self._build_env()
            env_file_path: Optional[str] = None
            identity_paths: Optional[Tuple[str, str]] = None
            container_started = False
            try:
                if env_map:
                    fd, env_file_path = tempfile.mkstemp(suffix=".env", prefix="gagent_qcd_")
                    with os.fdopen(fd, "w") as f:
                        for k, v in env_map.items():
                            f.write(f"{k}={v}\n")
                identity_paths = DockerPTYBackend._create_identity_mount_files()

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
                if identity_paths:
                    docker_cmd.extend([
                        "-v", f"{identity_paths[0]}:/etc/passwd:ro",
                        "-v", f"{identity_paths[1]}:/etc/group:ro",
                    ])

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
                container_started = True
                if identity_paths:
                    self._identity_file_paths[session_id] = identity_paths
            finally:
                if env_file_path:
                    try:
                        os.unlink(env_file_path)
                    except OSError:
                        pass
                if not container_started and identity_paths:
                    DockerPTYBackend._cleanup_identity_files(identity_paths)

            self._containers[session_id] = name
            # Stable qwen session id for initial --session-id and later --resume.
            self._session_ids.setdefault(
                session_id,
                _sanitise_qwen_session_id(f"agent:{session_id}"),
            )
            self._locks.setdefault(session_id, asyncio.Lock())
            self._shared_terminal_ids.pop(session_id, None)
            self._touch(session_id)
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
        # Touch all sessions using this container to reset idle TTL
        for sid, cname in self._containers.items():
            if cname == container_name:
                self._touch(sid)
        cmd: List[str] = ["docker", "exec", "-e", f"PATH={CONTAINER_EXEC_PATH}"]
        if cwd:
            cmd.extend(["-w", cwd])
        normalized_args = list(qwen_args)
        if normalized_args and normalized_args[0] in {"qwen", QWEN_EXECUTABLE}:
            normalized_args = normalized_args[1:]
        cmd.extend([container_name, QWEN_EXECUTABLE] + normalized_args)

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
        """Return the stable Qwen session id reused across conversational turns."""
        return self._session_ids.get(session_id)

    def get_execution_lock(self, session_id: str) -> asyncio.Lock:
        """Return the per-session execution lock used by agent/UI shared qwen access."""
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    async def cleanup(self, session_id: str) -> None:
        """Stop and remove the container for a session."""
        async with self._lock:
            name = self._containers.pop(session_id, None)
            self._session_ids.pop(session_id, None)
            self._locks.pop(session_id, None)
            shared_terminal_id = self._shared_terminal_ids.pop(session_id, None)
            identity_paths = self._identity_file_paths.pop(session_id, None)
            self._last_used.pop(session_id, None)
        if shared_terminal_id:
            return
        if name:
            await self._force_remove(name)
        if identity_paths:
            DockerPTYBackend._cleanup_identity_files(identity_paths)

    async def cleanup_all(self) -> None:
        """Remove all managed containers."""
        async with self._lock:
            shared_session_ids = set(self._shared_terminal_ids.keys())
            names = [
                name
                for session_id, name in self._containers.items()
                if session_id not in shared_session_ids
            ]
            self._containers.clear()
            self._session_ids.clear()
            self._locks.clear()
            self._shared_terminal_ids.clear()
            identity_paths = list(self._identity_file_paths.values())
            self._identity_file_paths.clear()
            self._last_used.clear()
        # Cancel the TTL reaper task
        if self._ttl_task and not self._ttl_task.done():
            self._ttl_task.cancel()
            self._ttl_task = None
        for name in names:
            await self._force_remove(name)
        for paths in identity_paths:
            DockerPTYBackend._cleanup_identity_files(paths)

    # ------------------------------------------------------------------
    # Idle container TTL reaper
    # ------------------------------------------------------------------

    def _touch(self, session_id: str) -> None:
        """Record that a session's container was just used."""
        import time
        self._last_used[session_id] = time.monotonic()
        self._ensure_ttl_reaper()

    def _ensure_ttl_reaper(self) -> None:
        """Start the background TTL reaper if not already running."""
        if self._ttl_task is not None and not self._ttl_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
            self._ttl_task = loop.create_task(self._ttl_reaper_loop())
        except RuntimeError:
            pass  # no event loop — skip (e.g. during tests)

    async def _ttl_reaper_loop(self) -> None:
        """Periodically check for idle containers and remove them."""
        import time
        while True:
            try:
                await asyncio.sleep(self._ttl_check_interval)
                now = time.monotonic()
                to_cleanup: list[str] = []
                async with self._lock:
                    for session_id, last_used in list(self._last_used.items()):
                        if now - last_used < self._ttl_seconds:
                            continue
                        # Don't reap if there's no container tracked
                        if session_id not in self._containers:
                            self._last_used.pop(session_id, None)
                            continue
                        to_cleanup.append(session_id)
                for session_id in to_cleanup:
                    logger.info(
                        "Reaping idle container for session %s (idle %.0fs, TTL %.0fs)",
                        session_id,
                        now - self._last_used.get(session_id, now),
                        self._ttl_seconds,
                    )
                    await self.cleanup(session_id)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("TTL reaper error: %s", exc)

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_env() -> Dict[str, str]:
        """Build container env (Qwen API creds, writable HOME)."""
        return DockerPTYBackend._build_container_env()

    @staticmethod
    async def _check_image(image: str) -> None:
        # Use `docker images -q` instead of `docker image inspect` because
        # inspect can return non-zero for cross-platform images (e.g.
        # linux/amd64 image on an arm64 host) even though the image exists
        # and is runnable via Rosetta / QEMU.
        proc = await asyncio.create_subprocess_exec(
            "docker", "images", "-q", image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if not stdout.decode().strip():
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

    @staticmethod
    async def _discover_containers(
        label: str,
        timeout: float,
    ) -> list[tuple[str, str]]:
        """Discover Docker containers with the given label.

        Returns list of (container_id, container_name) tuples.
        Raises on Docker unavailability or timeout.
        """
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "-a",
            "--filter", f"label={label}",
            "--format", "{{.ID}} {{.Names}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(
                f"docker ps failed (exit {proc.returncode}): {stderr.decode().strip()}"
            )
        containers: list[tuple[str, str]] = []
        for line in stdout.decode().strip().splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                containers.append((parts[0], parts[1]))
        return containers

    @staticmethod
    async def _remove_container(
        container_id: str,
        timeout: float,
    ) -> bool:
        """Force-remove a container by ID. Returns True on success, False on failure."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", container_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode == 0
        except Exception:
            return False

    @staticmethod
    async def cleanup_orphaned_containers(
        *,
        timeout: float = 30.0,
        label: str = "gagent.component=qwen-code-agent",
    ) -> None:
        """Discover and remove orphaned Qwen Code containers on startup.

        All errors are caught and logged; no exceptions propagate.
        """
        try:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout

            # Discovery phase
            remaining = max(0.1, deadline - loop.time())
            try:
                containers = await QwenSessionDriver._discover_containers(label, remaining)
            except FileNotFoundError:
                logger.warning("Skipping orphaned container cleanup: docker CLI not found")
                return
            except asyncio.TimeoutError:
                logger.warning("Skipping orphaned container cleanup: docker ps timed out")
                return
            except Exception as exc:
                logger.warning("Skipping orphaned container cleanup: %s", exc)
                return

            if not containers:
                logger.debug("No orphaned Qwen Code containers found")
                return

            # Removal phase
            removed = 0
            for idx, (container_id, container_name) in enumerate(containers):
                remaining = deadline - loop.time()
                if remaining <= 0:
                    logger.warning(
                        "Orphaned container cleanup timed out: %d/%d removed, %d remaining",
                        removed, len(containers), len(containers) - idx,
                    )
                    break

                success = await QwenSessionDriver._remove_container(container_id, remaining)
                if success:
                    removed += 1
                    logger.info("Removed orphaned container %s (%s)", container_name, container_id)
                else:
                    logger.warning(
                        "Failed to remove orphaned container %s (%s)", container_name, container_id
                    )

            logger.info(
                "Orphaned container cleanup: %d/%d containers removed", removed, len(containers)
            )
        except Exception as exc:
            logger.warning("Unexpected error during orphaned container cleanup: %s", exc)


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
