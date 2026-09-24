"""Manuscript release metadata, bibliography rendering and output publishing.

Extracted from ``publisher.py`` (clusters "稿件元数据抽取+bibtex 解析渲染+release
summary" and "稿件发布") per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.3. This sibling owns
everything between a ``manuscript_writer`` / ``review_pack_writer`` tool result
and the published ``paper/`` / ``refs/`` / ``docs/`` artifacts: release-state
metadata, structure metadata, bibtex parsing and PI-readable report rendering,
the release-summary document, and the section/figure/report publishing pipeline.

Compatibility contract: the ``publisher`` facade re-exports every name defined
here and composes ``_ManuscriptMethods`` onto ``DeliverablePublisher``, so the
chat path (``action_handlers``), the plan path (``plan_executor``) and the
artifact stream (``RegistryProjector``) keep calling the publisher object
unchanged.  ``tool_name`` literals ("manuscript_writer"/"review_pack_writer"),
the ``## References`` rendering and every payload/path key are byte-identical to
the pre-refactor implementation.

Late binding: the static helpers below are the only places that referenced the
``DeliverablePublisher`` class by name (``DeliverablePublisher._x(...)``).  They
now read it through the facade module at call time (the ``_dta()`` pattern used
by the deep_think siblings), which keeps the class object authoritative and the
call site patchable.  Sanctioned deviation from byte-verbatim.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .policy import DOC_ALLOWED_STEMS

logger = logging.getLogger(__name__)


class _ManuscriptMethods:
    """Manuscript/release methods mixed into ``DeliverablePublisher``."""

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
        from . import publisher as facade

        manuscript_result = facade.DeliverablePublisher._extract_manuscript_result(payload)
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
            else not facade.DeliverablePublisher._is_failed_result(payload)
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
        from . import publisher as facade

        normalized_tool = str(tool_name or "").strip().lower()
        if normalized_tool not in {"manuscript_writer", "review_pack_writer"}:
            return False
        release_meta = facade.DeliverablePublisher._extract_release_metadata(raw_result)
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
        from . import publisher as facade

        if not isinstance(raw_result, dict):
            return False

        manuscript_result = facade.DeliverablePublisher._extract_manuscript_result(raw_result)
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


__all__ = ["_ManuscriptMethods"]
