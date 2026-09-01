"""Registry projector: the single consumer of artifact events.

Every producer only emits events; this projector owns every derived view:

* ``artifacts/registry.json`` — the queryable fact snapshot
* ``deliverables/latest/`` — materialization (copy for small files,
  hardlink/reference for big ones)
* ``deliverables/manifest_latest.json`` — the manifest the UI reads today

Plan task outputs are published as *trusted* artifacts: they bypass the
publisher's filename-keyword whitelists (which silently dropped real task
outputs like ``task1_evidence_cards.md``) while noise/intermediate-script
safety filters still apply.  Chat-path materialization is unchanged: the
publisher handles it as before and ``record_chat_publish`` only records the
result into the event stream + registry.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.services.deliverables import publisher as _publisher_mod
from app.services.deliverables.publisher import (
    CODE_EXTS,
    IMAGE_EXTS,
    REF_EXTS,
    TABULAR_EXTS,
    DeliverablePublisher,
    PublishReport,
    get_deliverable_publisher,
)

from .events import ArtifactEvent, append_events, utc_now_iso
from .registry import RegistryStore, apply_event_to_registry

logger = logging.getLogger(__name__)

_DEFAULT_COPY_MAX_BYTES = 268435456


class RegistryProjector:
    def __init__(self, *, publisher: Optional[DeliverablePublisher] = None, settings=None) -> None:
        self._publisher = publisher or get_deliverable_publisher()
        self._settings = settings or self._publisher.settings

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._settings, "artifact_event_stream_enabled", True))

    # ------------------------------------------------------------------ config
    def _copy_max_bytes(self) -> int:
        value = getattr(self._settings, "copy_max_bytes", None)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = _DEFAULT_COPY_MAX_BYTES
        return max(0, value)

    def _link_strategy(self) -> str:
        strategy = str(getattr(self._settings, "link_strategy", "hardlink") or "hardlink").strip().lower()
        return strategy if strategy in {"hardlink", "reference"} else "hardlink"

    # ------------------------------------------------------------------ module resolution
    def resolve_module(self, path: Path, alias: Optional[str] = None) -> str:
        """Deterministic module for a produced artifact.

        Unlike the publisher's filename-keyword classification (which returns
        None for real task outputs such as ``task1_evidence_cards.md``), this
        never returns None: contract alias hints first, publisher
        classification second, extension fallback last.
        """
        if alias:
            alias_text = str(alias).strip()
            if alias_text.startswith("report."):
                return "docs"
            slot = alias_text.split(".", 1)[1].lower() if "." in alias_text else ""
            if slot in {"manuscript_md", "draft_md"}:
                return "paper"
        path = Path(path)
        classified = self._publisher._classify_module(path)
        if classified:
            return classified
        suffix = path.suffix.lower()
        if suffix in CODE_EXTS:
            return "code"
        if suffix in IMAGE_EXTS or suffix in TABULAR_EXTS:
            return "image_tabular"
        if suffix in REF_EXTS:
            return "refs"
        return "docs"

    # ------------------------------------------------------------------ plan path
    def consume_plan_events(
        self,
        *,
        session_id: str,
        events: List[ArtifactEvent],
        plan_id: Optional[int] = None,
        task_id: Optional[int] = None,
        task_name: Optional[str] = None,
        task_instruction: Optional[str] = None,
    ) -> Optional[PublishReport]:
        """Append plan-task events, update the registry, and materialize
        requested artifacts into deliverables (trusted publish)."""
        events = [event for event in events if isinstance(event, ArtifactEvent)]
        if not events or not session_id:
            return None
        session_dir = self._publisher.get_session_dir(session_id, create=True)
        store = RegistryStore(session_dir)
        report: Optional[PublishReport] = None
        with store.locked() as registry:
            seen = set(registry.get("event_ids") or [])
            new_events = [event for event in events if event.event_id not in seen]
            if not new_events:
                logger.debug("artifact projector: all %d event(s) already consumed", len(events))
                return None
            append_events(session_dir, new_events)

            small: List[Dict[str, Any]] = []
            big: List[Tuple[ArtifactEvent, Path, str]] = []
            for event in new_events:
                if event.publish_requested:
                    path = Path(event.file_path)
                    if path.is_file():
                        try:
                            event.file_size = path.stat().st_size
                        except OSError:
                            pass
                        module = self.resolve_module(path, event.alias)
                        event.module = module
                        if event.file_size is not None and event.file_size > self._copy_max_bytes():
                            big.append((event, path, module))
                        else:
                            small.append({"path": str(path), "module": module, "trusted": True})
                    else:
                        logger.info(
                            "artifact event %s source missing; registered only: %s",
                            event.event_id[:8],
                            event.file_path,
                        )
                apply_event_to_registry(registry, event)

            if small:
                logger.info(
                    "artifact projector: publishing %d trusted artifact(s) for session %s",
                    len(small),
                    session_id,
                )
                report = self._publisher.publish_from_tool_result(
                    session_id=session_id,
                    tool_name="deliverable_submit",
                    raw_result={"deliverable_submit": {"artifacts": small, "publish": True}},
                    plan_id=plan_id,
                    task_id=task_id,
                    task_name=task_name,
                    task_instruction=task_instruction,
                    publish_status="final",
                )

            big_rows: List[Dict[str, Any]] = []
            if big:
                latest_root = session_dir / "deliverables" / "latest"
                latest_root.mkdir(parents=True, exist_ok=True)
                for event, path, module in big:
                    row = self._materialize_big_file(latest_root=latest_root, path=path, module=module)
                    big_rows.append(row)
                    logger.info(
                        "artifact projector: big file %s materialized as %s (%s)",
                        path.name,
                        row.get("storage"),
                        row.get("path"),
                    )
                self._merge_rows_into_manifest(session_dir=session_dir, rows=big_rows)

            manifest = self._read_manifest(session_dir)
            self._update_publish_state(registry, new_events, manifest)
            store.save(registry)
        return report

    def _materialize_big_file(self, *, latest_root: Path, path: Path, module: str) -> Dict[str, Any]:
        """Link-or-reference a big file instead of copying it."""
        module_dir = latest_root / module
        module_dir.mkdir(parents=True, exist_ok=True)
        target = module_dir / path.name
        storage = "reference"
        if self._link_strategy() == "hardlink":
            try:
                if target.exists() and not os.path.samefile(str(target), str(path)):
                    target = module_dir / f"{path.stem}.{uuid4().hex[:6]}{path.suffix}"
                if not target.exists():
                    os.link(str(path), str(target))
                if os.path.samefile(str(target), str(path)):
                    storage = "hardlink"
            except OSError:
                storage = "reference"
        if storage == "hardlink":
            rel_path = str(target.relative_to(latest_root)).replace("\\", "/")
        else:
            rel_path = f"{module}/{path.name}"
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        row: Dict[str, Any] = {
            "module": module,
            "path": rel_path,
            "status": "final",
            "size": size,
            "updated_at": utc_now_iso(),
            "source_path": self._publisher._to_project_relative(path),
            "trusted_publish": True,
            "storage": storage,
        }
        if storage == "reference":
            row["reference_source"] = str(path)
        return row

    def _skeleton_manifest(self) -> Dict[str, Any]:
        return {
            "version_id": self._publisher._new_version_id(),
            "created_at": utc_now_iso(),
            "template": self._settings.default_template,
            "single_version": True,
            "source": {"channel": "artifact_event_stream"},
            "modules": {},
            "paper_status": {},
            "release_state": "final",
            "public_release_ready": True,
            "release_summary": None,
            "hidden_artifact_prefixes": [],
            "published_files_count": 0,
            "published_modules": [],
            "items": [],
        }

    def _read_manifest(self, session_dir: Path) -> Dict[str, Any]:
        manifest_path = session_dir / "deliverables" / "manifest_latest.json"
        if not manifest_path.exists():
            return {}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _merge_rows_into_manifest(self, *, session_dir: Path, rows: List[Dict[str, Any]]) -> None:
        """Post-process the manifest so link/reference rows are listed even
        though the publisher never copied them."""
        if not rows:
            return
        manifest_path = session_dir / "deliverables" / "manifest_latest.json"
        manifest = self._read_manifest(session_dir) or self._skeleton_manifest()
        items = manifest.setdefault("items", [])
        if not isinstance(items, list):
            items = []
            manifest["items"] = items
        existing = {
            f"{row.get('module')}::{row.get('path')}"
            for row in items
            if isinstance(row, dict)
        }
        for row in rows:
            key = f"{row.get('module')}::{row.get('path')}"
            if key in existing:
                continue
            items.append(row)
            existing.add(key)
        modules: Dict[str, List[Dict[str, Any]]] = {}
        for item in items:
            if isinstance(item, dict) and item.get("module"):
                modules.setdefault(str(item["module"]), []).append(item)
        for module_items in modules.values():
            module_items.sort(key=lambda row: str(row.get("path")))
        manifest["modules"] = modules
        manifest["published_files_count"] = len(items)
        manifest["published_modules"] = sorted(modules.keys())
        _publisher_mod._atomic_write_json(manifest_path, manifest)

    def _update_publish_state(
        self,
        registry: Dict[str, Any],
        events: List[ArtifactEvent],
        manifest: Dict[str, Any],
    ) -> None:
        items = registry.get("items") or {}
        rows = manifest.get("items") or []
        by_source: Dict[str, Dict[str, Any]] = {}
        by_deliverable: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = f"{row.get('module')}::{row.get('path')}"
            by_deliverable[key] = row
            source_path = str(row.get("source_path") or "").strip()
            if source_path:
                by_source[source_path] = row
        for event in events:
            item = items.get(event.identity())
            if not isinstance(item, dict):
                continue
            published = item.setdefault("published", {"state": "registered"})
            if not event.publish_requested:
                continue
            row: Optional[Dict[str, Any]] = None
            if event.deliverable_path and event.module:
                row = by_deliverable.get(f"{event.module}::{event.deliverable_path}")
            if row is None:
                try:
                    rel_source = self._publisher._to_project_relative(Path(event.file_path))
                except Exception:
                    rel_source = str(event.file_path)
                row = by_source.get(rel_source)
            if row is not None:
                event.deliverable_path = row.get("path")
                item["deliverable_path"] = row.get("path")
                published["state"] = "published"
                published["deliverable_path"] = row.get("path")
                published["storage"] = row.get("storage") or "copy"
            else:
                published["state"] = "registered"

    # ------------------------------------------------------------------ chat envelope
    def record_chat_publish(
        self,
        *,
        session_id: Optional[str],
        tool_name: str,
        report: Optional[PublishReport],
        job_id: Optional[str] = None,
        plan_id: Optional[int] = None,
        task_id: Optional[int] = None,
        task_name: Optional[str] = None,
    ) -> None:
        """Record an already-completed chat-path publish into the event stream
        and registry.  Materialization stays with the publisher; this never
        touches deliverables/."""
        if not self.enabled or report is None or not session_id:
            return
        try:
            session_dir = self._publisher.get_session_dir(session_id)
        except Exception:
            return
        manifest_path = (
            Path(str(report.manifest_path))
            if report.manifest_path
            else session_dir / "deliverables" / "manifest_latest.json"
        )
        manifest: Dict[str, Any] = {}
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    manifest = data
            except Exception:
                manifest = {}
        rows = manifest.get("items") or []
        if not rows:
            return
        events: List[ArtifactEvent] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rel = str(row.get("path") or "").strip()
            module = str(row.get("module") or "").strip()
            if not rel or not module:
                continue
            abs_path = session_dir / "deliverables" / "latest" / rel
            source_path = str(row.get("source_path") or "").strip()
            size = row.get("size")
            events.append(
                ArtifactEvent(
                    session_id=session_id,
                    file_path=str(abs_path),
                    path_aliases=[source_path] if source_path else [],
                    module=module,
                    file_size=size if isinstance(size, int) else None,
                    file_ext=Path(rel).suffix.lower(),
                    file_sha256=row.get("sha256") if isinstance(row.get("sha256"), str) else None,
                    producer_kind="chat_tool",
                    producer_tool=tool_name,
                    producer_plan_id=plan_id,
                    producer_task_id=task_id,
                    producer_task_name=task_name,
                    producer_job_id=str(job_id) if job_id else None,
                    publish_requested=True,
                    deliverable_path=rel,
                )
            )
        if not events:
            return
        store = RegistryStore(session_dir)
        with store.locked() as registry:
            seen = set(registry.get("event_ids") or [])
            new_events = [event for event in events if event.event_id not in seen]
            if not new_events:
                return
            append_events(session_dir, new_events)
            for event in new_events:
                item = apply_event_to_registry(registry, event)
                if isinstance(item, dict):
                    item["deliverable_path"] = event.deliverable_path
                    item["published"] = {
                        "state": "published",
                        "deliverable_path": event.deliverable_path,
                        "storage": "copy",
                    }
            store.save(registry)


_projector: Optional[RegistryProjector] = None


def get_registry_projector() -> RegistryProjector:
    global _projector
    if _projector is None:
        _projector = RegistryProjector()
    return _projector
