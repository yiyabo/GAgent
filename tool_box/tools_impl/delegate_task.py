"""delegate_task tool: hand self-contained work to isolated sub-agents.

Two backends sit behind one surface (``DELEGATE_TASK_BACKEND``):

* ``native`` (default when enabled): in-process document-only sub-agents from
  ``app/services/plans/native_subagent.py`` — role-clamped local file /
  document / vision tools, scratch workspaces under the session, bounded
  parallel fan-out (``tasks=[...]``), cancellation and parent attribution.
* ``cli``: the S1 neutral contract of
  ``design/2026-09-25-subagent-delegation-plane.md`` §3 S2
  (``CodeAgentTaskDelegateExecutor`` with ``plan_id`` / ``task_id`` unset); a
  single goal only, no roles.

Distinct from ``execute_code`` (the orchestrator writing Python in its own
kernel): here the goal is handed off and only a summary comes back.

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
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from tool_box.context import ToolContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.plans.task_delegate_executor import TaskDelegationSpec

logger = logging.getLogger(__name__)

TOOL_NAME = "delegate_task"
ENV_ENABLED = "DELEGATE_TASK_ENABLED"
ENV_BACKEND = "DELEGATE_TASK_BACKEND"
MAX_PARALLEL_TASKS = 6
MAX_GOAL_CHARS = 8000  # mirrors app.services.plans.native_subagent.MAX_GOAL_CHARS
ROLES: tuple[str, ...] = ("scout", "researcher", "worker", "verifier")

# Hard caps on what may re-enter the parent context.
MAX_SUMMARY_CHARS = 4000
MAX_ARTIFACT_PATHS = 20
MAX_CONTEXT_PATHS = 20
MAX_TASK_NAME_CHARS = 80

# Mirrors the base definition in this module (intentional duplicate, locked by
# ``app/tests/tools/test_delegate_task_offer.py``).
DESCRIPTION = (
    "Hand one self-contained document workflow to an ISOLATED sub-agent and get "
    "back only a summary plus artifact paths. The sub-agent runs its own tool loop: "
    "it does not see this conversation, and its transcript never enters your context. "
    "Sub-agents work on local documents only: they list and read text files, PDFs and "
    "images in the session workspace and (role 'worker') write new files into their "
    "own scratch folder; they have no shell, code execution, web, database, sequence "
    "or bio tools. Use it for long, self-contained reading and writing you do not need "
    "to watch (summarising a folder of reports, cross-checking several documents, "
    "drafting a document from given inputs). "
    "Division of labor: delegate_task (this tool) is THE delegation surface — it "
    "hands a whole GOAL to an isolated sub-agent and shows only the result; "
    "execute_code is YOU writing Python that calls tools as functions in a kernel you "
    "keep using — the cheap default for anything one script can do and the lane for "
    "any computation. "
    "Do NOT use delegate_task when: one tool call or two already answers the question "
    "(call them directly); you must read or judge the intermediate results yourself; "
    "the goal needs code, network, sequence or database tools (sub-agents do not have "
    "them); or the goal depends on implicit context from this conversation that you "
    "cannot write down in goal. "
    "Parallel fan-out: pass tasks=[{goal, role?, deliverable?, context_paths?}, ...] "
    "(1-6 independent items) instead of goal to run several sub-agents concurrently "
    "(at most 4 at once by default); each child result comes back in subagent_runs, "
    "and one child failing never affects its siblings. "
    "Roles: 'scout' (read-only: list and read files), 'researcher' (read-only: read "
    "and profile documents), 'worker' (default: read the inputs and write new files "
    "into its scratch folder), 'verifier' (read-only checking of produced documents, "
    "figures and scanned pages, including vision). "
    "context_paths name the session-workspace files or directories a sub-agent may "
    "read; a missing or out-of-workspace entry refuses the whole call. "
    "Good: goal='Read the meeting notes under notes/ and write a one-page summary of "
    "the decisions and open questions to summary.md', deliverable='summary.md with "
    "Decisions and Open questions sections', context_paths=['notes']. "
    "Bad: goal='count the rows in data/submissions.csv' — file_operations answers that "
    "in one call, while delegating pays a full agent run for a one-line answer. "
    "With DELEGATE_TASK_BACKEND=cli a single goal goes to the legacy coding sub-agent "
    "instead and role/tasks are refused. "
    "Returns summary (hard-capped), artifact_paths, usage, trace_ref and, for native "
    "sub-agents, subagent_runs; raw sub-agent transcripts are never returned."
)

PARAMETERS_SCHEMA: dict[str, Any] = {
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
                "Optional inputs the sub-agent may READ. Native sub-agents: files or "
                "directories relative to the session workspace; every entry must exist inside "
                "it, otherwise the whole call is refused. CLI backend: directories (absolute or "
                "project-root-relative)."
            ),
        },
        "role": {
            "type": "string",
            "enum": list(ROLES),
            "description": (
                "Optional sub-agent role for single-goal mode (default 'worker'): "
                "scout | researcher | worker | verifier."
            ),
        },
        "tasks": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_PARALLEL_TASKS,
            "description": (
                "Parallel fan-out: independent sub-agent briefs run concurrently. Pass either goal or tasks, not both."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "role": {"type": "string", "enum": list(ROLES)},
                    "deliverable": {"type": "string"},
                    "context_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": MAX_CONTEXT_PATHS,
                    },
                },
                "required": ["goal"],
            },
        },
    },
}


def delegate_task_enabled() -> bool:
    """Whether the general delegation tool is registered and offered."""
    return os.environ.get(ENV_ENABLED, "").strip() == "1"


def delegate_task_backend() -> str:
    """``native`` (in-process DeepThinkAgent, default) or ``cli`` (legacy code_executor lane)."""
    value = os.environ.get(ENV_BACKEND, "").strip().lower()
    return "cli" if value == "cli" else "native"


def _payload(
    *,
    success: bool,
    status: str,
    summary: str,
    artifact_paths: list[str] | None = None,
    usage: Mapping[str, Any] | None = None,
    trace_ref: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """The convergent return contract; every exit path of the handler uses it."""
    payload: dict[str, Any] = {
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


def _context_text(tool_context: ToolContext | None, field: str) -> str:
    value = getattr(tool_context, field, None) if tool_context is not None else None
    return str(value or "").strip()


def _normalize_context_paths(raw: Any) -> list[str]:
    if isinstance(raw, str):
        candidates: list[Any] = [raw]
    elif isinstance(raw, (list, tuple)):
        candidates = list(raw)
    else:
        return []
    paths: list[str] = []
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
    context_paths: list[str],
    tool_context: ToolContext | None,
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
        summary = _STATUS_FALLBACK_SUMMARY.get(status, "Sub-agent delegation finished.")
    return _truncate(summary)


def _usage(raw_result: Mapping[str, Any], duration_ms: float) -> dict[str, Any]:
    """Token accounting from the code_executor payload + measured wall clock.

    ``duration_ms`` is the handler's own wall clock around the delegation: the
    qwen CLI ledger entry (``llm_usage_log.duration_ms``) is not carried in the
    code_executor payload, so it is measured here instead of read back.
    """
    usage: dict[str, Any] = {"duration_ms": round(float(duration_ms), 1)}
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


def _trace_ref(result: Any) -> dict[str, Any]:
    """Pointers back to the run; every path reported here is absolute."""
    raw_result = getattr(result, "raw_result", None)
    raw: Mapping[str, Any] = raw_result if isinstance(raw_result, Mapping) else {}
    metadata = getattr(result, "metadata", None)
    meta: Mapping[str, Any] = metadata if isinstance(metadata, Mapping) else {}

    trace: dict[str, Any] = {}
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


def _result_payload(result: Any, duration_ms: float) -> dict[str, Any]:
    status = str(getattr(result, "status", "") or "").strip().lower() or "failed"
    raw_result = getattr(result, "raw_result", None)
    raw: Mapping[str, Any] = raw_result if isinstance(raw_result, Mapping) else {}
    artifact_paths = [
        str(path).strip() for path in list(getattr(result, "artifact_paths", None) or []) if str(path).strip()
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


def _new_native_executor():
    """Late-bound so tests can patch the native executor in its home module."""
    from app.services.plans.native_subagent import NativeSubagentExecutor

    return NativeSubagentExecutor()


_TASK_KEYS = frozenset({"goal", "role", "deliverable", "context_paths"})


def _strict_item(label: str, entry: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Validate one native sub-agent request; fail closed on any malformed field."""
    unknown = sorted(str(key) for key in entry if key not in _TASK_KEYS)
    if unknown:
        return {}, f"{label} has unknown field(s): {', '.join(unknown)}."
    goal = entry.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        return {}, f"{label} requires a non-empty 'goal' string."
    if len(goal) > MAX_GOAL_CHARS:
        return {}, f"{label}.goal exceeds {MAX_GOAL_CHARS} characters."
    for key in ("role", "deliverable"):
        value = entry.get(key)
        if value is not None and not isinstance(value, str):
            return {}, f"{label}.{key} must be a string."
    raw_paths = entry.get("context_paths")
    if raw_paths is None:
        paths: list[str] = []
    elif not isinstance(raw_paths, (list, tuple)):
        return {}, f"{label}.context_paths must be a list of workspace-relative paths."
    elif len(raw_paths) > MAX_CONTEXT_PATHS:
        return {}, f"{label}.context_paths accepts at most {MAX_CONTEXT_PATHS} entries."
    elif any(not isinstance(item, str) or not item.strip() for item in raw_paths):
        return {}, f"{label}.context_paths entries must be non-empty strings."
    else:
        paths = list(dict.fromkeys(item.strip() for item in raw_paths))
    return {
        "goal": goal.strip(),
        "role": entry.get("role"),
        "deliverable": str(entry.get("deliverable") or "").strip(),
        "context_paths": paths,
    }, None


def _parse_tasks(raw: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Normalize the ``tasks`` fan-out list; returns (items, error message)."""
    if raw is None:
        return [], None
    if not isinstance(raw, (list, tuple)):
        return [], "'tasks' must be a list of {goal, role?, deliverable?, context_paths?} objects."
    if len(raw) > MAX_PARALLEL_TASKS:
        return [], f"'tasks' accepts at most {MAX_PARALLEL_TASKS} items (got {len(raw)})."
    items: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            return [], f"tasks[{index}] must be an object with a 'goal'."
        item, error = _strict_item(f"tasks[{index}]", entry)
        if error:
            return [], error
        items.append(item)
    return items, None


def _invalid(error: str, summary: str) -> dict[str, Any]:
    return _payload(success=False, status="failed", error=error, summary=summary)


def _parent_context(tool_context: ToolContext | None):
    from app.services.plans.native_subagent import ParentContext

    extra = getattr(tool_context, "extra", None)
    extra_map: Mapping[str, Any] = extra if isinstance(extra, Mapping) else {}
    chain_raw = extra_map.get("ancestor_chain")
    chain = [item for item in chain_raw if isinstance(item, int)] if isinstance(chain_raw, (list, tuple)) else []
    depth_raw = extra_map.get("delegation_depth")
    delegation_depth = int(depth_raw) if isinstance(depth_raw, int) and not isinstance(depth_raw, bool) else 0
    plan_id = getattr(tool_context, "plan_id", None)
    task_id = getattr(tool_context, "task_id", None)
    abort_event = getattr(tool_context, "abort_event", None)
    return ParentContext(
        session_id=_context_text(tool_context, "session_id") or None,
        owner_id=_context_text(tool_context, "owner_id") or None,
        plan_id=plan_id if isinstance(plan_id, int) else None,
        task_id=task_id if isinstance(task_id, int) else None,
        job_id=_context_text(tool_context, "job_id") or None,
        work_dir=_context_text(tool_context, "work_dir") or None,
        ancestor_chain=chain,
        delegation_depth=delegation_depth,
        on_progress=getattr(tool_context, "on_progress", None),
        parent_cancel=abort_event if isinstance(abort_event, asyncio.Event) else None,
    )


async def _native_dispatch(items: list[dict[str, Any]], tool_context: ToolContext | None) -> dict[str, Any]:
    from app.services.plans.native_subagent import (
        SubagentInputError,
        SubagentSpec,
        normalize_role,
    )

    specs: list[SubagentSpec] = []
    for index, item in enumerate(items):
        role = normalize_role(item.get("role"))
        if role is None:
            return _invalid(
                "invalid_role",
                f"tasks[{index}].role must be one of {', '.join(ROLES)} (got {item.get('role')!r}).",
            )
        specs.append(
            SubagentSpec(
                goal=item["goal"],
                role=role,
                deliverable=item["deliverable"],
                context_paths=list(item["context_paths"]),
            )
        )

    started_at = time.perf_counter()
    try:
        runs = await _new_native_executor().run_many(specs, _parent_context(tool_context))
    except SubagentInputError as exc:
        # Raised while validating inputs, before any child started.
        return _invalid("invalid_context_paths", _truncate(str(exc)))
    duration_ms = round((time.perf_counter() - started_at) * 1000.0, 1)
    completed = [run for run in runs if run.get("status") == "completed"]
    if len(runs) == 1:
        only = runs[0]
        summary = str(only.get("summary") or "")
    else:
        summary = "\n\n".join(
            f"[{index + 1}/{len(runs)} {run.get('role')} · {run.get('status')}] {run.get('summary') or ''}".strip()
            for index, run in enumerate(runs)
        )
    if completed and len(completed) == len(runs):
        status = "completed"
    elif completed:
        status = "partial"
    elif runs and all(run.get("status") == "cancelled" for run in runs):
        status = "cancelled"
    else:
        status = "failed"
    artifact_paths: list[str] = []
    for run in runs:
        for path in run.get("artifact_paths") or []:
            if path not in artifact_paths:
                artifact_paths.append(path)
    payload = _payload(
        success=bool(completed),
        status=status,
        summary=_truncate(summary or _STATUS_FALLBACK_SUMMARY.get(status, "Sub-agent delegation finished.")),
        artifact_paths=artifact_paths[:MAX_ARTIFACT_PATHS],
        usage={"duration_ms": duration_ms, "children": len(runs), "completed": len(completed)},
        trace_ref={"backend": "native", "subrun_ids": [run.get("subrun_id") for run in runs]},
        error=None if completed else "subagent_failed",
    )
    payload["subagent_runs"] = runs
    return payload


async def delegate_task_handler(
    goal: str | None = None,
    deliverable: str | None = None,
    context_paths: list[str] | None = None,
    role: str | None = None,
    tasks: list[dict[str, Any]] | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Delegate one (or several parallel) self-contained workflows; return only the bounded digest."""
    if not delegate_task_enabled():
        return _payload(
            success=False,
            status="disabled",
            error="delegate_task_disabled",
            summary=("delegate_task is disabled. Set DELEGATE_TASK_ENABLED=1 to enable sub-agent delegation."),
        )

    if goal is not None and not isinstance(goal, str):
        return _invalid("invalid_goal", "'goal' must be a string.")
    goal_text = str(goal or "").strip()
    items, tasks_error = _parse_tasks(tasks)
    if tasks_error:
        return _invalid("invalid_tasks", tasks_error)
    if goal_text and items:
        return _invalid(
            "goal_and_tasks",
            "delegate_task takes either a single 'goal' or a parallel 'tasks' list, not both.",
        )
    if not goal_text and not items:
        return _invalid(
            "empty_goal",
            "delegate_task requires a non-empty 'goal' string or a non-empty 'tasks' list.",
        )

    if delegate_task_backend() == "native":
        if not items:
            entry: dict[str, Any] = {"goal": goal}
            for key, value in (("role", role), ("deliverable", deliverable), ("context_paths", context_paths)):
                if value is not None:
                    entry[key] = value
            item, item_error = _strict_item("delegate_task", entry)
            if item_error:
                return _invalid("invalid_params", item_error)
            items = [item]
        try:
            return await _native_dispatch(items, tool_context)
        except Exception as exc:  # noqa: BLE001 - reported as a bounded failure
            logger.exception("delegate_task native dispatch raised: %s", exc)
            return _invalid(
                "delegate_task_error",
                _truncate(f"delegate_task failed before any sub-agent result: {type(exc).__name__}: {exc}"),
            )

    if items:
        return _invalid(
            "tasks_unsupported_backend",
            "Parallel 'tasks' fan-out requires DELEGATE_TASK_BACKEND=native.",
        )
    if role is not None and str(role).strip():
        # The CLI lane cannot enforce role tool clamps, so a role request (e.g. a
        # read-only 'scout') must never silently run as a full coding agent.
        return _invalid(
            "role_unsupported_backend",
            "Sub-agent roles require DELEGATE_TASK_BACKEND=native.",
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
    on_progress_loop = asyncio.get_running_loop() if on_progress is not None else None
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
                f"delegate_task failed before the sub-agent produced a result: {type(exc).__name__}: {exc}"
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
        "Read the meeting notes under notes/ and write a one-page summary of decisions and open questions",
        "Cross-check the three protocol drafts under drafts/ and list every inconsistency between them",
        "Verify that report.md cites every figure stored under figures/",
    ],
}
