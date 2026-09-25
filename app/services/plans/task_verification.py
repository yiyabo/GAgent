from __future__ import annotations

import copy
import csv
import glob
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .acceptance_criteria import (
    derive_acceptance_criteria_from_text,
    derive_expected_deliverables,
    derive_relative_output_dirs,
    resolve_glob_min_count,
    resolve_glob_pattern,
    strengthen_acceptance_criteria,
)
from .artifact_contracts import (
    artifact_path_matches_alias,
    artifact_manifest_path,
    candidate_filenames_for_alias,
    infer_artifact_contract,
    is_artifact_alias,
    load_artifact_manifest,
    resolve_artifact_contract_with_provenance,
    resolve_manifest_aliases,
)
from .artifact_validation import validate_artifact
from .model_metric_schema import (
    collect_metric_model_entries,
    is_tree_model_entry,
    missing_required_model_metrics,
)
from .plan_models import PlanNode
from .verification_checks import _CheckMethods
from .verification_discovery import _DiscoveryMethods
from .verification_records import _RecordMethods
from .verification_semantic import _SemanticMethods
from .verification_cues import (
    _COMPLETED_LIKE,
    _FAILED_LIKE,
    _INTERNAL_ARTIFACT_FILENAMES,
    _INTERNAL_TOOL_OUTPUT_RE,
    _NON_DELIVERABLE_SUFFIXES,
    _OUTPUT_DISCOVERY_DIR_NAMES,
    _PATH_KEYS,
    _PDB_LINE_RECORDS,
    _SCAFFOLDING_DIR_NAMES,
    _SEMANTIC_DELIVERABLE_KEYWORDS,
    _SEMANTIC_DELIVERABLE_SUFFIXES,
    _SEMANTIC_FILENAME_STOPWORDS,
    _SEMANTIC_SINGLETON_FALLBACK_GENERIC_TOKENS,
    _SEMANTIC_TOPIC_ALIASES,
    _SOURCE_DISCOVERY_CONTEXT_CUES,
    _SOURCE_DISCOVERY_LINE_FOUND_CUES,
    _SOURCE_DISCOVERY_LINE_MISSING_CUES,
    _SOURCE_DISCOVERY_PATH_CHECKS,
    _SOURCE_DISCOVERY_POSITIVE_CUES,
    _TABULAR_ROW_COUNT_KEYS,
    _CueMethods,
)
from .verification_paths import _PathMethods

logger = logging.getLogger(__name__)


@dataclass
class VerificationFinalization:
    final_status: str
    execution_status: str
    payload: Dict[str, Any]
    verification: Optional[Dict[str, Any]] = None
    artifact_paths: List[str] = field(default_factory=list)


class TaskVerificationService(_CueMethods, _PathMethods, _CheckMethods, _DiscoveryMethods, _SemanticMethods, _RecordMethods):
    """Deterministic verification gate for file/data-oriented task results."""

    @staticmethod
    def is_manual_acceptance_active(metadata: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(metadata, dict):
            return False
        manual_acceptance = metadata.get("manual_acceptance")
        if not isinstance(manual_acceptance, dict):
            return False
        status = str(manual_acceptance.get("status") or "").strip().lower()
        if status:
            return status == "accepted"
        accepted = manual_acceptance.get("accepted")
        return accepted is True

    @staticmethod
    def _is_delegation_successfully_executed(metadata: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(metadata, dict):
            return False
        if not metadata.get("delegated_task_execution"):
            return False
        executor = str(metadata.get("executor") or "").strip().lower()
        if executor and executor not in ("qwen_code", "code_executor"):
            return False
        if str(metadata.get("delegation_status") or "").strip().lower() != "completed":
            return False
        if metadata.get("execution_success") is not True:
            return False
        artifact_paths = metadata.get("artifact_paths")
        if isinstance(artifact_paths, list) and not artifact_paths:
            contract_artifacts = metadata.get("contract_artifacts")
            if not isinstance(contract_artifacts, list) or not contract_artifacts:
                return False
        return True

    def collect_artifact_paths(self, payload: Any) -> List[str]:
        return self._extract_artifact_paths(payload)

    def build_verification_diagnostics(
        self,
        node: PlanNode,
        criteria: Optional[Dict[str, Any]],
        artifact_paths: Sequence[str],
        *,
        payload: Optional[Dict[str, Any]] = None,
        base_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Return read-only path-resolution diagnostics for a verification run."""

        chosen_base_dir = base_dir or self._resolve_base_dir(
            criteria,
            artifact_paths,
            payload=payload,
            node=node,
        )
        task_raw_candidates = self._task_raw_files_base_dir_candidates(
            node=node,
            payload=payload,
            artifact_paths=artifact_paths,
        )
        payload_base_candidates = self._payload_base_dir_candidates(payload)
        inferred_relative_base = self._infer_relative_output_base_dir(criteria, artifact_paths)
        session_roots = self._runtime_session_roots_from_payload_and_artifacts(
            payload=payload,
            artifact_paths=artifact_paths,
        )
        return {
            "plan_id": int(getattr(node, "plan_id", 0) or 0),
            "task_id": int(getattr(node, "id", 0) or 0),
            "node_path": str(getattr(node, "path", "") or ""),
            "chosen_base_dir": str(chosen_base_dir),
            "criteria_uses_relative_paths": self._criteria_uses_relative_paths(criteria),
            "candidate_dirs": {
                "task_raw_files": [str(path) for path in task_raw_candidates],
                "payload_base_dirs": [str(path) for path in payload_base_candidates],
                "inferred_relative_output_base_dir": str(inferred_relative_base) if inferred_relative_base is not None else None,
                "session_roots": [str(path) for path in session_roots],
            },
            "resolved_checks": self._diagnose_check_resolutions(criteria, chosen_base_dir),
            "artifact_path_stats": self._artifact_path_category_stats(artifact_paths),
        }

    def finalize_payload(
        self,
        node: PlanNode,
        payload: Dict[str, Any],
        *,
        execution_status: Optional[str] = None,
        trigger: str = "auto",
    ) -> VerificationFinalization:
        payload_metadata = payload.get("metadata") if isinstance(payload, dict) else None
        normalized_execution_status = self._normalize_status(
            execution_status
            or (payload_metadata.get("execution_status") if isinstance(payload_metadata, dict) else None)
            or payload.get("status")
            or node.status
        )

        normalized_payload = self._coerce_payload(payload, fallback_status=normalized_execution_status)
        metadata = dict(normalized_payload.get("metadata") or {})
        metadata["execution_status"] = normalized_execution_status
        metadata["repair_attempts"] = self._coerce_repair_attempts(metadata.get("repair_attempts"))
        metadata["verification_status"] = "not_run"
        metadata.pop("failure_kind", None)
        metadata.pop("contract_diff", None)
        metadata.pop("plan_patch_suggestion", None)

        effective_criteria, generated = self._effective_acceptance_criteria(node)

        artifact_paths = self._extract_artifact_paths(normalized_payload)
        local_artifact_paths = self._normalize_artifact_paths(
            artifact_paths,
            payload=normalized_payload,
        )
        precheck_base_dir = self._resolve_base_dir(
            effective_criteria,
            local_artifact_paths,
            payload=normalized_payload,
            node=node,
        )
        local_artifact_paths = self._augment_artifact_paths_with_discovered_outputs(
            node=node,
            criteria=effective_criteria,
            payload=normalized_payload,
            artifact_paths=local_artifact_paths,
            base_dir=precheck_base_dir,
        )
        execution_output_recovered = (
            normalized_execution_status not in _COMPLETED_LIKE
            and self._has_output_evidence(local_artifact_paths)
        )

        # Fallback: if execution failed and no outputs found in run_directory,
        # try to discover outputs by extracting paths from task instruction
        # and checking project-level output directories.
        if normalized_execution_status not in _COMPLETED_LIKE and not execution_output_recovered:
            fallback_paths = self._fallback_discover_outputs_for_failed_task(
                node=node,
                payload=normalized_payload,
                existing_paths=local_artifact_paths,
            )
            if fallback_paths:
                local_artifact_paths = list(local_artifact_paths) + fallback_paths
                execution_output_recovered = self._has_output_evidence(local_artifact_paths)

        if normalized_execution_status not in _COMPLETED_LIKE and not execution_output_recovered:
            metadata["failure_kind"] = self._derive_failure_kind(
                execution_status=normalized_execution_status,
                verification_status="not_run",
                payload_metadata=metadata,
            )
            normalized_payload["status"] = normalized_execution_status
            normalized_payload["metadata"] = metadata
            return VerificationFinalization(
                final_status=normalized_execution_status,
                execution_status=normalized_execution_status,
                payload=normalized_payload,
                verification=None,
                artifact_paths=local_artifact_paths,
            )

        if execution_output_recovered:
            metadata["execution_warning"] = True
            metadata["execution_warning_reason"] = (
                "Execution reported failure, but non-empty output artifacts were discovered."
            )
            metadata["execution_reported_status"] = normalized_execution_status

        if not self._has_checks(effective_criteria):
            skipped_status = "warning" if execution_output_recovered else "skipped"
            verification = self._build_verification_record(
                status=skipped_status,
                trigger=trigger,
                blocking=bool((effective_criteria or {}).get("blocking", True)),
                generated=generated,
                checks_total=0,
                checks_passed=0,
                failures=[],
                artifact_paths=local_artifact_paths,
            )
            metadata["verification"] = verification
            metadata["verification_status"] = skipped_status
            if execution_output_recovered:
                metadata["verification_warning"] = True
                verification["blocking"] = False
                verification["warnings"] = [{
                    "type": "execution_status",
                    "success": False,
                    "message": metadata["execution_warning_reason"],
                }]
            # When manually triggered, skipping verification should NOT
            # silently mark the task as completed — the user explicitly asked
            # for verification, so preserve the current execution status and
            # signal that criteria are missing.
            if trigger == "manual":
                final = normalized_execution_status
                verification["needs_criteria"] = True
            else:
                final = "completed"
            normalized_payload["status"] = final
            normalized_payload["metadata"] = metadata
            return VerificationFinalization(
                final_status=final,
                execution_status=normalized_execution_status,
                payload=normalized_payload,
                verification=verification,
                artifact_paths=local_artifact_paths,
            )

        base_dir = self._resolve_base_dir(
            effective_criteria,
            local_artifact_paths,
            payload=normalized_payload,
            node=node,
        )
        local_artifact_paths = self._augment_artifact_paths_with_discovered_outputs(
            node=node,
            criteria=effective_criteria,
            payload=normalized_payload,
            artifact_paths=local_artifact_paths,
            base_dir=base_dir,
        )
        local_artifact_paths = self._materialize_semantic_expected_deliverables(
            node=node,
            criteria=effective_criteria,
            artifact_paths=local_artifact_paths,
            base_dir=base_dir,
        )
        if local_artifact_paths:
            normalized_payload["artifact_paths"] = list(dict.fromkeys(local_artifact_paths))[:80]
            metadata["artifact_paths"] = list(normalized_payload["artifact_paths"])
        diagnostics = self.build_verification_diagnostics(
            node,
            effective_criteria,
            local_artifact_paths,
            payload=normalized_payload,
            base_dir=base_dir,
        )
        metadata["verification_diagnostics"] = diagnostics
        criteria = effective_criteria if isinstance(effective_criteria, dict) else {}
        blocking = bool(criteria.get("blocking", True))
        failures: List[Dict[str, Any]] = []
        hard_failures: List[Dict[str, Any]] = []
        checks = criteria.get("checks") or []
        checks_passed = 0
        checks_executed = 0
        for raw_check in checks:
            outcome = self._run_check(raw_check, base_dir=base_dir, artifact_paths=local_artifact_paths)
            if outcome is None:
                # Defensive: _run_check should never return None, but if it
                # does, treat it as an error rather than silently skipping.
                logger.warning("Verification check returned None for: %s", raw_check)
                failures.append({
                    "type": str((raw_check or {}).get("type", "unknown")),
                    "success": False,
                    "message": "Check returned no result.",
                })
                checks_executed += 1
                continue
            checks_executed += 1
            if outcome["success"]:
                checks_passed += 1
            else:
                failures.append(outcome)
                if isinstance(raw_check, dict) and bool(raw_check.get("hard")):
                    hard_failures.append(outcome)

        verification_status = "passed" if not failures else "failed"
        verification = self._build_verification_record(
            status=verification_status,
            trigger=trigger,
            blocking=blocking,
            generated=generated,
            checks_total=checks_executed,
            checks_passed=checks_passed,
            failures=failures,
            artifact_paths=local_artifact_paths,
        )
        metadata["verification_status"] = verification_status
        contract_diff: Optional[Dict[str, List[str]]] = None
        if failures:
            contract_diff = self._build_contract_diff(
                criteria=effective_criteria,
                failures=failures,
                artifact_paths=local_artifact_paths,
                base_dir=base_dir,
            )
            metadata["contract_diff"] = contract_diff
            metadata["failure_kind"] = "contract_mismatch"
            verification["contract_diff"] = copy.deepcopy(contract_diff)
            plan_patch_suggestion = self._build_plan_patch_suggestion(contract_diff)
            if plan_patch_suggestion:
                metadata["plan_patch_suggestion"] = plan_patch_suggestion
                verification["plan_patch_suggestion"] = plan_patch_suggestion
        artifact_schema_results = self._validate_published_artifact_schemas(
            node=node,
            artifact_paths=local_artifact_paths,
            base_dir=base_dir,
        )
        schema_failures = [
            result for result in artifact_schema_results.values()
            if not bool(result.get("validated") and result.get("schema_valid"))
        ]
        if schema_failures and verification_status == "passed":
            failures.extend(
                {
                    "type": "artifact_schema",
                    "success": False,
                    "path": result.get("path"),
                    "alias": result.get("alias"),
                    "message": result.get("failure_reason") or "Artifact schema validation failed.",
                }
                for result in schema_failures
            )
            verification_status = "failed"
            metadata["verification_status"] = verification_status
            metadata["failure_kind"] = "contract_mismatch"
            verification["status"] = verification_status
            verification["failures"] = failures
            verification["checks_total"] = int(verification.get("checks_total") or checks_executed) + len(schema_failures)
            verification["checks_passed"] = checks_passed
        if schema_failures:
            if contract_diff is None:
                contract_diff = self._build_contract_diff(
                    criteria=effective_criteria,
                    failures=failures,
                    artifact_paths=local_artifact_paths,
                    base_dir=base_dir,
                )
            invalid_outputs = contract_diff.setdefault("invalid_artifacts", [])
            for result in schema_failures:
                label = str(result.get("path") or result.get("alias") or "").strip()
                reason = str(result.get("failure_reason") or "schema validation failed").strip()
                if label:
                    invalid_outputs.append(f"{label}: {reason}")
            metadata["contract_diff"] = contract_diff
            verification["contract_diff"] = copy.deepcopy(contract_diff)
            plan_patch_suggestion = self._build_plan_patch_suggestion(contract_diff)
            if plan_patch_suggestion:
                metadata["plan_patch_suggestion"] = plan_patch_suggestion
                verification["plan_patch_suggestion"] = plan_patch_suggestion
        if (
            verification_status == "failed"
            and self._failures_are_validated_explicit_publish_failures(
                node=node,
                failures=failures,
                artifact_schema_results=artifact_schema_results,
            )
        ):
            verification_status = "passed"
            metadata["verification_status"] = verification_status
            metadata["artifact_contract_satisfied_verification"] = True
            metadata.pop("failure_kind", None)
            metadata.pop("contract_diff", None)
            metadata.pop("plan_patch_suggestion", None)
            verification["status"] = verification_status
            verification["failures"] = []
            verification["checks_passed"] = verification.get("checks_total", checks_executed)
            verification.pop("contract_diff", None)
            verification.pop("plan_patch_suggestion", None)
            failures = []
            contract_diff = None

        if (
            failures
            and self._failures_are_verification_config_errors(failures)
            and self._has_output_evidence(local_artifact_paths)
            and normalized_execution_status in _COMPLETED_LIKE
        ):
            verification_status = "config_error"
            config_errors = [dict(item) for item in failures]
            metadata["verification_status"] = verification_status
            metadata["verification_config_error"] = True
            metadata["verification_config_errors"] = config_errors
            metadata.pop("failure_kind", None)
            metadata.pop("contract_diff", None)
            metadata.pop("plan_patch_suggestion", None)
            verification["status"] = verification_status
            verification["blocking"] = False
            verification["config_error"] = True
            verification["config_errors"] = config_errors
            verification["failures"] = config_errors
            verification.pop("contract_diff", None)
            verification.pop("plan_patch_suggestion", None)
            failures = []
            hard_failures = []
            contract_diff = None

        artifact_verification = self._build_artifact_verification_summary(
            criteria=effective_criteria,
            artifact_paths=local_artifact_paths,
            base_dir=base_dir,
            verification_status=verification_status,
            contract_diff=contract_diff,
        )
        if artifact_schema_results:
            artifact_verification["schema_results"] = artifact_schema_results
            if schema_failures and "artifact_schema_invalid" not in artifact_verification["tags"]:
                artifact_verification["tags"].append("artifact_schema_invalid")
        metadata["artifact_schema_validation"] = artifact_schema_results
        metadata["artifact_verification"] = artifact_verification
        metadata["verification"] = verification
        verification["diagnostics"] = copy.deepcopy(diagnostics)
        verification["artifact_verification"] = copy.deepcopy(artifact_verification)
        normalized_payload["metadata"] = metadata


        format_mismatch_failures = [
            f for f in failures
            if isinstance(f, dict) and f.get("format_mismatch")
        ]
        if format_mismatch_failures and (normalized_execution_status in _COMPLETED_LIKE or execution_output_recovered):
            metadata["format_mismatch_recovery"] = True
            metadata["verification_status"] = "warning"
            metadata["verification_warning"] = True
            metadata["verification_warnings"] = format_mismatch_failures
            metadata.pop("failure_kind", None)
            metadata.pop("contract_diff", None)
            verification["status"] = "warning"
            verification["blocking"] = False
            verification["format_mismatch_recovery"] = True
            verification["warnings"] = format_mismatch_failures
            failures = [f for f in failures if not f.get("format_mismatch")]
            if not failures:
                verification["status"] = "passed"
                metadata["verification_status"] = "passed"
                metadata.pop("verification_warning", None)
                normalized_payload["status"] = "completed"
                normalized_payload["metadata"] = metadata
                return VerificationFinalization(
                    final_status="completed",
                    execution_status=normalized_execution_status,
                    payload=normalized_payload,
                    verification=verification,
                    artifact_paths=local_artifact_paths,
                )

        if failures and normalized_execution_status in _COMPLETED_LIKE and not hard_failures:
            source_discovery_verification = self._source_discovery_verification(
                node=node,
                criteria=effective_criteria,
                failures=failures,
                artifact_paths=local_artifact_paths,
                payload=normalized_payload,
            )
            if isinstance(source_discovery_verification, dict) and source_discovery_verification.get("status") == "passed":
                metadata["source_discovery_verification"] = source_discovery_verification
                metadata["verification_overridden_by_source_discovery"] = True
                metadata["verification_status"] = "passed"
                metadata.pop("failure_kind", None)
                metadata.pop("contract_diff", None)
                metadata.pop("plan_patch_suggestion", None)
                verification["status"] = "passed"
                verification["source_discovery_override"] = True
                verification["source_discovery_verification"] = copy.deepcopy(source_discovery_verification)
                verification["failures"] = []
                verification["checks_passed"] = verification.get("checks_total", 0)
                artifact_summary = verification.get("artifact_verification")
                if isinstance(artifact_summary, dict):
                    artifact_summary["status"] = "passed"
                    artifact_summary["tags"] = ["source_discovery_verified"]
                    artifact_summary["source_discovery_verification"] = copy.deepcopy(source_discovery_verification)
                    artifact_summary["missing_required_outputs"] = []
                    artifact_summary["wrong_format_outputs"] = []
                    artifact_summary["unexpected_outputs"] = []
                if isinstance(metadata.get("artifact_verification"), dict):
                    metadata["artifact_verification"] = copy.deepcopy(artifact_summary)
                normalized_payload["metadata"] = metadata
                normalized_payload["status"] = "completed"
                logger.info(
                    "[Verification] Source-discovery criteria override accepted task %s: %s",
                    getattr(node, "id", "?"),
                    source_discovery_verification.get("summary"),
                )
                return VerificationFinalization(
                    final_status="completed",
                    execution_status=normalized_execution_status,
                    payload=normalized_payload,
                    verification=verification,
                    artifact_paths=local_artifact_paths,
                )

        # When auto-derived (generated) acceptance criteria fail but the task
        # execution itself succeeded, invoke a lightweight LLM call to judge
        # whether the actual outputs semantically satisfy the task requirements.
        # This avoids false-positive failures from heuristic filename extraction
        # (e.g. task instruction mentions "_summary.csv" but output is ".parquet").
        if failures and generated and normalized_execution_status in _COMPLETED_LIKE and not hard_failures:
            llm_verdict = self._llm_arbitrate_verification(
                node=node,
                failures=failures,
                artifact_paths=local_artifact_paths,
                payload=normalized_payload,
            )
            if llm_verdict is True:
                # LLM judged the outputs satisfy the task requirements.
                # Update all status fields so consumers (UI, API, status resolver)
                # see a consistent "passed" state.
                metadata["verification_overridden_by_llm"] = True
                metadata["verification_status"] = "passed"
                metadata.pop("failure_kind", None)
                metadata.pop("contract_diff", None)
                metadata.pop("plan_patch_suggestion", None)
                verification["status"] = "passed"
                verification["llm_override"] = True
                verification["failures"] = []
                verification["checks_passed"] = verification.get("checks_total", 0)
                if isinstance(verification.get("artifact_verification"), dict):
                    verification["artifact_verification"]["status"] = "passed"
                    verification["artifact_verification"]["tags"] = []
                if isinstance(metadata.get("artifact_verification"), dict):
                    metadata["artifact_verification"]["status"] = "passed"
                    metadata["artifact_verification"]["tags"] = []
                normalized_payload["metadata"] = metadata
                normalized_payload["status"] = "completed"
                logger.info(
                    "[Verification] LLM arbitration overrode auto-derived criteria failure "
                    "for task %s — outputs judged sufficient.",
                    getattr(node, "id", "?"),
                )
                return VerificationFinalization(
                    final_status="completed",
                    execution_status=normalized_execution_status,
                    payload=normalized_payload,
                    verification=verification,
                    artifact_paths=local_artifact_paths,
                )
            elif llm_verdict is None:
                # LLM call failed — preserve the verification failure as-is.
                # Do NOT silently pass; the static check result stands.
                logger.warning(
                    "[Verification] LLM arbitration unavailable for task %s; "
                    "preserving static verification failure.",
                    getattr(node, "id", "?"),
                )
                metadata["verification_llm_unavailable"] = True
                normalized_payload["metadata"] = metadata
            # llm_verdict is False — LLM confirmed the outputs are insufficient,
            # fall through to normal failure handling below.

        if (
            failures
            and (normalized_execution_status in _COMPLETED_LIKE or execution_output_recovered)
            and trigger != "manual"
            and self._has_output_evidence(local_artifact_paths)
        ):
            warning_items = [dict(item) for item in failures]
            metadata["verification_status"] = "warning"
            metadata["verification_warning"] = True
            metadata["verification_warnings"] = warning_items
            metadata.pop("failure_kind", None)
            verification["status"] = "warning"
            verification["blocking"] = False
            verification["warnings"] = warning_items
            # Preserve the original failures for diagnostics, but do not let
            # verifier/schema/path mismatches override a successful execution.
            verification["soft_failed"] = True
            artifact_summary = verification.get("artifact_verification")
            if isinstance(artifact_summary, dict):
                artifact_summary["status"] = "warning"
                artifact_summary.setdefault("tags", [])
                if "verification_warning" not in artifact_summary["tags"]:
                    artifact_summary["tags"].append("verification_warning")
            if isinstance(metadata.get("artifact_verification"), dict):
                metadata["artifact_verification"] = copy.deepcopy(artifact_summary)
            normalized_payload["metadata"] = metadata
            normalized_payload["status"] = "completed"
        else:
            if failures and trigger == "manual":
                normalized_payload["status"] = "failed"
            else:
                normalized_payload["status"] = "failed" if failures and blocking else "completed"

        if execution_output_recovered and normalized_payload.get("status") == "completed":
            metadata["execution_warning"] = True
            metadata["execution_warning_reason"] = (
                "Execution reported failure, but non-empty output artifacts were discovered."
            )
            metadata["execution_reported_status"] = normalized_execution_status
            normalized_payload["metadata"] = metadata

        return VerificationFinalization(
            final_status=str(normalized_payload["status"]),
            execution_status=normalized_execution_status,
            payload=normalized_payload,
            verification=verification,
            artifact_paths=local_artifact_paths,
        )

    def verify_task(
        self,
        repo: Any,
        *,
        plan_id: int,
        task_id: int,
        trigger: str = "manual",
        override_criteria: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        session_id: Optional[str] = None,
    ) -> VerificationFinalization:
        """Run verification on an existing task.

        Parameters
        ----------
        override_criteria:
            If provided, this ``acceptance_criteria`` dict takes precedence over
            whatever is stored in the task's metadata. When an LLM passes
            ``verification_criteria`` via action params, the handler converts
            them and injects here so that the verifier actually runs checks
            instead of skipping.
        """
        tree = repo.get_plan_tree(plan_id)
        if not tree.has_node(task_id):
            raise ValueError(f"Task {task_id} not found in plan {plan_id}")
        node = tree.get_node(task_id)

        # If override criteria are provided, inject them unconditionally so
        # that finalize_payload uses the caller's rules instead of stale ones.
        if override_criteria and self._has_checks(override_criteria):
            if not isinstance(node.metadata, dict):
                node.metadata = {}
            node.metadata["acceptance_criteria"] = override_criteria
            logger.info(
                "Injected override acceptance_criteria for task %s: %d checks",
                task_id,
                len(override_criteria.get("checks", [])),
            )

        raw_payload = self._parse_execution_result(
            node.execution_result,
            fallback_status=node.status,
        )
        finalization = self.finalize_payload(
            node,
            raw_payload,
            execution_status=raw_payload.get("metadata", {}).get("execution_status")
            if isinstance(raw_payload.get("metadata"), dict)
            else raw_payload.get("status"),
            trigger=trigger,
        )
        finalization = self.apply_artifact_authority(
            plan_id,
            node,
            finalization,
            session_id=session_id,
        )

        # Persist the effective acceptance_criteria into execution_result.metadata
        # so that future re-verifications (without override) can still find them.
        if override_criteria and self._has_checks(override_criteria):
            payload_meta = finalization.payload.get("metadata")
            if isinstance(payload_meta, dict) and "acceptance_criteria" not in payload_meta:
                payload_meta["acceptance_criteria"] = override_criteria

        if not dry_run:
            repo.update_task(
                plan_id,
                task_id,
                status=finalization.final_status,
                execution_result=json.dumps(finalization.payload, ensure_ascii=False),
                metadata=node.metadata if isinstance(node.metadata, dict) else None,
            )
        return finalization

    def dry_run_reverify_plan(
        self,
        repo: Any,
        *,
        plan_id: int,
        task_ids: Optional[Sequence[int]] = None,
    ) -> Dict[str, Any]:
        tree = repo.get_plan_tree(plan_id)
        selected = set(int(task_id) for task_id in task_ids) if task_ids is not None else None
        items: List[Dict[str, Any]] = []
        summary = {
            "total": 0,
            "verifiable": 0,
            "would_pass": 0,
            "would_fail": 0,
            "would_skip": 0,
            "would_change_status": 0,
            "unverifiable": 0,
        }
        for node in tree.ordered_nodes():
            if selected is not None and node.id not in selected:
                continue
            summary["total"] += 1
            if not node.execution_result:
                summary["unverifiable"] += 1
                items.append({
                    "task_id": node.id,
                    "name": node.name,
                    "current_status": node.status,
                    "dry_run_status": "not_run",
                    "would_change_status": False,
                    "reason": "Task has no execution_result.",
                    "verification_status": "not_run",
                    "diagnostics": None,
                })
                continue
            finalization = self.verify_task(
                repo,
                plan_id=plan_id,
                task_id=node.id,
                trigger="dry_run",
                dry_run=True,
            )
            metadata = finalization.payload.get("metadata") if isinstance(finalization.payload, dict) else {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata_verification = metadata.get("verification")
            verification = metadata_verification if isinstance(metadata_verification, dict) else {}
            verification_status = str(verification.get("status") or metadata.get("verification_status") or "not_run")
            dry_run_status = str(finalization.final_status or "pending")
            current_status = str(node.status or "pending")
            would_change = dry_run_status != current_status
            summary["verifiable"] += 1
            if verification_status == "passed":
                summary["would_pass"] += 1
            elif verification_status == "failed":
                summary["would_fail"] += 1
            else:
                summary["would_skip"] += 1
            if would_change:
                summary["would_change_status"] += 1
            items.append({
                "task_id": node.id,
                "name": node.name,
                "current_status": current_status,
                "dry_run_status": dry_run_status,
                "would_change_status": would_change,
                "verification_status": verification_status,
                "checks_total": verification.get("checks_total"),
                "checks_passed": verification.get("checks_passed"),
                "failure_kind": metadata.get("failure_kind"),
                "failures": list(verification.get("failures") or [])[:10],
                "diagnostics": metadata.get("verification_diagnostics") or verification.get("diagnostics"),
            })
        return {
            "plan_id": plan_id,
            "dry_run": True,
            "summary": summary,
            "items": items,
        }

    def accept_task_result(
        self,
        repo: Any,
        *,
        plan_id: int,
        task_id: int,
        reason: str,
        accepted_by: Optional[str] = None,
        task_name: Optional[str] = None,
        task_instruction: Optional[str] = None,
        trigger: str = "manual_review",
    ) -> VerificationFinalization:
        tree = repo.get_plan_tree(plan_id)
        if not tree.has_node(task_id):
            raise ValueError(f"Task {task_id} not found in plan {plan_id}")

        node = tree.get_node(task_id)
        raw_payload = self._parse_execution_result(node.execution_result, fallback_status=node.status)
        payload = self._coerce_payload(raw_payload, fallback_status=node.status)
        payload_metadata = payload.get("metadata")
        metadata = payload_metadata if isinstance(payload_metadata, dict) else {}
        payload["metadata"] = metadata

        reason_text = str(reason or "").strip()
        if not reason_text:
            raise ValueError("manual acceptance reason is required")

        metadata_verification = metadata.get("verification")
        verification = metadata_verification if isinstance(metadata_verification, dict) else None
        metadata_artifact_authority = metadata.get("artifact_authority")
        artifact_authority = (
            metadata_artifact_authority
            if isinstance(metadata_artifact_authority, dict)
            else None
        )
        original_payload_status = str(payload.get("status") or node.status or "pending").strip().lower() or "pending"
        original_task_status = str(node.status or "pending").strip().lower() or "pending"
        verification_status = (
            str(verification.get("status") or "").strip().lower()
            if verification is not None
            else str(metadata.get("verification_status") or "").strip().lower()
        )
        reviewable_statuses = {"failed", "skipped", "error"}
        can_accept = (
            verification_status == "failed"
            or original_task_status in reviewable_statuses
            or original_payload_status in reviewable_statuses
        )
        if not can_accept:
            raise ValueError(
                "manual acceptance is only allowed for failed, skipped, errored, or verification-failed task results"
            )

        manual_acceptance: Dict[str, Any] = {
            "status": "accepted",
            "accepted": True,
            "trigger": trigger,
            "reason": reason_text,
            "accepted_at": datetime.now(timezone.utc).isoformat(),
            "accepted_by": str(accepted_by).strip() if accepted_by is not None and str(accepted_by).strip() else None,
            "original_task_status": original_task_status,
            "original_payload_status": original_payload_status,
            "verification_status": verification_status or None,
            "artifact_authority_status": (
                str(artifact_authority.get("status") or "").strip().lower()
                if artifact_authority is not None
                else None
            ),
        }
        patch_fields: Dict[str, Any] = {}
        if task_name is not None and str(task_name).strip():
            patch_fields["name"] = str(task_name).strip()
        if task_instruction is not None and str(task_instruction).strip():
            patch_fields["instruction"] = str(task_instruction).strip()
        if patch_fields:
            manual_acceptance["task_patch"] = dict(patch_fields)

        metadata["manual_acceptance"] = manual_acceptance
        metadata["manual_acceptance_status"] = "accepted"
        metadata["user_status_override"] = True
        metadata["user_override_note"] = reason_text
        metadata["original_status"] = original_task_status
        payload["status"] = "completed"

        updates: Dict[str, Any] = {
            "status": "completed",
            "execution_result": json.dumps(payload, ensure_ascii=False),
            "metadata": node.metadata if isinstance(node.metadata, dict) else None,
        }
        if patch_fields:
            updates.update(patch_fields)

        repo.update_task(plan_id, task_id, **updates)

        return VerificationFinalization(
            final_status="completed",
            execution_status=str(metadata.get("execution_status") or original_payload_status or "completed"),
            payload=payload,
            verification=verification,
            artifact_paths=self._extract_artifact_paths(payload),
        )

    def reset_downstream_skipped_tasks(
        self,
        repo: Any,
        *,
        plan_id: int,
        task_id: int,
    ) -> int:
        """Reset immediate skipped dependents to pending after manual acceptance.

        This keeps manual acceptance semantics consistent across API and tool
        entrypoints so downstream tasks can be retried without a second manual
        status edit.
        """
        tree = repo.get_plan_tree(plan_id)
        reset_count = 0
        for dep_node in tree.iter_nodes():
            if task_id not in (dep_node.dependencies or []):
                continue
            if str(dep_node.status or "").strip().lower() != "skipped":
                continue
            repo.update_task(plan_id, dep_node.id, status="pending")
            reset_count += 1
        return reset_count

    def apply_artifact_authority(
        self,
        plan_id: int,
        node: PlanNode,
        finalization: VerificationFinalization,
        *,
        manifest: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> VerificationFinalization:
        """Check both publish and require contracts against the artifact manifest.

        Publish check: did this task produce all explicitly declared outputs?
        Require check: are all explicitly declared inputs available in the manifest?
        """
        payload = finalization.payload if isinstance(finalization.payload, dict) else {}
        payload_metadata = payload.get("metadata")
        metadata = payload_metadata if isinstance(payload_metadata, dict) else {}
        payload["metadata"] = metadata
        node_metadata = node.metadata if isinstance(node.metadata, dict) else {}
        provenance = resolve_artifact_contract_with_provenance(
            task_name=node.display_name(),
            instruction=node.instruction or "",
            metadata=node_metadata,
        )

        # --- Publish satisfaction ---
        publish_aliases = list(provenance.explicit_publishes)
        compat_publish_aliases = [
            alias
            for alias in provenance.publishes()
            if alias not in publish_aliases
        ]

        manifest_payload = manifest if isinstance(manifest, dict) else load_artifact_manifest(plan_id, session_id)
        manifest_payload_artifacts = manifest_payload.get("artifacts")
        manifest_artifacts = manifest_payload_artifacts if isinstance(manifest_payload_artifacts, dict) else {}

        resolved_publish = resolve_manifest_aliases(manifest_payload, publish_aliases)
        published_aliases: List[str] = []
        missing_publish_aliases: List[str] = []
        for alias in publish_aliases:
            entry = manifest_artifacts.get(alias) if isinstance(manifest_artifacts, dict) else None
            producer_task_id = int(entry.get("producer_task_id") or -1) if isinstance(entry, dict) else -1
            if alias in resolved_publish and producer_task_id == node.id:
                published_aliases.append(alias)
            else:
                missing_publish_aliases.append(alias)

        publish_status = "not_applicable"
        if publish_aliases:
            publish_status = "passed" if not missing_publish_aliases else "failed"

        # --- Require satisfaction ---
        require_aliases = list(provenance.explicit_requires)
        compat_require_aliases = [
            alias
            for alias in provenance.requires()
            if alias not in require_aliases
        ]

        resolved_require = resolve_manifest_aliases(manifest_payload, require_aliases) if require_aliases else {}
        satisfied_require_aliases: List[str] = []
        missing_require_aliases: List[str] = []
        for alias in require_aliases:
            if alias in resolved_require:
                satisfied_require_aliases.append(alias)
            else:
                missing_require_aliases.append(alias)

        require_status = "not_applicable"
        if require_aliases:
            require_status = "passed" if not missing_require_aliases else "failed"

        # --- Combined authority ---
        contract_source = provenance.contract_source
        has_any_contract = bool(publish_aliases or require_aliases)
        all_passed = (publish_status != "failed") and (require_status != "failed")
        authority_status = "not_applicable"
        if has_any_contract:
            authority_status = "passed" if all_passed else "failed"

        authority_summary = {
            "status": authority_status,
            "contract_source": contract_source,
            "has_explicit_contract": provenance.has_explicit,
            # Publish
            "expected_publish_aliases": publish_aliases,
            "compat_publish_aliases": compat_publish_aliases,
            "published_aliases": published_aliases,
            "missing_publish_aliases": missing_publish_aliases,
            "publish_status": publish_status,
            # Require
            "expected_require_aliases": require_aliases,
            "compat_require_aliases": compat_require_aliases,
            "satisfied_require_aliases": satisfied_require_aliases,
            "missing_require_aliases": missing_require_aliases,
            "require_status": require_status,
            # Manifest
            "manifest_path": str(artifact_manifest_path(plan_id, session_id)) if has_any_contract else None,
        }
        metadata["artifact_authority"] = authority_summary

        manual_acceptance_active = self.is_manual_acceptance_active(metadata)
        delegation_success = self._is_delegation_successfully_executed(metadata)

        if (
            not manual_acceptance_active
            and not delegation_success
            and require_aliases
            and finalization.final_status in _COMPLETED_LIKE
            and missing_require_aliases
        ):
            metadata["blocked_by_dependencies"] = True
            metadata["missing_artifact_aliases"] = list(missing_require_aliases)
            metadata["artifact_require_blocked"] = True
            payload["status"] = "skipped"
            finalization.final_status = "skipped"

        if (
            not manual_acceptance_active
            and not delegation_success
            and publish_aliases
            and finalization.final_status in _COMPLETED_LIKE
            and missing_publish_aliases
        ):
            metadata["artifact_publish_warning"] = True
            metadata["missing_publish_aliases"] = list(missing_publish_aliases)
            metadata.setdefault("verification_status", "warning")
            payload["status"] = "completed"
            finalization.final_status = "completed"

        if manual_acceptance_active:
            payload["status"] = "completed"
            finalization.final_status = "completed"
            metadata.setdefault("manual_acceptance_status", "accepted")

        finalization.payload = payload
        return finalization

    def _effective_acceptance_criteria(self, node: PlanNode) -> Tuple[Optional[Dict[str, Any]], bool]:
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        criteria = metadata.get("acceptance_criteria")
        if isinstance(criteria, dict):
            return strengthen_acceptance_criteria(copy.deepcopy(criteria)), False
        exec_result = node.execution_result
        if isinstance(exec_result, str):
            try:
                exec_result = json.loads(exec_result)
            except (json.JSONDecodeError, TypeError):
                exec_result = None
        if isinstance(exec_result, dict):
            exec_meta = exec_result.get("metadata")
            if isinstance(exec_meta, dict):
                criteria = exec_meta.get("acceptance_criteria")
                if isinstance(criteria, dict):
                    return strengthen_acceptance_criteria(copy.deepcopy(criteria)), False
        derived = derive_acceptance_criteria_from_text(getattr(node, "instruction", None))
        if isinstance(derived, dict) and self._has_checks(derived):
            return derived, True
        return None, False

    @classmethod
    def parse_shorthand_criteria(cls, raw_criteria: Sequence[str]) -> Dict[str, Any]:
        """Parse shorthand verification criteria strings into acceptance_criteria format.

        Supported shorthand formats:
            - ``file_exists:<path>``
            - ``file_nonempty:<path>``
            - ``glob_nonempty:<glob>``
            - ``glob_count_at_least:<glob>:<min_count>``
            - ``text_contains:<path>:<pattern>``
            - ``json_field_equals:<path>:<key_path>:<expected>``
            - ``json_field_at_least:<path>:<key_path>:<min_value>``
            - ``pdf_valid:<path>``
            - ``model_metrics_valid:<path>``
            - ``pdb_residue_present:<path>:<residue>``

        Returns a well-formed ``acceptance_criteria`` dict with ``checks`` list.
        """
        checks: List[Dict[str, Any]] = []
        for raw in raw_criteria:
            if not isinstance(raw, str) or not raw.strip():
                continue
            parts = raw.strip().split(":", maxsplit=1)
            check_type = parts[0].strip()
            rest = parts[1].strip() if len(parts) > 1 else ""

            if check_type in ("file_exists", "file_nonempty"):
                if rest:
                    checks.append({"type": check_type, "path": rest})
            elif check_type == "glob_nonempty":
                if rest:
                    checks.append({"type": check_type, "glob": rest})
            elif check_type == "glob_count_at_least":
                segments = rest.rsplit(":", maxsplit=1)
                if len(segments) == 2:
                    checks.append({
                        "type": check_type,
                        "glob": segments[0].strip(),
                        "min_count": int(segments[1].strip()),
                    })
            elif check_type == "text_contains":
                segments = rest.split(":", maxsplit=1)
                if len(segments) == 2:
                    checks.append({
                        "type": check_type,
                        "path": segments[0].strip(),
                        "pattern": segments[1].strip(),
                    })
            elif check_type == "json_field_equals":
                segments = rest.split(":", maxsplit=2)
                if len(segments) == 3:
                    checks.append({
                        "type": check_type,
                        "path": segments[0].strip(),
                        "key_path": segments[1].strip(),
                        "expected": segments[2].strip(),
                    })
            elif check_type == "json_field_at_least":
                segments = rest.split(":", maxsplit=2)
                if len(segments) == 3:
                    checks.append({
                        "type": check_type,
                        "path": segments[0].strip(),
                        "key_path": segments[1].strip(),
                        "min_value": float(segments[2].strip()),
                    })
            elif check_type in {"pdf_valid", "model_metrics_valid", "manuscript_markdown_quality"}:
                if rest:
                    checks.append({"type": check_type, "path": rest, "hard": True})
            elif check_type == "pdb_residue_present":
                segments = rest.split(":", maxsplit=1)
                if len(segments) == 2:
                    checks.append({
                        "type": check_type,
                        "path": segments[0].strip(),
                        "residue": segments[1].strip(),
                    })
                elif len(segments) == 1 and segments[0].strip():
                    # path only – residue must be provided separately; skip
                    pass
            else:
                logger.warning("Unknown shorthand check type: %s", check_type)
                continue

        return {
            "category": "file_data",
            "blocking": True,
            "checks": checks,
        }

    @staticmethod
    def _has_checks(criteria: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(criteria, dict):
            return False
        checks = criteria.get("checks")
        return isinstance(checks, list) and len(checks) > 0

    def _build_generated_criteria(self, artifact_paths: Sequence[str]) -> Dict[str, Any]:
        checks: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for raw_path in artifact_paths:
            text = str(raw_path).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            resolved = Path(text).expanduser()
            checks.append({"type": "file_exists", "path": text})
            if resolved.exists() and resolved.is_file():
                checks.append({"type": "file_nonempty", "path": text})
            elif resolved.suffix:
                checks.append({"type": "file_nonempty", "path": text})
        return {
            "category": "file_data",
            "blocking": True,
            "checks": checks,
        }

    @staticmethod
    def _is_internal_artifact_path(value: str) -> bool:
        normalized = "/" + str(value or "").strip().replace("\\", "/").lstrip("/")
        if not normalized or normalized == "/":
            return False
        lowered = normalized.lower()
        basename = lowered.rsplit("/", 1)[-1]
        if basename in _INTERNAL_ARTIFACT_FILENAMES and "/tool_outputs/" in lowered:
            return True
        if lowered.endswith("/deliverables/manifest_latest.json"):
            return True
        return bool(_INTERNAL_TOOL_OUTPUT_RE.search(lowered))

    @staticmethod
    def _is_local_path(value: str) -> bool:
        """Check if a string looks like a local filesystem path.

        Criteria (must satisfy at least one):
        - Starts with ``/``, ``./``, ``../``, or ``~/``
        - Contains a path separator AND has a file-like extension
        - Is a bare filename with a common data/output extension
        """
        text = value.strip()
        if not text:
            return False
        lowered = text.lower()
        if lowered.startswith(("http://", "https://", "data:", "ftp://", "s3://", "gs://")):
            return False
        # Reject strings that are clearly not paths
        if " " in text and "/" not in text and "\\" not in text:
            return False
        # Obvious path prefixes
        if text.startswith(("/", "./", "../", "~/")):
            return True
        # Has a path separator and a file-like extension
        p = Path(text)
        ext = p.suffix.lower()
        _DATA_EXTENSIONS = {
            ".txt", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".yaml", ".yml",
            ".pdb", ".cif", ".fasta", ".fa", ".fna", ".faa", ".fastq", ".fq",
            ".gff", ".gff3", ".gtf", ".bed", ".bam", ".sam", ".vcf",
            ".pdf", ".md", ".rst", ".html", ".log",
            ".xlsx", ".xls", ".docx", ".pptx",
            ".png", ".jpg", ".jpeg", ".svg", ".gif", ".tiff",
            ".py", ".sh", ".r", ".R", ".ipynb",
            ".gz", ".tar", ".zip", ".bz2",
            ".bib", ".tex",
        }
        if "/" in text or "\\" in text:
            return bool(ext) or p.name.startswith(".")
        # Bare filename with known extension
        return ext in _DATA_EXTENSIONS

    @staticmethod
    def _get_json_value(payload: Any, key_path: str) -> Any:
        if key_path == "mandatory_gates_passed" and isinstance(payload, dict):
            derived = TaskVerificationService._derive_mandatory_gates_passed(payload)
            if derived is not None:
                return derived
        current = payload
        for part in [segment for segment in key_path.split(".") if segment]:
            if isinstance(current, list):
                try:
                    index = int(part)
                except (ValueError, TypeError):
                    raise KeyError(f"Cannot use {part!r} as list index")
                if index < 0 or index >= len(current):
                    raise KeyError(f"Index {index} out of range (length {len(current)})")
                current = current[index]
                continue
            if not isinstance(current, dict):
                raise KeyError(f"Cannot descend into {part!r}")
            if part not in current:
                raise KeyError(f"Missing key: {part}")
            current = current[part]
        return current

    @staticmethod
    def _derive_mandatory_gates_passed(payload: Dict[str, Any]) -> Optional[bool]:
        summary = payload.get("mandatory_gates_summary")
        if isinstance(summary, dict):
            failed = summary.get("failed")
            unchecked = summary.get("unchecked")
            passed = summary.get("passed")
            total = summary.get("total_mandatory")
            try:
                if int(failed or 0) == 0 and int(unchecked or 0) == 0:
                    if total is None or int(passed or 0) >= int(total or 0):
                        return True
                return False
            except (TypeError, ValueError):
                pass

        gates = payload.get("gates")
        if isinstance(gates, dict) and gates:
            mandatory = [gate for gate in gates.values() if isinstance(gate, dict) and gate.get("mandatory") is True]
            if mandatory:
                return all(
                    str(gate.get("status") or "").strip().upper() == "PASS"
                    and gate.get("checked") is not False
                    for gate in mandatory
                )

        status = str(payload.get("overall_status") or "").strip().upper()
        if status in {"PASS", "PASSED"}:
            return True
        if status in {"FAIL", "FAILED"}:
            return False
        return None

    def _pdb_residue_present(self, path: Path, residue: str) -> bool:
        if not residue:
            raise ValueError("Check is missing a valid `residue`.")
        text = path.read_text(encoding="utf-8", errors="ignore")
        residue = residue.upper()
        residue_pattern = re.compile(rf"\b{re.escape(residue)}\b")
        for line in text.splitlines():
            record = line[:6].strip().upper()
            if record not in _PDB_LINE_RECORDS:
                continue
            if record in {"ATOM", "HETATM"}:
                if len(line) >= 20 and line[17:20].strip().upper() == residue:
                    return True
                continue
            if residue_pattern.search(line):
                return True
        return False

    @staticmethod
    def _normalize_status(raw_status: Optional[str]) -> str:
        value = str(raw_status or "").strip().lower()
        if value in _COMPLETED_LIKE:
            return "completed"
        if value in _FAILED_LIKE:
            return "failed"
        if value == "complete":
            return "completed"
        if not value:
            return "completed"
        return value

    def _parse_execution_result(self, raw_value: Any, *, fallback_status: Optional[str]) -> Dict[str, Any]:
        if raw_value in (None, ""):
            return self._coerce_payload({}, fallback_status=fallback_status)
        payload: Any = raw_value
        if isinstance(raw_value, (bytes, bytearray)):
            payload = raw_value.decode("utf-8", errors="ignore")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {
                    "status": fallback_status or "completed",
                    "content": payload,
                    "notes": [],
                    "metadata": {},
                }
        return self._coerce_payload(payload, fallback_status=fallback_status)

    def _coerce_payload(self, payload: Any, *, fallback_status: Optional[str]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {"content": str(payload)}
        normalized = copy.deepcopy(payload)
        status = self._normalize_status(normalized.get("status") or fallback_status)
        normalized["status"] = status
        if "content" not in normalized or normalized["content"] is None:
            normalized["content"] = ""
        if not isinstance(normalized.get("notes"), list):
            raw_notes = normalized.get("notes")
            normalized["notes"] = [] if raw_notes in (None, "") else [str(raw_notes)]
        if not isinstance(normalized.get("metadata"), dict):
            normalized["metadata"] = {}
        return normalized
