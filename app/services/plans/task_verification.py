from __future__ import annotations

import copy
import csv
import fnmatch
import glob
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .acceptance_criteria import (
    derive_expected_deliverables,
    derive_relative_output_dirs,
    resolve_glob_min_count,
    resolve_glob_pattern,
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


@dataclass
class VerificationFinalization:
    final_status: str
    execution_status: str
    payload: Dict[str, Any]
    verification: Optional[Dict[str, Any]] = None
    artifact_paths: List[str] = field(default_factory=list)


class TaskVerificationService:
    """Deterministic verification gate for file/data-oriented task results."""

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

        explicit_criteria = self._explicit_acceptance_criteria(node)
        generated = False
        effective_criteria = explicit_criteria

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
        metadata["verification"] = verification
        normalized_payload["metadata"] = metadata
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
            whatever is stored in the task's metadata.  When an LLM passes
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

        raw_payload = self._parse_execution_result(node.execution_result, fallback_status=node.status)
        finalization = self.finalize_payload(
            node,
            raw_payload,
            execution_status=raw_payload.get("metadata", {}).get("execution_status")
            if isinstance(raw_payload.get("metadata"), dict)
            else raw_payload.get("status"),
            trigger=trigger,
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

    def _explicit_acceptance_criteria(self, node: PlanNode) -> Optional[Dict[str, Any]]:
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        criteria = metadata.get("acceptance_criteria")
        if isinstance(criteria, dict):
            return copy.deepcopy(criteria)
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
                    return copy.deepcopy(criteria)
        return None

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

        for raw in artifact_paths:
            ap = Path(raw)
            if not ap.exists() or not ap.is_file():
                continue
            if basename_hit is None and ap.name.lower() == expected_name:
                basename_hit = ap

        if basename_hit:
            logger.info("[Verification] Fallback basename match: %s -> %s", expected_path, basename_hit)
            return basename_hit

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
        missing_suffixes = {
            Path(item).suffix.lower()
            for item in missing_required_outputs
            if Path(str(item)).suffix
        }
        wrong_format_outputs = [
            output
            for output in unexpected_outputs
            if Path(output).suffix.lower() in missing_suffixes
        ]
        return {
            "expected_deliverables": expected_deliverables,
            "actual_outputs": actual_outputs,
            "missing_required_outputs": missing_required_outputs,
            "wrong_format_outputs": wrong_format_outputs,
            "unexpected_outputs": unexpected_outputs,
        }

    def _expected_deliverables(self, criteria: Optional[Dict[str, Any]]) -> List[str]:
        return derive_expected_deliverables(criteria)

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
