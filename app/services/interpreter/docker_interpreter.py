"""
Docker Code Interpreter Module

Execute Python code inside a Docker container.
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import docker
    from docker.errors import ContainerError, ImageNotFound, APIError
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False
    docker = None


@dataclass
class CodeExecutionResult:
    """Result for one Docker code execution attempt."""
    status: str  # 'success', 'failed', 'error', 'timeout'
    output: str  # Standard output.
    error: str  # Standard error or system error message.
    exit_code: int


class DockerCodeInterpreter:
    """
    Docker-based Python execution helper.

    Example:
        interpreter = DockerCodeInterpreter(
            image="agent-plotter",
            timeout=60,
            work_dir="./results"
        )
        result = interpreter.run_python_code("print('Hello, World!')")
    """

    def __init__(
        self,
        image: str = "agent-plotter",  #  pandas/numpy/matplotlib 
        timeout: int = 60,
        auto_pull: bool = True,
        work_dir: Optional[str] = None,
        data_dir: Optional[str] = None
    ):
        """
        Initialize Docker interpreter.

        Args:
            image: Docker name
            timeout: Execution timeout in seconds.
            auto_pull: Pull image automatically when missing.
            work_dir: Host directory mounted as `/workspace` (read/write).
            data_dir: Optional host directory mounted as `/data` (read-only).
                If omitted, `work_dir` is used for both data and outputs.
        """
        self.image = image
        self.timeout = timeout
        self.auto_pull = auto_pull
        self.work_dir = os.path.abspath(work_dir) if work_dir else os.getcwd()
        self.data_dir = os.path.abspath(data_dir) if data_dir else None
        self.client = None

        if HAS_DOCKER:
            try:
                self.client = docker.from_env()
            except Exception as e:
                logger.error(f"Failed to connect to Docker Daemon: {e}")
        else:
            logger.warning("Python 'docker' package is not installed. Please run `pip install docker`.")

    def run_python_code(self, code: str) -> CodeExecutionResult:
        """
        Execute Python code inside a Docker container.

        Args:
            code: Python source code.

        Returns:
            CodeExecutionResult for this run.
        """
        if not self.client:
            return CodeExecutionResult(
                status="error",
                output="",
                error="Docker client is not available or 'docker' library is missing.",
                exit_code=-1
            )

        container = None
        try:
            if self.auto_pull:
                try:
                    self.client.images.get(self.image)
                except docker.errors.ImageNotFound:
                    logger.info(f"Pulling image {self.image}...")
                    self.client.images.pull(self.image)
                except Exception as e:
                    return CodeExecutionResult("error", "", f"Failed to check/pull image: {e}", -1)

            volumes = {
                self.work_dir: {'bind': '/workspace', 'mode': 'rw'}
            }

            if self.data_dir and self.data_dir != self.work_dir:
                volumes[self.data_dir] = {'bind': '/data', 'mode': 'ro'}
                logger.info(f"Docker mounts: /workspace={self.work_dir}, /data={self.data_dir}")
            else:
                logger.info(f"Docker mounts: /workspace={self.work_dir}")

            container = self.client.containers.run(
                image=self.image,
                command=["python", "-c", code],
                detach=True,
                network_disabled=True,
                mem_limit="512m",
                volumes=volumes,
                working_dir="/workspace",
            )

            start_time = time.time()
            while True:
                container.reload()  # refresh container status
                status = container.status

                if status in ['exited', 'dead']:
                    break

                if time.time() - start_time > self.timeout:
                    container.kill()
                    return CodeExecutionResult(
                        status="timeout",
                        output="",
                        error=f"Execution exceeded {self.timeout} seconds limit.",
                        exit_code=-1
                    )
                time.sleep(0.5)

            result_state = container.wait()
            exit_code = result_state.get('StatusCode', 0)

            stdout = container.logs(stdout=True, stderr=False).decode('utf-8', errors='replace')
            stderr = container.logs(stdout=False, stderr=True).decode('utf-8', errors='replace')

            if exit_code == 0:
                return CodeExecutionResult("success", stdout, stderr, exit_code)
            else:
                return CodeExecutionResult("failed", stdout, stderr, exit_code)

        except Exception as e:
            logger.exception("Error during Docker execution")
            return CodeExecutionResult("error", "", str(e), -1)

        finally:
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
