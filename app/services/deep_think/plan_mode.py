"""Tool policy for the explicit, client-driven Plan mode.

Plan mode permits evidence gathering and persistent Plan-tree edits, but no
artifact writes, code execution, remote job submission, or Plan execution.
The offer filter reduces accidental calls; the dispatcher guard is the actual
security boundary and is deliberately conservative for unknown tools.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

# Tools whose normal operations are observational. Unknown tools are denied.
_READ_TOOLS = frozenset(
    {
        "web_search",
        "literature_pipeline",
        "document_reader",
        "vision_reader",
        "lightrag_query",
        "graph_rag",
        "sequence_fetch",
        "ncbi_search",
        "load_skill",
        "load_tool_schema",
    }
)

# These tools need an operation-level check because they mix reads and writes.
_SCOPED_TOOLS = frozenset(
    {
        "file_operations",
        "plan_operation",
        "phagescope_data",
        "phagescope",
        "phagescope_research",
        "delegate_task",
    }
)

_READ_FILE_OPERATIONS = frozenset({"read", "list", "exists", "info", "profile", "census"})
_PLAN_OPERATIONS = frozenset(
    {
        "create",
        "bind",
        "review",
        "optimize",
        "get",
        "todo_list",
        "update_task",
        "resolve_input",
        "ask_user",
    }
)
_READ_PHAGESCOPE_DATA_OPERATIONS = frozenset(
    {
        "overview",
        "datasets",
        "search_phages",
        "get_phage",
        "aggregate",
        "cohort_report",
        "search_proteins",
        "family_index_status",
        "family_members",
        "features",
        "structures",
    }
)
_READ_PHAGESCOPE_ACTIONS = frozenset(
    {
        "query",
        "ping",
        "input_check",
        "task_list",
        "task_detail",
        "task_log",
        "result",
    }
)
_READ_RESEARCH_ACTIONS = frozenset({"audit", "deep_profile", "research_plan"})


def _operation(params: Mapping[str, Any], *, key: str = "operation") -> str:
    return str(params.get(key) or "").strip().lower()


def _delegate_roles(params: Mapping[str, Any]) -> list[str]:
    """Every role a delegate_task call would spawn (blank role = default worker)."""
    tasks = params.get("tasks")
    entries: list[Any] = list(tasks) if isinstance(tasks, (list, tuple)) and tasks else [params]
    roles: list[str] = []
    for entry in entries:
        raw = entry.get("role") if isinstance(entry, Mapping) else None
        roles.append(str(raw or "").strip().lower() or "worker")
    return roles


def _delegate_backend_native() -> bool:
    from tool_box.tools_impl.delegate_task import delegate_task_backend

    return delegate_task_backend() == "native"


def plan_mode_tool_allowed(tool_name: str, params: Mapping[str, Any] | None = None) -> bool:
    """Return whether one exact call is safe in explicit Plan mode."""
    name = str(tool_name or "").strip()
    values = params or {}
    if name in _READ_TOOLS:
        return True
    if name == "file_operations":
        return _operation(values) in _READ_FILE_OPERATIONS
    if name == "plan_operation":
        return _operation(values) in _PLAN_OPERATIONS
    if name == "phagescope_data":
        if _operation(values) not in _READ_PHAGESCOPE_DATA_OPERATIONS:
            return False
        # A read query can still be asked to persist an artifact.
        return values.get("save_tsv") is not True and values.get("materialize_pdb") is not True
    if name == "phagescope":
        return _operation(values, key="action") in _READ_PHAGESCOPE_ACTIONS
    if name == "phagescope_research":
        return _operation(values, key="action") in _READ_RESEARCH_ACTIONS
    if name == "delegate_task":
        # Read-only parallel recon is part of planning; any other role executes work.
        # Only the native backend enforces the scout clamp, so the CLI lane stays closed.
        return _delegate_backend_native() and all(role == "scout" for role in _delegate_roles(values))
    return False


def plan_mode_tool_names(tool_names: Sequence[str]) -> list[str]:
    """Filter a model offer without changing order or introducing duplicates."""
    allowed = _READ_TOOLS | _SCOPED_TOOLS
    native_delegate = _delegate_backend_native()
    return [
        name
        for name in tool_names
        if name in allowed and (name != "delegate_task" or native_delegate)
    ]

def defer_plan_mode_execution(decision: Any) -> Any:
    """Remove execution authority while preserving the router's intent telemetry."""
    if not decision.plan_contract_enforced:
        return decision
    if not (
        decision.plan_execute_required
        or decision.full_plan_execution
        or decision.explicit_task_override
        or decision.intent_type == "execute_task"
    ):
        return decision
    reasons = list(decision.route_reason_codes)
    if "plan_mode_execution_deferred" not in reasons:
        reasons.append("plan_mode_execution_deferred")
    return replace(
        decision,
        intent_type="chat",
        explicit_task_override=False,
        full_plan_execution=False,
        plan_execute_required=False,
        plan_execute_after_create_required=False,
        route_reason_codes=reasons,
    )


def plan_mode_denial(tool_name: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Structured tool failure returned before any handler can run."""
    values = params or {}
    if tool_name == "delegate_task":
        blocked = sorted({role for role in _delegate_roles(values) if role != "scout"})
        if not _delegate_backend_native():
            reason = "the CLI delegation backend cannot enforce read-only scout sub-agents"
        else:
            reason = (
                f"delegate_task was asked to spawn role(s) {', '.join(blocked)} "
                "that can execute work or write artifacts"
            )
        return {
            "success": False,
            "executed": False,
            "error": "plan_mode_read_only",
            "tool": tool_name,
            "operation": ",".join(blocked) or None,
            "summary": (f"Plan mode only allows read-only scout sub-agents; {reason}."),
            "hint": (
                'Use role="scout" for every delegated task during planning, or approve the Plan '
                "and exit Plan mode to delegate execution work."
            ),
        }
    operation = _operation(
        values,
        key="action" if tool_name in {"phagescope", "phagescope_research"} else "operation",
    )
    return {
        "success": False,
        "executed": False,
        "error": "plan_mode_read_only",
        "tool": tool_name,
        "operation": operation or None,
        "summary": (
            f"Plan mode blocked '{tool_name}' because it can execute work or write artifacts. "
            "Read-only research and Plan-tree edits are allowed. Ask the user to approve the Plan "
            "and switch to execution mode before performing this operation."
        ),
        "hint": "Approve the Plan and exit Plan mode to execute this operation.",
    }
