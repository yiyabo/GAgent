"""
Review Pack Writer Tool
-----------------------
One-shot command to build a submission-grade review starter pack and a draft.

Pipeline (deterministic orchestration, keeps individual tools usable):
1) literature_pipeline(query=...) -> library.jsonl + references.bib + evidence.md (+ optional PMC PDFs)
2) manuscript_writer(task=...) with context_paths=[evidence.md, references.bib] -> review draft

Outputs are written under the chosen out_dir (project-relative preferred).

Notes:
- This tool does NOT rely on claude_code.
- It reuses the existing tool implementations directly.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .literature_pipeline import literature_pipeline_handler
from .manuscript_writer import manuscript_writer_handler

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
_TRUTHY_VALUES = {"1", "true", "yes", "on", "y"}


def _env_enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in _TRUTHY_VALUES


def _bar(done: int, total: int, width: int = 24) -> str:
    total = max(1, int(total))
    done = max(0, min(int(done), total))
    frac = done / total
    filled = int(round(frac * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + f"] {int(frac*100)}%"


def _resolve_default_pack_dir(*, session_id: Optional[str], timestamp: str) -> Path:
    if isinstance(session_id, str) and session_id.strip():
        from app.services.session_paths import get_session_tool_outputs_dir

        root = get_session_tool_outputs_dir(session_id.strip(), create=True)
        return (root / "review_pack_writer" / f"review_pack_{timestamp}").resolve()
    return (_PROJECT_ROOT / "runtime" / "literature" / f"review_pack_{timestamp}").resolve()


def _resolve_session_dir(session_id: Optional[str]) -> Optional[Path]:
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    normalized = session_id.strip()
    session_name = normalized if normalized.startswith("session_") else f"session_{normalized}"
    return (_PROJECT_ROOT / "runtime" / session_name).resolve()


def _to_session_relative(path: Path, session_dir: Optional[Path]) -> Optional[str]:
    if session_dir is None:
        return None
    try:
        return str(path.resolve().relative_to(session_dir.resolve()))
    except Exception:
        return None


def _default_pubmed_query(topic: str) -> str:
    # Focused for: phage-host interactions + phage omics/databases.
    # Users can override with query explicitly.
    base = "phage OR bacteriophage"
    host = "host interaction OR receptor OR adsorption OR immunity OR CRISPR OR lysogeny OR temperate"
    omics = "virome OR metagenomics OR database OR atlas OR catalog OR benchmark OR pipeline"
    # IMPORTANT: PubMed queries are typically English. If topic is Chinese-only, do NOT append it
    # (it can collapse results to zero). We keep a stable English query and only append topic when
    # it already contains ASCII keywords.
    t = (topic or "").strip()
    has_ascii = any(ord(ch) < 128 and ch.isalnum() for ch in t)
    extra = ""
    if has_ascii:
        t2 = t.replace("，", " ").replace(",", " ")
        t2 = " ".join(x for x in t2.split() if x)[:120]
        if t2:
            extra = f"({t2})"
    # Reduce false positives from "phage display" literature.
    avoid = 'NOT ("phage display" OR "phage-displayed" OR "phage display library")'
    return f"(({base}) AND ({host})) OR (({base}) AND ({omics})) {extra} {avoid}".strip()


def _default_task(topic: str) -> str:
    t = topic.strip() if isinstance(topic, str) and topic.strip() else "phage-related topic"
    return (
        f"Write a submission-ready English review article on: {t}.\n"
        "Requirements:\n"
        "1) Use Markdown citekeys throughout (for example: [@citekey]).\n"
        "2) Do not fabricate citations. The References section must only include citekeys present in the provided BibTeX.\n"
        "3) If the evidence files do not provide concrete values/statistics, write \"Not available\" instead of inventing numbers.\n"
        "4) Suggested structure: Abstract, Introduction, thematic sections, Challenges & Outlook, Conclusion, References.\n"
    )


async def review_pack_writer_handler(
    topic: str,
    *,
    query: Optional[str] = None,
    out_dir: Optional[str] = None,
    max_results: int = 80,
    # Default to route-A: do not depend on PDFs (PMC may block with 403 in some networks).
    download_pdfs: bool = True,
    max_pdfs: int = 80,
    # manuscript writer options
    output_path: Optional[str] = None,
    sections: Optional[List[str]] = None,
    max_revisions: int = 5,
    evaluation_threshold: float = 0.8,
    keep_workspace: bool = True,
    task: Optional[str] = None,
    # optional model/provider overrides
    generation_model: Optional[str] = None,
    evaluation_model: Optional[str] = None,
    merge_model: Optional[str] = None,
    generation_provider: Optional[str] = None,
    evaluation_provider: Optional[str] = None,
    merge_provider: Optional[str] = None,
    # misc
    user_agent: Optional[str] = None,
    proxy: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(topic, str) or not topic.strip():
        return {"tool": "review_pack_writer", "success": False, "error": "missing_topic"}

    stage_names = ["literature_pack", "draft_manuscript", "done"]
    stage = 0

    # Resolve out_dir
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    try:
        default_out = _resolve_default_pack_dir(session_id=session_id, timestamp=ts)
    except Exception as exc:
        return {
            "tool": "review_pack_writer",
            "success": False,
            "error": f"session_output_dir_unavailable: {exc}",
        }
    pack_dir = Path(out_dir) if out_dir else default_out
    if not pack_dir.is_absolute():
        pack_dir = (_PROJECT_ROOT / pack_dir).resolve()
    if not str(pack_dir).startswith(str(_PROJECT_ROOT)):
        return {"tool": "review_pack_writer", "success": False, "error": "out_dir_outside_project"}
    pack_dir.mkdir(parents=True, exist_ok=True)

    # Default output manuscript path inside pack_dir
    if not output_path:
        output_file = pack_dir / "review_draft.md"
        output_path = str(output_file.relative_to(_PROJECT_ROOT))

    final_query = (query.strip() if isinstance(query, str) and query.strip() else _default_pubmed_query(topic))
    final_task = task.strip() if isinstance(task, str) and task.strip() else _default_task(topic)

    # 1) Literature pack
    pack = await literature_pipeline_handler(
        query=final_query,
        max_results=max_results,
        out_dir=str(pack_dir.relative_to(_PROJECT_ROOT)),
        download_pdfs=download_pdfs,
        max_pdfs=max_pdfs,
        user_agent=user_agent,
        proxy=proxy,
        session_id=session_id,
    )
    stage = 1

    if not isinstance(pack, dict) or not pack.get("success"):
        return {
            "tool": "review_pack_writer",
            "success": False,
            "error": "literature_pipeline_failed",
            "progress_bar": _bar(stage, len(stage_names)),
            "progress_stage": stage_names[stage - 1],
            "pack": pack,
        }

    outputs = pack.get("outputs") if isinstance(pack.get("outputs"), dict) else {}
    evidence_coverage_passed = bool(pack.get("evidence_coverage_passed"))
    coverage_summary = str(pack.get("coverage_summary") or "").strip()
    coverage_report_path = (
        str(pack.get("coverage_report_path") or "").strip()
        or str(outputs.get("coverage_report_json") or "").strip()
    )
    context_paths = []
    for k in ("study_cards_jsonl", "coverage_report_json", "evidence_md", "references_bib"):
        p = outputs.get(k)
        if isinstance(p, str) and p.strip():
            context_paths.append(p.strip())

    session_root = _resolve_session_dir(session_id)
    hidden_artifact_prefixes: List[str] = []
    pack_rel = _to_session_relative(pack_dir, session_root)
    if pack_rel and pack_rel not in hidden_artifact_prefixes:
        hidden_artifact_prefixes.append(pack_rel)

    if not evidence_coverage_passed:
        release_summary = (
            f"Publication blocked: {coverage_summary}"
            if coverage_summary
            else "Publication blocked: evidence coverage was too weak for a PI-readable review manuscript."
        )
        return {
            "tool": "review_pack_writer",
            "success": False,
            "partial": False,
            "error_code": "low_evidence_coverage",
            "warnings": None,
            "quality_gate_passed": False,
            "polish_gate_passed": False,
            "public_release_ready": False,
            "evidence_coverage_passed": False,
            "coverage_summary": coverage_summary or release_summary,
            "coverage_report_path": coverage_report_path or None,
            "release_state": "blocked",
            "release_summary": release_summary,
            "hidden_artifact_prefixes": hidden_artifact_prefixes,
            "topic": topic,
            "query": final_query,
            "out_dir": str(pack_dir.relative_to(_PROJECT_ROOT)),
            "progress_bar": _bar(stage, len(stage_names)),
            "progress_steps": stage_names,
            "pack": pack,
            "draft": None,
            "artifacts": {
                "library_jsonl": outputs.get("library_jsonl"),
                "study_cards_jsonl": outputs.get("study_cards_jsonl"),
                "coverage_report_json": outputs.get("coverage_report_json"),
                "references_bib": outputs.get("references_bib"),
                "evidence_md": outputs.get("evidence_md"),
                "evidence_coverage_md": outputs.get("evidence_coverage_md"),
                "study_matrix_md": outputs.get("study_matrix_md"),
                "pdf_dir": outputs.get("pdf_dir"),
                "manuscript_output": None,
                "manuscript_partial": None,
                "manuscript_workspace": None,
            },
        }

    # 2) Draft manuscript
    draft = await manuscript_writer_handler(
        task=final_task,
        output_path=output_path,
        context_paths=context_paths,
        sections=sections,
        max_revisions=max_revisions,
        evaluation_threshold=evaluation_threshold,
        keep_workspace=keep_workspace,
        generation_model=generation_model,
        evaluation_model=evaluation_model,
        merge_model=merge_model,
        generation_provider=generation_provider,
        evaluation_provider=evaluation_provider,
        merge_provider=merge_provider,
        session_id=session_id,
    )
    stage = 2

    draft_ok = isinstance(draft, dict) and bool(draft.get("success"))
    quality_gate_passed = (
        bool(draft.get("quality_gate_passed"))
        if isinstance(draft, dict) and draft.get("quality_gate_passed") is not None
        else draft_ok
    )
    polish_gate_passed = (
        bool(draft.get("polish_gate_passed"))
        if isinstance(draft, dict) and draft.get("polish_gate_passed") is not None
        else draft_ok
    )
    public_release_ready = (
        bool(draft.get("public_release_ready"))
        if isinstance(draft, dict) and draft.get("public_release_ready") is not None
        else bool(draft_ok and quality_gate_passed and polish_gate_passed)
    )
    release_state = (
        str(draft.get("release_state") or "").strip().lower()
        if isinstance(draft, dict)
        else ""
    ) or ("final" if public_release_ready else "blocked")
    release_summary = (
        str(draft.get("release_summary") or "").strip()
        if isinstance(draft, dict)
        else ""
    )
    partial_path = (
        draft.get("partial_output_path")
        if isinstance(draft, dict) and isinstance(draft.get("partial_output_path"), str)
        else None
    )
    ok = bool(draft_ok and quality_gate_passed and polish_gate_passed and public_release_ready)
    partial = bool(partial_path and not ok)
    error_code: Optional[str] = None
    if not ok:
        if isinstance(draft, dict):
            draft_error = draft.get("error_code") or draft.get("error")
            if isinstance(draft_error, str) and draft_error.strip():
                error_code = draft_error.strip()
        if partial and not error_code:
            error_code = "section_evaluation_failed"
        if not error_code:
            error_code = "manuscript_writer_failed"
    warnings: List[str] = []
    if partial:
        warnings.append(
            "manuscript_writer did not pass section evaluation; a partial draft was still produced. "
            f"See {partial_path}"
        )
    if isinstance(draft, dict):
        for item in draft.get("hidden_artifact_prefixes") or []:
            value = str(item or "").strip().lstrip("/").replace("\\", "/")
            if value and value not in hidden_artifact_prefixes:
                hidden_artifact_prefixes.append(value)

    return {
        "tool": "review_pack_writer",
        "success": True if ok else False,
        "partial": partial,
        "error_code": error_code,
        "partial_output_path": partial_path,
        "warnings": warnings if warnings else None,
        "quality_gate_passed": quality_gate_passed,
        "polish_gate_passed": polish_gate_passed,
        "public_release_ready": public_release_ready,
        "evidence_coverage_passed": evidence_coverage_passed,
        "coverage_summary": coverage_summary or (
            str((draft or {}).get("coverage_summary") or "").strip() if isinstance(draft, dict) else ""
        ),
        "coverage_report_path": coverage_report_path or (
            str((draft or {}).get("coverage_report_path") or "").strip() if isinstance(draft, dict) else ""
        ) or None,
        "release_state": release_state,
        "release_summary": release_summary or (
            "Publication blocked: the manuscript did not pass the final release gate."
            if not ok
            else "Review manuscript passed the final release gate."
        ),
        "hidden_artifact_prefixes": hidden_artifact_prefixes,
        "topic": topic,
        "query": final_query,
        "out_dir": str(pack_dir.relative_to(_PROJECT_ROOT)),
        "progress_bar": _bar(len(stage_names), len(stage_names)),
        "progress_steps": stage_names,
        "pack": pack,
        "draft": draft,
        "artifacts": {
            "library_jsonl": outputs.get("library_jsonl"),
            "study_cards_jsonl": outputs.get("study_cards_jsonl"),
            "coverage_report_json": outputs.get("coverage_report_json"),
            "references_bib": outputs.get("references_bib"),
            "evidence_md": outputs.get("evidence_md"),
            "evidence_coverage_md": outputs.get("evidence_coverage_md"),
            "study_matrix_md": outputs.get("study_matrix_md"),
            "pdf_dir": outputs.get("pdf_dir"),
            "manuscript_output": (draft or {}).get("output_path") if isinstance(draft, dict) else None,
            "manuscript_partial": partial_path,
            "manuscript_workspace": (draft or {}).get("temp_workspace") if isinstance(draft, dict) else None,
        },
    }


review_pack_writer_tool = {
    "name": "review_pack_writer",
    "description": (
        "One-shot: build a PubMed/PMC literature pack (BibTeX + evidence inventory + optional OA PDFs) "
        "then draft an English review with Markdown citekeys via manuscript_writer. "
        "Keeps literature_pipeline/manuscript_writer available for standalone use."
    ),
    "category": "document_writing",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Review topic (e.g., phage–host interaction; phage omics/databases)."},
            "query": {"type": "string", "description": "Optional PubMed query override (boolean operators supported)."},
            "out_dir": {"type": "string", "description": "Output directory (project-relative preferred)."},
            "session_id": {
                "type": "string",
                "description": "Optional session id. When out_dir is omitted, the review pack is created under the session tool_outputs directory.",
            },
            "max_results": {"type": "integer", "default": 80, "description": "Max PubMed results (<=500)."},
            "download_pdfs": {"type": "boolean", "default": True, "description": "Download OA PDFs from PMC when possible."},
            "max_pdfs": {"type": "integer", "default": 80, "description": "Max PMC PDFs to download."},
            "output_path": {"type": "string", "description": "Final manuscript output path (project-relative)."},
            "sections": {"type": "array", "items": {"type": "string"}, "description": "Optional section list for manuscript_writer."},
            "max_revisions": {"type": "integer", "default": 5, "description": "Max revisions per section."},
            "evaluation_threshold": {"type": "number", "default": 0.8, "description": "Section pass threshold (0-1)."},
            "keep_workspace": {"type": "boolean", "default": True, "description": "Keep intermediate drafts/reviews workspace."},
            "task": {"type": "string", "description": "Optional writing task override (should enforce [@citekey] usage)."},
            "generation_model": {"type": "string", "description": "Optional generation model (or env var key)."},
            "evaluation_model": {"type": "string", "description": "Optional evaluation model (or env var key)."},
            "merge_model": {"type": "string", "description": "Optional merge model (or env var key)."},
            "generation_provider": {"type": "string", "description": "Optional provider override for generation."},
            "evaluation_provider": {"type": "string", "description": "Optional provider override for evaluation."},
            "merge_provider": {"type": "string", "description": "Optional provider override for merge."},
            "user_agent": {"type": "string", "description": "Optional User-Agent override for HTTP requests."},
            "proxy": {
                "type": "string",
                "description": "Optional HTTP proxy URL for literature downloads only (e.g. http://127.0.0.1:7897).",
            },
        },
        "required": ["topic"],
    },
    "handler": review_pack_writer_handler,
    "tags": ["review", "pubmed", "pmc", "bibtex", "citekey", "manuscript"],
    "examples": [
        "Build + draft: topic='phage-host interactions and phage omics/databases' out_dir='runtime/literature/phage_review_pack'",
    ],
}
