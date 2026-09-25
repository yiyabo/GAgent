"""Output/source discovery cluster of ``TaskVerificationService``.

Moved verbatim out of ``task_verification.py`` per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.6 (TV cluster ④):
failed-task output discovery (``_diagnose_file_state`` …
``_infer_candidate_roots``), source-discovery verification for
locate/find/inventory tasks (``_source_discovery_verification`` …
``_match_source_discovery_target``) and disk-scanning output discovery plus
expected-output matching (``_actual_outputs`` … ``_build_plan_patch_suggestion``).
Composed into ``TaskVerificationService`` as the ``_DiscoveryMethods`` mixin, so
every ``self.*``/``cls.*`` call site is unchanged.

Late binding: four helpers read sibling methods through the class
(``TaskVerificationService._is_internal_artifact_path`` /
``._is_output_evidence_file`` / ``._normalize_glob_text``).  The class lives in
the facade, which imports this module at import time, so each of them binds
``TaskVerificationService = _facade().TaskVerificationService`` at call time —
four added lines, the only deviation from byte-verbatim here; the original call
expressions are untouched.

This module has no logger of its own (no logging calls in the moved code).
"""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .acceptance_criteria import resolve_glob_pattern
from .plan_models import PlanNode
from .verification_cues import (
    _NON_DELIVERABLE_SUFFIXES,
    _OUTPUT_DISCOVERY_DIR_NAMES,
    _SCAFFOLDING_DIR_NAMES,
    _SOURCE_DISCOVERY_CONTEXT_CUES,
    _SOURCE_DISCOVERY_LINE_FOUND_CUES,
    _SOURCE_DISCOVERY_LINE_MISSING_CUES,
    _SOURCE_DISCOVERY_PATH_CHECKS,
    _SOURCE_DISCOVERY_POSITIVE_CUES,
)


def _facade() -> Any:
    """Late-bound task_verification facade module (monkeypatch-friendly lookups)."""
    from . import task_verification

    return task_verification


class _DiscoveryMethods:
    """Output/source discovery cluster of ``TaskVerificationService`` (mixin)."""

    def _diagnose_file_state(
        self,
        raw_path: Any,
        base_dir: Path,
        artifact_paths: Sequence[str] = (),
    ) -> Dict[str, Any]:
        raw_str = str(raw_path or "").strip()
        candidates: List[Path] = []
        if raw_str:
            candidates.append((base_dir / raw_str).resolve())
            candidates.append(Path(raw_str).expanduser().resolve())
        for ap in artifact_paths:
            p = Path(str(ap or "")).expanduser()
            candidates.append(p)
            if p.is_dir():
                name = Path(raw_str).name if raw_str else ""
                if name:
                    candidates.append(p / name)
        for p in candidates:
            try:
                if p.exists() and p.is_file() and p.stat().st_size > 0:
                    size = p.stat().st_size
                    ext = p.suffix.lower().lstrip(".")
                    fmt = "json" if ext in ("json", "jsonl") else ext or "unknown"
                    return {
                        "found": True,
                        "found_at": str(p),
                        "size_bytes": size,
                        "size_mb": size / 1048576,
                        "format": fmt,
                        "extension": ext,
                    }
            except OSError:
                continue
        return {"found": False}

    def _has_output_evidence(self, artifact_paths: Sequence[str]) -> bool:
        for raw_path in artifact_paths:
            path = Path(str(raw_path or "")).expanduser()
            try:
                if self._is_output_evidence_file(path):
                    return True
                if path.exists() and path.is_dir():
                    for child in path.rglob("*"):
                        if self._is_output_evidence_file(child):
                            return True
            except OSError:
                continue
        return False

    def _fallback_discover_outputs_for_failed_task(
        self,
        *,
        node: PlanNode,
        payload: Optional[Dict[str, Any]],
        existing_paths: Sequence[str],
    ) -> List[str]:
        """Fallback discovery for failed tasks: extract paths from instruction text.
        
        This complements ToolOutputResolver by handling edge cases where:
        - LLMs ignore instructions and write to unexpected locations
        - Promotion logic (_promote_project_level_strays) doesn't catch files
        - Task instruction mentions specific paths we can extract
        
        ToolOutputResolver handles the common case (session-scoped paths), while
        this fallback handles the uncommon case (unexpected locations).
        """
        from app.services.plans.acceptance_criteria import extract_explicit_deliverables_from_text

        instruction = getattr(node, "instruction", None)
        if not isinstance(instruction, str) or not instruction.strip():
            return []

        mentioned_paths = extract_explicit_deliverables_from_text(instruction)
        if not mentioned_paths:
            return []

        candidate_roots = self._infer_candidate_roots(payload)
        if not candidate_roots:
            return []

        discovered: List[str] = []
        seen = set(existing_paths)

        for raw_path in mentioned_paths:
            path = Path(raw_path).expanduser()
            if path.is_absolute():
                try:
                    if path.exists() and path.is_file() and path.stat().st_size > 0:
                        resolved = str(path.resolve())
                        if resolved not in seen:
                            seen.add(resolved)
                            discovered.append(resolved)
                except OSError:
                    continue
            else:
                for root in candidate_roots:
                    candidate = root / path
                    try:
                        if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
                            resolved = str(candidate.resolve())
                            if resolved not in seen:
                                seen.add(resolved)
                                discovered.append(resolved)
                                break
                    except OSError:
                        continue

        return discovered

    @staticmethod
    def _infer_candidate_roots(payload: Optional[Dict[str, Any]]) -> List[Path]:
        if not isinstance(payload, dict):
            return []

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return []

        run_dir_str = metadata.get("run_directory") or metadata.get("working_directory")
        if not isinstance(run_dir_str, str) or not run_dir_str.strip():
            return []

        run_dir = Path(run_dir_str).expanduser()
        if not run_dir.exists():
            return []

        roots: List[Path] = []
        seen: set[str] = set()

        current = run_dir
        for _ in range(10):
            parent = current.parent
            if parent == current:
                break
            if (parent / "results").is_dir() or (parent / "output").is_dir():
                key = str(parent)
                if key not in seen:
                    seen.add(key)
                    roots.append(parent)
            current = parent

        return roots

    def _source_discovery_verification(
        self,
        *,
        node: PlanNode,
        criteria: Optional[Dict[str, Any]],
        failures: Sequence[Dict[str, Any]],
        artifact_paths: Sequence[str],
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Verify source-discovery tasks by found-file evidence, not stale source paths.

        A locate/search/inventory task is allowed to complete when it reports
        where existing inputs actually live.  This intentionally does *not*
        relax copy/generate tasks: those must still materialize their declared
        deliverables at the requested output path.
        """
        if not self._looks_like_source_discovery_task(node):
            return None
        if not self._failures_are_source_path_checks(failures):
            return None

        expected_targets = self._source_discovery_expected_targets(criteria)
        if not expected_targets:
            return None

        discovered_files = self._source_discovery_discovered_files(artifact_paths)
        evidence_text = self._source_discovery_payload_text(payload)
        evidence_lines = [line.strip() for line in evidence_text.splitlines() if line.strip()]

        matched: List[Dict[str, Any]] = []
        missing: List[str] = []
        format_alternatives: List[Dict[str, str]] = []

        for expected in expected_targets:
            match = self._match_source_discovery_target(
                expected,
                discovered_files=discovered_files,
                evidence_lines=evidence_lines,
            )
            if match is None:
                missing.append(expected)
                continue
            matched.append(match)
            if match.get("match_type") == "alternate_format":
                format_alternatives.append({
                    "expected": str(match.get("expected") or expected),
                    "actual": str(match.get("actual") or ""),
                })

        if missing:
            return {
                "status": "failed",
                "expected": expected_targets,
                "matched": matched,
                "missing": missing,
            }

        warnings: List[str] = []
        if format_alternatives:
            warnings.append(
                "Some discovered source files use a different format than the stale acceptance criteria; downstream tasks must convert or preserve the alternate format."
            )

        return {
            "status": "passed",
            "summary": "source discovery found all expected file identities",
            "expected": expected_targets,
            "matched": matched,
            "format_alternatives": format_alternatives,
            "warnings": warnings,
        }

    @staticmethod
    def _looks_like_source_discovery_task(node: PlanNode) -> bool:
        text = "\n".join(
            part
            for part in (
                str(getattr(node, "name", "") or ""),
                str(getattr(node, "instruction", "") or ""),
            )
            if part
        ).lower()
        if not text:
            return False
        has_discovery_cue = any(cue in text for cue in _SOURCE_DISCOVERY_POSITIVE_CUES)
        has_context_cue = any(cue in text for cue in _SOURCE_DISCOVERY_CONTEXT_CUES)
        return has_discovery_cue and has_context_cue

    @staticmethod
    def _failures_are_source_path_checks(failures: Sequence[Dict[str, Any]]) -> bool:
        if not failures:
            return False
        for failure in failures:
            if not isinstance(failure, dict):
                return False
            check_type = str(failure.get("type") or "").strip().lower()
            if check_type not in _SOURCE_DISCOVERY_PATH_CHECKS:
                return False
        return True

    @staticmethod
    def _source_discovery_expected_targets(criteria: Optional[Dict[str, Any]]) -> List[str]:
        targets: List[str] = []
        seen: set[str] = set()
        checks = criteria.get("checks") if isinstance(criteria, dict) else None
        if not isinstance(checks, list):
            return targets
        for raw_check in checks:
            if not isinstance(raw_check, dict):
                continue
            check_type = str(raw_check.get("type") or "").strip().lower()
            if check_type not in _SOURCE_DISCOVERY_PATH_CHECKS:
                continue
            candidate = str(raw_check.get("path") or "").strip()
            if not candidate or any(token in candidate for token in ("*", "?", "[")):
                continue
            name = Path(candidate).name
            if not name or "." not in name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            targets.append(name)
        return targets

    @staticmethod
    def _source_discovery_discovered_files(artifact_paths: Sequence[str]) -> List[Path]:
        discovered: List[Path] = []
        seen: set[str] = set()
        for raw in artifact_paths:
            path = Path(str(raw or "")).expanduser()
            try:
                exists = path.exists() and path.is_file()
            except OSError:
                exists = False
            if not exists:
                continue
            try:
                resolved = path.resolve()
            except Exception:
                resolved = path
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            discovered.append(resolved)
        return discovered

    @staticmethod
    def _source_discovery_payload_text(payload: Dict[str, Any]) -> str:
        parts: List[str] = []
        content = payload.get("content") if isinstance(payload, dict) else None
        if isinstance(content, str) and content.strip():
            parts.append(content)
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        if isinstance(metadata, dict):
            for key in ("plot_inventory", "file_inventory", "inventory", "source_inventory"):
                value = metadata.get(key)
                if value is not None:
                    try:
                        parts.append(json.dumps(value, ensure_ascii=False))
                    except TypeError:
                        parts.append(str(value))
        return "\n".join(parts)

    def _match_source_discovery_target(
        self,
        expected: str,
        *,
        discovered_files: Sequence[Path],
        evidence_lines: Sequence[str],
    ) -> Optional[Dict[str, Any]]:
        expected_name = Path(expected).name.lower()
        expected_stem = Path(expected_name).stem.lower()
        expected_suffix = Path(expected_name).suffix.lower()

        for actual in discovered_files:
            actual_name = actual.name.lower()
            if actual_name == expected_name:
                return {
                    "expected": expected,
                    "actual": str(actual),
                    "match_type": "exact_path_artifact",
                }

        for actual in discovered_files:
            if actual.stem.lower() == expected_stem and actual.suffix.lower() != expected_suffix:
                return {
                    "expected": expected,
                    "actual": str(actual),
                    "match_type": "alternate_format",
                }

        for line in evidence_lines:
            lowered = line.lower()
            if expected_name not in lowered and expected_stem not in lowered:
                continue
            if any(cue in lowered for cue in _SOURCE_DISCOVERY_LINE_MISSING_CUES):
                continue
            if any(cue in lowered for cue in _SOURCE_DISCOVERY_LINE_FOUND_CUES):
                match_type = "text_reported_found"
                if expected_name not in lowered:
                    match_type = "alternate_format"
                return {
                    "expected": expected,
                    "actual": line[:500],
                    "match_type": match_type,
                }

        return None

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

    def _augment_artifact_paths_with_discovered_outputs(
        self,
        *,
        node: PlanNode,
        criteria: Optional[Dict[str, Any]],
        payload: Optional[Dict[str, Any]],
        artifact_paths: Sequence[str],
        base_dir: Path,
    ) -> List[str]:
        has_explicit_base_dir = bool(
            isinstance(criteria, dict)
            and isinstance(criteria.get("base_dir"), str)
            and str(criteria.get("base_dir") or "").strip()
        )
        payload_base_candidates = self._payload_base_dir_candidates(payload)
        task_base_candidates = self._task_raw_files_base_dir_candidates(
            node=node,
            payload=payload,
            artifact_paths=artifact_paths,
        )
        if not artifact_paths and not payload_base_candidates and not task_base_candidates and not has_explicit_base_dir:
            return []

        paths: List[str] = []
        seen: set[str] = set()

        def _add_raw(value: Any) -> None:
            if not isinstance(value, str) or not value.strip():
                return
            text = value.strip()
            if text in seen or not self._is_local_path(text) or self._is_internal_artifact_path(text):
                return
            seen.add(text)
            paths.append(text)

        def _add_path(path: Path) -> None:
            if self._should_include_discovered_output(path):
                try:
                    resolved = path.expanduser().resolve()
                except Exception:
                    resolved = path.expanduser()
                text = str(resolved)
                if text not in seen:
                    seen.add(text)
                    paths.append(text)

        def _add_existing(raw: Any) -> None:
            if not isinstance(raw, str) or not raw.strip():
                return
            raw_path = Path(raw.strip()).expanduser()
            if not raw_path.is_absolute():
                raw_path = base_dir / raw_path
            if raw_path.exists() and raw_path.is_file():
                _add_path(raw_path)
            elif raw_path.exists() and raw_path.is_dir():
                self._collect_discovered_output_files(raw_path, add=_add_path)

        for raw in artifact_paths:
            _add_raw(raw)
            _add_existing(raw)

        roots = self._output_discovery_roots(
            node=node,
            criteria=criteria,
            payload=payload,
            artifact_paths=artifact_paths,
            base_dir=base_dir,
            payload_base_candidates=payload_base_candidates,
            task_base_candidates=task_base_candidates,
            include_base_dir=has_explicit_base_dir or bool(artifact_paths),
        )
        for root in roots:
            self._collect_discovered_output_files(root, add=_add_path)

        return paths[:80]

    def _output_discovery_roots(
        self,
        *,
        node: PlanNode,
        criteria: Optional[Dict[str, Any]],
        payload: Optional[Dict[str, Any]],
        artifact_paths: Sequence[str],
        base_dir: Path,
        payload_base_candidates: Sequence[Path],
        task_base_candidates: Sequence[Path],
        include_base_dir: bool,
    ) -> List[Path]:
        roots: List[Path] = []
        seen: set[str] = set()

        def _add_root(candidate: Path) -> None:
            try:
                path = candidate.expanduser().resolve()
            except Exception:
                path = candidate.expanduser()
            if not path.exists() or not path.is_dir():
                return
            # Filesystem-level roots (e.g. "/" or a mount point such as
            # "/data") are never task-scoped output roots; scanning them
            # would crawl unrelated system directories.
            if len(path.parts) <= 2:
                return
            key = str(path)
            if key in seen:
                return
            seen.add(key)
            roots.append(path)

        if include_base_dir:
            _add_root(base_dir)
        for candidate in payload_base_candidates:
            _add_root(candidate)
        for candidate in task_base_candidates:
            _add_root(candidate)
        inferred = self._infer_relative_output_base_dir(criteria, artifact_paths)
        if inferred is not None:
            _add_root(inferred)
        for raw in artifact_paths:
            path = Path(str(raw or "")).expanduser()
            if path.exists() and path.is_dir():
                _add_root(path)
            elif path.exists() and path.is_file():
                _add_root(path.parent)

        expanded: List[Path] = list(roots)
        for root in list(roots):
            for dirname in _OUTPUT_DISCOVERY_DIR_NAMES:
                candidate = root / dirname
                if candidate.exists() and candidate.is_dir():
                    expanded.append(candidate)
        roots = []
        seen.clear()
        for root in expanded:
            _add_root(root)
        return roots

    def _collect_discovered_output_files(self, root: Path, *, add) -> None:
        if root.name.lower() in _OUTPUT_DISCOVERY_DIR_NAMES:
            for child in root.rglob("*"):
                add(child)
            return
        for dirname in _OUTPUT_DISCOVERY_DIR_NAMES:
            candidate = root / dirname
            if candidate.exists() and candidate.is_dir():
                for child in candidate.rglob("*"):
                    add(child)

    @staticmethod
    def _is_output_evidence_file(path: Path) -> bool:
        """Non-empty, non-junk deliverable file; unlike discovery it ignores directory name."""
        TaskVerificationService = _facade().TaskVerificationService
        try:
            if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
                return False
        except OSError:
            return False
        if path.suffix.lower() in _NON_DELIVERABLE_SUFFIXES:
            return False
        if path.name.lower().endswith("_code_executor.log") or path.name.lower().endswith("_claude_debug.log"):
            return False
        normalized = str(path).replace("\\", "/")
        if TaskVerificationService._is_internal_artifact_path(normalized):
            return False
        if {part.lower() for part in path.parts} & _SCAFFOLDING_DIR_NAMES:
            return False
        return True

    @staticmethod
    def _should_include_discovered_output(path: Path) -> bool:
        TaskVerificationService = _facade().TaskVerificationService
        if not TaskVerificationService._is_output_evidence_file(path):
            return False
        parts = {part.lower() for part in path.parts}
        return bool(parts & _OUTPUT_DISCOVERY_DIR_NAMES)

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
            if check_type in {
                "file_exists",
                "file_nonempty",
                "text_contains",
                "json_field_equals",
                "json_field_at_least",
                "pdf_valid",
                "model_metrics_valid",
            }:
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
        TaskVerificationService = _facade().TaskVerificationService
        normalized_output = TaskVerificationService._normalize_glob_text(output)
        output_candidates = [normalized_output]
        if normalized_output:
            suffix_source = normalized_output[1:] if normalized_output.startswith("/") else normalized_output
            parts = [part for part in suffix_source.split("/") if part and part != "."]
            for index in range(1, len(parts)):
                output_candidates.append("/".join(parts[index:]))
        output_candidates = [candidate for candidate in dict.fromkeys(output_candidates) if candidate]
        for expected in expected_deliverables:
            text = TaskVerificationService._normalize_glob_text(expected)
            if not text:
                continue
            if any(token in text for token in ("*", "?", "[")):
                if any(fnmatch.fnmatch(candidate, text) for candidate in output_candidates):
                    return True
            elif text in output_candidates:
                return True
        return False

    @staticmethod
    def _deliverable_identity_parts(value: Any) -> tuple[str, str, str]:
        TaskVerificationService = _facade().TaskVerificationService
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
