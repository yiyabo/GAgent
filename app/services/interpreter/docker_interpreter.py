"""
Docker Code Interpreter Module

Execute Python code inside a Docker container.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from urllib.parse import urlparse
from typing import Dict, Optional, Sequence

from .local_interpreter import CodeExecutionResult

logger = logging.getLogger(__name__)

_PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)

try:
    import docker
    from docker.errors import APIError, DockerException, ImageNotFound

    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False
    docker = None
    APIError = Exception
    DockerException = Exception
    ImageNotFound = Exception


class DockerCodeInterpreter:
    """
    Docker-based Python execution helper.

    Example:
        interpreter = DockerCodeInterpreter(
            image="gagent-python-runtime:latest",
            timeout=60,
            work_dir="./results"
        )
        result = interpreter.run_python_code("print('Hello, World!')")
    """

    def __init__(
        self,
        image: str = "gagent-python-runtime:latest",
        timeout: int = 60,
        auto_pull: bool = False,
        work_dir: Optional[str] = None,
        data_dir: Optional[str] = None,
        extra_read_dirs: Optional[Sequence[str]] = None,
        extra_write_dirs: Optional[Sequence[str]] = None,
    ):
        """
        Initialize Docker interpreter.

        Args:
            image: Docker image name.
            timeout: Execution timeout in seconds.
            auto_pull: Pull image automatically when missing.
            work_dir: Host directory mounted read/write at the same absolute path.
            data_dir: Optional host directory mounted read-only at the same path.
            extra_read_dirs: Additional host directories mounted read-only.
            extra_write_dirs: Additional host directories mounted read/write.
        """
        self.image = image
        self.timeout = timeout
        self.auto_pull = auto_pull
        self.work_dir = os.path.abspath(work_dir) if work_dir else os.getcwd()
        self.data_dir = os.path.abspath(data_dir) if data_dir else None
        self.extra_read_dirs = self._normalize_dirs(extra_read_dirs)
        self.extra_write_dirs = self._normalize_dirs(extra_write_dirs)
        self.client = None

        if HAS_DOCKER:
            try:
                self.client = docker.from_env()
            except Exception as e:
                logger.error("Failed to connect to Docker daemon: %s", e)
        else:
            logger.warning("Python 'docker' package is not installed. Please run `pip install docker`.")

    @staticmethod
    def _normalize_dirs(values: Optional[Sequence[str]]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values or ():
            candidate = os.path.abspath(str(value))
            if candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    @staticmethod
    def _is_same_or_child(path: str, parent: str) -> bool:
        try:
            return os.path.commonpath([path, parent]) == parent
        except ValueError:
            return False

    def _build_env(self) -> dict:
        data_dir = self.data_dir or self.work_dir
        cache_root = os.path.join(self.work_dir, ".code_executor_cache")
        env = {
            "DATA_DIR": data_dir,
            "WORKSPACE": self.work_dir,
            "DATA": data_dir,
            "HOME": cache_root,
            "XDG_CACHE_HOME": os.path.join(cache_root, "xdg"),
            "MPLCONFIGDIR": os.path.join(cache_root, "matplotlib"),
            "NUMBA_CACHE_DIR": os.path.join(cache_root, "numba"),
        }
        for name in _PROXY_ENV_NAMES:
            value = os.environ.get(name)
            if value:
                env[name] = value
        return env

    @staticmethod
    def _proxy_targets_loopback(value: str) -> bool:
        parsed = urlparse(value)
        host = parsed.hostname
        if not host and "://" not in value:
            host = urlparse(f"http://{value}").hostname
        return host in {"127.0.0.1", "localhost", "::1"}

    def _should_use_host_network(self, environment: dict) -> bool:
        if not sys.platform.startswith("linux"):
            return False
        return any(
            self._proxy_targets_loopback(environment[name])
            for name in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            )
            if environment.get(name)
        )

    def build_preamble(self) -> str:
        """Return the path-setup preamble for scripts executed in Docker."""
        cache_root = os.path.join(self.work_dir, ".code_executor_cache")
        xdg_cache = os.path.join(cache_root, "xdg")
        mpl_cache = os.path.join(cache_root, "matplotlib")
        numba_cache = os.path.join(cache_root, "numba")
        return f'''# Auto-injected path setup
import os

os.chdir("{self.work_dir}")

os.environ["DATA_DIR"] = "{self.data_dir or self.work_dir}"

_DATA_PATH = "{self.data_dir or self.work_dir}"
_WORKSPACE_PATH = "{self.work_dir}"
_CACHE_ROOT = "{cache_root}"
_XDG_CACHE_PATH = "{xdg_cache}"
_MATPLOTLIB_CACHE_PATH = "{mpl_cache}"
_NUMBA_CACHE_PATH = "{numba_cache}"

os.environ["WORKSPACE"] = _WORKSPACE_PATH
os.environ["DATA"] = _DATA_PATH
os.environ["HOME"] = _CACHE_ROOT
os.environ["XDG_CACHE_HOME"] = _XDG_CACHE_PATH
os.environ["MPLCONFIGDIR"] = _MATPLOTLIB_CACHE_PATH
os.environ["NUMBA_CACHE_DIR"] = _NUMBA_CACHE_PATH

os.makedirs(_CACHE_ROOT, exist_ok=True)
os.makedirs(_XDG_CACHE_PATH, exist_ok=True)
os.makedirs(_MATPLOTLIB_CACHE_PATH, exist_ok=True)
os.makedirs(_NUMBA_CACHE_PATH, exist_ok=True)

'''

    def _build_volume_mounts(self) -> Dict[str, Dict[str, str]]:
        mounts: Dict[str, Dict[str, str]] = {}
        readonly_candidates = self._normalize_dirs([self.data_dir] if self.data_dir else [])
        readonly_candidates.extend(self.extra_read_dirs)
        writable_candidates = self._normalize_dirs([self.work_dir, *self.extra_write_dirs])

        def _should_skip_readonly(candidate: str) -> bool:
            return any(
                self._is_same_or_child(candidate, writable)
                or self._is_same_or_child(writable, candidate)
                for writable in writable_candidates
            )

        for candidate in sorted(self._normalize_dirs(readonly_candidates), key=lambda value: (len(value), value)):
            if _should_skip_readonly(candidate):
                continue
            if any(self._is_same_or_child(candidate, mounted) for mounted in mounts):
                continue
            for mounted in list(mounts):
                if self._is_same_or_child(mounted, candidate):
                    del mounts[mounted]
            mounts[candidate] = {"bind": candidate, "mode": "ro"}

        for candidate in sorted(self._normalize_dirs(writable_candidates), key=lambda value: (len(value), value)):
            if any(self._is_same_or_child(candidate, mounted) for mounted, spec in mounts.items() if spec["mode"] == "rw"):
                continue
            for mounted in list(mounts):
                if self._is_same_or_child(mounted, candidate):
                    del mounts[mounted]
            mounts[candidate] = {"bind": candidate, "mode": "rw"}
        return mounts

    @staticmethod
    def _decode_logs(payload) -> str:
        if payload is None:
            return ""
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="replace")
        return str(payload)

    @staticmethod
    def _resolve_container_user() -> Optional[str]:
        if os.name != "posix":
            return None
        try:
            uid = os.getuid()
            gid = os.getgid()
        except AttributeError:
            return None
        if uid < 0 or gid < 0:
            return None
        return f"{uid}:{gid}"

    def _ensure_runtime_available(self) -> Optional[CodeExecutionResult]:
        if not self.client:
            return CodeExecutionResult(
                status="error",
                output="",
                error="Docker runtime is unavailable: client is not connected.",
                exit_code=-1,
                runtime_failure=True,
            )

        try:
            self.client.images.get(self.image)
            return None
        except ImageNotFound:
            if self.auto_pull:
                try:
                    logger.info("Pulling Docker image %s", self.image)
                    self.client.images.pull(self.image)
                    return None
                except Exception as exc:
                    return CodeExecutionResult(
                        status="error",
                        output="",
                        error=f"Docker runtime failed while pulling image '{self.image}': {exc}",
                        exit_code=-1,
                        runtime_failure=True,
                    )
            return CodeExecutionResult(
                status="error",
                output="",
                error=f"Docker image not found: {self.image}",
                exit_code=-1,
                runtime_failure=True,
            )
        except DockerException as exc:
            return CodeExecutionResult(
                status="error",
                output="",
                error=f"Docker runtime is unavailable: {exc}",
                exit_code=-1,
                runtime_failure=True,
            )
        except Exception as exc:
            return CodeExecutionResult(
                status="error",
                output="",
                error=f"Docker runtime failed while checking image '{self.image}': {exc}",
                exit_code=-1,
                runtime_failure=True,
            )

    def _run_container(self, command: Sequence[str]) -> CodeExecutionResult:
        runtime_check = self._ensure_runtime_available()
        if runtime_check is not None:
            return runtime_check

        container = None
        try:
            volumes = self._build_volume_mounts()
            user = self._resolve_container_user()
            environment = self._build_env()
            logger.info(
                "Docker mounts for %s: %s",
                self.image,
                {host: spec["mode"] for host, spec in volumes.items()},
            )

            run_kwargs = {
                "image": self.image,
                "command": list(command),
                "detach": True,
                "mem_limit": "8g",
                "volumes": volumes,
                "working_dir": self.work_dir,
                "environment": environment,
            }
            if self._should_use_host_network(environment):
                run_kwargs["network_mode"] = "host"
            else:
                run_kwargs["network_disabled"] = False
            if user:
                run_kwargs["user"] = user

            container = self.client.containers.run(**run_kwargs)

            start_time = time.time()
            while True:
                container.reload()
                status = container.status

                if status in {"exited", "dead"}:
                    break

                if time.time() - start_time > self.timeout:
                    container.kill()
                    return CodeExecutionResult(
                        status="timeout",
                        output="",
                        error=f"Execution exceeded {self.timeout} seconds limit.",
                        exit_code=-1,
                    )
                time.sleep(0.5)

            result_state = container.wait()
            exit_code = int(result_state.get("StatusCode", 0))
            stdout = self._decode_logs(container.logs(stdout=True, stderr=False))
            stderr = self._decode_logs(container.logs(stdout=False, stderr=True))

            if exit_code == 0:
                return CodeExecutionResult("success", stdout, stderr, exit_code)
            return CodeExecutionResult("failed", stdout, stderr, exit_code)

        except APIError as exc:
            logger.exception("Docker API error during execution")
            return CodeExecutionResult(
                status="error",
                output="",
                error=f"Docker runtime failed while starting or waiting for the container: {exc}",
                exit_code=-1,
                runtime_failure=True,
            )
        except Exception as exc:
            logger.exception("Unexpected Docker execution error")
            return CodeExecutionResult(
                status="error",
                output="",
                error=f"Docker runtime failed unexpectedly: {exc}",
                exit_code=-1,
                runtime_failure=True,
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    def run_python_code(self, code: str) -> CodeExecutionResult:
        """Execute Python code inside a Docker container."""
        return self._run_container(["python", "-c", self.build_preamble() + code])

    def run_file(self, code_file: str) -> CodeExecutionResult:
        """Execute a Python file inside a Docker container."""
        return self._run_container(["python", os.path.abspath(code_file)])
