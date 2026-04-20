from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set

from .artifact_contracts import load_artifact_manifest
from .artifact_preflight import ArtifactPreflightIssue, ArtifactPreflightService
from .plan_models import PlanTree
from .task_verification import TaskVerificationService

_COMPLETED_LIKE = {"completed", "done", "success"}
_FAILED_LIKE = {"failed", "failure", "error"}


def _normalize_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"done", "success"}:
        return "completed"
    if text == "error":
        return "failed"
    return text


def _parse_execution_result(raw_value: Any) -> tuple[Optional[str], List[str], Dict[str, Any], Optional[Dict[str, Any]]]:
    if raw_value in (None, ""):
        return None, [], {}, None

    payload: Any = raw_value
    if isinstance(raw_value, (bytes, bytearray)):
        try:
            payload = raw_value.decode("utf-8")
        except Exception:
            payload = raw_value

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return payload, [], {}, None

    if isinstance(payload, dict):
        content = payload.get("content")
        notes_data = payload.get("notes") or []
        if isinstance(notes_data, list):
            notes = [str(item) for item in notes_data if item is not None]
        else:
            notes = [str(notes_data)]
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        return content, notes, metadata, payload
    return str(payload), [], {}, None


def _truncate_reason(value: Optional[str], max_chars: int = 220) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3].rstrip()}..."


def _looks_like_failure_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    tokens = (
        "traceback",
        "exception",
        "failed",
        "error",
        "unable to",
        "timed out",
        "interrupted",
    )
    return any(token in text for token in tokens)


def _looks_like_dependency_blocked_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    tokens = (
        "blocked by dependencies",
        "dependency outputs are missing",
        "incomplete dependencies",
        "unmet dependencies",
    )
    return any(token in text for token in tokens)


def _looks_like_retry_or_blocked_failure_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    tokens = (
        "retry",
        "blocked",
        "did not pass",
        "quality gate",
        "release_state: blocked",
        "release state: blocked",
        "unable to",
        "error:",
        "exception",
        "failed",
        "阻断",
        "重试",
        "未通过",
    )
    return any(token in text for token in tokens)


def _looks_like_success_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    tokens = ("completed", "completion", "succeeded", "success", "done")
    return any(token in text for token in tokens)


class PlanStatusResolver:
    def __init__(self) -> None:
        self._artifact_preflight = ArtifactPreflightService()

    def resolve_plan_states(
        self,
        plan_id: int,
        tree: PlanTree,
        *,
        snapshot: Optional[Dict[str, Any]] = None,
        manifest: Optional[Dict[str, Any]] = None,
    ) -> Dict[int, Dict[str, Any]]:
        manifest_payload = manifest if isinstance(manifest, dict) else load_artifact_manifest(plan_id)
        preflight = self._artifact_preflight.validate_plan(
            plan_id,
            tree,
            manifest=manifest_payload,
        )
        contract_by_task = {snapshot.task_id: snapshot for snapshot in preflight.task_contracts}
        blocking_issue_map = self._group_blocking_issues(preflight.errors)
        active_task_ids: Set[int] = set()
        for x in (snapshot or {}).get("active_task_ids") or set():
            try:
                active_task_ids.add(int(x))
            except (TypeError, ValueError):
                pass
        manifest_artifacts = manifest_payload.get("artifacts") if isinstance(manifest_payload.get("artifacts"), dict) else {}
        memo: Dict[int, Dict[str, Any]] = {}
        visiting: Set[int] = set()

        def _dependency_block_reason(task_id: int, incomplete_dependencies: List[int]) -> str:
            parts: List[str] = []
            for dep_id in incomplete_dependencies:
                dep_state = memo.get(dep_id) or {}
                dep_status = str(dep_state.get("effective_status") or "pending").strip().lower() or "pending"
                parts.append(f"#{dep_id}({dep_status})")
            incomplete_display = ", ".join(parts)
            return (
                f"Blocked by dependencies: task #{task_id} requires completed outputs from "
                f"{len(incomplete_dependencies)} dependency task(s): {incomplete_display}."
            )

        def _missing_required_reason(task_id: int, missing_required_aliases: List[str]) -> str:
            aliases_text = ", ".join(missing_required_aliases)
            return (
                f"Blocked: task #{task_id} is missing canonical required artifacts "
                f"({aliases_text})."
            )

        def _missing_publish_reason(task_id: int, missing_publish_aliases: List[str]) -> str:
            aliases_text = ", ".join(missing_publish_aliases)
            return (
                f"Completion contract unsatisfied: task #{task_id} did not publish canonical artifacts "
                f"({aliases_text})."
            )

        def _resolve(task_id: int) -> Dict[str, Any]:
            if task_id in memo:
                return memo[task_id]

            node = tree.nodes.get(task_id)
            raw_status = _normalize_status(getattr(node, "status", None))
            if node is None:
                state = {
                    "task_id": task_id,
                    "raw_status": raw_status or "pending",
                    "effective_status": raw_status or "pending",
                    "status_reason": None,
                    "blocked_by_dependencies": False,
                    "incomplete_dependencies": [],
                    "is_active_execution": task_id in active_task_ids,
                    "missing_required_aliases": [],
                    "required_aliases": [],
                    "published_aliases": [],
                    "missing_publish_aliases": [],
                    "publish_contract_satisfied": True,
                    "contract_source": "none",
                    "verification_status": None,
                    "failure_kind": None,
                    "reason_code": "missing_node",
                }
                memo[task_id] = state
                return state
            if task_id in visiting:
                state = {
                    "task_id": task_id,
                    "raw_status": raw_status or "pending",
                    "effective_status": raw_status or "pending",
                    "status_reason": "Dependency cycle detected while resolving task state.",
                    "blocked_by_dependencies": False,
                    "incomplete_dependencies": [],
                    "is_active_execution": task_id in active_task_ids,
                    "missing_required_aliases": [],
                    "required_aliases": [],
                    "published_aliases": [],
                    "missing_publish_aliases": [],
                    "publish_contract_satisfied": True,
                    "contract_source": "none",
                    "verification_status": None,
                    "failure_kind": None,
                    "reason_code": "cycle_fallback",
                }
                memo[task_id] = state
                return state

            visiting.add(task_id)

            # ── Composite parent aggregation ──────────────────────────
            # If this task has children, derive its status from them rather
            # than from its own execution_result (composite parents are not
            # executed directly).
            child_ids = list(tree.children_ids(task_id))
            if child_ids:
                child_states = [_resolve(cid) for cid in child_ids]
                child_statuses = [
                    str(cs.get("effective_status") or "pending")
                    for cs in child_states
                ]
                if any(s == "running" for s in child_statuses):
                    agg_status = "running"
                    agg_reason = "One or more child tasks are currently running."
                    agg_code = "composite_running"
                elif any(s == "failed" for s in child_statuses):
                    agg_status = "failed"
                    failed_ids = [
                        child_ids[i] for i, s in enumerate(child_statuses) if s == "failed"
                    ]
                    agg_reason = f"Child task(s) failed: {failed_ids}"
                    agg_code = "composite_child_failed"
                elif any(s == "blocked" for s in child_statuses):
                    agg_status = "blocked"
                    agg_reason = "One or more child tasks are blocked."
                    agg_code = "composite_child_blocked"
                elif all(s == "completed" for s in child_statuses):
                    agg_status = "completed"
                    agg_reason = "All child tasks completed."
                    agg_code = "composite_all_completed"
                elif any(s == "completed" for s in child_statuses):
                    agg_status = "running"
                    agg_reason = "Some child tasks completed, others pending."
                    agg_code = "composite_partial"
                else:
                    agg_status = "pending"
                    agg_reason = "Child tasks have not started."
                    agg_code = "composite_pending"

                state = {
                    "task_id": task_id,
                    "raw_status": raw_status or "pending",
                    "effective_status": agg_status,
                    "status_reason": agg_reason,
                    "blocked_by_dependencies": agg_status == "blocked",
                    "incomplete_dependencies": [],
                    "is_active_execution": task_id in active_task_ids,
                    "missing_required_aliases": [],
                    "required_aliases": [],
                    "authoritative_required_aliases": [],
                    "publish_aliases": [],
                    "authoritative_publish_aliases": [],
                    "published_aliases": [],
                    "missing_publish_aliases": [],
                    "publish_contract_satisfied": True,
                    "contract_source": "composite",
                    "verification_status": None,
                    "failure_kind": None,
                    "reason_code": agg_code,
                }
                memo[task_id] = state
                visiting.remove(task_id)
                return state

            execution_result = getattr(node, "execution_result", None)
            content, _notes, metadata, raw_payload = _parse_execution_result(execution_result)
            payload_status = _normalize_status(raw_payload.get("status")) if isinstance(raw_payload, dict) else ""
            raw_result_text = ""
            if isinstance(execution_result, str):
                raw_result_text = execution_result
            elif isinstance(raw_payload, dict):
                raw_result_text = json.dumps(raw_payload, ensure_ascii=False)
            elif content:
                raw_result_text = content

            contract_snapshot = contract_by_task.get(task_id)
            required_aliases = list(contract_snapshot.requires) if contract_snapshot else []
            authoritative_required_aliases = list(contract_snapshot.explicit_requires) if contract_snapshot else []
            publish_aliases = list(contract_snapshot.publishes) if contract_snapshot else []
            authoritative_publish_aliases = list(contract_snapshot.explicit_publishes) if contract_snapshot else []
            contract_source = contract_snapshot.contract_source if contract_snapshot else "none"
            missing_required_aliases = [
                alias
                for alias in authoritative_required_aliases
                if alias not in preflight.manifest_resolved_aliases
            ]
            published_aliases: List[str] = []
            missing_publish_aliases: List[str] = []
            for alias in authoritative_publish_aliases:
                entry = manifest_artifacts.get(alias) if isinstance(manifest_artifacts, dict) else None
                producer_task_id = int(entry.get("producer_task_id") or -1) if isinstance(entry, dict) else -1
                if alias in preflight.manifest_resolved_aliases and producer_task_id == task_id:
                    published_aliases.append(alias)
                else:
                    missing_publish_aliases.append(alias)

            incomplete_dependencies: List[int] = []
            for dep_id in list(getattr(node, "dependencies", []) or []):
                if dep_id not in tree.nodes:
                    continue
                dep_state = _resolve(dep_id)
                if dep_state.get("effective_status") != "completed":
                    incomplete_dependencies.append(dep_id)

            is_active_execution = task_id in active_task_ids
            blocked_meta = bool(metadata.get("blocked_by_dependencies"))
            recorded_incomplete = metadata.get("incomplete_dependencies")
            if not blocked_meta and isinstance(recorded_incomplete, list) and recorded_incomplete:
                blocked_meta = True

            verification = metadata.get("verification") if isinstance(metadata, dict) else None
            verification_status = None
            if isinstance(verification, dict) and verification.get("status") is not None:
                verification_status = _normalize_status(verification.get("status"))
            if not verification_status:
                verification_status = _normalize_status(metadata.get("verification_status")) or None
            failure_kind = str(metadata.get("failure_kind") or "").strip() or None
            manual_acceptance = metadata.get("manual_acceptance") if isinstance(metadata, dict) else None
            manual_acceptance_active = TaskVerificationService.is_manual_acceptance_active(metadata)
            manual_acceptance_reason = None
            if isinstance(manual_acceptance, dict):
                manual_acceptance_reason = str(manual_acceptance.get("reason") or "").strip() or None

            preflight_issues = blocking_issue_map.get(task_id) or []
            effective_status = "pending"
            status_reason: Optional[str] = None
            reason_code = "ready"

            if is_active_execution:
                effective_status = "running"
                status_reason = "Currently executing in an active background job."
                reason_code = "active_execution"
            elif incomplete_dependencies:
                effective_status = "blocked"
                status_reason = _dependency_block_reason(task_id, incomplete_dependencies)
                reason_code = "dependency_blocked"
            elif manual_acceptance_active:
                effective_status = "completed"
                status_reason = manual_acceptance_reason or "Task was manually accepted after review."
                reason_code = "manual_acceptance"
            elif missing_required_aliases and manifest_artifacts:
                effective_status = "blocked"
                status_reason = _missing_required_reason(task_id, missing_required_aliases)
                reason_code = "artifact_input_missing"
            elif preflight_issues and (raw_status in _COMPLETED_LIKE or payload_status in _COMPLETED_LIKE):
                # If verification passed, preflight issues (e.g. ambiguous producer)
                # should not override the completed status.
                if verification_status == "passed":
                    effective_status = "completed"
                    status_reason = _truncate_reason(content or raw_result_text) or "Completed (preflight warning)."
                    reason_code = "completed_preflight_warning"
                else:
                    effective_status = "failed"
                    status_reason = preflight_issues[0].message
                    reason_code = preflight_issues[0].code
            elif preflight_issues:
                effective_status = "blocked"
                status_reason = preflight_issues[0].message
                reason_code = preflight_issues[0].code
            elif verification_status == "failed":
                effective_status = "failed"
                status_reason = _truncate_reason(content or raw_result_text) or "Verification failed."
                reason_code = "verification_failed"
            elif payload_status in _FAILED_LIKE:
                effective_status = "failed"
                status_reason = _truncate_reason(content or raw_result_text) or "Task failed."
                reason_code = "payload_failed"
            elif raw_status in _FAILED_LIKE:
                effective_status = "failed"
                status_reason = _truncate_reason(content or raw_result_text) or "Task failed."
                reason_code = "raw_failed"
            elif raw_status == "running":
                if _looks_like_retry_or_blocked_failure_text(raw_result_text) or _looks_like_failure_text(raw_result_text):
                    effective_status = "failed"
                    status_reason = _truncate_reason(content or raw_result_text) or "Task failed."
                    reason_code = "running_failed"
                elif payload_status in _COMPLETED_LIKE or _looks_like_success_text(raw_result_text):
                    if missing_publish_aliases and manifest_artifacts:
                        effective_status = "failed"
                        status_reason = _missing_publish_reason(task_id, missing_publish_aliases)
                        reason_code = "publish_contract_missing"
                    else:
                        effective_status = "completed"
                        status_reason = _truncate_reason(content or raw_result_text) or "Completed."
                        reason_code = "completed_from_payload"
                else:
                    effective_status = "failed"
                    status_reason = "Execution interrupted."
                    reason_code = "running_interrupted"
            elif raw_status in _COMPLETED_LIKE or payload_status in _COMPLETED_LIKE:
                if _looks_like_retry_or_blocked_failure_text(content or raw_result_text):
                    effective_status = "failed"
                    status_reason = _truncate_reason(content or raw_result_text) or "Task failed."
                    reason_code = "retry_or_blocked_failure"
                elif missing_publish_aliases and manifest_artifacts and verification_status != "passed":
                    effective_status = "failed"
                    status_reason = _missing_publish_reason(task_id, missing_publish_aliases)
                    reason_code = "publish_contract_missing"
                else:
                    effective_status = "completed"
                    status_reason = _truncate_reason(content or raw_result_text) or "Completed."
                    reason_code = "completed"
            elif raw_status == "skipped":
                if blocked_meta:
                    effective_status = "blocked"
                    missing_alias_text = metadata.get("missing_artifact_aliases")
                    if isinstance(missing_alias_text, list) and missing_alias_text:
                        status_reason = (
                            "Blocked: required artifacts are still missing "
                            f"({', '.join(str(alias) for alias in missing_alias_text)})."
                        )
                        reason_code = "artifact_input_missing"
                    else:
                        status_reason = "Blocked until required dependencies or artifacts are available."
                        reason_code = "blocked_skip"
                else:
                    effective_status = "skipped"
                    status_reason = _truncate_reason(content) or "Task was skipped and can be retried."
                    reason_code = "skipped_retryable"
            elif raw_status in {"pending", ""}:
                effective_status = "pending"
                status_reason = "Ready to run."
                reason_code = "ready"
            else:
                effective_status = "pending"
                status_reason = "Ready to run."
                reason_code = "ready"

            state = {
                "task_id": task_id,
                "raw_status": raw_status or "pending",
                "payload_status": payload_status or None,
                "effective_status": effective_status,
                "status_reason": status_reason,
                "blocked_by_dependencies": effective_status == "blocked",
                "incomplete_dependencies": incomplete_dependencies,
                "is_active_execution": is_active_execution,
                "missing_required_aliases": missing_required_aliases,
                "required_aliases": required_aliases,
                "authoritative_required_aliases": authoritative_required_aliases,
                "publish_aliases": publish_aliases,
                "authoritative_publish_aliases": authoritative_publish_aliases,
                "published_aliases": published_aliases,
                "missing_publish_aliases": missing_publish_aliases,
                "publish_contract_satisfied": not missing_publish_aliases,
                "contract_source": contract_source,
                "verification_status": verification_status,
                "failure_kind": failure_kind,
                "manual_acceptance_active": manual_acceptance_active,
                "reason_code": reason_code,
            }
            memo[task_id] = state
            visiting.remove(task_id)
            return state

        for task_id in sorted(tree.nodes):
            _resolve(task_id)
        return memo

    @staticmethod
    def _group_blocking_issues(
        issues: List[ArtifactPreflightIssue],
    ) -> Dict[int, List[ArtifactPreflightIssue]]:
        grouped: Dict[int, List[ArtifactPreflightIssue]] = {}
        for issue in issues:
            task_ids: Set[int] = set(issue.related_task_ids or [])
            if issue.task_id is not None and issue.task_id > 0:
                task_ids.add(issue.task_id)
            for task_id in sorted(task_ids):
                grouped.setdefault(task_id, []).append(issue)
        return grouped


__all__ = ["PlanStatusResolver"]