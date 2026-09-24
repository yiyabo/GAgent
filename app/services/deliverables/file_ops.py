"""Deliverable file operations: copying, watermarking, conflict naming.

Extracted from ``publisher.py`` (clusters "来源归属（.source_owners.json）+冲突
命名" and "复制+水印+md 图片路径重写" plus ``_atomic_write_json``) per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.3. This sibling owns
the physical placement of a published file: basename conflicts and the
source-ownership map, the watermark/copy decision, the markdown image rewrite
that keeps front-end-visible paths free of ``..``, and the atomic JSON writer
used for ``manifest_latest.json``.

Compatibility contract: the ``publisher`` facade re-exports every name defined
here (including the private ``_KeepFirstConflict`` exception and
``_atomic_write_json``), so ``app/services/artifacts/projector.py``
(``_publisher_mod._atomic_write_json``) and every other import site keep working
unchanged.

The ``from tool_box.watermark import apply_watermark`` import stays *inside*
``_copy_to_module`` exactly as before: hoisting it to module level would create
the app -> tool_box import edge at facade import time that the lazy form exists
to avoid.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config.deliverable_config import DeliverableConflictStrategy

from .policy import IMAGE_EXTS, SOURCE_OWNERSHIP_MAP


class _KeepFirstConflict(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _FileOpsMethods:
    """Copy / watermark / conflict methods mixed into ``DeliverablePublisher``."""

    def _source_identity(self, source_path: Path) -> str:
        try:
            return str(source_path.resolve())
        except Exception:
            return str(source_path)

    def _load_source_ownership(self, module_dir: Path) -> Dict[str, str]:
        map_path = module_dir / SOURCE_OWNERSHIP_MAP
        if not map_path.exists():
            return {}
        try:
            payload = json.loads(map_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(name): str(owner)
            for name, owner in payload.items()
            if isinstance(name, str) and isinstance(owner, str)
        }

    def _write_source_ownership(self, module_dir: Path, owners: Dict[str, str]) -> None:
        map_path = module_dir / SOURCE_OWNERSHIP_MAP
        map_path.write_text(
            json.dumps(owners, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _manifest_source_identity(
        self,
        *,
        latest_root: Path,
        module_dir: Path,
        file_name: str,
        previous_manifest: Dict[str, Any],
    ) -> Optional[str]:
        rel_path = f"{module_dir.relative_to(latest_root).as_posix()}/{file_name}"
        for row in self._manifest_items_from_manifest(previous_manifest):
            row_path = str(row.get("path") or "").strip().replace("\\", "/")
            if row_path != rel_path:
                continue
            source_path = row.get("source_path")
            if not isinstance(source_path, str):
                return None
            candidate = source_path.strip()
            if not candidate or candidate.startswith("task:") or candidate == "inline_bib":
                return None
            return candidate
        return None

    @staticmethod
    def _split_name_suffix(file_name: str) -> Tuple[str, str]:
        path = Path(file_name)
        suffix = "".join(path.suffixes)
        if suffix and file_name.endswith(suffix):
            return file_name[: -len(suffix)], suffix
        return file_name, ""

    def _next_conflict_target_name(
        self,
        *,
        file_name: str,
        module_dir: Path,
        owners: Dict[str, str],
        latest_root: Optional[Path],
        previous_manifest: Optional[Dict[str, Any]],
    ) -> str:
        stem, suffix = self._split_name_suffix(file_name)
        for index in range(2, 10000):
            candidate_name = f"{stem}__{index}{suffix}"
            candidate_target = module_dir / candidate_name
            if candidate_target.exists():
                continue
            if candidate_name in owners:
                continue
            if latest_root is not None and previous_manifest:
                manifest_owner = self._manifest_source_identity(
                    latest_root=latest_root,
                    module_dir=module_dir,
                    file_name=candidate_name,
                    previous_manifest=previous_manifest,
                )
                if manifest_owner is not None:
                    continue
            return candidate_name
        raise ValueError(
            f"Unable to find available deliverable basename for '{file_name}' in {module_dir}"
        )

    def _copy_to_module(
        self,
        *,
        source_path: Path,
        module_dir: Path,
        source_identity: Optional[str] = None,
        latest_root: Optional[Path] = None,
        previous_manifest: Optional[Dict[str, Any]] = None,
        conflict_strategy: Optional[DeliverableConflictStrategy] = None,
    ) -> Path:
        module_dir.mkdir(parents=True, exist_ok=True)
        target = module_dir / source_path.name
        owners: Dict[str, str] = {}
        existing_owner: Optional[str] = None
        if source_identity:
            owners = self._load_source_ownership(module_dir)
            existing_owner = owners.get(source_path.name)
            if existing_owner is None and latest_root is not None and previous_manifest:
                existing_owner = self._manifest_source_identity(
                    latest_root=latest_root,
                    module_dir=module_dir,
                    file_name=source_path.name,
                    previous_manifest=previous_manifest,
                )
        if target.exists() and self._same_file(source_path, target):
            if source_identity and owners.get(source_path.name) != source_identity:
                owners[source_path.name] = source_identity
                self._write_source_ownership(module_dir, owners)
            return target

        conflict_message: Optional[str] = None
        if target.exists():
            if source_identity and existing_owner and existing_owner != source_identity:
                conflict_message = (
                    f"Conflicting deliverable basename '{source_path.name}' from "
                    f"'{existing_owner}' and '{source_identity}'"
                )
            elif existing_owner is None and not self._same_file(source_path, target):
                conflict_message = (
                    f"Conflicting deliverable basename '{source_path.name}' with unknown existing source in "
                    f"{module_dir}"
                )

        if conflict_message:
            strategy: DeliverableConflictStrategy = conflict_strategy or self._settings.basename_conflict_strategy
            if strategy == "keep_first":
                raise _KeepFirstConflict(
                    f"{conflict_message}; kept existing deliverable per conflict strategy"
                )
            if strategy == "rename":
                renamed_name = self._next_conflict_target_name(
                    file_name=source_path.name,
                    module_dir=module_dir,
                    owners=owners,
                    latest_root=latest_root,
                    previous_manifest=previous_manifest,
                )
                target = module_dir / renamed_name
            else:
                raise ValueError(conflict_message)
        if self._should_watermark_deliverable(module_dir, source_path):
            from tool_box.watermark import apply_watermark

            apply_watermark(source_path, target)
        else:
            shutil.copy2(source_path, target)
        if source_identity:
            owners[target.name] = source_identity
            self._write_source_ownership(module_dir, owners)
        return target

    @staticmethod
    def _should_watermark_deliverable(module_dir: Path, source_path: Path) -> bool:
        module = module_dir.name
        suffix = source_path.suffix.lower()
        if module == "docs":
            return suffix in {".md", ".markdown", ".txt"}
        if module == "image_tabular":
            return suffix in IMAGE_EXTS | {".pdf"}
        if module == "paper":
            return suffix in {".md", ".markdown", ".txt", ".pdf"}
        return False

    def _extract_markdown_image_paths(self, text: str) -> List[str]:
        if not text:
            return []
        return [match.group(1).strip() for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", text)]

    def _resolve_section_asset_path(
        self,
        raw_path: str,
        *,
        section_source: Path,
        session_dir: Path,
    ) -> Optional[Path]:
        candidate = str(raw_path or "").strip()
        if not candidate or re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", candidate):
            return None
        local_candidate = (section_source.parent / candidate).resolve()
        if local_candidate.exists():
            return local_candidate
        return self._resolve_path(candidate, session_dir=session_dir)

    def _stage_figures_from_section_text(
        self,
        *,
        latest_root: Path,
        section_source: Path,
        text: str,
        session_dir: Path,
        previous_manifest: Optional[Dict[str, Any]] = None,
    ) -> List[Path]:
        staged: List[Path] = []
        for raw_path in self._extract_markdown_image_paths(text):
            resolved = self._resolve_section_asset_path(
                raw_path,
                section_source=section_source,
                session_dir=session_dir,
            )
            if resolved is None or not resolved.is_file():
                continue
            if resolved.suffix.lower() not in IMAGE_EXTS:
                continue
            try:
                staged.append(
                    self._copy_to_module(
                        source_path=resolved,
                        module_dir=latest_root / "image_tabular",
                        source_identity=self._source_identity(resolved),
                        latest_root=latest_root,
                        previous_manifest=previous_manifest,
                    )
                )
            except _KeepFirstConflict:
                continue
            except ValueError:
                continue
        return staged

    def _rewrite_md_image_paths_in_deliverable(
        self,
        *,
        target: Path,
        section_source: Path,
        latest_root: Path,
        session_dir: Path,
        previous_manifest: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            text = target.read_text(encoding="utf-8")
        except Exception:
            return
        image_paths = self._extract_markdown_image_paths(text)
        if not image_paths:
            return
        rewritten = text
        for raw_path in image_paths:
            if not raw_path or re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", raw_path):
                continue
            resolved = self._resolve_section_asset_path(
                raw_path,
                section_source=section_source,
                session_dir=session_dir,
            )
            if resolved is None or not resolved.is_file() or resolved.suffix.lower() not in IMAGE_EXTS:
                continue
            try:
                staged = self._copy_to_module(
                    source_path=resolved,
                    module_dir=latest_root / "image_tabular",
                    source_identity=self._source_identity(resolved),
                    latest_root=latest_root,
                    previous_manifest=previous_manifest,
                )
            except (_KeepFirstConflict, ValueError):
                continue
            new_ref = str(staged.relative_to(latest_root))
            escaped_old = re.escape(raw_path)
            rewritten = re.sub(
                r"(!\[[^\]]*\]\()\s*" + escaped_old + r"\s*(\))",
                lambda m: m.group(1) + new_ref + m.group(2),
                rewritten,
            )
        if rewritten != text:
            try:
                target.write_text(rewritten, encoding="utf-8")
            except Exception:
                pass

    def _same_file(self, source_path: Path, target: Path) -> bool:
        try:
            return source_path.stat().st_size == target.stat().st_size and source_path.read_bytes() == target.read_bytes()
        except Exception:
            return False

    @staticmethod
    def _sha256_file(path: Path) -> Optional[str]:
        try:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except Exception:
            return None


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON to *path* atomically via a temporary file + os.replace."""
    import tempfile as _tempfile

    parent = str(path.parent)
    tmp_fd, tmp_path = _tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


__all__ = ["_KeepFirstConflict", "_FileOpsMethods", "_atomic_write_json"]
