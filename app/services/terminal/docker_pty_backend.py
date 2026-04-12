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
import re
import signal
import struct
import tempfile
import termios
from typing import Dict, List, Optional, Sequence, Tuple
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable defaults (overridable via env)
# ---------------------------------------------------------------------------
QWEN_CODE_IMAGE = os.getenv("QWEN_CODE_DOCKER_IMAGE", "gagent-qwen-code-runtime:latest")
CONTAINER_MEMORY_LIMIT = os.getenv("QWEN_CODE_CONTAINER_MEMORY", "4g")
CONTAINER_PIDS_LIMIT = int(os.getenv("QWEN_CODE_CONTAINER_PIDS", "512"))
CONTAINER_WORKDIR = "/workspace"
CONTAINER_HOME = "/tmp/gagent_home"
CONTAINER_USERNAME = "runner"
CONTAINER_EXEC_PATH = "/opt/conda/bin:/opt/conda/condabin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
QWEN_EXECUTABLE = "/opt/conda/bin/qwen"


def _sanitise_qwen_session_id(raw: str) -> str:
    """Return a qwen-compatible UUID session id with stable normalization."""
    text = str(raw).strip()
    if not text:
        return str(uuid5(NAMESPACE_URL, "gagent-terminal-session"))
    try:
        return str(UUID(text))
    except ValueError:
        return str(uuid5(NAMESPACE_URL, text))


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
        self._identity_file_paths: tuple[Optional[str], Optional[str]] = (None, None)
        self._same_path_mount_roots: tuple[str, ...] = ()
        self._qwen_session_id: Optional[str] = None
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
        exec_args: Optional[Sequence[str]] = None,
        extra_mounts: Optional[Sequence[Tuple[str, str]]] = None,
        qwen_session_id: Optional[str] = None,
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
        exec_args:
            Command executed by ``docker exec -it`` after the container is
            created. Defaults to ``/bin/bash``.
        extra_mounts:
            Additional bind mounts applied when the container is created.
            Same-path mounts are tracked so agent-side execution can verify
            whether an absolute host path is available inside the container.
        qwen_session_id:
            Stable qwen ``--session-id`` associated with this container-backed
            terminal session.
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
        exec_argv = list(exec_args) if exec_args else ["/bin/bash"]
        normalized_mounts = self._normalize_extra_mounts(extra_mounts)
        self._qwen_session_id = (
            _sanitise_qwen_session_id(qwen_session_id)
            if qwen_session_id
            else None
        )

        await self._check_image(image)

        # -- 1. Build env and create the container -------------------------
        container_env = self._build_container_env(env)

        env_file_path: Optional[str] = None
        identity_paths: tuple[Optional[str], Optional[str]] = (None, None)
        container_started = False
        try:
            if container_env:
                fd, env_file_path = tempfile.mkstemp(
                    suffix=".env", prefix="gagent_qc_"
                )
                with os.fdopen(fd, "w") as f:
                    for k, v in container_env.items():
                        # Escape newlines to keep one-var-per-line contract
                        f.write(f"{k}={v}\n")

            identity_paths = self._create_identity_mount_files()

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
            passwd_file_path, group_file_path = identity_paths
            if passwd_file_path and group_file_path:
                docker_cmd.extend([
                    "-v", f"{passwd_file_path}:/etc/passwd:ro",
                    "-v", f"{group_file_path}:/etc/group:ro",
                ])

            if cwd:
                real_cwd = os.path.realpath(cwd)
                docker_cmd.extend(["-v", f"{real_cwd}:{CONTAINER_WORKDIR}"])
            for host_path, container_path in normalized_mounts:
                docker_cmd.extend(["-v", f"{host_path}:{container_path}"])

            docker_cmd.extend([
                "-w", CONTAINER_WORKDIR,
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
            container_started = True
            self._identity_file_paths = identity_paths
            self._same_path_mount_roots = tuple(
                host_path
                for host_path, container_path in normalized_mounts
                if os.path.normpath(container_path) == host_path
            )
        finally:
            if env_file_path:
                try:
                    os.unlink(env_file_path)
                except OSError:
                    pass
            if not container_started:
                self._cleanup_identity_files(identity_paths)

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
                    [
                        "docker", "exec", "-it",
                        "-e", f"PATH={CONTAINER_EXEC_PATH}",
                        self._container_name,
                        *exec_argv,
                    ],
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

    @property
    def qwen_session_id(self) -> Optional[str]:
        return self._qwen_session_id

    @property
    def same_path_mount_roots(self) -> tuple[str, ...]:
        return self._same_path_mount_roots

    def covers_path(self, path: str) -> bool:
        """Return True when *path* is visible inside the container at the same path."""
        token = str(path or "").strip()
        if not token:
            return False
        resolved = os.path.realpath(token)
        for root in self._same_path_mount_roots:
            if resolved == root or resolved.startswith(root + os.sep):
                return True
        return False

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
        env["HOME"] = CONTAINER_HOME
        env["USER"] = CONTAINER_USERNAME
        env["LOGNAME"] = CONTAINER_USERNAME

        qwen_model = os.getenv("QWEN_CODE_MODEL", "").strip()
        if qwen_model:
            env["QWEN_CODE_MODEL"] = qwen_model

        if extra:
            env.update(extra)
        return env

    @staticmethod
    def build_qwen_exec_args(
        *,
        workspace_dir: str = CONTAINER_WORKDIR,
        qwen_session_id: Optional[str] = None,
        extra_dirs: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """Build the interactive qwen CLI command for terminal sessions."""
        cmd: List[str] = [
            QWEN_EXECUTABLE,
            "--auth-type", "openai",
            "--add-dir", workspace_dir,
        ]
        seen_dirs = {workspace_dir}
        for raw_dir in extra_dirs or ():
            dir_token = str(raw_dir or "").strip()
            if not dir_token or dir_token in seen_dirs:
                continue
            cmd.extend(["--add-dir", dir_token])
            seen_dirs.add(dir_token)
        qwen_model = os.getenv("QWEN_CODE_MODEL", "").strip()
        if qwen_model:
            cmd.extend(["-m", qwen_model])
        if qwen_session_id:
            cmd.extend(["--session-id", _sanitise_qwen_session_id(qwen_session_id)])
        return cmd

    @staticmethod
    def _normalize_extra_mounts(
        extra_mounts: Optional[Sequence[Tuple[str, str]]],
    ) -> List[Tuple[str, str]]:
        mounts: List[Tuple[str, str]] = []
        seen: set[Tuple[str, str]] = set()
        for host_path, container_path in extra_mounts or ():
            host_token = str(host_path or "").strip()
            container_token = str(container_path or "").strip()
            if not host_token or not container_token:
                continue
            resolved_host = os.path.realpath(host_token)
            normalized_container = os.path.normpath(container_token)
            if not os.path.exists(resolved_host):
                continue
            key = (resolved_host, normalized_container)
            if key in seen:
                continue
            seen.add(key)
            mounts.append(key)
        return mounts

    @staticmethod
    def _build_identity_mount_contents(
        *,
        uid: Optional[int] = None,
        gid: Optional[int] = None,
        username: str = CONTAINER_USERNAME,
        home: str = CONTAINER_HOME,
    ) -> Tuple[str, str]:
        """Return minimal Linux passwd/group entries for the mapped UID/GID."""
        resolved_uid = int(os.getuid() if uid is None else uid)
        resolved_gid = int(os.getgid() if gid is None else gid)
        safe_username = re.sub(r"[^A-Za-z0-9._-]+", "", username).strip() or CONTAINER_USERNAME
        passwd_contents = (
            "root:x:0:0:root:/root:/bin/bash\n"
            f"{safe_username}:x:{resolved_uid}:{resolved_gid}:{safe_username}:{home}:/bin/bash\n"
        )
        group_contents = (
            "root:x:0:\n"
            f"{safe_username}:x:{resolved_gid}:\n"
        )
        return passwd_contents, group_contents

    @classmethod
    def _create_identity_mount_files(cls) -> Tuple[str, str]:
        """Create temporary passwd/group files for the mapped container user."""
        passwd_contents, group_contents = cls._build_identity_mount_contents()
        passwd_fd, passwd_path = tempfile.mkstemp(suffix=".passwd", prefix="gagent_qc_")
        group_fd, group_path = tempfile.mkstemp(suffix=".group", prefix="gagent_qc_")
        try:
            with os.fdopen(passwd_fd, "w") as passwd_file:
                passwd_file.write(passwd_contents)
            with os.fdopen(group_fd, "w") as group_file:
                group_file.write(group_contents)
            return passwd_path, group_path
        except Exception:
            cls._cleanup_identity_files((passwd_path, group_path))
            raise

    @staticmethod
    def _cleanup_identity_files(paths: Tuple[Optional[str], Optional[str]]) -> None:
        for file_path in paths:
            if not file_path:
                continue
            try:
                os.unlink(file_path)
            except OSError:
                pass

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
            self._cleanup_identity_files(self._identity_file_paths)
            self._identity_file_paths = (None, None)
            self._same_path_mount_roots = ()
            self._qwen_session_id = None
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
        finally:
            self._cleanup_identity_files(self._identity_file_paths)
            self._identity_file_paths = (None, None)
            self._same_path_mount_roots = ()
            self._qwen_session_id = None
