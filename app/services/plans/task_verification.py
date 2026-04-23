from __future__ import annotations

import copy
import csv
import fnmatch
import glob
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .acceptance_criteria import (
    derive_acceptance_criteria_from_text,
    derive_expected_deliverables,
    derive_relative_output_dirs,
    resolve_glob_min_count,
    resolve_glob_pattern,
)
from .artifact_contracts import (
    artifact_path_matches_alias,
    artifact_manifest_path,
    infer_artifact_contract,
    is_artifact_alias,
    load_artifact_manifest,
    resolve_artifact_contract_with_provenance,
    resolve_manifest_aliases,
)
from .plan_models import PlanNode

logger = logging.getLogger(__name__)

_COMPLETED_LIKE = {"completed", "done", "success"}
_FAILED_LIKE = {"failed", "failure", "error"}
_PATH_KEYS = {
    "path",
    "output_path",
    "analysis_path",
    "effective_output_path",
    "effective_analysis_path",
    "partial_output_path",
    "combined_path",
    "combined_partial",
    "sections_dir",
    "reviews_dir",
    "merge_queue",
    "citation_validation_path",
    "manifest_path",
    "result_path",
    "preview_path",
    "run_directory",
    "working_directory",
    "task_directory_full",
    "task_root_directory",
    "results_directory",
    "work_dir",
    "run_dir",
    "references_bib",
    "evidence_md",
    "library_jsonl",
    "pdf_dir",
    "artifact_paths",
}
_PDB_LINE_RECORDS = {"HET", "HETNAM", "HETATM", "ATOM", "MODRES", "LINK"}
_INTERNAL_ARTIFACT_FILENAMES = {"result.json", "manifest.json", "preview.json"}
_INTERNAL_TOOL_OUTPUT_RE = re.compile(
    r"(?:^|/)tool_outputs/job_[^/]+/step_\d+_[^/]+(?:/.*)?$",
    re.IGNORECASE,
)
_TABULAR_ROW_COUNT_KEYS = {"row_count", "rows", "record_count"}
_SEMANTIC_DELIVERABLE_SUFFIXES = {".md"}
_SEMANTIC_DELIVERABLE_KEYWORDS = {"evidence"}
_SEMANTIC_FILENAME_STOPWORDS = {
    "a",
    "an",
    "and",
    "draft",
    "evidence",
    "file",
    "final",
    "for",
    "key",
    "md",
    "of",
    "output",
    "outputs",
    "report",
    "section",
    "sections",
    "summary",
    "summaries",
    "task",
    "the",
    "v2",
    "v3",
}
_SEMANTIC_SINGLETON_FALLBACK_GENERIC_TOKENS = {
    "memo",
    "memos",
    "misc",
    "miscellaneous",
    "note",
    "notes",
    "placeholder",
    "scratch",
    "temp",
    "tmp",
    "todo",
    "todos",
}
_SEMANTIC_TOPIC_ALIASES = {
    "conclusion": {
        "advance",
        "advances",
        "future",
        "outlook",
        "perspective",
        "perspectives",
        "prospect",
        "prospects",
    },
}


@dataclass
class VerificationFinalization:
    final_status: str
    execution_status: str
    payload: Dict[str, Any]
    verification: Optional[Dict[str, Any]] = None
    artifact_paths: List[str] = field(default_factory=list)


class TaskVerificationService:
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

    def collect_artifact_paths(self, payload: Any) -> List[str]:
        return self._extract_artifact_paths(payload)

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

        artifact_paths = self._extract_artifact_paths(normalized_payload)
        local_artifact_paths = [path for path in artifact_paths if self._is_local_path(path)]

        if normalized_execution_status not in _COMPLETED_LIKE:
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

        effective_criteria, generated = self._effective_acceptance_criteria(node)

        if not self._has_checks(effective_criteria):
            verification = self._build_verification_record(
                status="skipped",
                trigger=trigger,
                blocking=bool((effective_criteria or {}).get("blocking", True)),
                generated=generated,
                checks_total=0,
                checks_passed=0,
                failures=[],
                artifact_paths=local_artifact_paths,
            )
            metadata["verification"] = verification
            metadata["verification_status"] = "skipped"
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
        blocking = bool(effective_criteria.get("blocking", True))
        failures: List[Dict[str, Any]] = []
        checks = effective_criteria.get("checks") or []
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
        artifact_verification = self._build_artifact_verification_summary(
            criteria=effective_criteria,
            artifact_paths=local_artifact_paths,
            base_dir=base_dir,
            verification_status=verification_status,
            contract_diff=contract_diff,
        )
        metadata["artifact_verification"] = artifact_verification
        metadata["verification"] = verification
        verification["artifact_verification"] = copy.deepcopy(artifact_verification)
        normalized_payload["metadata"] = metadata

        # When auto-derived (generated) acceptance criteria fail but the task
        # execution itself succeeded, invoke a lightweight LLM call to judge
        # whether the actual outputs semantically satisfy the task requirements.
        # This avoids false-positive failures from heuristic filename extraction
        # (e.g. task instruction mentions "_summary.csv" but output is ".parquet").
        if failures and generated and normalized_execution_status in _COMPLETED_LIKE:
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

        normalized_payload["status"] = "failed" if failures and blocking else "completed"

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
        )

        # Persist the effective acceptance_criteria into execution_result.metadata
        # so that future re-verifications (without override) can still find them.
        if override_criteria and self._has_checks(override_criteria):
            payload_meta = finalization.payload.get("metadata")
            if isinstance(payload_meta, dict) and "acceptance_criteria" not in payload_meta:
                payload_meta["acceptance_criteria"] = override_criteria

        repo.update_task(
            plan_id,
            task_id,
            status=finalization.final_status,
            execution_result=json.dumps(finalization.payload, ensure_ascii=False),
            metadata=node.metadata if isinstance(node.metadata, dict) else None,
        )
        return finalization

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
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        payload["metadata"] = metadata

        reason_text = str(reason or "").strip()
        if not reason_text:
            raise ValueError("manual acceptance reason is required")

        verification = metadata.get("verification") if isinstance(metadata.get("verification"), dict) else None
        artifact_authority = (
            metadata.get("artifact_authority")
            if isinstance(metadata.get("artifact_authority"), dict)
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

        manual_acceptance = {
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
    ) -> VerificationFinalization:
        """Check both publish and require contracts against the artifact manifest.

        Publish check: did this task produce all explicitly declared outputs?
        Require check: are all explicitly declared inputs available in the manifest?
        """
        payload = finalization.payload if isinstance(finalization.payload, dict) else {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
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

        manifest_payload = manifest if isinstance(manifest, dict) else load_artifact_manifest(plan_id)
        manifest_artifacts = manifest_payload.get("artifacts") if isinstance(manifest_payload.get("artifacts"), dict) else {}

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
            "manifest_path": str(artifact_manifest_path(plan_id)) if has_any_contract else None,
        }
        metadata["artifact_authority"] = authority_summary

        manual_acceptance_active = self.is_manual_acceptance_active(metadata)

        # Demote completed → failed if publish contract unsatisfied.
        # However, if verification already passed, a publish-only failure
        # (artifact exists but wasn't registered in the manifest) should not
        # override the verification result.  This prevents plan-time path
        # guesses or missing publish steps from blocking the entire plan
        # when the task actually produced its outputs.
        if (
            not manual_acceptance_active
            and publish_aliases
            and finalization.final_status in _COMPLETED_LIKE
            and missing_publish_aliases
        ):
            verification = metadata.get("verification")
            verification_passed = (
                isinstance(verification, dict)
                and str(verification.get("status") or "").strip().lower() == "passed"
            )
            if verification_passed:
                # Verification confirmed the task produced valid outputs.
                # Record the publish gap as a warning, not a hard failure.
                metadata["failure_kind"] = "artifact_publish_warning"
                metadata["artifact_authority_warning"] = (
                    f"Publish contract unsatisfied for aliases {missing_publish_aliases}, "
                    "but verification passed. Treating as completed with warning."
                )
                logger.warning(
                    "Task %s: publish contract unsatisfied %s but verification passed; "
                    "keeping completed status (plan_id=%s)",
                    node.id,
                    missing_publish_aliases,
                    plan_id,
                )
            else:
                metadata["failure_kind"] = "artifact_publish_missing"
                payload["status"] = "failed"
                finalization.final_status = "failed"

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
            return copy.deepcopy(criteria), False
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
                    return copy.deepcopy(criteria), False
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

    def _resolve_base_dir(
        self,
        criteria: Optional[Dict[str, Any]],
        artifact_paths: Sequence[str],
        *,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Path:
        if isinstance(criteria, dict):
            raw_base_dir = criteria.get("base_dir")
            if isinstance(raw_base_dir, str) and raw_base_dir.strip():
                return Path(raw_base_dir).expanduser()

        # --- Unified output path: try PathRouter first ---
        # Check if payload contains output_location with hierarchical path info
        if isinstance(payload, dict):
            output_location = payload.get("output_location")
            if isinstance(output_location, dict):
                base_dir_str = output_location.get("base_dir")
                if isinstance(base_dir_str, str) and base_dir_str.strip():
                    candidate = Path(base_dir_str)
                    if candidate.exists() and candidate.is_dir():
                        return candidate

            # Also try resolving via PathRouter if session_id and task_id are available
            session_id = payload.get("session_id") or (
                payload.get("metadata", {}).get("session_id")
                if isinstance(payload.get("metadata"), dict) else None
            )
            task_id = payload.get("task_id") or (
                payload.get("metadata", {}).get("task_id")
                if isinstance(payload.get("metadata"), dict) else None
            )
            ancestor_chain = (
                output_location.get("ancestor_chain")
                if isinstance(output_location, dict) else None
            )
            if session_id and task_id is not None:
                try:
                    from app.services.path_router import get_path_router
                    router = get_path_router()
                    unified_dir = router.get_task_output_dir(
                        session_id, int(task_id), ancestor_chain, create=False
                    )
                    if unified_dir.exists() and unified_dir.is_dir():
                        return unified_dir
                except (ValueError, TypeError):
                    pass

        # For relative acceptance-criteria paths like ``results/foo.csv``, the
        # correct base is the task run/work directory rather than whichever
        # artifact path happened to be extracted first.  CLI backends often
        # expose verification artifacts via ``tool_outputs/...`` while the real
        # deliverables live under ``<run>/results``.
        if self._criteria_uses_relative_paths(criteria):
            for candidate in self._payload_base_dir_candidates(payload):
                if candidate.exists() and candidate.is_dir():
                    return candidate
            inferred = self._infer_relative_output_base_dir(criteria, artifact_paths)
            if inferred is not None:
                return inferred

        candidate_dirs: List[str] = []
        for raw_path in artifact_paths:
            path = Path(raw_path).expanduser()
            candidate = path
            if path.exists() and path.is_file():
                candidate = path.parent
            elif path.suffix:
                candidate = path.parent
            candidate_dirs.append(str(candidate))
        if candidate_dirs:
            try:
                return Path(os.path.commonpath(candidate_dirs))
            except Exception:
                return Path(candidate_dirs[0])
        return Path.cwd()

    @staticmethod
    def _criteria_uses_relative_paths(criteria: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(criteria, dict):
            return False
        for raw_check in criteria.get("checks") or []:
            if not isinstance(raw_check, dict):
                continue
            raw_path = raw_check.get("path")
            if isinstance(raw_path, str) and raw_path.strip():
                path = Path(raw_path.strip()).expanduser()
                if not path.is_absolute():
                    return True
        return False

    @staticmethod
    def _payload_base_dir_candidates(payload: Optional[Dict[str, Any]]) -> List[Path]:
        if not isinstance(payload, dict):
            return []

        ordered_keys = (
            "run_directory",
            "working_directory",
            "task_directory_full",
            "task_root_directory",
            "results_directory",
            "work_dir",
            "run_dir",
        )
        candidates: List[Path] = []
        seen: set[str] = set()

        def _append(value: Any) -> None:
            if not isinstance(value, str):
                return
            text = value.strip()
            if not text:
                return
            path = Path(text).expanduser()
            key = str(path)
            if key in seen:
                return
            seen.add(key)
            candidates.append(path)

        for key in ordered_keys:
            _append(payload.get(key))

        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            for key in ordered_keys:
                _append(metadata.get(key))

        return candidates

    @staticmethod
    def _infer_relative_output_base_dir(
        criteria: Optional[Dict[str, Any]],
        artifact_paths: Sequence[str],
    ) -> Optional[Path]:
        relative_dirs = derive_relative_output_dirs(criteria)
        if not relative_dirs or not artifact_paths:
            return None

        root_counts: Dict[str, int] = {}
        root_paths: Dict[str, Path] = {}

        for raw_path in artifact_paths:
            raw_text = str(raw_path or "").strip()
            if not raw_text:
                continue
            candidate_path = Path(raw_text).expanduser()
            if candidate_path.exists() and candidate_path.is_file():
                candidate_path = candidate_path.parent
            elif candidate_path.suffix:
                candidate_path = candidate_path.parent
            try:
                resolved = candidate_path.resolve()
            except Exception:
                resolved = candidate_path

            parts = list(resolved.parts)
            lowered_parts = [part.lower() for part in parts]
            for relative_dir in relative_dirs:
                token = str(relative_dir or "").strip().strip("/\\").lower()
                if not token:
                    continue
                for index in range(len(lowered_parts) - 1, -1, -1):
                    if lowered_parts[index] != token or index <= 0:
                        continue
                    root = Path(*parts[:index])
                    if not root.exists() or not root.is_dir():
                        continue
                    key = str(root)
                    root_paths[key] = root
                    root_counts[key] = root_counts.get(key, 0) + 1
                    break

        if not root_counts:
            return None

        best_key = max(
            root_counts,
            key=lambda item: (root_counts[item], len(root_paths[item].parts)),
        )
        return root_paths[best_key]

    def _run_check(self, raw_check: Any, *, base_dir: Path, artifact_paths: Sequence[str] = ()) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_check, dict):
            return {
                "type": "invalid_check",
                "message": "Check definition must be an object.",
                "success": False,
            }

        raw_check = self._normalize_check(raw_check)

        check_type = str(raw_check.get("type") or "").strip()
        if not check_type:
            return {
                "type": "invalid_check",
                "message": "Check definition is missing `type`.",
                "success": False,
            }

        try:
            if check_type == "file_exists":
                path = self._resolve_path(raw_check.get("path"), base_dir)
                success = path.exists()
                if not success and artifact_paths:
                    fallback = self._fallback_artifact_match(path, artifact_paths)
                    if fallback:
                        path = fallback
                        success = True
                return self._check_result(check_type, success, path=path, message=None if success else "File does not exist.")
            if check_type == "file_nonempty":
                path = self._resolve_path(raw_check.get("path"), base_dir)
                success = path.exists() and path.is_file() and path.stat().st_size > 0
                if not success and artifact_paths:
                    fallback = self._fallback_artifact_match(path, artifact_paths)
                    if fallback and fallback.exists() and fallback.is_file() and fallback.stat().st_size > 0:
                        path = fallback
                        success = True
                return self._check_result(check_type, success, path=path, message=None if success else "File is missing or empty.")
            if check_type == "glob_nonempty":
                raw_glob = resolve_glob_pattern(raw_check)
                if not raw_glob:
                    return {"type": check_type, "success": False, "message": "Check is missing `glob`."}
                pattern = self._resolve_glob(raw_glob, base_dir)
                matched = glob.glob(pattern, recursive=True)
                success = len(matched) > 0
                return {
                    "type": check_type,
                    "success": success,
                    "glob": pattern,
                    "count": len(matched),
                    "message": None if success else "No files matched glob pattern.",
                }
            if check_type == "glob_count_at_least":
                raw_glob = resolve_glob_pattern(raw_check)
                pattern = self._resolve_glob(raw_glob, base_dir)
                min_count = resolve_glob_min_count(raw_check)
                matched = [item for item in glob.glob(pattern, recursive=True)]
                if not matched and artifact_paths and raw_glob and not glob.has_magic(raw_glob):
                    fallback = self._fallback_artifact_match(
                        self._resolve_path(raw_glob, base_dir),
                        artifact_paths,
                    )
                    if fallback:
                        matched = [str(fallback)]
                if not matched and artifact_paths and raw_glob and glob.has_magic(raw_glob):
                    matched = self._fallback_artifact_glob_matches(
                        raw_glob=raw_glob,
                        resolved_glob=pattern,
                        base_dir=base_dir,
                        artifact_paths=artifact_paths,
                    )
                success = len(matched) >= min_count
                return {
                    "type": check_type,
                    "success": success,
                    "glob": pattern,
                    "count": len(matched),
                    "message": None if success else f"Matched {len(matched)} items, expected at least {min_count}.",
                }
            if check_type == "text_contains":
                path = self._resolve_path(raw_check.get("path"), base_dir)
                pattern = str(raw_check.get("pattern") or "")
                if not path.exists() and artifact_paths:
                    fallback = self._fallback_artifact_match(path, artifact_paths, lenient=False)
                    if fallback:
                        path = fallback
                if not path.exists():
                    raise FileNotFoundError(f"File does not exist: {path}")
                text = path.read_text(encoding="utf-8", errors="ignore")
                success = pattern in text
                return self._check_result(check_type, success, path=path, message=None if success else f"Pattern not found: {pattern}")
            if check_type in {"json_field_equals", "json_field_at_least"}:
                path = self._resolve_path(raw_check.get("path"), base_dir)
                key_path = self._coerce_json_key_path(raw_check)
                if not path.exists() and artifact_paths:
                    fallback = self._fallback_artifact_match(path, artifact_paths, lenient=False)
                    if fallback:
                        path = fallback
                if not path.exists():
                    raise FileNotFoundError(f"JSON file does not exist: {path}")
                if self._looks_like_tabular_row_count_check(path, key_path):
                    actual = self._read_tabular_row_count(path)
                else:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    actual = self._get_json_value(payload, key_path)
                if check_type == "json_field_equals":
                    expected = self._coerce_json_expected(raw_check)
                    # Smart type coercion: if expected is a string but actual is
                    # numeric/bool, try parsing expected to match the JSON type.
                    success = actual == expected
                    if not success and isinstance(expected, str):
                        try:
                            coerced = json.loads(expected)
                            success = actual == coerced
                        except (json.JSONDecodeError, ValueError):
                            pass
                    message = None if success else f"Expected {expected!r}, got {actual!r}."
                else:
                    min_value = self._coerce_json_min_value(raw_check)
                    if min_value is None:
                        raise ValueError("json_field_at_least check is missing `min_value`.")
                    actual_num = float(actual)
                    min_num = float(min_value)
                    success = actual_num >= min_num
                    message = None if success else f"Expected >= {min_num}, got {actual_num}."
                return {
                    "type": check_type,
                    "success": success,
                    "path": str(path),
                    "key_path": key_path,
                    "actual": actual,
                    "message": message,
                }
            if check_type == "pdb_residue_present":
                path = self._resolve_path(raw_check.get("path"), base_dir)
                residue = str(raw_check.get("residue") or "").strip().upper()
                success = self._pdb_residue_present(path, residue)
                return self._check_result(
                    check_type,
                    success,
                    path=path,
                    message=None if success else f"{residue} residue not found in structure records.",
                    extra={"residue": residue},
                )
        except Exception as exc:
            logger.warning("Verification check %s failed with exception: %s", check_type, exc)
            return {
                "type": check_type,
                "success": False,
                "message": str(exc),
            }

        return {
            "type": check_type,
            "success": False,
            "message": f"Unsupported verification check type: {check_type}",
        }

    def _resolve_path(self, raw_path: Any, base_dir: Path) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("Check is missing a valid `path`.")
        path = Path(raw_path.strip()).expanduser()
        if path.is_absolute():
            return path
        return (base_dir / path).resolve()

    def _resolve_glob(self, raw_glob: Any, base_dir: Path) -> str:
        if not isinstance(raw_glob, str) or not raw_glob.strip():
            raise ValueError("Check is missing a valid `glob`.")
        text = raw_glob.strip()
        if os.path.isabs(text):
            return text
        return str((base_dir / text).resolve())

    def _llm_arbitrate_verification(
        self,
        *,
        node: Any,
        failures: List[Dict[str, Any]],
        artifact_paths: Sequence[str],
        payload: Dict[str, Any],
    ) -> Optional[bool]:
        """Use a lightweight LLM call to judge whether actual outputs satisfy the task.

        Returns:
            True  — LLM judged outputs are sufficient (override failure)
            False — LLM judged outputs are insufficient (keep failure)
            None  — LLM call failed (caller should use fallback logic)
        """
        try:
            from app.llm import get_default_client
        except Exception:
            logger.warning("[Verification] Cannot import LLM client for arbitration.")
            return None

        task_instruction = str(getattr(node, "instruction", "") or "").strip()
        if not task_instruction:
            task_instruction = str(getattr(node, "name", "") or "").strip()
        if not task_instruction:
            return None

        # Build a concise list of actual output files (name + size)
        actual_files: List[str] = []
        for raw_path in artifact_paths:
            p = Path(raw_path)
            try:
                if p.exists() and p.is_file():
                    size_kb = p.stat().st_size / 1024
                    actual_files.append(f"{p.name} ({size_kb:.0f} KB)")
                else:
                    actual_files.append(p.name)
            except OSError:
                actual_files.append(p.name)
        # Deduplicate by name while preserving order
        seen_names: set = set()
        deduped_files: List[str] = []
        for item in actual_files:
            name_part = item.split(" (")[0]
            if name_part not in seen_names:
                seen_names.add(name_part)
                deduped_files.append(item)
        actual_files_text = "\n".join(f"  - {f}" for f in deduped_files[:20]) or "  (none)"

        # Build failure summary
        failure_descriptions = []
        for f in failures[:5]:
            check_type = f.get("type", "unknown")
            message = f.get("message", "")
            path = f.get("path", "")
            failure_descriptions.append(f"  - [{check_type}] {path}: {message}")
        failures_text = "\n".join(failure_descriptions)

        # Extract execution stdout summary if available
        exec_stdout = ""
        metadata = payload.get("metadata", {})
        content = str(payload.get("content", "")).strip()
        if content and len(content) > 20:
            exec_stdout = content[:1500]

        prompt = (
            "You are a task verification judge. A task has been executed and produced output files, "
            "but the automated file-name check failed. Your job is to determine whether the actual "
            "outputs semantically satisfy the task requirements, even if the filenames differ.\n\n"
            f"## Task Instruction\n{task_instruction}\n\n"
            f"## Automated Check Failures\n{failures_text}\n\n"
            f"## Actual Output Files\n{actual_files_text}\n\n"
        )
        if exec_stdout:
            prompt += f"## Execution Summary\n{exec_stdout[:1000]}\n\n"
        prompt += (
            "## Your Judgment\n"
            "Based on the task instruction and actual outputs, do the outputs satisfy the task requirements?\n"
            "Consider: format equivalence (csv≈parquet≈tsv), naming variations, and whether the data content "
            "matches what was requested.\n\n"
            'Respond with EXACTLY one line in this format:\n'
            'VERDICT: pass\n'
            'or\n'
            'VERDICT: fail\n\n'
            'Then on the next line, briefly explain your reasoning (one sentence).'
        )

        try:
            client = get_default_client()
            response = client.chat(prompt, max_tokens=256, timeout=15)
            response_text = str(response or "").strip()

            # Parse verdict
            for line in response_text.splitlines():
                line_stripped = line.strip().upper()
                if line_stripped.startswith("VERDICT:"):
                    verdict_value = line_stripped[len("VERDICT:"):].strip()
                    if verdict_value == "PASS":
                        # Extract reasoning
                        reasoning_lines = [
                            l.strip() for l in response_text.splitlines()
                            if l.strip() and not l.strip().upper().startswith("VERDICT:")
                        ]
                        reasoning = reasoning_lines[0] if reasoning_lines else ""
                        logger.info(
                            "[Verification] LLM arbitration PASS for task %s: %s",
                            getattr(node, "id", "?"),
                            reasoning[:200],
                        )
                        return True
                    elif verdict_value == "FAIL":
                        reasoning_lines = [
                            l.strip() for l in response_text.splitlines()
                            if l.strip() and not l.strip().upper().startswith("VERDICT:")
                        ]
                        reasoning = reasoning_lines[0] if reasoning_lines else ""
                        logger.info(
                            "[Verification] LLM arbitration FAIL for task %s: %s",
                            getattr(node, "id", "?"),
                            reasoning[:200],
                        )
                        return False

            # Could not parse verdict
            logger.warning(
                "[Verification] LLM arbitration returned unparseable response for task %s: %s",
                getattr(node, "id", "?"),
                response_text[:200],
            )
            return None

        except Exception as exc:
            logger.warning(
                "[Verification] LLM arbitration call failed for task %s: %s",
                getattr(node, "id", "?"),
                exc,
            )
            return None

    def _fallback_artifact_match(
        self,
        expected_path: Path,
        artifact_paths: Sequence[str],
        *,
        lenient: bool = True,
    ) -> Optional[Path]:
        """When a path-based check fails, search known artifact paths for a match.

        Search order (most specific → least specific):
        1. Exact basename match  (e.g. ``deg_metadata.csv``)

        The ``lenient`` parameter is accepted for API compatibility but the
        previous "any existing file" and "same extension" fallbacks have been
        removed. Those fallbacks caused false-positive verification: a task that
        produced *some* output (or merely another CSV) could pass plan
        verification despite missing the required deliverable path.
        """
        if not artifact_paths:
            return None

        expected_name = expected_path.name.lower()

        basename_hit: Optional[Path] = None
        alias_hit: Optional[Path] = None
        expected_alias = expected_name if is_artifact_alias(expected_name) else None

        for raw in artifact_paths:
            ap = Path(raw)
            if not ap.exists() or not ap.is_file():
                continue
            if basename_hit is None and ap.name.lower() == expected_name:
                basename_hit = ap
            if alias_hit is None and expected_alias and artifact_path_matches_alias(str(ap), expected_alias):
                alias_hit = ap

        if basename_hit:
            logger.info("[Verification] Fallback basename match: %s -> %s", expected_path, basename_hit)
            return basename_hit

        if alias_hit:
            logger.info("[Verification] Fallback alias match: %s -> %s", expected_path, alias_hit)
            return alias_hit

        return None

    def _fallback_artifact_glob_matches(
        self,
        *,
        raw_glob: str,
        resolved_glob: str,
        base_dir: Path,
        artifact_paths: Sequence[str],
    ) -> List[str]:
        if not artifact_paths:
            return []

        patterns: List[str] = []
        seen_patterns: set[str] = set()

        def _add_pattern(value: Any) -> None:
            text = self._normalize_glob_text(value)
            if not text or text in seen_patterns:
                return
            seen_patterns.add(text)
            patterns.append(text)

        _add_pattern(raw_glob)
        _add_pattern(resolved_glob)
        try:
            _add_pattern(Path(resolved_glob).expanduser().resolve().relative_to(base_dir.resolve()))
        except Exception:
            pass

        if not patterns:
            return []

        matched: List[str] = []
        seen_matches: set[str] = set()
        for raw in artifact_paths:
            artifact = Path(str(raw)).expanduser()
            if not artifact.exists() or not artifact.is_file():
                continue
            candidates = self._artifact_glob_match_candidates(artifact, base_dir=base_dir)
            if any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates for pattern in patterns):
                artifact_text = str(artifact)
                if artifact_text not in seen_matches:
                    seen_matches.add(artifact_text)
                    matched.append(artifact_text)
        return matched

    def _artifact_glob_match_candidates(self, artifact_path: Path, *, base_dir: Path) -> List[str]:
        candidates: List[str] = []
        seen: set[str] = set()

        def _add_with_suffixes(value: Any) -> None:
            text = self._normalize_glob_text(value)
            if not text:
                return
            if text not in seen:
                seen.add(text)
                candidates.append(text)
            suffix_source = text[1:] if text.startswith("/") else text
            parts = [part for part in suffix_source.split("/") if part and part != "."]
            for index in range(1, len(parts)):
                suffix = "/".join(parts[index:])
                if suffix not in seen:
                    seen.add(suffix)
                    candidates.append(suffix)

        try:
            resolved = artifact_path.resolve()
        except Exception:
            resolved = artifact_path
        _add_with_suffixes(resolved)
        try:
            _add_with_suffixes(resolved.relative_to(base_dir.resolve()))
        except Exception:
            pass
        return candidates

    @staticmethod
    def _normalize_glob_text(value: Any) -> str:
        text = str(value or "").strip().replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        return text

    @staticmethod
    def _normalize_check(raw_check: Any) -> Any:
        """Normalize legacy field names in a check dict to standard names.

        Mapping (only when the standard field is absent):
          - ``field`` → ``key_path``
          - ``min_count`` → ``min_value``

        Returns a shallow copy; the original dict is never mutated.
        Non-dict inputs are returned as-is.
        """
        if not isinstance(raw_check, dict):
            return raw_check
        result = dict(raw_check)
        if "field" in result and "key_path" not in result:
            result["key_path"] = result["field"]
        if "min_count" in result and "min_value" not in result:
            result["min_value"] = result["min_count"]
        return result

    @staticmethod
    def _coerce_json_key_path(raw_check: Dict[str, Any]) -> str:
        return str(raw_check.get("key_path") or raw_check.get("field") or "").strip()

    @staticmethod
    def _coerce_json_expected(raw_check: Dict[str, Any]) -> Any:
        if raw_check.get("expected") is not None:
            return raw_check.get("expected")
        return raw_check.get("value")

    @staticmethod
    def _coerce_json_min_value(raw_check: Dict[str, Any]) -> Any:
        if raw_check.get("min_value") is not None:
            return raw_check.get("min_value")
        if raw_check.get("min_count") is not None:
            return raw_check.get("min_count")
        return raw_check.get("value")

    @staticmethod
    def _looks_like_tabular_row_count_check(path: Path, key_path: str) -> bool:
        return (
            path.suffix.lower() in {".csv", ".tsv"}
            and str(key_path or "").strip().lower() in _TABULAR_ROW_COUNT_KEYS
        )

    @staticmethod
    def _read_tabular_row_count(path: Path) -> int:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            try:
                next(reader)
            except StopIteration:
                return 0
            return sum(1 for row in reader if any(str(cell).strip() for cell in row))

    @staticmethod
    def _check_result(
        check_type: str,
        success: bool,
        *,
        path: Path,
        message: Optional[str],
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "type": check_type,
            "success": success,
            "path": str(path),
            "message": message,
        }
        if extra:
            result.update(extra)
        return result

    def _build_verification_record(
        self,
        *,
        status: str,
        trigger: str,
        blocking: bool,
        generated: bool,
        checks_total: int,
        checks_passed: int,
        failures: Sequence[Dict[str, Any]],
        artifact_paths: Sequence[str],
    ) -> Dict[str, Any]:
        return {
            "status": status,
            "trigger": trigger,
            "blocking": blocking,
            "generated": generated,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "checks_total": checks_total,
            "checks_passed": checks_passed,
            "failures": [dict(item) for item in failures],
            "evidence": {
                "artifact_paths": list(artifact_paths),
            },
        }

    @staticmethod
    def _coerce_repair_attempts(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, parsed)

    def _derive_failure_kind(
        self,
        *,
        execution_status: str,
        verification_status: str,
        payload_metadata: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        metadata = payload_metadata if isinstance(payload_metadata, dict) else {}
        if bool(metadata.get("blocked_by_dependencies")):
            return "blocked_dependency"
        if execution_status not in _COMPLETED_LIKE:
            return "execution_failed"
        if verification_status == "failed":
            return "contract_mismatch"
        return None

    def _build_contract_diff(
        self,
        *,
        criteria: Optional[Dict[str, Any]],
        failures: Sequence[Dict[str, Any]],
        artifact_paths: Sequence[str],
        base_dir: Path,
    ) -> Dict[str, List[str]]:
        expected_deliverables = self._expected_deliverables(criteria)
        actual_outputs = self._actual_outputs(artifact_paths, base_dir=base_dir)
        missing_required_outputs = self._missing_required_outputs(
            criteria,
            failures,
            base_dir=base_dir,
        )
        unexpected_outputs = [
            output for output in actual_outputs
            if not self._output_matches_expected(output, expected_deliverables)
        ]
        missing_identity_suffixes: Dict[str, set[str]] = {}
        missing_stem_suffixes: Dict[str, set[str]] = {}
        for item in missing_required_outputs:
            identity, stem, suffix = self._deliverable_identity_parts(item)
            if identity and suffix:
                missing_identity_suffixes.setdefault(identity, set()).add(suffix)
            if stem and suffix:
                missing_stem_suffixes.setdefault(stem, set()).add(suffix)

        wrong_format_outputs = [
            output
            for output in unexpected_outputs
            if self._looks_like_wrong_format_output(
                output,
                missing_identity_suffixes=missing_identity_suffixes,
                missing_stem_suffixes=missing_stem_suffixes,
            )
        ]
        return {
            "expected_deliverables": expected_deliverables,
            "actual_outputs": actual_outputs,
            "missing_required_outputs": missing_required_outputs,
            "wrong_format_outputs": wrong_format_outputs,
            "unexpected_outputs": unexpected_outputs,
        }

    def _build_artifact_verification_summary(
        self,
        *,
        criteria: Optional[Dict[str, Any]],
        artifact_paths: Sequence[str],
        base_dir: Path,
        verification_status: str,
        contract_diff: Optional[Dict[str, List[str]]],
    ) -> Dict[str, Any]:
        expected_deliverables = self._expected_deliverables(criteria)
        actual_outputs = self._actual_outputs(artifact_paths, base_dir=base_dir)
        verified_outputs = [
            output
            for output in actual_outputs
            if self._output_matches_expected(output, expected_deliverables)
        ]
        diff = contract_diff if isinstance(contract_diff, dict) else {}
        if verification_status == "passed":
            tags = ["verified_outputs"]
        elif verification_status == "failed":
            tags = ["contract_mismatch"]
        else:
            tags = ["verification_skipped"]
        return {
            "status": verification_status,
            "tags": tags,
            "expected_deliverables": expected_deliverables,
            "actual_outputs": actual_outputs,
            "verified_outputs": verified_outputs,
            "missing_required_outputs": list(diff.get("missing_required_outputs") or []),
            "wrong_format_outputs": list(diff.get("wrong_format_outputs") or []),
            "unexpected_outputs": list(diff.get("unexpected_outputs") or []),
        }

    def _expected_deliverables(self, criteria: Optional[Dict[str, Any]]) -> List[str]:
        return derive_expected_deliverables(criteria)

    def _materialize_semantic_expected_deliverables(
        self,
        *,
        node: PlanNode,
        criteria: Optional[Dict[str, Any]],
        artifact_paths: Sequence[str],
        base_dir: Path,
    ) -> List[str]:
        updated_paths: List[str] = []
        seen: set[str] = set()
        for raw in artifact_paths:
            text = str(raw or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            updated_paths.append(text)

        for expected in self._expected_deliverables(criteria):
            if not self._should_semantically_materialize_expected(expected):
                continue
            target = self._resolve_semantic_materialization_target(expected, base_dir)
            if target is None:
                logger.info(
                    "Skipping semantic materialization for unsafe target %r (task=%s, base_dir=%s)",
                    expected,
                    node.id,
                    base_dir,
                )
                continue
            if target.exists() and target.is_file() and target.stat().st_size > 0:
                target_text = str(target)
                if target_text not in seen:
                    seen.add(target_text)
                    updated_paths.append(target_text)
                continue

            candidate = self._select_semantic_expected_candidate(
                node=node,
                expected=expected,
                artifact_paths=updated_paths,
            )
            if candidate is None:
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if candidate.resolve() != target.resolve():
                    shutil.copy2(candidate, target)
            except Exception as exc:
                logger.warning(
                    "Failed to materialize semantic deliverable %s from %s for task %s: %s",
                    target,
                    candidate,
                    node.id,
                    exc,
                )
                continue

            target_text = str(target)
            if target_text not in seen:
                seen.add(target_text)
                updated_paths.append(target_text)

        return updated_paths[:80]

    @staticmethod
    def _should_semantically_materialize_expected(expected: str) -> bool:
        text = str(expected or "").strip().replace("\\", "/")
        if not text or any(token in text for token in ("*", "?", "[")):
            return False
        path = Path(text)
        if path.suffix.lower() not in _SEMANTIC_DELIVERABLE_SUFFIXES:
            return False
        lowered = path.stem.lower()
        if any(keyword in lowered for keyword in _SEMANTIC_DELIVERABLE_KEYWORDS):
            return True
        return any(
            keyword in part.lower()
            for part in path.parts[:-1]
            for keyword in _SEMANTIC_DELIVERABLE_KEYWORDS
        )

    @staticmethod
    def _resolve_semantic_materialization_target(expected: str, base_dir: Path) -> Optional[Path]:
        text = str(expected or "").strip()
        if not text:
            return None

        raw_target = Path(text).expanduser()
        if raw_target.is_absolute():
            return None

        try:
            resolved_base = base_dir.expanduser().resolve()
        except Exception:
            resolved_base = base_dir.expanduser()

        try:
            resolved_target = (resolved_base / raw_target).resolve()
        except Exception:
            resolved_target = resolved_base / raw_target

        try:
            resolved_target.relative_to(resolved_base)
        except ValueError:
            return None
        return resolved_target

    def _select_semantic_expected_candidate(
        self,
        *,
        node: PlanNode,
        expected: str,
        artifact_paths: Sequence[str],
    ) -> Optional[Path]:
        candidates = self._semantic_candidate_files(node=node, artifact_paths=artifact_paths)
        if not candidates:
            return None

        expected_name = Path(str(expected or "")).name.lower()
        expected_core = self._semantic_core_tokens(expected_name)
        task_core = self._semantic_task_tokens(node)
        topic_core = self._semantic_topic_tokens(
            expected_core=expected_core,
            task_core=task_core,
        )

        best_score: Optional[tuple[int, int, int, int]] = None
        best_path: Optional[Path] = None
        for candidate in candidates:
            score = self._semantic_candidate_score(
                expected_name=expected_name,
                expected_core=expected_core,
                candidate_name=candidate.name.lower(),
                candidate_count=len(candidates),
                topic_core=topic_core,
            )
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_score = score
                best_path = candidate
        return best_path

    def _semantic_candidate_files(
        self,
        *,
        node: PlanNode,
        artifact_paths: Sequence[str],
    ) -> List[Path]:
        all_files: List[Path] = []
        current_task_files: List[Path] = []
        seen: set[str] = set()
        task_marker = f"/task_{node.id}/"

        for raw in artifact_paths:
            path = Path(str(raw)).expanduser()
            if not path.exists() or not path.is_file():
                continue
            if self._is_internal_artifact_path(str(path)):
                continue
            lowered_name = path.name.lower()
            if lowered_name.endswith(".analysis.md") or lowered_name.endswith(".partial.md"):
                continue
            if path.suffix.lower() not in _SEMANTIC_DELIVERABLE_SUFFIXES:
                continue
            try:
                resolved = path.resolve()
            except Exception:
                resolved = path
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            all_files.append(resolved)
            if task_marker in str(resolved).replace("\\", "/"):
                current_task_files.append(resolved)

        return current_task_files or all_files

    @staticmethod
    def _semantic_text_tokens(text: str) -> set[str]:
        tokens = [
            token
            for token in re.split(r"[^a-z0-9]+", str(text or "").lower())
            if token and not token.isdigit()
        ]
        return {
            token
            for token in tokens
            if token not in _SEMANTIC_FILENAME_STOPWORDS
        }

    @classmethod
    def _semantic_core_tokens(cls, file_name: str) -> set[str]:
        stem = Path(str(file_name or "")).stem.lower()
        return cls._semantic_text_tokens(stem)

    @classmethod
    def _semantic_task_tokens(cls, node: PlanNode) -> set[str]:
        return cls._semantic_text_tokens(
            " ".join(
                part
                for part in (
                    str(getattr(node, "name", "") or "").strip(),
                    str(getattr(node, "instruction", "") or "").strip(),
                )
                if part
            )
        )

    @classmethod
    def _semantic_topic_tokens(
        cls,
        *,
        expected_core: set[str],
        task_core: set[str],
    ) -> set[str]:
        topic_core = set(expected_core) | set(task_core)
        expanded = set(topic_core)
        for token in list(topic_core):
            expanded.update(_SEMANTIC_TOPIC_ALIASES.get(token, set()))
        return expanded

    @staticmethod
    def _allow_singleton_semantic_fallback(
        *,
        candidate_core: set[str],
        topic_core: set[str],
    ) -> bool:
        informative_core = candidate_core - _SEMANTIC_SINGLETON_FALLBACK_GENERIC_TOKENS
        return bool(informative_core & topic_core)

    def _semantic_candidate_score(
        self,
        *,
        expected_name: str,
        expected_core: set[str],
        candidate_name: str,
        candidate_count: int,
        topic_core: set[str],
    ) -> Optional[tuple[int, int, int, int]]:
        if candidate_name == expected_name:
            return (1000, 0, 0, 0)

        candidate_core = self._semantic_core_tokens(candidate_name)
        overlap = len(expected_core & candidate_core)
        extras = len(candidate_core - expected_core)
        missing = len(expected_core - candidate_core)

        base_score = 0
        if expected_core:
            if overlap == 0:
                if candidate_count == 1:
                    if not self._allow_singleton_semantic_fallback(
                        candidate_core=candidate_core,
                        topic_core=topic_core,
                    ):
                        return None
                    base_score = 1
                else:
                    return None
            else:
                base_score = overlap * 10 - extras - missing
        elif candidate_count == 1:
            base_score = 1
        else:
            return None

        if "evidence" in expected_name and "evidence" in candidate_name:
            base_score += 3
        if candidate_name.endswith("_summary.md"):
            base_score += 1
        if re.search(r"(?:^|[_-])v\d+$", Path(candidate_name).stem):
            base_score -= 1

        if base_score <= 0 and candidate_count > 1:
            return None

        return (base_score, overlap, -extras, -len(candidate_name))

    def _actual_outputs(self, artifact_paths: Sequence[str], *, base_dir: Path) -> List[str]:
        outputs: List[str] = []
        seen: set[str] = set()
        for raw in artifact_paths:
            path = Path(str(raw)).expanduser()
            if not path.exists() or not path.is_file():
                continue
            try:
                rel = str(path.resolve().relative_to(base_dir.resolve()))
            except Exception:
                rel = str(path.resolve())
            if rel in seen:
                continue
            seen.add(rel)
            outputs.append(rel)
        return outputs[:80]

    def _missing_required_outputs(
        self,
        criteria: Optional[Dict[str, Any]],
        failures: Sequence[Dict[str, Any]],
        *,
        base_dir: Path,
    ) -> List[str]:
        if not isinstance(criteria, dict):
            return []
        failed_targets: List[str] = []
        seen: set[str] = set()
        failed_signatures = set()
        for item in failures:
            if not isinstance(item, dict):
                continue
            failed_signatures.add((
                str(item.get("type") or "").strip(),
                str(item.get("path") or item.get("glob") or "").strip(),
            ))
        for raw_check in criteria.get("checks") or []:
            if not isinstance(raw_check, dict):
                continue
            check_type = str(raw_check.get("type") or "").strip()
            if check_type in {"file_exists", "file_nonempty", "text_contains", "json_field_equals", "json_field_at_least"}:
                candidate = str(raw_check.get("path") or "").strip()
                resolved = str(self._resolve_path(candidate, base_dir)) if candidate else ""
            elif check_type == "glob_count_at_least":
                candidate = str(resolve_glob_pattern(raw_check) or "").strip()
                resolved = str(self._resolve_glob(candidate, base_dir)) if candidate else ""
            else:
                candidate = ""
                resolved = ""
            if not candidate:
                continue
            if (
                (check_type, candidate) not in failed_signatures
                and (check_type, resolved) not in failed_signatures
            ) or candidate in seen:
                continue
            seen.add(candidate)
            failed_targets.append(candidate)
        return failed_targets

    @staticmethod
    def _output_matches_expected(output: str, expected_deliverables: Sequence[str]) -> bool:
        normalized_output = TaskVerificationService._normalize_glob_text(output)
        output_candidates = [normalized_output]
        if normalized_output:
            suffix_source = normalized_output[1:] if normalized_output.startswith("/") else normalized_output
            parts = [part for part in suffix_source.split("/") if part and part != "."]
            for index in range(1, len(parts)):
                output_candidates.append("/".join(parts[index:]))
        for expected in expected_deliverables:
            text = TaskVerificationService._normalize_glob_text(expected)
            if not text:
                continue
            if any(token in text for token in ("*", "?", "[")):
                if any(fnmatch.fnmatch(candidate, text) for candidate in output_candidates):
                    return True
            elif normalized_output == text:
                return True
        return False

    @staticmethod
    def _deliverable_identity_parts(value: Any) -> tuple[str, str, str]:
        normalized = TaskVerificationService._normalize_glob_text(value)
        if not normalized or any(token in normalized for token in ("*", "?", "[")):
            return "", "", ""
        root, suffix = os.path.splitext(normalized)
        basename = normalized.rsplit("/", 1)[-1]
        stem, _ = os.path.splitext(basename)
        return root, stem, suffix.lower()

    @classmethod
    def _looks_like_wrong_format_output(
        cls,
        output: str,
        *,
        missing_identity_suffixes: Dict[str, set[str]],
        missing_stem_suffixes: Dict[str, set[str]],
    ) -> bool:
        identity, stem, suffix = cls._deliverable_identity_parts(output)
        if not suffix:
            return False
        expected_suffixes = set(missing_identity_suffixes.get(identity, set()))
        if not expected_suffixes:
            expected_suffixes = set(missing_stem_suffixes.get(stem, set()))
        return bool(expected_suffixes) and suffix not in expected_suffixes

    @staticmethod
    def _build_plan_patch_suggestion(contract_diff: Dict[str, List[str]]) -> Optional[str]:
        missing = list(contract_diff.get("missing_required_outputs") or [])
        unexpected = list(contract_diff.get("unexpected_outputs") or [])
        wrong_format = list(contract_diff.get("wrong_format_outputs") or [])
        if not missing or not (unexpected or wrong_format):
            return None
        return (
            "Execution produced stable artifacts that do not match the current "
            "plan contract. Review acceptance_criteria and required deliverables "
            "before changing the plan."
        )

    def _extract_artifact_paths(self, payload: Any) -> List[str]:
        found: List[str] = []
        seen: set[str] = set()

        def _add(value: Any) -> None:
            if not isinstance(value, str):
                return
            text = value.strip()
            if (
                not text
                or text in seen
                or not self._is_local_path(text)
                or self._is_internal_artifact_path(text)
            ):
                return
            seen.add(text)
            found.append(text)

        def _visit(value: Any, key: Optional[str] = None) -> None:
            if value is None:
                return
            if isinstance(value, dict):
                for item_key, item_value in value.items():
                    lowered = str(item_key).strip().lower()
                    if lowered == "artifact_paths" and isinstance(item_value, (list, tuple, set)):
                        for item in item_value:
                            _add(item)
                    elif lowered in _PATH_KEYS or lowered.endswith("_path") or lowered.endswith("_file") or lowered.endswith("_dir"):
                        if isinstance(item_value, (list, tuple, set)):
                            for item in item_value:
                                _add(item)
                        else:
                            _add(item_value)
                    if isinstance(item_value, (dict, list, tuple, set)):
                        _visit(item_value, key=lowered)
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    _visit(item, key=key)

        _visit(payload)
        if len(found) > 40:
            logger.debug("Artifact paths truncated: %d -> 40", len(found))
        return found[:40]

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
