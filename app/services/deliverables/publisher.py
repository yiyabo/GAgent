from __future__ import annotations

import json
import logging
import os
import re
import shutil
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

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


class DeliverablePublisher(_PolicyMethods, _ManifestMethods, _FileOpsMethods, _SubmitPayloadMethods):
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

    @staticmethod
    def _normalize_manuscript_section(section: Optional[str]) -> Optional[str]:
        if not isinstance(section, str):
            return None
        key = section.strip().lower()
        mapping = {
            "methods": "method",
            "method": "method",
            "experiments": "experiment",
            "experiment": "experiment",
            "results": "result",
            "result": "result",
            "abstract": "abstract",
            "introduction": "introduction",
            "discussion": "discussion",
            "conclusion": "conclusion",
        }
        return mapping.get(key)

    @staticmethod
    def _extract_manuscript_result(payload: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None

        normalized_tool = str(payload.get("tool") or "").strip().lower()
        if normalized_tool == "manuscript_writer":
            return payload

        if normalized_tool == "review_pack_writer":
            draft = payload.get("draft")
            if not isinstance(draft, dict):
                return None
            draft_tool = str(draft.get("tool") or "").strip().lower()
            if draft_tool == "manuscript_writer":
                return draft
            if draft_tool:
                return None
            if isinstance(draft.get("sections"), list) or isinstance(draft.get("output_path"), str):
                return draft

        return None

    @staticmethod
    def _extract_release_metadata(payload: Any) -> Dict[str, Any]:
        manuscript_result = DeliverablePublisher._extract_manuscript_result(payload)
        source_payload = payload if isinstance(payload, dict) else {}
        preferred_payload = source_payload
        if not any(
            key in source_payload
            for key in ("public_release_ready", "release_state", "release_summary", "hidden_artifact_prefixes")
        ) and isinstance(manuscript_result, dict):
            preferred_payload = manuscript_result

        public_release_ready = (
            bool(preferred_payload.get("public_release_ready"))
            if isinstance(preferred_payload, dict) and preferred_payload.get("public_release_ready") is not None
            else not DeliverablePublisher._is_failed_result(payload)
        )
        release_state = str(
            (preferred_payload.get("release_state") if isinstance(preferred_payload, dict) else None)
            or ("final" if public_release_ready else "blocked")
        ).strip().lower()
        release_summary = (
            str(preferred_payload.get("release_summary") or "").strip()
            if isinstance(preferred_payload, dict)
            else ""
        ) or None

        hidden_artifact_prefixes: List[str] = []
        for candidate_payload in (manuscript_result, source_payload):
            if not isinstance(candidate_payload, dict):
                continue
            values = candidate_payload.get("hidden_artifact_prefixes")
            if not isinstance(values, list):
                continue
            for item in values:
                normalized = str(item or "").strip().lstrip("/").replace("\\", "/")
                if normalized and normalized not in hidden_artifact_prefixes:
                    hidden_artifact_prefixes.append(normalized)

        return {
            "public_release_ready": public_release_ready,
            "release_state": release_state,
            "release_summary": release_summary,
            "hidden_artifact_prefixes": hidden_artifact_prefixes,
        }

    def _extract_manuscript_structure_metadata(self, payload: Any) -> Dict[str, Any]:
        manuscript_result = self._extract_manuscript_result(payload)
        section_profile = "research"
        applicable_sections: List[str] = []
        if isinstance(manuscript_result, dict):
            raw_profile = manuscript_result.get("section_profile")
            section_profile = self._paper_builder.normalize_section_profile(
                raw_profile if isinstance(raw_profile, str) else None
            )
            raw_applicable = manuscript_result.get("applicable_sections")
            if isinstance(raw_applicable, list):
                for item in raw_applicable:
                    normalized = self._normalize_manuscript_section(str(item or "").strip()) or str(item or "").strip().lower()
                    if normalized and normalized != "references" and normalized not in applicable_sections:
                        applicable_sections.append(normalized)
            elif isinstance(manuscript_result.get("sections"), list):
                for row in manuscript_result.get("sections") or []:
                    if not isinstance(row, dict):
                        continue
                    normalized = self._normalize_manuscript_section(str(row.get("section") or "").strip())
                    if normalized and normalized not in applicable_sections:
                        applicable_sections.append(normalized)
        if not applicable_sections:
            applicable_sections = list(
                self._paper_builder.section_order(section_profile=section_profile)
            )
        return {
            "section_profile": section_profile,
            "applicable_sections": applicable_sections,
        }

    @staticmethod
    def _is_blocked_manuscript_release(tool_name: str, raw_result: Any) -> bool:
        normalized_tool = str(tool_name or "").strip().lower()
        if normalized_tool not in {"manuscript_writer", "review_pack_writer"}:
            return False
        release_meta = DeliverablePublisher._extract_release_metadata(raw_result)
        release_state = str(release_meta.get("release_state") or "").strip().lower()
        if release_state:
            return release_state == "blocked"
        return not bool(release_meta.get("public_release_ready"))

    @staticmethod
    def _parse_bibtex_entries(bib_text: str) -> Dict[str, Dict[str, str]]:
        entries: Dict[str, Dict[str, str]] = {}
        if not bib_text:
            return entries
        for match in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", bib_text, flags=re.DOTALL):
            key = str(match.group(2) or "").strip()
            body = str(match.group(3) or "")
            if not key:
                continue
            fields: Dict[str, str] = {}
            for field_match in re.finditer(r"(\w+)\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}", body, flags=re.DOTALL):
                field_name = str(field_match.group(1) or "").strip().lower()
                field_value = re.sub(r"\s+", " ", str(field_match.group(2) or "")).strip()
                if field_name and field_value:
                    fields[field_name] = field_value
            entries[key] = fields
        return entries

    @staticmethod
    def _format_author_year(fields: Dict[str, str]) -> str:
        authors_raw = str(fields.get("author") or "").strip()
        year = str(fields.get("year") or "n.d.").strip() or "n.d."
        if not authors_raw:
            return f"Unknown, {year}"
        authors = [part.strip() for part in authors_raw.split(" and ") if part.strip()]
        surnames: List[str] = []
        for author in authors:
            if "," in author:
                surnames.append(author.split(",", 1)[0].strip())
            else:
                surname = author.split()[-1].strip()
                surnames.append(surname or author.strip())
        if not surnames:
            return f"Unknown, {year}"
        if len(surnames) == 1:
            return f"{surnames[0]}, {year}"
        if len(surnames) == 2:
            return f"{surnames[0]} and {surnames[1]}, {year}"
        return f"{surnames[0]} et al., {year}"

    @classmethod
    def _render_pi_readable_report(cls, markdown_text: str, bib_entries: Dict[str, Dict[str, str]]) -> str:
        text = str(markdown_text or "")
        if not text or not bib_entries:
            return text
        reference_keys: List[str] = []
        references_match = re.search(r"(?ms)^## References\s*$", text)
        if references_match:
            for key_match in re.finditer(r"\[@([A-Za-z0-9_:\-]+)\]", text[references_match.end() :]):
                key = str(key_match.group(1) or "").strip()
                if key and key not in reference_keys:
                    reference_keys.append(key)

        def _replace_citation(match: re.Match[str]) -> str:
            raw_group = str(match.group(1) or "")
            rendered: List[str] = []
            for part in raw_group.split(";"):
                key = part.strip()
                if key.startswith("@"):
                    key = key[1:].strip()
                if not key:
                    continue
                fields = bib_entries.get(key)
                if not fields:
                    rendered.append(f"@{key}")
                    continue
                rendered.append(cls._format_author_year(fields))
            return "(" + "; ".join(rendered) + ")" if rendered else match.group(0)

        text = re.sub(
            r"\[((?:\s*@[A-Za-z0-9_:\-]+\s*(?:;\s*@[A-Za-z0-9_:\-]+\s*)*))\]",
            _replace_citation,
            text,
        )

        if references_match:
            prefix = text[: references_match.start()].rstrip()
            reference_lines = ["## References", ""]
            for key in reference_keys:
                fields = bib_entries.get(key) or {}
                title = str(fields.get("title") or key).strip()
                journal = str(fields.get("journal") or "Unknown journal").strip()
                doi = str(fields.get("doi") or "").strip()
                doi_text = f". DOI: {doi}" if doi else ""
                reference_lines.append(
                    f"- {cls._format_author_year(fields)}. {title}. {journal}{doi_text}"
                )
            if len(reference_lines) == 2:
                reference_lines.append("- Not available")
            text = prefix + "\n\n" + "\n".join(reference_lines) + "\n"
        return text

    def _purge_manuscript_public_outputs(self, latest_root: Path) -> None:
        paper_dir = latest_root / "paper"
        refs_dir = latest_root / "refs"
        docs_dir = latest_root / "docs"

        if paper_dir.exists():
            shutil.rmtree(paper_dir, ignore_errors=True)
        if refs_dir.exists():
            shutil.rmtree(refs_dir, ignore_errors=True)

        paper_dir.mkdir(parents=True, exist_ok=True)
        refs_dir.mkdir(parents=True, exist_ok=True)

        if docs_dir.exists() and docs_dir.is_dir():
            for file_path in docs_dir.iterdir():
                if not file_path.is_file():
                    continue
                if file_path.name == "release_summary.md":
                    try:
                        file_path.unlink()
                    except Exception:
                        logger.debug("Failed to remove stale release summary: %s", file_path)
                    continue
                if file_path.stem.lower() in DOC_ALLOWED_STEMS:
                    try:
                        file_path.unlink()
                    except Exception:
                        logger.debug("Failed to remove manuscript doc artifact: %s", file_path)

    def _publish_release_summary(
        self,
        *,
        latest_root: Path,
        release_summary: str,
        updated_at: str,
        source_path: Optional[str],
    ) -> List[Dict[str, Any]]:
        docs_dir = latest_root / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        summary_path = docs_dir / "release_summary.md"
        summary_text = (release_summary or "").strip() or "Publication blocked: the manuscript did not pass the final release gate."
        summary_path.write_text(summary_text + "\n", encoding="utf-8")
        return [
            {
                "module": "docs",
                "path": str(summary_path.relative_to(latest_root)),
                "status": "final",
                "size": summary_path.stat().st_size,
                "updated_at": updated_at,
                "source_path": source_path,
            }
        ]

    def _resolve_reference_library_path(
        self,
        *,
        latest_root: Path,
        raw_result: Any,
        manuscript_result: Dict[str, Any],
    ) -> Optional[Path]:
        session_dir = latest_root.parent.parent
        candidates: List[str] = []
        for payload in (manuscript_result, raw_result):
            if not isinstance(payload, dict):
                continue
            value = payload.get("reference_library_path")
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
            outputs = payload.get("outputs")
            if isinstance(outputs, dict):
                for key in ("references_bib", "reference_library_path"):
                    value = outputs.get(key)
                    if isinstance(value, str) and value.strip():
                        candidates.append(value.strip())
            pack = payload.get("pack")
            if isinstance(pack, dict):
                pack_outputs = pack.get("outputs")
                if isinstance(pack_outputs, dict):
                    value = pack_outputs.get("references_bib")
                    if isinstance(value, str) and value.strip():
                        candidates.append(value.strip())
        for candidate in candidates:
            resolved = self._resolve_path(candidate, session_dir=session_dir)
            if resolved is not None and resolved.is_file():
                return resolved
        return None

    def _publish_manuscript_outputs(
        self,
        *,
        latest_root: Path,
        raw_result: Any,
        publish_status: str,
        updated_at: str,
        source_task_id: Optional[int],
        task_name: Optional[str],
        previous_manifest: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        manuscript_result = self._extract_manuscript_result(raw_result)
        if not isinstance(manuscript_result, dict):
            return []

        items: List[Dict[str, Any]] = []
        title = task_name or "Research Project"
        paper_dir = latest_root / "paper"
        refs_dir = latest_root / "refs"
        docs_dir = latest_root / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        structure_meta = self._extract_manuscript_structure_metadata(manuscript_result)
        section_profile = structure_meta.get("section_profile")
        applicable_sections = structure_meta.get("applicable_sections")
        paper_structure_initialized = False
        published_paper_sections = False

        def _ensure_paper_structure() -> None:
            nonlocal paper_structure_initialized
            if paper_structure_initialized:
                return
            self._paper_builder.ensure_structure(
                paper_dir=paper_dir,
                refs_dir=refs_dir,
                title=title,
                section_profile=section_profile if isinstance(section_profile, str) else None,
                section_order=applicable_sections if isinstance(applicable_sections, list) else None,
            )
            paper_structure_initialized = True

        sections_payload = manuscript_result.get("sections")
        if isinstance(sections_payload, list):
            for row in sections_payload:
                if not isinstance(row, dict):
                    continue
                if row.get("substantive") is False:
                    continue
                raw_section = str(row.get("section") or "").strip().lower()
                section_key = self._normalize_manuscript_section(raw_section)
                raw_path = row.get("path")
                if not isinstance(raw_path, str):
                    continue
                source = self._resolve_path(raw_path, session_dir=latest_root.parent.parent)
                if source is None or not source.is_file():
                    continue
                try:
                    text = source.read_text(encoding="utf-8")
                except Exception:
                    continue
                staged_figures = self._stage_figures_from_section_text(
                    latest_root=latest_root,
                    section_source=source,
                    text=text,
                    session_dir=latest_root.parent.parent,
                    previous_manifest=previous_manifest,
                )
                for figure_path in staged_figures:
                    items.append(
                        {
                            "module": "image_tabular",
                            "path": str(figure_path.relative_to(latest_root)),
                            "status": publish_status,
                            "size": figure_path.stat().st_size,
                            "updated_at": updated_at,
                            "source_path": self._to_project_relative(source),
                        }
                    )
                if raw_section in {"reference", "references"}:
                    references_doc = docs_dir / "references.md"
                    ref_text = text.strip()
                    references_doc.write_text(ref_text + ("\n" if ref_text else ""), encoding="utf-8")
                    items.append(
                        {
                            "module": "docs",
                            "path": str(references_doc.relative_to(latest_root)),
                            "status": publish_status,
                            "size": references_doc.stat().st_size,
                            "updated_at": updated_at,
                            "source_path": self._to_project_relative(source),
                        }
                    )
                    continue
                if not section_key:
                    continue
                _ensure_paper_structure()
                section_path = self._paper_builder.update_section(
                    paper_dir=paper_dir,
                    section=section_key,
                    content=text,
                )
                published_paper_sections = True
                items.append(
                    {
                        "module": "paper",
                        "path": str(section_path.relative_to(latest_root)),
                        "status": publish_status,
                        "size": section_path.stat().st_size,
                        "updated_at": updated_at,
                        "source_path": self._to_project_relative(source),
                    }
                )
                doc_stem = "methods" if section_key == "method" else section_key
                if doc_stem in DOC_ALLOWED_STEMS:
                    doc_path = docs_dir / f"{doc_stem}.md"
                    doc_text = text.strip()
                    doc_path.write_text(doc_text + ("\n" if doc_text else ""), encoding="utf-8")
                    items.append(
                        {
                            "module": "docs",
                            "path": str(doc_path.relative_to(latest_root)),
                            "status": publish_status,
                            "size": doc_path.stat().st_size,
                            "updated_at": updated_at,
                            "source_path": self._to_project_relative(source),
                        }
                    )

        if not published_paper_sections:
            shutil.rmtree(paper_dir, ignore_errors=True)
            if refs_dir.exists() and not any(self._refs_file_is_substantive(path) for path in refs_dir.rglob("*") if path.is_file()):
                shutil.rmtree(refs_dir, ignore_errors=True)

        analysis_ref = manuscript_result.get("effective_analysis_path") or manuscript_result.get("analysis_path")
        if isinstance(analysis_ref, str) and analysis_ref.strip():
            analysis_source = self._resolve_path(analysis_ref, session_dir=latest_root.parent.parent)
            if analysis_source is not None and analysis_source.is_file():
                try:
                    analysis_text = analysis_source.read_text(encoding="utf-8").strip()
                except Exception:
                    analysis_text = ""
                analysis_doc = docs_dir / "analysis.md"
                analysis_doc.write_text(analysis_text + ("\n" if analysis_text else ""), encoding="utf-8")
                items.append(
                    {
                        "module": "docs",
                        "path": str(analysis_doc.relative_to(latest_root)),
                        "status": publish_status,
                        "size": analysis_doc.stat().st_size,
                        "updated_at": updated_at,
                        "source_path": self._to_project_relative(analysis_source),
                    }
                )

        output_ref = manuscript_result.get("effective_output_path") or manuscript_result.get("output_path")
        if isinstance(output_ref, str) and output_ref.strip():
            output_source = self._resolve_path(output_ref, session_dir=latest_root.parent.parent)
            if output_source is not None and output_source.is_file():
                try:
                    output_text = output_source.read_text(encoding="utf-8").strip()
                except Exception:
                    output_text = ""
                reference_library = self._resolve_reference_library_path(
                    latest_root=latest_root,
                    raw_result=raw_result,
                    manuscript_result=manuscript_result,
                )
                if reference_library is not None:
                    try:
                        bib_entries = self._parse_bibtex_entries(
                            reference_library.read_text(encoding="utf-8")
                        )
                    except Exception:
                        bib_entries = {}
                    output_text = self._render_pi_readable_report(output_text, bib_entries)
                report_doc = docs_dir / "report.md"
                report_doc.write_text(output_text + ("\n" if output_text else ""), encoding="utf-8")
                items.append(
                    {
                        "module": "docs",
                        "path": str(report_doc.relative_to(latest_root)),
                        "status": publish_status,
                        "size": report_doc.stat().st_size,
                        "updated_at": updated_at,
                        "source_path": self._to_project_relative(output_source),
                    }
                )

        # Keep source task info explicit for generated synthetic files.
        source_tag = f"task:{source_task_id or 'unknown'}"
        for item in items:
            if not item.get("source_path"):
                item["source_path"] = source_tag
        return items

    @staticmethod
    def _is_failed_result(raw_result: Any) -> bool:
        if not isinstance(raw_result, dict):
            return False
        success_value = raw_result.get("success")
        if success_value is False:
            return True
        status_value = str(raw_result.get("status") or "").strip().lower()
        return status_value in {"failed", "error"}

    @staticmethod
    def _has_publishable_partial_result(raw_result: Any) -> bool:
        if not isinstance(raw_result, dict):
            return False

        manuscript_result = DeliverablePublisher._extract_manuscript_result(raw_result)
        if isinstance(manuscript_result, dict):
            sections = manuscript_result.get("sections")
            if isinstance(sections, list) and sections:
                return True
            for key in (
                "partial_output_path",
                "combined_partial",
                "effective_output_path",
                "output_path",
                "effective_analysis_path",
                "analysis_path",
            ):
                value = manuscript_result.get(key)
                if isinstance(value, str) and value.strip():
                    return True

        for key in ("partial_output_path", "combined_partial"):
            value = raw_result.get(key)
            if isinstance(value, str) and value.strip():
                return True
        return bool(raw_result.get("partial"))


_publisher: Optional[DeliverablePublisher] = None


def get_deliverable_publisher() -> DeliverablePublisher:
    global _publisher
    if _publisher is None:
        _publisher = DeliverablePublisher()
    return _publisher


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["DeliverablePublisher", "PublishReport", "get_deliverable_publisher"]
