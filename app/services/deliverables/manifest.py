"""Deliverable manifest IO, merging and module housekeeping.

Extracted from ``publisher.py`` (clusters "manifest 读取/收集" and
"去重/manifest 构建/legacy 清理") per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.3. This sibling owns
``manifest_latest.json`` reading, the merged item list that is rebuilt from the
publish root on every run, the manifest payload itself and the
legacy/noise module cleanup.

Compatibility contract: the ``publisher`` facade re-exports every name defined
here (constants included) and composes ``_ManifestMethods`` onto
``DeliverablePublisher``, so ``app/services/artifacts/projector.py``
(``_publisher_mod._atomic_write_json``, ``_publisher._new_version_id`` /
``_to_project_relative``) and every other import site keep working unchanged.
The manifest payload keys are a UI contract and are byte-identical to the
pre-refactor implementation.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .policy import SOURCE_OWNERSHIP_MAP

logger = logging.getLogger(__name__)


class _ManifestMethods:
    """Manifest IO / merge / housekeeping methods mixed into ``DeliverablePublisher``."""

    def _read_manifest(self, path: Path) -> Dict[str, Any]:
        if not path.exists() or not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _manifest_items_from_manifest(self, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
        items = manifest.get("items")
        rows: List[Dict[str, Any]] = []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    rows.append(dict(item))
            if rows:
                return rows

        modules = manifest.get("modules")
        if not isinstance(modules, dict):
            return rows
        for module_name, module_items in modules.items():
            if not isinstance(module_items, list):
                continue
            for item in module_items:
                if isinstance(item, dict):
                    row = dict(item)
                    row.setdefault("module", module_name)
                    rows.append(row)
        return rows

    def _collect_latest_items(
        self,
        *,
        latest_root: Path,
        previous_manifest: Dict[str, Any],
        updated_items: List[Dict[str, Any]],
        fallback_timestamp: str,
        fallback_status: str,
    ) -> List[Dict[str, Any]]:
        previous_map: Dict[str, Dict[str, Any]] = {}
        for row in self._manifest_items_from_manifest(previous_manifest):
            module = str(row.get("module") or "").strip().lower()
            path = str(row.get("path") or "").strip().replace("\\", "/")
            if not module or not path:
                continue
            previous_map[f"{module}::{path}"] = row

        update_map: Dict[str, Dict[str, Any]] = {}
        for row in updated_items:
            module = str(row.get("module") or "").strip().lower()
            path = str(row.get("path") or "").strip().replace("\\", "/")
            if not module or not path:
                continue
            update_map[f"{module}::{path}"] = row

        merged: List[Dict[str, Any]] = []
        allowed_modules = set(self._settings.modules)
        for file_path in sorted(latest_root.rglob("*")):
            if not file_path.is_file():
                continue
            rel_path = str(file_path.relative_to(latest_root)).replace("\\", "/")
            rel_parts = rel_path.split("/")
            module = rel_parts[0].strip().lower() if rel_parts else ""
            if module not in allowed_modules:
                continue
            key = f"{module}::{rel_path}"
            source = update_map.get(key) or previous_map.get(key) or {}
            trusted_publish = bool(source.get("trusted_publish"))
            if module == "docs" and not trusted_publish and not self._is_allowed_doc_file(file_path):
                continue
            if not trusted_publish and not self._should_publish_file(module, file_path):
                continue

            source_path_str = str(source.get("source_path") or "").strip()
            if source_path_str and self._source_path_is_blocked(source_path_str):
                try:
                    file_path.unlink()
                except Exception:
                    pass
                continue

            if not source and not self._file_belongs_in_deliverables(file_path, module):
                if file_path.name == SOURCE_OWNERSHIP_MAP:
                    continue
                try:
                    file_path.unlink()
                except Exception:
                    pass
                continue

            status_value = str(source.get("status") or fallback_status or "final").strip().lower()
            stat = file_path.stat()
            updated_at = source.get("updated_at")
            if not isinstance(updated_at, str) or not updated_at.strip():
                updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            source_path = source.get("source_path")
            row = {
                "module": module,
                "path": rel_path,
                "status": status_value,
                "size": stat.st_size,
                "updated_at": updated_at or fallback_timestamp,
                "source_path": str(source_path) if source_path is not None else None,
            }
            if source.get("trusted_publish"):
                row["trusted_publish"] = True
            if source.get("storage"):
                row["storage"] = source.get("storage")
            if source.get("reference_source"):
                row["reference_source"] = source.get("reference_source")
            checksum = source.get("sha256")
            if isinstance(checksum, str) and checksum.strip():
                row["sha256"] = checksum.strip()
            merged.append(row)
        return sorted(merged, key=lambda item: (str(item.get("module")), str(item.get("path"))))

    def _dedupe_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: Dict[str, Dict[str, Any]] = {}
        for item in items:
            key = f"{item.get('module')}::{item.get('path')}"
            deduped[key] = item
        return sorted(deduped.values(), key=lambda item: (str(item.get("module")), str(item.get("path"))))

    def _build_manifest(
        self,
        *,
        version_id: str,
        created_at: str,
        source: Dict[str, Any],
        items: List[Dict[str, Any]],
        paper_status: Dict[str, Any],
        release_state: str,
        public_release_ready: bool,
        release_summary: Optional[str],
        hidden_artifact_prefixes: List[str],
    ) -> Dict[str, Any]:
        modules: Dict[str, List[Dict[str, Any]]] = {}
        for item in items:
            modules.setdefault(str(item["module"]), []).append(item)
        for module_items in modules.values():
            module_items.sort(key=lambda row: str(row.get("path")))
        return {
            "version_id": version_id,
            "created_at": created_at,
            "template": self._settings.default_template,
            "single_version": True,
            "source": source,
            "modules": modules,
            "paper_status": paper_status,
            "release_state": str(release_state or "final"),
            "public_release_ready": bool(public_release_ready),
            "release_summary": release_summary,
            "hidden_artifact_prefixes": list(hidden_artifact_prefixes),
            "published_files_count": len(items),
            "published_modules": sorted(modules.keys()),
            "items": items,
        }

    def _prune_legacy_modules(self, latest_root: Path) -> None:
        if not latest_root.exists() or not latest_root.is_dir():
            return
        allowed_modules = set(self._settings.modules)
        for child in latest_root.iterdir():
            if not child.is_dir():
                continue
            if child.name in allowed_modules:
                continue
            shutil.rmtree(child, ignore_errors=True)

    def _cleanup_docs_module(self, docs_dir: Path) -> None:
        if not docs_dir.exists() or not docs_dir.is_dir():
            return
        # Files published as trusted (plan task outputs via the artifact event
        # stream) fail the docs keyword whitelist by design; keep them.
        trusted_names: set = set()
        manifest = self._read_manifest(docs_dir.parent.parent / "manifest_latest.json")
        for row in self._manifest_items_from_manifest(manifest):
            if not isinstance(row, dict):
                continue
            if str(row.get("module") or "").strip().lower() != "docs":
                continue
            if not row.get("trusted_publish"):
                continue
            name = Path(str(row.get("path") or "")).name
            if name:
                trusted_names.add(name)
        for file_path in docs_dir.iterdir():
            if not file_path.is_file():
                continue
            if file_path.name in trusted_names:
                continue
            if self._is_allowed_doc_file(file_path):
                continue
            try:
                file_path.unlink()
            except Exception:
                logger.debug("Failed to remove non-deliverable docs file: %s", file_path)

    def _new_version_id(self) -> str:
        now = datetime.now(timezone.utc)
        return f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"

    def _to_project_relative(self, path: Path) -> str:
        lexical_abs = Path(os.path.abspath(str(path)))
        try:
            return str(lexical_abs.relative_to(self._project_root))
        except Exception:
            try:
                return str(path.resolve().relative_to(self._project_root))
            except Exception:
                return str(path)


__all__ = ["_ManifestMethods"]
