"""
Docker Code Interpreter Module

Execute Python code inside a Docker container.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, Optional, Sequence

from .local_interpreter import CodeExecutionResult

logger = logging.getLogger(__name__)

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
        return {
            "DATA_DIR": data_dir,
            "WORKSPACE": self.work_dir,
            "DATA": data_dir,
        }

    def build_preamble(self) -> str:
        """Return the path-setup preamble for scripts executed in Docker."""
        return f'''# Auto-injected path setup
import os

os.chdir("{self.work_dir}")

os.environ["DATA_DIR"] = "{self.data_dir or self.work_dir}"

_DATA_PATH = "{self.data_dir or self.work_dir}"
_WORKSPACE_PATH = "{self.work_dir}"

os.environ["WORKSPACE"] = _WORKSPACE_PATH
os.environ["DATA"] = _DATA_PATH

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
        try:
            return f"{os.getuid()}:{os.getgid()}"
        except AttributeError:
            return None

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
            logger.info(
                "Docker mounts for %s: %s",
                self.image,
                {host: spec["mode"] for host, spec in volumes.items()},
            )

            run_kwargs = {
                "image": self.image,
                "command": list(command),
                "detach": True,
                "network_disabled": False,
                "mem_limit": "512m",
                "volumes": volumes,
                "working_dir": self.work_dir,
                "environment": self._build_env(),
            }
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
