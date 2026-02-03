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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .literature_pipeline import literature_pipeline_handler
from .manuscript_writer import manuscript_writer_handler

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()


def _bar(done: int, total: int, width: int = 24) -> str:
    total = max(1, int(total))
    done = max(0, min(int(done), total))
    frac = done / total
    filled = int(round(frac * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + f"] {int(frac*100)}%"


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
    t = topic.strip() if isinstance(topic, str) and topic.strip() else "噬菌体相关主题"
    return (
        f"写一篇可投稿的中文综述，主题：{t}。\n"
        "要求：\n"
        "1) 全文必须使用 Markdown citekeys（形如 [@citekey]）。\n"
        "2) 不得捏造引用；References 章节只能列出提供的 BibTeX 中存在的 citekey。\n"
        "3) 如果证据文件未提供具体数值/统计量，必须写“Not available”，不得编造数字。\n"
        "4) 结构建议：摘要、引言、（若干主题小节）、挑战与展望、结论、参考文献。\n"
    )


async def review_pack_writer_handler(
    topic: str,
    *,
    query: Optional[str] = None,
    out_dir: Optional[str] = None,
    max_results: int = 80,
    # Default to route-A: do not depend on PDFs (PMC may block with 403 in some networks).
    download_pdfs: bool = False,
    max_pdfs: int = 30,
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
) -> Dict[str, Any]:
    if not isinstance(topic, str) or not topic.strip():
        return {"tool": "review_pack_writer", "success": False, "error": "missing_topic"}

    stage_names = ["literature_pack", "draft_manuscript", "done"]
    stage = 0

    # Resolve out_dir
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    default_out = Path("runtime/literature") / f"review_pack_{ts}"
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
    context_paths = []
    for k in ("evidence_md", "references_bib"):
        p = outputs.get(k)
        if isinstance(p, str) and p.strip():
            context_paths.append(p.strip())

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
    )
    stage = 2

    draft_ok = isinstance(draft, dict) and bool(draft.get("success"))
    partial_path = (
        draft.get("partial_output_path")
        if isinstance(draft, dict) and isinstance(draft.get("partial_output_path"), str)
        else None
    )
    ok = bool(draft_ok or partial_path)
    warnings: List[str] = []
    if not draft_ok and partial_path:
        warnings.append(
            "manuscript_writer did not pass section evaluation; a partial draft was still produced. "
            f"See {partial_path}"
        )
    return {
        "tool": "review_pack_writer",
        "success": True if ok else False,
        "partial": True if (not draft_ok and partial_path) else False,
        "warnings": warnings if warnings else None,
        "topic": topic,
        "query": final_query,
        "out_dir": str(pack_dir.relative_to(_PROJECT_ROOT)),
        "progress_bar": _bar(len(stage_names), len(stage_names)),
        "progress_steps": stage_names,
        "pack": pack,
        "draft": draft,
        "artifacts": {
            "library_jsonl": outputs.get("library_jsonl"),
            "references_bib": outputs.get("references_bib"),
            "evidence_md": outputs.get("evidence_md"),
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
        "then draft a Chinese review with Markdown citekeys via manuscript_writer. "
        "Keeps literature_pipeline/manuscript_writer available for standalone use."
    ),
    "category": "document_writing",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Review topic (e.g., phage–host interaction; phage omics/databases)."},
            "query": {"type": "string", "description": "Optional PubMed query override (boolean operators supported)."},
            "out_dir": {"type": "string", "description": "Output directory (project-relative preferred)."},
            "max_results": {"type": "integer", "default": 80, "description": "Max PubMed results (<=500)."},
            "download_pdfs": {"type": "boolean", "default": False, "description": "Download OA PDFs from PMC when possible."},
            "max_pdfs": {"type": "integer", "default": 30, "description": "Max PMC PDFs to download."},
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
        "Build + draft: topic='噬菌体-宿主互作、噬菌体组学/数据库' out_dir='runtime/literature/phage_review_pack'",
    ],
}

