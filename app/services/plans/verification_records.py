"""Verification record / failure-classification cluster of ``TaskVerificationService``.

Moved verbatim out of ``task_verification.py`` per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.6 (TV cluster ⑥):
config-error records (``_verification_config_error``,
``_failures_are_verification_config_errors``), check-result and verification
records (``_check_result``, ``_build_verification_record``,
``_coerce_repair_attempts``), failure classification (``_derive_failure_kind``),
the contract diff / artifact verification summary builders, expected-deliverable
lookup, and the published-artifact schema / alias validation helpers
(``_validate_published_artifact_schemas`` … ``_artifact_failure_target_matches_alias``).
Composed into ``TaskVerificationService`` as the ``_RecordMethods`` mixin, so
every ``self.*`` call site is unchanged.

Late binding: ``_artifact_output_presence_failure_matches_alias`` reads
``TaskVerificationService._artifact_failure_target_matches_alias`` by class
attribute; the class lives in the facade, which imports this module at import
time, so it binds ``TaskVerificationService = _facade().TaskVerificationService``
at call time — one added line, the only deviation from byte-verbatim here.

This module has no logger of its own (no logging calls in the moved code).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .acceptance_criteria import derive_expected_deliverables
from .artifact_contracts import (
    artifact_path_matches_alias,
    candidate_filenames_for_alias,
    infer_artifact_contract,
    resolve_artifact_contract_with_provenance,
)
from .artifact_validation import validate_artifact
from .plan_models import PlanNode
from .verification_cues import _COMPLETED_LIKE


def _facade() -> Any:
    """Late-bound task_verification facade module (monkeypatch-friendly lookups)."""
    from . import task_verification

    return task_verification


class _RecordMethods:
    """Verification record cluster of ``TaskVerificationService`` (mixin)."""

    @staticmethod
    def _verification_config_error(check_type: str, message: str) -> Dict[str, Any]:
        return {
            "type": str(check_type or "invalid_check"),
            "success": False,
            "failure_kind": "verification_config_error",
            "verification_config_error": True,
            "message": message,
        }

    @staticmethod
    def _failures_are_verification_config_errors(failures: Sequence[Dict[str, Any]]) -> bool:
        return bool(failures) and all(
            isinstance(item, dict) and bool(item.get("verification_config_error"))
            for item in failures
        )

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

    def _validate_published_artifact_schemas(
        self,
        *,
        node: PlanNode,
        artifact_paths: Sequence[str],
        base_dir: Path,
    ) -> Dict[str, Dict[str, Any]]:
        contract = infer_artifact_contract(
            task_name=node.display_name(),
            instruction=node.instruction or "",
            metadata=node.metadata if isinstance(node.metadata, dict) else {},
        )
        aliases = [str(alias).strip() for alias in contract.get("publishes", []) if str(alias).strip()]
        if not aliases:
            return {}
        results: Dict[str, Dict[str, Any]] = {}
        for alias in aliases:
            source = self._find_artifact_path_for_alias(alias, artifact_paths, base_dir)
            if source is None:
                continue
            results[alias] = validate_artifact(alias, str(source)).to_dict()
        return results

    def _find_artifact_path_for_alias(
        self,
        alias: str,
        artifact_paths: Sequence[str],
        base_dir: Path,
    ) -> Optional[Path]:
        candidates: List[Path] = []
        for raw_path in artifact_paths:
            text = str(raw_path or "").strip()
            if not text:
                continue
            path = Path(text).expanduser()
            if not path.is_absolute():
                path = base_dir / path
            candidates.append(path)

        wanted = set(candidate_filenames_for_alias(alias))
        for candidate in reversed(candidates):
            if candidate.exists() and artifact_path_matches_alias(str(candidate), alias):
                return candidate
            if candidate.exists() and candidate.name.lower() in wanted:
                return candidate

        for candidate in reversed(candidates):
            if not candidate.exists() or not candidate.is_dir():
                continue
            for basename in wanted:
                direct = candidate / basename
                if direct.exists() and (direct.is_file() or direct.is_dir()):
                    return direct
            for child in candidate.rglob("*"):
                if child.name.lower() in wanted and (child.is_file() or child.is_dir()):
                    return child
        return None

    def _failures_are_validated_explicit_publish_failures(
        self,
        *,
        node: PlanNode,
        failures: Sequence[Dict[str, Any]],
        artifact_schema_results: Dict[str, Dict[str, Any]],
    ) -> bool:
        if not failures:
            return False
        provenance = resolve_artifact_contract_with_provenance(
            task_name=node.display_name(),
            instruction=node.instruction or "",
            metadata=node.metadata if isinstance(node.metadata, dict) else {},
        )
        aliases = [
            str(alias).strip()
            for alias in provenance.explicit_publishes
            if str(alias).strip()
        ]
        if not aliases:
            return False

        for alias in aliases:
            result = artifact_schema_results.get(alias)
            if not isinstance(result, dict):
                return False
            if not bool(result.get("validated") and result.get("schema_valid")):
                return False

        return all(
            self._artifact_output_presence_failure_matches_alias(failure, aliases)
            for failure in failures
        )

    @staticmethod
    def _artifact_output_presence_failure_matches_alias(
        failure: Dict[str, Any],
        aliases: Sequence[str],
    ) -> bool:
        TaskVerificationService = _facade().TaskVerificationService
        if not isinstance(failure, dict):
            return False
        allowed = {
            "file_exists",
            "file_nonempty",
            "glob_count_at_least",
            "model_metrics_valid",
        }
        check_type = str(failure.get("type") or "").strip()
        if check_type not in allowed:
            return False
        target = str(failure.get("path") or failure.get("glob") or "").strip()
        if not target:
            return False
        return any(
            TaskVerificationService._artifact_failure_target_matches_alias(target, alias)
            for alias in aliases
        )

    @staticmethod
    def _artifact_failure_target_matches_alias(target: str, alias: str) -> bool:
        target_text = str(target or "").strip()
        alias_text = str(alias or "").strip()
        if not target_text or not alias_text:
            return False
        if target_text == alias_text:
            return True
        if artifact_path_matches_alias(target_text, alias_text):
            return True

        normalized = target_text.replace(chr(92), "/").strip("/").lower()
        alias_normalized = alias_text.lower()
        if normalized == alias_normalized:
            return True
        padded = f"/{normalized}/"
        for candidate in candidate_filenames_for_alias(alias_text):
            candidate_text = str(candidate or "").replace(chr(92), "/").strip("/").lower()
            if not candidate_text:
                continue
            candidate_basename = candidate_text.rsplit("/", 1)[-1]
            if normalized == candidate_text or normalized.endswith(f"/{candidate_text}"):
                return True
            if f"/{candidate_text}/" in padded:
                return True
            if candidate_basename and (
                normalized == candidate_basename
                or normalized.endswith(f"/{candidate_basename}")
                or f"/{candidate_basename}/" in padded
            ):
                return True
        return False
