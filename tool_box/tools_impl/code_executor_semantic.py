"""Semantic failure protocol + success classification + payload rewriting.

Extracted from ``code_executor.py`` (clusters C2+C3) per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.1. This sibling owns
the semantic-failure wire protocol (``STATUS: BLOCKED_*`` / ``DETAIL:`` lines
and the ``_SEMANTIC_FAILURE_*`` regexes), the required-output detection, the
execution success/failure classification, and the failure payload rewrite.

Compatibility contract (gating.py pattern): the ``code_executor`` facade
re-exports every name defined here; test and production import sites keep
working unchanged.
"""

from __future__ import annotations

import fnmatch as _fnmatch
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.services.plans.acceptance_criteria import derive_expected_deliverables

logger = logging.getLogger(__name__)


def _ce():
    """Late-bind the code_executor facade (gating ``_dta()`` pattern).

    Sanctioned deviation: siblings never import facade top-level names
    directly (the door is one-way). The single cross-cluster call into
    facade-resident ``_find_unique_run_prefixed_contract_source`` resolves
    through the facade module object at call time, keeping the facade's
    monkeypatch surface authoritative and staying safe when that helper
    itself moves to a later sibling (the facade re-export keeps resolving).
    """
    from tool_box.tools_impl import code_executor

    return code_executor


_BLOCK_SCOPE_STATUS = "STATUS: BLOCKED_SCOPE"
_BLOCK_SCOPE_REASON = "REASON: NEED_ATOMIC_TASK"
_SEMANTIC_FAILURE_STATUS_DETAILS: Dict[str, str] = {
    "BLOCKED_DEPENDENCY": "Blocked by missing or unusable upstream dependency.",
    "MISSING_INPUT": "Blocked by missing input data.",
    "NO_OUTPUT": "Execution produced no required outputs.",
}
_SEMANTIC_FAILURE_STATUS_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?STATUS(?:\*\*)?\s*:\s*"
    r"(?:\*\*)?(BLOCKED_DEPENDENCY|MISSING_INPUT|NO_OUTPUT)(?:\*\*)?\b",
    re.IGNORECASE | re.MULTILINE,
)
_SEMANTIC_FAILURE_DETAIL_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?DETAIL(?:\*\*)?\s*:\s*(?:\*\*)?(?P<detail>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_SKILL_GUIDANCE_MAX_CHARS = 2000
_SKILL_GUIDANCE_MAX_SKILLS = 2


def _get_skill_guidance(task: str) -> str:
    """Budgeted skill guidance for the delegation prompt.

    Deterministic selection only (no LLM call, no added latency), capped at
    two skills / _SKILL_GUIDANCE_MAX_CHARS chars so the delegation prompt
    stays lean. Every call goes through get_skills_loader, so this is also
    the hot-reload checkpoint for the chat (non-plan) execution path.
    """
    try:
        from app.services.skills import get_skills_loader

        loader = get_skills_loader(auto_sync=False)
        eligible = loader._eligible_skills("task")
        if not eligible:
            return ""
        selected = loader._deterministic_candidates(
            eligible=eligible,
            task_title=str(task or "")[:200],
            task_description=str(task or "")[:2000],
            dependency_paths=None,
            tool_hints=None,
            preferred_skills=None,
        )[: _SKILL_GUIDANCE_MAX_SKILLS]
        if not selected:
            return ""
        content = str(
            getattr(
                loader.build_skill_context(selected, max_chars=_SKILL_GUIDANCE_MAX_CHARS),
                "content",
                "",
            )
            or ""
        ).strip()
        if not content:
            return ""
        return f"Skill guidance (apply when relevant to the task):\n{content}\n\n"
    except Exception as exc:
        logger.debug("Skill guidance unavailable: %s", exc)
        return ""


def _normalize_csv_values(value: Any) -> List[str]:
    if value is None:
        return []

    raw_items: List[str] = []
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if not text:
                continue
            if "," in text:
                raw_items.extend(text.split(","))
            else:
                raw_items.append(text)
    else:
        text = str(value).strip()
        if text:
            raw_items = [text]

    tokens: List[str] = []
    seen = set()
    for item in raw_items:
        token = str(item).strip()
        if not token:
            continue
        normalized = token.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(token)
    return tokens


def _semantic_failure_kind(status: str) -> str:
    return str(status or "").strip().lower()


def _semantic_failure_error(status: str, detail: str) -> str:
    status_text = str(status or "").strip().upper() or "EXECUTION_FAILED"
    detail_text = str(detail or "").strip()
    if detail_text:
        return f"{status_text}: {detail_text}"
    return _SEMANTIC_FAILURE_STATUS_DETAILS.get(status_text, status_text)


def _extract_semantic_failure_from_text(text: str) -> Optional[Dict[str, str]]:
    if not isinstance(text, str) or not text.strip():
        return None
    status_match = _SEMANTIC_FAILURE_STATUS_RE.search(text)
    if not status_match:
        return None
    prefix = text[max(0, status_match.start() - 120) : status_match.start()].lower()
    if "if blocked" in prefix or "output exactly" in prefix:
        return None
    status = status_match.group(1).strip().upper()
    detail = ""
    detail_match = _SEMANTIC_FAILURE_DETAIL_RE.search(text)
    if detail_match:
        detail = detail_match.group("detail").strip().strip("*").strip()
    if "<" in detail and ">" in detail:
        return None
    if not detail:
        detail = _SEMANTIC_FAILURE_STATUS_DETAILS.get(status, "Execution reported semantic failure.")
    return {
        "status": status,
        "detail": detail,
        "failure_kind": _semantic_failure_kind(status),
    }


def _iter_structured_semantic_text(value: Any) -> List[str]:
    texts: List[str] = []

    def _append(candidate: Any) -> None:
        if isinstance(candidate, str) and candidate.strip():
            texts.append(candidate)

    def _visit(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                _visit(child)
            return
        if not isinstance(item, dict):
            return

        raw_status = str(item.get("status") or item.get("execution_status") or "").strip().upper()
        if raw_status in _SEMANTIC_FAILURE_STATUS_DETAILS:
            detail_value = item.get("detail") or item.get("error") or item.get("message") or item.get("summary")
            _append(f"STATUS: {raw_status}\nDETAIL: {detail_value or _SEMANTIC_FAILURE_STATUS_DETAILS[raw_status]}")

        event_type = str(item.get("type") or "").strip().lower()
        if event_type == "assistant":
            message = item.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and str(part.get("type") or "").strip().lower() == "text":
                            _append(part.get("text"))
                else:
                    _append(content)
            return

        if event_type == "result":
            for key in ("result", "content", "message", "summary", "error"):
                _append(item.get(key))
            return

        if not event_type:
            for key in ("result", "content", "message", "raw_output", "summary", "error"):
                _append(item.get(key))
            for child in item.values():
                if isinstance(child, (dict, list)):
                    _visit(child)

    _visit(value)
    return texts


def _detect_semantic_execution_failure(
    stdout: str,
    output_data: Any,
) -> Optional[Dict[str, str]]:
    structured_texts = _iter_structured_semantic_text(output_data)
    for text in structured_texts:
        failure = _extract_semantic_failure_from_text(text)
        if failure:
            return failure
    if structured_texts:
        return None
    failure = _extract_semantic_failure_from_text(stdout or "")
    if failure:
        return failure
    return None


def _detect_missing_required_outputs(
    execution_spec: Optional[Dict[str, Any]],
    produced_files: Sequence[str],
    *,
    task_work_dir: Optional[Path] = None,
) -> Optional[str]:
    if not isinstance(execution_spec, dict):
        return None
    criteria = execution_spec.get("acceptance_criteria")
    if not isinstance(criteria, dict) or criteria.get("blocking") is False:
        return None
    checks = criteria.get("checks")
    if not isinstance(checks, list) or not checks:
        return None
    expected = derive_expected_deliverables(criteria)
    if expected:
        produced_normalized: List[str] = []
        for raw_path in produced_files:
            text = str(raw_path or "").strip().replace("\\", "/")
            if text:
                produced_normalized.append(text.strip("/"))

        missing: List[str] = []
        for raw_expected in expected:
            expected_text = str(raw_expected or "").strip().replace("\\", "/")
            if not expected_text:
                continue
            if _required_output_is_present(
                expected_text,
                produced_normalized,
                task_work_dir=task_work_dir,
            ):
                continue
            missing.append(expected_text)
        if not missing:
            return None
        preview = ", ".join(str(item) for item in missing[:5])
        if len(missing) > 5:
            preview += ", ..."
        return f"Missing required deliverables: {preview}"
    return "No files were produced despite blocking acceptance criteria requiring output artifacts."


def _required_output_is_present(
    expected_text: str,
    produced_normalized: Sequence[str],
    *,
    task_work_dir: Optional[Path],
) -> bool:
    expected_norm = str(expected_text or "").strip().replace("\\", "/").strip("/")
    if not expected_norm:
        return False

    has_glob = any(token in expected_norm for token in ("*", "?", "["))
    for produced in produced_normalized:
        produced_norm = str(produced or "").strip().replace("\\", "/").strip("/")
        if not produced_norm:
            continue
        if has_glob:
            if _fnmatch.fnmatch(produced_norm, expected_norm) or _fnmatch.fnmatch(
                produced_norm,
                f"*/{expected_norm}",
            ):
                return True
            continue
        if produced_norm == expected_norm or produced_norm.endswith(f"/{expected_norm}"):
            return True

    if task_work_dir is None:
        return False
    expected_path = Path(expected_text).expanduser()
    candidate = expected_path if expected_path.is_absolute() else task_work_dir / expected_path
    try:
        if has_glob:
            return any(path.is_file() for path in candidate.parent.glob(candidate.name))
        if candidate.is_file():
            return True
        if candidate.is_dir():
            return any(candidate.iterdir())
        if not expected_path.is_absolute():
            prefixed = _ce()._find_unique_run_prefixed_contract_source(task_work_dir, expected_path)
            return prefixed is not None
        return False
    except OSError:
        return False


def _execution_failure_error_category(failure_kind: str) -> str:
    normalized = str(failure_kind or "").strip().lower()
    if normalized == "blocked_dependency":
        return "blocked_dependency"
    if normalized == "missing_input":
        return "missing_input"
    if normalized in {"no_output", "missing_required_outputs"}:
        return "missing_required_outputs"
    return "execution_semantic_failure"


def _detect_execution_semantic_or_output_failure(
    *,
    stdout: str,
    output_data: Any,
    execution_spec: Optional[Dict[str, Any]],
    produced_files: Sequence[str],
    success: bool,
    task_work_dir: Optional[Path] = None,
) -> Optional[Dict[str, str]]:
    semantic_failure = _detect_semantic_execution_failure(stdout, output_data)
    if semantic_failure:
        return semantic_failure

    if success:
        missing_detail = _detect_missing_required_outputs(
            execution_spec,
            produced_files,
            task_work_dir=task_work_dir,
        )
        if missing_detail:
            return {
                "status": "NO_OUTPUT",
                "detail": missing_detail,
                "failure_kind": "missing_required_outputs",
            }
    return None


def _classify_execution_success(
    *,
    stdout: str,
    output_data: Any,
    execution_spec: Optional[Dict[str, Any]],
    produced_files: Sequence[str],
    success: bool,
    task_work_dir: Optional[Path] = None,
) -> tuple[bool, Optional[Dict[str, str]]]:
    execution_failure = _detect_execution_semantic_or_output_failure(
        stdout=stdout,
        output_data=output_data,
        execution_spec=execution_spec,
        produced_files=produced_files,
        success=success,
        task_work_dir=task_work_dir,
    )
    if execution_failure:
        return False, execution_failure
    return bool(success), None


def _apply_execution_failure_to_payload(
    result_payload: Dict[str, Any],
    execution_failure: Optional[Dict[str, str]],
) -> None:
    if not execution_failure:
        return
    status = str(execution_failure.get("status") or "").strip().upper()
    detail = str(execution_failure.get("detail") or "").strip()
    failure_kind = str(execution_failure.get("failure_kind") or _semantic_failure_kind(status)).strip()
    result_payload["success"] = False
    result_payload["execution_status"] = "failed"
    result_payload["failure_kind"] = failure_kind
    result_payload["error_category"] = _execution_failure_error_category(failure_kind)
    result_payload["error_summary"] = _semantic_failure_error(status, detail)
    result_payload["error"] = result_payload["error_summary"]
    if failure_kind in {"blocked_dependency", "missing_input", "no_output", "missing_required_outputs"}:
        result_payload["produced_files"] = []
        result_payload["produced_files_count"] = 0
        result_payload["artifact_paths"] = []
        result_payload["contract_artifacts"] = []
        result_payload["session_artifact_paths"] = []
        output_location = result_payload.get("output_location")
        if isinstance(output_location, dict):
            output_location["files"] = []
        result_payload.pop("deliverable_submit", None)


def _detect_scope_blocked(stdout: str, output_data: Any) -> Optional[str]:
    candidates: List[str] = []
    if stdout:
        candidates.append(stdout)
    if isinstance(output_data, dict):
        for key in ("result", "content", "message", "raw_output"):
            value = output_data.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value)

    for text in candidates:
        if _BLOCK_SCOPE_STATUS not in text:
            continue
        detail_match = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("DETAIL:"):
                detail_match = stripped[len("DETAIL:") :].strip()
                break
        if detail_match:
            return detail_match
        if _BLOCK_SCOPE_REASON in text:
            return "Need atomic task decomposition."
        return "Blocked by execution scope guardrail."
    return None


def _clear_stale_contract_failure_state(
    *,
    success: bool,
    verification_status: Optional[str],
    contract_error_summary: Optional[str],
    contract_fix_guidance: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Drop stale contract failure details once verification has passed."""
    if success and verification_status == "passed":
        return None, None
    return contract_error_summary, contract_fix_guidance
