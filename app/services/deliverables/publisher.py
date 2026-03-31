from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from uuid import uuid4

from app.config.deliverable_config import DeliverableSettings, get_deliverable_settings
from app.services.session_paths import normalize_session_base

from .paper_builder import PaperBuilder

logger = logging.getLogger(__name__)

CODE_EXTS = {
    ".py",
    ".ipynb",
    ".r",
    ".jl",
    ".sh",
    ".bash",
    ".zsh",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".cpp",
    ".c",
    ".rs",
    ".go",
    ".sql",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
}

TABULAR_EXTS = {
    ".csv",
    ".tsv",
    ".xlsx",
    ".xls",
    ".jsonl",
    ".parquet",
}

IMAGE_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".gif",
    ".bmp",
    ".tiff",
    ".webp",
}

DOC_EXTS = {
    ".md",
    ".markdown",
    ".txt",
    ".docx",
    ".rtf",
}

PAPER_EXTS = {
    ".tex",
    ".pdf",
    ".cls",
    ".sty",
    ".bst",
}

REF_EXTS = {
    ".bib",
}

# PDFs next to manuscript sources are usually downloaded references; keep only obvious build outputs in paper/
MANUSCRIPT_PDF_STEMS = {
    "main",
    "manuscript",
    "paper",
    "submission",
    "preprint",
}

DELIVERABLE_EXTS = CODE_EXTS | TABULAR_EXTS | IMAGE_EXTS | DOC_EXTS | PAPER_EXTS | REF_EXTS

DOC_ALLOWED_STEMS = {
    "abstract",
    "introduction",
    "method",
    "methods",
    "experiment",
    "result",
    "results",
    "discussion",
    "conclusion",
    "reference",
    "references",
    "report",
    "analysis",
    "evidence_coverage",
    "release_summary",
    "study_matrix",
    "survey",
    "summary",
}

SOURCE_OWNERSHIP_MAP = ".source_owners.json"

PATH_HINT_KEYS = {
    "path",
    "file_path",
    "output_path",
    "save_path",
    "manifest_path",
    "result_path",
    "preview_path",
    "pdf_dir",
    "references_bib",
    "reference_library_path",
    "evidence_md",
    "evidence_coverage_md",
    "study_matrix_md",
    "coverage_report_path",
    "evidence_coverage_path",
    "study_matrix_path",
    "study_cards_jsonl",
    "coverage_report_json",
    "library_jsonl",
    "manuscript_output",
    "manuscript_partial",
    "effective_output_path",
    "effective_analysis_path",
    "analysis_path",
    "partial_output_path",
    "sections_dir",
    "reviews_dir",
    "combined_path",
    "combined_partial",
    "merge_queue",
    "citation_validation_path",
    "out_dir",
    "output_dir",
    "task_directory",
    "task_directory_full",
    "task_root_directory",
    "run_directory",
    "working_directory",
    "session_directory",
    "log_path",
}

PATH_CONTAINER_KEYS = {
    "artifacts",
    "files",
    "generated_files",
    "outputs",
    "produced_files",
    "saved_files",
}

MAX_PATH_CANDIDATE_LENGTH = 1024
PATH_LIST_KEYS = {"paths", "files", "directories", "dirs", "file_paths"}
EXPLICIT_FILE_LIST_KEYS = {
    "produced_files",
    "generated_files",
}
EXPLICIT_FILE_ITEM_KEYS = {
    "path",
    "file",
    "file_path",
    "output_path",
    "save_path",
    "result_path",
}

TEXT_DELIVERABLE_TOOLS = {
    "code_executor",
    "manuscript_writer",
    "review_pack_writer",
    "paper_replication",
}

NOISE_PATH_SEGMENTS = {
    "/tool_outputs/",
    "/information_sessions/",
}

NOISE_FILENAMES = {
    "manifest.json",
    "preview.json",
    "result.json",
}

BLOCKED_SOURCE_SEGMENTS = {
    "node_modules",
    ".git",
    "__MACOSX",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".eggs",
    "dist",
    ".cursor",
    ".codex",
    ".claude",
}

BLOCKED_PROJECT_DIRS = {
    "app",
    "tool_box",
    "execute_memory",
    "web-ui",
    "scripts",
    "docker",
    ".github",
}

BLOCKED_SOURCE_FILENAMES = {
    ".DS_Store",
    "Thumbs.db",
}

_CC_RUN_ARTIFACT_RE = re.compile(r"^run_\d{8}_\d{6}_")

# Agent-explicit deliverables (see DELIVERABLES_INGEST_MODE=explicit)
DELIVERABLE_SUBMIT_KEY = "deliverable_submit"
EXPLICIT_AUTO_PUBLISH_TOOLS = frozenset({"manuscript_writer", "review_pack_writer"})

CC_INTERMEDIATE_SCRIPT_EXTS = {".py", ".sh", ".bash", ".r", ".jl"}


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


class DeliverablePublisher:
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
            if self._should_use_legacy_file_ingest(normalized_tool):
                explicit_file_candidates = self._extract_explicit_file_candidates(raw_result)
                if explicit_file_candidates:
                    resolved_files = self._resolve_files(
                        path_candidates=explicit_file_candidates,
                        session_dir=session_dir,
                        allow_directories=False,
                    )
                else:
                    path_candidates = self._extract_path_candidates(raw_result)
                    resolved_files = self._resolve_files(
                        path_candidates=path_candidates,
                        session_dir=session_dir,
                        allow_directories=True,
                    )
                for file_path in resolved_files:
                    module = self._classify_module(file_path)
                    if module is None:
                        continue
                    copied = self._copy_resolved_file_to_deliverables(
                        file_path=file_path,
                        module=module,
                        latest_root=latest_root,
                        session_dir=session_dir,
                        raw_result=raw_result,
                        publish_status=publish_status,
                        now=now,
                        previous_manifest=previous_manifest,
                        from_explicit_submit=False,
                    )
                    if copied is not None:
                        items.append(copied)

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

        manuscript_has_section_artifacts = any(
            isinstance(item, dict)
            and str(item.get("module") or "").strip().lower() == "paper"
            and str(item.get("path") or "").strip().startswith("paper/sections/")
            for item in manuscript_items
        )

        text_blob = self._extract_text_blob(raw_result=raw_result, summary=summary)
        section: Optional[str] = None
        # manuscript_writer outputs with explicit section artifacts should be source of truth;
        # skip generic text-based section inference to avoid overwriting section content with
        # summaries (e.g., "manuscript finished").
        if (
            not blocked_manuscript_release
            and not manuscript_has_section_artifacts
            and self._should_use_legacy_file_ingest(normalized_tool)
        ):
            if normalized_tool == "code_executor":
                # CC: only publish to paper when task_name implies a section; do not use instruction/text
                section = self._paper_builder.infer_section(
                    task_name=task_name,
                    task_instruction=None,
                    text=None,
                )
            elif self._should_publish_text_blob(tool_name=tool_name, raw_result=raw_result):
                section = self._paper_builder.infer_section(
                    task_name=task_name,
                    task_instruction=task_instruction,
                    text=text_blob,
                )
        title = task_name or "Research Project"
        paper_dir = latest_root / "paper"
        refs_dir = latest_root / "refs"
        if section is not None:
            self._paper_builder.ensure_structure(paper_dir=paper_dir, refs_dir=refs_dir, title=title)
            section_path = self._paper_builder.update_section(
                paper_dir=paper_dir,
                section=section,
                content=text_blob or summary or "",
            )
            items.append(
                {
                    "module": "paper",
                    "path": str(section_path.relative_to(latest_root)),
                    "status": publish_status,
                    "size": section_path.stat().st_size,
                    "updated_at": now,
                    "source_path": f"task:{task_id or 'unknown'}",
                }
            )
            doc_section = "methods" if section == "method" else section
            if doc_section in DOC_ALLOWED_STEMS and (text_blob or summary):
                docs_dir = latest_root / "docs"
                docs_dir.mkdir(parents=True, exist_ok=True)
                section_doc_path = docs_dir / f"{doc_section}.md"
                doc_text = (text_blob or summary or "").strip()
                section_doc_path.write_text(doc_text + ("\n" if doc_text else ""), encoding="utf-8")
                items.append(
                    {
                        "module": "docs",
                        "path": str(section_doc_path.relative_to(latest_root)),
                        "status": publish_status,
                        "size": section_doc_path.stat().st_size,
                        "updated_at": now,
                        "source_path": f"task:{task_id or 'unknown'}",
                    }
                )

        bib_text_candidates = self._extract_bib_text_candidates(raw_result)
        if self._should_use_legacy_file_ingest(normalized_tool):
            for bib_text in bib_text_candidates:
                self._paper_builder.ensure_structure(paper_dir=paper_dir, refs_dir=refs_dir, title=title)
                merged = self._paper_builder.merge_bib_entries(refs_dir=refs_dir, bib_text=bib_text)
                if merged is None:
                    continue
                items.append(
                    {
                        "module": "refs",
                        "path": str(merged.relative_to(latest_root)),
                        "status": publish_status,
                        "size": merged.stat().st_size,
                        "updated_at": now,
                        "source_path": "inline_bib",
                    }
                )

        if not items and submit_artifacts_requested is None:
            return None

        if blocked_manuscript_release:
            paper_status = {
                "completed_sections": [],
                "missing_sections": [],
                "total_sections": 0,
                "completed_count": 0,
            }
        elif paper_dir.exists() and (items or self._manifest_items_from_manifest(previous_manifest)):
            self._paper_builder.ensure_structure(paper_dir=paper_dir, refs_dir=refs_dir, title=title)
            paper_status = self._paper_builder.get_status(paper_dir=paper_dir).to_dict()
        else:
            paper_status = {
                "completed_sections": [],
                "missing_sections": [],
                "total_sections": 0,
                "completed_count": 0,
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

    @staticmethod
    def _is_blocked_manuscript_release(tool_name: str, raw_result: Any) -> bool:
        normalized_tool = str(tool_name or "").strip().lower()
        if normalized_tool not in {"manuscript_writer", "review_pack_writer"}:
            return False
        release_meta = DeliverablePublisher._extract_release_metadata(raw_result)
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
        self._paper_builder.ensure_structure(paper_dir=paper_dir, refs_dir=refs_dir, title=title)
        docs_dir.mkdir(parents=True, exist_ok=True)

        sections_payload = manuscript_result.get("sections")
        if isinstance(sections_payload, list):
            for row in sections_payload:
                if not isinstance(row, dict):
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
                section_path = self._paper_builder.update_section(
                    paper_dir=paper_dir,
                    section=section_key,
                    content=text,
                )
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

    def _extract_explicit_file_candidates(self, payload: Any) -> List[str]:
        found: Set[str] = set()

        def _collect(value: Any) -> None:
            if isinstance(value, str):
                candidate = value.strip()
                if candidate and self._is_path_like(candidate, key_hint="produced_files"):
                    found.add(candidate)
                return
            if isinstance(value, dict):
                for key in EXPLICIT_FILE_ITEM_KEYS:
                    entry = value.get(key)
                    if isinstance(entry, str):
                        candidate = entry.strip()
                        if candidate and self._is_path_like(candidate, key_hint=key):
                            found.add(candidate)
                return

        def _visit(value: Any, key: Optional[str] = None) -> None:
            if value is None:
                return
            if isinstance(value, dict):
                for item_key, item_value in value.items():
                    lowered = str(item_key).strip().lower()
                    if lowered in EXPLICIT_FILE_LIST_KEYS:
                        if isinstance(item_value, dict):
                            for nested in item_value.values():
                                _collect(nested)
                        elif isinstance(item_value, (list, tuple, set)):
                            for nested in item_value:
                                _collect(nested)
                        else:
                            _collect(item_value)
                    if isinstance(item_value, (dict, list, tuple, set)):
                        _visit(item_value, key=lowered)
                return
            if isinstance(value, (list, tuple, set)):
                if key in EXPLICIT_FILE_LIST_KEYS:
                    for item in value:
                        _collect(item)
                    return
                for item in value:
                    if isinstance(item, (dict, list, tuple, set)):
                        _visit(item, key=key)

        _visit(payload, key=None)
        return sorted(found)

    def _extract_path_candidates(self, payload: Any) -> List[str]:
        """Collect path-like strings from tool JSON (legacy ingest only; see ingest_mode)."""
        found: Set[str] = set()

        def _visit(value: Any, key: Optional[str] = None) -> None:
            if value is None:
                return
            if isinstance(value, str):
                hinted = bool(
                    key
                    and (
                        key in PATH_HINT_KEYS
                        or key in PATH_CONTAINER_KEYS
                        or key in PATH_LIST_KEYS
                        or key.endswith("_path")
                        or key.endswith("_file")
                        or key.endswith("_dir")
                        or key.endswith("_paths")
                        or key.endswith("_files")
                    )
                )
                if hinted and self._is_path_like(value, key_hint=key):
                    found.add(value.strip())
                return
            if isinstance(value, dict):
                for item_key, item_value in value.items():
                    lowered = str(item_key).strip().lower()
                    if lowered in PATH_HINT_KEYS or lowered.endswith("_path") or lowered.endswith("_file"):
                        if isinstance(item_value, str) and self._is_path_like(item_value, key_hint=lowered):
                            found.add(item_value.strip())
                    if lowered in PATH_CONTAINER_KEYS:
                        if isinstance(item_value, dict):
                            for nested in item_value.values():
                                if isinstance(nested, str) and self._is_path_like(nested, key_hint=lowered):
                                    found.add(nested.strip())
                        elif isinstance(item_value, list):
                            for nested in item_value:
                                if isinstance(nested, str) and self._is_path_like(nested, key_hint=lowered):
                                    found.add(nested.strip())
                    if isinstance(item_value, (dict, list, tuple, set)):
                        _visit(item_value, key=lowered)
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    if isinstance(item, (dict, list, tuple, set)):
                        _visit(item, key=key)
                    elif (
                        isinstance(item, str)
                        and key is not None
                        and (
                            key in PATH_CONTAINER_KEYS
                            or key in PATH_LIST_KEYS
                            or key.endswith("_paths")
                            or key.endswith("_files")
                        )
                        and self._is_path_like(item, key_hint=key)
                    ):
                        found.add(item.strip())

        _visit(payload, key=None)
        return sorted(found)

    def _extract_bib_text_candidates(self, payload: Any) -> List[str]:
        candidates: List[str] = []

        def _visit(value: Any, key: Optional[str] = None) -> None:
            if value is None:
                return
            if isinstance(value, str):
                if "@" in value and "{" in value and (
                    key == "bibtex"
                    or "@article" in value.lower()
                    or "@inproceedings" in value.lower()
                ):
                    candidates.append(value)
                return
            if isinstance(value, dict):
                for item_key, item_value in value.items():
                    _visit(item_value, key=str(item_key).strip().lower())
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    _visit(item, key=key)

        _visit(payload, key=None)
        return candidates

    def _extract_text_blob(self, *, raw_result: Any, summary: Optional[str]) -> str:
        chunks: List[str] = []
        if isinstance(summary, str) and summary.strip():
            chunks.append(summary.strip())
        if isinstance(raw_result, dict):
            # Claude Code returns {"type":"result","result":"...", "duration_ms":...}; prefer "result" as main text
            cc_text = raw_result.get("result")
            if isinstance(cc_text, str) and cc_text.strip():
                chunks.append(cc_text.strip())
            for key in ("content", "response", "answer", "summary", "text"):
                if key == "result":
                    continue
                value = raw_result.get(key)
                if isinstance(value, str) and value.strip():
                    chunks.append(value.strip())
        elif isinstance(raw_result, str) and raw_result.strip():
            # If raw_result is a JSON string (e.g. CC wrapper), try to parse and extract "result"
            raw_str = raw_result.strip()
            if raw_str.startswith("{"):
                try:
                    parsed = json.loads(raw_str)
                    if isinstance(parsed, dict):
                        cc_text = parsed.get("result")
                        if isinstance(cc_text, str) and cc_text.strip():
                            chunks.append(cc_text.strip())
                except Exception:
                    pass
        if not chunks:
            return ""
        return "\n\n".join(chunks)[:12000]

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
            if module == "docs" and not self._is_allowed_doc_file(file_path):
                continue
            if not self._should_publish_file(module, file_path):
                continue

            key = f"{module}::{rel_path}"
            source = update_map.get(key) or previous_map.get(key) or {}

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
            merged.append(
                {
                    "module": module,
                    "path": rel_path,
                    "status": status_value,
                    "size": stat.st_size,
                    "updated_at": updated_at or fallback_timestamp,
                    "source_path": str(source_path) if source_path is not None else None,
                }
            )
        return sorted(merged, key=lambda item: (str(item.get("module")), str(item.get("path"))))

    def _source_path_is_blocked(self, source_path: str) -> bool:
        """Check if a source_path points to project infrastructure (not research output)."""
        normalized = source_path.replace("\\", "/").strip("/")
        for blocked_dir in BLOCKED_PROJECT_DIRS:
            if normalized.startswith(blocked_dir + "/") or normalized == blocked_dir:
                return True
        if normalized.startswith("runtime/") and "/deliverables/" not in normalized:
            parts = normalized.split("/")
            if len(parts) >= 2 and not parts[1].startswith("session_"):
                return True
        return False

    def _file_belongs_in_deliverables(self, file_path: Path, module: str) -> bool:
        """Heuristic check for orphan files with no source tracking."""
        if module in {"paper", "refs", "docs"}:
            return True
        if module == "image_tabular":
            return file_path.suffix.lower() in (IMAGE_EXTS | TABULAR_EXTS | {".pdf"})
        if module == "code":
            name_lower = file_path.name.lower()
            agent_infra_names = {
                "action_execution.py", "action_handlers.py", "agent.py",
                "agent_routes.py", "artifact_routes.py", "chat_routes.py",
                "plan_routes.py", "stream.py", "llm.py", "settings.py",
                "deep_think_agent.py", "plan_executor.py", "plan_decomposer.py",
                "tool_schemas.py", "tool_executor.py", "publisher.py",
                "session_paths.py", "database.py", "database_config.py",
                "database_pool.py", "middleware.py",
            }
            if name_lower in agent_infra_names:
                return False
            return True
        return True

    def _resolve_files(
        self,
        *,
        path_candidates: Iterable[str],
        session_dir: Path,
        allow_directories: bool = True,
    ) -> List[Path]:
        files: List[Path] = []
        seen: Set[Path] = set()
        for candidate in path_candidates:
            resolved = self._resolve_path(candidate, session_dir=session_dir)
            if resolved is None:
                continue
            if resolved.is_file():
                if resolved not in seen:
                    seen.add(resolved)
                    files.append(resolved)
                continue
            if resolved.is_dir():
                if not allow_directories:
                    continue
                if not self._should_scan_directory(resolved, session_dir=session_dir):
                    continue
                count = 0
                for path in sorted(resolved.rglob("*")):
                    if not path.is_file() or not self._is_allowed_source(path):
                        continue
                    if path in seen:
                        continue
                    seen.add(path)
                    files.append(path)
                    count += 1
                    if count >= 200:
                        break
        return files

    def _should_scan_directory(self, path: Path, *, session_dir: Path) -> bool:
        try:
            resolved = path.resolve()
        except Exception:
            return False

        if resolved in {self._project_root, self._runtime_dir, session_dir.resolve()}:
            return False

        blocked_names = {
            ".git",
            "node_modules",
            "__pycache__",
            ".venv",
            "venv",
            ".pytest_cache",
            ".mypy_cache",
            ".tox",
            ".eggs",
            "dist",
            "tool_outputs",
            "deliverables",
        }
        if any(part in blocked_names for part in resolved.parts):
            return False

        if resolved.parent == self._runtime_dir and resolved.name.startswith(("session_", "session-")):
            return False

        for blocked_dir in BLOCKED_PROJECT_DIRS:
            blocked_path = (self._project_root / blocked_dir).resolve()
            if self._is_within(resolved, blocked_path):
                return False

        return True

    def _resolve_path(self, value: str, *, session_dir: Path) -> Optional[Path]:
        raw = str(value or "").strip()
        if not raw:
            return None
        if "\n" in raw or "\r" in raw:
            return None
        if len(raw) > MAX_PATH_CANDIDATE_LENGTH:
            return None
        if raw.startswith("http://") or raw.startswith("https://"):
            return None
        if raw.startswith("~"):
            return None

        path = Path(raw)
        candidates: List[Path] = []
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.append(session_dir / path)
            candidates.append(self._project_root / path)

        for item in candidates:
            try:
                resolved = item.resolve()
            except Exception:
                continue
            if not resolved.exists():
                continue
            if not self._is_allowed_source(item):
                continue
            return resolved
        return None

    def _is_allowed_source(self, path: Path) -> bool:
        if path.name in BLOCKED_SOURCE_FILENAMES:
            return False
        if path.name.startswith("._"):
            return False
        path_parts = set(Path(os.path.abspath(str(path))).parts)
        if path_parts & BLOCKED_SOURCE_SEGMENTS:
            return False

        lexical_abs = Path(os.path.abspath(str(path)))
        if self._is_within(lexical_abs, self._project_root):
            if self._is_in_blocked_project_dir(lexical_abs):
                return False
            return True
        try:
            resolved = path.resolve()
        except Exception:
            return False
        if self._is_within(resolved, self._project_root):
            if self._is_in_blocked_project_dir(resolved):
                return False
            return True
        return False

    def _is_in_blocked_project_dir(self, abs_path: Path) -> bool:
        """Check if an absolute path falls inside a blocked project directory."""
        for blocked_dir in BLOCKED_PROJECT_DIRS:
            blocked_abs = (self._project_root / blocked_dir).resolve()
            if self._is_within(abs_path, blocked_abs):
                return True
        return False

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _is_path_like(self, value: str, key_hint: Optional[str] = None) -> bool:
        raw = str(value or "").strip()
        if not raw:
            return False
        if "\n" in raw or "\r" in raw:
            return False
        if len(raw) > MAX_PATH_CANDIDATE_LENGTH:
            return False
        if raw.startswith("http://") or raw.startswith("https://"):
            return False
        if raw.startswith("{") and raw.endswith("}"):
            return False
        if key_hint in PATH_HINT_KEYS or key_hint in PATH_CONTAINER_KEYS:
            return True
        if key_hint and (key_hint.endswith("_path") or key_hint.endswith("_file")):
            return True
        if "/" in raw or "\\" in raw:
            if " " in raw and Path(raw).suffix.lower() not in DELIVERABLE_EXTS:
                return False
            if "|" in raw and Path(raw).suffix.lower() not in DELIVERABLE_EXTS:
                return False
            return True
        suffix = Path(raw).suffix.lower()
        return bool(suffix and suffix in DELIVERABLE_EXTS)

    def _classify_module(self, path: Path) -> Optional[str]:
        path_lower = str(path).lower()
        suffix = path.suffix.lower()
        file_stem = path.stem.lower()

        # Reference materials take priority over generic /paper/ path match
        if "/refs/" in path_lower or suffix in REF_EXTS or "references" in path.name.lower():
            return "refs"
        if "/reference_paper" in path_lower and suffix == ".pdf":
            return "refs"
        # Manuscript LaTeX (and class/style) always live under paper/
        if suffix in {".tex", ".cls", ".sty", ".bst"}:
            return "paper"
        # PDFs under paper/ are almost always downloaded papers, not the compiled manuscript
        if suffix == ".pdf" and "/paper/" in path_lower:
            if file_stem in MANUSCRIPT_PDF_STEMS:
                return "paper"
            return "refs"
        if "/paper/" in path_lower:
            return "paper"
        if "/code/" in path_lower or suffix in CODE_EXTS:
            return "code"
        if suffix in IMAGE_EXTS or suffix in TABULAR_EXTS:
            return "image_tabular"
        if suffix == ".pdf":
            if any(token in path_lower for token in ("/fig", "/figure", "/table", "/plot", "/chart")):
                return "image_tabular"
            # PDFs from literature_pipeline are downloaded references, not our paper
            if any(token in path_lower for token in (
                "/literature_pipeline/",
                "/review_pack",
                "/reference_paper",
            )):
                return "refs"
            loose_ref_tokens = (
                "/refs/",
                "/references/",
                "/downloads/",
                "/literature/",
                "/citation",
                "/bibliography",
                "/preprint",
                "/arxiv",
                "/supplement",
                "/supplementary",
            )
            if any(t in path_lower for t in loose_ref_tokens):
                return "refs"
            return None
        if "/docs/" in path_lower or suffix in DOC_EXTS:
            if file_stem in DOC_ALLOWED_STEMS:
                return "docs"
            return None
        if file_stem in DOC_ALLOWED_STEMS and suffix in {".md", ".txt"}:
            return "docs"
        return None

    def _should_publish_file(self, module: str, source_path: Path) -> bool:
        if module not in self._settings.modules:
            return False
        if self._is_noise_artifact_file(source_path):
            return False
        if self._is_cc_intermediate_artifact(source_path):
            return False
        if module == "docs":
            return self._is_allowed_doc_file(source_path)
        if module == "code":
            return self._is_allowed_code_file(source_path)
        return True

    @staticmethod
    def _is_allowed_code_file(path: Path) -> bool:
        """Only actual code files belong in the code module; raw JSON/YAML data files do not."""
        return path.suffix.lower() in CODE_EXTS

    def _should_publish_deliverable_code(
        self,
        file_path: Path,
        *,
        raw_result: Any,
        session_dir: Path,
    ) -> bool:
        """RAW session results may contain full code trees; only promote paths under submission/ or deliverable/, or listed in deliverable_code_paths."""
        if not isinstance(raw_result, dict):
            raw_result = {}
        explicit = raw_result.get("deliverable_code_paths")
        if isinstance(explicit, list) and explicit:
            for entry in explicit:
                if not isinstance(entry, str) or not entry.strip():
                    continue
                resolved = self._resolve_path(entry.strip(), session_dir=session_dir)
                if resolved is None:
                    continue
                try:
                    if resolved.resolve() == file_path.resolve():
                        return True
                except OSError:
                    continue
            return False
        norm = str(file_path).replace("\\", "/").lower()
        return "/submission/" in norm or "/deliverable/" in norm

    def _should_use_legacy_file_ingest(self, tool_name: str) -> bool:
        """Heuristic path extraction from tool JSON; off in explicit mode except manuscript tools."""
        normalized = tool_name.strip().lower()
        if normalized == DELIVERABLE_SUBMIT_KEY:
            return False
        mode = getattr(self._settings, "ingest_mode", "legacy") or "legacy"
        if mode != "explicit":
            return True
        return normalized in EXPLICIT_AUTO_PUBLISH_TOOLS

    @staticmethod
    def _extract_deliverable_submit(raw_result: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_result, dict):
            return None
        payload = raw_result.get(DELIVERABLE_SUBMIT_KEY)
        return payload if isinstance(payload, dict) else None

    def _copy_resolved_file_to_deliverables(
        self,
        *,
        file_path: Path,
        module: str,
        latest_root: Path,
        session_dir: Path,
        raw_result: Any,
        publish_status: str,
        now: str,
        previous_manifest: Dict[str, Any],
        from_explicit_submit: bool = False,
    ) -> Optional[Dict[str, Any]]:
        item, _ = self._copy_resolved_file_to_deliverables_with_reason(
            file_path=file_path,
            module=module,
            latest_root=latest_root,
            session_dir=session_dir,
            raw_result=raw_result,
            publish_status=publish_status,
            now=now,
            previous_manifest=previous_manifest,
            from_explicit_submit=from_explicit_submit,
        )
        return item

    def _copy_resolved_file_to_deliverables_with_reason(
        self,
        *,
        file_path: Path,
        module: str,
        latest_root: Path,
        session_dir: Path,
        raw_result: Any,
        publish_status: str,
        now: str,
        previous_manifest: Dict[str, Any],
        from_explicit_submit: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        module_key = str(module or "").strip().lower()
        if module_key not in self._settings.modules:
            return None, f"unsupported module '{module_key}'"
        if module_key == "code" and not from_explicit_submit:
            if not self._should_publish_deliverable_code(file_path, raw_result=raw_result, session_dir=session_dir):
                return None, "code artifact must be under submission/ or deliverable/, or listed in deliverable_code_paths"
        if not self._should_publish_file(module_key, file_path):
            if self._is_noise_artifact_file(file_path):
                return None, "artifact matches noise-file filter"
            if self._is_cc_intermediate_artifact(file_path):
                return None, "artifact is a Claude Code intermediate script"
            if module_key == "code" and not self._is_allowed_code_file(file_path):
                return None, "artifact is not an allowed code file for the code module"
            if module_key == "docs" and not self._is_allowed_doc_file(file_path):
                return None, "artifact is not an allowed document for the docs module"
            return None, "artifact was filtered by deliverable publish policy"
        if module_key == "refs" and file_path.suffix.lower() == ".bib":
            refs_dir = latest_root / "refs"
            refs_dir.mkdir(parents=True, exist_ok=True)
            try:
                bib_text = file_path.read_text(encoding="utf-8")
            except Exception:
                bib_text = ""
            merged = self._paper_builder.merge_bib_entries(refs_dir=refs_dir, bib_text=bib_text)
            if merged is None:
                return None, "failed to merge bibliography entries"
            return {
                "module": "refs",
                "path": str(merged.relative_to(latest_root)),
                "status": publish_status,
                "size": merged.stat().st_size,
                "updated_at": now,
                "source_path": self._to_project_relative(file_path),
            }, None
        source_identity = (
            self._source_identity(file_path)
            if module_key == "image_tabular" and file_path.suffix.lower() in IMAGE_EXTS
            else None
        )
        target = self._copy_to_module(
            source_path=file_path,
            module_dir=(latest_root / module_key),
            source_identity=source_identity,
            latest_root=latest_root,
            previous_manifest=previous_manifest,
        )
        rel_path = str(target.relative_to(latest_root))
        return {
            "module": module_key,
            "path": rel_path,
            "status": publish_status,
            "size": target.stat().st_size,
            "updated_at": now,
            "source_path": self._to_project_relative(file_path),
        }, None

    def _apply_deliverable_submit_payload(
        self,
        *,
        payload: Dict[str, Any],
        latest_root: Path,
        session_dir: Path,
        raw_result: Any,
        publish_status: str,
        now: str,
        previous_manifest: Dict[str, Any],
    ) -> Dict[str, Any]:
        artifacts = payload.get("artifacts")
        requested_count = len(artifacts) if isinstance(artifacts, list) else 0
        warnings: List[str] = []
        if not payload.get("publish", True):
            return {"items": [], "requested_count": requested_count, "warnings": warnings}
        if not isinstance(artifacts, list):
            return {"items": [], "requested_count": 0, "warnings": warnings}
        out: List[Dict[str, Any]] = []
        for idx, row in enumerate(artifacts):
            if not isinstance(row, dict):
                warnings.append(f"artifact[{idx}] skipped: entry must be an object")
                continue
            raw_path = row.get("path")
            module_hint = row.get("module")
            if not isinstance(raw_path, str) or not raw_path.strip():
                warnings.append(f"artifact[{idx}] skipped: missing path")
                continue
            if not isinstance(module_hint, str) or not module_hint.strip():
                warnings.append(f"artifact[{idx}] skipped: missing module")
                continue
            raw_path_text = raw_path.strip()
            resolved = self._resolve_path(raw_path_text, session_dir=session_dir)
            if resolved is None:
                raw_candidate = Path(raw_path_text)
                if raw_candidate.is_absolute():
                    if raw_candidate.exists():
                        reason_text = "path is outside allowed deliverable sources"
                    else:
                        reason_text = "path does not exist"
                else:
                    candidate_paths = [session_dir / raw_candidate, self._project_root / raw_candidate]
                    if any(path.exists() for path in candidate_paths):
                        reason_text = "path is outside allowed deliverable sources"
                    else:
                        reason_text = "path does not exist"
                warnings.append(f"artifact[{idx}] skipped: {reason_text} ('{raw_path_text}')")
                continue
            if not resolved.exists():
                warnings.append(
                    f"artifact[{idx}] skipped: path '{raw_path_text}' does not exist"
                )
                continue
            if not resolved.is_file():
                warnings.append(
                    f"artifact[{idx}] skipped: path '{raw_path_text}' is not a file"
                )
                continue
            item, reason = self._copy_resolved_file_to_deliverables_with_reason(
                file_path=resolved,
                module=module_hint,
                latest_root=latest_root,
                session_dir=session_dir,
                raw_result=raw_result,
                publish_status=publish_status,
                now=now,
                previous_manifest=previous_manifest,
                from_explicit_submit=True,
            )
            if item is not None:
                out.append(item)
                continue
            warnings.append(
                f"artifact[{idx}] skipped: {reason or 'artifact was not publishable'} "
                f"({self._to_project_relative(resolved)})"
            )
        return {"items": out, "requested_count": requested_count, "warnings": warnings}

    def _is_noise_artifact_file(self, source_path: Path) -> bool:
        file_name = source_path.name.lower()
        if file_name not in NOISE_FILENAMES:
            return False
        normalized = "/" + str(source_path).replace("\\", "/").lower()
        return any(segment in normalized for segment in NOISE_PATH_SEGMENTS)

    @staticmethod
    def _is_cc_intermediate_artifact(source_path: Path) -> bool:
        """CC auto-generated one-off scripts (run_YYYYMMDD_HHMMSS_*) are working artifacts, not deliverables."""
        if source_path.suffix.lower() not in CC_INTERMEDIATE_SCRIPT_EXTS:
            return False
        return bool(_CC_RUN_ARTIFACT_RE.match(source_path.name))

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

    @staticmethod
    def _should_publish_text_blob(*, tool_name: str, raw_result: Any) -> bool:
        normalized_tool = str(tool_name or "").strip().lower()
        # Claude Code: only publish to paper when infer_section returns a section (handled at call site)
        if normalized_tool == "code_executor":
            return False
        if normalized_tool in TEXT_DELIVERABLE_TOOLS:
            return True
        if normalized_tool != "file_operations" or not isinstance(raw_result, dict):
            return False
        operation = str(raw_result.get("operation") or "").strip().lower()
        return operation == "write" and raw_result.get("success") is True

    @staticmethod
    def _is_allowed_doc_file(path: Path) -> bool:
        return path.suffix.lower() in DOC_EXTS and path.stem.lower() in DOC_ALLOWED_STEMS

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

    def _copy_to_module(
        self,
        *,
        source_path: Path,
        module_dir: Path,
        source_identity: Optional[str] = None,
        latest_root: Optional[Path] = None,
        previous_manifest: Optional[Dict[str, Any]] = None,
    ) -> Path:
        module_dir.mkdir(parents=True, exist_ok=True)
        target = module_dir / source_path.name
        owners: Dict[str, str] = {}
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
            if existing_owner and existing_owner != source_identity:
                raise ValueError(
                    f"Conflicting deliverable basename '{source_path.name}' from "
                    f"'{existing_owner}' and '{source_identity}'"
                )
            if target.exists() and existing_owner is None and not self._same_file(source_path, target):
                raise ValueError(
                    f"Conflicting deliverable basename '{source_path.name}' with unknown existing source in "
                    f"{module_dir}"
                )
        if target.exists() and self._same_file(source_path, target):
            if source_identity and owners.get(source_path.name) != source_identity:
                owners[source_path.name] = source_identity
                self._write_source_ownership(module_dir, owners)
            return target
        shutil.copy2(source_path, target)
        if source_identity:
            owners[source_path.name] = source_identity
            self._write_source_ownership(module_dir, owners)
        return target

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
            staged.append(
                self._copy_to_module(
                    source_path=resolved,
                    module_dir=latest_root / "image_tabular",
                    source_identity=self._source_identity(resolved),
                    latest_root=latest_root,
                    previous_manifest=previous_manifest,
                )
            )
        return staged

    def _same_file(self, source_path: Path, target: Path) -> bool:
        try:
            return source_path.stat().st_size == target.stat().st_size and source_path.read_bytes() == target.read_bytes()
        except Exception:
            return False

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
        for file_path in docs_dir.iterdir():
            if not file_path.is_file():
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


_publisher: Optional[DeliverablePublisher] = None


def get_deliverable_publisher() -> DeliverablePublisher:
    global _publisher
    if _publisher is None:
        _publisher = DeliverablePublisher()
    return _publisher


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["DeliverablePublisher", "PublishReport", "get_deliverable_publisher"]
