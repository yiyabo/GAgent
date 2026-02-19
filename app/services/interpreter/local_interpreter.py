"""
Local Code Interpreter Module

Execute Python code locally without Docker.

This module is a lightweight fallback for environments where Docker-based
execution is unavailable or unnecessary.
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
    """Result for one local code execution attempt."""
    status: str  # 'success', 'failed', 'error', 'timeout'
    output: str  # Standard output.
    error: str  # Standard error or system error message.
    exit_code: int


class LocalCodeInterpreter:
    """
    Local Python execution helper.

    Runs Python snippets in a controlled working directory.

    Example:
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
        image: str = None,  #  Docker , parameter
    ):
        """
        Initialize local interpreter.

        Args:
            timeout: Execution timeout in seconds.
            work_dir: Working directory for code execution and output files.
            data_dir: Optional data directory exposed as `DATA_DIR`.
            image: Unused compatibility parameter (kept for interface parity).
        """
        self.timeout = timeout
        self.work_dir = os.path.abspath(work_dir) if work_dir else os.getcwd()
        self.data_dir = os.path.abspath(data_dir) if data_dir else None

        os.makedirs(self.work_dir, exist_ok=True)

        logger.info(f"LocalCodeInterpreter initialized: work_dir={self.work_dir}, data_dir={self.data_dir}")

    def run_python_code(self, code: str) -> CodeExecutionResult:
        """
        Execute Python code locally.

        Args:
            code: Python source code.

        Returns:
            CodeExecutionResult for this run.
        """
        script_path = None

        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.py',
                delete=False,
                encoding='utf-8'
            ) as f:
                preamble = f'''# Auto-injected path setup
import os
import sys

os.chdir("{self.work_dir}")

os.environ["DATA_DIR"] = "{self.data_dir or self.work_dir}"

_DATA_PATH = "{self.data_dir or self.work_dir}"
_WORKSPACE_PATH = "{self.work_dir}"

os.environ["WORKSPACE"] = _WORKSPACE_PATH
os.environ["DATA"] = _DATA_PATH

'''
                f.write(preamble)
                f.write(code)
                script_path = f.name

            env = os.environ.copy()
            env["DATA_DIR"] = self.data_dir or self.work_dir
            env["WORKSPACE"] = self.work_dir
            env["DATA"] = self.data_dir or self.work_dir

            logger.info(f"Executing local Python code (timeout={self.timeout}s, cwd={self.work_dir})")

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
            logger.warning(f"Local code execution timed out after {self.timeout}s")
            return CodeExecutionResult(
                status="timeout",
                output="",
                error=f"Execution timed out after {self.timeout} seconds.",
                exit_code=-1
            )
        except Exception as e:
            logger.exception(f"Local code execution error: {e}")
            return CodeExecutionResult(
                status="error",
                output="",
                error=str(e),
                exit_code=-1
            )
        finally:
            if script_path and os.path.exists(script_path):
                try:
                    os.unlink(script_path)
                except Exception:
                    pass
