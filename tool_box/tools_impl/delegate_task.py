"""delegate_task tool: hand one self-contained workflow to an isolated sub-agent.

The general delegation surface of ``design/2026-09-25-subagent-delegation-plane.md``
§3 S2, built on the S1 neutral contract (``CodeAgentTaskDelegateExecutor`` with
``plan_id`` / ``task_id`` unset). Distinct from ``code_executor`` (a CODING task
for the pi harness) and ``execute_code`` (the orchestrator writing Python in its
own kernel): here the goal is handed off and only a summary comes back.

Env-gated (``DELEGATE_TASK_ENABLED=1``, default OFF). The offer-side gates live
in ``tool_box/tool_registry.py`` (not registered at all),
``app/services/tool_schemas.py`` (native schema) and
``app/routers/chat/request_routing.py`` (the chat pool); the handler also
refuses with a structured failure instead of raising.

The return payload is deliberately convergent: the sub-agent's trace (CLI
stdout/stderr, transcripts) never travels back into the parent context. Only
``summary`` (hard-capped), ``artifact_paths`` (capped), ``usage`` and
``trace_ref`` come back; everything else stays on disk behind ``trace_ref``.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import time
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional

from tool_box.context import ToolContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.plans.task_delegate_executor import TaskDelegationSpec

logger = logging.getLogger(__name__)

TOOL_NAME = "delegate_task"
ENV_ENABLED = "DELEGATE_TASK_ENABLED"

# Hard caps on what may re-enter the parent context.
MAX_SUMMARY_CHARS = 4000
MAX_ARTIFACT_PATHS = 20
MAX_CONTEXT_PATHS = 20
MAX_TASK_NAME_CHARS = 80

# Mirrors the base definition in this module (intentional duplicate, locked by
# ``app/tests/tools/test_delegate_task_offer.py``).
DESCRIPTION = (
    "Hand one self-contained, long-horizon workflow to an ISOLATED sub-agent and "
    "get back only a summary plus artifact paths. The sub-agent runs in its own "
    "session with its own tool loop: it does not see this conversation, and its "
    "transcript never enters your context. Use it when the work is long, "
    "self-contained, and you do not need to watch the intermediate steps "
    "(multi-file refactors, audit-and-repair passes, bulk literature or accession "
    "sweeps, 'take this dataset and produce X end to end'). "
    "Division of labor: delegate_task (this tool) hands off a whole GOAL and shows "
    "only the result; code_executor hands off a CODING task to the pi coding "
    "harness (it writes and debugs the code) and returns its execution result; "
    "execute_code is YOU writing Python that calls tools as functions in a kernel "
    "you keep using. "
    "Do NOT use delegate_task when: one tool call or two already answers the "
    "question (call them directly); you must read or judge the intermediate "
    "results yourself (use the tools, or execute_code when you need fan-out); you "
    "need to reuse the current kernel's state (use execute_code); the goal is "
    "'write code that does X' and you want the code back (use code_executor); the "
    "goal is read-only checking, counting/printing, or verifying results you "
    "already have (that takes seconds here and a whole agent run there); or the "
    "goal depends on implicit context from this conversation that you cannot write "
    "down in goal. Calls run one at a time — there is no parallel fan-out. "
    "Good: goal='Audit every Python file under data/pipeline for calls to the "
    "removed pandas.append API, fix them, and report the changed files', "
    "deliverable='patched files + a markdown report listing every change', "
    "context_paths=['data/pipeline']. "
    "Bad: goal='count the rows in data/submissions.csv' — file_operations or "
    "result_interpreter answers that in one call, while delegating pays a full "
    "agent run for a one-line answer. "
    "Returns summary (hard-capped), artifact_paths, usage, and trace_ref; raw CLI "
    "stdout/stderr is never returned, so inspect the run through trace_ref "
    "(run id plus absolute log paths) when you really need the detail."
)

PARAMETERS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal": {
            "type": "string",
            "description": (
                "What must be accomplished, written as a self-contained brief: the "
                "sub-agent cannot see this conversation, so name the inputs, the "
                "expected outcome, and any constraint that matters."
            ),
        },
        "deliverable": {
            "type": "string",
            "description": (
                "Optional: the shape of the expected result and how you will accept "
                "it (files, formats, must-cover points). Free text; it is passed to "
                "the sub-agent verbatim."
            ),
        },
        "context_paths": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": MAX_CONTEXT_PATHS,
            "description": (
                "Optional directories the sub-agent may READ (absolute or "
                "project-root-relative). Pass directories, not single files; "
                "non-directory or non-existent entries are ignored by the runtime."
            ),
        },
    },
    "required": ["goal"],
}


def delegate_task_enabled() -> bool:
    """Whether the general delegation tool is registered and offered."""
    return os.environ.get(ENV_ENABLED, "").strip() == "1"


def _payload(
    *,
    success: bool,
    status: str,
    summary: str,
    artifact_paths: Optional[List[str]] = None,
    usage: Optional[Mapping[str, Any]] = None,
    trace_ref: Optional[Mapping[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """The convergent return contract; every exit path of the handler uses it."""
    payload: Dict[str, Any] = {
        "tool": TOOL_NAME,
        "success": bool(success),
        "status": status,
        "summary": summary,
        "artifact_paths": list(artifact_paths or []),
        "usage": dict(usage or {}),
        "trace_ref": dict(trace_ref or {}),
    }
    if error:
        payload["error"] = error
    return payload


def _context_text(tool_context: Optional[ToolContext], field: str) -> str:
    value = getattr(tool_context, field, None) if tool_context is not None else None
    return str(value or "").strip()


def _normalize_context_paths(raw: Any) -> List[str]:
    if isinstance(raw, str):
        candidates: List[Any] = [raw]
    elif isinstance(raw, (list, tuple)):
        candidates = list(raw)
    else:
        return []
    paths: List[str] = []
    for item in candidates:
        text = str(item or "").strip()
        if text and text not in paths:
            paths.append(text)
        if len(paths) >= MAX_CONTEXT_PATHS:
            break
    return paths


def _task_name(goal: str) -> str:
    first_line = (goal.splitlines() or [goal])[0]
    collapsed = " ".join(first_line.split()).strip()
    if not collapsed:
        collapsed = "Delegated task"
    if len(collapsed) > MAX_TASK_NAME_CHARS:
        return collapsed[: MAX_TASK_NAME_CHARS - 1].rstrip() + "…"
    return collapsed


def _build_prompt(goal: str, deliverable: str) -> str:
    lines = ["GOAL:", goal]
    if deliverable:
        lines.extend(["", "EXPECTED DELIVERABLE:", deliverable])
    lines.extend(
        [
            "",
            "Write every file you produce inside your working directory, and report the "
            "absolute path of each one together with a short statement of what it contains.",
        ]
    )
    return "\n".join(lines)


def _build_spec(
    *,
    goal: str,
    deliverable: str,
    context_paths: List[str],
    tool_context: Optional[ToolContext],
) -> TaskDelegationSpec:
    from app.services.plans.task_delegate_executor import TaskDelegationSpec

    return TaskDelegationSpec(
        task_name=_task_name(goal),
        task_instruction=goal,
        task_prompt=_build_prompt(goal, deliverable),
        # Empty backend: ``code_executor`` then resolves its own lane
        # (CODE_EXECUTION_BACKEND / auto routing), i.e. exactly the lane a chat
        # code_executor call would take. UnifiedToolExecutor drops the empty
        # string instead of using it as a "plan_task_delegation" override.
        executor_backend="",
        plan_id=None,
        task_id=None,
        session_id=_context_text(tool_context, "session_id") or None,
        owner_id=_context_text(tool_context, "owner_id") or None,
        current_job_id=_context_text(tool_context, "job_id") or None,
        work_dir=_context_text(tool_context, "work_dir") or None,
        readable_dirs=list(context_paths),
    )


def _truncate(text: str) -> str:
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    marker = f"…[truncated, {len(text)} chars total]"
    keep = max(0, MAX_SUMMARY_CHARS - len(marker))
    return text[:keep].rstrip() + marker


def _summary_is_raw_stream_text(summary: str, stdout: str, stderr: str) -> bool:
    """True when ``summary`` is verbatim CLI output rather than a result.

    ``CodeAgentTaskDelegateExecutor._summarize_result`` falls back to the
    payload's ``stdout`` prefix when a run produced no result text; that text
    must not reach the parent, so such a summary is replaced by a status line.
    """
    if len(summary) < 20:
        return False
    for stream in (stdout, stderr):
        text = str(stream or "").strip()
        if text and text.startswith(summary):
            return True
    return False


_STATUS_FALLBACK_SUMMARY = {
    "completed": "Sub-agent delegation completed.",
    "blocked": "Sub-agent delegation is blocked: a required input was missing (see trace_ref).",
    "failed": "Sub-agent delegation failed.",
    "cancelled": "Sub-agent delegation was cancelled by user request.",
}


def _safe_summary(result: Any, status: str) -> str:
    summary = str(getattr(result, "summary", "") or "").strip()
    if summary and _summary_is_raw_stream_text(
        summary,
        str(getattr(result, "stdout", "") or ""),
        str(getattr(result, "stderr", "") or ""),
    ):
        logger.info(
            "delegate_task dropped a raw-stream summary (status=%s, chars=%d)",
            status,
            len(summary),
        )
        summary = ""
    if not summary:
        summary = _STATUS_FALLBACK_SUMMARY.get(
            status, "Sub-agent delegation finished."
        )
    return _truncate(summary)


def _usage(raw_result: Mapping[str, Any], duration_ms: float) -> Dict[str, Any]:
    """Token accounting from the code_executor payload + measured wall clock.

    ``duration_ms`` is the handler's own wall clock around the delegation: the
    qwen CLI ledger entry (``llm_usage_log.duration_ms``) is not carried in the
    code_executor payload, so it is measured here instead of read back.
    """
    usage: Dict[str, Any] = {"duration_ms": round(float(duration_ms), 1)}
    cli_usage = raw_result.get("cli_usage")
    if isinstance(cli_usage, Mapping):
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = cli_usage.get(key)
            if isinstance(value, (int, float)):
                usage[key] = int(value)
        for key in ("model", "provider"):
            value = str(cli_usage.get(key) or "").strip()
            if value:
                usage[key] = value
    return usage


def _trace_ref(result: Any) -> Dict[str, Any]:
    """Pointers back to the run; every path reported here is absolute."""
    raw_result = getattr(result, "raw_result", None)
    raw: Mapping[str, Any] = raw_result if isinstance(raw_result, Mapping) else {}
    metadata = getattr(result, "metadata", None)
    meta: Mapping[str, Any] = metadata if isinstance(metadata, Mapping) else {}

    trace: Dict[str, Any] = {}
    run_id = str(getattr(result, "executor_session_id", "") or raw.get("run_id") or "").strip()
    if run_id:
        trace["run_id"] = run_id
    for key in (
        "execution_backend",
        "run_directory",
        "task_directory_full",
        "session_directory",
        "log_path",
        "debug_log_path",
    ):
        value = raw.get(key) if raw.get(key) is not None else meta.get(key)
        text = str(value or "").strip()
        if text:
            trace[key] = text
    trace["path_base"] = "absolute"
    return trace


def _result_payload(result: Any, duration_ms: float) -> Dict[str, Any]:
    status = str(getattr(result, "status", "") or "").strip().lower() or "failed"
    raw_result = getattr(result, "raw_result", None)
    raw: Mapping[str, Any] = raw_result if isinstance(raw_result, Mapping) else {}
    artifact_paths = [
        str(path).strip()
        for path in list(getattr(result, "artifact_paths", None) or [])
        if str(path).strip()
    ]
    return _payload(
        success=status == "completed",
        status=status,
        summary=_safe_summary(result, status),
        artifact_paths=artifact_paths[:MAX_ARTIFACT_PATHS],
        usage=_usage(raw, duration_ms),
        trace_ref=_trace_ref(result),
    )


def _new_executor():
    """Late-bound so tests can patch the executor in its home module."""
    from app.services.plans.task_delegate_executor import CodeAgentTaskDelegateExecutor

    return CodeAgentTaskDelegateExecutor()


async def delegate_task_handler(
    goal: str,
    deliverable: Optional[str] = None,
    context_paths: Optional[List[str]] = None,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Delegate one self-contained workflow and return only the bounded digest."""
    if not delegate_task_enabled():
        return _payload(
            success=False,
            status="disabled",
            error="delegate_task_disabled",
            summary=(
                "delegate_task is disabled. Set DELEGATE_TASK_ENABLED=1 to enable "
                "sub-agent delegation."
            ),
        )

    goal_text = str(goal or "").strip()
    if not goal_text:
        return _payload(
            success=False,
            status="failed",
            error="empty_goal",
            summary="delegate_task requires a non-empty 'goal' string.",
        )

    spec = _build_spec(
        goal=goal_text,
        deliverable=str(deliverable or "").strip(),
        context_paths=_normalize_context_paths(context_paths),
        tool_context=tool_context,
    )

    started_at = time.perf_counter()
    # The parent's activity-stream channel. The delegation is driven from a
    # worker thread, so the callback and the loop that owns it must travel
    # together: the CLI lane posts progress back onto this loop.
    on_progress = getattr(tool_context, "on_progress", None)
    on_progress_loop = (
        asyncio.get_running_loop() if on_progress is not None else None
    )
    try:
        # The delegation is synchronous (and can run for hours), so it must not
        # block the event loop; the worker thread has no running loop, which is
        # the shape the plan domain already drives it in.
        result = await asyncio.to_thread(
            functools.partial(
                _new_executor().execute,
                spec,
                on_progress=on_progress,
                on_progress_loop=on_progress_loop,
            )
        )
    except Exception as exc:  # noqa: BLE001 - reported as a bounded failure
        duration_ms = (time.perf_counter() - started_at) * 1000.0
        logger.exception("delegate_task delegation raised: %s", exc)
        return _payload(
            success=False,
            status="failed",
            error="delegate_task_error",
            summary=_truncate(
                "delegate_task failed before the sub-agent produced a result: "
                f"{type(exc).__name__}: {exc}"
            ),
            usage={"duration_ms": round(duration_ms, 1)},
        )
    return _result_payload(result, (time.perf_counter() - started_at) * 1000.0)


delegate_task_tool = {
    "name": TOOL_NAME,
    "description": DESCRIPTION,
    "category": "execution",
    "parameters_schema": PARAMETERS_SCHEMA,
    "handler": delegate_task_handler,
    "tags": ["delegation", "subagent", "execution", "workflow", "long_horizon"],
    "examples": [
        "Audit every Python file under data/pipeline for the removed pandas.append API, fix the occurrences, and report the changed files",
        "Scan the literature for phage lysins active against Pseudomonas aeruginosa and save an evidence table",
        "Take the raw sequencing directory and produce a QC report plus a cleaned FASTA set",
    ],
}
