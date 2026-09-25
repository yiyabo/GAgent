"""Qwen process lifecycle + command/mount assembly for code_executor.

Extracted from ``code_executor.py`` (clusters C5 + the qwen half of C7) per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.1. This sibling owns
the qwen CLI timeout knobs, the drain/watchdog loop, the early-exit contract
verification + flat-output materialization, transcript extraction and pending
shell-call recovery, CLI failure error construction, partial-completion
detection, and the qwen command/container-mount builders.  The delegation cancel
watcher lives here too, and its 0.25s poll also carries the delegation's progress
heartbeat (``delegation_progress``), self-gated to its own interval.

Compatibility contract (gating.py pattern): the ``code_executor`` facade
re-exports every name defined here; test and production import sites keep
working unchanged. Sibling-to-sibling imports (semantic constants, cli_parse
helpers) are top-level because none of those names has a monkeypatch surface
(grep-verified). The four calls into facade-resident names
(``_build_cli_task_contract``, ``_rerun_update_mode_prompt``,
``_final_response_contract_prompt``, ``_is_path_within``) resolve through
``_ce()`` at call time — the door is one-way.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import signal
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from app.services.cancellation import current_cancel_token
from app.services.plans.acceptance_criteria import derive_expected_deliverables

from .code_executor_cli_parse import (
    _DEFAULT_TASK_SUBDIRECTORIES,
    _compact_cli_text,
    _extract_qwen_debug_log_path,
    _extract_readable_error,
    _format_directory_choices,
    _format_task_subdirectories,
    _is_qwen_truncated_tool_failure_text,
    _qwen_truncated_tool_failure_note,
)
from .code_executor_semantic import (
    _BLOCK_SCOPE_REASON,
    _BLOCK_SCOPE_STATUS,
)

logger = logging.getLogger(__name__)


def _ce():
    """Late-bind the code_executor facade (gating ``_dta()`` pattern).

    Sanctioned deviation: siblings never import facade top-level names
    directly. The calls into facade-resident helpers (C8 prompt builders and
    the C10 path predicate) resolve through the facade module object at call
    time, which also keeps this module correct when those helpers themselves
    move to later siblings (contracts/backend) — the facade re-export keeps
    resolving.
    """
    from tool_box.tools_impl import code_executor

    return code_executor


_QWEN_TRANSCRIPTS_ROOT = "/tmp/gagent_home/.qwen/projects"
_QWEN_SHELL_FALLBACK_TIMEOUT_MS = 600000
_QWEN_SHELL_FALLBACK_MAX_TIMEOUT_MS = 3600000
_QWEN_COMPLETED_OUTPUT_EXIT_GRACE_SECONDS = 300.0
_QWEN_COMPLETED_OUTPUT_EXIT_CHECK_SECONDS = 15.0
_QWEN_PROCESS_EXIT_WAIT_SECONDS = 30.0
_QWEN_PROCESS_KILL_WAIT_SECONDS = 10.0
_QWEN_CLI_NO_OUTPUT_TIMEOUT_SECONDS = 1800.0
_QWEN_FATAL_DEBUG_SCAN_BYTES = 65536
_DELEGATION_CANCEL_POLL_SECONDS = 0.25
_DELEGATION_CANCEL_GRACE_SECONDS = 2.0

#: Marker appended to stderr when a cancel killed the delegation's CLI process.
#: It must not overlap the qwen watchdog / infrastructure patterns in
#: ``code_executor_backend`` — otherwise a cancellation would be reclassified as
#: a retryable infrastructure failure and re-delegated.
DELEGATION_CANCELLED_MARKER = "[DELEGATION_CANCELLED]"
DELEGATION_CANCELLED_NOTE = (
    f"{DELEGATION_CANCELLED_MARKER} delegation cancelled by user request; "
    "the CLI process group was terminated"
)


def _resolve_qwen_completed_output_exit_grace_seconds() -> float:
    raw = str(os.getenv("QWEN_COMPLETED_OUTPUT_EXIT_GRACE_SECONDS", "")).strip()
    if not raw:
        return _QWEN_COMPLETED_OUTPUT_EXIT_GRACE_SECONDS
    try:
        return max(30.0, min(1800.0, float(raw)))
    except ValueError:
        return _QWEN_COMPLETED_OUTPUT_EXIT_GRACE_SECONDS


def _resolve_qwen_completed_output_exit_check_seconds() -> float:
    raw = str(os.getenv("QWEN_COMPLETED_OUTPUT_EXIT_CHECK_SECONDS", "")).strip()
    if not raw:
        return _QWEN_COMPLETED_OUTPUT_EXIT_CHECK_SECONDS
    try:
        return max(5.0, min(300.0, float(raw)))
    except ValueError:
        return _QWEN_COMPLETED_OUTPUT_EXIT_CHECK_SECONDS


def _resolve_qwen_process_exit_wait_seconds() -> float:
    raw = str(os.getenv("QWEN_PROCESS_EXIT_WAIT_SECONDS", "")).strip()
    if not raw:
        return _QWEN_PROCESS_EXIT_WAIT_SECONDS
    try:
        return max(1.0, min(300.0, float(raw)))
    except ValueError:
        return _QWEN_PROCESS_EXIT_WAIT_SECONDS


def _resolve_qwen_process_kill_wait_seconds() -> float:
    raw = str(os.getenv("QWEN_PROCESS_KILL_WAIT_SECONDS", "")).strip()
    if not raw:
        return _QWEN_PROCESS_KILL_WAIT_SECONDS
    try:
        return max(1.0, min(60.0, float(raw)))
    except ValueError:
        return _QWEN_PROCESS_KILL_WAIT_SECONDS


def _resolve_qwen_cli_no_output_timeout_seconds() -> float:
    raw = str(os.getenv("QWEN_CLI_NO_OUTPUT_TIMEOUT_SECONDS", "")).strip()
    if not raw:
        return _QWEN_CLI_NO_OUTPUT_TIMEOUT_SECONDS
    try:
        return max(5.0, min(7200.0, float(raw)))
    except ValueError:
        return _QWEN_CLI_NO_OUTPUT_TIMEOUT_SECONDS


def _resolve_delegation_cancel_poll_seconds() -> float:
    """How long a cancelled delegation may take to notice (bounds the latency).

    Clamped to 2s so that even at the maximum setting the CLI receives its
    termination signal well inside the promised five-second bound.
    """
    raw = str(os.getenv("DELEGATION_CANCEL_POLL_SECONDS", "")).strip()
    if not raw:
        return _DELEGATION_CANCEL_POLL_SECONDS
    try:
        return max(0.05, min(2.0, float(raw)))
    except ValueError:
        return _DELEGATION_CANCEL_POLL_SECONDS


def _resolve_delegation_cancel_grace_seconds() -> float:
    """How long SIGTERM gets to work before the process group is SIGKILLed."""
    raw = str(os.getenv("DELEGATION_CANCEL_GRACE_SECONDS", "")).strip()
    if not raw:
        return _DELEGATION_CANCEL_GRACE_SECONDS
    try:
        return max(0.0, min(10.0, float(raw)))
    except ValueError:
        return _DELEGATION_CANCEL_GRACE_SECONDS


def _is_delegation_cancelled(*texts: Any) -> bool:
    """True when any supplied stdout/stderr text carries the cancel marker."""
    for text in texts:
        if DELEGATION_CANCELLED_MARKER in str(text or ""):
            return True
    return False


def _cli_process_group_id(process: Any) -> Optional[int]:
    """The CLI child's process group, or None when it cannot be signalled.

    None covers every shape the escalation has to fall back from: a fake
    process without a pid (tests), an already reaped child, and non-POSIX hosts.
    """
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        return os.getpgid(pid)
    except (ProcessLookupError, PermissionError, OSError):
        return None


async def _terminate_cli_process_group(process: Any, *, cli_label: str) -> None:
    """SIGTERM the CLI's process group, escalating to SIGKILL after the grace window.

    Same escalation as ``execute_code``'s kernel kill (``_kill_process_group``)
    and the same intent as the qwen watchdog/timeout branches: the process body
    must be gone, not merely reported as dead.  Killing the *group* is what
    reaches the shell/python children the CLI spawned; it requires the CLI to be
    its own session leader (``start_new_session=True`` at spawn).  When the group
    cannot be resolved — or would be our own process group, which must never be
    signalled — the fallback is the pre-existing ``process.kill()``.
    """
    group = _cli_process_group_id(process)
    if group is None or group == os.getpgrp():
        try:
            process.kill()
        except ProcessLookupError:
            pass
        return

    try:
        os.killpg(group, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass

    grace = _ce()._resolve_delegation_cancel_grace_seconds()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + grace
    while getattr(process, "returncode", None) is None and loop.time() < deadline:
        await asyncio.sleep(0.05)

    if getattr(process, "returncode", None) is None:
        logger.warning(
            "[CODE_EXECUTOR] %s process group %s survived SIGTERM for %.1fs; killing.",
            cli_label,
            group,
            grace,
        )
        try:
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


async def _await_delegation_cancellation(
    *,
    process: Any,
    cli_label: str,
    cancel_state: Dict[str, Any],
    container_name: Optional[str] = None,
    on_tick: Optional[Callable[[], Awaitable[None]]] = None,
) -> None:
    """Kill the CLI process group once the ambient cancel token is set.

    Runs beside the normal drain/watchdog wait, so the wait loop's cadence is
    untouched when nothing cancels (the watcher is a no-op task then).  The
    kill makes the stream drain finish, which lets the ordinary return-code
    path report a terminated CLI instead of a fabricated success.

    ``on_tick`` rides the same poll: it is the delegation's progress heartbeat
    (``delegation_progress``), which self-gates on its own interval so the 0.25s
    poll never becomes an event every 0.25s.  A tick that raises is contained
    here — a broken progress channel must not retire the cancel watch.
    """
    token = current_cancel_token()
    if token is None and on_tick is None:
        return

    poll = _ce()._resolve_delegation_cancel_poll_seconds()
    while token is None or not token.cancelled:
        if getattr(process, "returncode", None) is not None:
            return
        await asyncio.sleep(poll)
        if on_tick is not None:
            try:
                await on_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - fail-open by contract
                logger.warning(
                    "[CODE_EXECUTOR] %s delegation progress heartbeat failed: %s: %s",
                    cli_label,
                    type(exc).__name__,
                    exc,
                )

    if getattr(process, "returncode", None) is not None:
        return

    cancel_state["cancelled"] = True
    cancel_state["note"] = DELEGATION_CANCELLED_NOTE
    logger.warning(
        "[CODE_EXECUTOR] %s delegation cancelled (token=%s); terminating CLI process group "
        "(pid=%s container=%s)",
        cli_label,
        (token.reason or "cancelled") if token is not None else "cancelled",
        getattr(process, "pid", None),
        container_name or "-",
    )
    await _terminate_cli_process_group(process, cli_label=cli_label)


def _start_delegation_cancel_watch(
    *,
    process: Any,
    cli_label: str,
    cancel_state: Dict[str, Any],
    container_name: Optional[str] = None,
    on_tick: Optional[Callable[[], Awaitable[None]]] = None,
) -> None:
    """(Re)arm the cancel watcher for *process*; the previous one is retired.

    Nothing is scheduled at all when neither a token is bound nor a progress
    heartbeat is wanted, so an execution outside a cancellable run with no
    progress channel keeps its exact previous behaviour.
    """
    _stop_delegation_cancel_watch(cancel_state)
    if current_cancel_token() is None and on_tick is None:
        return

    async def _watch() -> None:
        try:
            await _await_delegation_cancellation(
                process=process,
                cli_label=cli_label,
                cancel_state=cancel_state,
                container_name=container_name,
                on_tick=on_tick,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "[CODE_EXECUTOR] delegation cancel watcher failed (%s): %s",
                cli_label,
                type(exc).__name__,
            )

    cancel_state["task"] = asyncio.get_running_loop().create_task(_watch())


def _stop_delegation_cancel_watch(cancel_state: Dict[str, Any]) -> None:
    """Retire the watcher task; it is already done once the CLI has exited."""
    task = cancel_state.pop("task", None)
    if task is None:
        return
    if not task.done():
        task.cancel()
        return
    # A finished task is never awaited; retrieving its (swallowed) exception here
    # keeps asyncio from reporting it as never retrieved.
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


async def _wait_for_cli_process_return_code(
    process: Any,
    *,
    backend_label: str,
    exit_timeout: Optional[float] = None,
    kill_timeout: Optional[float] = None,
) -> int:
    return_code = getattr(process, "returncode", None)
    if return_code is not None:
        return int(return_code)

    facade = _ce()
    effective_exit_timeout = (
        facade._resolve_qwen_process_exit_wait_seconds()
        if exit_timeout is None
        else float(exit_timeout)
    )
    effective_kill_timeout = (
        facade._resolve_qwen_process_kill_wait_seconds()
        if kill_timeout is None
        else float(kill_timeout)
    )
    try:
        return int(await asyncio.wait_for(process.wait(), timeout=effective_exit_timeout))
    except asyncio.TimeoutError:
        logger.warning(
            "[CODE_EXECUTOR] %s process did not exit after %.0fs; terminating.",
            backend_label,
            effective_exit_timeout,
        )
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            return int(await asyncio.wait_for(process.wait(), timeout=effective_kill_timeout))
        except asyncio.TimeoutError:
            logger.warning(
                "[CODE_EXECUTOR] %s process did not exit %.0fs after kill; continuing with timeout code.",
                backend_label,
                effective_kill_timeout,
            )
            return -1


async def _wait_for_qwen_cli_drain_or_watchdog(
    *,
    process: Any,
    drain_task: Any,
    stdout_task: Any,
    stderr_task: Any,
    local_stdout_lines: List[str],
    local_stderr_lines: List[str],
    cli_progress: Dict[str, float],
    can_finish_from_contract_outputs: bool,
    execution_spec: Optional[Dict[str, Any]],
    task_work_dir: Path,
    unified_output_dir: Optional[Path],
    cli_label: str,
    container_name: Optional[str],
    log_file: Any,
    log_lock: asyncio.Lock,
) -> Optional[int]:
    """Wait for qwen CLI stream drain, with contract early-exit and no-output watchdog.

    Returns an override return code when the process was intentionally killed,
    otherwise None so the caller can wait for the process normally.
    """
    facade = _ce()
    no_output_timeout = facade._resolve_qwen_cli_no_output_timeout_seconds()
    no_output_deadline = float(cli_progress.get("last_output_at") or 0.0) + no_output_timeout
    grace_deadline: Optional[float] = None

    while True:
        if drain_task.done():
            await drain_task
            return None

        wait_seconds = (
            facade._resolve_qwen_completed_output_exit_check_seconds()
            if can_finish_from_contract_outputs
            else 15.0
        )
        loop = asyncio.get_running_loop()
        wait_seconds = max(0.1, min(wait_seconds, no_output_deadline - loop.time()))
        done, _pending = await asyncio.wait({drain_task}, timeout=wait_seconds)
        if done:
            await drain_task
            return None

        fatal_note = await _detect_qwen_debug_fatal_failure(
            stderr_text="\n".join(local_stderr_lines),
            container_name=container_name,
        )
        if fatal_note:
            logger.warning("[CODE_EXECUTOR] %s", fatal_note)
            if log_file:
                try:
                    async with log_lock:
                        log_file.write(f"[{datetime.utcnow().isoformat()}Z] {fatal_note}\n")
                        log_file.flush()
                except Exception:
                    pass
            local_stderr_lines.append(fatal_note)
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await _wait_for_cli_process_return_code(
                    process,
                    backend_label=cli_label,
                    exit_timeout=facade._resolve_qwen_process_kill_wait_seconds(),
                    kill_timeout=facade._resolve_qwen_process_kill_wait_seconds(),
                )
            except Exception:
                pass
            try:
                await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                stdout_task.cancel()
                stderr_task.cancel()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            return -1

        loop = asyncio.get_running_loop()
        last_output_at = float(cli_progress.get("last_output_at") or loop.time())
        timeout_seconds = facade._resolve_qwen_cli_no_output_timeout_seconds()
        if loop.time() - last_output_at >= timeout_seconds:
            timeout_note = (
                f"[QWEN_NO_OUTPUT_TIMEOUT] qwen_cli_no_output_timeout: "
                f"qwen CLI produced no stdout/stderr for {timeout_seconds:.0f}s "
                f"and did not exit"
            )
            logger.warning("[CODE_EXECUTOR] %s", timeout_note)
            if log_file:
                try:
                    async with log_lock:
                        log_file.write(f"[{datetime.utcnow().isoformat()}Z] {timeout_note}\n")
                        log_file.flush()
                except Exception:
                    pass
            local_stderr_lines.append(timeout_note)
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await _wait_for_cli_process_return_code(
                    process,
                    backend_label=cli_label,
                    exit_timeout=facade._resolve_qwen_process_kill_wait_seconds(),
                    kill_timeout=facade._resolve_qwen_process_kill_wait_seconds(),
                )
            except Exception:
                pass
            try:
                await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                stdout_task.cancel()
                stderr_task.cancel()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            return -1
        no_output_deadline = last_output_at + timeout_seconds

        if not can_finish_from_contract_outputs:
            continue

        alternate_work_dirs = [unified_output_dir] if unified_output_dir is not None else None
        passed, reason = _qwen_outputs_pass_contract_for_early_exit(
            execution_spec=execution_spec,
            task_work_dir=task_work_dir,
            alternate_work_dirs=alternate_work_dirs,
        )
        if not passed:
            grace_deadline = None
            continue

        if grace_deadline is None:
            grace_seconds = facade._resolve_qwen_completed_output_exit_grace_seconds()
            grace_deadline = loop.time() + grace_seconds
            logger.info(
                "[CODE_EXECUTOR] Qwen outputs satisfy contract before CLI exit; "
                "waiting %.0fs for graceful final response (%s).",
                grace_seconds,
                reason,
            )
            if log_file:
                try:
                    async with log_lock:
                        log_file.write(
                            f"[{datetime.utcnow().isoformat()}Z] "
                            "Qwen outputs satisfy contract before CLI exit; "
                            f"waiting {grace_seconds:.0f}s for graceful final response\n"
                        )
                        log_file.flush()
                except Exception:
                    pass
            continue
        if loop.time() < grace_deadline:
            continue

        logger.info(
            "[CODE_EXECUTOR] Task completed successfully. All required outputs verified. "
            "Qwen CLI session finalized after grace period."
        )
        if log_file:
            try:
                async with log_lock:
                    log_file.write(
                        f"[{datetime.utcnow().isoformat()}Z] "
                        "Task completed successfully. All required outputs verified. "
                        "Qwen CLI session finalized.\n"
                    )
                    log_file.flush()
            except Exception:
                pass
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await _wait_for_cli_process_return_code(
                process,
                backend_label=cli_label,
                exit_timeout=facade._resolve_qwen_process_kill_wait_seconds(),
                kill_timeout=facade._resolve_qwen_process_kill_wait_seconds(),
            )
        except Exception:
            pass
        try:
            await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            stdout_task.cancel()
            stderr_task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        local_stdout_lines.append(
            "[code_executor] Task completed successfully. All required outputs verified and ready."
        )
        return 0


def _verify_contract_for_qwen_early_exit(
    execution_spec: Dict[str, Any],
    *,
    task_work_dir: Path,
) -> object:
    from app.services.interpreter.code_execution import (
        CodeExecutionSpec,
        _verify_execution_against_contract,
    )

    return _verify_execution_against_contract(
        execution_spec=CodeExecutionSpec(
            plan_id=execution_spec.get("plan_id"),
            task_id=execution_spec.get("task_id"),
            task_name=execution_spec.get("task_name"),
            task_instruction=execution_spec.get("task_instruction"),
            acceptance_criteria=execution_spec.get("acceptance_criteria"),
            dependency_outputs=list(execution_spec.get("dependency_outputs") or []),
            dependency_artifact_paths=list(execution_spec.get("dependency_artifact_paths") or []),
        ),
        work_dir=str(task_work_dir),
    )


def _qwen_outputs_pass_contract_for_early_exit(
    *,
    execution_spec: Optional[Dict[str, Any]],
    task_work_dir: Path,
    alternate_work_dirs: Optional[Sequence[Path]] = None,
) -> tuple[bool, str]:
    if not isinstance(execution_spec, dict):
        return False, "no_acceptance_criteria"
    acceptance_criteria = execution_spec.get("acceptance_criteria")
    if not isinstance(acceptance_criteria, dict):
        return False, "no_acceptance_criteria"
    checks = acceptance_criteria.get("checks")
    if not isinstance(checks, list) or not checks:
        return False, "no_acceptance_checks"

    work_dirs: List[Path] = [task_work_dir]
    seen_dirs = {str(task_work_dir)}
    for candidate in alternate_work_dirs or ():
        candidate_path = Path(candidate)
        candidate_text = str(candidate_path)
        if candidate_text in seen_dirs:
            continue
        seen_dirs.add(candidate_text)
        work_dirs.append(candidate_path)

    last_reason = "verification_not_run"
    for candidate_dir in work_dirs:
        passed, reason = _qwen_single_work_dir_passes_contract_for_early_exit(
            execution_spec=execution_spec,
            task_work_dir=candidate_dir,
        )
        if passed:
            if candidate_dir != task_work_dir:
                _ = _materialize_contract_outputs_for_standard_paths(
                    execution_spec=execution_spec,
                    source_dir=candidate_dir,
                    scratch_dir=task_work_dir,
                )
            return True, reason if candidate_dir == task_work_dir else f"{reason}:alternate_output_dir"
        if candidate_dir != task_work_dir:
            view_dir = _materialize_contract_view_for_flat_outputs(
                execution_spec=execution_spec,
                source_dir=candidate_dir,
                scratch_dir=task_work_dir,
            )
            if view_dir is not None:
                passed, view_reason = _qwen_single_work_dir_passes_contract_for_early_exit(
                    execution_spec=execution_spec,
                    task_work_dir=view_dir,
                )
                if passed:
                    _ = _materialize_contract_outputs_for_standard_paths(
                        execution_spec=execution_spec,
                        source_dir=view_dir,
                        scratch_dir=task_work_dir,
                    )
                    return True, f"{view_reason}:alternate_output_view"
                reason = view_reason
        last_reason = reason
    return False, last_reason


def _materialize_contract_outputs_for_standard_paths(
    *,
    execution_spec: Dict[str, Any],
    source_dir: Path,
    scratch_dir: Path,
) -> List[Path]:
    criteria = execution_spec.get("acceptance_criteria")
    if not isinstance(criteria, dict):
        return []
    expected = derive_expected_deliverables(criteria)
    if not expected:
        return []
    if not source_dir.exists() or not source_dir.is_dir():
        return []

    materialized: List[Path] = []
    scratch_root = scratch_dir.resolve()
    for raw_expected in expected:
        expected_text = str(raw_expected or "").strip().replace(chr(92), "/").strip("/")
        if not expected_text or any(token in expected_text for token in ("*", "?", "[")):
            continue
        expected_path = Path(expected_text)
        if expected_path.is_absolute():
            continue
        direct_source = source_dir / expected_path
        flat_source = source_dir / expected_path.name
        prefixed_source = _find_unique_run_prefixed_contract_source(source_dir, expected_path)
        source = direct_source if direct_source.exists() else flat_source
        if not source.exists() and prefixed_source is not None:
            source = prefixed_source
        if not source.exists() or not source.is_file():
            continue
        destination = (scratch_dir / expected_path).resolve()
        try:
            destination.relative_to(scratch_root)
        except ValueError:
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists() or destination.stat().st_size != source.stat().st_size:
                shutil.copy2(source, destination)
            materialized.append(destination)
        except OSError as exc:
            logger.debug("Failed to materialize qwen contract output %s from %s: %s", destination, source, exc)
    return materialized


def _find_unique_run_prefixed_contract_source(source_dir: Path, expected_path: Path) -> Optional[Path]:
    """Find a single run-prefixed file that corresponds to a contract filename.

    Some code-agent prompts encourage run-prefixed output filenames while the task
    contract requires a stable path such as ``artifacts/dl_hyperopt_results.json``.
    Treat only same-directory ``run_*_<expected name>`` files as repair candidates,
    and only when the match is unique. Ambiguous candidates must fail verification
    instead of being guessed.
    """

    expected_name = expected_path.name
    if not expected_name:
        return None

    parent = (source_dir / expected_path.parent).resolve()
    try:
        if not parent.exists() or not parent.is_dir():
            return None
    except OSError:
        return None

    suffix = f"_{expected_name}"
    matches: List[Path] = []
    try:
        for candidate in parent.iterdir():
            if not candidate.is_file():
                continue
            name = candidate.name
            if name.startswith("run_") and name.endswith(suffix):
                matches.append(candidate)
    except OSError:
        return None

    if len(matches) != 1:
        return None
    return matches[0]


def _materialize_contract_view_for_flat_outputs(
    *,
    execution_spec: Dict[str, Any],
    source_dir: Path,
    scratch_dir: Path,
) -> Optional[Path]:
    criteria = execution_spec.get("acceptance_criteria")
    if not isinstance(criteria, dict):
        return None
    expected = derive_expected_deliverables(criteria)
    if not expected:
        return None
    if not source_dir.exists() or not source_dir.is_dir():
        return None

    source_key = hashlib.md5(str(source_dir).encode("utf-8")).hexdigest()[:12]
    view_dir = scratch_dir / ".contract_views" / source_key
    materialized = False
    for raw_expected in expected:
        expected_text = str(raw_expected or "").strip().replace(chr(92), "/").strip("/")
        if not expected_text or any(token in expected_text for token in ("*", "?", "[")):
            continue
        expected_path = Path(expected_text)
        if expected_path.is_absolute() or len(expected_path.parts) < 2:
            continue
        direct_source = source_dir / expected_path
        flat_source = source_dir / expected_path.name
        prefixed_source = _find_unique_run_prefixed_contract_source(source_dir, expected_path)
        source = direct_source if direct_source.exists() else flat_source
        if not source.exists() and prefixed_source is not None:
            source = prefixed_source
        if not source.exists() or not source.is_file():
            continue
        destination = view_dir / expected_path
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists() or destination.stat().st_size != source.stat().st_size:
                shutil.copy2(source, destination)
            materialized = True
        except OSError as exc:
            logger.debug("Failed to materialize qwen contract view %s from %s: %s", destination, source, exc)
    return view_dir if materialized else None


def _qwen_single_work_dir_passes_contract_for_early_exit(
    *,
    execution_spec: Dict[str, Any],
    task_work_dir: Path,
) -> tuple[bool, str]:
    try:
        finalization = _ce()._verify_contract_for_qwen_early_exit(
            execution_spec,
            task_work_dir=task_work_dir,
        )
    except Exception as exc:
        return False, f"verification_error:{exc}"

    payload = getattr(finalization, "payload", None)
    metadata_obj = payload.get("metadata") if isinstance(payload, dict) else None
    metadata: Dict[str, Any] = metadata_obj if isinstance(metadata_obj, dict) else {}
    verification_obj = getattr(finalization, "verification", None)
    if isinstance(verification_obj, dict):
        verification: Dict[str, Any] = verification_obj
    else:
        metadata_verification = metadata.get("verification")
        verification = metadata_verification if isinstance(metadata_verification, dict) else {}

    final_status = str(getattr(finalization, "final_status", "") or "").strip().lower()
    verification_status = str(
        metadata.get("verification_status") or verification.get("status") or ""
    ).strip().lower()
    checks_total = verification.get("checks_total")
    checks_total_int = 0
    if checks_total is not None:
        try:
            checks_total_int = int(checks_total)
        except (TypeError, ValueError):
            checks_total_int = 0
    failures = verification.get("failures")

    if final_status == "failed" or verification_status == "failed":
        return False, "verification_failed"
    if final_status != "completed":
        return False, final_status or "verification_not_completed"
    if verification_status != "passed":
        return False, verification_status or "verification_not_passed"
    if checks_total_int <= 0:
        return False, "no_executed_checks"
    if isinstance(failures, list) and failures:
        return False, "verification_failed"
    if verification.get("llm_override") or metadata.get("verification_overridden_by_llm"):
        return False, "verification_llm_override"
    return True, "verification_passed"


def _extract_pending_qwen_function_call(transcript_text: str) -> Optional[Dict[str, Any]]:
    """Return the latest assistant function call without a matching tool result."""
    if not transcript_text or not transcript_text.strip():
        return None

    completed_call_ids: set[str] = set()
    pending_calls: list[Dict[str, Any]] = []

    for raw_line in transcript_text.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue

        payload_type = str(payload.get("type") or "").strip().lower()
        if payload_type == "tool_result":
            tool_call_result = payload.get("toolCallResult")
            if isinstance(tool_call_result, dict):
                call_id = str(tool_call_result.get("callId") or "").strip()
                if call_id:
                    completed_call_ids.add(call_id)
                    continue
            message = payload.get("message")
            if isinstance(message, dict):
                for part in message.get("parts") or []:
                    if not isinstance(part, dict):
                        continue
                    function_response = part.get("functionResponse")
                    if not isinstance(function_response, dict):
                        continue
                    call_id = str(function_response.get("id") or "").strip()
                    if call_id:
                        completed_call_ids.add(call_id)
                        break
            continue

        if payload_type != "assistant":
            continue

        message = payload.get("message")
        if not isinstance(message, dict):
            continue
        for part in message.get("parts") or []:
            if not isinstance(part, dict):
                continue
            function_call = part.get("functionCall")
            if not isinstance(function_call, dict):
                continue
            call_id = str(function_call.get("id") or "").strip()
            name = str(function_call.get("name") or "").strip()
            args = function_call.get("args")
            if not call_id or not name or not isinstance(args, dict):
                continue
            pending_calls.append(
                {
                    "id": call_id,
                    "name": name,
                    "args": json.loads(json.dumps(args, ensure_ascii=False)),
                }
            )

    for function_call in reversed(pending_calls):
        if function_call["id"] not in completed_call_ids:
            return function_call
    return None


def _extract_pending_qwen_shell_command(transcript_text: str) -> Optional[Dict[str, Any]]:
    pending_call = _extract_pending_qwen_function_call(transcript_text)
    if not isinstance(pending_call, dict):
        return None
    if str(pending_call.get("name") or "").strip() != "run_shell_command":
        return None

    args = pending_call.get("args")
    if not isinstance(args, dict):
        return None

    command = str(args.get("command") or "").strip()
    if not command:
        return None

    raw_timeout = args.get("timeout")
    timeout_ms = _QWEN_SHELL_FALLBACK_TIMEOUT_MS
    if raw_timeout is not None:
        try:
            timeout_ms = int(str(raw_timeout).strip())
        except (TypeError, ValueError):
            timeout_ms = _QWEN_SHELL_FALLBACK_TIMEOUT_MS
    timeout_ms = max(timeout_ms, _QWEN_SHELL_FALLBACK_TIMEOUT_MS)
    timeout_ms = min(timeout_ms, _QWEN_SHELL_FALLBACK_MAX_TIMEOUT_MS)

    return {
        "call_id": str(pending_call.get("id") or "").strip(),
        "command": command,
        "description": str(args.get("description") or "").strip(),
        "timeout_ms": timeout_ms,
    }


async def _run_subprocess_capture(
    command: Sequence[str],
    *,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout_s: Optional[float] = None,
) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        if timeout_s and timeout_s > 0:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_s,
            )
        else:
            stdout_bytes, stderr_bytes = await process.communicate()
        return_code = int(process.returncode or 0)
    except asyncio.TimeoutError:
        try:
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
        except Exception:
            stdout_bytes = b""
            stderr_bytes = b""
        timeout_note = f"\n[TIMEOUT] pending qwen shell call exceeded {int(timeout_s or 0)}s"
        stderr_text = stderr_bytes.decode("utf-8", errors="replace") + timeout_note
        return -1, stdout_bytes.decode("utf-8", errors="replace"), stderr_text

    return (
        return_code,
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
    )


async def _read_qwen_debug_log_text(
    *,
    debug_log_path: str,
    container_name: Optional[str] = None,
) -> str:
    path_text = str(debug_log_path or "").strip()
    if not path_text:
        return ""
    if container_name:
        command = (
            f"test -f {shlex.quote(path_text)} && "
            f"tail -c {_QWEN_FATAL_DEBUG_SCAN_BYTES} {shlex.quote(path_text)}"
        )
        return_code, stdout, _stderr = await _run_subprocess_capture(
            ["docker", "exec", container_name, "sh", "-lc", command],
            timeout_s=5.0,
        )
        if return_code == 0:
            return stdout
        return ""
    try:
        path = Path(path_text)
        if not path.is_file():
            return ""
        with path.open("rb") as handle:
            try:
                handle.seek(max(0, path.stat().st_size - _QWEN_FATAL_DEBUG_SCAN_BYTES))
            except OSError:
                pass
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


async def _detect_qwen_debug_fatal_failure(
    *,
    stderr_text: str,
    container_name: Optional[str] = None,
) -> Optional[str]:
    if _is_qwen_truncated_tool_failure_text(stderr_text):
        return _qwen_truncated_tool_failure_note("stderr")
    debug_log_path = _extract_qwen_debug_log_path(stderr_text)
    if not debug_log_path:
        return None
    debug_text = await _ce()._read_qwen_debug_log_text(
        debug_log_path=debug_log_path,
        container_name=container_name,
    )
    if _is_qwen_truncated_tool_failure_text(debug_text):
        return _qwen_truncated_tool_failure_note(debug_log_path)
    return None


async def _read_qwen_transcript_text(
    *,
    qwen_session_id: Optional[str],
    container_name: Optional[str] = None,
) -> str:
    session_token = str(qwen_session_id or "").strip()
    if not session_token:
        return ""

    if container_name:
        transcript_glob = f"*/chats/{session_token}.jsonl"
        shell_cmd = (
            f'path="$(find {_QWEN_TRANSCRIPTS_ROOT} -path {shlex.quote(transcript_glob)} -print -quit)" && '
            '[ -n "$path" ] && cat "$path"'
        )
        return_code, stdout, _stderr = await _run_subprocess_capture(
            ["docker", "exec", container_name, "sh", "-lc", shell_cmd],
            timeout_s=5.0,
        )
        if return_code == 0:
            return stdout
        return ""

    host_projects_root = Path.home() / ".qwen" / "projects"
    if not host_projects_root.exists():
        return ""
    for candidate in host_projects_root.glob(f"**/chats/{session_token}.jsonl"):
        try:
            return candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


async def _recover_pending_qwen_shell_call(
    *,
    qwen_session_id: Optional[str],
    container_name: Optional[str],
    task_work_dir: str,
) -> Optional[Dict[str, Any]]:
    transcript_text = await _ce()._read_qwen_transcript_text(
        qwen_session_id=qwen_session_id,
        container_name=container_name,
    )
    pending_shell = _extract_pending_qwen_shell_command(transcript_text)
    if not isinstance(pending_shell, dict):
        return None

    shell_command = str(pending_shell.get("command") or "").strip()
    if not shell_command:
        return None

    timeout_ms = int(pending_shell.get("timeout_ms") or _QWEN_SHELL_FALLBACK_TIMEOUT_MS)
    timeout_s = max(1.0, timeout_ms / 1000.0)

    if container_name:
        from app.services.terminal.docker_pty_backend import CONTAINER_EXEC_PATH

        command = [
            "docker",
            "exec",
            "-e",
            f"PATH={CONTAINER_EXEC_PATH}",
            "-w",
            str(task_work_dir),
            container_name,
            "/bin/bash",
            "-c",
            shell_command,
        ]
        return_code, stdout, stderr = await _run_subprocess_capture(
            command,
            timeout_s=timeout_s,
        )
    else:
        return_code, stdout, stderr = await _run_subprocess_capture(
            ["/bin/bash", "-c", shell_command],
            cwd=str(task_work_dir),
            timeout_s=timeout_s,
        )

    return {
        "exit_code": return_code,
        "stdout": stdout,
        "stderr": stderr,
        "command": shell_command,
        "timeout_ms": timeout_ms,
        "call_id": str(pending_shell.get("call_id") or "").strip(),
        "description": str(pending_shell.get("description") or "").strip(),
    }


def _build_cli_failure_error(
    *,
    return_code: Optional[int],
    stderr: str,
    stdout: str,
    backend_label: str = "Claude Code",
) -> str:
    parts: List[str] = []
    if return_code is not None:
        parts.append(f"exit_code={return_code}")
    stderr_excerpt = _extract_readable_error(stderr)
    if stderr_excerpt:
        parts.append(f"stderr={stderr_excerpt}")
    else:
        debug_log_path = _extract_qwen_debug_log_path(stderr)
        if debug_log_path:
            parts.append(f"debug_log={debug_log_path}")
    stdout_excerpt = _compact_cli_text(stdout, limit=220)
    if stdout_excerpt:
        parts.append(f"stdout={stdout_excerpt}")
    if not parts:
        return f"{backend_label} execution failed (success=false)."
    return f"{backend_label} execution failed: {'; '.join(parts)}"


_PARTIAL_COMPLETION_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"(?:processed|completed|finished|done)\s+(\d+)\s*(?:of|/)\s*(\d+)", re.IGNORECASE),
    re.compile(r"(\d+)\s*/\s*(\d+)\s+(?:cell\s*types?|samples?|items?|files?|tasks?)", re.IGNORECASE),
]

_WARNING_LINE_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"(?:^|\s)(?:warning|warn)\s*[:：]", re.IGNORECASE),
    re.compile(r"(?:^|\s)error\s*[:：]", re.IGNORECASE),
    re.compile(r"(?:^|\s)(?:skipping|skipped)\s", re.IGNORECASE),
    re.compile(r"(?:^|\s)failed\s+to\s+(?:process|load|read|write|open|parse)", re.IGNORECASE),
    re.compile(r"Traceback\s*\(most\s+recent\s+call\s+last\)", re.IGNORECASE),
]


def _detect_partial_completion_cli(
    stdout: str,
    stderr: str,
    produced_files: List[str],
    *,
    success: bool,
) -> Dict[str, Any]:
    """Scan execution output for signs of incomplete processing.

    Renamed from ``_detect_partial_completion`` during the C5 extraction (W1
    stage 2): this is the CLI-stdout layer detector, NOT to be confused with
    ``gating_probe._detect_partial_completion_in_tool_results`` (tool-result
    layer, different module and input). The ``code_executor`` facade keeps
    the historical name available as an alias, so the two detectors are never
    re-exported into the same namespace under one name.

    Returns a dict with:
      - ``warnings``: list of warning-like lines found in output
      - ``partial_completion_suspected``: True if patterns suggest partial work
      - ``partial_ratio``: e.g. "2/6" if a progress pattern was found
    """
    warnings: List[str] = []
    partial_ratio: Optional[str] = None
    partial_suspected = False

    combined = (stdout or "") + "\n" + (stderr or "")
    lines = [ln.strip() for ln in combined.splitlines() if ln.strip()]

    for line in lines:
        for pat in _WARNING_LINE_PATTERNS:
            if pat.search(line):
                compact = line[:200]
                if compact not in warnings:
                    warnings.append(compact)
                break

    if success:
        for pat in _PARTIAL_COMPLETION_PATTERNS:
            m = pat.search(combined)
            if m:
                done_count = int(m.group(1))
                total_count = int(m.group(2))
                if 0 < done_count < total_count:
                    partial_ratio = f"{done_count}/{total_count}"
                    partial_suspected = True
                    break

    if success and not produced_files:
        partial_suspected = True

    # Cap warnings to avoid payload bloat
    warnings = warnings[:20]

    result: Dict[str, Any] = {}
    if warnings:
        result["output_warnings"] = warnings
    if partial_suspected:
        result["partial_completion_suspected"] = True
    if partial_ratio:
        result["partial_ratio"] = partial_ratio
    return result


def _build_qwen_code_command(
    *,
    task: str,
    work_dir: str,
    file_prefix: str,
    output_format: str,
    allowed_tools: List[str],
    allowed_dirs: List[str],
    model: Optional[str],
    debug: bool,
    allowed_dirs_info: str,
    qwen_session_id: Optional[str] = None,
    task_subdirs: Sequence[str] = _DEFAULT_TASK_SUBDIRECTORIES,
    execution_spec: Optional[Dict[str, Any]] = None,
    resolved_resources: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[str]:
    """Build the ``qwen`` CLI command for non-interactive prompt execution."""
    task_text = _ce()._build_cli_task_contract(task, execution_spec, resolved_resources)
    writable_dirs = [name for name in task_subdirs if str(name).strip().lower() != "code"]
    try:
        from app.config.executor_config import get_executor_settings as _get_settings
        _settings = _get_settings()
        _max_turns = str(_settings.qc_max_session_turns)
        _shell_timeout_ms = max(
            1000,
            min(600000, int(getattr(_settings, "qc_shell_timeout_ms", 600000))),
        )
    except Exception:
        _max_turns = "50"
        _shell_timeout_ms = 600000
    enhanced_task = (
        f"[ATOMIC TASK]\n"
        f"Execute the task below as a single unit. Multi-step code execution "
        f"within the task (read data → process → save results → plot) is "
        f"expected and normal — do NOT report that as needing decomposition.\n"
        f"If upstream data or dependencies are missing, unreadable, or schema-"
        f"incompatible, report BLOCKED_DEPENDENCY so the orchestration layer "
        f"can re-run the upstream tasks first.\n"
        f"If an upstream artifact exists and is readable but contains zero rows "
        f"or no significant hits, treat that as a valid zero-result outcome for "
        f"downstream aggregation, visualization, and export tasks. Continue and "
        f"produce empty-but-valid outputs at the required paths (for example "
        f"empty tables, serialized empty objects, placeholder figures, and a "
        f"short summary that explicitly documents zero findings). Do NOT "
        f"fabricate positive signals.\n"
        f"Do NOT silently fabricate or fix upstream outputs yourself — unless "
        f"the task instruction explicitly authorizes you to produce them.\n"
        f"The plan/task contract is authoritative. Required deliverables must "
        f"be produced exactly at the specified paths/patterns. Extra outputs "
        f"are allowed, but they do NOT replace missing required outputs.\n"
        f"Only output BLOCKED_SCOPE if the request is fundamentally outside "
        f"the scope of code execution (e.g. 'plan the entire project' or "
        f"'manage my calendar').\n"
        f"If blocked by missing, unreadable, or schema-incompatible dependencies, output exactly:\n"
        f"  STATUS: BLOCKED_DEPENDENCY\n"
        f"  DETAIL: <which upstream task/data is missing>\n"
        f"If truly out of scope, output exactly:\n"
        f"  {_BLOCK_SCOPE_STATUS}\n"
        f"  {_BLOCK_SCOPE_REASON}\n"
        f"  DETAIL: <one sentence>\n\n"
        f"Workspace: {work_dir}\n"
        f"Output dirs: {_format_task_subdirectories(task_subdirs)}\n"
        f"File prefix: {file_prefix}\n"
        f"Task:\n{task_text}\n\n"
        f"Deliverables:\n"
        f"1. Write scripts under code/ only when needed.\n"
        f"2. Run them and save outputs under {_format_directory_choices(writable_dirs)}.\n"
        f"3. Put publishable deliverable code under results/submission/ "
        f"or results/deliverable/.\n"
        f"4. Return a summary of actual outputs produced.\n"
        f"5. Progress Reporting: When processing multiple items in a loop, "
        f"print progress after each item: print(f'Processed {{i+1}}/{{total}} items'). "
        f"Print final summary: print(f'Completed {{done}}/{{total}} items'). "
        f"Save results after each item, not only at the end.\n"
        f"6. When using the shell tool for installs, builds, tests, or other "
        f"one-shot commands that may exceed two minutes, set its `timeout` "
        f"parameter explicitly to {_shell_timeout_ms} milliseconds instead of "
        f"relying on the default 120000ms timeout.\n"
        f"7. Use background execution only for processes that are meant to "
        f"keep running (servers, watchers, daemons), not for one-shot installs "
        f"or analysis commands.\n"
        f"8. Do NOT modify shared host environments: no global `conda install`, "
        f"`pip install`, `npm install -g`, or writes into shared site-packages.\n"
        f"9. Inside qwen_code, do NOT create a new virtual environment with "
        f"`python -m venv`; lightweight Python dependencies should be installed "
        f"with `python3 -m pip install --user ...` or `python3 -m pip install "
        f"--target <workspace>/vendor ...` instead.\n"
        f"10. If a lightweight dependency is needed before running a script, "
        f"combine the install step and the main script execution in the same "
        f"`run_shell_command` call so the task finishes in one tool invocation.\n"
        f"11. If the dependency requires a heavy solver, compiled stack, or a "
        f"new runtime image/profile, stop and report BLOCKED_DEPENDENCY instead "
        f"of mutating the shared host environment.\n"
        f"12. Network proxy for package installation: If `pip install` fails due to "
        f"network issues (timeout, connection refused, SSL errors), use the SOCKS5 proxy:\n"
        f"    ```bash\n"
        f"    pip install --proxy socks5://119.147.24.196:10800 <package_name>\n"
        f"    ```\n"
        f"    This proxy is available inside the container and should be used for all "
        f"external package installations.\n"
        f"13. Use `pip install` directly for Python packages. Do NOT use `conda install` "
        f"or wait for conda environment setup — pip is faster and sufficient for most "
        f"dependencies. Only use conda if the package explicitly requires it (e.g., "
        f"complex C/C++ dependencies that pip cannot handle).\n\n"
        f"{_ce()._rerun_update_mode_prompt()}\n"
        f"{_ce()._final_response_contract_prompt()}"
        f"{allowed_dirs_info}"
    )
    cmd: List[str] = [
        "qwen",
        "-p", enhanced_task,
        "-o", output_format,
        "--max-session-turns", _max_turns,
        "--approval-mode", "yolo",
        "--auth-type", "openai",
    ]
    if qwen_session_id:
        cmd.extend(["--session-id", qwen_session_id])
    if model:
        cmd.extend(["-m", model])
    if debug:
        cmd.append("-d")
    # QC --allowed-tools takes space-separated array (not comma-joined).
    if allowed_tools:
        cmd.extend(["--allowed-tools"] + list(allowed_tools))
    for abs_path in allowed_dirs:
        cmd.extend(["--add-dir", abs_path])
    return cmd


def _build_qwen_container_mounts(
    *,
    task_work_dir: Path,
    session_dir: Path,
    allowed_dirs: Sequence[str],
) -> List[tuple[str, str]]:
    """Return bind mounts needed for containerized qwen access.

    Every directory exposed to qwen via ``--add-dir`` must also exist inside
    the container at the same absolute path. We mount the minimal covering set:
    skip directories already covered by ``task_work_dir`` or an earlier parent
    mount, while preserving same-path host/container mapping.
    """

    task_root = task_work_dir.resolve()
    candidates: List[Path] = []
    if session_dir.exists():
        candidates.append(session_dir.resolve())
    for raw_dir in allowed_dirs:
        token = str(raw_dir or "").strip()
        if not token:
            continue
        try:
            raw_path = Path(token).absolute()
            if not raw_path.exists() or not raw_path.is_dir():
                continue
            candidates.append(raw_path)
            resolved = raw_path.resolve()
            if resolved != raw_path:
                candidates.append(resolved)
        except OSError:
            continue

    ordered: List[Path] = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=lambda item: (len(item.parts), str(item))):
        candidate_str = str(candidate)
        if candidate_str in seen:
            continue
        if _ce()._is_path_within(candidate, task_root):
            continue
        # Do not let a parent repository mount hide a symlinked child directory.
        # PhageScope is commonly checked out as <repo>/phagescope -> /mnt/sdm/...
        # Mounting only <repo> leaves that symlink broken inside the container.
        is_symlink_mount = candidate.is_symlink()
        if (
            not is_symlink_mount
            and any(
                _ce()._is_path_within(candidate, mounted_parent)
                and not mounted_parent.is_symlink()
                for mounted_parent in ordered
            )
        ):
            continue
        ordered.append(candidate)
        seen.add(candidate_str)

    return [(str(path), str(path)) for path in ordered]
