"""
Unified local code execution: LLM generates Python → write to file → execute → fix on failure.

Both `task_executer.py` and `code_executor.py` delegate to `execute_code_locally()`
so there is a single implementation of the generate → run → fix loop.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.services.plans.acceptance_criteria import (
    derive_relative_output_dirs,
    resolve_glob_min_count,
    resolve_glob_pattern,
)

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
    execution_status: str = "failed"
    verification_status: Optional[str] = None
    failure_kind: Optional[str] = None
    contract_diff: Optional[Dict[str, Any]] = None
    repair_attempts: int = 0
    plan_patch_suggestion: Optional[str] = None


@dataclass
class CodeExecutionSpec:
    """Structured task contract passed into unified code execution."""

    plan_id: Optional[int] = None
    task_id: Optional[int] = None
    task_name: Optional[str] = None
    task_instruction: Optional[str] = None
    acceptance_criteria: Optional[Dict[str, Any]] = None
    dependency_outputs: List[Dict[str, Any]] = field(default_factory=list)
    dependency_artifact_paths: List[str] = field(default_factory=list)


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
_BLOCKED_DEPENDENCY_PATTERNS = (
    "blocked_dependency",
    "dependency missing",
    "missing upstream",
    "missing prerequisite",
    "missing prerequisites",
    "upstream deliverable",
    "upstream artifact",
    "upstream artifacts",
    "upstream output",
    "upstream outputs",
    "prerequisite not met",
    "prerequisites not met",
    "fewer than 2 valid",
    "need at least 2 valid",
    "requires at least 2 valid",
    "cannot proceed with integration",
    "missing filtered data for sample",
    "前置条件不满足",
    "依赖缺失",
    "缺少上游",
    "缺少前置",
    "上游产物",
)

# Regex patterns for parameterised dependency-blocker messages that simple
# substring matching cannot cover (e.g. "fewer than 5 valid filtered samples").
_BLOCKED_DEPENDENCY_REGEXES: tuple[re.Pattern, ...] = (
    re.compile(r"fewer than \d+ valid", re.IGNORECASE),
    re.compile(r"missing (?:filtered )?data for sample", re.IGNORECASE),
    re.compile(r"requires? the output from task", re.IGNORECASE),
    re.compile(r"ensure task \d+ has been completed", re.IGNORECASE),
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


def _looks_like_blocked_dependency(error_text: str) -> bool:
    lowered = str(error_text or "").strip().lower()
    if not lowered:
        return False
    if any(token in lowered for token in _BLOCKED_DEPENDENCY_PATTERNS):
        return True
    return any(rx.search(lowered) for rx in _BLOCKED_DEPENDENCY_REGEXES)


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
    if _looks_like_blocked_dependency(error_text):
        return "blocked_dependency"
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


_MAX_CONTRACT_REPAIR_ATTEMPTS = 1


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
    "blocked_dependency": (
        "The failure is a deterministic prerequisite or dependency blocker. "
        "Do NOT guess alternative paths or rewrite the task into upstream work. "
        "Report the missing prerequisite clearly and stop."
    ),
    "acceptance_criteria_failed": (
        "The code executed, but the authoritative task contract was not satisfied. "
        "Do NOT change task scope or methods. Preserve useful extra outputs, but generate the missing required deliverables exactly at the expected paths."
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
    execution_spec: Optional[CodeExecutionSpec] = None,
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

    # ---- Apply explicit execution contract and optional batch profiling ----
    contracted_description = _append_execution_contract(
        task_description,
        execution_spec=execution_spec,
    )
    enhanced_description = contracted_description
    if (
        execution_spec is not None
        and data_dir
        and Path(data_dir).exists()
        and _spec_requests_batch_profile(execution_spec)
    ):
        try:
            from app.services.interpreter.code_generation_enhancer import CodeGenerationEnhancer

            enhancer = CodeGenerationEnhancer()
            enhanced_description = await enhancer.enhance_task_description(
                task_description=contracted_description,
                data_dir=data_dir,
                require_batch_processing=True,
            )
            logger.info(
                "Task description enhanced with explicit batch contract for task %s",
                execution_spec.task_id,
            )
        except Exception as e:
            logger.warning(f"Failed to enhance task description: {e}")
            enhanced_description = contracted_description

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
            task_description=enhanced_description,  # Use enhanced description
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
    execution_status = "completed" if exec_result.status == "success" else "failed"
    verification_status: Optional[str] = None
    failure_kind: Optional[str] = None
    contract_diff: Optional[Dict[str, Any]] = None
    repair_attempts = 0
    plan_patch_suggestion: Optional[str] = None

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
        if error_category == "blocked_dependency":
            logger.info(
                "Skipping auto-fix because execution hit a blocked dependency: %.200s",
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

    post_execution_error_summary: Optional[str] = None
    if success and execution_spec is not None and execution_spec.acceptance_criteria:
        try:
            finalization = _verify_execution_against_contract(
                execution_spec=execution_spec,
                work_dir=work_dir,
            )
            verification, verification_status, failure_kind, contract_diff, plan_patch_suggestion = (
                _extract_verification_state(finalization)
            )
            if finalization.final_status == "failed":
                error_category = "acceptance_criteria_failed"
                post_execution_error_summary = _summarize_verification_failures(verification)
                fix_guidance = _format_verification_guidance(verification)
                error_analysis = None
                if auto_fix and repair_attempts < _MAX_CONTRACT_REPAIR_ATTEMPTS:
                    repair_attempts += 1
                    repaired_code = await _ask_llm_to_repair_contract(
                        generator=generator,
                        metadata_list=list(metadata_list),
                        task_title=task_title,
                        task_description=enhanced_description,
                        code=preamble + last_code,
                        verification=verification,
                        contract_diff=contract_diff or {},
                    )
                    if repaired_code and repaired_code.strip():
                        last_code = repaired_code
                        _write_code_file(code_file, preamble, last_code)
                        exec_result = await asyncio.to_thread(interpreter.run_file, code_file)
                        execution_status = "completed" if exec_result.status == "success" else "failed"
                        attempt += 1
                        logger.info(
                            "%s contract-repair attempt %d/%d: status=%s exit_code=%d runtime_failure=%s",
                            normalized_backend,
                            repair_attempts,
                            _MAX_CONTRACT_REPAIR_ATTEMPTS,
                            exec_result.status,
                            exec_result.exit_code,
                            exec_result.runtime_failure,
                        )
                        success = exec_result.status == "success"
                        if success:
                            finalization = _verify_execution_against_contract(
                                execution_spec=execution_spec,
                                work_dir=work_dir,
                            )
                            verification, verification_status, failure_kind, contract_diff, plan_patch_suggestion = (
                                _extract_verification_state(finalization)
                            )
                            if finalization.final_status == "failed":
                                success = False
                                error_category = "acceptance_criteria_failed"
                                post_execution_error_summary = _summarize_verification_failures(verification)
                                fix_guidance = _format_verification_guidance(verification)
                            else:
                                success = True
                                error_category = None
                                post_execution_error_summary = None
                                fix_guidance = None
                        else:
                            verification_status = "not_run"
                            failure_kind = "execution_failed"
                            contract_diff = None
                            plan_patch_suggestion = None
                            if exec_result.runtime_failure:
                                error_category = _runtime_error_category(normalized_backend)
                            else:
                                error_analysis = _analyze_execution_failure(
                                    exec_result.error,
                                    exec_result.output,
                                    exec_result.exit_code,
                                )
                                error_category = error_analysis.category
                                post_execution_error_summary = error_analysis.summary_text
                                fix_guidance = _FIX_HINTS.get(error_category, "")
                    else:
                        success = False
                else:
                    success = False
                if not success and error_category == "acceptance_criteria_failed":
                    logger.warning(
                        "Explicit acceptance-criteria verification failed for task %s: %s",
                        execution_spec.task_id,
                        post_execution_error_summary,
                    )
        except Exception as e:
            logger.warning(f"Failed to verify explicit acceptance criteria: {e}")

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
            post_execution_error_summary
            or (error_analysis.summary_text if error_analysis else None)
            or str(exec_result.error or "").strip()
            or _extract_actionable_stdout_failure(exec_result.output)
            or None
        ),
        fix_guidance=fix_guidance,
        stdout_file=stdout_file,
        stderr_file=stderr_file,
        execution_backend=normalized_backend,
        runtime_failure=exec_result.runtime_failure,
        execution_status=execution_status,
        verification_status=verification_status,
        failure_kind=failure_kind,
        contract_diff=contract_diff,
        repair_attempts=repair_attempts,
        plan_patch_suggestion=plan_patch_suggestion,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _append_execution_contract(
    task_description: str,
    *,
    execution_spec: Optional[CodeExecutionSpec],
) -> str:
    if execution_spec is None:
        return task_description

    lines: List[str] = [task_description.strip(), "", "## Structured Execution Contract"]
    if execution_spec.task_id is not None:
        lines.append(f"- Task ID: {execution_spec.task_id}")
    if execution_spec.task_name:
        lines.append(f"- Task Name: {execution_spec.task_name}")

    if execution_spec.dependency_outputs:
        lines.append("- Upstream dependencies:")
        for dep in execution_spec.dependency_outputs[:6]:
            dep_name = str(dep.get("task_name") or dep.get("task_id") or "unknown").strip()
            dep_status = str(dep.get("status") or "unknown").strip()
            artifact_paths = dep.get("artifact_paths") if isinstance(dep, dict) else []
            if isinstance(artifact_paths, list) and artifact_paths:
                joined = "; ".join(str(item).strip() for item in artifact_paths[:4] if str(item).strip())
                if len(artifact_paths) > 4:
                    joined += "; ..."
                lines.append(f"  - {dep_name} [{dep_status}] -> {joined}")
            else:
                lines.append(f"  - {dep_name} [{dep_status}]")

    formatted_checks = _format_acceptance_checks(execution_spec.acceptance_criteria)
    if formatted_checks:
        lines.append("- Deterministic acceptance criteria (authoritative):")
        lines.extend(f"  - {item}" for item in formatted_checks)
        lines.append("- The plan contract is authoritative: required deliverables must be produced exactly as specified.")
        lines.append("- Extra outputs are allowed, but they do NOT replace missing required deliverables.")

    return "\n".join(line for line in lines if line is not None).strip()


def _format_acceptance_checks(criteria: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(criteria, dict):
        return []
    checks = criteria.get("checks")
    if not isinstance(checks, list):
        return []

    formatted: List[str] = []
    for raw_check in checks[:12]:
        if not isinstance(raw_check, dict):
            continue
        check_type = str(raw_check.get("type") or "").strip()
        if check_type == "file_exists":
            formatted.append(f"file must exist: {raw_check.get('path')}")
        elif check_type == "file_nonempty":
            formatted.append(f"file must be non-empty: {raw_check.get('path')}")
        elif check_type == "glob_count_at_least":
            pattern = resolve_glob_pattern(raw_check)
            min_count = resolve_glob_min_count(raw_check)
            formatted.append(
                f"at least {min_count} matches for glob: {pattern}"
            )
        elif check_type == "text_contains":
            formatted.append(
                f"text file {raw_check.get('path')} must contain: {raw_check.get('pattern')}"
            )
        elif check_type == "json_field_equals":
            key_path = raw_check.get("key_path") or raw_check.get("field")
            expected = (
                raw_check.get("expected")
                if raw_check.get("expected") is not None
                else raw_check.get("value")
            )
            formatted.append(
                f"json {raw_check.get('path')} field {key_path} must equal {expected}"
            )
        elif check_type == "json_field_at_least":
            key_path = raw_check.get("key_path") or raw_check.get("field")
            min_value = (
                raw_check.get("min_value")
                if raw_check.get("min_value") is not None
                else raw_check.get("value")
            )
            formatted.append(
                f"json {raw_check.get('path')} field {key_path} must be >= {min_value}"
            )
        else:
            formatted.append(json.dumps(raw_check, ensure_ascii=False))
    return formatted


def _spec_requests_batch_profile(execution_spec: CodeExecutionSpec) -> bool:
    criteria = execution_spec.acceptance_criteria
    checks = criteria.get("checks") if isinstance(criteria, dict) else None
    if not isinstance(checks, list):
        return False
    for raw_check in checks:
        if not isinstance(raw_check, dict):
            continue
        if str(raw_check.get("type") or "").strip() != "glob_count_at_least":
            continue
        min_count = resolve_glob_min_count(raw_check)
        if min_count > 1:
            return True
    return False


def _collect_contract_artifact_paths(
    work_dir: str,
    *,
    acceptance_criteria: Optional[Dict[str, Any]] = None,
) -> List[str]:
    del acceptance_criteria
    collected: List[str] = []
    seen: set[str] = set()
    for subdir in ("results", "data", "docs"):
        root = Path(work_dir) / subdir
        if not root.exists():
            continue
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue
            text = str(candidate.resolve())
            if text in seen:
                continue
            seen.add(text)
            collected.append(text)
    return collected


def _collect_guidance_artifact_paths(
    work_dir: str,
    *,
    acceptance_criteria: Optional[Dict[str, Any]] = None,
) -> List[str]:
    collected: List[str] = []
    seen: set[str] = set()
    for subdir in derive_relative_output_dirs(
        acceptance_criteria,
        default_dirs=("results", "code", "data", "docs"),
    ):
        root = Path(work_dir) / subdir
        if not root.exists():
            continue
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue
            text = str(candidate.resolve())
            if text in seen:
                continue
            seen.add(text)
            collected.append(text)
    return collected


def _find_same_name_artifact_candidates(
    expected_path: str,
    artifact_paths: Sequence[str],
    *,
    limit: int = 3,
) -> List[str]:
    expected_text = str(expected_path or "").strip()
    expected_name = Path(expected_text).name.lower()
    if not expected_name:
        return []

    matches: List[str] = []
    seen: set[str] = set()
    for raw_path in artifact_paths:
        candidate_text = str(raw_path or "").strip()
        if not candidate_text or candidate_text == expected_text:
            continue
        candidate = Path(candidate_text)
        if not candidate.exists() or not candidate.is_file():
            continue
        if candidate.name.lower() != expected_name:
            continue
        if candidate_text in seen:
            continue
        seen.add(candidate_text)
        matches.append(candidate_text)
        if len(matches) >= limit:
            break
    return matches


def _verify_execution_against_contract(
    *,
    execution_spec: CodeExecutionSpec,
    work_dir: str,
):
    from app.services.plans.plan_models import PlanNode
    from app.services.plans.task_verification import TaskVerificationService

    criteria = copy.deepcopy(execution_spec.acceptance_criteria) or {}
    criteria.setdefault("blocking", True)
    criteria.setdefault("base_dir", work_dir)

    node = PlanNode(
        id=int(execution_spec.task_id or 0),
        plan_id=int(execution_spec.plan_id or 0),
        name=execution_spec.task_name or "Code execution task",
        instruction=execution_spec.task_instruction,
        metadata={"acceptance_criteria": criteria},
    )
    payload = {
        "status": "completed",
        "artifact_paths": _collect_contract_artifact_paths(
            work_dir,
            acceptance_criteria=criteria,
        ),
        "metadata": {
            "guidance_artifact_paths": _collect_guidance_artifact_paths(
                work_dir,
                acceptance_criteria=criteria,
            )
        },
    }
    verifier = TaskVerificationService()
    return verifier.finalize_payload(
        node,
        payload,
        execution_status="completed",
        trigger="auto",
    )


def _extract_verification_state(
    finalization,
) -> tuple[Dict[str, Any], Optional[str], Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    verification = finalization.verification or {}
    payload_meta = finalization.payload.get("metadata") if isinstance(finalization.payload, dict) else {}
    if not isinstance(payload_meta, dict):
        payload_meta = {}
    guidance_paths = payload_meta.get("guidance_artifact_paths")
    if isinstance(guidance_paths, list):
        evidence = verification.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {}
            verification["evidence"] = evidence
        evidence["guidance_artifact_paths"] = guidance_paths
    verification_status = str(
        payload_meta.get("verification_status") or verification.get("status") or ""
    ).strip() or None
    failure_kind = str(payload_meta.get("failure_kind") or "").strip() or None
    contract_diff = payload_meta.get("contract_diff")
    if not isinstance(contract_diff, dict):
        alt = verification.get("contract_diff")
        contract_diff = alt if isinstance(alt, dict) else None
    plan_patch_suggestion = str(
        payload_meta.get("plan_patch_suggestion") or verification.get("plan_patch_suggestion") or ""
    ).strip() or None
    return verification, verification_status, failure_kind, contract_diff, plan_patch_suggestion


def _summarize_verification_failures(verification: Dict[str, Any]) -> str:
    failures = verification.get("failures")
    if not isinstance(failures, list) or not failures:
        return "Deterministic acceptance criteria failed."
    snippets: List[str] = []
    for failure in failures[:3]:
        if not isinstance(failure, dict):
            continue
        failure_type = str(failure.get("type") or "check").strip()
        message = str(failure.get("message") or "").strip()
        path = str(failure.get("path") or "").strip()
        expected_glob = str(failure.get("glob") or "").strip()
        if message:
            if path:
                snippets.append(f"{failure_type}: {path} ({message})")
            elif expected_glob:
                snippets.append(f"{failure_type}: {expected_glob} ({message})")
            else:
                snippets.append(f"{failure_type}: {message}")
        else:
            if path:
                snippets.append(f"{failure_type}: {path}")
            elif expected_glob:
                snippets.append(f"{failure_type}: {expected_glob}")
            else:
                snippets.append(failure_type)
    return "; ".join(snippets) if snippets else "Deterministic acceptance criteria failed."


def _format_verification_guidance(verification: Dict[str, Any]) -> str:
    failures = verification.get("failures")
    if not isinstance(failures, list) or not failures:
        return "Inspect the deterministic acceptance criteria and regenerate the missing outputs."
    evidence = verification.get("evidence")
    artifact_paths = []
    if isinstance(evidence, dict):
        guidance_paths = evidence.get("guidance_artifact_paths")
        if isinstance(guidance_paths, list) and guidance_paths:
            artifact_paths = guidance_paths
        else:
            candidate_paths = evidence.get("artifact_paths")
            if isinstance(candidate_paths, list):
                artifact_paths = candidate_paths
    if not isinstance(artifact_paths, list):
        artifact_paths = []
    bullets: List[str] = []
    for failure in failures[:3]:
        if not isinstance(failure, dict):
            continue
        failure_type = str(failure.get("type") or "check").strip()
        message = str(failure.get("message") or "").strip()
        path = str(failure.get("path") or "").strip()
        expected_glob = str(failure.get("glob") or "").strip()
        if path:
            detail = message or "Required file check failed."
            line = f"{detail} Expected path: {path}."
            same_name_hits = _find_same_name_artifact_candidates(path, artifact_paths)
            if same_name_hits:
                line += (
                    " Found same filename at: "
                    + "; ".join(same_name_hits)
                    + ". Move or copy the valid file to the expected path instead of leaving it elsewhere."
                )
            bullets.append(line)
            continue
        if expected_glob:
            detail = message or "Required glob did not match enough outputs."
            bullets.append(f"{detail} Expected glob: {expected_glob}.")
            continue
        if message:
            bullets.append(f"{failure_type}: {message}")
    if not bullets:
        return "Inspect the deterministic acceptance criteria and regenerate the missing outputs."
    return "Acceptance criteria failed: " + "; ".join(bullets)


def _format_contract_diff(contract_diff: Optional[Dict[str, Any]]) -> str:
    if not isinstance(contract_diff, dict):
        return ""

    def _join(key: str, limit: int = 6) -> str:
        values = contract_diff.get(key)
        if not isinstance(values, list) or not values:
            return ""
        cleaned = [str(item).strip() for item in values if str(item).strip()]
        if not cleaned:
            return ""
        if len(cleaned) > limit:
            cleaned = cleaned[:limit] + ["..."]
        return ", ".join(cleaned)

    lines: List[str] = []
    expected = _join("expected_deliverables")
    missing = _join("missing_required_outputs")
    wrong = _join("wrong_format_outputs")
    unexpected = _join("unexpected_outputs")
    actual = _join("actual_outputs")
    if expected:
        lines.append(f"Expected deliverables: {expected}")
    if missing:
        lines.append(f"Missing required outputs: {missing}")
    if wrong:
        lines.append(f"Wrong-format outputs: {wrong}")
    if unexpected:
        lines.append(f"Unexpected extra outputs: {unexpected}")
    if actual:
        lines.append(f"Actual outputs observed: {actual}")
    return "\n".join(lines)


async def _ask_llm_to_repair_contract(
    *,
    generator: CodeGenerator,
    metadata_list: list,
    task_title: str,
    task_description: str,
    code: str,
    verification: Dict[str, Any],
    contract_diff: Dict[str, Any],
) -> Optional[str]:
    guidance = _format_verification_guidance(verification)
    contract_text = _format_contract_diff(contract_diff)
    error = (
        "The code executed successfully, but it did NOT satisfy the authoritative task contract.\n"
        "Repair the code so that all required deliverables are produced exactly at the expected paths/patterns.\n"
        "Do NOT change task scope, scientific method, thresholds, or upstream/downstream responsibilities.\n"
        "Keep useful extra outputs if you want, but they do not count as replacements for missing required outputs.\n"
    )
    if contract_text:
        error += "\n## Contract mismatch\n" + contract_text + "\n"
    if guidance:
        error += "\n## Repair guidance\n" + guidance + "\n"
    code_response: CodeTaskResponse = await asyncio.to_thread(
        generator.fix_code,
        metadata_list=metadata_list,
        task_title=task_title,
        task_description=task_description,
        code=code,
        error=error,
        max_retries=1,
    )
    fixed = code_response.code
    if fixed and fixed.strip():
        return fixed
    return None


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
