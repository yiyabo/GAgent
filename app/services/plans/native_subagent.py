"""Native in-process sub-agent executor for ``delegate_task``.

Runs each delegated goal as an in-process async ``DeepThinkAgent`` bound to the
project LLM service (no external CLI dependency). Sub-agents do ordinary
document work only. Each child gets:

* a role-scoped, recursion-free allowlist of local document tools
  (``file_operations`` / ``document_reader``, plus ``vision_reader`` for the
  verifier) — never a shell, code execution, network, bio, plan or delegation
  tool;
* fail-closed parameter checks: unknown parameters, unknown operations,
  missing or non-string paths and escaping glob patterns are refused before a
  handler runs;
* an isolated scratch workspace (``<session>/subagents/<subrun_id>``) that is
  the only writable location; reads are confined to that workspace plus the
  inputs the parent selected, which are validated before any child starts;
* bounded concurrency (per call and process-wide), a bounded iteration budget,
  a wall-clock timeout and a cancel event bridged from the parent's cancel
  token, with a short grace period before a non-cooperating child is stopped;
* LLM usage attribution through ``set_usage_context(run_id=<parent>:sub_<id>,
  parent_run_id=<parent>)``. Children never create ``chat_runs`` rows, so the
  restart gate can never be wedged by a sub-agent.

An empty scratch workspace is removed when its child finishes; produced files
are kept and reported as ``artifact_paths``.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
import uuid
import weakref
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_ROLE = "worker"
ROLES: tuple[str, ...] = ("scout", "researcher", "worker", "verifier")
READ_ONLY_ROLES: frozenset[str] = frozenset({"scout", "researcher", "verifier"})
MAX_ANCESTOR_DEPTH = 2
DEFAULT_TIMEOUT_SECONDS = 600.0
ENV_MAX_ITERATIONS = "DELEGATE_TASK_MAX_ITERATIONS"
ENV_TIMEOUT_SECONDS = "DELEGATE_TASK_TIMEOUT_SECONDS"
ENV_MAX_CONCURRENCY = "DELEGATE_TASK_MAX_CONCURRENCY"
DEFAULT_MAX_CONCURRENCY = 4
MAX_CONCURRENCY_CAP = 6
MAX_SUMMARY_CHARS = 4000
MAX_ARTIFACT_PATHS = 20
MAX_GOAL_CHARS = 8000
MAX_WRITE_CHARS = 1_000_000
# How long a child may take to stop on its own after its cancel event is set
# (parent cancel or wall-clock timeout) before its task is cancelled outright.
CANCEL_GRACE_SECONDS = 5.0
_CANCEL_POLL_SECONDS = 0.2
SUBAGENTS_DIRNAME = "subagents"

# Tools a child may never see, whatever its role: they would recurse into
# another delegation, mutate the plan, submit parent deliverables, or reach an
# ambient shell / network / external bio service from the parent's process.
# The role allowlists below are the enforcement; this set documents (and the
# tests lock) that none of these can ever be added to a role by accident.
FORBIDDEN_CHILD_TOOLS: frozenset[str] = frozenset(
    {
        "delegate_task",
        "code_executor",
        "execute_code",
        "plan_operation",
        "deliverable_submit",
        "terminal_session",
        "web_search",
        "url_fetch",
        "graph_rag",
        "lightrag_query",
        "literature_pipeline",
        "phagescope",
        "phagescope_data",
        "phagescope_research",
        "sequence_fetch",
        "ncbi_search",
        "bio_tools",
        "paper_replication",
        "result_interpreter",
        "load_skill",
    }
)

# Child roles work on local documents only.
_READ_TOOLS = ("file_operations", "document_reader")
ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    "scout": _READ_TOOLS,
    "researcher": _READ_TOOLS,
    "worker": _READ_TOOLS,
    "verifier": _READ_TOOLS + ("vision_reader",),
}

_DEFAULT_MAX_ITERATIONS: dict[str, int] = {
    "scout": 10,
    "verifier": 10,
    "researcher": 14,
    "worker": 14,
}

# Per-role operation clamps: tool -> (action field, allowed values).
# Read-only roles may also profile/census: both only read and summarise files,
# and the DeepThinkAgent forces a directory ``profile`` before it accepts a final
# answer whose prompt names a directory, so refusing it would stall the child.
_READ_FILE_OPS = frozenset({"read", "list", "exists", "info", "profile", "census"})
_WRITE_FILE_OPS = frozenset({"write"})
_KNOWN_FILE_OPS = _READ_FILE_OPS | _WRITE_FILE_OPS
_DOCUMENT_READER_OPS = frozenset({"read_pdf", "read_image", "read_text", "read_any"})
_VISION_READER_OPS = frozenset(
    {"read_pdf", "read_image", "ocr_page", "read_equation_image", "describe_figure", "extract_table"}
)
_ROLE_CLAMPS: dict[str, dict[str, tuple[str, frozenset[str]]]] = {
    "scout": {"file_operations": ("operation", _READ_FILE_OPS)},
    "researcher": {"file_operations": ("operation", _READ_FILE_OPS)},
    "verifier": {"file_operations": ("operation", _READ_FILE_OPS)},
    "worker": {"file_operations": ("operation", _KNOWN_FILE_OPS)},
}
# Operation allowlists that hold for every role exposed to the tool.
_TOOL_OPERATIONS: dict[str, tuple[str, frozenset[str]]] = {
    "document_reader": ("operation", _DOCUMENT_READER_OPS),
    "vision_reader": ("operation", _VISION_READER_OPS),
}

# Exactly the parameters each child tool accepts; anything else is refused.
_TOOL_PARAMS: dict[str, frozenset[str]] = {
    "file_operations": frozenset({"operation", "path", "content", "destination", "pattern"}),
    "document_reader": frozenset({"operation", "file_path", "use_ocr"}),
    "vision_reader": frozenset(
        {
            "operation",
            "file_path",
            "image_path",
            "page_numbers",
            "page_number",
            "region",
            "question",
            "max_pages",
            "language",
        }
    ),
}
# Runtime plumbing the agent loop attaches to every call; never forwarded.
_INJECTED_PARAMS = frozenset({"tool_context"})

# Path-bearing parameters checked against the child's workspace boundary.
_READ_PATH_KEYS: dict[str, tuple[str, ...]] = {
    "document_reader": ("file_path",),
    "vision_reader": ("file_path", "image_path"),
}

ToolExecutorFn = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
ProgressFn = Callable[[dict[str, Any]], Awaitable[None]]


class SubagentInputError(ValueError):
    """A delegated input path that is missing or outside the parent workspace."""


def normalize_role(raw: Any) -> str | None:
    """Return the canonical role name, the default when blank, or None if unknown."""
    if raw is not None and not isinstance(raw, str):
        return None
    text = str(raw or "").strip().lower()
    if not text:
        return DEFAULT_ROLE
    return text if text in ROLES else None


def role_tools(role: str) -> list[str]:
    """Return the exact tools enforced at dispatch for this child role."""
    return list(ROLE_TOOLS.get(role, ()))


def configured_max_concurrency() -> int:
    """Process-wide cap on concurrently running children (env-tunable, bounded)."""
    raw = os.environ.get(ENV_MAX_CONCURRENCY, "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if 1 <= value <= MAX_CONCURRENCY_CAP:
            return value
        logger.warning("Ignoring invalid %s=%r (expected 1-%d)", ENV_MAX_CONCURRENCY, raw, MAX_CONCURRENCY_CAP)
    return DEFAULT_MAX_CONCURRENCY


_LOOP_LIMITERS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()


def _process_limiter() -> asyncio.Semaphore:
    """One semaphore per event loop bounds children across concurrent delegate calls."""
    loop = asyncio.get_running_loop()
    limiter = _LOOP_LIMITERS.get(loop)
    if limiter is None:
        limiter = asyncio.Semaphore(configured_max_concurrency())
        _LOOP_LIMITERS[loop] = limiter
    return limiter


def _refusal(error: str, summary: str) -> dict[str, Any]:
    return {"success": False, "executed": False, "error": error, "summary": summary}


def clamp_violation(role: str, tool_name: str, params: Mapping[str, Any]) -> dict[str, Any] | None:
    """A structured refusal when ``role`` may not run this tool/operation, else None.

    Deny by default: a tool outside the role's current surface, or an operation
    outside the role's allowlist, is refused; there is no permissive fallback.
    """
    if tool_name not in role_tools(role):
        return _refusal(
            "subagent_tool_not_allowed",
            f"Tool '{tool_name}' is not available to a '{role}' sub-agent.",
        )
    clamp = _ROLE_CLAMPS.get(role, {}).get(tool_name)
    if clamp is None:
        return None
    key, allowed = clamp
    value = str((params or {}).get(key) or "").strip().lower()
    if value in allowed:
        return None
    if allowed <= _READ_FILE_OPS:
        return _refusal(
            "subagent_read_only",
            f"A '{role}' sub-agent is read-only: {tool_name}.{key}='{value}' is not "
            f"allowed (allowed: {', '.join(sorted(allowed))}).",
        )
    return _refusal(
        "subagent_operation_not_allowed",
        f"{tool_name}.{key}='{value}' is not allowed for a '{role}' sub-agent (allowed: {', '.join(sorted(allowed))}).",
    )


def _invalid_params(tool_name: str, detail: str) -> dict[str, Any]:
    return _refusal("subagent_invalid_params", f"{tool_name}: {detail}")


def _escaping_pattern(pattern: str) -> bool:
    if not pattern:
        return False
    if pattern.startswith(("/", "\\", "~")) or PureWindowsPath(pattern).drive:
        return True
    parts = PurePosixPath(pattern.replace("\\", "/")).parts
    return ".." in parts


def validate_params(tool_name: str, params: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Fail closed on malformed child tool calls. Returns (refusal, cleaned params).

    The agent loop's injected ``tool_context`` is dropped (the executor builds
    the child's own context); every other unknown key is refused.
    """
    values = {key: value for key, value in dict(params or {}).items() if key not in _INJECTED_PARAMS}
    allowed = _TOOL_PARAMS.get(tool_name)
    if allowed is None:
        return _refusal("subagent_tool_not_allowed", f"Tool '{tool_name}' is not available."), values
    unknown = sorted(str(key) for key in values if key not in allowed)
    if unknown:
        return _invalid_params(tool_name, f"unknown parameter(s): {', '.join(unknown)}."), values
    operation = values.get("operation")
    if not isinstance(operation, str) or not operation.strip():
        return _invalid_params(tool_name, "'operation' must be a non-empty string."), values
    tool_ops = _TOOL_OPERATIONS.get(tool_name)
    if tool_ops is not None and operation.strip().lower() not in tool_ops[1]:
        return (
            _invalid_params(
                tool_name, f"operation '{operation}' is not allowed (allowed: {', '.join(sorted(tool_ops[1]))})."
            ),
            values,
        )
    path_keys = ("path", "destination") if tool_name == "file_operations" else _READ_PATH_KEYS.get(tool_name, ())
    for key in path_keys:
        if key in values and (not isinstance(values[key], str) or not values[key].strip()):
            return _invalid_params(tool_name, f"'{key}' must be a non-empty string."), values
        if key in values and tool_name != "file_operations" and any(ch in values[key] for ch in "*?["):
            # Reader tools expand wildcards themselves, which would bypass the per-path check.
            return _invalid_params(tool_name, f"'{key}' must name one file, not a wildcard pattern."), values
    required = ("path",) if tool_name == "file_operations" else ()
    if tool_name == "document_reader":
        required = ("file_path",)
    for key in required:
        if key not in values:
            return _invalid_params(tool_name, f"'{key}' is required."), values
    if tool_name == "vision_reader" and not (values.get("file_path") or values.get("image_path")):
        return _invalid_params(tool_name, "'file_path' is required."), values
    if tool_name == "file_operations":
        pattern = values.get("pattern")
        if pattern is not None and (not isinstance(pattern, str) or _escaping_pattern(pattern.strip())):
            return _invalid_params(tool_name, "'pattern' must be a relative glob without '..' segments."), values
        content = values.get("content")
        if content is not None and not isinstance(content, str):
            return _invalid_params(tool_name, "'content' must be a string."), values
        if isinstance(content, str) and len(content) > MAX_WRITE_CHARS:
            return _invalid_params(tool_name, f"'content' exceeds {MAX_WRITE_CHARS} characters."), values
        if operation.strip().lower() == "write" and content is None:
            return _invalid_params(tool_name, "'content' is required for write."), values
    return None, values


def _within(path: Path, roots: list[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def enforce_workspace(
    tool_name: str,
    params: Mapping[str, Any],
    *,
    write_root: Path,
    read_roots: list[Path],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Confine path parameters to the child's workspace.

    Relative paths resolve against ``write_root`` (the child's scratch dir) and
    are rewritten to absolute paths; symlinks are resolved before the check.
    Reads may target ``read_roots``; anything that creates, changes or deletes
    a file must stay inside ``write_root``. Returns (refusal, params).
    """
    values = dict(params or {})
    write_roots = [write_root.resolve()]
    readable = write_roots + [root.resolve() for root in read_roots]

    def _resolve(raw: Any) -> Path | None:
        text = str(raw or "").strip()
        if not text:
            return None
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = write_root / candidate
        return candidate.resolve()

    def _check(key: str, roots: list[Path], mode: str) -> dict[str, Any] | None:
        resolved = _resolve(values.get(key))
        if resolved is None:
            return None
        if not _within(resolved, roots):
            return _refusal(
                "subagent_path_outside_workspace",
                f"{tool_name}.{key}='{values.get(key)}' is outside the sub-agent's {mode} "
                "workspace; stay inside the scratch directory or the readable input paths.",
            )
        values[key] = str(resolved)
        return None

    checks: list[tuple[str, list[Path], str]] = []
    if tool_name == "file_operations":
        operation = str(values.get("operation") or "").strip().lower()
        if operation in _WRITE_FILE_OPS:
            checks.append(("path", write_roots, "writable"))
        else:
            checks.append(("path", readable, "readable"))
        if operation in ("copy", "move") or values.get("destination"):
            checks.append(("destination", write_roots, "writable"))
    else:
        checks.extend((key, readable, "readable") for key in _READ_PATH_KEYS.get(tool_name, ()))
    for key, roots, mode in checks:
        refusal = _check(key, roots, mode)
        if refusal is not None:
            return refusal, values
    return None, values


def _positive_env(name: str, parse: Callable[[str], float]) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = parse(raw)
    except ValueError:
        value = 0.0
    if math.isfinite(value) and value > 0:
        return value
    logger.warning("Ignoring invalid %s=%r", name, raw)
    return None


def _max_iterations(role: str) -> int:
    value = _positive_env(ENV_MAX_ITERATIONS, int)
    return int(value) if value is not None else _DEFAULT_MAX_ITERATIONS[role]


def _timeout_seconds() -> float:
    value = _positive_env(ENV_TIMEOUT_SECONDS, float)
    return value if value is not None else DEFAULT_TIMEOUT_SECONDS


def _truncate(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    if len(text) <= limit:
        return text
    marker = f"…[truncated, {len(text)} chars total]"
    return text[: max(0, limit - len(marker))].rstrip() + marker


def _iterations_of(outcome: Any) -> int:
    value = getattr(outcome, "total_iterations", 0)
    return value if isinstance(value, int) else 0


@dataclass
class SubagentSpec:
    goal: str
    role: str = DEFAULT_ROLE
    deliverable: str = ""
    context_paths: list[str] = field(default_factory=list)


@dataclass
class ParentContext:
    """What the child inherits from the delegating tool call."""

    session_id: str | None = None
    owner_id: str | None = None
    plan_id: int | None = None
    task_id: int | None = None
    job_id: str | None = None
    work_dir: str | None = None
    ancestor_chain: list[int] = field(default_factory=list)
    on_progress: ProgressFn | None = None
    parent_cancel: asyncio.Event | None = None


def resolve_parent_run_id(parent: ParentContext) -> str:
    """Chat run claim, then the current job id, then the plan task identity."""
    try:
        from app.services.chat_run_state import chat_run_claim

        claim = chat_run_claim.get()
    except Exception:  # noqa: BLE001 - attribution must never break delegation
        claim = None
    if claim and claim[0]:
        return str(claim[0])
    if parent.job_id:
        return str(parent.job_id)
    if parent.plan_id is not None and parent.task_id is not None:
        return f"plan_{parent.plan_id}_task_{parent.task_id}"
    return f"session_{parent.session_id or 'none'}"


def parent_workspace_root(parent: ParentContext) -> Path:
    """The trusted parent workspace: the runtime session dir, else an absolute work dir."""
    if parent.session_id:
        from app.services.session_paths import get_runtime_session_dir

        return Path(get_runtime_session_dir(parent.session_id)).resolve()
    if not parent.work_dir or not Path(parent.work_dir).is_absolute():
        raise SubagentInputError("Delegation requires a session or a trusted absolute work directory.")
    return Path(parent.work_dir).resolve()


def subagent_workspace(parent: ParentContext, subrun_id: str) -> Path:
    container = parent_workspace_root(parent) / SUBAGENTS_DIRNAME
    if container.is_symlink():
        raise ValueError("Invalid sub-agent workspace")
    container.mkdir(mode=0o700, parents=True, exist_ok=True)
    sub_dir = container / subrun_id
    sub_dir.mkdir(mode=0o700)
    return sub_dir


def resolve_inputs(paths: list[str], base: Path) -> list[Path]:
    """Model-selected inputs may only narrow the trusted parent workspace.

    Every entry must exist, resolve (after symlinks) inside ``base`` and stay
    out of the sub-agent workspaces; the workspace root itself is too broad.
    """
    base = base.resolve()
    roots: list[Path] = []
    for raw in paths:
        text = str(raw or "").strip()
        if not text:
            raise SubagentInputError("Delegated input paths must be non-empty strings.")
        try:
            candidate = (base / Path(text).expanduser()).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SubagentInputError(f"Delegated input '{text}' does not exist.") from exc
        if not candidate.is_relative_to(base) or candidate.is_relative_to(base / SUBAGENTS_DIRNAME):
            raise SubagentInputError(f"Delegated input '{text}' is outside the parent input workspace.")
        if candidate == base:
            raise SubagentInputError("Choose a specific input file or directory, not the whole workspace.")
        if candidate not in roots:
            roots.append(candidate)
    return roots


def build_child_prompt(spec: SubagentSpec, sub_dir: Path, read_roots: list[Path]) -> str:
    lines = [f"ROLE: {spec.role}", "", "GOAL:", spec.goal]
    if spec.deliverable:
        lines.extend(["", "EXPECTED DELIVERABLE:", spec.deliverable])
    if read_roots:
        lines.extend(["", "READABLE INPUTS (read-only):"])
        lines.extend(f"- {path}" for path in read_roots)
    lines.extend(["", f"Your scratch working directory is: {sub_dir}"])
    if spec.role in READ_ONLY_ROLES:
        lines.append(
            "You are READ-ONLY: you cannot create, modify or delete files. Report your findings in your final answer."
        )
    else:
        lines.append(
            "Write every file you produce inside that directory and report the absolute path of "
            "each one with a short statement of what it contains."
        )
    lines.append(
        "You only have local document tools; you can read nothing outside the inputs above and your scratch directory."
    )
    return "\n".join(lines)


def _collect_artifacts(sub_dir: Path) -> list[str]:
    paths: list[str] = []
    root = sub_dir.resolve()
    try:
        for item in sorted(sub_dir.rglob("*")):
            if item.is_file() and not item.is_symlink() and item.resolve().is_relative_to(root):
                paths.append(str(item))
            if len(paths) >= MAX_ARTIFACT_PATHS:
                break
    except OSError:
        return paths
    return paths


def _remove_if_empty(sub_dir: Path) -> bool:
    """Remove a workspace that holds no files; returns True when it is gone."""
    try:
        if any(not item.is_dir() or item.is_symlink() for item in sub_dir.rglob("*")):
            return False
        for item in sorted(sub_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            item.rmdir()
        sub_dir.rmdir()
        return True
    except OSError:
        logger.debug("could not remove empty sub-agent workspace %s", sub_dir, exc_info=True)
        return not sub_dir.exists()


def _default_agent_factory(**kwargs: Any) -> Any:
    from app.services.deep_think_agent import DeepThinkAgent
    from app.services.llm.llm_service import get_llm_service

    return DeepThinkAgent(llm_client=get_llm_service(), **kwargs)


async def _default_tool_executor(tool_name: str, params: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run a child tool call and hand the agent the handler's own result.

    ``UnifiedToolExecutor`` wraps every result as ``{success, result, summary}``
    and the agent's dispatch wraps it once more, which buries fields such as
    ``operation`` too deep for the agent's evidence gates (e.g. the directory
    profile gate would never see its own forced profile and loop). Returning
    the handler payload keeps one wrapper level, as in the chat lane.
    """
    from app.services.execution.tool_executor import UnifiedToolExecutor

    run_tool = UnifiedToolExecutor().execute
    response = await run_tool(tool_name, params, context=context)
    inner = response.get("result") if isinstance(response, dict) else None
    if not isinstance(inner, dict):
        return response
    unwrapped = dict(inner)
    unwrapped["success"] = response.get("success") is not False
    for key in ("summary", "error"):
        if response.get(key) and not unwrapped.get(key):
            unwrapped[key] = response[key]
    return unwrapped


def _parent_cancelled(parent: ParentContext) -> bool:
    from app.services.cancellation import current_cancel_token

    token = current_cancel_token()
    if token is not None and token.cancelled:
        return True
    return parent.parent_cancel is not None and parent.parent_cancel.is_set()


async def _stop_task(task: asyncio.Future[Any]) -> None:
    """Cancel ``task`` if still running and wait until it has really finished.

    Only the stopped task's own outcome is absorbed (marked retrieved); a
    cancellation of the *calling* task still propagates.
    """
    if not task.done():
        task.cancel()
        await asyncio.wait({task})
    if not task.cancelled():
        task.exception()


class NativeSubagentExecutor:
    """Execute one or many sub-agent specs in-process."""

    def __init__(
        self,
        *,
        agent_factory: Callable[..., Any] | None = None,
        tool_executor: Callable[[str, dict[str, Any], Any], Awaitable[dict[str, Any]]] | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self._agent_factory = agent_factory or _default_agent_factory
        self._tool_executor = tool_executor or _default_tool_executor
        self._max_concurrency = max(1, max_concurrency or configured_max_concurrency())

    def validate_inputs(self, specs: list[SubagentSpec], parent: ParentContext) -> list[list[Path]]:
        """Resolve every child's inputs up front; raises ``SubagentInputError`` (nothing started)."""
        if any(spec.context_paths for spec in specs) or not parent.session_id:
            base = parent_workspace_root(parent)
            return [resolve_inputs(spec.context_paths, base) for spec in specs]
        return [[] for _ in specs]

    async def run_many(self, specs: list[SubagentSpec], parent: ParentContext) -> list[dict[str, Any]]:
        inputs = self.validate_inputs(specs, parent)
        semaphore = asyncio.Semaphore(self._max_concurrency)
        limiter = _process_limiter()

        async def _bounded(spec: SubagentSpec, read_roots: list[Path]) -> dict[str, Any]:
            async with semaphore, limiter:
                return await self._run(spec, parent, read_roots)

        tasks = [asyncio.ensure_future(_bounded(spec, roots)) for spec, roots in zip(specs, inputs, strict=True)]
        try:
            return list(await asyncio.gather(*tasks))
        finally:
            for task in tasks:
                await _stop_task(task)

    async def run_one(self, spec: SubagentSpec, parent: ParentContext) -> dict[str, Any]:
        [result] = await self.run_many([spec], parent)
        return result

    async def _run(self, spec: SubagentSpec, parent: ParentContext, read_roots: list[Path]) -> dict[str, Any]:
        subrun_id = uuid.uuid4().hex[:12]
        parent_run_id = resolve_parent_run_id(parent)
        started = time.perf_counter()
        result: dict[str, Any] = {
            "subrun_id": subrun_id,
            "child_run_id": f"{parent_run_id}:sub_{subrun_id}",
            "parent_run_id": parent_run_id,
            "role": spec.role,
            "goal": _truncate(spec.goal, 400),
            "deliverable": _truncate(spec.deliverable, 400),
            "status": "failed",
            "iterations": 0,
            "summary": "",
            "artifact_paths": [],
            "workspace": None,
            "duration_ms": 0.0,
            "error": None,
        }

        def _finish() -> dict[str, Any]:
            result["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
            return result

        if len(parent.ancestor_chain) >= MAX_ANCESTOR_DEPTH:
            result["error"] = "subagent_recursion_limit"
            result["summary"] = "Refused: sub-agent delegation depth limit reached."
            return _finish()
        if spec.role not in ROLES:
            result["error"] = "invalid_role"
            result["summary"] = f"Unknown sub-agent role '{spec.role}'."
            return _finish()
        if not spec.goal.strip() or len(spec.goal) > MAX_GOAL_CHARS:
            result["error"] = "invalid_goal"
            result["summary"] = f"A sub-agent goal must be 1-{MAX_GOAL_CHARS} characters."
            return _finish()
        if _parent_cancelled(parent):
            result["status"] = "cancelled"
            result["error"] = "cancelled"
            result["summary"] = "Sub-agent was cancelled before it started."
            return _finish()

        await self._emit(parent, result, "started")
        sub_dir: Path | None = None
        try:
            sub_dir = subagent_workspace(parent, subrun_id)
            result.update(await self._execute(spec, parent, sub_dir, read_roots, result["child_run_id"]))
        except asyncio.CancelledError:
            result["status"] = "cancelled"
            result["error"] = "cancelled"
            result["summary"] = "Sub-agent was cancelled."
            self._settle_workspace(result, sub_dir)
            await self._emit(parent, result, "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - isolated per-child failure
            logger.exception("native sub-agent %s failed", subrun_id)
            result["status"] = "failed"
            result["error"] = "subagent_error"
            result["summary"] = _truncate(f"Sub-agent failed: {type(exc).__name__}: {exc}")
        self._settle_workspace(result, sub_dir)
        await self._emit(parent, result, str(result["status"]))
        return _finish()

    @staticmethod
    def _settle_workspace(result: dict[str, Any], sub_dir: Path | None) -> None:
        if sub_dir is None:
            return
        artifacts = _collect_artifacts(sub_dir)
        result["artifact_paths"] = artifacts
        result["workspace"] = None if not artifacts and _remove_if_empty(sub_dir) else str(sub_dir)

    async def _emit(self, parent: ParentContext, result: Mapping[str, Any], status: str) -> None:
        """Report into the parent's delegation progress channel (``stage``/``message``)."""
        if parent.on_progress is None:
            return
        role = str(result.get("role") or "")
        message = f"Sub-agent {role} {status}"
        if status != "started" and result.get("error"):
            message = f"{message} ({result['error']})"
        payload = {
            "stage": status,
            "message": message,
            "detail": _truncate(str(result.get("goal") or ""), 160),
            "subagent_id": result.get("subrun_id"),
            "child_run_id": result.get("child_run_id"),
            "parent_run_id": result.get("parent_run_id"),
            "role": role,
            "status": status,
        }
        try:
            await parent.on_progress(payload)
        except Exception:  # noqa: BLE001 - progress is best effort
            logger.debug("sub-agent progress callback failed", exc_info=True)

    async def _execute(
        self,
        spec: SubagentSpec,
        parent: ParentContext,
        sub_dir: Path,
        read_roots: list[Path],
        child_run_id: str,
    ) -> dict[str, Any]:
        from app.llm import (
            clear_usage_context,
            current_usage_context,
            set_usage_context,
        )
        from app.services.execution.tool_executor import ToolExecutionContext

        parent_run_id = child_run_id.rsplit(":sub_", 1)[0]
        child_chain = list(parent.ancestor_chain) + [len(parent.ancestor_chain) + 1]
        exec_context = ToolExecutionContext(
            plan_id=parent.plan_id,
            task_id=parent.task_id,
            task_name=f"subagent:{spec.role}",
            task_instruction=spec.goal,
            session_id=parent.session_id,
            ancestor_chain=child_chain,
            owner_id=parent.owner_id,
            current_job_id=parent.job_id,
            work_dir=str(sub_dir),
            channel="subagent",
            mode="delegated_task_execution",
        )
        role = spec.role

        async def _tool_wrapper(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
            refusal = clamp_violation(role, tool_name, params or {})
            if refusal is not None:
                return refusal
            refusal, cleaned = validate_params(tool_name, params or {})
            if refusal is not None:
                return refusal
            refusal, checked = enforce_workspace(tool_name, cleaned, write_root=sub_dir, read_roots=read_roots)
            if refusal is not None:
                return refusal
            return await self._tool_executor(tool_name, checked, exec_context)

        cancel_event = asyncio.Event()

        async def _bridge_cancel() -> None:
            while not cancel_event.is_set():
                if _parent_cancelled(parent):
                    cancel_event.set()
                    return
                await asyncio.sleep(_CANCEL_POLL_SECONDS)

        agent = self._agent_factory(
            available_tools=role_tools(role),
            tool_executor=_tool_wrapper,
            max_iterations=_max_iterations(role),
            cancel_event=cancel_event,
        )
        inherited = current_usage_context() or {}
        token = set_usage_context(
            session_id=parent.session_id,
            plan_id=parent.plan_id,
            task_id=parent.task_id,
            run_id=child_run_id,
            parent_run_id=parent_run_id,
            phase="subagent",
            call_purpose=f"subagent_{role}",
            billing_lane=inherited.get("billing_lane") if isinstance(inherited, Mapping) else None,
        )
        bridge = asyncio.ensure_future(_bridge_cancel())
        think = asyncio.ensure_future(
            agent.think(
                build_child_prompt(spec, sub_dir, read_roots),
                context={
                    "subagent_role": role,
                    "work_dir": str(sub_dir),
                    "readable_dirs": [str(p) for p in read_roots],
                },
            )
        )
        stop_reason: str | None = None
        try:
            cancel_wait = asyncio.ensure_future(cancel_event.wait())
            try:
                await asyncio.wait(
                    {think, cancel_wait}, timeout=_timeout_seconds(), return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                await _stop_task(cancel_wait)
            if not think.done():
                stop_reason = "cancelled" if cancel_event.is_set() else "subagent_timeout"
                cancel_event.set()
                await asyncio.wait({think}, timeout=CANCEL_GRACE_SECONDS)
                await _stop_task(think)
        finally:
            await _stop_task(think)
            await _stop_task(bridge)
            clear_usage_context(token)

        outcome = None if think.cancelled() else think.result()  # re-raises a child failure
        if stop_reason == "subagent_timeout":
            return {
                "status": "failed",
                "iterations": _iterations_of(outcome),
                "summary": f"Sub-agent timed out after {_timeout_seconds():g}s.",
                "error": "subagent_timeout",
            }
        termination = str(getattr(outcome, "termination_reason", "") or "")
        cancelled = stop_reason == "cancelled" or cancel_event.is_set() or termination == "cancelled"
        answer = str(getattr(outcome, "final_answer", "") or "").strip()
        status = "cancelled" if cancelled else ("completed" if answer else "failed")
        if status == "cancelled":
            summary = "Sub-agent was cancelled." + (f" Partial answer: {answer}" if answer else "")
        else:
            summary = answer or f"Sub-agent finished without an answer ({termination or 'unknown'})."
        return {
            "status": status,
            "iterations": _iterations_of(outcome),
            "summary": _truncate(summary),
            "error": None if status == "completed" else ("cancelled" if cancelled else (termination or status)),
        }
