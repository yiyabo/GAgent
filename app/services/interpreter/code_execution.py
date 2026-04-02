"""
Unified local code execution: LLM generates Python → write to file → execute → fix on failure.

Both `task_executer.py` and `code_executor.py` delegate to `execute_code_locally()`
so there is a single implementation of the generate → run → fix loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from .coder import CodeGenerator, CodeTaskResponse
from .docker_interpreter import DockerCodeInterpreter
from .local_interpreter import CodeExecutionResult, LocalCodeInterpreter
from .metadata import DatasetMetadata
from .prompts.coder_prompt import (
    _DOCKER_EXTRA_AVAILABLE_LIBRARIES,
    _DOCKER_EXTRA_SYSTEM_TOOLS,
    build_coder_system_prompt,
)

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".svg", ".pdf"})
_DEFAULT_DOCKER_IMAGE = "gagent-python-runtime:latest"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class CodeExecutionOutcome:
    """Result of a unified local code execution."""

    success: bool
    code: str = ""
    description: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    attempts: int = 0
    code_file: str = ""
    has_visualization: bool = False
    visualization_files: List[str] = field(default_factory=list)
    visualization_purpose: Optional[str] = None
    visualization_analysis: Optional[str] = None
    error_category: Optional[str] = None
    error_summary: Optional[str] = None
    fix_guidance: Optional[str] = None
    stdout_file: Optional[str] = None
    stderr_file: Optional[str] = None
    execution_backend: str = "local"
    runtime_failure: bool = False


# ---------------------------------------------------------------------------
# Output truncation
# ---------------------------------------------------------------------------

_OUTPUT_TRUNCATION_THRESHOLD = 4000  # ~1000 tokens


def _truncate_large_output(
    text: str,
    *,
    work_dir: str,
    filename: str,
    head_lines: int = 30,
    tail_lines: int = 30,
) -> tuple[str, Optional[str]]:
    """If *text* exceeds threshold, write full content to file and return a preview.

    Returns (possibly_truncated_text, file_path_or_None).
    """
    if len(text) <= _OUTPUT_TRUNCATION_THRESHOLD:
        return text, None

    file_path = os.path.join(work_dir, filename)
    try:
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        logger.warning("Failed to write large output to %s: %s", file_path, exc)
        return text, None

    lines = text.splitlines()
    total = len(lines)
    if total <= head_lines + tail_lines:
        return text, file_path

    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[-tail_lines:])
    omitted = total - head_lines - tail_lines
    preview = f"{head}\n\n[... {omitted} lines omitted, full output: {file_path} ...]\n\n{tail}"
    return preview, file_path


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

_MISSING_PKG_RE = re.compile(
    r"(?:ModuleNotFoundError|ImportError).*No module named ['\"](\S+?)['\"]",
)
_WARNING_HEADER_RE = re.compile(
    r"(?:^|:\s*)(?:UserWarning|DeprecationWarning|FutureWarning|RuntimeWarning|"
    r"PendingDeprecationWarning|ImportWarning|SyntaxWarning|ResourceWarning)\s*:",
)
_EXCEPTION_LINE_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception):"
)
_STDOUT_ERROR_HINT_RE = re.compile(
    r"\b(?:error|failed|missing required input|not found|cannot proceed|blocked_dependency)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ErrorAnalysis:
    category: str
    actionable_error_text: str
    warning_text: str
    summary_text: str
    warning_only: bool = False


def _is_warning_header_line(line: str) -> bool:
    return bool(_WARNING_HEADER_RE.search(str(line or "").strip()))


def _split_leading_warning_text(stderr: str) -> tuple[str, str]:
    raw = str(stderr or "")
    if not raw.strip():
        return "", ""

    lines = raw.splitlines()
    idx = 0
    warning_lines: List[str] = []

    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        if not _is_warning_header_line(stripped):
            break

        warning_lines.append(line)
        idx += 1
        while idx < len(lines):
            candidate = lines[idx]
            candidate_stripped = candidate.strip()
            if not candidate_stripped:
                warning_lines.append(candidate)
                idx += 1
                continue
            if _is_warning_header_line(candidate_stripped):
                break
            if candidate_stripped.startswith("Traceback (most recent call last):"):
                break
            if candidate_stripped.startswith("During handling of the above exception"):
                break
            if _EXCEPTION_LINE_RE.match(candidate_stripped):
                break
            if candidate.startswith((" ", "\t")) or candidate_stripped.startswith("^"):
                warning_lines.append(candidate)
                idx += 1
                continue
            break

        while idx < len(lines) and not lines[idx].strip():
            warning_lines.append(lines[idx])
            idx += 1

    warning_text = "\n".join(warning_lines).strip()
    actionable_error_text = "\n".join(lines[idx:]).strip()
    return warning_text, actionable_error_text


def classify_error(stderr: str, exit_code: int) -> str:
    """Classify subprocess stderr into an actionable error category."""
    if exit_code == -1:
        return "timeout"
    warning_text, actionable_error_text = _split_leading_warning_text(stderr)
    if not actionable_error_text and warning_text:
        return "non_fatal_warning_noise"
    error_text = actionable_error_text or str(stderr or "")
    lowered = error_text.lower()
    if "SyntaxError" in error_text or "IndentationError" in error_text:
        return "syntax_error"
    if _MISSING_PKG_RE.search(error_text):
        return "missing_package"
    if (
        "FileNotFoundError" in error_text
        or "PermissionError" in error_text
        or "not found" in lowered
        or "missing required input file" in lowered
        or "missing required input files" in lowered
    ):
        return "file_access"
    if "MemoryError" in error_text:
        return "resource_limit"
    return "runtime_error"


def _extract_missing_package(stderr: str) -> Optional[str]:
    """Extract the missing package name from an import error."""
    m = _MISSING_PKG_RE.search(stderr)
    if m:
        pkg = m.group(1).split(".")[0]
        return pkg
    return None


def _analyze_execution_error(stderr: str, exit_code: int) -> ErrorAnalysis:
    warning_text, actionable_error_text = _split_leading_warning_text(stderr)
    category = classify_error(stderr, exit_code)
    summary_text = actionable_error_text or warning_text or str(stderr or "").strip()
    return ErrorAnalysis(
        category=category,
        actionable_error_text=actionable_error_text or summary_text,
        warning_text=warning_text,
        summary_text=summary_text,
        warning_only=category == "non_fatal_warning_noise",
    )


def _extract_actionable_stdout_failure(stdout: str) -> str:
    raw = str(stdout or "").strip()
    if not raw:
        return ""
    lines = [line.rstrip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return ""

    for idx, line in enumerate(lines):
        if line.strip().startswith("Traceback (most recent call last):"):
            return "\n".join(lines[idx:]).strip()

    marker_indices = [
        idx for idx, line in enumerate(lines)
        if _STDOUT_ERROR_HINT_RE.search(line.strip())
    ]
    if not marker_indices:
        return ""

    start = marker_indices[-1]
    while start > 0 and len(lines[start - 1]) < 160:
        previous = lines[start - 1].strip()
        if _STDOUT_ERROR_HINT_RE.search(previous):
            start -= 1
            continue
        break
    return "\n".join(lines[start:]).strip()


def _analyze_execution_failure(stderr: str, stdout: str, exit_code: int) -> ErrorAnalysis:
    stderr_analysis = _analyze_execution_error(stderr, exit_code)
    stdout_error = _extract_actionable_stdout_failure(stdout)
    if stdout_error and (stderr_analysis.warning_only or not stderr_analysis.actionable_error_text.strip()):
        stdout_category = classify_error(stdout_error, exit_code)
        if stdout_category == "non_fatal_warning_noise":
            stdout_category = "runtime_error"
        return ErrorAnalysis(
            category=stdout_category,
            actionable_error_text=stdout_error,
            warning_text=stderr_analysis.warning_text,
            summary_text=stdout_error,
            warning_only=False,
        )
    return stderr_analysis


def _normalize_execution_backend(value: Optional[str]) -> str:
    raw = str(value or "local").strip().lower()
    if raw in {"local", "docker"}:
        return raw
    return "local"


def _runtime_error_category(execution_backend: str) -> str:
    return f"{execution_backend}_runtime_error"


# ---------------------------------------------------------------------------
# Fix-strategy hints (injected into the LLM prompt)
# ---------------------------------------------------------------------------

_FIX_HINTS = {
    "syntax_error": (
        "The error is a syntax or indentation error. "
        "Fix ONLY the syntax issue. Do NOT change the logic."
    ),
    "missing_package": (
        "A required package is not installed. "
        "Add `import subprocess; subprocess.check_call(['pip', 'install', '{pkg}'])` "
        "at the very top of the code, before the failing import."
    ),
    "file_access": (
        "A file was not found or a permission error occurred. "
        "Double-check all file paths. Use the data directory and output directory "
        "provided in the task description. List the directory contents with "
        "`os.listdir()` if unsure."
    ),
    "timeout": (
        "The code timed out. Optimise for performance: "
        "reduce data volume (sample or head), avoid nested loops on large data, "
        "use vectorised pandas/numpy operations."
    ),
    "resource_limit": (
        "The code ran out of memory. Reduce memory usage: "
        "process data in chunks, avoid loading everything at once, "
        "use generators or iterators."
    ),
    "runtime_error": "",  # generic, no extra hint
    "non_fatal_warning_noise": "",
}


# ---------------------------------------------------------------------------
# Core execution function
# ---------------------------------------------------------------------------

async def execute_code_locally(
    *,
    task_title: str,
    task_description: str,
    metadata_list: Sequence[DatasetMetadata] = (),
    llm_service=None,
    work_dir: str,
    data_dir: Optional[str] = None,
    code_filename: str = "task_code.py",
    max_attempts: int = 3,
    timeout: int = 120,
    auto_fix: bool = True,
    execution_backend: str = "local",
    docker_image: Optional[str] = None,
    readable_dirs: Sequence[str] = (),
    writable_dirs: Sequence[str] = (),
) -> CodeExecutionOutcome:
    """Unified local code execution with optional LLM-based error recovery.

    1. LLM generates Python code from *task_description*.
    2. Code is written to ``{work_dir}/{code_filename}`` (persistent).
    3. Subprocess executes the file.
    4. On failure the error is classified:
       - ``auto_fix=True`` (default): a targeted hint is injected and the
         LLM generates a fix. Steps 2-4 repeat up to *max_attempts*.
       - ``auto_fix=False``: the error, generated code, and fix guidance
         are returned immediately so the calling agent can decide the
         next step.
    """
    effective_data_dir = data_dir or work_dir

    # Ensure output directories exist.
    results_dir = Path(work_dir) / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    normalized_backend = _normalize_execution_backend(execution_backend)
    generator = CodeGenerator(
        llm_service=llm_service,
        system_prompt=build_coder_system_prompt(
            extra_libraries=_DOCKER_EXTRA_AVAILABLE_LIBRARIES
            if normalized_backend == "docker"
            else (),
            extra_system_tools=_DOCKER_EXTRA_SYSTEM_TOOLS
            if normalized_backend == "docker"
            else (),
        ),
    )

    # ---- Code generation ----
    try:
        code_response: CodeTaskResponse = await asyncio.to_thread(
            generator.generate,
            metadata_list=list(metadata_list),
            task_title=task_title,
            task_description=task_description,
        )
    except Exception as e:
        logger.error("Code generation failed: %s", e)
        return CodeExecutionOutcome(
            success=False,
            stderr=str(e),
            error_category="generation_failed",
        )

    code = code_response.code
    if not code or not code.strip():
        return CodeExecutionOutcome(
            success=False,
            stderr="LLM returned empty code",
            error_category="generation_failed",
        )

    # ---- Interpreter + preamble ----
    if normalized_backend == "docker":
        interpreter = DockerCodeInterpreter(
            image=str(docker_image or _DEFAULT_DOCKER_IMAGE).strip() or _DEFAULT_DOCKER_IMAGE,
            timeout=timeout,
            auto_pull=False,
            work_dir=work_dir,
            data_dir=effective_data_dir,
            extra_read_dirs=readable_dirs,
            extra_write_dirs=writable_dirs,
        )
    else:
        interpreter = LocalCodeInterpreter(
            timeout=timeout,
            work_dir=work_dir,
            data_dir=effective_data_dir,
        )
    preamble = interpreter.build_preamble()
    code_file = str(Path(work_dir) / code_filename)

    # ---- Execute with retry ----
    last_code = code
    _write_code_file(code_file, preamble, last_code)

    attempt = 1
    exec_result: CodeExecutionResult = await asyncio.to_thread(
        interpreter.run_file, code_file,
    )
    logger.info(
        "%s execution attempt %d/%d: status=%s exit_code=%d runtime_failure=%s",
        normalized_backend,
        attempt,
        max_attempts,
        exec_result.status,
        exec_result.exit_code,
        exec_result.runtime_failure,
    )

    error_category: Optional[str] = None
    fix_guidance: Optional[str] = None
    error_analysis: Optional[ErrorAnalysis] = None

    if not auto_fix and exec_result.status != "success":
        if exec_result.runtime_failure:
            error_category = _runtime_error_category(normalized_backend)
        else:
            error_analysis = _analyze_execution_failure(
                exec_result.error,
                exec_result.output,
                exec_result.exit_code,
            )
            error_category = error_analysis.category
            fix_guidance = _FIX_HINTS.get(error_category, "")
            if error_category == "missing_package":
                match = _MISSING_PKG_RE.search(error_analysis.actionable_error_text)
                if match:
                    pkg = match.group(1).split(".")[0]
                    fix_guidance = _FIX_HINTS["missing_package"].format(pkg=pkg)

    while (
        auto_fix
        and exec_result.status != "success"
        and not exec_result.runtime_failure
        and attempt < max_attempts
    ):
        error_analysis = _analyze_execution_failure(
            exec_result.error,
            exec_result.output,
            exec_result.exit_code,
        )
        error_category = error_analysis.category
        if error_analysis.warning_only:
            logger.warning(
                "Skipping auto-fix because stderr only contains warning noise: %.200s",
                error_analysis.summary_text,
            )
            break
        attempt += 1
        logger.info(
            "Code execution failed (attempt %d), category=%s, fixing. Error: %.200s",
            attempt - 1, error_category, error_analysis.summary_text,
        )

        try:
            fixed_code = await _ask_llm_to_fix(
                generator=generator,
                metadata_list=list(metadata_list),
                task_title=task_title,
                task_description=task_description,
                code=preamble + last_code,
                error=error_analysis.actionable_error_text,
                error_category=error_category,
            )
        except Exception as e:
            logger.warning("Code fix attempt %d failed: %s", attempt, e)
            break

        if not fixed_code:
            logger.warning("LLM returned empty code during fix attempt")
            break

        last_code = fixed_code
        _write_code_file(code_file, preamble, last_code)

        exec_result = await asyncio.to_thread(interpreter.run_file, code_file)
        logger.info(
            "%s execution attempt %d/%d: status=%s exit_code=%d runtime_failure=%s code_file=%s",
            normalized_backend,
            attempt,
            max_attempts,
            exec_result.status,
            exec_result.exit_code,
            exec_result.runtime_failure,
            code_file,
        )

    if exec_result.runtime_failure and exec_result.status != "success":
        logger.warning(
            "Skipping auto-fix retries because %s runtime failed: %s",
            normalized_backend,
            exec_result.error,
        )

    # ---- Detect visualisation artifacts ----
    viz_files: List[str] = []
    if results_dir.exists():
        viz_files = [
            str(f) for f in results_dir.iterdir()
            if f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS
        ]

    success = exec_result.status == "success"
    if not success and error_category is None:
        if exec_result.runtime_failure:
            error_category = _runtime_error_category(normalized_backend)
        else:
            error_analysis = error_analysis or _analyze_execution_failure(
                exec_result.error,
                exec_result.output,
                exec_result.exit_code,
            )
            error_category = error_analysis.category
            if fix_guidance is None:
                fix_guidance = _FIX_HINTS.get(error_category, "")
            if error_category == "missing_package":
                pkg = _extract_missing_package(error_analysis.actionable_error_text)
                if pkg:
                    fix_guidance = _FIX_HINTS["missing_package"].format(pkg=pkg)

    # Truncate large outputs and write full content to files
    stdout_text, stdout_file = _truncate_large_output(
        exec_result.output, work_dir=work_dir, filename="full_stdout.txt",
    )
    stderr_text, stderr_file = _truncate_large_output(
        exec_result.error, work_dir=work_dir, filename="full_stderr.txt",
    )

    return CodeExecutionOutcome(
        success=success,
        code=last_code,
        description=code_response.description or f"Task: {task_title}",
        stdout=stdout_text,
        stderr=stderr_text,
        exit_code=exec_result.exit_code,
        attempts=attempt,
        code_file=code_file,
        has_visualization=bool(viz_files) or code_response.has_visualization,
        visualization_files=viz_files,
        visualization_purpose=code_response.visualization_purpose,
        visualization_analysis=code_response.visualization_analysis if viz_files else None,
        error_category=error_category,
        error_summary=(
            (error_analysis.summary_text if error_analysis else None)
            or str(exec_result.error or "").strip()
            or _extract_actionable_stdout_failure(exec_result.output)
            or None
        ),
        fix_guidance=fix_guidance,
        stdout_file=stdout_file,
        stderr_file=stderr_file,
        execution_backend=normalized_backend,
        runtime_failure=exec_result.runtime_failure,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_code_file(code_file: str, preamble: str, code: str) -> None:
    with open(code_file, "w", encoding="utf-8") as fh:
        fh.write(preamble)
        fh.write(code)


async def _ask_llm_to_fix(
    *,
    generator: CodeGenerator,
    metadata_list: list,
    task_title: str,
    task_description: str,
    code: str,
    error: str,
    error_category: str,
) -> Optional[str]:
    """Ask the LLM to fix code, injecting a category-specific hint."""
    hint = _FIX_HINTS.get(error_category, "")
    if error_category == "missing_package":
        pkg = _extract_missing_package(error)
        if pkg:
            hint = hint.format(pkg=pkg)

    augmented_error = error
    if hint:
        augmented_error = f"{error}\n\n## Fix guidance\n{hint}"

    code_response: CodeTaskResponse = await asyncio.to_thread(
        generator.fix_code,
        metadata_list=metadata_list,
        task_title=task_title,
        task_description=task_description,
        code=code,
        error=augmented_error,
        max_retries=1,
    )
    fixed = code_response.code
    if fixed and fixed.strip():
        return fixed
    return None
