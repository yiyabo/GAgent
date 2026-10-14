"""Distinguish a planning deliverable from manuscript content without an LLM."""
from __future__ import annotations

import re
from typing import Any

# Match the deliverable goal, not a paper mentioned as input or background.
# No filenames, extensions, task IDs or biological topics determine the mode.
_PLANNING_TITLE = re.compile(
    r"(?:定位(?:策略|与.{0,12}(?:主线|范围|策略))|(?:大纲|提纲)(?:设计|文档|构建|规划|整理)?|"
    r"(?:目标读者|拟投期刊).{0,12}(?:定位|选择|策略))(?:与差异分析)?\s*$|"
    r"^(?:(?:write|draft|prepare|design|create|develop|revise|update|generate)\s+(?:a\s+|the\s+)?)?"
    r"(?:positioning|scope|editorial)\s+(?:strategy|note|plan|brief)\b|"
    r"^(?:(?:write|draft|prepare|design|create|develop|revise|update|assemble|generate|build|finali[sz]e)\s+(?:a\s+|the\s+)?)?"
    r"(?:(?:detailed|final|structured|chapter-level)\s+)?"
    r"(?:(?:chapter|section|review|paper|manuscript|research|introduction)\s+)?(?:outline|proposal|protocol)\b"
    r"(?:\s+(?:design|document|note|plan|draft|strategy))?(?:\s*$|\s*[:（(])",
    re.I,
)
_PLANNING_OUTPUT = re.compile(
    r"(?:输出|交付|产出)(?:为|一份|一个|完整的|最终的|结构化|\s){0,12}"
    r"(?:定位策略笔记|范围说明|(?:章节级|详细|综述)?大纲(?:文档|笔记)?)|"
    r"\b(?:deliver|produce|output)\s+(?:a\s+|the\s+)?"
    r"(?:(?:positioning|scope|editorial)\s+(?:strategy\s+)?(?:note|brief)|(?:chapter\s+)?outline)\b",
    re.I,
)
_MANUSCRIPT_WRITING = re.compile(
    r"\b(?:write|draft|revise|assemble|generate|polish)\b.{0,60}"
    r"\b(?:manuscript|paper|review|report|section|introduction|abstract)\b|"
    r"(?:撰写|起草|写作|修订|润色|成稿|合并|整合).{0,30}(?:论文|手稿|综述|章节|正文|报告)", re.I)


def is_planning_document(name: Any, instruction: Any, metadata: Any = None) -> bool:
    role = (metadata or {}).get("paper_role") if isinstance(metadata, dict) else None
    if _PLANNING_TITLE.search(str(name or "")) or _PLANNING_OUTPUT.search(str(instruction or "")):
        return True
    # User-directed edits may leave the old role in metadata. A clear current
    # drafting goal must not inherit an earlier planning-only restriction.
    return bool(role == "planning_document" and not _MANUSCRIPT_WRITING.search(f"{name or ''}\n{instruction or ''}"))


def requires_manuscript_content(name: Any, instruction: Any, metadata: Any = None) -> bool:
    if is_planning_document(name, instruction, metadata):
        return False
    meta = metadata if isinstance(metadata, dict) else {}
    role = str(meta.get("paper_role") or "").lower()
    if role in {"evidence_collector", "evidence_extractor", "citation_validator"}:
        return False
    if role in {"section_writer", "manuscript_writer", "manuscript_assembler"}:
        return True
    return bool(_MANUSCRIPT_WRITING.search(f"{name or ''}\n{instruction or ''}"))


PLANNING_DOCUMENT_SCOPE = (
    "This task delivers a planning document, not manuscript body text. Cover the requested positioning, "
    "scope or outline decisions and save the declared file. Reuse supplied context and verify only concrete "
    "factual gaps, such as current journal requirements; do not start a full evidence corpus or draft later "
    "chapters unless this task explicitly requests them. Avoid recursive workspace discovery and repeated "
    "format checks when the declared task directory and output are already known. Ordinary file writing "
    "is sufficient; membership in a paper project does not require manuscript_writer or publication polish."
)
