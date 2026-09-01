"""Per-session artifact registry: derived snapshot of the event log.

``registry.json`` answers "what did this session produce, from where, and did
it reach the deliverables panel" without scanning directories.  It is a pure
projection: delete it and ``RegistryStore.rebuild()`` replays events.jsonl to
restore it (publish state included, when the deliverables manifest is given).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from .events import ArtifactEvent, iter_events, utc_now_iso

REGISTRY_SCHEMA_VERSION = 1
REGISTRY_NAME = "registry.json"

_registry_locks: Dict[str, threading.Lock] = {}
_registry_locks_guard = threading.Lock()


def _lock_for(session_dir: Path) -> threading.Lock:
    key = str(Path(session_dir))
    with _registry_locks_guard:
        lock = _registry_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _registry_locks[key] = lock
        return lock


class RegistryStore:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = Path(session_dir)
        self.artifacts_dir = self.session_dir / "artifacts"
        self.registry_path = self.artifacts_dir / REGISTRY_NAME

    def _empty(self) -> Dict[str, Any]:
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "session_id": self.session_dir.name,
            "updated_at": None,
            "event_ids": [],
            "items": {},
        }

    def load(self) -> Dict[str, Any]:
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            except Exception:
                data = None
            if isinstance(data, dict) and isinstance(data.get("items"), dict):
                return data
        return self._empty()

    def save(self, registry: Dict[str, Any]) -> None:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        registry["updated_at"] = utc_now_iso()
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(self.artifacts_dir), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(registry, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(self.registry_path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @contextmanager
    def locked(self) -> Iterator[Dict[str, Any]]:
        """Load the registry while holding the per-session write lock.

        Callers must finish with ``store.save(registry)`` inside the block.
        """
        lock = _lock_for(self.session_dir)
        lock.acquire()
        try:
            yield self.load()
        finally:
            lock.release()

    def rebuild(self, *, deliverables_manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Replay the event log into a fresh registry snapshot."""
        registry = self._empty()
        for event in iter_events(self.session_dir):
            apply_event_to_registry(registry, event)
        if deliverables_manifest:
            restore_publish_state_from_manifest(registry, deliverables_manifest)
        self.save(registry)
        return registry


def apply_event_to_registry(registry: Dict[str, Any], event: ArtifactEvent) -> Optional[Dict[str, Any]]:
    """Fold one event into the registry.  Returns the item, or None when the
    event was already applied (idempotent by ``event_id``)."""
    event_ids = registry.setdefault("event_ids", [])
    if event.event_id in event_ids:
        return None
    event_ids.append(event.event_id)
    items = registry.setdefault("items", {})
    identity = event.identity()
    now = event.ts or utc_now_iso()
    item = items.get(identity)
    if not isinstance(item, dict):
        item = {
            "identity": identity,
            "aliases": [],
            "first_seen": now,
        }
        items[identity] = item
    aliases = item.setdefault("aliases", [])
    for alias in [event.alias, *(event.path_aliases or [])]:
        if alias and str(alias).strip() and alias not in aliases:
            aliases.append(alias)
    item["source_path"] = event.file_path
    if event.module:
        item["module"] = event.module
    if event.file_size is not None:
        item["size"] = event.file_size
    if event.file_ext:
        item["ext"] = event.file_ext
    if event.file_sha256:
        item["sha256"] = event.file_sha256
    item["producer"] = {
        "kind": event.producer_kind,
        "tool": event.producer_tool,
        "plan_id": event.producer_plan_id,
        "task_id": event.producer_task_id,
        "task_name": event.producer_task_name,
        "job_id": event.producer_job_id,
    }
    item["contract"] = {
        "declared": bool(event.contract_declared),
        "alias_source": event.contract_alias_source,
    }
    item["publish_requested"] = bool(event.publish_requested)
    if event.publish_role and event.publish_role != "normal":
        item["publish_role"] = event.publish_role
    if event.deliverable_path:
        item["deliverable_path"] = event.deliverable_path
    item["last_seen"] = now
    item.setdefault("published", {"state": "registered"})
    return item


def restore_publish_state_from_manifest(registry: Dict[str, Any], manifest: Dict[str, Any]) -> None:
    """Re-attach published state from a deliverables manifest after a rebuild.

    Matching is by deliverable path first (chat envelope events carry it),
    then by source path suffix (manifest rows store project-relative sources).
    """
    rows = manifest.get("items") if isinstance(manifest, dict) else None
    if not isinstance(rows, list):
        return
    by_deliverable: Dict[str, Dict[str, Any]] = {}
    source_rows: list = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        module = str(row.get("module") or "").strip()
        rel = str(row.get("path") or "").strip()
        if module and rel:
            by_deliverable[f"{module}::{rel}"] = row
        source_path = str(row.get("source_path") or "").strip()
        if source_path:
            source_rows.append((source_path, row))
    items = registry.get("items") or {}
    for item in items.values():
        if not isinstance(item, dict):
            continue
        row: Optional[Dict[str, Any]] = None
        deliverable_path = str(item.get("deliverable_path") or "").strip()
        module = str(item.get("module") or "").strip()
        if deliverable_path and module:
            row = by_deliverable.get(f"{module}::{deliverable_path}")
        if row is None:
            item_source = str(item.get("source_path") or "")
            for source_path, candidate in source_rows:
                if item_source.endswith(source_path) or source_path.endswith(item_source):
                    row = candidate
                    break
        if row is None:
            continue
        item["deliverable_path"] = row.get("path")
        item["published"] = {
            "state": "published",
            "deliverable_path": row.get("path"),
            "storage": row.get("storage") or "copy",
        }
