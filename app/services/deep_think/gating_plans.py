"""Plan operation events, structured plan contract checks and dataset
profile gates for the DeepThink agent (god-class split, behaviour
zero-change).

Each function here is the body of the like-named DeepThinkAgent method with
`self` renamed to `agent` (`cls` kept); the class keeps thin wrappers with
the same decorators. Display-family helpers (detect_reasoning_language,
_localized_text) stay in deep_think_agent and are reached through the
late-bound `_dta()` so their monkeypatch surface is unchanged.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from app.services.deep_think.models import ThinkingStep
from app.services.response_style import sanitize_professional_response_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.deep_think_agent import DeepThinkAgent

logger = logging.getLogger(__name__)


def _dta() -> Any:
    """Late-bound deep_think_agent module (monkeypatch-friendly lookups)."""
    from app.services import deep_think_agent

    return deep_think_agent


_DIRECTORY_DATASET_REQUEST_RE = re.compile(
    r"\b(?:analy[sz]e|inspect|audit|summari[sz]e|review|look|profile|census|folder|directory|dataset|data\s+folder|output\s+folder)\b|分析|查看|检查|审计|总结|目录|文件夹|数据集",
    re.IGNORECASE,
)
_PHAGESCOPE_DATASET_REQUEST_RE = re.compile(
    r"\b(?:phagescope|phage[-\s]?host|host\s+prediction|data\s+splitting|model\s+selection|benchmarking|biological\s+validation)\b|噬菌体|宿主预测|数据划分|模型选择|生物学验证",
    re.IGNORECASE,
)
_PHAGESCOPE_ANALYSIS_ACTION_RE = re.compile(
    r"\b(?:analy[sz]e|inspect|audit|summari[sz]e|review|look|profile|explore|start|dataset|data)\b|分析|查看|看看|检查|审计|总结|探索|数据集|数据",
    re.IGNORECASE,
)
# Generic spreadsheet/tabular files (Excel/CSV/Parquet) under a "phagescope"
# directory are NOT PhageScope datasets. ``.tsv``/``.txt`` are deliberately
# excluded here because those ARE real PhageScope metadata formats.
_NON_PHAGESCOPE_TABULAR_FILE_EXTS = (
    ".xlsx",
    ".xls",
    ".xlsm",
    ".csv",
    ".parquet",
    ".feather",
)
# Match real absolute filesystem paths without treating the slash inside
# relative output paths such as ``results/figures/目录`` as ``/figures/目录``.
_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.-])/(?:[^\s`\"'<>，。；;])+")


def _collect_plan_operation_events(cls: Any, steps: List[ThinkingStep]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for step in steps:
        for entry in cls._extract_tool_payloads_from_step(step):
            if str(entry.get("tool") or "").strip().lower() != "plan_operation":
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            rp = cls._unwrap_tool_result(payload)
            success = bool(rp.get("success", payload.get("success")))
            operation = str(rp.get("operation") or "").strip().lower()
            plan_id = cls._coerce_positive_int(rp.get("plan_id"))
            plan_title = str(rp.get("plan_title") or rp.get("title") or "").strip()
            error = str(rp.get("error") or "").strip()
            if not error:
                error = str(
                    payload.get("error")
                    or payload.get("summary")
                    or (rp.get("message") if not success else "")
                    or ""
                ).strip()
            applied_changes = rp.get("applied_changes")
            failed_changes = rp.get("failed_changes")
            try:
                applied_changes = int(applied_changes) if applied_changes is not None else None
            except (TypeError, ValueError):
                applied_changes = None
            try:
                failed_changes = int(failed_changes) if failed_changes is not None else None
            except (TypeError, ValueError):
                failed_changes = None
            events.append(
                {
                    "success": success,
                    "operation": operation or None,
                    "plan_id": plan_id,
                    "plan_title": plan_title or None,
                    "applied_changes": applied_changes,
                    "failed_changes": failed_changes,
                    "error": error or None,
                    "already_bound_plan_reused": bool(
                        rp.get("already_bound_plan_reused")
                        or payload.get("already_bound_plan_reused")
                    ),
                }
            )
    return events


def _summarize_structured_plan_outcome(
    agent: "DeepThinkAgent",
    steps: List[ThinkingStep],
    *,
    user_query: str = "",
) -> Dict[str, Any]:
    # Enforce explicit plan lifecycle contracts. The LLM still decides how
    # to decompose and what evidence to gather, but once routing identifies
    # create/review/optimize/execute intent, prose-only answers are not
    # allowed to masquerade as real plan operations.
    plan_id = agent._current_plan_id()
    plan_title = agent._current_plan_title()
    flags = agent._plan_contract_flags()
    route_reasons = agent.request_profile.get("route_reason_codes")
    if not isinstance(route_reasons, list):
        route_reasons = []

    events = agent._collect_plan_operation_events(steps)

    if flags["conflict_requires_confirmation"]:
        return {
            "required": True,
            "mode": "plan_conflict_confirmation",
            "called": bool(events),
            "satisfied": False,
            "state": "confirmation_required",
            "message": agent._build_plan_conflict_confirmation_message(),
            "plan_id": plan_id,
            "plan_title": plan_title,
            "operation": None,
        }

    required_ops: List[str] = []
    if flags["create_required"]:
        required_ops.append("create")
    if flags["execute_required"]:
        required_ops.append("execute_all")
    if flags["review_required"]:
        required_ops.append("review")
    if flags["optimize_required"]:
        required_ops.append("optimize")

    if required_ops:
        def _successful_event(op_name: str) -> Optional[Dict[str, Any]]:
            for event in events:
                if not event.get("success"):
                    continue
                if event.get("operation") != op_name:
                    continue
                if op_name == "create" and event.get("already_bound_plan_reused"):
                    continue
                if op_name == "optimize" and event.get("applied_changes") == 0:
                    continue
                return event
            return None

        create_event = _successful_event("create") if flags["create_required"] else None
        execute_event = _successful_event("execute_all") if flags["execute_required"] else None
        review_event = _successful_event("review") if flags["review_required"] else None
        optimize_event = _successful_event("optimize") if flags["optimize_required"] else None

        satisfied_ops: List[str] = []
        missing_ops: List[str] = []
        if flags["create_required"]:
            (satisfied_ops if create_event else missing_ops).append("create")
        if flags["execute_required"]:
            execute_matches_created = True
            if flags["execute_after_create_required"] and create_event and execute_event:
                created_id = create_event.get("plan_id")
                executed_id = execute_event.get("plan_id")
                execute_matches_created = bool(created_id and executed_id == created_id)
            if execute_event and execute_matches_created:
                satisfied_ops.append("execute_all")
            else:
                missing_ops.append("execute_all")
        if flags["review_required"]:
            (satisfied_ops if review_event else missing_ops).append("review")
        if flags["optimize_required"]:
            (satisfied_ops if optimize_event else missing_ops).append("optimize")

        satisfied = not missing_ops
        last_op = events[-1].get("operation") if events else None
        outcome_plan_id = None
        for event in reversed(events):
            if event.get("plan_id") is not None:
                outcome_plan_id = event.get("plan_id")
                break
        if outcome_plan_id is None:
            outcome_plan_id = plan_id
        outcome_plan_title = plan_title
        for event in reversed(events):
            if event.get("plan_title"):
                outcome_plan_title = event.get("plan_title")
                break
        message = None
        if not satisfied:
            if events:
                missing_text = ", ".join(missing_ops)
                message = (
                    "The requested structured plan contract was not satisfied: "
                    f"missing successful plan_operation operation(s): {missing_text}."
                )
            else:
                missing_text = ", ".join(missing_ops)
                message = (
                    "The requested structured plan contract was not satisfied: "
                    "plan_operation was not called. "
                    f"Required operation(s): {missing_text}."
                )
        return {
            "required": True,
            "mode": "plan_lifecycle",
            "called": bool(events),
            "satisfied": satisfied,
            "state": "satisfied" if satisfied else ("called_but_incomplete" if events else "not_called"),
            "message": message,
            "plan_id": outcome_plan_id,
            "plan_title": outcome_plan_title,
            "operation": last_op,
            "required_operations": required_ops,
            "satisfied_operations": satisfied_ops,
            "missing_operations": missing_ops,
        }

    is_bound_plan_mutation_request = (
        plan_id is not None
        and any(
            code in route_reasons
            for code in ("plan_review", "plan_optimize")
        )
    )

    if not is_bound_plan_mutation_request:
        return {
            "required": False,
            "mode": None,
            "called": False,
            "satisfied": False,
            "state": None,
            "message": None,
            "plan_id": plan_id,
            "plan_title": plan_title,
            "operation": None,
        }

    # Check if plan_operation was actually called with a mutation operation.
    # Exclude "create" — when a plan is already bound, the tool wrapper
    # rewrites create into a no-op already_bound_plan_reused result that
    # does not actually modify the plan.
    mutation_ops = {"review", "optimize", "update"}
    called = bool(events)
    satisfied = any(
        e.get("success")
        and e.get("operation") in mutation_ops
        and not e.get("already_bound_plan_reused")
        for e in events
    )
    last_op = events[-1].get("operation") if events else None

    return {
        "required": True,
        "mode": "bound_plan_mutation",
        "called": called,
        "satisfied": satisfied,
        "state": "satisfied" if satisfied else ("called_but_failed" if called else "not_called"),
        "message": None if satisfied else (
            "plan_operation was called but did not succeed"
            if called
            else "plan_operation was not called; the user requested a plan mutation"
        ),
        "plan_id": plan_id,
        "plan_title": plan_title,
        "operation": last_op,
    }


def _extract_successful_created_plan_from_tool_results(
    cls: Any,
    tool_results: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    for item in tool_results:
        if str(item.get("tool_name") or "").strip().lower() != "plan_operation":
            continue
        payload = item.get("tool_result")
        if not isinstance(payload, dict):
            continue
        inner = cls._unwrap_tool_result(payload)
        success = bool(inner.get("success", payload.get("success")))
        operation = str(inner.get("operation") or payload.get("operation") or "").strip().lower()
        if not success or operation != "create":
            continue
        plan_id = cls._coerce_positive_int(inner.get("plan_id") or payload.get("plan_id"))
        if plan_id is None:
            continue
        plan_title = str(
            inner.get("plan_title")
            or inner.get("title")
            or payload.get("plan_title")
            or payload.get("title")
            or ""
        ).strip()
        return {
            "plan_id": plan_id,
            "plan_title": plan_title or None,
            "already_bound_plan_reused": bool(
                inner.get("already_bound_plan_reused")
                or payload.get("already_bound_plan_reused")
            ),
        }
    return None


def _ensure_structured_plan_notice(
    agent: "DeepThinkAgent",
    answer: str,
    *,
    outcome: Dict[str, Any],
    user_query: str,
) -> str:
    text = str(answer or "").strip()
    if not outcome.get("required") or outcome.get("satisfied"):
        return text
    notice = sanitize_professional_response_text(str(outcome.get("message") or "").strip())
    if not notice:
        notice = _dta()._localized_text(
            _dta().detect_reasoning_language(user_query or text),
            "本轮未创建或更新结构化计划。",
            "A structured plan was not created or updated in this run.",
        )
    if not text:
        return notice
    if text.startswith(notice):
        return text
    return f"{notice}\n\n{text}"


def _build_structured_plan_contract_failure_answer(
    agent: "DeepThinkAgent",
    *,
    outcome: Dict[str, Any],
    user_query: str,
) -> str:
    if str(outcome.get("state") or "") == "confirmation_required":
        return agent._build_plan_conflict_confirmation_message()
    message = sanitize_professional_response_text(str(outcome.get("message") or "").strip())
    if not message:
        missing = outcome.get("missing_operations")
        if isinstance(missing, list) and missing:
            message = (
                "The requested structured plan contract was not satisfied: missing successful "
                f"plan_operation operation(s): {', '.join(str(item) for item in missing)}."
            )
        else:
            message = "The requested structured plan contract was not satisfied."
    required = outcome.get("required_operations")
    if isinstance(required, list) and required:
        return (
            f"{message}\n\n"
            f"Required operation(s): {', '.join(str(item) for item in required)}. "
            "I cannot treat file probes, terminal checks, or ordinary markdown text as a completed structured plan operation."
        )
    return message


def _directory_dataset_analysis_requested(user_query: str) -> bool:
    text = str(user_query or "").strip()
    if not text:
        return False
    return bool(_DIRECTORY_DATASET_REQUEST_RE.search(text) and _ABSOLUTE_PATH_RE.search(text))


def _extract_directory_path_from_query(user_query: str) -> Optional[str]:
    matches = [match.group(0).rstrip(".,;:)]}>") for match in _ABSOLUTE_PATH_RE.finditer(str(user_query or ""))]
    if not matches:
        return None
    return max(matches, key=len)


def _phagescope_dataset_analysis_requested(cls: Any, user_query: str) -> bool:
    text = str(user_query or "").strip()
    if not text:
        return False
    path = cls._extract_directory_path_from_query(text) or ""
    path_mentions_phagescope = "phagescope" in path.lower()
    if not path_mentions_phagescope:
        return False
    if cls._path_is_generic_tabular_file(path):
        return False
    # A real on-disk directory without a meta_data/ child is NOT a PhageScope
    # dataset (e.g. .../phagescope/test holding a clinical xlsx). deep_profile
    # would fail with "Missing meta_data directory", so demote here and let the
    # generic tool chain (code_executor) handle it.
    if cls._directory_positively_lacks_phagescope_meta_data(path):
        return False
    return bool(
        _PHAGESCOPE_DATASET_REQUEST_RE.search(text)
        or _PHAGESCOPE_ANALYSIS_ACTION_RE.search(text)
    )


def _path_is_generic_tabular_file(path: str) -> bool:
    text = str(path or "").strip().strip("`'\"").rstrip(".,;:)]}>，。；：）】》").lower()
    return text.endswith(_NON_PHAGESCOPE_TABULAR_FILE_EXTS)


def _directory_positively_lacks_phagescope_meta_data(path: str) -> bool:
    text = str(path or "").strip().strip("`'\"").rstrip(".,;:)]}>，。；：）】》")
    if not text:
        return False
    try:
        return os.path.isdir(text) and not os.path.isdir(os.path.join(text, "meta_data"))
    except OSError:
        return False


def _directory_payload_is_generic_tabular_only(path: str) -> bool:
    # Positive on-disk evidence that this is a simple tabular data folder (e.g. one
    # clinical .xlsx), not a multi-file dataset needing a profile/census: demote the
    # barrier so code_executor analyzes it directly. Conservative on purpose.
    text = str(path or "").strip().strip("`'\"").rstrip(".,;:)]}>，。；：）】》")
    if not text:
        return False
    try:
        if not os.path.isdir(text):
            return False
        saw_tabular_file = False
        with os.scandir(text) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    return False
                if not entry.is_file():
                    continue
                if entry.name.lower().endswith(_NON_PHAGESCOPE_TABULAR_FILE_EXTS):
                    saw_tabular_file = True
                else:
                    return False
        return saw_tabular_file
    except OSError:
        return False


def _file_operation_profile_or_census_seen(cls: Any, steps: Sequence[ThinkingStep]) -> bool:
    for step in steps:
        for entry in cls._extract_tool_payloads_from_step(step):
            if str(entry.get("tool") or "").strip().lower() != "file_operations":
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            inner = cls._unwrap_tool_result(payload)
            operation = str(inner.get("operation") or payload.get("operation") or "").strip().lower()
            if operation in {"profile", "census"}:
                return True
    return False


def _phagescope_deep_profile_seen(cls: Any, steps: Sequence[ThinkingStep]) -> bool:
    for step in steps:
        for entry in cls._extract_tool_payloads_from_step(step):
            if str(entry.get("tool") or "").strip().lower() != "phagescope_research":
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            inner = cls._unwrap_tool_result(payload)
            action = str(inner.get("action") or payload.get("action") or "").strip().lower()
            if action == "deep_profile" and inner.get("success", payload.get("success")) is not False:
                return True
    return False


def _phagescope_deep_profile_failure(cls: Any, steps: Sequence[ThinkingStep]) -> Optional[str]:
    for step in steps:
        for entry in cls._extract_tool_payloads_from_step(step):
            if str(entry.get("tool") or "").strip().lower() != "phagescope_research":
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            inner = cls._unwrap_tool_result(payload)
            action = str(inner.get("action") or payload.get("action") or "").strip().lower()
            if action != "deep_profile":
                continue
            success = inner.get("success", payload.get("success"))
            if success is not False:
                continue
            error = inner.get("error") or payload.get("error") or inner.get("summary") or payload.get("summary")
            return str(error or "phagescope_research deep_profile failed").strip()
    return None


def _build_phagescope_deep_profile_failure_answer(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    steps: Sequence[ThinkingStep],
) -> Optional[str]:
    if not agent._phagescope_dataset_analysis_requested(user_query):
        return None
    if agent._phagescope_deep_profile_seen(steps):
        return None
    error = agent._phagescope_deep_profile_failure(steps)
    if not error:
        return None
    path = agent._extract_directory_path_from_query(user_query) or "the PhageScope dataset path"
    return (
        f"I could not complete the PhageScope dataset analysis because `phagescope_research` "
        f"`deep_profile` failed for `{path}`: {error}. "
        "I am not going to synthesize a dataset-level answer from shallow file listings or sampled metadata. "
        "Please retry after fixing the tool/path permission issue; until then, any directory-listing evidence is only a limited diagnostic, not a complete PhageScope profile."
    )


def _needs_phagescope_deep_profile_before_final(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    steps: Sequence[ThinkingStep],
) -> Optional[str]:
    if "phagescope_research" not in agent.available_tools:
        return None
    if not agent._phagescope_dataset_analysis_requested(user_query):
        return None
    if agent._phagescope_deep_profile_seen(steps):
        return None
    return agent._extract_directory_path_from_query(user_query)


def _needs_directory_profile_before_final(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    steps: Sequence[ThinkingStep],
) -> Optional[str]:
    if "file_operations" not in agent.available_tools:
        return None
    if (
        "phagescope_research" in agent.available_tools
        and agent._phagescope_dataset_analysis_requested(user_query)
    ):
        return None
    if not agent._directory_dataset_analysis_requested(user_query):
        return None
    path = agent._extract_directory_path_from_query(user_query)
    if path and agent._directory_payload_is_generic_tabular_only(path):
        return None
    if agent._file_operation_profile_or_census_seen(steps):
        return None
    return agent._extract_directory_path_from_query(user_query)
