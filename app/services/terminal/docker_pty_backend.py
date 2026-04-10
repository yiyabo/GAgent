"""Docker container PTY backend for isolated terminal sessions.

Spawns a long-lived Docker container and attaches an interactive PTY via
``docker exec -it``.  The container provides process-level isolation,
filesystem sandboxing, and resource limits — replacing the host-side
security guard / rcfile used by the local :class:`PTYBackend`.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import pty
import signal
import struct
import tempfile
import termios
from typing import Dict, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable defaults (overridable via env)
# ---------------------------------------------------------------------------
QWEN_CODE_IMAGE = os.getenv("QWEN_CODE_DOCKER_IMAGE", "gagent-qwen-code-runtime:latest")
CONTAINER_MEMORY_LIMIT = os.getenv("QWEN_CODE_CONTAINER_MEMORY", "4g")
CONTAINER_PIDS_LIMIT = int(os.getenv("QWEN_CODE_CONTAINER_PIDS", "512"))


class DockerPTYBackend:
    """Spawn and control a Docker container with an attached PTY.

    Lifecycle::

        backend = DockerPTYBackend()
        await backend.spawn(cwd="/path/to/workspace")
        # ...read/write/resize...
        await backend.terminate()
    """

    def __init__(self) -> None:
        self.master_fd: Optional[int] = None
        self.child_pid: Optional[int] = None
        self._container_name: Optional[str] = None
        self._output_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4096)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._closed = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def spawn(
        self,
        *,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        cols: int = 120,
        rows: int = 36,
        image: Optional[str] = None,
    ) -> None:
        """Create a container and attach a PTY to it.

        Parameters
        ----------
        cwd:
            Host directory to bind-mount as ``/workspace`` inside the
            container (read-write).
        env:
            Extra environment variables injected into the container on top
            of the default Qwen API credentials.
        cols, rows:
            Initial terminal dimensions.
        image:
            Docker image to use.  Defaults to ``QWEN_CODE_DOCKER_IMAGE``
            env var or ``gagent-qwen-code-runtime:latest``.
        """
        if self.child_pid is not None:
            raise RuntimeError("Docker PTY is already running")

        image = image or QWEN_CODE_IMAGE
        self._loop = asyncio.get_running_loop()
        self._container_name = f"gagent-qc-{uuid4().hex[:12]}"

        await self._check_image(image)

        # -- 1. Build env and create the container -------------------------
        container_env = self._build_container_env(env)

        env_file_path: Optional[str] = None
        try:
            if container_env:
                fd, env_file_path = tempfile.mkstemp(
                    suffix=".env", prefix="gagent_qc_"
                )
                with os.fdopen(fd, "w") as f:
                    for k, v in container_env.items():
                        # Escape newlines to keep one-var-per-line contract
                        f.write(f"{k}={v}\n")

            docker_cmd = [
                "docker", "run", "-d",
                "--name", self._container_name,
                "--label", "gagent.component=qwen-code-terminal",
                # Hardening
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                f"--memory={CONTAINER_MEMORY_LIMIT}",
                f"--pids-limit={CONTAINER_PIDS_LIMIT}",
                # Match host UID/GID so workspace writes have correct ownership
                "--user", f"{os.getuid()}:{os.getgid()}",
            ]

            if env_file_path:
                docker_cmd.extend(["--env-file", env_file_path])

            if cwd:
                real_cwd = os.path.realpath(cwd)
                docker_cmd.extend(["-v", f"{real_cwd}:/workspace"])

            docker_cmd.extend([
                "-w", "/workspace",
                image,
                "sleep", "infinity",
            ])

            logger.info(
                "Creating qwen-code container %s (image=%s)",
                self._container_name,
                image,
            )
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Failed to create container: {stderr.decode().strip()}"
                )
            logger.info("Container %s started", self._container_name)
        finally:
            if env_file_path:
                try:
                    os.unlink(env_file_path)
                except OSError:
                    pass

        # -- 2. Attach PTY via docker exec -it -----------------------------
        master_fd, slave_fd = pty.openpty()
        self._set_winsize(slave_fd, cols, rows)

        pid = os.fork()
        if pid == 0:  # pragma: no cover - child
            try:
                os.setsid()
                os.close(master_fd)
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                if slave_fd > 2:
                    os.close(slave_fd)

                child_env = os.environ.copy()
                child_env["TERM"] = "xterm-256color"

                os.execvpe(
                    "docker",
                    ["docker", "exec", "-it", self._container_name, "/bin/bash"],
                    child_env,
                )
            except Exception:
                os._exit(127)

        # Parent
        os.close(slave_fd)
        self.master_fd = master_fd
        self.child_pid = pid
        self._closed = False

        os.set_blocking(self.master_fd, False)
        self._loop.add_reader(self.master_fd, self._on_master_readable)

    async def read(self) -> bytes:
        """Block until output is available, then return it."""
        return await self._output_queue.get()

    async def write(self, data: bytes) -> None:
        """Send raw bytes to the container PTY."""
        if self.master_fd is None:
            raise RuntimeError("Docker PTY is not running")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("write() expects bytes")
        os.write(self.master_fd, bytes(data))

    async def resize(self, cols: int, rows: int) -> None:
        """Resize the terminal inside the container."""
        if self.master_fd is None:
            return
        self._set_winsize(self.master_fd, cols, rows)
        if self.child_pid:
            try:
                os.kill(self.child_pid, signal.SIGWINCH)
            except ProcessLookupError:
                pass

    async def terminate(self) -> None:
        """Kill the exec process, then stop and remove the container."""
        self._closed = True

        # Remove event-loop reader
        if self._loop and self.master_fd is not None:
            try:
                self._loop.remove_reader(self.master_fd)
            except Exception:
                pass

        # Close master fd
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

        # Kill the docker-exec child process
        if self.child_pid is not None:
            pid = self.child_pid
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                self.child_pid = None

            await self._wait_for_exit(timeout_sec=3.0)
            if self.child_pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self._wait_for_exit(timeout_sec=1.0)

        # Stop + remove the container
        await self._cleanup_container()

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def is_available() -> bool:
        """Return *True* if Docker is reachable on this host."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            return proc.returncode == 0
        except Exception:
            return False

    @staticmethod
    async def image_exists(image: Optional[str] = None) -> bool:
        """Return *True* if the runtime image is present locally."""
        image = image or QWEN_CODE_IMAGE
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "image", "inspect", image,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            return proc.returncode == 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def pid(self) -> Optional[int]:
        return self.child_pid

    @property
    def container_name(self) -> Optional[str]:
        return self._container_name

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _build_container_env(
        extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Assemble the container env (Qwen API creds + extras)."""
        env: Dict[str, str] = {}

        qwen_key = os.getenv("QWEN_API_KEY", "").strip()
        if qwen_key:
            env["OPENAI_API_KEY"] = qwen_key

        base_url = (
            os.getenv("QWEN_CODE_BASE_URL", "").strip()
            or os.getenv("OPENAI_BASE_URL", "").strip()
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        env["OPENAI_BASE_URL"] = base_url

        env["TERM"] = "xterm-256color"
        # Writable HOME for the arbitrary UID we pass via --user
        env["HOME"] = "/tmp/gagent_home"

        qwen_model = os.getenv("QWEN_CODE_MODEL", "").strip()
        if qwen_model:
            env["QWEN_CODE_MODEL"] = qwen_model

        if extra:
            env.update(extra)
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
                f"Build it with: ./scripts/build_qwen_code_runtime_image.sh"
            )

    @staticmethod
    def _set_winsize(fd: int, cols: int, rows: int) -> None:
        winsize = struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

    def _on_master_readable(self) -> None:
        if self.master_fd is None:
            return
        try:
            chunk = os.read(self.master_fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""

        if chunk:
            if self._output_queue.full():
                try:
                    self._output_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            self._output_queue.put_nowait(chunk)
            return

        # EOF — docker exec exited
        self._closed = True
        if self._loop and self.master_fd is not None:
            try:
                self._loop.remove_reader(self.master_fd)
            except Exception:
                pass

    async def _wait_for_exit(self, *, timeout_sec: float) -> None:
        if self.child_pid is None:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout_sec)
        while loop.time() < deadline and self.child_pid is not None:
            try:
                pid, _status = os.waitpid(self.child_pid, os.WNOHANG)
            except ChildProcessError:
                self.child_pid = None
                break
            if pid == self.child_pid:
                self.child_pid = None
                break
            await asyncio.sleep(0.05)

    async def _cleanup_container(self) -> None:
        """Force-remove the container (idempotent)."""
        name = self._container_name
        if not name:
            return
        self._container_name = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            logger.info("Container %s removed", name)
        except Exception as exc:
            logger.warning("Failed to remove container %s: %s", name, exc)
