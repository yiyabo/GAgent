"""
Docker Code Interpreter Module

在 Docker 容器中安全执行 Python 代码。
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# 尝试导入 docker，如果不可用则设置标志
try:
    import docker
    from docker.errors import ContainerError, ImageNotFound, APIError
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False
    docker = None


@dataclass
class CodeExecutionResult:
    """代码执行结果封装类"""
    status: str  # 'success', 'failed', 'error', 'timeout'
    output: str  # 标准输出 (stdout)
    error: str   # 标准错误 (stderr) 或 系统错误信息
    exit_code: int


class DockerCodeInterpreter:
    """
    Docker 代码解释器

    在隔离的 Docker 容器中执行 Python 代码，提供安全的代码执行环境。

    使用示例:
        interpreter = DockerCodeInterpreter(
            image="agent-plotter",
            timeout=60,
            work_dir="./results"
        )
        result = interpreter.run_python_code("print('Hello, World!')")
    """

    def __init__(
        self,
        image: str = "agent-plotter",  # 使用包含 pandas/numpy/matplotlib 的自定义镜像
        timeout: int = 60,
        auto_pull: bool = True,
        work_dir: Optional[str] = None,
        data_dir: Optional[str] = None
    ):
        """
        初始化 Docker 代码解释器

        Args:
            image: Docker 镜像名称
            timeout: 执行超时时间（秒）
            auto_pull: 如果镜像不存在，是否自动尝试拉取
            work_dir: 宿主机工作目录，将挂载到容器的 /workspace（用于输出文件）
            data_dir: 宿主机数据目录，将挂载到容器的 /data（用于读取数据文件）
                      如果不指定，则使用 work_dir
        """
        self.image = image
        self.timeout = timeout
        self.auto_pull = auto_pull
        self.work_dir = os.path.abspath(work_dir) if work_dir else os.getcwd()
        # 如果指定了 data_dir，单独挂载；否则数据文件也在 work_dir 中
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
        在 Docker 容器中运行 Python 代码

        Args:
            code: Python 代码字符串

        Returns:
            CodeExecutionResult: 执行结果
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
            # 1. 检查镜像是否存在，不存在则拉取
            if self.auto_pull:
                try:
                    self.client.images.get(self.image)
                except docker.errors.ImageNotFound:
                    logger.info(f"Pulling image {self.image}...")
                    self.client.images.pull(self.image)
                except Exception as e:
                    return CodeExecutionResult("error", "", f"Failed to check/pull image: {e}", -1)

            # 2. 启动容器
            # 挂载目录：
            #   - work_dir -> /workspace (读写，用于输出文件)
            #   - data_dir -> /data (只读，用于读取数据文件，如果指定了的话)
            volumes = {
                self.work_dir: {'bind': '/workspace', 'mode': 'rw'}
            }

            # 如果指定了单独的数据目录，挂载到 /data
            if self.data_dir and self.data_dir != self.work_dir:
                volumes[self.data_dir] = {'bind': '/data', 'mode': 'ro'}
                logger.info(f"Docker 挂载: /workspace={self.work_dir}, /data={self.data_dir}")
            else:
                logger.info(f"Docker 挂载: /workspace={self.work_dir}")

            container = self.client.containers.run(
                image=self.image,
                command=["python", "-c", code],
                detach=True,
                network_disabled=True,
                mem_limit="512m",
                volumes=volumes,
                working_dir="/workspace",
            )

            # 3. 监控执行状态（实现超时机制）
            start_time = time.time()
            while True:
                container.reload()  # 刷新容器状态
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
                time.sleep(0.5)  # 轮询间隔

            # 4. 获取执行结果
            result_state = container.wait()
            exit_code = result_state.get('StatusCode', 0)

            # 获取日志
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
