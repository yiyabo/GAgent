"""Path/base-dir resolution cluster of ``TaskVerificationService``.

Moved verbatim out of ``task_verification.py`` per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.6 (TV cluster ②):
relative-path base-directory inference (``_resolve_base_dir`` family),
check-path/search-root resolution (``_select_path_for_check`` …
``_resolve_glob``) and artifact-path extraction/normalization
(``_extract_artifact_paths`` … ``_resolve_session_relative_artifact_paths``).
Composed into ``TaskVerificationService`` as the ``_PathMethods`` mixin, so every
``self.*``/``cls.*`` call site is unchanged.

Late binding: ``_runtime_session_roots`` reads ``TaskVerificationService`` by
class attribute (``TaskVerificationService._payload_base_dir_candidates``); the
class lives in the facade, which imports this module at import time.  That method
therefore binds the class through ``_facade()`` at call time (one added line —
the only deviation from byte-verbatim in this module); the original call
expression stays intact.  Module-level ``__getattr__`` (PEP 562) does NOT work
here: it covers attribute access on the module object, not the in-function global
lookup, which fails with NameError.

The two path predicates that the router layer reaches through instances —
``_is_internal_artifact_path`` (D5 convergence target, also read by plan_executor
as a class attribute) and ``_is_local_path`` — deliberately stay defined in the
facade class body; this module only calls them via ``self.``, which resolves on
``TaskVerificationService`` at call time.

The module uses its own ``logging.getLogger(__name__)`` (split precedent).
"""

from __future__ import annotations

import glob
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .acceptance_criteria import derive_relative_output_dirs, resolve_glob_pattern
from .plan_models import PlanNode
from .verification_cues import _PATH_KEYS

logger = logging.getLogger(__name__)


def _facade() -> Any:
    """Late-bound task_verification facade module (monkeypatch-friendly lookups)."""
    from . import task_verification

    return task_verification


class _PathMethods:
    """Path/base-dir resolution cluster of ``TaskVerificationService`` (mixin)."""

    def _resolve_base_dir(
        self,
        criteria: Optional[Dict[str, Any]],
        artifact_paths: Sequence[str],
        *,
        payload: Optional[Dict[str, Any]] = None,
        node: Optional[PlanNode] = None,
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
            for candidate in self._task_raw_files_base_dir_candidates(
                node=node,
                payload=payload,
                artifact_paths=artifact_paths,
            ):
                if candidate.exists() and candidate.is_dir():
                    return candidate
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

    @classmethod
    def _task_raw_files_base_dir_candidates(
        cls,
        *,
        node: Optional[PlanNode],
        payload: Optional[Dict[str, Any]],
        artifact_paths: Sequence[str],
    ) -> List[Path]:
        if node is None:
            return []

        task_suffix = cls._task_raw_files_suffix(node)
        if task_suffix is None:
            return []

        candidates = cls._task_raw_files_candidates_from_artifact_paths(
            task_suffix=task_suffix,
            artifact_paths=artifact_paths,
        )
        seen: set[str] = {str(candidate) for candidate in candidates}
        roots = cls._runtime_session_roots_from_payload_and_artifacts(
            payload=payload,
            artifact_paths=artifact_paths,
        )
        for root in roots:
            candidate = root / "raw_files" / task_suffix
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
        return candidates

    @staticmethod
    def _task_raw_files_candidates_from_artifact_paths(
        *,
        task_suffix: Path,
        artifact_paths: Sequence[str],
    ) -> List[Path]:
        suffix_parts = tuple(task_suffix.parts)
        candidates: List[Path] = []
        seen: set[str] = set()

        def _add(candidate: Path) -> None:
            key = str(candidate)
            if key in seen:
                return
            seen.add(key)
            candidates.append(candidate)

        for raw in artifact_paths:
            if not isinstance(raw, str) or not raw.strip():
                continue
            path = Path(raw.strip()).expanduser()
            parts = tuple(path.parts)
            try:
                raw_index = parts.index("raw_files")
            except ValueError:
                continue
            after_raw = parts[raw_index + 1:]
            if len(after_raw) < len(suffix_parts):
                continue
            if after_raw[:len(suffix_parts)] != suffix_parts:
                continue
            candidate = Path(*parts[:raw_index + 1 + len(suffix_parts)])
            _add(candidate)

        return candidates

    @staticmethod
    def _task_raw_files_suffix(node: PlanNode) -> Optional[Path]:
        raw_path = str(getattr(node, "path", "") or "").strip()
        parts = [part for part in raw_path.strip("/").split("/") if part]
        if not parts:
            parts = [str(getattr(node, "id", "") or "").strip()]
        task_parts: List[str] = []
        for part in parts:
            try:
                task_id = int(part)
            except (TypeError, ValueError):
                return None
            task_parts.append(f"task_{task_id}")
        if not task_parts:
            return None
        return Path(*task_parts)

    @classmethod
    def _runtime_session_roots_from_payload_and_artifacts(
        cls,
        *,
        payload: Optional[Dict[str, Any]],
        artifact_paths: Sequence[str],
    ) -> List[Path]:
        roots: List[Path] = []
        seen: set[str] = set()

        def _add(root: Path) -> None:
            try:
                resolved = root.expanduser().resolve()
            except Exception:
                resolved = root.expanduser()
            if not resolved.exists() or not resolved.is_dir():
                return
            key = str(resolved)
            if key in seen:
                return
            seen.add(key)
            roots.append(resolved)

        for raw in artifact_paths:
            if not isinstance(raw, str) or not raw.strip():
                continue
            path = Path(raw.strip()).expanduser()
            for candidate in (path, *path.parents):
                if candidate.name.startswith("session_") and candidate.parent.name == "runtime":
                    _add(candidate)
                    break

        for candidate in cls._payload_base_dir_candidates(payload):
            path = candidate.expanduser()
            for parent in (path, *path.parents):
                if parent.name.startswith("session_") and parent.parent.name == "runtime":
                    _add(parent)
                    break

        return roots

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

    def _select_path_for_check(
        self,
        raw_path: Any,
        base_dir: Path,
        artifact_paths: Sequence[str],
        *,
        mode: str,
    ) -> Path:
        candidates = self._resolve_path_candidates(raw_path, base_dir, artifact_paths)
        for candidate in candidates:
            try:
                if mode == "nonempty_file" and candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate
                if mode == "exists" and candidate.exists():
                    return candidate
            except OSError:
                continue
        return candidates[0]

    def _resolve_path_candidates(
        self,
        raw_path: Any,
        base_dir: Path,
        artifact_paths: Sequence[str],
    ) -> List[Path]:
        primary = self._resolve_path(raw_path, base_dir)
        text = str(raw_path or "").strip()
        raw = Path(text).expanduser()
        if raw.is_absolute():
            return [primary]
        candidates: List[Path] = []
        seen: set[str] = set()
        for root in self._verification_search_roots(base_dir, artifact_paths):
            candidate = (root / raw).resolve()
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
        return candidates or [primary]

    def _resolve_glob_patterns(
        self,
        raw_glob: str,
        base_dir: Path,
        artifact_paths: Sequence[str],
    ) -> List[str]:
        text = str(raw_glob or "").strip()
        if os.path.isabs(text):
            return [text]
        patterns: List[str] = []
        seen: set[str] = set()
        for root in self._verification_search_roots(base_dir, artifact_paths):
            pattern = str((root / text).resolve())
            if pattern in seen:
                continue
            seen.add(pattern)
            patterns.append(pattern)
        return patterns or [self._resolve_glob(text, base_dir)]

    @staticmethod
    def _glob_matches(patterns: Sequence[str]) -> List[str]:
        matched: List[str] = []
        seen: set[str] = set()
        for pattern in patterns:
            for item in glob.glob(pattern, recursive=True):
                if item in seen:
                    continue
                seen.add(item)
                matched.append(item)
        return matched

    @staticmethod
    def _verification_search_roots(base_dir: Path, artifact_paths: Sequence[str]) -> List[Path]:
        roots: List[Path] = []
        seen: set[str] = set()

        def _add(candidate: Path) -> None:
            try:
                resolved = candidate.expanduser().resolve()
            except Exception:
                resolved = candidate.expanduser()
            if not resolved.exists() or not resolved.is_dir():
                return
            key = str(resolved)
            if key in seen:
                return
            seen.add(key)
            roots.append(resolved)

        _add(base_dir)
        for raw in artifact_paths:
            text = str(raw or "").strip()
            if not text:
                continue
            path = Path(text).expanduser()
            try:
                if path.exists() and path.is_dir():
                    _add(path)
                elif path.exists() and path.is_file():
                    _add(path.parent)
                elif path.suffix:
                    _add(path.parent)
            except OSError:
                continue
        return roots

    def _diagnose_check_resolutions(
        self,
        criteria: Optional[Dict[str, Any]],
        base_dir: Path,
    ) -> List[Dict[str, Any]]:
        checks = criteria.get("checks") if isinstance(criteria, dict) else None
        if not isinstance(checks, list):
            return []

        resolved: List[Dict[str, Any]] = []
        for raw_check in checks:
            if not isinstance(raw_check, dict):
                resolved.append({"type": "invalid_check", "message": "Check definition is not an object."})
                continue
            check = self._normalize_check(raw_check)
            check_type = str(check.get("type") or "").strip()
            item: Dict[str, Any] = {"type": check_type or "invalid_check"}
            raw_path = check.get("path")
            raw_glob = resolve_glob_pattern(check) if check_type in {"glob_nonempty", "glob_count_at_least"} else None
            if isinstance(raw_path, str) and raw_path.strip():
                try:
                    path = self._resolve_path(raw_path, base_dir)
                    item.update(self._path_diagnostic(path))
                    item["raw_path"] = raw_path
                    item["resolved_path"] = str(path)
                except Exception as exc:
                    item.update({"raw_path": raw_path, "message": str(exc)})
            if isinstance(raw_glob, str) and raw_glob.strip():
                try:
                    pattern = self._resolve_glob(raw_glob, base_dir)
                    matches = glob.glob(pattern, recursive=True)
                    item.update({
                        "raw_glob": raw_glob,
                        "resolved_glob": pattern,
                        "match_count": len(matches),
                    })
                except Exception as exc:
                    item.update({"raw_glob": raw_glob, "message": str(exc)})
            resolved.append(item)
        return resolved

    @staticmethod
    def _path_diagnostic(path: Path) -> Dict[str, Any]:
        try:
            exists = path.exists()
            is_file = path.is_file() if exists else False
            is_dir = path.is_dir() if exists else False
            size = path.stat().st_size if is_file else None
        except OSError as exc:
            return {
                "exists": False,
                "is_file": False,
                "is_dir": False,
                "size": None,
                "error": str(exc),
            }
        return {
            "exists": exists,
            "is_file": is_file,
            "is_dir": is_dir,
            "size": size,
        }

    def _artifact_path_category_stats(self, artifact_paths: Sequence[str]) -> Dict[str, int]:
        stats = {
            "total": 0,
            "local_paths": 0,
            "existing_files": 0,
            "existing_dirs": 0,
            "missing_paths": 0,
            "internal_paths": 0,
            "session_relative_paths": 0,
        }
        for raw in artifact_paths:
            if not isinstance(raw, str) or not raw.strip():
                continue
            text = raw.strip()
            stats["total"] += 1
            if self._is_internal_artifact_path(text):
                stats["internal_paths"] += 1
            if text.replace("\\", "/").lstrip("/").startswith("task_"):
                stats["session_relative_paths"] += 1
            if self._is_local_path(text):
                stats["local_paths"] += 1
            path = Path(text).expanduser()
            try:
                if path.exists() and path.is_file():
                    stats["existing_files"] += 1
                elif path.exists() and path.is_dir():
                    stats["existing_dirs"] += 1
                else:
                    stats["missing_paths"] += 1
            except OSError:
                stats["missing_paths"] += 1
        return stats

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

    def _normalize_artifact_paths(
        self,
        artifact_paths: Sequence[str],
        *,
        payload: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        normalized: List[str] = []
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
            normalized.append(text)

        session_roots = self._runtime_session_roots(payload)
        for raw in artifact_paths:
            _add(raw)
            for candidate in self._resolve_session_relative_artifact_paths(raw, session_roots):
                _add(str(candidate))

        return normalized[:80]

    @staticmethod
    def _runtime_session_roots(payload: Optional[Dict[str, Any]] = None) -> List[Path]:
        TaskVerificationService = _facade().TaskVerificationService
        roots: List[Path] = []
        seen: set[str] = set()

        def _add(root: Path) -> None:
            try:
                resolved = root.expanduser().resolve()
            except Exception:
                resolved = root.expanduser()
            if not resolved.exists() or not resolved.is_dir():
                return
            key = str(resolved)
            if key in seen:
                return
            seen.add(key)
            roots.append(resolved)

        for candidate in TaskVerificationService._payload_base_dir_candidates(payload):
            path = candidate.expanduser()
            for parent in (path, *path.parents):
                if parent.name.startswith("session_") and parent.parent.name == "runtime":
                    _add(parent)
                    break

        if roots:
            return roots

        from app.services.session_paths import get_runtime_root

        runtime_root = get_runtime_root()
        if runtime_root.exists() and runtime_root.is_dir():
            for session_dir in runtime_root.glob("session_*"):
                _add(session_dir)

        return roots

    @staticmethod
    def _resolve_session_relative_artifact_paths(
        raw_path: Any,
        session_roots: Sequence[Path],
    ) -> List[Path]:
        if not isinstance(raw_path, str) or not session_roots:
            return []
        text = raw_path.strip().replace("\\", "/")
        if not text:
            return []
        if Path(text).expanduser().exists():
            return []

        parts = [part for part in text.lstrip("/").split("/") if part and part != "."]
        if not parts or ".." in parts:
            return []
        if "raw_files" in parts:
            index = parts.index("raw_files")
            suffix_parts = parts[index + 1:]
        elif parts[0].startswith("task_"):
            suffix_parts = parts
        else:
            return []
        if not suffix_parts or not suffix_parts[0].startswith("task_"):
            return []

        suffix = Path(*suffix_parts)
        resolved: List[Path] = []
        seen: set[str] = set()
        for session_root in session_roots:
            candidate = session_root / "raw_files" / suffix
            if not candidate.exists():
                continue
            try:
                candidate = candidate.resolve()
            except Exception:
                pass
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(candidate)
        if len(resolved) > 1:
            logger.warning(
                "Ambiguous session-relative artifact path %s matched %d runtime sessions; ignoring fallback.",
                raw_path,
                len(resolved),
            )
            return []
        return resolved
