"""Check execution cluster of ``TaskVerificationService``.

Moved verbatim out of ``task_verification.py`` per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.6 (TV cluster ③):
``_run_check`` (the 11-way ``check_type`` string router) and the check
implementations it dispatches to — artifact fallback matching/globbing,
check normalization, JSON tabular coercion, ``pdf_valid``,
``model_metrics_valid`` and ``manuscript_markdown_quality``.  Composed into
``TaskVerificationService`` as the ``_CheckMethods`` mixin, so every ``self.*``
call site is unchanged.

Late binding: ``_pdf_valid_result``, ``_model_metrics_valid_result`` and
``_manuscript_markdown_quality_result`` build their records through
``TaskVerificationService._check_result(...)``; the class lives in the facade
(which imports this module at import time), so each of those three binds
``TaskVerificationService = _facade().TaskVerificationService`` at call time.
That is the only deviation from byte-verbatim here (three added lines; the build
call expressions stay intact).  Module-level PEP 562 ``__getattr__`` is NOT a
valid substitute — it does not cover in-function global lookups.

The module uses its own ``logging.getLogger(__name__)`` (split precedent).
"""

from __future__ import annotations

import csv
import fnmatch
import glob
import importlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .acceptance_criteria import resolve_glob_min_count, resolve_glob_pattern
from .artifact_contracts import artifact_path_matches_alias, is_artifact_alias
from .model_metric_schema import (
    collect_metric_model_entries,
    is_tree_model_entry,
    missing_required_model_metrics,
)
from .verification_cues import _TABULAR_ROW_COUNT_KEYS

logger = logging.getLogger(__name__)


def _facade() -> Any:
    """Late-bound task_verification facade module (monkeypatch-friendly lookups)."""
    from . import task_verification

    return task_verification


class _CheckMethods:
    """Check execution cluster of ``TaskVerificationService`` (mixin)."""

    def _run_check(self, raw_check: Any, *, base_dir: Path, artifact_paths: Sequence[str] = ()) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_check, dict):
            return self._verification_config_error("invalid_check", "Check definition must be an object.")

        raw_check = self._normalize_check(raw_check)

        check_type = str(raw_check.get("type") or "").strip()
        if not check_type:
            return self._verification_config_error("invalid_check", "Check definition is missing `type`.")

        try:
            if check_type == "file_exists":
                if not self._has_nonempty_string(raw_check.get("path")):
                    return self._verification_config_error(check_type, "Check is missing a valid `path`.")
                path = self._select_path_for_check(raw_check.get("path"), base_dir, artifact_paths, mode="exists")
                success = path.exists()
                if not success and artifact_paths:
                    fallback = self._fallback_artifact_match(path, artifact_paths)
                    if fallback:
                        path = fallback
                        success = True
                return self._check_result(check_type, success, path=path, message=None if success else "File does not exist.")
            if check_type == "file_nonempty":
                if not self._has_nonempty_string(raw_check.get("path")):
                    return self._verification_config_error(check_type, "Check is missing a valid `path`.")
                path = self._select_path_for_check(raw_check.get("path"), base_dir, artifact_paths, mode="nonempty_file")
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
                    return self._verification_config_error(check_type, "Check is missing a valid `glob`.")
                patterns = self._resolve_glob_patterns(raw_glob, base_dir, artifact_paths)
                matched = self._glob_matches(patterns)
                success = len(matched) > 0
                return {
                    "type": check_type,
                    "success": success,
                    "glob": patterns[0],
                    "glob_patterns": patterns,
                    "count": len(matched),
                    "message": None if success else "No files matched glob pattern.",
                }
            if check_type == "glob_count_at_least":
                raw_glob = resolve_glob_pattern(raw_check)
                if not raw_glob:
                    return self._verification_config_error(check_type, "Check is missing a valid `glob`.")
                min_count = resolve_glob_min_count(raw_check, default=1)
                patterns = self._resolve_glob_patterns(raw_glob, base_dir, artifact_paths)
                pattern = patterns[0]
                matched = self._glob_matches(patterns)
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
                    "glob_patterns": patterns,
                    "count": len(matched),
                    "message": None if success else f"Matched {len(matched)} items, expected at least {min_count}.",
                }
            if check_type == "text_contains":
                if not self._has_nonempty_string(raw_check.get("path")):
                    return self._verification_config_error(check_type, "Check is missing a valid `path`.")
                path = self._select_path_for_check(raw_check.get("path"), base_dir, artifact_paths, mode="exists")
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
                if not self._has_nonempty_string(raw_check.get("path")):
                    return self._verification_config_error(check_type, "Check is missing a valid `path`.")
                path = self._select_path_for_check(raw_check.get("path"), base_dir, artifact_paths, mode="exists")
                key_path = self._coerce_json_key_path(raw_check)
                if not path.exists() and artifact_paths:
                    fallback = self._fallback_artifact_match(path, artifact_paths, lenient=False)
                    if fallback:
                        path = fallback
                if not path.exists():
                    diagnostic = self._diagnose_file_state(raw_check.get("path"), base_dir, artifact_paths)
                    if diagnostic.get("found"):
                        return {
                            "type": check_type,
                            "success": False,
                            "path": str(path),
                            "key_path": key_path,
                            "message": (
                                f"File exists at {diagnostic['found_at']} "
                                f"({diagnostic['size_mb']:.1f} MB, {diagnostic['format']} format) "
                                f"but check type '{check_type}' expects JSON. "
                                f"Output is present — consider 'file_exists' or 'file_nonempty' check."
                            ),
                            "diagnostic": diagnostic,
                            "format_mismatch": True,
                        }
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
                        return self._verification_config_error(check_type, "json_field_at_least check is missing `min_value`.")
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
                if not self._has_nonempty_string(raw_check.get("path")):
                    return self._verification_config_error(check_type, "Check is missing a valid `path`.")
                path = self._select_path_for_check(raw_check.get("path"), base_dir, artifact_paths, mode="exists")
                residue = str(raw_check.get("residue") or "").strip().upper()
                success = self._pdb_residue_present(path, residue)
                return self._check_result(
                    check_type,
                    success,
                    path=path,
                    message=None if success else f"{residue} residue not found in structure records.",
                    extra={"residue": residue},
                )
            if check_type == "pdf_valid":
                if not self._has_nonempty_string(raw_check.get("path")):
                    return self._verification_config_error(check_type, "Check is missing a valid `path`.")
                path = self._select_path_for_check(raw_check.get("path"), base_dir, artifact_paths, mode="exists")
                if not path.exists() and artifact_paths:
                    fallback = self._fallback_artifact_match(path, artifact_paths, lenient=False)
                    if fallback:
                        path = fallback
                return self._pdf_valid_result(path, raw_check)
            if check_type == "model_metrics_valid":
                if not self._has_nonempty_string(raw_check.get("path")):
                    return self._verification_config_error(check_type, "Check is missing a valid `path`.")
                path = self._select_path_for_check(raw_check.get("path"), base_dir, artifact_paths, mode="exists")
                if not path.exists() and artifact_paths:
                    fallback = self._fallback_artifact_match(path, artifact_paths, lenient=False)
                    if fallback:
                        path = fallback
                return self._model_metrics_valid_result(path)
            if check_type == "manuscript_markdown_quality":
                if not self._has_nonempty_string(raw_check.get("path")):
                    return self._verification_config_error(check_type, "Check is missing a valid `path`.")
                path = self._select_path_for_check(raw_check.get("path"), base_dir, artifact_paths, mode="exists")
                if not path.exists() and artifact_paths:
                    fallback = self._fallback_artifact_match(path, artifact_paths, lenient=False)
                    if fallback:
                        path = fallback
                return self._manuscript_markdown_quality_result(path, raw_check)
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
            "failure_kind": "verification_config_error",
            "verification_config_error": True,
            "message": f"Unsupported verification check type: {check_type}",
        }

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

        artifact_dirs: List[Path] = []
        for raw in artifact_paths:
            ap = Path(raw)
            if not ap.exists():
                continue
            if ap.is_dir():
                artifact_dirs.append(ap)
                continue
            if not ap.is_file():
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

        for artifact_dir in reversed(artifact_dirs):
            direct = artifact_dir / expected_path.name
            if direct.exists() and direct.is_file():
                logger.info(
                    "[Verification] Fallback basename match inside artifact directory: %s -> %s",
                    expected_path,
                    direct,
                )
                return direct

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
        basename_patterns = self._promoted_artifact_basename_patterns(patterns)
        for raw in artifact_paths:
            artifact = Path(str(raw)).expanduser()
            if not artifact.exists() or not artifact.is_file():
                continue
            candidates = self._artifact_glob_match_candidates(artifact, base_dir=base_dir)
            direct_match = any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates for pattern in patterns)
            flattened_promoted_match = (
                not direct_match
                and basename_patterns
                and self._looks_like_promoted_task_artifact(artifact)
                and any(fnmatch.fnmatch(artifact.name, pattern) for pattern in basename_patterns)
            )
            if direct_match or flattened_promoted_match:
                artifact_text = str(artifact)
                if artifact_text not in seen_matches:
                    seen_matches.add(artifact_text)
                    matched.append(artifact_text)
        return matched

    @staticmethod
    def _promoted_artifact_basename_patterns(patterns: Sequence[str]) -> List[str]:
        basename_patterns: List[str] = []
        seen: set[str] = set()
        for pattern in patterns:
            text = str(pattern or "").strip().replace("\\", "/")
            if not text or not glob.has_magic(text):
                continue
            basename = text.rsplit("/", 1)[-1]
            if not basename or basename == text or not glob.has_magic(basename):
                continue
            if basename not in seen:
                seen.add(basename)
                basename_patterns.append(basename)
        return basename_patterns

    @staticmethod
    def _looks_like_promoted_task_artifact(path: Path) -> bool:
        parts = [part.lower() for part in path.parts]
        if "raw_files" not in parts:
            return False
        return any(part.startswith("task_") for part in parts)

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
    def _pdf_valid_result(path: Path, raw_check: Dict[str, Any]) -> Dict[str, Any]:
        TaskVerificationService = _facade().TaskVerificationService
        min_pages = int(raw_check.get("min_pages") or 1)
        min_text_chars = int(raw_check.get("min_text_chars") or 0)
        if not path.exists() or not path.is_file():
            return TaskVerificationService._check_result(
                "pdf_valid",
                False,
                path=path,
                message="PDF file does not exist.",
            )
        if path.stat().st_size <= 0:
            return TaskVerificationService._check_result(
                "pdf_valid",
                False,
                path=path,
                message="PDF file is empty.",
            )
        with path.open("rb") as handle:
            header = handle.read(5)
        if header != b"%PDF-":
            return TaskVerificationService._check_result(
                "pdf_valid",
                False,
                path=path,
                message="File does not start with a PDF header.",
                extra={"header": header.decode("latin-1", errors="replace")},
            )

        pages: Optional[int] = None
        text_chars: Optional[int] = None
        if min_pages > 0 or min_text_chars > 0:
            try:
                pypdf = importlib.import_module("pypdf")

                with path.open("rb") as handle:
                    reader = pypdf.PdfReader(handle)
                    pages = len(reader.pages)
                    if min_text_chars > 0:
                        extracted: List[str] = []
                        for page in reader.pages[:20]:
                            extracted.append(page.extract_text() or "")
                        text_chars = len("\n".join(extracted).strip())
            except Exception as exc:
                return TaskVerificationService._check_result(
                    "pdf_valid",
                    False,
                    path=path,
                    message=f"Unable to parse PDF: {exc}",
                )

        if pages is not None and pages < min_pages:
            return TaskVerificationService._check_result(
                "pdf_valid",
                False,
                path=path,
                message=f"Expected at least {min_pages} PDF page(s), got {pages}.",
                extra={"pages": pages, "min_pages": min_pages},
            )
        if text_chars is not None and text_chars < min_text_chars:
            return TaskVerificationService._check_result(
                "pdf_valid",
                False,
                path=path,
                message=f"Expected at least {min_text_chars} extracted text characters, got {text_chars}.",
                extra={"pages": pages, "text_chars": text_chars, "min_text_chars": min_text_chars},
            )
        return TaskVerificationService._check_result(
            "pdf_valid",
            True,
            path=path,
            message=None,
            extra={"pages": pages, "text_chars": text_chars},
        )

    @staticmethod
    def _model_metrics_valid_result(path: Path) -> Dict[str, Any]:
        TaskVerificationService = _facade().TaskVerificationService
        if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
            return TaskVerificationService._check_result(
                "model_metrics_valid",
                False,
                path=path,
                message="Metrics JSON is missing or empty.",
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        model_entries = collect_metric_model_entries(payload)
        valid_models: List[str] = []
        missing: Dict[str, List[str]] = {}
        for name, metrics in model_entries.items():
            missing_fields = list(missing_required_model_metrics(metrics))
            if missing_fields:
                missing[name] = missing_fields
            else:
                valid_models.append(name)

        has_tree_model = any(
            is_tree_model_entry(name, model_entries[name])
            for name in valid_models
        )
        success = len(valid_models) >= 1 and has_tree_model
        message = None
        if not success:
            message = (
                "Metrics JSON must contain at least one tree-model entry with numeric "
                "accuracy and macro_f1 values."
            )
        return TaskVerificationService._check_result(
            "model_metrics_valid",
            success,
            path=path,
            message=message,
            extra={"valid_models": valid_models, "missing_metrics": missing},
        )

    @staticmethod
    def _manuscript_markdown_quality_result(path: Path, raw_check: Dict[str, Any]) -> Dict[str, Any]:
        TaskVerificationService = _facade().TaskVerificationService
        if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
            return TaskVerificationService._check_result(
                "manuscript_markdown_quality",
                False,
                path=path,
                message="Markdown manuscript is missing or empty.",
            )

        text = path.read_text(encoding="utf-8", errors="ignore")
        min_text_chars = int(raw_check.get("min_text_chars") or 0)
        min_sections = int(raw_check.get("min_sections") or 0)
        min_long_paragraphs = int(raw_check.get("min_long_paragraphs") or 0)
        raw_max_bullet_ratio = raw_check.get("max_bullet_ratio")
        max_bullet_ratio = float(raw_max_bullet_ratio if raw_max_bullet_ratio is not None else 1.0)
        min_figure_callouts = int(raw_check.get("min_figure_callouts") or 0)
        min_table_callouts = int(raw_check.get("min_table_callouts") or 0)
        min_results_subsections = int(raw_check.get("min_results_subsections") or 0)
        required_terms = raw_check.get("required_terms") or []
        if isinstance(required_terms, str):
            required_terms = [term.strip() for term in required_terms.split(",") if term.strip()]

        nonempty_lines = [line.strip() for line in text.splitlines() if line.strip()]
        bullet_lines = [
            line for line in nonempty_lines
            if re.match(r"^(?:[-*+•]|\d+[.)])\s+", line)
        ]
        bullet_ratio = len(bullet_lines) / max(len(nonempty_lines), 1)
        headings = re.findall(r"(?m)^#{1,4}\s+(.+?)\s*$", text)
        paragraph_blocks = [
            block.strip() for block in re.split(r"\n\s*\n", text)
            if block.strip() and not block.lstrip().startswith("#") and not block.lstrip().startswith("|")
        ]
        long_paragraphs = [block for block in paragraph_blocks if len(re.sub(r"\s+", " ", block)) >= 450]
        figure_callouts = re.findall(
            r"(?:\bfig(?:ure)?\.?)\s*\d+|!\[[^\]]*\]\([^\)]*\)",
            text,
            flags=re.IGNORECASE,
        )
        table_callouts = re.findall(
            r"\btable\s*\d+|^\|.+\|$",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        results_match = re.search(
            r"^##\s+Results\b(.*?)(?=^##\s+|\Z)",
            text,
            flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        results_subsections = 0
        if results_match:
            results_subsections = len(re.findall(r"(?m)^#{3,4}\s+", results_match.group(1)))
        lowered = text.lower()
        missing_terms = [str(term) for term in required_terms if str(term).strip().lower() not in lowered]

        failures: List[str] = []
        if len(text.strip()) < min_text_chars:
            failures.append(f"text_chars {len(text.strip())} < {min_text_chars}")
        if len(headings) < min_sections:
            failures.append(f"sections {len(headings)} < {min_sections}")
        if len(long_paragraphs) < min_long_paragraphs:
            failures.append(f"long_paragraphs {len(long_paragraphs)} < {min_long_paragraphs}")
        if bullet_ratio > max_bullet_ratio:
            failures.append(f"bullet_ratio {bullet_ratio:.3f} > {max_bullet_ratio:.3f}")
        if len(figure_callouts) < min_figure_callouts:
            failures.append(f"figure_callouts {len(figure_callouts)} < {min_figure_callouts}")
        if len(table_callouts) < min_table_callouts:
            failures.append(f"table_callouts {len(table_callouts)} < {min_table_callouts}")
        if results_subsections < min_results_subsections:
            failures.append(f"results_subsections {results_subsections} < {min_results_subsections}")
        if missing_terms:
            failures.append(f"missing required terms: {', '.join(missing_terms[:10])}")

        success = not failures
        return TaskVerificationService._check_result(
            "manuscript_markdown_quality",
            success,
            path=path,
            message=None if success else "; ".join(failures),
            extra={
                "text_chars": len(text.strip()),
                "sections": len(headings),
                "long_paragraphs": len(long_paragraphs),
                "bullet_ratio": round(bullet_ratio, 4),
                "figure_callouts": len(figure_callouts),
                "table_callouts": len(table_callouts),
                "results_subsections": results_subsections,
                "missing_terms": missing_terms,
            },
        )
