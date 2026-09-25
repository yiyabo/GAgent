"""Deliverable publisher facade (god-class split, behaviour zero-change).

``DeliverablePublisher`` and its collaborators were split into same-directory
sibling modules per ``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.3
and every name is re-exported here, so ``import ...deliverables.publisher``
call sites (``app/services/artifacts/projector.py``, ``app/services/execution``,
``app/routers/chat/action_handlers.py``, ``app/services/plans/plan_executor.py``)
and the tests keep working unchanged:

- ``policy.py``: extension/stem tables, blocked path tables, path predicates.
- ``report.py``: ``PublishReport`` + ``format_deliverable_submit_summary``.
- ``manifest.py``: manifest IO, merged item list, module housekeeping.
- ``file_ops.py``: copy/watermark, source ownership, conflict naming,
  ``_KeepFirstConflict`` and ``_atomic_write_json`` (lazy ``tool_box.watermark``
  import preserved inside ``_copy_to_module``).
- ``submit_payload.py``: ``deliverable_submit`` payload application.
- ``manuscript.py``: manuscript/release metadata, bibtex rendering, publishing.

The class composes one mixin per sibling; the orchestration entry point
(``publish_from_tool_result``), construction, session directory resolution and
the singleton stay here.  Only the manuscript metadata helpers read the class
back through ``from . import publisher as facade`` at call time (the deep_think
``_dta()`` pattern), which is the single sanctioned deviation from byte-verbatim
bodies.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config.deliverable_config import (
    DeliverableConflictStrategy,
    DeliverableSettings,
    get_deliverable_settings,
)
from app.services.session_paths import normalize_session_base

from .paper_builder import PaperBuilder

logger = logging.getLogger(__name__)

from .policy import (
    BLOCKED_PROJECT_DIRS,
    BLOCKED_SOURCE_FILENAMES,
    BLOCKED_SOURCE_SEGMENTS,
    CC_INTERMEDIATE_SCRIPT_EXTS,
    CODE_EXTS,
    DELIVERABLE_SUBMIT_KEY,
    DOC_ALLOWED_STEMS,
    DOC_EXTS,
    IMAGE_EXTS,
    MANUSCRIPT_PDF_STEMS,
    MAX_PATH_CANDIDATE_LENGTH,
    NOISE_FILENAMES,
    NOISE_PATH_SEGMENTS,
    PAPER_EXTS,
    REF_EXTS,
    SOURCE_OWNERSHIP_MAP,
    TABULAR_EXTS,
    _CC_RUN_ARTIFACT_RE,
    _PolicyMethods,
)


from .report import PublishReport, format_deliverable_submit_summary
from .manifest import _ManifestMethods
from .file_ops import _FileOpsMethods, _KeepFirstConflict, _atomic_write_json
from .submit_payload import _SubmitPayloadMethods
from .manuscript import _ManuscriptMethods


class DeliverablePublisher(
    _PolicyMethods,
    _ManifestMethods,
    _FileOpsMethods,
    _SubmitPayloadMethods,
    _ManuscriptMethods,
):
    def __init__(
        self,
        *,
        settings: Optional[DeliverableSettings] = None,
        project_root: Optional[Path] = None,
        runtime_dir: Optional[Path] = None,
        paper_builder: Optional[PaperBuilder] = None,
    ) -> None:
        self._settings = settings or get_deliverable_settings()
        self._project_root = (project_root or Path(__file__).resolve().parents[3]).resolve()
        self._runtime_dir = (runtime_dir or (self._project_root / "runtime")).resolve()
        self._paper_builder = paper_builder or PaperBuilder()

    @property
    def settings(self) -> DeliverableSettings:
        return self._settings

    def get_session_dir(self, session_id: str, *, create: bool = False) -> Path:
        normalized = str(session_id or "").strip()
        if not normalized:
            raise ValueError("session_id is required")
        session_base = self._normalize_session_base(normalized)
        if not session_base:
            raise ValueError("session_id is invalid")
        session_dir = (self._runtime_dir / f"session_{session_base}").resolve()
        if create:
            session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    @staticmethod
    def _normalize_session_base(value: str) -> str:
        return normalize_session_base(value)

    def publish_from_tool_result(
        self,
        *,
        session_id: Optional[str],
        tool_name: str,
        raw_result: Any,
        summary: Optional[str] = None,
        source: Optional[Dict[str, Any]] = None,
        job_id: Optional[str] = None,
        plan_id: Optional[int] = None,
        task_id: Optional[int] = None,
        task_name: Optional[str] = None,
        task_instruction: Optional[str] = None,
        publish_status: str = "final",
    ) -> Optional[PublishReport]:
        if not self._settings.enabled:
            return None
        normalized_session = str(session_id or "").strip()
        if not normalized_session:
            return None
        normalized_tool = str(tool_name or "").strip().lower()
        release_meta = self._extract_release_metadata(raw_result)
        blocked_manuscript_release = self._is_blocked_manuscript_release(tool_name, raw_result)
        if (
            self._is_failed_result(raw_result)
            and not self._has_publishable_partial_result(raw_result)
            and not blocked_manuscript_release
        ):
            return None

        session_dir = self.get_session_dir(normalized_session, create=True)
        deliverables_root = session_dir / "deliverables"
        latest_root = deliverables_root / "latest"
        latest_root.mkdir(parents=True, exist_ok=True)

        self._prune_legacy_modules(latest_root)
        self._cleanup_docs_module(latest_root / "docs")
        for module in self._settings.modules:
            (latest_root / module).mkdir(parents=True, exist_ok=True)
        latest_manifest_path = deliverables_root / "manifest_latest.json"
        previous_manifest = self._read_manifest(latest_manifest_path)
        if normalized_tool in {"manuscript_writer", "review_pack_writer"}:
            self._purge_manuscript_public_outputs(latest_root)

        now = _utc_now()
        source_payload = {
            "session_id": normalized_session,
            "tool_name": tool_name,
            "job_id": job_id,
            "plan_id": plan_id,
            "task_id": task_id,
            "task_name": task_name,
            "source": source or {},
        }
        if isinstance(raw_result, dict):
            run_stats = raw_result.get("run_stats")
            if isinstance(run_stats, dict):
                source_payload["run_stats"] = run_stats
        source_payload["release_state"] = release_meta["release_state"]
        source_payload["public_release_ready"] = release_meta["public_release_ready"]
        items: List[Dict[str, Any]] = []
        submit_artifacts_requested: Optional[int] = None
        submit_artifacts_published: Optional[int] = None
        submit_artifacts_skipped: Optional[int] = None
        submit_warnings: List[str] = []

        if blocked_manuscript_release:
            release_items = self._publish_release_summary(
                latest_root=latest_root,
                release_summary=release_meta.get("release_summary") or "",
                updated_at=now,
                source_path=f"job:{job_id}" if job_id else f"task:{task_id or 'unknown'}",
            )
            items.extend(release_items)
        else:
            submit_payload = self._extract_deliverable_submit(raw_result)
            if submit_payload:
                submit_result = self._apply_deliverable_submit_payload(
                    payload=submit_payload,
                    latest_root=latest_root,
                    session_dir=session_dir,
                    raw_result=raw_result,
                    publish_status=publish_status,
                    now=now,
                    previous_manifest=previous_manifest,
                )
                items.extend(submit_result["items"])
                submit_artifacts_requested = submit_result["requested_count"]
                submit_artifacts_published = len(submit_result["items"])
                submit_artifacts_skipped = max(
                    0,
                    int(submit_artifacts_requested or 0) - int(submit_artifacts_published or 0),
                )
                submit_warnings = list(submit_result["warnings"])
                if submit_artifacts_requested and submit_artifacts_skipped:
                    source_payload["submit_status"] = "partial" if submit_artifacts_published else "failed"
                    source_payload["submit_warnings"] = list(submit_warnings)

        manuscript_result = self._extract_manuscript_result(raw_result)
        manuscript_structure = self._extract_manuscript_structure_metadata(raw_result)
        manuscript_items: List[Dict[str, Any]] = []
        if not blocked_manuscript_release:
            manuscript_items = self._publish_manuscript_outputs(
                latest_root=latest_root,
                raw_result=raw_result,
                publish_status=publish_status,
                updated_at=now,
                source_task_id=task_id,
                task_name=task_name,
                previous_manifest=previous_manifest,
            )
            if manuscript_items:
                items.extend(manuscript_items)

        title = task_name or "Research Project"
        paper_dir = latest_root / "paper"
        refs_dir = latest_root / "refs"

        if not items and submit_artifacts_requested is None:
            return None

        if blocked_manuscript_release:
            paper_status = {
                "completed_sections": [],
                "missing_sections": [],
                "total_sections": 0,
                "completed_count": 0,
                "section_profile": manuscript_structure.get("section_profile") or "research",
                "applicable_sections": [],
            }
        elif paper_dir.exists() and (items or self._manifest_items_from_manifest(previous_manifest)):
            self._paper_builder.ensure_structure(
                paper_dir=paper_dir,
                refs_dir=refs_dir,
                title=title,
                section_profile=manuscript_structure.get("section_profile"),
                section_order=manuscript_structure.get("applicable_sections"),
            )
            paper_status = self._paper_builder.get_status(
                paper_dir=paper_dir,
                section_profile=manuscript_structure.get("section_profile"),
                section_order=manuscript_structure.get("applicable_sections"),
            ).to_dict()
        else:
            paper_status = {
                "completed_sections": [],
                "missing_sections": [],
                "total_sections": 0,
                "completed_count": 0,
                "section_profile": manuscript_structure.get("section_profile") or "research",
                "applicable_sections": [],
            }

        deduped_updates = self._dedupe_items(items)
        merged_items = self._collect_latest_items(
            latest_root=latest_root,
            previous_manifest=previous_manifest,
            updated_items=deduped_updates,
            fallback_timestamp=now,
            fallback_status=publish_status,
        )
        modules = sorted({item["module"] for item in merged_items})
        version_id = self._new_version_id()
        manifest = self._build_manifest(
            version_id=version_id,
            created_at=now,
            source=source_payload,
            items=merged_items,
            paper_status=paper_status,
            release_state=str(release_meta.get("release_state") or "final"),
            public_release_ready=bool(release_meta.get("public_release_ready")),
            release_summary=release_meta.get("release_summary"),
            hidden_artifact_prefixes=list(release_meta.get("hidden_artifact_prefixes") or []),
        )
        _atomic_write_json(latest_manifest_path, manifest)

        return PublishReport(
            version_id=version_id,
            published_files_count=len(merged_items),
            published_modules=modules,
            manifest_path=str(latest_manifest_path),
            paper_status=paper_status,
            release_state=str(release_meta.get("release_state") or "final"),
            public_release_ready=bool(release_meta.get("public_release_ready")),
            release_summary=release_meta.get("release_summary"),
            hidden_artifact_prefixes=list(release_meta.get("hidden_artifact_prefixes") or []),
            submit_artifacts_requested=submit_artifacts_requested,
            submit_artifacts_published=submit_artifacts_published,
            submit_artifacts_skipped=submit_artifacts_skipped,
            warnings=submit_warnings,
        )


_publisher: Optional[DeliverablePublisher] = None


def get_deliverable_publisher() -> DeliverablePublisher:
    global _publisher
    if _publisher is None:
        _publisher = DeliverablePublisher()
    return _publisher


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["DeliverablePublisher", "PublishReport", "get_deliverable_publisher"]
