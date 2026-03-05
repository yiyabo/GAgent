from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set
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

DELIVERABLE_EXTS = CODE_EXTS | TABULAR_EXTS | IMAGE_EXTS | DOC_EXTS | PAPER_EXTS | REF_EXTS

DOC_ALLOWED_STEMS = {
    "abstract",
    "introduction",
    "method",
    "methods",
    "experiment",
    "result",
    "results",
    "conclusion",
    "reference",
    "references",
    "report",
    "analysis",
    "survey",
    "summary",
}

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
    "evidence_md",
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
    "claude_code",
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
}

BLOCKED_SOURCE_FILENAMES = {
    ".DS_Store",
    "Thumbs.db",
}

_CC_RUN_ARTIFACT_RE = re.compile(r"^run_\d{8}_\d{6}_")

CC_INTERMEDIATE_SCRIPT_EXTS = {".py", ".sh", ".bash", ".r", ".jl"}


@dataclass(frozen=True)
class PublishReport:
    version_id: str
    published_files_count: int
    published_modules: List[str]
    manifest_path: str
    paper_status: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_id": self.version_id,
            "published_files_count": self.published_files_count,
            "published_modules": list(self.published_modules),
            "manifest_path": self.manifest_path,
            "paper_status": dict(self.paper_status),
        }


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
        if self._is_failed_result(raw_result):
            return None

        session_dir = self.get_session_dir(normalized_session, create=True)
        deliverables_root = session_dir / "deliverables"
        latest_root = deliverables_root / "latest"
        latest_root.mkdir(parents=True, exist_ok=True)

        self._prune_legacy_modules(latest_root)
        self._cleanup_docs_module(latest_root / "docs")
        for module in self._settings.modules:
            (latest_root / module).mkdir(parents=True, exist_ok=True)

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
        items: List[Dict[str, Any]] = []

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
            if not self._should_publish_file(module, file_path):
                continue
            if module == "refs" and file_path.suffix.lower() == ".bib":
                refs_dir = latest_root / "refs"
                refs_dir.mkdir(parents=True, exist_ok=True)
                try:
                    bib_text = file_path.read_text(encoding="utf-8")
                except Exception:
                    bib_text = ""
                merged = self._paper_builder.merge_bib_entries(refs_dir=refs_dir, bib_text=bib_text)
                if merged is not None:
                    merged_rel = str(merged.relative_to(latest_root))
                    items.append(
                        {
                            "module": "refs",
                            "path": merged_rel,
                            "status": publish_status,
                            "size": merged.stat().st_size,
                            "updated_at": now,
                            "source_path": self._to_project_relative(file_path),
                        }
                    )
                continue
            target = self._copy_to_module(
                source_path=file_path,
                module_dir=(latest_root / module),
            )
            rel_path = str(target.relative_to(latest_root))
            items.append(
                {
                    "module": module,
                    "path": rel_path,
                    "status": publish_status,
                    "size": target.stat().st_size,
                    "updated_at": now,
                    "source_path": self._to_project_relative(file_path),
                }
            )

        manuscript_items = self._publish_manuscript_outputs(
            latest_root=latest_root,
            raw_result=raw_result,
            publish_status=publish_status,
            updated_at=now,
            source_task_id=task_id,
            task_name=task_name,
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
        normalized_tool = str(tool_name or "").strip().lower()
        # manuscript_writer outputs with explicit section artifacts should be source of truth;
        # skip generic text-based section inference to avoid overwriting section content with
        # summaries (e.g., "manuscript finished").
        if not manuscript_has_section_artifacts:
            if normalized_tool == "claude_code":
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

        if not items:
            return None

        if paper_dir.exists():
            self._paper_builder.ensure_structure(paper_dir=paper_dir, refs_dir=refs_dir, title=title)
        paper_status = self._paper_builder.get_status(paper_dir=paper_dir).to_dict() if paper_dir.exists() else {
            "completed_sections": [],
            "missing_sections": [],
            "total_sections": 0,
            "completed_count": 0,
        }

        latest_manifest_path = deliverables_root / "manifest_latest.json"
        previous_manifest = self._read_manifest(latest_manifest_path)
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
        )
        latest_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        return PublishReport(
            version_id=version_id,
            published_files_count=len(merged_items),
            published_modules=modules,
            manifest_path=str(latest_manifest_path),
            paper_status=paper_status,
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
            "conclusion": "conclusion",
        }
        return mapping.get(key)

    def _publish_manuscript_outputs(
        self,
        *,
        latest_root: Path,
        raw_result: Any,
        publish_status: str,
        updated_at: str,
        source_task_id: Optional[int],
        task_name: Optional[str],
    ) -> List[Dict[str, Any]]:
        if not isinstance(raw_result, dict):
            return []

        normalized_tool = str(raw_result.get("tool") or "").strip().lower()
        if normalized_tool != "manuscript_writer":
            return []

        items: List[Dict[str, Any]] = []
        title = task_name or "Research Project"
        paper_dir = latest_root / "paper"
        refs_dir = latest_root / "refs"
        docs_dir = latest_root / "docs"
        self._paper_builder.ensure_structure(paper_dir=paper_dir, refs_dir=refs_dir, title=title)
        docs_dir.mkdir(parents=True, exist_ok=True)

        sections_payload = raw_result.get("sections")
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

        analysis_ref = raw_result.get("effective_analysis_path") or raw_result.get("analysis_path")
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

        output_ref = raw_result.get("effective_output_path") or raw_result.get("output_path")
        if isinstance(output_ref, str) and output_ref.strip():
            output_source = self._resolve_path(output_ref, session_dir=latest_root.parent.parent)
            if output_source is not None and output_source.is_file():
                try:
                    output_text = output_source.read_text(encoding="utf-8").strip()
                except Exception:
                    output_text = ""
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

        path = Path(raw).expanduser()
        candidates: List[Path] = []
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.append(self._project_root / path)
            candidates.append(session_dir / path)
            candidates.append(self._runtime_dir / path)

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
            return True
        try:
            resolved_abs = path.resolve()
        except Exception:
            return False
        return self._is_within(resolved_abs, self._project_root)

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

        if "/paper/" in path_lower or suffix in {".tex", ".cls", ".sty", ".bst"}:
            return "paper"
        if "/refs/" in path_lower or suffix in REF_EXTS or "references" in path.name.lower():
            return "refs"
        if "/code/" in path_lower or suffix in CODE_EXTS:
            return "code"
        if suffix in IMAGE_EXTS or suffix in TABULAR_EXTS:
            return "image_tabular"
        if suffix == ".pdf":
            if any(token in path_lower for token in ("/fig", "/figure", "/table", "/plot", "/chart")):
                return "image_tabular"
            return "paper"
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
    def _should_publish_text_blob(*, tool_name: str, raw_result: Any) -> bool:
        normalized_tool = str(tool_name or "").strip().lower()
        # Claude Code: only publish to paper when infer_section returns a section (handled at call site)
        if normalized_tool == "claude_code":
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

    def _copy_to_module(self, *, source_path: Path, module_dir: Path) -> Path:
        module_dir.mkdir(parents=True, exist_ok=True)
        target = module_dir / source_path.name
        if target.exists() and self._same_file(source_path, target):
            return target
        shutil.copy2(source_path, target)
        return target

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["DeliverablePublisher", "PublishReport", "get_deliverable_publisher"]
