"""
Local Code Interpreter Module

在本地 Python 环境中执行代码（无需 Docker）。
用于开发阶段，替代 DockerCodeInterpreter。
"""

import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CodeExecutionResult:
    """代码执行结果封装类"""
    status: str  # 'success', 'failed', 'error', 'timeout'
    output: str  # 标准输出 (stdout)
    error: str   # 标准错误 (stderr) 或 系统错误信息
    exit_code: int


class LocalCodeInterpreter:
    """
    本地代码解释器

    在本地 Python 环境中执行代码，无需 Docker。
    与 DockerCodeInterpreter 保持相同的接口，便于替换。

    使用示例:
        interpreter = LocalCodeInterpreter(
            timeout=60,
            work_dir="./results"
        )
        result = interpreter.run_python_code("print('Hello, World!')")
    """

    def __init__(
        self,
        timeout: int = 60,
        work_dir: Optional[str] = None,
        data_dir: Optional[str] = None,
        image: str = None,  # 兼容 Docker 接口，忽略此参数
    ):
        """
        初始化本地代码解释器

        Args:
            timeout: 执行超时时间（秒）
            work_dir: 工作目录（代码执行的 cwd，输出文件保存位置）
            data_dir: 数据目录（会设置环境变量 DATA_DIR 供代码访问）
            image: 忽略，仅用于兼容 DockerCodeInterpreter 接口
        """
        self.timeout = timeout
        self.work_dir = os.path.abspath(work_dir) if work_dir else os.getcwd()
        self.data_dir = os.path.abspath(data_dir) if data_dir else None

        # 确保工作目录存在
        os.makedirs(self.work_dir, exist_ok=True)

        logger.info(f"LocalCodeInterpreter 初始化: work_dir={self.work_dir}, data_dir={self.data_dir}")

    def run_python_code(self, code: str) -> CodeExecutionResult:
        """
        在本地 Python 环境中执行代码

        Args:
            code: Python 代码字符串

        Returns:
            CodeExecutionResult: 执行结果
        """
        script_path = None

        try:
            # 1. 将代码写入临时文件
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.py',
                delete=False,
                encoding='utf-8'
            ) as f:
                # 注入路径设置，模拟 Docker 的目录挂载
                preamble = f'''# Auto-injected path setup
import os
import sys

# 设置工作目录
os.chdir("{self.work_dir}")

# 设置数据目录环境变量（模拟 Docker 的 /data 挂载）
os.environ["DATA_DIR"] = "{self.data_dir or self.work_dir}"

# 为了兼容原 Docker 代码中使用 /data 和 /workspace 的情况
# 创建符号链接或路径映射
_DATA_PATH = "{self.data_dir or self.work_dir}"
_WORKSPACE_PATH = "{self.work_dir}"

# 如果代码中使用了 /data 或 /workspace，替换为实际路径
# 这里通过环境变量让用户代码可以访问
os.environ["WORKSPACE"] = _WORKSPACE_PATH
os.environ["DATA"] = _DATA_PATH

'''
                f.write(preamble)
                f.write(code)
                script_path = f.name

            # 2. 准备环境变量
            env = os.environ.copy()
            env["DATA_DIR"] = self.data_dir or self.work_dir
            env["WORKSPACE"] = self.work_dir
            env["DATA"] = self.data_dir or self.work_dir

            # 3. 执行代码
            logger.info(f"执行代码 (timeout={self.timeout}s, cwd={self.work_dir})")

            result = subprocess.run(
                ["python", script_path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.work_dir,
                env=env,
            )

            stdout = result.stdout or ""
            stderr = result.stderr or ""

            # 4. 判断结果
            if result.returncode == 0:
                return CodeExecutionResult(
                    status="success",
                    output=stdout,
                    error=stderr,
                    exit_code=0
                )
            else:
                return CodeExecutionResult(
                    status="failed",
                    output=stdout,
                    error=stderr,
                    exit_code=result.returncode
                )

        except subprocess.TimeoutExpired:
            logger.warning(f"代码执行超时 ({self.timeout}s)")
            return CodeExecutionResult(
                status="timeout",
                output="",
                error=f"执行超时，超过 {self.timeout} 秒限制",
                exit_code=-1
            )
        except Exception as e:
            logger.exception(f"代码执行出错: {e}")
            return CodeExecutionResult(
                status="error",
                output="",
                error=str(e),
                exit_code=-1
            )
        finally:
            # 5. 清理临时文件
            if script_path and os.path.exists(script_path):
                try:
                    os.unlink(script_path)
                except Exception:
                    pass
