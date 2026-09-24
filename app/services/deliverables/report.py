"""Deliverable publish report value object.

Extracted from ``publisher.py`` (cluster "PublishReport(frozen) +
format_deliverable_submit_summary") per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.3. The frozen
``PublishReport`` payload and its string rendering carry the release/submit
summary keys consumed by the chat and plan publish paths, so this module is a
pure data cluster with no dependency on the publisher or its siblings.

Compatibility contract: the ``publisher`` facade re-exports both names, so
``from app.services.deliverables.publisher import PublishReport`` (tests,
``projector.py``, ``action_handlers``) keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class PublishReport:
    version_id: str
    published_files_count: int
    published_modules: List[str]
    manifest_path: str
    paper_status: Dict[str, Any]
    release_state: str = "final"
    public_release_ready: bool = True
    release_summary: Optional[str] = None
    hidden_artifact_prefixes: List[str] = field(default_factory=list)
    submit_artifacts_requested: Optional[int] = None
    submit_artifacts_published: Optional[int] = None
    submit_artifacts_skipped: Optional[int] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "version_id": self.version_id,
            "published_files_count": self.published_files_count,
            "published_modules": list(self.published_modules),
            "manifest_path": self.manifest_path,
            "paper_status": dict(self.paper_status),
            "release_state": self.release_state,
            "public_release_ready": bool(self.public_release_ready),
            "release_summary": self.release_summary,
            "hidden_artifact_prefixes": list(self.hidden_artifact_prefixes),
        }
        if self.submit_artifacts_requested is not None:
            payload["submit_artifacts_requested"] = int(self.submit_artifacts_requested)
            payload["submit_artifacts_published"] = int(self.submit_artifacts_published or 0)
            payload["submit_artifacts_skipped"] = int(self.submit_artifacts_skipped or 0)
            payload["warnings"] = list(self.warnings)
        return payload

    def submit_summary(self) -> Optional[str]:
        if self.submit_artifacts_requested is None:
            return None
        published = int(self.submit_artifacts_published or 0)
        skipped = int(self.submit_artifacts_skipped or 0)
        if skipped > 0 and self.warnings:
            preview = "; ".join(str(item).strip() for item in self.warnings[:2] if str(item).strip())
            suffix = f": {preview}" if preview else ""
            return (
                f"Deliverable submit published {published} artifact(s); "
                f"skipped {skipped} with warnings{suffix}"
            )
        return f"Deliverable submit published {published} artifact(s) to Deliverables"


def format_deliverable_submit_summary(report: Optional[PublishReport]) -> Optional[str]:
    if report is None:
        return None
    return report.submit_summary()


__all__ = ["PublishReport", "format_deliverable_submit_summary"]
